"""Meeting library: one folder per meeting under library/ (audio, transcript, reports)."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .config import LIBRARY_DIR, TRASH_DIR, ensure_dirs
from .i18n import available, t

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg",
              ".opus", ".webm", ".mp4", ".mov", ".mkv", ".amr", ".wma", ".aac", ".wmv", ".avi"}


def _slug(title: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", title.strip())
    s = re.sub(r"\.{2,}", "-", s)  # ".." would be rejected by the path-traversal guards
    # Windows silently strips trailing dots/spaces from folder names → id mismatch
    return s[:60].strip("-. ") or "untitled"


class Meeting:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.name

    @property
    def meta_file(self) -> Path:
        return self.path / "meta.json"

    @property
    def meta(self) -> dict:
        if self.meta_file.exists():
            return json.loads(self.meta_file.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, **kwargs) -> None:
        m = self.meta
        m.update(kwargs)
        self.meta_file.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def title(self) -> str:
        return self.meta.get("title", self.id)

    @property
    def date(self) -> str:
        """Effective date (YYYY-MM-DD): manual meta.date, else creation day, else folder prefix."""
        d = self.meta.get("date")
        if d:
            return d
        created = self.meta.get("created", "")
        if created:
            return created[:10]
        m = re.match(r"\d{4}-\d{2}-\d{2}", self.id)
        return m.group(0) if m else ""

    @property
    def audio_file(self) -> Path | None:
        for f in sorted(self.path.iterdir()):
            if f.suffix.lower() in AUDIO_EXTS:
                return f
        return None

    @property
    def transcript_md(self) -> Path:
        return self.path / "transcript.md"

    @property
    def transcript_json(self) -> Path:
        return self.path / "transcript.json"

    @property
    def reports_dir(self) -> Path:
        return self.path / "reports"

    def transcript_text(self) -> str:
        if self.transcript_md.exists():
            return self.transcript_md.read_text(encoding="utf-8")
        return ""

    def segments(self) -> list[dict]:
        """Timed segments from transcript.json (empty for plain-text imports)."""
        if not self.transcript_json.exists():
            return []
        try:
            data = json.loads(self.transcript_json.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return [s for s in data.get("segments", []) if isinstance(s, dict) and s.get("text")]

    def reports(self) -> list[Path]:
        if not self.reports_dir.exists():
            return []
        return sorted(self.reports_dir.glob("*.md"))

    def summary(self) -> dict:
        m = self.meta
        src = m.get("source", "")
        return {
            "id": self.id,
            "title": self.title,
            "created": m.get("created", ""),
            "date": self.date,
            "duration": m.get("duration", ""),
            "source": src,
            "source_label": source_label(src),
            "language": m.get("language", ""),  # language detected / used for transcription
            "status": m.get("status", "new"),  # new|transcribing|done|error
            "has_transcript": self.transcript_md.exists(),
            "has_raw": (self.path / "transcript.raw.md").exists(),  # pre-correction original
            "has_segments": self.transcript_json.exists(),
            "names_fixed_at": m.get("names_fixed_at", ""),
            "participants": m.get("participants", ""),
            "has_preclean": (self.path / "transcript.preclean.md").exists(),
            "reports": [p.stem for p in self.reports()],
        }


def _participants_re() -> "re.Pattern":
    labels = {t("transcript.participants", l["code"]) for l in available()}
    alts = "|".join(re.escape(x) for x in sorted(labels, key=len, reverse=True))
    return re.compile(rf"^- (?:{alts})\s*[:：]\s*(.*)$", re.M)


def _empty_markers() -> set[str]:
    return {t("transcript.participants_empty", l["code"]).strip() for l in available()}


def participants_line(text: str) -> str:
    """The transcript header line for the given participants ('' → localized placeholder)."""
    return f"- {t('transcript.participants')}: {text.strip() or t('transcript.participants_empty')}"


def participants_from_transcript(md: str) -> "str | None":
    """Value of the participants header line, '' when it holds the placeholder, None when absent."""
    m = _participants_re().search(md)
    if not m:
        return None
    v = " ".join(m.group(1).split())
    return "" if v in _empty_markers() else v


def set_participants(mtg: "Meeting", text: str) -> str:
    """Store participants in meta and write/replace the header line in transcript.md so every
    analysis (which reads the transcript) sees who was in the room. Returns the stored value."""
    text = " ".join((text or "").split())
    mtg.save_meta(participants=text)
    md = mtg.transcript_text()
    if md:
        line = participants_line(text)
        rx = _participants_re()
        if rx.search(md):
            md = rx.sub(lambda _m: line, md, count=1)
        else:
            lines = md.splitlines()
            first_h2 = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
            last_meta = max((i for i in range(first_h2) if lines[i].startswith("- ")), default=None)
            at = last_meta + 1 if last_meta is not None else (1 if lines and lines[0].startswith("# ") else 0)
            if last_meta is None and at < len(lines) and lines[at].strip() == "":
                lines.insert(at, "")
            lines.insert(at, line)
            md = "\n".join(lines).rstrip() + "\n"
        mtg.transcript_md.write_text(md, encoding="utf-8")
    return text


def clean_meeting(mtg: "Meeting", apply: bool = False) -> dict:
    """Remove hallucinated lines from an existing transcript (preview by default).

    Applies the same rules as fresh transcriptions to transcript.md (body lines only) and to the
    segments in transcript.json. The first apply keeps transcript.preclean.md as a backup.
    Returns {"removed": [lines], "count": n, "applied": bool}.
    """
    from .transcriber import clean_segments, clean_transcript_lines
    md = mtg.transcript_text()
    if not md:
        return {"removed": [], "count": 0, "applied": False}
    prompt = "本次对话可能涉及。以下是普通话的句子，请用简体中文输出。"
    lines = md.splitlines()
    first_h2 = next((i for i, l in enumerate(lines) if l.startswith("## ")), -1)
    head, body = (lines[: first_h2 + 1], lines[first_h2 + 1:]) if first_h2 >= 0 else ([], lines)
    kept, removed = clean_transcript_lines(body, prompt)
    if apply and removed:
        bak = mtg.path / "transcript.preclean.md"
        if not bak.exists():
            bak.write_text(md, encoding="utf-8")
        mtg.transcript_md.write_text("\n".join(head + kept).rstrip() + "\n", encoding="utf-8")
        if mtg.transcript_json.exists():
            try:
                data = json.loads(mtg.transcript_json.read_text(encoding="utf-8"))
                segs, _ = clean_segments(data.get("segments", []), prompt)
                data["segments"] = segs
                data["text"] = "\n".join(s["text"] for s in segs) if segs else data.get("text", "")
                mtg.transcript_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            except ValueError:
                pass
        mtg.save_meta(cleaned_at=dt.datetime.now().isoformat(timespec="seconds"),
                      hallucinations_removed=int(mtg.meta.get("hallucinations_removed", 0) or 0) + len(removed))
    return {"removed": removed, "count": len(removed), "applied": bool(apply and removed)}


def source_label(src: str) -> str:
    """Localized label for a source id ('upload', 'record', 'watch:folder' …);
    legacy Chinese labels stored by older versions are shown as they are."""
    if not src:
        return ""
    if src.startswith("watch:"):
        return t("source.watch", folder=src[6:])
    label = t(f"source.{src}")
    return src if label == f"source.{src}" else label


def create_meeting(title: str, audio_path: Path | None = None,
                   source: str = "import", move: bool = False) -> Meeting:
    ensure_dirs()
    date = dt.date.today().isoformat()
    base = f"{date}-{_slug(title)}"
    folder = LIBRARY_DIR / base
    n = 1
    while folder.exists():
        n += 1
        folder = LIBRARY_DIR / f"{base}-{n}"
    folder.mkdir(parents=True)
    mtg = Meeting(folder)
    mtg.save_meta(title=title, created=dt.datetime.now().isoformat(timespec="seconds"),
                  source=source, status="new")
    if audio_path is not None:
        dest = folder / ("audio" + audio_path.suffix.lower())
        if move:
            shutil.move(str(audio_path), dest)
        else:
            shutil.copy2(audio_path, dest)
    return mtg


def list_meetings() -> list[Meeting]:
    ensure_dirs()
    out = []
    for p in LIBRARY_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").exists():
            out.append(Meeting(p))
    # newest effective date first, then creation time, then id
    out.sort(key=lambda m: (m.date, m.meta.get("created", ""), m.id), reverse=True)
    return out


def find_meeting(key: str) -> Meeting:
    """Exact id, or unique substring of id / title; ambiguous → error."""
    meetings = list_meetings()
    for m in meetings:
        if m.id == key:
            return m
    hits = [m for m in meetings if key in m.id or key in m.title]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LookupError(t("library.not_found", key=key))
    names = ", ".join(m.id for m in hits[:8])
    raise LookupError(t("library.ambiguous", key=key, names=names))


def meetings_on(date: str) -> list[Meeting]:
    return [m for m in list_meetings() if m.date == date]


def delete_meeting(mtg: Meeting) -> Path:
    """Soft delete: move the whole folder into library/_trash."""
    if mtg.meta.get("status") == "transcribing":
        raise RuntimeError(t("library.deleting_while_transcribing"))
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = TRASH_DIR / f"{mtg.id}~{stamp}"
    shutil.move(str(mtg.path), dest)
    return dest


def search_library(query: str, per_meeting: int = 4, limit: int = 50) -> list[dict]:
    """Case-insensitive full-text search over transcripts and reports."""
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for m in list_meetings():
        sources = []
        if m.transcript_md.exists():
            sources.append((t("files.transcript"), m.transcript_md))
        sources += [(p.stem, p) for p in m.reports()]
        matches = []
        for label, path in sources:
            for line in path.read_text(encoding="utf-8").splitlines():
                if q in line.lower():
                    matches.append({"where": label, "line": line.strip()[:120]})
                    if len(matches) >= per_meeting:
                        break
            if len(matches) >= per_meeting:
                break
        if matches:
            results.append({"id": m.id, "title": m.title, "created": m.meta.get("created", ""),
                            "matches": matches})
        if len(results) >= limit:
            break
    return results
