"""会议库：library/ 下每个会议一个文件夹，含音频、转写、报告。"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .config import LIBRARY_DIR, TRASH_DIR, ensure_dirs

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aiff", ".aif", ".flac", ".ogg",
              ".opus", ".webm", ".mp4", ".mov", ".mkv", ".amr", ".wma"}


def _slug(title: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", title.strip())
    s = re.sub(r"\.{2,}", "-", s)  # ".." 会被下载/删除接口的路径穿越防护拒绝
    # Windows 会静默去掉文件夹名末尾的点和空格，导致创建的目录名与记录的 id 对不上
    return s[:60].strip("-. ") or "未命名"


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
    def date(self) -> str:
        """有效日期（YYYY-MM-DD）：手动校准的 meta.date 优先，否则取创建日，
        再不行从文件夹名前缀推断。用于显示、排序、按日筛选与每日简报分组。"""
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
            "date": self.date,
            "duration": m.get("duration", ""),
            "source": m.get("source", ""),
            "language": m.get("language", ""),  # 转写时识别/使用的语种
            "status": m.get("status", "new"),  # new|transcribing|done|error
            "has_transcript": self.transcript_md.exists(),
            "has_raw": (self.path / "transcript.raw.md").exists(),  # 人名校正前原稿，供前端「恢复」入口
            "names_fixed_at": m.get("names_fixed_at", ""),
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
    for p in LIBRARY_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").exists():
            out.append(Meeting(p))
    # 按有效日期倒序（手动校准录音日期后顺序随之调整），同日再按创建时间、id
    out.sort(key=lambda m: (m.date, m.meta.get("created", ""), m.id), reverse=True)
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
    return [m for m in list_meetings() if m.date == date]


def delete_meeting(mtg: Meeting) -> Path:
    """软删除：整个会议文件夹移入 library/_trash，可手动恢复。"""
    if mtg.meta.get("status") == "transcribing":
        raise RuntimeError("该会议正在转写中，等转写结束后再删除")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = TRASH_DIR / f"{mtg.id}~{stamp}"
    shutil.move(str(mtg.path), dest)
    return dest


def search_library(query: str, per_meeting: int = 4, limit: int = 50) -> list[dict]:
    """在所有转写和报告里做不区分大小写的全文搜索。"""
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for m in list_meetings():
        sources = []
        if m.transcript_md.exists():
            sources.append(("转写", m.transcript_md))
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
            results.append({"id": m.id, "title": m.title,
                            "created": m.meta.get("created", ""),
                            "matches": matches})
        if len(results) >= limit:
            break
    return results
