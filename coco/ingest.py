"""导入已有的文字材料，不经转写直接入库（归集私人上下文）。

支持：纯文本 / Markdown（.txt / .md / .markdown）、字幕（.srt / .vtt）、
Whisper 风格 JSON（.json），以及直接粘贴的文本（聊天记录、邮件、他人纪要等）。
带时间轴的材料渲染成与本地转写一致的 `[mm:ss] 文本` 形态，
之后的报告、对话、简报、追踪、长期记忆全部照常工作。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .library import Meeting, create_meeting
from .transcriber import _fmt_ts

TEXT_EXTS = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json"}


def _ts_to_seconds(ts: str) -> float:
    """解析 'HH:MM:SS,mmm' / 'HH:MM:SS.mmm' / 'MM:SS' 为秒数。"""
    ts = ts.strip().replace(",", ".")
    try:
        parts = [float(p) for p in ts.split(":")]
    except ValueError:
        return 0.0
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0.0, parts[0], parts[1]
    elif len(parts) == 1:
        h, m, s = 0.0, 0.0, parts[0]
    else:
        return 0.0
    return h * 3600 + m * 60 + s


def _parse_subtitles(raw: str) -> list[dict]:
    """解析 .srt / .vtt 字幕块 → [{start, end, text}]。"""
    segments = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), -1)
        if ti < 0:
            continue
        m = re.search(r"([\d:.,]+)\s*-->\s*([\d:.,]+)", lines[ti])
        if not m:
            continue
        text = " ".join(lines[ti + 1:]).strip()
        text = re.sub(r"<[^>]+>", "", text)  # 去掉 VTT 内联标签如 <c>、<00:00:01.000>
        if text:
            segments.append({"start": round(_ts_to_seconds(m.group(1)), 2),
                             "end": round(_ts_to_seconds(m.group(2)), 2),
                             "text": text})
    return segments


def _parse_json(raw: str) -> dict:
    """解析 Whisper 风格 JSON：含 segments/text 的 dict，或 segment 列表。"""
    data = json.loads(raw)
    segs, text = None, ""
    if isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            segs = data["segments"]
        text = (data.get("text") or "").strip()
    elif isinstance(data, list):
        segs = data
    segments = []
    for s in (segs or []):
        if not isinstance(s, dict):
            continue
        t = (s.get("text") or "").strip()
        if not t:
            continue
        st, en = s.get("start"), s.get("end")
        segments.append({"start": round(float(st), 2) if st is not None else None,
                         "end": round(float(en), 2) if en is not None else None,
                         "text": t})
    if not segments and not text:
        raise ValueError("JSON 里没有可识别的 'segments' 或 'text' 字段")
    return {"segments": segments, "text": text}


def build_imported(raw: str, ext: str) -> dict:
    """按扩展名把原始文字材料规整成 {segments, text}。"""
    ext = ext.lower()
    if ext in (".srt", ".vtt"):
        segments = _parse_subtitles(raw)
        if not segments:
            raise ValueError(f"{ext} 文件里没有找到字幕条目")
        return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    if ext == ".json":
        r = _parse_json(raw)
        if not r["text"]:
            r["text"] = "\n".join(s["text"] for s in r["segments"])
        return r
    # .txt / .md / .markdown —— 纯文本原样使用
    text = raw.strip()
    if not text:
        raise ValueError("文件内容为空")
    return {"segments": [], "text": text}


def _write_meeting(mtg: Meeting, data: dict, fmt: str, source_label: str) -> None:
    segments = data["segments"]
    duration = ""
    if segments and any(s.get("start") is not None for s in segments):
        body = [(f"[{_fmt_ts(s['start'])}] {s['text']}" if s.get("start") is not None
                 else s["text"]) for s in segments]
        ends = [s["end"] for s in segments if s.get("end") is not None]
        if ends:
            duration = _fmt_ts(max(ends))
    else:
        body = [data["text"]]

    # 材料自带日期（如导入旧聊天记录）优先于入库时间，保证 AI 读到的时间线正确
    date_line = mtg.meta.get("date") or mtg.meta.get("created", "")
    header = [f"# {mtg.title}", "", f"- 日期：{date_line}"]
    if duration:
        header.append(f"- 时长：{duration}")
    header += [f"- 来源：{source_label}（{fmt}）", "", "## 转写", ""]
    mtg.transcript_md.write_text("\n".join(header + body).rstrip() + "\n",
                                 encoding="utf-8")
    mtg.transcript_json.write_text(json.dumps({
        "text": data["text"], "segments": segments, "duration": duration,
        "model": "imported", "language": "imported", "source_format": fmt,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    mtg.save_meta(status="done", duration=duration, whisper_model="imported",
                  language="imported",
                  imported_at=dt.datetime.now().isoformat(timespec="seconds"),
                  error=None)


def import_transcript_file(path: Path, title: str | None = None,
                           source: str = "文本导入") -> Meeting:
    """从已有的文字材料文件建会议（无音频、不转写）。原文件复制进会议文件夹留档。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    data = build_imported(raw, path.suffix)
    mtg = create_meeting(title or path.stem, source=source)
    try:
        shutil.copy2(path, mtg.path / f"source{path.suffix.lower()}")
    except OSError:
        pass  # 留档失败不影响导入
    _write_meeting(mtg, data, path.suffix.lstrip(".").lower(), source)
    return mtg


def import_text(content: str, title: str, date: str = "",
                source: str = "粘贴文本") -> Meeting:
    """从粘贴的文本建会议（聊天记录、邮件、他人纪要等私人上下文）。"""
    text = content.strip()
    if not text:
        raise ValueError("内容为空")
    mtg = create_meeting(title.strip() or "粘贴文本", source=source)
    if date:
        mtg.save_meta(date=date)
    _write_meeting(mtg, {"segments": [], "text": text}, "粘贴", source)
    return mtg
