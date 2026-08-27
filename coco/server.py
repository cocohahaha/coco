"""coco local web server (FastAPI, bound to 127.0.0.1 only)."""
from __future__ import annotations

import datetime as dt
import io
import json
import queue
import re
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import ai, i18n, providers
from .config import (BRIEFS_DIR, GLOSSARY_FILE, LONGTERM_FILE, MEMORY_FILE, PREP_DIR,
                     TRACKING_DIR, TRASH_DIR, WEEKLY_DIR, ensure_dirs, load_config,
                     save_config)
from .i18n import get_lang, memory_placeholder, set_lang, t
from .ingest import TEXT_EXTS, TIMED_EXTS, import_text, import_transcript_file
from .library import (AUDIO_EXTS, Meeting, create_meeting, delete_meeting, find_meeting,
                      list_meetings, participants_from_transcript, search_library,
                      set_participants)
from .recorder import Recorder, record_supported, record_unsupported_hint
from .templates import (list_templates, list_track_modes, report_label, resolve_track_mode,
                        track_label)
from .transcriber import (detect_backend, detect_device, is_apple_silicon, no_engine_hint,
                          transcribe_meeting)

app = FastAPI(title="coco", docs_url=None, redoc_url=None)
recorder = Recorder()
JOBS: dict[str, dict] = {}  # jid -> {status, detail, meeting_id, error}
STATIC = Path(__file__).parent / "static"
TRANSCRIBE_LOCK = threading.Lock()  # one transcription at a time (one model in memory)


@app.middleware("http")
async def language_middleware(request: Request, call_next):
    """Per-request language: X-Coco-Lang header (set by the UI) > ?lang= > Accept-Language."""
    lang = (request.headers.get("x-coco-lang") or request.query_params.get("lang")
            or (request.headers.get("accept-language") or "").split(",")[0])
    token = set_lang(lang if i18n.normalize(lang) else None)
    try:
        return await call_next(request)
    finally:
        i18n.reset_lang(token)


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


def _bg(fn) -> None:
    """Run fn in a daemon thread that inherits the request language."""
    lang = get_lang()

    def run():
        set_lang(lang)
        fn()

    threading.Thread(target=run, daemon=True).start()


def _trash_file(path: Path, dest_stem: str) -> Path:
    """Soft-delete one .md into library/_trash."""
    if not path.exists():
        _err(FileNotFoundError(t("server.file_missing", name=path.name)), 404)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = TRASH_DIR / f"{dest_stem}~{stamp}.md"
    shutil.move(str(path), dest)
    return dest


def _require_transcriber() -> None:
    """Refuse audio up front when there is no engine – a meeting that can never be
    transcribed would look like a stuck job."""
    if not detect_backend():
        _err(RuntimeError(no_engine_hint()))


def _start_transcribe_job(mtg: Meeting, model: str | None = None) -> str:
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"status": "running", "detail": t("job.queued"), "meeting_id": mtg.id}
    lang = get_lang()

    def work():
        set_lang(lang)
        try:
            if not mtg.path.exists():
                raise FileNotFoundError(t("job.deleted_while_queued"))
            with TRANSCRIBE_LOCK:
                JOBS[jid].update(detail=t("job.transcribing"))
                transcribe_meeting(mtg, model=model,
                                   progress=lambda msg: JOBS[jid].update(detail=msg))
            if load_config().get("auto_memory", True):
                try:
                    JOBS[jid].update(detail=t("job.memorizing"))
                    ai.update_longterm(mtg)
                except Exception as e:  # memory failure never invalidates the transcript
                    mtg.save_meta(memory_error=str(e))
            JOBS[jid].update(status="done", detail=t("job.done"))
        except Exception as e:
            JOBS[jid].update(status="error", detail=str(e))

    threading.Thread(target=work, daemon=True).start()
    return jid


@app.on_event("startup")
def resume_interrupted():
    """Re-queue transcriptions that were running or queued when the server was killed."""
    for m in list_meetings():
        if (m.meta.get("status") in ("new", "transcribing")
                and not m.transcript_md.exists() and m.audio_file):
            _start_transcribe_job(m)


# ---------- page / i18n ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/i18n")
def api_i18n(lang: str = ""):
    code = i18n.normalize(lang) or get_lang()
    return {"lang": code, "strings": i18n.ui_strings(code), "available": i18n.available()}


# ---------- library ----------

def _active_job_detail(meeting_id: str) -> str | None:
    for j in JOBS.values():
        if j.get("meeting_id") == meeting_id and j.get("status") == "running":
            return j.get("detail")
    return None


@app.get("/api/meetings")
def api_meetings():
    out = []
    for m in list_meetings():
        s = m.summary()
        if s["status"] == "transcribing":
            detail = _active_job_detail(m.id)
            if detail:
                s["job_detail"] = detail
        out.append(s)
    return out


