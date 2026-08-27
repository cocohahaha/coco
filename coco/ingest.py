"""Import existing text material without transcribing (collect private context).

Supported: plain text / Markdown (.txt .md .markdown), Word (.docx), OpenDocument (.odt),
PDF (.pdf – needs the optional ``pypdf`` package), RTF (.rtf), HTML (.html .htm),
e-mail (.eml), spreadsheets / exports (.csv .tsv), subtitles (.srt .vtt .sbv .lrc
.ass .ssa), JSON transcripts (.json – Whisper style and most tool exports), plus text
pasted directly (chat logs, e-mails, other people's minutes …).

Transcripts exported by Zoom, Teams, Google Meet, Otter, Fireflies, Tencent Meeting,
Feishu Minutes, iFlytek, Plaud … can be dropped in as they are. Timed material is
rendered as ``[mm:ss] text`` exactly like a local transcription, and speaker labels
are kept, so reports, chat, briefs, insights and memory all work the same way.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import shutil
from pathlib import Path

from .i18n import sections, t
from .library import Meeting, create_meeting, participants_line
from .transcriber import _fmt_ts

TEXT_EXTS = {".txt", ".md", ".markdown", ".docx", ".odt", ".pdf", ".rtf", ".html", ".htm",
             ".eml", ".csv", ".tsv", ".srt", ".vtt", ".sbv", ".lrc", ".ass", ".ssa", ".json"}
BINARY_TEXT_EXTS = {".docx", ".odt", ".pdf", ".eml"}  # not plain text: dedicated readers
# Formats that keep timing information (rendered with [mm:ss] stamps)
TIMED_EXTS = {".srt", ".vtt", ".sbv", ".lrc", ".ass", ".ssa", ".json", ".csv", ".tsv"}


# ---------- timestamps ----------

def _ts_to_seconds(ts: str) -> float:
    """'HH:MM:SS,mmm' / 'HH:MM:SS.mmm' / 'MM:SS' / '12.5' → seconds."""
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


def _num(v) -> "float | None":
    """Timestamp cell → seconds: numeric seconds, or hh:mm:ss text."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return float(s)
    if ":" in s:
        return _ts_to_seconds(s)
    return None


# ---------- subtitle formats ----------

def _clean_tags(text: str) -> str:
    # WebVTT voice tags → "Name: " prefix (Teams etc. mark speakers this way)
    text = re.sub(r"<v(?:\.[^\s>]+)*\s+([^>]+)>", r"\1: ", text)
    text = re.sub(r"</?v[^>]*>", "", text)
    text = re.sub(r"<[^>]+>", "", text)  # other inline tags such as <c> or <00:00:01.000>
    text = re.sub(r"\{\\[^}]*\}", "", text)  # ASS override tags
    return re.sub(r"\s+", " ", text).strip()


def _parse_subtitles(raw: str) -> list[dict]:
    """.srt / .vtt blocks → [{start, end, text}]."""
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
        text = _clean_tags(" ".join(lines[ti + 1:]))
        if text:
            segments.append({"start": round(_ts_to_seconds(m.group(1)), 2),
                             "end": round(_ts_to_seconds(m.group(2)), 2), "text": text})
    return segments


def _parse_sbv(raw: str) -> list[dict]:
    """YouTube .sbv: '0:00:01.000,0:00:03.000' line followed by text lines."""
    segments = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.match(r"\s*([\d:.]+)\s*,\s*([\d:.]+)\s*$", lines[0])
        if not m:
            continue
        text = _clean_tags(" ".join(lines[1:]))
        if text:
            segments.append({"start": round(_ts_to_seconds(m.group(1)), 2),
                             "end": round(_ts_to_seconds(m.group(2)), 2), "text": text})
    return segments


_LRC_TAG = re.compile(r"\[(\d{1,2}:\d{2}(?:[.:]\d{1,3})?)\]")


