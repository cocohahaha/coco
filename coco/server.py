"""coco 本地 Web 服务（FastAPI，仅监听 127.0.0.1）。"""
from __future__ import annotations

import datetime as dt
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import ai
from .config import (BRIEFS_DIR, LONGTERM_FILE, LONGTERM_PLACEHOLDER,
                     MEMORY_FILE, MEMORY_PLACEHOLDER, TRACKING_DIR, TRASH_DIR,
                     ensure_dirs, load_config, save_config)
from .library import (AUDIO_EXTS, Meeting, create_meeting, delete_meeting,
                      find_meeting, list_meetings, search_library)
from .recorder import Recorder
from .templates import TEMPLATES
from .transcriber import transcribe_meeting

app = FastAPI(title="coco", docs_url=None, redoc_url=None)
recorder = Recorder()
JOBS: dict[str, dict] = {}  # jid -> {status, detail, meeting_id, error}
STATIC = Path(__file__).parent / "static"
TRANSCRIBE_LOCK = threading.Lock()  # 转写串行执行，避免多个模型同时加载


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


def _trash_file(path: Path, dest_stem: str) -> Path:
    """单个 .md 文件软删除：移入回收站 library/_trash，可手动找回。"""
    if not path.exists():
        _err(FileNotFoundError(f"文件不存在：{path.name}"), 404)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = TRASH_DIR / f"{dest_stem}~{stamp}.md"
    shutil.move(str(path), dest)
    return dest


def _start_transcribe_job(mtg: Meeting, model: str | None = None) -> str:
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"status": "running", "detail": "排队中…", "meeting_id": mtg.id}

    def work():
        try:
            if not mtg.path.exists():
                raise FileNotFoundError("会议在排队期间被删除")
            with TRANSCRIBE_LOCK:
                JOBS[jid].update(detail="转写中…")
                transcribe_meeting(
                    mtg, model=model,
                    progress=lambda msg: JOBS[jid].update(detail=msg),
                )
            if load_config().get("auto_memory", True):
                try:
                    JOBS[jid].update(detail="提取长期记忆…")
                    ai.update_longterm(mtg)
                except Exception as e:  # 记忆失败不影响转写结果
                    mtg.save_meta(memory_error=str(e))
            JOBS[jid].update(status="done", detail="转写完成")
        except Exception as e:
            JOBS[jid].update(status="error", detail=str(e))

    threading.Thread(target=work, daemon=True).start()
    return jid


@app.on_event("startup")
def resume_interrupted():
    """服务重启后，把上次被打断或还在排队的转写任务重新排队。

    覆盖两种情况：转写到一半被杀（transcribing）、排队中被杀（new）。
    """
    for m in list_meetings():
        if (m.meta.get("status") in ("new", "transcribing")
                and not m.transcript_md.exists() and m.audio_file):
            _start_transcribe_job(m)


# ---------- 页面 ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------- 会议库 ----------

@app.get("/api/meetings")
def api_meetings():
    return [m.summary() for m in list_meetings()]


@app.get("/api/meetings/{mid}")
def api_meeting(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    reports = [
        {"name": p.stem, "content": p.read_text(encoding="utf-8")}
        for p in m.reports()
    ]
    return {**m.summary(), "transcript": m.transcript_text(), "report_list": reports}


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


@app.delete("/api/meetings/{mid}/reports/{name}")
def api_delete_report(mid: str, name: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if "/" in name or ".." in name:
        _err(ValueError("非法报告名"))
    dest = _trash_file(m.reports_dir / f"{name}.md", f"报告~{m.id}~{name}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/search")
def api_search(q: str = ""):
    return search_library(q)


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), title: str = Form(""),
                     model: str = Form("")):
    suffix = Path(file.filename or "audio").suffix.lower()
    if suffix not in AUDIO_EXTS:
        _err(ValueError(f"不支持的文件类型：{suffix}"))
    ensure_dirs()
    tmp = Path("/tmp") / f"coco_upload_{uuid.uuid4().hex[:6]}{suffix}"
    tmp.write_bytes(await file.read())
    mtg = create_meeting(title or Path(file.filename).stem,
                         audio_path=tmp, source="上传", move=True)
    jid = _start_transcribe_job(mtg, model or None)
    return {"meeting_id": mtg.id, "job": jid}


class ImportBody(BaseModel):
    path: str
    title: str = ""
    model: str = ""