@app.get("/api/meetings/{mid}")
def api_meeting(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    reports = [{"name": p.stem, "label": report_label(p.stem),
                "content": p.read_text(encoding="utf-8")} for p in m.reports()]
    return {**m.summary(), "transcript": m.transcript_text(), "report_list": reports,
            "job_detail": _active_job_detail(mid)}


@app.delete("/api/meetings/{mid}")
def api_delete_meeting(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    try:
        dest = delete_meeting(m)
    except RuntimeError as e:
        _err(e)
    return {"ok": True, "trash": str(dest)}


def _safe_name(name: str) -> None:
    if "/" in name or "\\" in name or ".." in name:
        _err(ValueError(t("server.bad_name")))


@app.delete("/api/meetings/{mid}/reports/{name}")
def api_delete_report(mid: str, name: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    _safe_name(name)
    dest = _trash_file(m.reports_dir / f"{name}.md", f"report~{m.id}~{name}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/search")
def api_search(q: str = ""):
    return search_library(q)


def _post_import_memory(mtg: Meeting) -> None:
    """Merge imported text into long-term memory in the background."""
    if not load_config().get("auto_memory", True):
        return

    def work():
        try:
            ai.update_longterm(mtg)
        except Exception as e:
            try:
                mtg.save_meta(memory_error=str(e))
            except Exception:
                pass

    _bg(work)


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), title: str = Form(""), model: str = Form("")):
    suffix = Path(file.filename or "audio").suffix.lower()
    if suffix not in AUDIO_EXTS and suffix not in TEXT_EXTS:
        _err(ValueError(t("server.unsupported_type", ext=suffix)))
    ensure_dirs()
    tmp = Path(tempfile.gettempdir()) / f"coco_upload_{uuid.uuid4().hex[:6]}{suffix}"
    try:  # stream to disk in chunks; never hold a whole video in memory
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        _err(RuntimeError(t("server.tmp_write_failed", detail=str(e))), 500)
    name = title or Path(file.filename).stem
    if suffix not in TEXT_EXTS and not detect_backend():
        tmp.unlink(missing_ok=True)
        _require_transcriber()
    if suffix in TEXT_EXTS:  # existing text material: straight into the library
        try:
            mtg = import_transcript_file(tmp, title=name, source="text")
        except Exception as e:
            _err(ValueError(t("server.parse_failed", detail=str(e))))
        finally:
            tmp.unlink(missing_ok=True)
        _post_import_memory(mtg)
        return {"meeting_id": mtg.id, "job": None, "text": True}
    mtg = create_meeting(name, audio_path=tmp, source="upload", move=True)
    jid = _start_transcribe_job(mtg, model or None)
    return {"meeting_id": mtg.id, "job": jid}


class ImportBody(BaseModel):
    path: str
    title: str = ""
    model: str = ""


# several text formats of the same recording (Whisper export folder): keep the richest one
_TEXT_PREF = {".json": 0, ".srt": 1, ".vtt": 2, ".sbv": 3, ".ass": 3, ".ssa": 3, ".lrc": 4,
              ".tsv": 5, ".csv": 5, ".md": 6, ".markdown": 7, ".txt": 8, ".rtf": 9,
              ".html": 9, ".htm": 9, ".docx": 10, ".odt": 10, ".pdf": 11, ".eml": 12}


def _plan_folder_import(files: "list[Path]") -> "list[tuple[Path, Path | None]]":
    """Plan [(main file, companion audio)]: same-stem text files collapse to the richest one;
    text + same-stem audio = 'recording with existing transcript' (archive audio, do not transcribe)."""
    best_text: dict[str, Path] = {}
    for f in files:
        sfx = f.suffix.lower()
        if sfx in TEXT_EXTS:
            cur = best_text.get(f.stem)
            if cur is None or _TEXT_PREF.get(sfx, 20) < _TEXT_PREF.get(cur.suffix.lower(), 20):
                best_text[f.stem] = f
    audio_by_stem = {f.stem: f for f in files if f.suffix.lower() in AUDIO_EXTS}
    plan = []
    for f in files:
        sfx = f.suffix.lower()
        if sfx in TEXT_EXTS:
            if best_text[f.stem] is f:
                plan.append((f, audio_by_stem.get(f.stem)))
        elif f.stem not in best_text:
            plan.append((f, None))
    return plan


@app.post("/api/import")
def api_import(body: ImportBody):
    """Import a local file or folder (folder = every audio / video / text file inside)."""
    p = Path(body.path.strip().strip("'\"")).expanduser()
    if not p.exists():
        _err(FileNotFoundError(t("server.path_missing", path=str(p))))
    ok_exts = AUDIO_EXTS | TEXT_EXTS
    if p.is_dir():
        files = [f for f in sorted(p.iterdir())
                 if f.suffix.lower() in ok_exts and not f.name.startswith(".")]
        if not files:
            _err(ValueError(t("server.folder_empty", path=str(p))))
        plan = _plan_folder_import(files)
    else:
        if p.suffix.lower() not in ok_exts:
            _err(ValueError(t("server.unsupported_type", ext=p.suffix)))
        plan = [(p, None)]
    has_engine = bool(detect_backend())
    if not has_engine and all(f.suffix.lower() not in TEXT_EXTS for f, _ in plan):
        _require_transcriber()
    ensure_dirs()
    imported = []
    single = len(plan) == 1
    for f, audio in plan:
        title = body.title if (body.title and single) else f.stem
        if f.suffix.lower() in TEXT_EXTS:
            try:
                mtg = import_transcript_file(f, title=title, source="text", audio_path=audio)
            except Exception as e:
                if audio is not None and has_engine:  # unreadable text but audio present: transcribe
                    mtg = create_meeting(title, audio_path=audio, source="import")
                    imported.append({"meeting_id": mtg.id,
                                     "job": _start_transcribe_job(mtg, body.model or None)})
                else:
                    imported.append({"error": f"{f.name}: {e}"})
                continue
            _post_import_memory(mtg)
            imported.append({"meeting_id": mtg.id, "job": None, "text": True})
        elif not has_engine:
            imported.append({"error": f"{f.name}: {no_engine_hint()}", "audio": True})
        else:
            mtg = create_meeting(title, audio_path=f, source="import")
            imported.append({"meeting_id": mtg.id, "job": _start_transcribe_job(mtg, body.model or None)})
    return {"imported": imported, "count": len(imported)}


class ImportTextBody(BaseModel):
    content: str
    title: str = ""
    date: str = ""  # optional YYYY-MM-DD, the material's own date


@app.post("/api/import-text")
def api_import_text(body: ImportTextBody):
    """Pasted material: chat logs, e-mails, minutes, raw transcripts (SRT/VTT/JSON detected)."""
    if body.date:
        try:
            dt.date.fromisoformat(body.date)
        except ValueError:
            _err(ValueError(t("server.bad_date")))
    try:
        mtg = import_text(body.content, body.title, date=body.date)
    except ValueError as e:
        _err(e)
    _post_import_memory(mtg)
    return {"meeting_id": mtg.id, "format": mtg.meta.get("source_format", "paste")}


# ---------- recording ----------

class RecordStart(BaseModel):
    title: str = ""


@app.post("/api/record/start")
def api_record_start(body: RecordStart):
    if recorder.active:
        _err(RuntimeError(t("record.already_recording")))
    if not record_supported():
        _err(RuntimeError(record_unsupported_hint()))
    _require_transcriber()
    title = body.title or t("record.default_title", time=dt.datetime.now().strftime('%H%M'))
    mtg = create_meeting(title, source="record")
    try:
        recorder.start(mtg.path / "audio.wav", title)
    except RuntimeError as e:
        _err(e)
    recorder.meeting_id = mtg.id
    return {"meeting_id": mtg.id}


@app.post("/api/record/stop")
def api_record_stop():
    try:
        recorder.stop()
    except RuntimeError as e:
        _err(e)
    mtg = find_meeting(recorder.meeting_id)
    jid = _start_transcribe_job(mtg)
    return {"meeting_id": mtg.id, "job": jid}


@app.get("/api/record/status")
def api_record_status():
    return {"active": recorder.active, "elapsed": recorder.elapsed(),
            "title": recorder.title if recorder.active else ""}


# ---------- jobs ----------

@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        _err(LookupError(t("server.job_missing")), 404)
    return job


# ---------- AI (sync + streaming) ----------

def _sse(fn) -> StreamingResponse:
    """Run fn(on_delta) in a worker thread and stream its text as server-sent events:
    ``delta`` {text}, then ``done`` {result} or ``error`` {detail}."""
    q: "queue.Queue[tuple[str, object]]" = queue.Queue()
    lang = get_lang()

    def work():
        set_lang(lang)
        try:
            q.put(("done", fn(lambda text: q.put(("delta", {"text": text})))))
        except (LookupError, ValueError, ai.AIError) as e:
            q.put(("error", {"detail": str(e)}))
        except Exception:
            traceback.print_exc(file=sys.stderr)
            q.put(("error", {"detail": t("server.internal_error")}))

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            try:
                kind, payload = q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if kind in ("done", "error"):
                return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class AskBody(BaseModel):
    question: str
    ids: list[str] = []
    all: bool = False  # ask across every transcribed meeting


def _ask_targets(body: AskBody) -> list[Meeting]:
    if body.all:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    elif body.ids:
        meetings = [find_meeting(i) for i in body.ids]
    else:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()][:1]
    if not meetings:
        raise ai.AIError(t("ai.library_empty"))
    return meetings


