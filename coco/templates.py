"""Registry of analysis templates and cross-meeting insight modes.

IDs are language-neutral (``minutes``, ``actions`` …) and used in API calls and
file names; display names / descriptions come from the locale files. The
original Chinese names are still accepted everywhere (and old report files
named after them keep their labels), so libraries created before the
multilingual version continue to work.
"""
from __future__ import annotations

import re

from .i18n import LOCALES_DIR, _load, get_lang, normalize, t

TEMPLATE_IDS = ["minutes", "actions", "mood", "tension", "bias", "topics",
                "client", "hiring", "followup"]
LEGACY_TEMPLATES = {
    "纪要": "minutes", "行动项": "actions", "情绪曲线": "mood", "张力与分歧": "tension",
    "认知偏误": "bias", "话题延伸": "topics", "客户跟进": "client", "招聘评估": "hiring",
    "跟进草稿": "followup",
}
TRACK_MODE_IDS = ["track", "signals", "synthesis"]
LEGACY_TRACK_MODES = {"追踪": "track", "深层信号": "signals", "调研综合": "synthesis"}

DEFAULT_TEMPLATE = "minutes"
DEFAULT_TRACK_MODE = "track"


def _labels(group: str, tid: str) -> set[str]:
    """Every localized name of a template/mode across all shipped locales."""
    names = set()
    for p in LOCALES_DIR.glob("*.json"):
        entry = (_load(p.stem).get(group) or {}).get(tid) or {}
        if entry.get("name"):
            names.add(entry["name"].strip().lower())
    return names


def resolve_template(name: str) -> "str | None":
    """Accept an id, the legacy Chinese name or any localized label → id."""
    if not name:
        return None
    n = name.strip()
    if n in TEMPLATE_IDS:
        return n
    if n in LEGACY_TEMPLATES:
        return LEGACY_TEMPLATES[n]
    low = n.lower()
    for tid in TEMPLATE_IDS:
        if low in _labels("templates", tid):
            return tid
    return None


def resolve_track_mode(name: str) -> "str | None":
    if not name:
        return None
    n = name.strip()
    if n in TRACK_MODE_IDS:
        return n
    if n in LEGACY_TRACK_MODES:
        return LEGACY_TRACK_MODES[n]
    low = n.lower()
    for tid in TRACK_MODE_IDS:
        if low in _labels("track_modes", tid):
            return tid
    return None


def template_label(tid: str, lang: "str | None" = None) -> str:
    return t(f"templates.{tid}.name", lang) if tid in TEMPLATE_IDS else tid


def template_desc(tid: str, lang: "str | None" = None) -> str:
    return t(f"templates.{tid}.desc", lang) if tid in TEMPLATE_IDS else ""


def track_label(tid: str, lang: "str | None" = None) -> str:
    return t(f"track_modes.{tid}.name", lang) if tid in TRACK_MODE_IDS else tid


def track_desc(tid: str, lang: "str | None" = None) -> str:
    return t(f"track_modes.{tid}.desc", lang) if tid in TRACK_MODE_IDS else ""


def list_templates(lang: "str | None" = None) -> list[dict]:
    return [{"id": i, "name": template_label(i, lang), "desc": template_desc(i, lang)}
            for i in TEMPLATE_IDS]


def list_track_modes(lang: "str | None" = None) -> list[dict]:
    return [{"id": i, "name": track_label(i, lang), "desc": track_desc(i, lang)}
            for i in TRACK_MODE_IDS]


_STAMP = re.compile(r"-(\d{2})(\d{2})(?:-\d+)?$")


def report_label(stem: str, lang: "str | None" = None) -> str:
    """'minutes-1355' / '纪要-1355' → '纪要 13:55' in the requested language;
    unknown prefixes keep their text, only the time suffix is made readable."""
    m = _STAMP.search(stem)
    base = stem[:m.start()] if m else stem
    tid = resolve_template(base)
    label = template_label(tid, lang) if tid else base
    return f"{label} {m.group(1)}:{m.group(2)}" if m else label


def all_template_names() -> list[str]:
    """ids + legacy names, for CLI argument validation / help."""
    return TEMPLATE_IDS + list(LEGACY_TEMPLATES)


def all_track_mode_names() -> list[str]:
    return TRACK_MODE_IDS + list(LEGACY_TRACK_MODES)


__all__ = ["TEMPLATE_IDS", "TRACK_MODE_IDS", "LEGACY_TEMPLATES", "LEGACY_TRACK_MODES",
           "resolve_template", "resolve_track_mode", "template_label", "template_desc",
           "track_label", "track_desc", "list_templates", "list_track_modes",
           "report_label", "all_template_names", "all_track_mode_names", "get_lang",
           "normalize"]