@app.post("/api/import")
def api_import(body: ImportBody):
    """导入本地文件或文件夹（文件夹则批量导入其中所有音频/视频）。"""
    p = Path(body.path.strip().strip("'\"")).expanduser()
    if not p.exists():
        _err(FileNotFoundError(f"路径不存在：{p}"))
    if p.is_dir():
        files = [f for f in sorted(p.iterdir())
                 if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith(".")]
        if not files:
            _err(ValueError(f"文件夹里没有可识别的音频/视频文件：{p}"))
    else:
        if p.suffix.lower() not in AUDIO_EXTS:
            _err(ValueError(f"不支持的文件类型：{p.suffix}"))
        files = [p]
    ensure_dirs()
    imported = []
    for f in files:
        mtg = create_meeting(body.title if (body.title and len(files) == 1) else f.stem,
                             audio_path=f, source="本地导入")
        imported.append({"meeting_id": mtg.id,
                         "job": _start_transcribe_job(mtg, body.model or None)})
    return {"imported": imported, "count": len(imported)}


# ---------- 录音 ----------

class RecordStart(BaseModel):
    title: str = ""


@app.post("/api/record/start")
def api_record_start(body: RecordStart):
    if recorder.active:
        _err(RuntimeError("已有录音在进行中"))
    title = body.title or f"录音-{dt.datetime.now().strftime('%H%M')}"
    mtg = create_meeting(title, source="录音")
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
    return {
        "active": recorder.active,
        "elapsed": recorder.elapsed(),
        "title": recorder.title if recorder.active else "",
    }


# ---------- 任务状态 ----------

@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        _err(LookupError("任务不存在"), 404)
    return job


# ---------- AI ----------

class AskBody(BaseModel):
    question: str
    ids: list[str] = []
    all: bool = False  # 跨全部已转写会议提问


@app.post("/api/ask")
def api_ask(body: AskBody):
    try:
        if body.all:
            meetings = [m for m in list_meetings() if m.transcript_md.exists()]
        elif body.ids:
            meetings = [find_meeting(i) for i in body.ids]
        else:
            meetings = [m for m in list_meetings() if m.transcript_md.exists()][:1]
        if not meetings:
            raise ai.AIError("会议库中没有已转写的会议")
        return {"answer": ai.ask(body.question, meetings)}
    except (LookupError, ai.AIError) as e:
        _err(e)


class TrackBody(BaseModel):
    focus: str = ""


@app.post("/api/track")
def api_track(body: TrackBody):
    try:
        path, content = ai.track(body.focus)
        return {"path": path, "content": content, "name": Path(path).stem}
    except ai.AIError as e:
        _err(e)


@app.delete("/api/tracking/{name}")
def api_delete_tracking(name: str):
    if "/" in name or ".." in name:
        _err(ValueError("非法文件名"))
    dest = _trash_file(TRACKING_DIR / f"{name}.md", f"追踪~{name}")
    return {"ok": True, "trash": str(dest)}


class ReportBody(BaseModel):
    id: str
    template: str


@app.post("/api/report")
def api_report(body: ReportBody):
    try:
        mtg = find_meeting(body.id)
        path, content = ai.generate_report(mtg, body.template)
        return {"path": path, "content": content}
    except (LookupError, ai.AIError) as e:
        _err(e)


class BriefBody(BaseModel):
    date: str = ""


@app.post("/api/brief")
def api_brief(body: BriefBody):
    try:
        path, content = ai.daily_brief(body.date or None)
    except ai.AIError as e:
        _err(e)
    if load_config().get("auto_memory", True):
        def merge():
            try:  # 简报刚重新生成，内容有变，强制重新合并
                ai.memorize_brief(Path(path), force=True)
            except Exception:
                pass
        threading.Thread(target=merge, daemon=True).start()
    return {"path": path, "content": content, "date": Path(path).stem}


@app.get("/api/briefs")
def api_briefs():
    if not BRIEFS_DIR.exists():
        return []
    return [
        {"date": p.stem, "content": p.read_text(encoding="utf-8")}
        for p in sorted(BRIEFS_DIR.glob("*.md"), reverse=True)
    ]


@app.delete("/api/briefs/{date}")
def api_delete_brief(date: str):
    if "/" in date or ".." in date:
        _err(ValueError("非法日期"))
    dest = _trash_file(BRIEFS_DIR / f"{date}.md", f"每日简报~{date}")
    return {"ok": True, "trash": str(dest)}