@app.post("/api/ask")
def api_ask(body: AskBody):
    try:
        return {"answer": ai.ask(body.question, _ask_targets(body))}
    except (LookupError, ai.AIError) as e:
        _err(e)


@app.post("/api/stream/ask")
def api_stream_ask(body: AskBody):
    return _sse(lambda on: {"answer": ai.ask(body.question, _ask_targets(body), on_delta=on)})


class TrackBody(BaseModel):
    focus: str = ""
    mode: str = "track"  # track | signals | synthesis (legacy Chinese names accepted)
    ids: list[str] = []  # empty = every transcribed meeting


def _track(body: TrackBody, on=None) -> dict:
    meetings = [find_meeting(i) for i in body.ids] if body.ids else None
    path, content = ai.track(body.focus, mode=body.mode, meetings=meetings, on_delta=on)
    mid = resolve_track_mode(body.mode) or body.mode
    return {"path": path, "content": content, "name": Path(path).stem,
            "mode": mid, "mode_label": track_label(mid)}


@app.post("/api/track")
def api_track(body: TrackBody):
    try:
        return _track(body)
    except (LookupError, ai.AIError) as e:
        _err(e)


@app.post("/api/stream/track")
def api_stream_track(body: TrackBody):
    return _sse(lambda on: _track(body, on))


@app.get("/api/track-modes")
def api_track_modes():
    return list_track_modes()


class PrepBody(BaseModel):
    topic: str
    people: str = ""
    goal: str = ""
    web: bool = False


def _prep(body: PrepBody, on=None) -> dict:
    path, content = ai.prep(body.topic, body.people, body.goal, use_web=body.web, on_delta=on)
    return {"path": path, "content": content, "name": Path(path).stem}


@app.post("/api/prep")
def api_prep(body: PrepBody):
    try:
        return _prep(body)
    except ai.AIError as e:
        _err(e)


@app.post("/api/stream/prep")
def api_stream_prep(body: PrepBody):
    return _sse(lambda on: _prep(body, on))


@app.get("/api/preps")
def api_preps():
    return ai.list_preps()


@app.delete("/api/preps/{name}")
def api_delete_prep(name: str):
    _safe_name(name)
    dest = _trash_file(PREP_DIR / f"{name}.md", f"prep~{name}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/download/prep/{name}")