def _parse_lrc(raw: str) -> list[dict]:
    """.lrc lyrics/transcript: '[mm:ss.xx] text' (several tags per line allowed)."""
    segments = []
    for line in raw.splitlines():
        tags = _LRC_TAG.findall(line)
        if not tags:
            continue
        text = _LRC_TAG.sub("", line).strip()
        if not text:
            continue
        for tg in tags:
            segments.append({"start": round(_ts_to_seconds(tg.replace(".", ":", 0)), 2),
                             "end": None, "text": text})
    segments.sort(key=lambda s: s["start"])
    for a, b in zip(segments, segments[1:]):
        a["end"] = b["start"]
    return segments


def _parse_ass(raw: str) -> list[dict]:
    """.ass / .ssa: 'Dialogue: layer,start,end,style,name,ml,mr,mv,effect,text'."""
    segments = []
    for line in raw.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line[len("Dialogue:"):].split(",", 9)
        if len(fields) < 10:
            continue
        start, end, name, text = fields[1], fields[2], fields[4].strip(), fields[9]
        text = _clean_tags(text.replace("\\N", " ").replace("\\n", " "))
        if text:
            seg = {"start": round(_ts_to_seconds(start), 2), "end": round(_ts_to_seconds(end), 2),
                   "text": text}
            if name:
                seg["speaker"] = name
            segments.append(seg)
    return segments


def looks_like_subtitles(raw: str) -> bool:
    return len(re.findall(r"\d[\d:.,]*\s*-->\s*\d[\d:.,]*", raw)) >= 2


# ---------- tabular ----------

_TEXT_COLS = ("text", "content", "transcript", "utterance", "sentence", "speech", "message",
              "内容", "文本", "发言", "消息", "转写", "texte", "contenu")
_START_COLS = ("start", "start_time", "starttime", "begin", "time", "timestamp", "offset",
               "开始", "时间", "起始", "début", "debut", "heure")
_END_COLS = ("end", "end_time", "endtime", "stop", "结束", "fin")
_SPEAKER_COLS = ("speaker", "speaker_name", "name", "who", "from", "sender", "participant",
                 "说话人", "发言人", "参与者", "姓名", "昵称", "orateur", "locuteur", "nom")


def _find_col(headers: list[str], names: tuple) -> "int | None":
    low = [h.strip().lower() for h in headers]
    for n in names:
        if n in low:
            return low.index(n)
    for i, h in enumerate(low):
        if any(h.startswith(n) for n in names):
            return i
    return None


