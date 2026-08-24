"""coco 本地 Web 服务（FastAPI，仅监听 127.0.0.1）。"""
from __future__ import annotations

import datetime as dt
import io
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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import ai
from .config import (BRIEFS_DIR, GLOSSARY_FILE, GLOSSARY_PLACEHOLDER,
                     LONGTERM_FILE, LONGTERM_PLACEHOLDER, MEMORY_FILE,
                     MEMORY_PLACEHOLDER, PREP_DIR, TRACKING_DIR, TRASH_DIR,
                     WEEKLY_DIR, ensure_dirs, load_config, save_config)
from .ingest import TEXT_EXTS, import_text, import_transcript_file
from .library import (AUDIO_EXTS, Meeting, create_meeting, delete_meeting,
                      find_meeting, list_meetings, search_library)
from .recorder import Recorder
from .templates import TEMPLATES, TRACK_MODES
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

def _active_job_detail(meeting_id: str) -> str | None:
    """按 meeting_id 反查正在运行的转写任务的阶段文字（刷新后丢了 jid 也能拿到进度）。"""
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
    reports = [
        {"name": p.stem, "content": p.read_text(encoding="utf-8")}
        for p in m.reports()
    ]
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


def _post_import_memory(mtg: Meeting) -> None:
    """文字材料导入后台并入长期记忆（转写件走转写任务里的同一步骤）。"""
    if not load_config().get("auto_memory", True):
        return

    def work():
        try:
            ai.update_longterm(mtg)
        except Exception as e:
            try:  # 会议可能在排队期间被删除，记录失败本身也不能抛
                mtg.save_meta(memory_error=str(e))
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), title: str = Form(""),
                     model: str = Form("")):
    suffix = Path(file.filename or "audio").suffix.lower()
    if suffix not in AUDIO_EXTS and suffix not in TEXT_EXTS:
        _err(ValueError(f"不支持的文件类型：{suffix}"))
    ensure_dirs()
    tmp = Path(tempfile.gettempdir()) / f"coco_upload_{uuid.uuid4().hex[:6]}{suffix}"
    try:  # 分块流式落盘，避免把整段大音视频一次性读入内存
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as e:
        tmp.unlink(missing_ok=True)  # 中途失败清理残留的半截临时文件
        _err(RuntimeError(f"写入临时文件失败（磁盘空间不足？）：{e}"), 500)
    name = title or Path(file.filename).stem
    if suffix in TEXT_EXTS:  # 已有文字材料：不转写直接入库
        try:
            mtg = import_transcript_file(tmp, title=name, source="文本导入")
        except Exception as e:
            _err(ValueError(f"解析失败：{e}"))
        finally:
            tmp.unlink(missing_ok=True)
        _post_import_memory(mtg)
        return {"meeting_id": mtg.id, "job": None, "text": True}
    mtg = create_meeting(name, audio_path=tmp, source="上传", move=True)
    jid = _start_transcribe_job(mtg, model or None)
    return {"meeting_id": mtg.id, "job": jid}


class ImportBody(BaseModel):
    path: str
    title: str = ""
    model: str = ""


# 同一录音的多种转写格式并存时（Whisper 导出目录），按信息量选一种，避免重复建会议
_TEXT_PREF = {".json": 0, ".srt": 1, ".vtt": 2, ".md": 3, ".markdown": 4, ".txt": 5}


def _plan_folder_import(files: "list[Path]") -> "list[tuple[Path, Path | None]]":
    """把文件夹里的文件规划成导入项 [(主文件, 配套音频)]。

    同名（同 stem）规则：多种文字格式只留信息量最高的一种；
    文字 + 同名音频视为「音频 + 现成转写」，用文字建会议、音频归档，不再转写。
    """
    best_text: dict[str, Path] = {}
    for f in files:
        sfx = f.suffix.lower()
        if sfx in TEXT_EXTS:
            cur = best_text.get(f.stem)
            if cur is None or _TEXT_PREF[sfx] < _TEXT_PREF[cur.suffix.lower()]:
                best_text[f.stem] = f
    audio_by_stem = {f.stem: f for f in files if f.suffix.lower() in AUDIO_EXTS}
    plan = []
    for f in files:
        sfx = f.suffix.lower()
        if sfx in TEXT_EXTS:
            if best_text[f.stem] is f:
                plan.append((f, audio_by_stem.get(f.stem)))
        elif f.stem not in best_text:  # 有同名转写的音频不再单独转写
            plan.append((f, None))
    return plan