def dl_prep(name: str):
    _safe_name(name)
    return _md_download(PREP_DIR / f"{name}.md", f"{t('files.prep')}-{name}.md")


@app.delete("/api/tracking/{name}")
def api_delete_tracking(name: str):
    _safe_name(name)
    dest = _trash_file(TRACKING_DIR / f"{name}.md", f"tracking~{name}")
    return {"ok": True, "trash": str(dest)}


class ReportBody(BaseModel):
    id: str
    template: str


def _report(body: ReportBody, on=None) -> dict:
    mtg = find_meeting(body.id)
    path, content = ai.generate_report(mtg, body.template, on_delta=on)
    stem = Path(path).stem
    return {"path": path, "content": content, "name": stem, "label": report_label(stem),
            "meeting_id": mtg.id}


@app.post("/api/report")
def api_report(body: ReportBody):
    try:
        return _report(body)
    except (LookupError, ai.AIError) as e:
        _err(e)


@app.post("/api/stream/report")
def api_stream_report(body: ReportBody):
    return _sse(lambda on: _report(body, on))


class BriefBody(BaseModel):
    date: str = ""


def _brief(body: BriefBody, on=None) -> dict:
    path, content = ai.daily_brief(body.date or None, on_delta=on)
    if load_config().get("auto_memory", True):
        def merge():
            try:  # regenerated brief: force a fresh merge
                ai.memorize_brief(Path(path), force=True)
            except Exception:
                pass
        _bg(merge)
    return {"path": path, "content": content, "date": Path(path).stem}


@app.post("/api/brief")
def api_brief(body: BriefBody):
    try:
        return _brief(body)
    except ai.AIError as e:
        _err(e)


@app.post("/api/stream/brief")
def api_stream_brief(body: BriefBody):
    return _sse(lambda on: _brief(body, on))


@app.get("/api/briefs")
def api_briefs():
    if not BRIEFS_DIR.exists():
        return []
    return [{"date": p.stem, "content": p.read_text(encoding="utf-8")}
            for p in sorted(BRIEFS_DIR.glob("*.md"), reverse=True)]


@app.delete("/api/briefs/{date}")
def api_delete_brief(date: str):
    _safe_name(date)
    dest = _trash_file(BRIEFS_DIR / f"{date}.md", f"brief~{date}")
    return {"ok": True, "trash": str(dest)}


# ---------- weekly ----------

class WeeklyBody(BaseModel):
    date: str = ""  # any day of the week (YYYY-MM-DD); empty = this week


def _weekly(body: WeeklyBody, on=None) -> dict:
    try:
        path, content = ai.weekly_brief(body.date or None, on_delta=on)
    except ValueError:
        raise ai.AIError(t("server.bad_date"))
    return {"path": path, "content": content, "week": Path(path).stem}


@app.post("/api/weekly")
def api_weekly(body: WeeklyBody):
    try:
        return _weekly(body)
    except ai.AIError as e:
        _err(e)


@app.post("/api/stream/weekly")
def api_stream_weekly(body: WeeklyBody):
    return _sse(lambda on: _weekly(body, on))


@app.get("/api/weeklies")
def api_weeklies():
    """Existing weeklies + every week covered by the library (null content = not generated yet)."""
    weeks: dict[str, "str | None"] = {}
    if WEEKLY_DIR.exists():
        for p in sorted(WEEKLY_DIR.glob("*.md"), reverse=True):
            weeks[p.stem] = p.read_text(encoding="utf-8")
    for m in list_meetings():
        if m.transcript_md.exists() and m.date:
            _, _, wk = ai.week_bounds(m.date)
            weeks.setdefault(wk, None)
    return [{"week": w, "content": weeks[w], "range": "%s ~ %s" % ai.week_bounds(
        dt.date.fromisocalendar(int(w[:4]), int(w[6:]), 1).isoformat())[:2]}
        for w in sorted(weeks, reverse=True)]


@app.delete("/api/weeklies/{week}")
def api_delete_weekly(week: str):
    _safe_name(week)
    dest = _trash_file(WEEKLY_DIR / f"{week}.md", f"weekly~{week}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/download/weekly/{week}")
def dl_weekly(week: str):
    _safe_name(week)
    return _md_download(WEEKLY_DIR / f"{week}.md", f"{t('files.weekly')}-{week}.md")


# ---------- config / settings ----------

@app.get("/api/config")
def api_config():
    cfg = load_config()
    return {
        "whisper_model": cfg["whisper_model"],
        "language": cfg.get("language", "auto"),
        "ui_language": cfg.get("ui_language", "auto"),
        "output_language": cfg.get("output_language", "ui"),
        "available_languages": i18n.available(),
        "platform": ("mac" if sys.platform == "darwin"
                     else "windows" if sys.platform.startswith("win") else "linux"),
        "apple_silicon": is_apple_silicon(),
        "transcribe": detect_backend(cfg),      # "mlx" | "faster" | "" (cannot transcribe audio)
        "transcribe_device": detect_device(cfg),  # "mlx" | "cuda" | "cpu" | ""
        "record": record_supported() and bool(detect_backend(cfg)),
        "text_exts": sorted(TEXT_EXTS),
        "timed_exts": sorted(TIMED_EXTS),
        "audio_exts": sorted(AUDIO_EXTS),
        "memory_merge": cfg.get("memory_merge", "delta"),
        "ai": _ai_settings(cfg),
    }


class ConfigBody(BaseModel):
    whisper_model: str | None = None
    language: str | None = None
    ui_language: str | None = None
    output_language: str | None = None
    memory_merge: str | None = None