def _parse_table(raw: str, delimiter: str) -> dict:
    rows = [r for r in csv.reader(io.StringIO(raw), delimiter=delimiter) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(t("ingest.empty_file"))
    headers = rows[0]
    ti = _find_col(headers, _TEXT_COLS)
    if ti is None:  # no header: try "start, end, text" triples, else join cells
        first = rows[0]
        if len(first) >= 3 and _num(first[0]) is not None and _num(first[1]) is not None:
            segs = [{"start": _num(r[0]), "end": _num(r[1]), "text": " ".join(c.strip() for c in r[2:] if c.strip())}
                    for r in rows if len(r) >= 3 and _num(r[0]) is not None]
            segs = [s for s in segs if s["text"]]
            if segs:
                return {"segments": segs, "text": "\n".join(s["text"] for s in segs)}
        lines = [" | ".join(c.strip() for c in r if c.strip()) for r in rows]
        return {"segments": [], "text": "\n".join(l for l in lines if l)}
    si, ei, pi = (_find_col(headers, _START_COLS), _find_col(headers, _END_COLS),
                  _find_col(headers, _SPEAKER_COLS))
    whisper_tsv = [h.strip().lower() for h in headers[:3]] == ["start", "end", "text"]
    segs = []
    for r in rows[1:]:
        if ti >= len(r):
            continue
        text = r[ti].strip()
        if not text:
            continue
        seg: dict = {"text": text}
        st = _num(r[si]) if si is not None and si < len(r) else None
        en = _num(r[ei]) if ei is not None and ei < len(r) else None
        if whisper_tsv and st is not None and float(st).is_integer():
            st = st / 1000.0  # whisper's .tsv is in milliseconds
            en = en / 1000.0 if en is not None else None
        if st is not None:
            seg["start"] = round(st, 2)
        if en is not None:
            seg["end"] = round(en, 2)
        if pi is not None and pi < len(r) and r[pi].strip():
            seg["speaker"] = r[pi].strip()
        segs.append(seg)
    if not segs:
        raise ValueError(t("ingest.table_no_text"))
    return {"segments": segs, "text": "\n".join(s["text"] for s in segs)}


# ---------- JSON ----------

_J_TEXT = ("text", "content", "transcript", "sentence", "utterance", "message", "punctuated_word")
_J_START = ("start", "start_time", "startTime", "begin", "offset", "timestamp", "ts", "time")
_J_END = ("end", "end_time", "endTime", "stop")
_J_SPEAKER = ("speaker", "speaker_name", "speakerName", "speaker_label", "name", "who", "sender")
_J_LISTS = ("segments", "sentences", "utterances", "transcripts", "monologues", "results",
            "items", "data", "messages", "paragraphs", "entries", "lines")


def _seg_from_obj(o: dict) -> "dict | None":
    text = next((o[k] for k in _J_TEXT if isinstance(o.get(k), str) and o[k].strip()), None)
    if text is None and isinstance(o.get("alternatives"), list) and o["alternatives"]:
        alt = o["alternatives"][0]
        if isinstance(alt, dict) and isinstance(alt.get("transcript"), str):
            text = alt["transcript"]
    if not text:
        return None
    seg: dict = {"text": text.strip()}
    for k in _J_START:
        if k in o and _num(o[k]) is not None:
            seg["start"] = _num(o[k])
            break
    for k in _J_END:
        if k in o and _num(o[k]) is not None:
            seg["end"] = _num(o[k])
            break
    for k in _J_SPEAKER:
        v = o.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            seg["speaker"] = str(v).strip()
            break
        if isinstance(v, dict) and isinstance(v.get("name"), str):
            seg["speaker"] = v["name"].strip()
            break
    return seg


def _find_segment_list(data, depth: int = 0) -> list[dict]:
    """Depth-first: the first list whose items mostly look like transcript segments."""
    if depth > 6:
        return []
    if isinstance(data, list):
        segs = [_seg_from_obj(x) for x in data if isinstance(x, dict)]
        good = [s for s in segs if s]
        if data and len(good) >= max(1, len(data) * 0.6):
            return good
        for x in data:
            found = _find_segment_list(x, depth + 1)
            if found:
                return found
        return []
    if isinstance(data, dict):
        for k in _J_LISTS:
            if k in data:
                found = _find_segment_list(data[k], depth + 1)
                if found:
                    return found
        for v in data.values():
            if isinstance(v, (dict, list)):
                found = _find_segment_list(v, depth + 1)
                if found:
                    return found
    return []


def _parse_json(raw: str) -> dict:
    """Whisper-style JSON, segment lists, and most transcription-tool exports."""
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError(t("ingest.bad_json", detail=str(e)[:80]))
    segments = _find_segment_list(data)
    text = ""
    if isinstance(data, dict):
        for k in ("text", "transcript", "content"):
            if isinstance(data.get(k), str) and data[k].strip():
                text = data[k].strip()
                break
        if not text:  # AWS Transcribe: results.transcripts[0].transcript
            tr = (data.get("results") or {}).get("transcripts") if isinstance(data.get("results"), dict) else None
            if isinstance(tr, list) and tr and isinstance(tr[0], dict):
                text = str(tr[0].get("transcript") or "").strip()
    if segments:
        starts = [s["start"] for s in segments if s.get("start") is not None]
        # integer offsets beyond 10 h are almost certainly milliseconds
        if starts and all(float(s).is_integer() for s in starts) and max(starts) > 36_000:
            for s in segments:
                for k in ("start", "end"):
                    if s.get(k) is not None:
                        s[k] = s[k] / 1000.0
        for s in segments:
            for k in ("start", "end"):
                if s.get(k) is not None:
                    s[k] = round(float(s[k]), 2)
        # word-level exports (hundreds of one-word items) → merge into ~sentences
        if len(segments) > 40 and sum(len(s["text"]) for s in segments) / len(segments) < 8:
            segments = _merge_words(segments)
    if not segments and not text:
        raise ValueError(t("ingest.json_no_text"))
    return {"segments": segments, "text": text or "\n".join(s["text"] for s in segments)}


def _merge_words(words: list[dict]) -> list[dict]:
    out, cur = [], None
    for w in words:
        if cur is None or (w.get("speaker") != cur.get("speaker")) or len(cur["text"]) > 200 \
                or re.search(r"[.!?。！？]$", cur["text"]):
            cur = {"start": w.get("start"), "end": w.get("end"), "text": w["text"]}
            if w.get("speaker"):
                cur["speaker"] = w["speaker"]
            out.append(cur)
        else:
            cur["text"] += ("" if re.match(r"^[,.!?;:，。！？；：]", w["text"]) else " ") + w["text"]
            cur["end"] = w.get("end", cur.get("end"))
    return out


# ---------- documents ----------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def read_docx_text(path: Path) -> str:
    """Text of a .docx via word/document.xml (standard library only). Paragraphs → lines,
    table cells in document order (meeting tools export speaker tables)."""
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError(t("ingest.bad_docx"))
    root = ET.fromstring(xml)
    paras = []
    for para in root.iter(f"{_W}p"):
        buf = []
        for el in para.iter():
            if el.tag == f"{_W}t":
                buf.append(el.text or "")
            elif el.tag == f"{_W}tab":
                buf.append("\t")
            elif el.tag in (f"{_W}br", f"{_W}cr"):
                buf.append("\n")
        paras.append("".join(buf).strip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(paras)).strip()
    if not text:
        raise ValueError(t("ingest.docx_empty"))
    return text


def read_odt_text(path: Path) -> str:
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("content.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError(t("ingest.bad_odt"))
    root = ET.fromstring(xml)
    paras = []
    for el in root.iter():
        if el.tag in (f"{_ODT_TEXT}p", f"{_ODT_TEXT}h"):
            buf = []
            for node in el.iter():
                if node.tag == f"{_ODT_TEXT}tab":
                    buf.append("\t")
                elif node.tag == f"{_ODT_TEXT}line-break":
                    buf.append("\n")
                elif node.tag == f"{_ODT_TEXT}s":
                    buf.append(" " * int(node.get(f"{_ODT_TEXT}c", "1") or 1))
                if node.text and node is not el or (node is el and node.text):
                    buf.append(node.text or "")
                if node is not el and node.tail:
                    buf.append(node.tail)
            paras.append("".join(buf).strip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(paras)).strip()
    if not text:
        raise ValueError(t("ingest.docx_empty"))
    return text


def read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError(t("ingest.pdf_needs_pypdf"))
    try:
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:
        raise ValueError(t("ingest.bad_pdf", detail=str(e)[:80]))
    text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(p.strip() for p in pages if p.strip())).strip()
    if not text:
        raise ValueError(t("ingest.pdf_no_text"))
    return text


_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "xmlnstbl",
             "listtable", "listoverridetable", "rsidtbl", "generator", "themedata", "colorschememapping",
             "latentstyles", "datastore", "mmathPr", "operator", "author", "company", "title"}