@app.post("/api/import")
def api_import(body: ImportBody):
    """导入本地文件或文件夹（文件夹则批量导入其中所有音频/视频/文字材料）。"""
    p = Path(body.path.strip().strip("'\"")).expanduser()
    if not p.exists():
        _err(FileNotFoundError(f"路径不存在：{p}"))
    ok_exts = AUDIO_EXTS | TEXT_EXTS
    if p.is_dir():
        files = [f for f in sorted(p.iterdir())
                 if f.suffix.lower() in ok_exts and not f.name.startswith(".")]
        if not files:
            _err(ValueError(f"文件夹里没有可识别的音频/视频/文字文件：{p}"))
        plan = _plan_folder_import(files)
    else:
        if p.suffix.lower() not in ok_exts:
            _err(ValueError(f"不支持的文件类型：{p.suffix}"))
        plan = [(p, None)]
    ensure_dirs()
    imported = []
    single = len(plan) == 1
    for f, audio in plan:
        title = body.title if (body.title and single) else f.stem
        if f.suffix.lower() in TEXT_EXTS:
            try:
                mtg = import_transcript_file(f, title=title, source="文本导入",
                                             audio_path=audio)
            except Exception as e:
                if audio is not None:  # 文字解析失败但有同名音频：退回正常转写
                    mtg = create_meeting(title, audio_path=audio, source="本地导入")
                    imported.append({"meeting_id": mtg.id,
                                     "job": _start_transcribe_job(mtg, body.model or None)})
                else:
                    imported.append({"error": f"{f.name}：{e}"})
                continue
            _post_import_memory(mtg)
            imported.append({"meeting_id": mtg.id, "job": None, "text": True})
        else:
            mtg = create_meeting(title, audio_path=f, source="本地导入")
            imported.append({"meeting_id": mtg.id,
                             "job": _start_transcribe_job(mtg, body.model or None)})
    return {"imported": imported, "count": len(imported)}


class ImportTextBody(BaseModel):
    content: str
    title: str = ""
    date: str = ""  # 可选 YYYY-MM-DD，材料的原始日期


@app.post("/api/import-text")
def api_import_text(body: ImportTextBody):
    """粘贴文字材料入库：聊天记录、邮件、他人纪要等私人上下文。"""
    if body.date:
        try:
            dt.date.fromisoformat(body.date)
        except ValueError:
            _err(ValueError("日期格式应为 YYYY-MM-DD"))
    try:
        mtg = import_text(body.content, body.title, date=body.date)
    except ValueError as e:
        _err(e)
    _post_import_memory(mtg)
    return {"meeting_id": mtg.id}


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
    mode: str = "追踪"  # 追踪 | 深层信号 | 调研综合
    ids: list[str] = []  # 留空 = 全部已转写会议


@app.post("/api/track")
def api_track(body: TrackBody):
    try:
        meetings = [find_meeting(i) for i in body.ids] if body.ids else None
        path, content = ai.track(body.focus, mode=body.mode, meetings=meetings)
        return {"path": path, "content": content, "name": Path(path).stem}
    except (LookupError, ai.AIError) as e:
        _err(e)


@app.get("/api/track-modes")
def api_track_modes():
    return [{"name": k, "desc": v[1]} for k, v in TRACK_MODES.items()]


# ---------- 会前调查 ----------

class PrepBody(BaseModel):
    topic: str
    people: str = ""
    goal: str = ""
    web: bool = False  # 是否联网搜索公开信息


@app.post("/api/prep")
def api_prep(body: PrepBody):
    try:
        path, content = ai.prep(body.topic, body.people, body.goal,
                                use_web=body.web)
        return {"path": path, "content": content, "name": Path(path).stem}
    except ai.AIError as e:
        _err(e)