def _valid_lang(v: str) -> bool:
    return v == "auto" or (v.isalpha() and 2 <= len(v) <= 3)


@app.post("/api/config")
def api_config_save(body: ConfigBody):
    cfg = load_config()
    if body.whisper_model is not None:
        if body.whisper_model not in ("turbo", "large"):
            _err(ValueError(t("server.bad_model")))
        cfg["whisper_model"] = body.whisper_model
    if body.language is not None:
        if not _valid_lang(body.language):
            _err(ValueError(t("server.bad_language")))
        cfg["language"] = body.language
    if body.ui_language is not None:
        if body.ui_language != "auto" and not i18n.normalize(body.ui_language):
            _err(ValueError(t("server.bad_ui_language",
                              codes=", ".join(l["code"] for l in i18n.available()))))
        cfg["ui_language"] = "auto" if body.ui_language == "auto" else i18n.normalize(body.ui_language)
    if body.output_language is not None:
        v = body.output_language.strip()
        if v not in ("ui", "source") and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{2,4})?", v):
            _err(ValueError(t("server.bad_output_language")))
        cfg["output_language"] = v
    if body.memory_merge is not None:
        if body.memory_merge not in ("delta", "full"):
            _err(ValueError(t("server.bad_memory_merge")))
        cfg["memory_merge"] = body.memory_merge
    save_config(cfg)
    return {"ok": True, "whisper_model": cfg["whisper_model"], "language": cfg.get("language", "auto"),
            "ui_language": cfg.get("ui_language", "auto"),
            "output_language": cfg.get("output_language", "ui"),
            "memory_merge": cfg.get("memory_merge", "delta")}


def _ai_settings(cfg: dict) -> dict:
    profiles = cfg.get("ai_profiles") or {}
    tasks = dict(providers.TASK_PROFILE_DEFAULTS)
    tasks.update({k: v for k, v in (cfg.get("ai_tasks") or {}).items() if v in providers.PROFILE_NAMES})
    return {
        "primary": providers.public_profile(providers.get_profile("primary", cfg)),
        "fast": providers.public_profile(providers.get_profile("fast", cfg)),
        "fast_configured": bool(profiles.get("fast")),
        "tasks": tasks,
        "presets": providers.PRESETS,
        "claude_bin_found": bool(shutil.which(cfg.get("claude_bin") or "claude")),
    }


class AIProfileBody(BaseModel):
    type: str = "claude-cli"
    model: str = ""
    base_url: str = ""
    api_key: str | None = None  # None = keep the stored key; "" = clear it
    api_key_env: str = ""
    max_context_chars: int = 0
    max_tokens: int = 16000
    extra_args: list[str] = []


class AISettingsBody(BaseModel):
    primary: AIProfileBody | None = None
    fast: AIProfileBody | None = None
    fast_same_as_primary: bool | None = None
    tasks: dict[str, str] | None = None
    memory_merge: str | None = None


def _profile_dict(body: AIProfileBody, old: dict) -> dict:
    if body.type not in providers.PROFILE_TYPES:
        _err(ValueError(t("server.bad_profile_type", types=", ".join(providers.PROFILE_TYPES))))
    d = {"type": body.type, "model": body.model.strip(), "base_url": body.base_url.strip().rstrip("/"),
         "api_key_env": body.api_key_env.strip(), "max_context_chars": max(0, int(body.max_context_chars)),
         "max_tokens": max(256, int(body.max_tokens)), "extra_args": [str(a) for a in body.extra_args]}
    d["api_key"] = old.get("api_key", "") if body.api_key is None else body.api_key.strip()
    return d


@app.get("/api/settings/ai")
def api_ai_settings():
    return _ai_settings(load_config())


@app.post("/api/settings/ai")
def api_ai_settings_save(body: AISettingsBody):
    cfg = load_config()
    profiles = dict(cfg.get("ai_profiles") or {})
    if body.primary is not None:
        profiles["primary"] = _profile_dict(body.primary, profiles.get("primary") or {})
    if body.fast_same_as_primary:
        profiles.pop("fast", None)
    elif body.fast is not None:
        profiles["fast"] = _profile_dict(body.fast, profiles.get("fast") or {})
    cfg["ai_profiles"] = profiles
    if body.tasks is not None:
        cfg["ai_tasks"] = {k: v for k, v in body.tasks.items()
                           if k in providers.TASK_PROFILE_DEFAULTS and v in providers.PROFILE_NAMES}
    if body.memory_merge is not None:
        if body.memory_merge not in ("delta", "full"):
            _err(ValueError(t("server.bad_memory_merge")))
        cfg["memory_merge"] = body.memory_merge
    save_config(cfg)
    return {"ok": True, **_ai_settings(cfg)}


class AITestBody(BaseModel):
    profile: str = "primary"            # test a stored profile …
    inline: AIProfileBody | None = None  # … or a not-yet-saved one from the settings form


@app.post("/api/settings/ai/test")
def api_ai_test(body: AITestBody):
    cfg = load_config()
    if body.inline is not None:
        old = (cfg.get("ai_profiles") or {}).get(body.profile) or {}
        p = dict(providers.PROFILE_DEFAULTS)
        p.update(_profile_dict(body.inline, old))
        p["name"] = body.profile
        return providers.test_profile(profile=p)
    if body.profile not in providers.PROFILE_NAMES:
        _err(ValueError(t("server.bad_profile_name")))
    return providers.test_profile(body.profile)


# ---------- transcript / reports editing, name fixing ----------

class TranscriptBody(BaseModel):
    content: str


class MeetingMetaBody(BaseModel):
    title: str | None = None
    date: str | None = None  # YYYY-MM-DD, manual recording date
    participants: str | None = None  # "Name (role), Name (role)" – also written into the transcript header