def rtf_to_text(raw: str) -> str:
    """Minimal RTF → text (paragraphs, tabs, \\'hh and \\uN escapes; skips font/colour tables)."""
    out: list[str] = []
    stack: list[bool] = []  # skipping-flag per group
    skip = False
    i, n = 0, len(raw)
    uc_skip = 0
    while i < n:
        c = raw[i]
        if c == "{":
            stack.append(skip)
            # destination groups {\*\...} or known tables are skipped entirely
            m = re.match(r"\{\\\*?\\?([a-zA-Z]+)", raw[i:i + 40])
            if m and (m.group(1) in _RTF_SKIP or raw[i:i + 3] == "{\\*"):
                skip = True
            i += 1
        elif c == "}":
            skip = stack.pop() if stack else False
            i += 1
        elif c == "\\":
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", raw[i:])
            if m:
                word, num = m.group(1), m.group(2)
                i += m.end()
                if skip:
                    continue
                if word in ("par", "line", "sect", "page"):
                    out.append("\n")
                elif word == "tab":
                    out.append("\t")
                elif word == "u" and num is not None:
                    code = int(num)
                    out.append(chr(code + 65536 if code < 0 else code))
                    uc_skip = 1
                elif word in ("emdash",):
                    out.append("—")
                elif word in ("endash",):
                    out.append("–")
                elif word in ("lquote", "rquote"):
                    out.append("'")
                elif word in ("ldblquote", "rdblquote"):
                    out.append('"')
                elif word == "bullet":
                    out.append("•")
                continue
            m = re.match(r"\\'([0-9a-fA-F]{2})", raw[i:])
            if m:
                i += 4
                if not skip:
                    if uc_skip:
                        uc_skip -= 1
                    else:
                        try:
                            out.append(bytes.fromhex(m.group(1)).decode("cp1252", errors="replace"))
                        except ValueError:
                            pass
                continue
            if i + 1 < n:
                if not skip and raw[i + 1] in "\\{}":
                    out.append(raw[i + 1])
                elif not skip and raw[i + 1] == "~":
                    out.append(" ")
                i += 2
            else:
                i += 1
        else:
            if not skip and c not in "\r\n":
                if uc_skip:
                    uc_skip -= 1
                else:
                    out.append(c)
            i += 1
    text = re.sub(r"[ \t]+\n", "\n", "".join(out))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(raw: str) -> str:
    from html.parser import HTMLParser

    class P(HTMLParser):
        BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
                 "article", "blockquote", "pre", "table", "ul", "ol", "dd", "dt", "hr"}

        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript", "head"):
                self.skip += 1
            elif tag in self.BLOCK:
                self.parts.append("\n")
            elif tag == "td":
                self.parts.append(" ")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript", "head"):
                self.skip = max(0, self.skip - 1)
            elif tag in self.BLOCK:
                self.parts.append("\n")

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    p = P()
    p.feed(raw)
    text = "".join(p.parts)
    text = "\n".join(re.sub(r"[ \t\xa0]+", " ", l).strip() for l in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def read_eml_text(path: Path) -> str:
    import email
    from email import policy
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    head = [f"{k}: {msg[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if msg.get(k)]
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        content = part.get_content()
        body = html_to_text(content) if part.get_content_type() == "text/html" else content
    text = "\n".join(head) + ("\n\n" + body.strip() if body.strip() else "")
    if not text.strip():
        raise ValueError(t("ingest.empty_file"))
    return text.strip()


# ---------- normalisation ----------

def build_imported(raw: str, ext: str) -> dict:
    """Raw material + extension → {segments, text}. Plain text is sniffed for subtitle /
    JSON content so a mis-named export still gets its timestamps."""
    ext = ext.lower()
    if ext in (".srt", ".vtt"):
        segments = _parse_subtitles(raw)
        if not segments:
            raise ValueError(t("ingest.no_subtitles", ext=ext))
        return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    if ext == ".sbv":
        segments = _parse_sbv(raw)
        if not segments:
            raise ValueError(t("ingest.no_subtitles", ext=ext))
        return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    if ext == ".lrc":
        segments = _parse_lrc(raw)
        if not segments:
            raise ValueError(t("ingest.no_subtitles", ext=ext))
        return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    if ext in (".ass", ".ssa"):
        segments = _parse_ass(raw)
        if not segments:
            raise ValueError(t("ingest.no_subtitles", ext=ext))
        return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    if ext == ".json":
        return _parse_json(raw)
    if ext in (".csv", ".tsv"):
        return _parse_table(raw, "\t" if ext == ".tsv" else ",")
    if ext == ".rtf":
        raw = rtf_to_text(raw)
    elif ext in (".html", ".htm"):
        raw = html_to_text(raw)
    text = raw.strip()
    if not text:
        raise ValueError(t("ingest.empty_file"))
    stripped = text.lstrip()
    if ext in (".txt", ".md", ".markdown", "") and stripped[:1] in "[{":
        try:
            return _parse_json(text)
        except ValueError:
            pass
    if ext in (".txt", ".md", ".markdown", "") and looks_like_subtitles(text):
        segments = _parse_subtitles(text)
        if segments:
            return {"segments": segments, "text": "\n".join(s["text"] for s in segments)}
    return {"segments": [], "text": text}


def detect_format(content: str) -> str:
    """Best-effort format label for pasted text (for the UI / meta only)."""
    s = content.strip()
    if s[:1] in "[{":
        try:
            _parse_json(s)
            return "json"
        except ValueError:
            pass
    if looks_like_subtitles(s):
        return "vtt" if s.upper().startswith("WEBVTT") else "srt"
    return "text"


def _render_line(s: dict) -> str:
    text = s["text"]
    sp = s.get("speaker")
    if sp and not text.lower().startswith(str(sp).lower()):
        text = f"{sp}: {text}"
    return f"[{_fmt_ts(s['start'])}] {text}" if s.get("start") is not None else text


def _write_meeting(mtg: Meeting, data: dict, fmt: str, source_label: str) -> None:
    segments = data["segments"]
    duration = ""
    if segments and any(s.get("start") is not None for s in segments):
        body = [_render_line(s) for s in segments]
        ends = [s["end"] for s in segments if s.get("end") is not None]
        if ends:
            duration = _fmt_ts(max(ends))
    elif segments:
        body = [_render_line(s) for s in segments]
    else:
        body = [data["text"]]

    # the material's own date (old chat logs) beats the import time: the AI must see the right timeline
    date_line = mtg.meta.get("date") or mtg.meta.get("created", "")
    header = [f"# {mtg.title}", "", f"- {t('transcript.date')}: {date_line}"]
    if duration:
        header.append(f"- {t('transcript.duration')}: {duration}")
    header += [f"- {t('transcript.source')}: {source_label} ({fmt})",
               participants_line(mtg.meta.get("participants", "")), "",
               f"## {sections()['transcript']}", ""]
    mtg.transcript_md.write_text("\n".join(header + body).rstrip() + "\n", encoding="utf-8")
    mtg.transcript_json.write_text(json.dumps({
        "text": data["text"], "segments": segments, "duration": duration,
        "model": "imported", "language": "imported", "source_format": fmt,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    mtg.save_meta(status="done", duration=duration, whisper_model="imported",
                  language="imported", source_format=fmt,
                  imported_at=dt.datetime.now().isoformat(timespec="seconds"), error=None)


def read_text_any(path: Path) -> str:
    """Decode UTF-8 → UTF-16 (BOM) → GB18030 → Big5. WeChat / QQ exports are often GBK;
    swallowing errors would import garbage that then lands in long-term memory."""
    data = path.read_bytes()
    encs = ["utf-8-sig"]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encs.append("utf-16")
    encs += ["gb18030", "big5"]
    for enc in encs:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


def read_material(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return read_docx_text(path)
    if ext == ".odt":
        return read_odt_text(path)
    if ext == ".pdf":
        return read_pdf_text(path)
    if ext == ".eml":
        return read_eml_text(path)
    return read_text_any(path)


def import_transcript_file(path: Path, title: str | None = None,
                           source: str = "text", audio_path: Path | None = None) -> Meeting:
    """Create a meeting from an existing text file (no transcription). The file is copied into
    the meeting folder; ``audio_path`` archives a matching recording (Whisper export folders)."""
    raw = read_material(path)
    data = build_imported(raw, path.suffix)
    mtg = create_meeting(title or path.stem, source=source, audio_path=audio_path)
    try:
        shutil.copy2(path, mtg.path / f"source{path.suffix.lower()}")
    except OSError:
        pass  # archiving failure does not block the import
    _write_meeting(mtg, data, path.suffix.lstrip(".").lower(), t(f"source.{source}"))
    return mtg


def import_text(content: str, title: str, date: str = "",
                source: str = "paste") -> Meeting:
    """Create a meeting from pasted text (chat log, e-mail, someone's minutes, a raw
    transcript …). SRT / VTT / JSON content is detected and keeps its timestamps."""
    text = content.strip()
    if not text:
        raise ValueError(t("ingest.empty_content"))
    fmt = detect_format(text)
    data = build_imported(text, ".txt" if fmt == "text" else f".{fmt}")
    mtg = create_meeting(title.strip() or t("source.paste"), source=source)
    if date:
        mtg.save_meta(date=date)
    _write_meeting(mtg, data, fmt if fmt != "text" else "paste", t(f"source.{source}"))
    return mtg