@app.get("/api/preps")
def api_preps():
    return ai.list_preps()


@app.delete("/api/preps/{name}")
def api_delete_prep(name: str):
    if "/" in name or ".." in name:
        _err(ValueError("非法文件名"))
    dest = _trash_file(PREP_DIR / f"{name}.md", f"会前调查~{name}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/download/prep/{name}")
def dl_prep(name: str):
    if "/" in name or ".." in name:
        _err(ValueError("非法文件名"))
    return _md_download(PREP_DIR / f"{name}.md", f"会前调查-{name}.md")


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


# ---------- 周报 ----------

class WeeklyBody(BaseModel):
    date: str = ""  # 该周内任意一天（YYYY-MM-DD），留空=本周


@app.post("/api/weekly")
def api_weekly(body: WeeklyBody):
    try:
        path, content = ai.weekly_brief(body.date or None)
    except ValueError:
        _err(ValueError("日期格式应为 YYYY-MM-DD"))
    except ai.AIError as e:
        _err(e)
    return {"path": path, "content": content, "week": Path(path).stem}


@app.get("/api/weeklies")
def api_weeklies():
    """已有周报列表 + 会议库覆盖到的全部周（未生成的标 null，供补生成）。"""
    weeks: dict[str, "str | None"] = {}
    if WEEKLY_DIR.exists():
        for p in sorted(WEEKLY_DIR.glob("*.md"), reverse=True):
            weeks[p.stem] = p.read_text(encoding="utf-8")
    for m in list_meetings():
        if m.transcript_md.exists() and m.date:
            _, _, wk = ai.week_bounds(m.date)
            weeks.setdefault(wk, None)
    return [{"week": w, "content": weeks[w], "range": "%s ~ %s" % ai.week_bounds(
        # 由周名反推该周周一：ISO 周第 1 天
        dt.date.fromisocalendar(int(w[:4]), int(w[6:]), 1).isoformat())[:2]}
        for w in sorted(weeks, reverse=True)]


@app.delete("/api/weeklies/{week}")
def api_delete_weekly(week: str):
    if "/" in week or ".." in week:
        _err(ValueError("非法周名"))
    dest = _trash_file(WEEKLY_DIR / f"{week}.md", f"周报~{week}")
    return {"ok": True, "trash": str(dest)}


@app.get("/api/download/weekly/{week}")
def dl_weekly(week: str):
    if "/" in week or ".." in week:
        _err(ValueError("非法周名"))
    return _md_download(WEEKLY_DIR / f"{week}.md", f"周报-{week}.md")


# ---------- 配置 / 转写编辑 / 人名校正 / 报告编辑 ----------

@app.get("/api/config")
def api_config():
    cfg = load_config()
    return {"whisper_model": cfg["whisper_model"], "language": cfg.get("language", "auto")}


class ConfigBody(BaseModel):
    whisper_model: str | None = None
    language: str | None = None


def _valid_lang(v: str) -> bool:
    return v == "auto" or (v.isalpha() and 2 <= len(v) <= 3)


@app.post("/api/config")
def api_config_save(body: ConfigBody):
    cfg = load_config()
    if body.whisper_model is not None:
        if body.whisper_model not in ("turbo", "large"):
            _err(ValueError("模型只能是 turbo 或 large"))
        cfg["whisper_model"] = body.whisper_model
    if body.language is not None:
        if not _valid_lang(body.language):
            _err(ValueError("语言只能是 auto 或 ISO 码（如 zh、en、fr）"))
        cfg["language"] = body.language
    save_config(cfg)
    return {"ok": True, "whisper_model": cfg["whisper_model"],
            "language": cfg.get("language", "auto")}


class TranscriptBody(BaseModel):
    content: str


class MeetingMetaBody(BaseModel):
    title: str | None = None
    date: str | None = None  # YYYY-MM-DD，手动校准录音日期