@app.post("/api/meetings/{mid}/meta")
def api_save_meeting_meta(mid: str, body: MeetingMetaBody):
    """Rename / re-date a meeting (folder and id stay stable)."""
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    updates: dict = {}
    if body.title is not None:
        tt = body.title.strip()
        if not tt:
            _err(ValueError(t("server.title_empty")))
        updates["title"] = tt
    if body.date is not None:
        d = body.date.strip()
        try:
            dt.date.fromisoformat(d)
        except ValueError:
            _err(ValueError(t("server.bad_date")))
        updates["date"] = d
    if body.participants is not None:
        if len(body.participants) > 1000:
            _err(ValueError(t("server.participants_too_long")))
        with ai.TRANSCRIPT_LOCK:
            set_participants(m, body.participants)
        updates["participants"] = m.meta.get("participants", "")
    if not updates:
        _err(ValueError(t("server.nothing_to_update")))
    m.save_meta(**{k: v for k, v in updates.items() if k != "participants"})
    return {"ok": True, **m.summary()}


@app.post("/api/meetings/{mid}/participants/detect")
def api_detect_participants(mid: str):
    """Suggest 'Name (role), …' from the transcript; the UI lets the user confirm before saving."""
    try:
        m = find_meeting(mid)
        return {"participants": ai.detect_participants(m)}
    except (LookupError, ai.AIError) as e:
        _err(e)


class ChatSaveBody(BaseModel):
    content: str


@app.post("/api/meetings/{mid}/chat")
def api_save_chat(mid: str, body: ChatSaveBody):
    """Store an exported chat as reports/chat-HHMM.md so it shows up as a tab of the meeting."""
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if not body.content.strip():
        _err(ValueError(t("server.content_empty")))
    m.reports_dir.mkdir(exist_ok=True)
    path = ai._unique_path(m.reports_dir, f"chat-{dt.datetime.now().strftime('%H%M')}")
    path.write_text(body.content.rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "name": path.stem, "label": report_label(path.stem)}


@app.post("/api/meetings/{mid}/transcript")
def api_save_transcript(mid: str, body: TranscriptBody):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if not body.content.strip():
        _err(ValueError(t("server.content_empty")))
    with ai.TRANSCRIPT_LOCK:
        m.transcript_md.write_text(body.content.rstrip() + "\n", encoding="utf-8")
        meta = {"edited_at": dt.datetime.now().isoformat(timespec="seconds")}
        who = participants_from_transcript(body.content)  # header line edited by hand → keep meta in sync
        if who is not None:
            meta["participants"] = who
        m.save_meta(**meta)
    return {"ok": True}


@app.post("/api/fix-names/{mid}")
def api_fix_names(mid: str):
    try:
        m = find_meeting(mid)
        return {"content": ai.fix_names(m)}
    except (LookupError, ai.AIError) as e:
        _err(e)


@app.post("/api/meetings/{mid}/restore-raw")
def api_restore_raw(mid: str):
    try:
        m = find_meeting(mid)
        return {"content": ai.restore_raw(m)}
    except (LookupError, ai.AIError) as e:
        _err(e)


FIX_ALL_LOCK = threading.Lock()


def _start_job(label: str, fn) -> str:
    """Generic background job with progress text; fn(progress) -> detail string."""
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"status": "running", "detail": label, "meeting_id": None}
    lang = get_lang()

    def work():
        set_lang(lang)
        try:
            detail = fn(lambda msg: JOBS[jid].update(detail=msg))
            JOBS[jid].update(status="done", detail=detail)
        except Exception as e:
            JOBS[jid].update(status="error", detail=str(e))

    threading.Thread(target=work, daemon=True).start()
    return jid


class FixAllBody(BaseModel):
    confirm: bool = False


@app.post("/api/fix-all-names")
def api_fix_all_names(body: FixAllBody = FixAllBody()):
    """Whole-library name correction. Destructive → requires confirm=true, otherwise preview only."""
    ready = [m for m in list_meetings() if m.transcript_md.exists()]
    if not body.confirm:
        return {"preview": True, "meeting_count": len(ready),
                "detail": t("server.fixall_preview", n=len(ready))}

    def run(progress):
        if not FIX_ALL_LOCK.acquire(blocking=False):
            raise RuntimeError(t("server.fixall_busy"))
        try:
            stats = ai.fix_all_names(progress=progress)
        finally:
            FIX_ALL_LOCK.release()
        detail = t("server.fixall_done", meetings=stats["meetings"],
                   transcripts=stats["transcripts"], reports=stats["reports"])
        if stats["errors"]:
            detail += t("server.fixall_skipped", n=len(stats["errors"]))
        return detail

    return {"job": _start_job(t("server.fixall_preparing"), run)}


class ReportEditBody(BaseModel):
    content: str


@app.post("/api/meetings/{mid}/reports/{name}")
def api_save_report(mid: str, name: str, body: ReportEditBody):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    _safe_name(name)
    path = m.reports_dir / f"{name}.md"
    if not path.exists():
        _err(FileNotFoundError(t("server.report_missing", name=name)), 404)
    if not body.content.strip():
        _err(ValueError(t("server.content_empty")))
    path.write_text(body.content.rstrip() + "\n", encoding="utf-8")
    m.save_meta(report_edited_at=dt.datetime.now().isoformat(timespec="seconds"))
    return {"ok": True}


# ---------- downloads ----------

def _md_download(path: Path, filename: str):
    if not path.exists():
        _err(FileNotFoundError(t("server.file_missing", name=path.name)), 404)
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=filename)


