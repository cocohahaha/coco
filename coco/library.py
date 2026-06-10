"""会议库：library/ 下每个会议一个文件夹，含音频、转写、报告。"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .config import LIBRARY_DIR, ensure_dirs

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg",
              ".opus", ".webm", ".mp4", ".mov", ".mkv", ".amr", ".wma"}


def _slug(title: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", title.strip())
    return s.strip("-")[:60] or "未命名"


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
        self.meta_file.write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def title(self) -> str:
        return self.meta.get("title", self.id)

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

    def reports(self) -> list[Path]:
        if not self.reports_dir.exists():
            return []
        return sorted(self.reports_dir.glob("*.md"))

    def summary(self) -> dict:
        m = self.meta
        return {
            "id": self.id,
            "title": self.title,
            "created": m.get("created", ""),
            "duration": m.get("duration", ""),
            "source": m.get("source", ""),
            "status": m.get("status", "new"),  # new|transcribing|done|error
            "has_transcript": self.transcript_md.exists(),
            "reports": [p.stem for p in self.reports()],
        }


def create_meeting(title: str, audio_path: Path | None = None,
                   source: str = "导入", move: bool = False) -> Meeting:
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
    mtg.save_meta(
        title=title,
        created=dt.datetime.now().isoformat(timespec="seconds"),
        source=source,
        status="new",
    )
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
    for p in sorted(LIBRARY_DIR.iterdir(), reverse=True):
        if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").exists():
            out.append(Meeting(p))
    return out


def find_meeting(key: str) -> Meeting:
    """按 id 精确匹配，或按标题/id 子串模糊匹配；歧义时报错。"""
    meetings = list_meetings()
    for m in meetings:
        if m.id == key:
            return m
    hits = [m for m in meetings if key in m.id or key in m.title]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LookupError(f"找不到会议：{key}（coco list 查看全部）")
    names = "、".join(m.id for m in hits[:8])
    raise LookupError(f"“{key}”匹配到多个会议：{names}，请用更精确的名字")


def meetings_on(date: str) -> list[Meeting]:
    return [m for m in list_meetings() if m.meta.get("created", "").startswith(date)]