@app.post("/api/meetings/{mid}/meta")
def api_save_meeting_meta(mid: str, body: MeetingMetaBody):
    """修改会议标题 / 校准录音日期（不重命名文件夹，id 保持稳定）。"""
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    updates: dict = {}
    if body.title is not None:
        t = body.title.strip()
        if not t:
            _err(ValueError("标题不能为空"))
        updates["title"] = t
    if body.date is not None:
        d = body.date.strip()
        try:
            dt.date.fromisoformat(d)  # 同时校验格式与是否真实日期
        except ValueError:
            _err(ValueError("日期格式应为 YYYY-MM-DD 且是有效日期"))
        updates["date"] = d
    if not updates:
        _err(ValueError("没有要更新的字段"))
    m.save_meta(**updates)
    return {"ok": True, **m.summary()}


@app.post("/api/meetings/{mid}/transcript")
def api_save_transcript(mid: str, body: TranscriptBody):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if not body.content.strip():
        _err(ValueError("内容为空，未保存"))
    with ai.TRANSCRIPT_LOCK:
        m.transcript_md.write_text(body.content.rstrip() + "\n", encoding="utf-8")
        m.save_meta(edited_at=dt.datetime.now().isoformat(timespec="seconds"))
    return {"ok": True}


@app.post("/api/fix-names/{mid}")
def api_fix_names(mid: str):
    try:
        m = find_meeting(mid)
        content = ai.fix_names(m)
        return {"content": content}
    except (LookupError, ai.AIError) as e:
        _err(e)


@app.post("/api/meetings/{mid}/restore-raw")
def api_restore_raw(mid: str):
    """恢复人名校正前的原稿（transcript.raw.md → transcript.md）。"""
    try:
        m = find_meeting(mid)
        content = ai.restore_raw(m)
        return {"content": content}
    except (LookupError, ai.AIError) as e:
        _err(e)


FIX_ALL_LOCK = threading.Lock()  # 全库人名校正一次只允许一个在跑


def _start_fix_all_job() -> str:
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"status": "running", "detail": "准备校正…", "meeting_id": None}

    def work():
        if not FIX_ALL_LOCK.acquire(blocking=False):
            JOBS[jid].update(status="error", detail="已有一个全库校正在进行中，请等它结束")
            return
        try:
            stats = ai.fix_all_names(progress=lambda msg: JOBS[jid].update(detail=msg))
            detail = (f"完成：{stats['meetings']} 场，修正转写 {stats['transcripts']}、"
                      f"报告 {stats['reports']}")
            if stats["errors"]:
                detail += f"，{len(stats['errors'])} 项跳过"
            JOBS[jid].update(status="done", detail=detail, stats=stats)
        except Exception as e:
            JOBS[jid].update(status="error", detail=str(e))
        finally:
            FIX_ALL_LOCK.release()

    threading.Thread(target=work, daemon=True).start()
    return jid


class FixAllBody(BaseModel):
    confirm: bool = False


@app.post("/api/fix-all-names")
def api_fix_all_names(body: FixAllBody = FixAllBody()):
    """全库按长期记忆校正人名（转写+报告）。破坏性操作：必须 confirm=true 才执行，
    否则只返回将影响的会议数量（预览），避免误调/冒烟测试改动真实数据。"""
    ready = [m for m in list_meetings() if m.transcript_md.exists()]
    if not body.confirm:
        return {"preview": True, "meeting_count": len(ready),
                "detail": f"将按记忆校正 {len(ready)} 场会议的转写与报告（需 confirm=true 执行）"}
    return {"job": _start_fix_all_job()}


class ReportEditBody(BaseModel):
    content: str


@app.post("/api/meetings/{mid}/reports/{name}")
def api_save_report(mid: str, name: str, body: ReportEditBody):
    """保存编辑后的报告正文（覆盖原 .md，不改文件名）。"""
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    if "/" in name or ".." in name:
        _err(ValueError("非法报告名"))
    path = m.reports_dir / f"{name}.md"
    if not path.exists():
        _err(FileNotFoundError(f"报告不存在：{name}"), 404)
    if not body.content.strip():
        _err(ValueError("内容为空，未保存"))
    path.write_text(body.content.rstrip() + "\n", encoding="utf-8")
    m.save_meta(report_edited_at=dt.datetime.now().isoformat(timespec="seconds"))
    return {"ok": True}


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
    return _md_download(TRACKING_DIR / f"{name}.md", f"跨会议洞察-{name}.md")