def _attachment(content: bytes, media: str, filename: str) -> Response:
    disp = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type=media, headers={"Content-Disposition": disp})


@app.get("/api/download/brief/{date}")
def dl_brief(date: str):
    _safe_name(date)
    return _md_download(BRIEFS_DIR / f"{date}.md", f"{t('files.brief')}-{date}.md")


@app.get("/api/download/tracking/{name}")
def dl_tracking(name: str):
    _safe_name(name)
    return _md_download(TRACKING_DIR / f"{name}.md", f"{t('files.tracking')}-{name}.md")


def _srt_ts(sec: float, sep: str = ",") -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def render_transcript(m: Meeting, fmt: str) -> "tuple[bytes, str, str]":
    """Transcript in md / txt / srt / vtt / json → (bytes, media type, extension)."""
    segs = m.segments()
    timed = [s for s in segs if s.get("start") is not None]
    for i, s in enumerate(timed):  # fill missing end times from the next cue
        if s.get("end") is None:
            s["end"] = timed[i + 1]["start"] if i + 1 < len(timed) else s["start"] + 5

    def line(s):
        sp = s.get("speaker")
        return f"{sp}: {s['text']}" if sp and not s["text"].lower().startswith(str(sp).lower()) else s["text"]

    if fmt == "json":
        return m.transcript_json.read_bytes(), "application/json", "json"
    if fmt == "txt":
        if segs:
            text = "\n".join(line(s) for s in segs)
        else:  # plain import / no json: body of the markdown without header and stamps
            body = m.transcript_text().split("\n## ", 1)[-1].split("\n", 1)[-1]
            text = re.sub(r"^\[\d+:\d{2}(?::\d{2})?\]\s*", "", body, flags=re.M)
        return (text.strip() + "\n").encode("utf-8"), "text/plain; charset=utf-8", "txt"
    if fmt in ("srt", "vtt"):
        if not timed:
            raise ValueError(t("server.no_timing"))
        out = ["WEBVTT", ""] if fmt == "vtt" else []
        sep = "." if fmt == "vtt" else ","
        for i, s in enumerate(timed, 1):
            if fmt == "srt":
                out.append(str(i))
            out.append(f"{_srt_ts(s['start'], sep)} --> {_srt_ts(s['end'], sep)}")
            out.append(line(s))
            out.append("")
        media = "text/vtt; charset=utf-8" if fmt == "vtt" else "application/x-subrip; charset=utf-8"
        return "\n".join(out).encode("utf-8"), media, fmt
    return m.transcript_md.read_bytes(), "text/markdown; charset=utf-8", "md"


@app.get("/api/download/meeting/{mid}/transcript")
def dl_transcript(mid: str, fmt: str = "md"):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if fmt not in ("md", "txt", "srt", "vtt", "json"):
        _err(ValueError(t("server.bad_format")))
    if not m.transcript_md.exists() or (fmt == "json" and not m.transcript_json.exists()):
        _err(FileNotFoundError(t("server.file_missing", name=f"transcript.{fmt}")), 404)
    try:
        data, media, ext = render_transcript(m, fmt)
    except ValueError as e:
        _err(e)
    return _attachment(data, media, f"{m.title}-{t('files.transcript')}.{ext}")


MEMORY_FILES = {"content": (MEMORY_FILE, "memory"), "longterm": (LONGTERM_FILE, "longterm"),
                "glossary": (GLOSSARY_FILE, "glossary")}


@app.get("/api/download/memory/{which}")
def dl_memory(which: str):
    if which not in MEMORY_FILES:
        _err(ValueError(t("server.bad_memory_which")))
    path, kind = MEMORY_FILES[which]
    ensure_dirs()
    return _md_download(path, f"{t('files.' + kind)}.md")