# ---------- 配置 / 转写编辑 / 校对 ----------

@app.get("/api/config")
def api_config():
    return {"whisper_model": load_config()["whisper_model"]}


class ConfigBody(BaseModel):
    whisper_model: str


@app.post("/api/config")
def api_config_save(body: ConfigBody):
    if body.whisper_model not in ("turbo", "large"):
        _err(ValueError("模型只能是 turbo 或 large"))
    cfg = load_config()
    cfg["whisper_model"] = body.whisper_model
    save_config(cfg)
    return {"ok": True, "whisper_model": body.whisper_model}


class TranscriptBody(BaseModel):
    content: str


@app.post("/api/meetings/{mid}/transcript")
def api_save_transcript(mid: str, body: TranscriptBody):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if not body.content.strip():
        _err(ValueError("内容为空，未保存"))
    m.transcript_md.write_text(body.content.rstrip() + "\n", encoding="utf-8")
    m.save_meta(edited_at=dt.datetime.now().isoformat(timespec="seconds"))
    return {"ok": True}


@app.post("/api/proofread/{mid}")
def api_proofread(mid: str):
    try:
        m = find_meeting(mid)
        content = ai.proofread(m)
        return {"content": content}
    except (LookupError, ai.AIError) as e:
        _err(e)


# ---------- 下载（.md 导出） ----------

def _md_download(path: Path, filename: str):
    if not path.exists():
        _err(FileNotFoundError(f"文件不存在：{path.name}"), 404)
    return FileResponse(path, media_type="text/markdown; charset=utf-8",
                        filename=filename)


@app.get("/api/download/brief/{date}")
def dl_brief(date: str):
    return _md_download(BRIEFS_DIR / f"{date}.md", f"每日简报-{date}.md")


@app.get("/api/download/tracking/{name}")
def dl_tracking(name: str):
    if "/" in name or ".." in name:
        _err(ValueError("非法文件名"))
    return _md_download(TRACKING_DIR / f"{name}.md", f"跨会议追踪-{name}.md")


@app.get("/api/download/meeting/{mid}/transcript")
def dl_transcript(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    return _md_download(m.transcript_md, f"{m.title}-转写.md")


MEMORY_FILES = {"content": (MEMORY_FILE, "全局记忆", MEMORY_PLACEHOLDER),
                "longterm": (LONGTERM_FILE, "长期记忆", LONGTERM_PLACEHOLDER)}


@app.get("/api/download/memory/{which}")
def dl_memory(which: str):
    if which not in MEMORY_FILES:
        _err(ValueError("which 只能是 content 或 longterm"))
    path, label, _ = MEMORY_FILES[which]
    ensure_dirs()
    return _md_download(path, f"{label}.md")


@app.get("/api/download/meeting/{mid}/report/{name}")
def dl_report(mid: str, name: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if "/" in name or ".." in name:
        _err(ValueError("非法报告名"))
    return _md_download(m.reports_dir / f"{name}.md", f"{m.title}-{name}.md")


# ---------- 模板 / 记忆 ----------

@app.get("/api/templates")
def api_templates():
    return [{"name": k, "desc": v["desc"]} for k, v in TEMPLATES.items()]


@app.get("/api/memory")
def api_memory():
    ensure_dirs()
    return {"content": MEMORY_FILE.read_text(encoding="utf-8"),
            "longterm": LONGTERM_FILE.read_text(encoding="utf-8")}


class MemoryBody(BaseModel):
    content: str | None = None
    longterm: str | None = None


def _backup_then_write(path: Path, new: str) -> bool:
    """覆盖前把旧内容备份为同目录 .bak.md（单槽撤销，与自动合并一致）。

    返回是否真的写了备份（内容没变化时不备份）。
    """
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
    return {"ok": True}


class MemoryClearBody(BaseModel):
    which: str  # content | longterm


@app.post("/api/memory/clear")
def api_memory_clear(body: MemoryClearBody):
    if body.which not in MEMORY_FILES:
        _err(ValueError("which 只能是 content 或 longterm"))
    ensure_dirs()
    path, _, placeholder = MEMORY_FILES[body.which]
    with ai.MEMORY_LOCK:
        backed = _backup_then_write(path, placeholder)
    return {"ok": True, "content": placeholder,
            "backup": str(path.with_suffix(".bak.md")) if backed else None}


@app.exception_handler(Exception)
def on_error(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