@app.get("/api/download/meeting/{mid}/transcript")
def dl_transcript(mid: str):
    try:
        m = find_meeting(mid)
    except LookupError as e:
        _err(e, 404)
    return _md_download(m.transcript_md, f"{m.title}-转写.md")


MEMORY_FILES = {"content": (MEMORY_FILE, "全局记忆", MEMORY_PLACEHOLDER),
                "longterm": (LONGTERM_FILE, "长期记忆", LONGTERM_PLACEHOLDER),
                "glossary": (GLOSSARY_FILE, "词表", GLOSSARY_PLACEHOLDER)}


@app.get("/api/download/memory/{which}")
def dl_memory(which: str):
    if which not in MEMORY_FILES:
        _err(ValueError("which 只能是 content、longterm 或 glossary"))
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


# ---------- 批量导出（多个 .md 打包 zip） ----------

def _report_label(name: str) -> str:
    # 行动项-1355 → 行动项 13:55，与页签显示一致
    return re.sub(r"-(\d{2})(\d{2})$", r" \1:\2", name)


@app.get("/api/export/manifest")
def api_export_manifest():
    """列出全部可导出的 .md，供导出弹窗逐个勾选。"""
    meetings = []
    for m in list_meetings():
        files = []
        if m.transcript_md.exists():
            files.append({"type": "transcript", "name": "", "label": "转写"})
        for p in m.reports():
            files.append({"type": "report", "name": p.stem,
                          "label": _report_label(p.stem)})
        if files:
            meetings.append({"id": m.id, "title": m.title,
                             "date": m.date, "files": files})
    briefs = ([{"name": p.stem, "label": p.stem}
               for p in sorted(BRIEFS_DIR.glob("*.md"), reverse=True)]
              if BRIEFS_DIR.exists() else [])
    tracking = ([{"name": p.stem, "label": p.stem}
                 for p in sorted(TRACKING_DIR.glob("*.md"), reverse=True)]
                if TRACKING_DIR.exists() else [])
    preps = ([{"name": p.stem, "label": p.stem}
              for p in sorted(PREP_DIR.glob("*.md"), reverse=True)]
             if PREP_DIR.exists() else [])
    weeklies = ([{"name": p.stem, "label": p.stem}
                 for p in sorted(WEEKLY_DIR.glob("*.md"), reverse=True)]
                if WEEKLY_DIR.exists() else [])
    return {"meetings": meetings, "briefs": briefs, "tracking": tracking,
            "preps": preps, "weeklies": weeklies}


def _safe_seg(s: str) -> str:
    """清洗成 zip 内安全的单段文件/目录名（去掉分隔符与首尾点）。"""
    return re.sub(r"[\\/]+", "_", s).strip().strip(".") or "未命名"


def _resolve_export_item(it: "ExportItem") -> "tuple[Path | None, str]":
    """把一条导出项安全解析为 (磁盘路径, zip 内相对路径)。非法项返回 (None, '')。

    所有 name 都禁止包含路径分隔符与 ..，会议路径只通过 find_meeting 解析已存在的会议，
    杜绝路径穿越。
    """
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
            return m.transcript_md, f"{folder}/转写.md"
        return m.reports_dir / f"{name}.md", f"{folder}/{_safe_seg(_report_label(name))}.md"
    if it.type == "brief":
        return BRIEFS_DIR / f"{name}.md", f"每日简报/{_safe_seg(name)}.md"
    if it.type == "tracking":
        return TRACKING_DIR / f"{name}.md", f"跨会议洞察/{_safe_seg(name)}.md"
    if it.type == "prep":
        return PREP_DIR / f"{name}.md", f"会前调查/{_safe_seg(name)}.md"
    if it.type == "weekly":
        return WEEKLY_DIR / f"{name}.md", f"周报/{_safe_seg(name)}.md"
    return None, ""


class ExportItem(BaseModel):
    type: str  # transcript | report | brief | tracking | prep | weekly
    id: str = ""
    name: str = ""