@app.get("/api/download/meeting/{mid}/report/{name}")
def dl_report(mid: str, name: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    _safe_name(name)
    return _md_download(m.reports_dir / f"{name}.md", f"{m.title}-{report_label(name)}.md")


# ---------- bulk export (zip of .md files) ----------

@app.get("/api/export/manifest")
def api_export_manifest():
    meetings = []
    for m in list_meetings():
        files = []
        if m.transcript_md.exists():
            files.append({"type": "transcript", "name": "", "label": t("files.transcript")})
        for p in m.reports():
            files.append({"type": "report", "name": p.stem, "label": report_label(p.stem)})
        if files:
            meetings.append({"id": m.id, "title": m.title, "date": m.date, "files": files})

    def listing(folder):
        return ([{"name": p.stem, "label": p.stem} for p in sorted(folder.glob("*.md"), reverse=True)]
                if folder.exists() else [])

    return {"meetings": meetings, "briefs": listing(BRIEFS_DIR), "tracking": listing(TRACKING_DIR),
            "preps": listing(PREP_DIR), "weeklies": listing(WEEKLY_DIR)}


def _safe_seg(s: str) -> str:
    return re.sub(r"[\\/]+", "_", s).strip().strip(".") or "untitled"


class ExportItem(BaseModel):
    type: str  # transcript | report | brief | tracking | prep | weekly
    id: str = ""
    name: str = ""


class ExportBody(BaseModel):
    items: list[ExportItem]


def _resolve_export_item(it: ExportItem) -> "tuple[Path | None, str]":
    """Export item → (disk path, path inside the zip); invalid → (None, '')."""
    name = it.name or ""
    if "/" in name or "\\" in name or ".." in name:
        return None, ""
    if it.type in ("transcript", "report"):
        try:
            m = find_meeting(it.id)
        except LookupError:
            return None, ""
        folder = _safe_seg(f"{m.date}-{m.title}")
        if it.type == "transcript":
            return m.transcript_md, f"{folder}/{_safe_seg(t('files.transcript'))}.md"
        return m.reports_dir / f"{name}.md", f"{folder}/{_safe_seg(report_label(name))}.md"
    groups = {"brief": (BRIEFS_DIR, "brief"), "tracking": (TRACKING_DIR, "tracking"),
              "prep": (PREP_DIR, "prep"), "weekly": (WEEKLY_DIR, "weekly")}
    if it.type in groups:
        folder, key = groups[it.type]
        return folder / f"{name}.md", f"{_safe_seg(t('files.' + key))}/{_safe_seg(name)}.md"
    return None, ""


@app.post("/api/export")
def api_export(body: ExportBody):
    if not body.items:
        _err(ValueError(t("server.export_nothing_selected")))
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in body.items:
            path, arc = _resolve_export_item(it)
            if path is None or not path.exists():
                continue
            base, n = arc, 1
            while arc in used:
                n += 1
                stem, dot, ext = base.rpartition(".")
                arc = f"{stem}-{n}.{ext}" if dot else f"{base}-{n}"
            used.add(arc)
            zf.write(path, arcname=arc)
    if not used:
        _err(ValueError(t("server.export_files_missing")))
    buf.seek(0)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"coco-{t('files.export')}-{stamp}.zip"
    disp = f"attachment; filename=coco-export-{stamp}.zip; filename*=UTF-8''{quote(fname)}"
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": disp})


# ---------- templates / memory ----------

@app.get("/api/templates")
def api_templates():
    return list_templates()


@app.get("/api/memory")
def api_memory():
    ensure_dirs()
    return {"content": MEMORY_FILE.read_text(encoding="utf-8"),
            "longterm": LONGTERM_FILE.read_text(encoding="utf-8"),
            "glossary": GLOSSARY_FILE.read_text(encoding="utf-8")}


class MemoryBody(BaseModel):
    content: str | None = None
    longterm: str | None = None
    glossary: str | None = None


def _backup_then_write(path: Path, new: str) -> bool:
    """Back the old content up as .bak.md before overwriting (single-slot undo)."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    backed = bool(old.strip()) and old != new
    if backed:
        path.with_suffix(".bak.md").write_text(old, encoding="utf-8")
    path.write_text(new, encoding="utf-8")
    return backed


@app.post("/api/memory")
def api_memory_save(body: MemoryBody):
    ensure_dirs()
    with ai.MEMORY_LOCK:
        if body.content is not None:
            _backup_then_write(MEMORY_FILE, body.content)
        if body.longterm is not None:
            _backup_then_write(LONGTERM_FILE, body.longterm)
        if body.glossary is not None:
            _backup_then_write(GLOSSARY_FILE, body.glossary)
    return {"ok": True}


class MemoryClearBody(BaseModel):
    which: str  # content | longterm | glossary


@app.post("/api/memory/clear")
def api_memory_clear(body: MemoryClearBody):
    if body.which not in MEMORY_FILES:
        _err(ValueError(t("server.bad_memory_which")))
    ensure_dirs()
    path, kind = MEMORY_FILES[body.which]
    placeholder = memory_placeholder(kind)
    with ai.MEMORY_LOCK:
        backed = _backup_then_write(path, placeholder)
    return {"ok": True, "content": placeholder,
            "backup": str(path.with_suffix(".bak.md")) if backed else None}


@app.post("/api/memory/compact")
def api_memory_compact():
    """Rewrite long-term memory once to merge / compress (background job)."""
    def run(progress):
        ai.compact_longterm(progress=progress)
        return t("server.compact_done")
    return {"job": _start_job(t("ai.compacting"), run)}


@app.post("/api/glossary/extract")
def api_glossary_extract():
    try:
        content = ai.extract_glossary()
    except ai.AIError as e:
        _err(e)
    return {"ok": True, "glossary": content}


# ---------- knowledge base ----------

def _section_items(text: str) -> dict[str, list[str]]:
    """Top-level bullet entries per canonical section ('people', 'projects', …)."""
    out: dict[str, list[str]] = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            section = i18n.canonical_section(s) or s[3:].strip()
            out.setdefault(section, [])
        elif section and (line.startswith("- ") or line.startswith("* ")):
            out[section].append(line[2:].strip())
    return out


@app.get("/api/knowledge")
def api_knowledge():
    ensure_dirs()
    longterm = LONGTERM_FILE.read_text(encoding="utf-8")
    glossary = GLOSSARY_FILE.read_text(encoding="utf-8")
    sections = _section_items(longterm)
    from .glossary import glossary_stats
    gs = glossary_stats(glossary)
    persons = sections.get("people", [])
    names = []
    for p in persons:  # entries look like "**Name**: note" or "Name: note"
        n = re.split(r"[:：（(]", p.replace("*", ""), 1)[0].strip()
        if 0 < len(n) <= 40:
            names.append(n)
    meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    return {
        "stats": {"meetings": len(meetings), "persons": len(persons),
                  "projects": len(sections.get("projects", [])),
                  "promises": len(sections.get("commitments", [])),
                  "glossary": gs["names"] + gs["terms"]},
        "person_names": names,
        "longterm": longterm,
        "glossary": glossary,
    }


@app.exception_handler(Exception)
def on_error(request, exc):
    traceback.print_exc(file=sys.stderr)  # stack to the terminal; a fixed message to the browser
    return JSONResponse(status_code=500, content={"detail": t("server.internal_error")})