class ExportBody(BaseModel):
    items: list[ExportItem]


@app.post("/api/export")
def api_export(body: ExportBody):
    """把选中的多个 .md 打包成 zip 返回。"""
    if not body.items:
        _err(ValueError("没有选择任何文件"))
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in body.items:
            path, arc = _resolve_export_item(it)
            if path is None or not path.exists():
                continue
            base, n = arc, 1
            while arc in used:  # 不同会议可能产生同名 arc，避免覆盖
                n += 1
                stem, dot, ext = base.rpartition(".")
                arc = f"{stem}-{n}.{ext}" if dot else f"{base}-{n}"
            used.add(arc)
            zf.write(path, arcname=arc)
    if not used:
        _err(ValueError("选中的文件都不存在"))
    buf.seek(0)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"coco-导出-{stamp}.zip"
    disp = f"attachment; filename=coco-export-{stamp}.zip; filename*=UTF-8''{quote(fname)}"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": disp})


# ---------- 模板 / 记忆 ----------

@app.get("/api/templates")
def api_templates():
    return [{"name": k, "desc": v["desc"]} for k, v in TEMPLATES.items()]


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
        if body.glossary is not None:
            _backup_then_write(GLOSSARY_FILE, body.glossary)
    return {"ok": True}


class MemoryClearBody(BaseModel):
    which: str  # content | longterm | glossary


@app.post("/api/memory/clear")
def api_memory_clear(body: MemoryClearBody):
    if body.which not in MEMORY_FILES:
        _err(ValueError("which 只能是 content、longterm 或 glossary"))
    ensure_dirs()
    path, _, placeholder = MEMORY_FILES[body.which]
    with ai.MEMORY_LOCK:
        backed = _backup_then_write(path, placeholder)
    return {"ok": True, "content": placeholder,
            "backup": str(path.with_suffix(".bak.md")) if backed else None}


@app.post("/api/glossary/extract")
def api_glossary_extract():
    """AI 从长期记忆与最近转写中提炼词表（人名/专有名词），合并进 glossary.md。"""
    try:
        content = ai.extract_glossary()
    except ai.AIError as e:
        _err(e)
    return {"ok": True, "glossary": content}


# ---------- 知识底座 ----------

def _section_items(text: str) -> dict[str, list[str]]:
    """把 Markdown 按「## 章节」切分，取每节的【顶层】条目行（无缩进的 - / *）。

    缩进的子条目（如人物名下的时间线补充）属于父条目，不单独计数。
    """
    out: dict[str, list[str]] = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            out.setdefault(section, [])
        elif section and (line.startswith("- ") or line.startswith("* ")):
            out[section].append(line[2:].strip())
    return out


@app.get("/api/knowledge")
def api_knowledge():
    """知识底座总览：统计 + 长期记忆 + 词表（供底座页面渲染）。"""
    ensure_dirs()
    longterm = LONGTERM_FILE.read_text(encoding="utf-8")
    glossary = GLOSSARY_FILE.read_text(encoding="utf-8")
    sections = _section_items(longterm)
    from .glossary import glossary_stats
    gs = glossary_stats(glossary)
    persons = sections.get("人物", [])
    # 人物条目通常是「**张三**：备注」或「张三：备注」，取名字部分做点选提问
    names = []
    for p in persons:
        n = re.split(r"[:：（(]", p.replace("*", ""), 1)[0].strip()
        if 0 < len(n) <= 20:
            names.append(n)
    meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    return {
        "stats": {
            "meetings": len(meetings),
            "persons": len(persons),
            "projects": len(sections.get("项目与客户", [])),
            "promises": len(sections.get("承诺与决定", [])),
            "glossary": gs["names"] + gs["terms"],
        },
        "person_names": names,
        "longterm": longterm,
        "glossary": glossary,
    }


@app.exception_handler(Exception)
def on_error(request, exc):
    # 堆栈打到终端供本人排查；只回固定文案给前端，不泄漏内部路径/异常细节
    traceback.print_exc(file=sys.stderr)
    return JSONResponse(status_code=500,
                        content={"detail": "服务器内部错误，请查看运行 coco 的终端日志"})
