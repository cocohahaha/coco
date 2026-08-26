"""Internationalisation: UI / back-end strings and the language of AI output.

Locales live in ``coco/locales/<code>.json``. Each file holds:

- ``ui``        strings for the web front-end (served through /api/i18n)
- ``msg``       back-end messages (errors, CLI output, file labels)
- ``sections``  the fixed Markdown headings coco relies on (long-term memory,
                glossary, transcript) – every parser accepts the headings of
                *all* locales, so a library created in one language keeps
                working after switching the interface to another
- ``templates`` / ``track_modes``  localized names of analysis templates

The current request language is kept in a context variable (set by the web
middleware from the ``X-Coco-Lang`` header, or by the CLI from the config /
system locale).
"""
from __future__ import annotations

import contextvars
import json
import locale as _locale
import os
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LANG = "en"
FALLBACK_CHAIN = ("en", "zh-CN")

_CACHE: dict[str, dict] = {}
_current: contextvars.ContextVar["str | None"] = contextvars.ContextVar("coco_lang", default=None)

# Aliases → locale code. Anything unknown falls back to DEFAULT_LANG.
_ALIASES = {
    "zh": "zh-CN", "zh-cn": "zh-CN", "zh_cn": "zh-CN", "zh-hans": "zh-CN", "zh-sg": "zh-CN",
    "zh-tw": "zh-CN", "zh-hk": "zh-CN", "zh-hant": "zh-CN", "zh_tw": "zh-CN", "zh_hk": "zh-CN",
    "en": "en", "en-us": "en", "en-gb": "en", "en_us": "en", "en_gb": "en",
    "fr": "fr", "fr-fr": "fr", "fr-ch": "fr", "fr-ca": "fr", "fr_fr": "fr", "fr_ch": "fr",
}

# Human-readable language names for the "write the output in …" directive.
# Native name first so the model sees the target script itself.
LANG_NAMES = {
    "zh-CN": "简体中文 (Simplified Chinese)", "zh-TW": "繁體中文 (Traditional Chinese)",
    "en": "English", "fr": "français (French)", "de": "Deutsch (German)",
    "es": "español (Spanish)", "it": "italiano (Italian)", "pt": "português (Portuguese)",
    "ja": "日本語 (Japanese)", "ko": "한국어 (Korean)", "ru": "русский (Russian)",
    "ar": "العربية (Arabic)", "hi": "हिन्दी (Hindi)", "th": "ไทย (Thai)",
    "vi": "Tiếng Việt (Vietnamese)", "id": "Bahasa Indonesia (Indonesian)",
    "nl": "Nederlands (Dutch)", "tr": "Türkçe (Turkish)", "pl": "polski (Polish)",
    "sv": "svenska (Swedish)",
}


def available() -> list[dict]:
    """Locales shipped with coco: [{code, name}] in a stable order."""
    out = []
    for p in sorted(LOCALES_DIR.glob("*.json")):
        meta = _load(p.stem).get("_meta", {})
        out.append({"code": p.stem, "name": meta.get("name", p.stem)})
    order = {"en": 0, "zh-CN": 1, "fr": 2}
    out.sort(key=lambda x: (order.get(x["code"], 9), x["code"]))
    return out


def _load(code: str) -> dict:
    if code not in _CACHE:
        path = LOCALES_DIR / f"{code}.json"
        _CACHE[code] = (json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})
    return _CACHE[code]


def normalize(code: "str | None") -> "str | None":
    """'zh_CN.UTF-8' / 'zh-Hans' / 'en-US' → locale code we ship, else None."""
    if not code:
        return None
    c = str(code).strip().split(".")[0].split("@")[0].replace("_", "-").lower()
    if c in _ALIASES:
        return _ALIASES[c]
    base = c.split("-")[0]
    if base in _ALIASES:
        return _ALIASES[base]
    if (LOCALES_DIR / f"{code}.json").exists():
        return code
    return None


def system_lang() -> str:
    for var in ("COCO_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v and v not in ("C", "POSIX"):
            n = normalize(v)
            if n:
                return n
    try:
        n = normalize(_locale.getlocale()[0])
        if n:
            return n
    except Exception:
        pass
    return DEFAULT_LANG


def configured_lang() -> "str | None":
    from .config import load_config  # lazy: config imports i18n for placeholders
    v = str(load_config().get("ui_language") or "auto")
    return None if v in ("auto", "") else normalize(v)


def get_lang() -> str:
    """Language for the current request / process."""
    return _current.get() or configured_lang() or system_lang()


def set_lang(code: "str | None"):
    """Set the language for the current context (web request or CLI run)."""
    return _current.set(normalize(code) if code else None)


def reset_lang(token) -> None:
    _current.reset(token)


def _lookup(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def t(_key: str, _lang: "str | None" = None, **kw) -> str:
    """Translate a ``msg.*`` key (or any dotted path) with {placeholder} formatting.

    Positional-only-ish parameter names so placeholders may be called ``key`` / ``lang``.
    """
    key = _key
    lang = normalize(_lang) if _lang else get_lang()
    path = key if "." in key and key.split(".", 1)[0] in ("ui", "msg", "sections", "templates",
                                                            "track_modes") else f"msg.{key}"
    for code in (lang, *FALLBACK_CHAIN):
        val = _lookup(_load(code), path)
        if isinstance(val, str):
            if kw:
                try:
                    return val.format(**kw)
                except (KeyError, IndexError):
                    return val
            return val
    return key


def ui_strings(lang: str) -> dict:
    """Front-end dictionary with fallback merged in (missing keys come from English)."""
    lang = normalize(lang) or DEFAULT_LANG
    merged: dict = {}
    for code in reversed((lang, *FALLBACK_CHAIN)):
        _deep_merge(merged, _load(code).get("ui", {}))
    return merged


def _deep_merge(dst: dict, src: dict) -> None:
    """Merge src into dst, copying nested dicts so the cached locale objects are never mutated."""
    for k, v in src.items():
        if isinstance(v, dict):
            if not isinstance(dst.get(k), dict):
                dst[k] = {}
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def lang_name(code: str) -> str:
    n = normalize(code) or code
    return LANG_NAMES.get(n) or LANG_NAMES.get(code) or code


# ---------- AI output language ----------

def output_lang() -> str:
    """Resolve config ``output_language``: 'ui' → interface language; 'source' →
    follow the material; otherwise an ISO code (any language the model knows)."""
    from .config import load_config
    v = str(load_config().get("output_language") or "ui").strip()
    if v in ("ui", "auto", ""):
        return get_lang()
    if v == "source":
        return "source"
    return normalize(v) or v


def sections(lang: "str | None" = None) -> dict:
    """Fixed headings for the given language (falls back to English)."""
    code = lang or output_lang()
    if code == "source":
        code = get_lang()
    code = normalize(code) or DEFAULT_LANG
    base = dict(_load("en").get("sections", {}))
    base.update(_load(code).get("sections", {}))
    return base


def section_aliases() -> dict[str, set[str]]:
    """canonical key → every heading text used by any locale (lower-cased)."""
    out: dict[str, set[str]] = {}
    for p in LOCALES_DIR.glob("*.json"):
        for k, v in _load(p.stem).get("sections", {}).items():
            if isinstance(v, str):
                out.setdefault(k, set()).add(v.strip().lower())
    return out


def canonical_section(heading: str) -> "str | None":
    """'## People' / '## 人物' → 'people'."""
    h = heading.strip().lstrip("#").strip().lower()
    for key, names in section_aliases().items():
        if h in names:
            return key
    return None


def detect_headings_lang(text: str) -> "str | None":
    """Which locale's long-term-memory headings does this file use? None if unknown."""
    heads = {l.strip()[3:].strip().lower() for l in text.splitlines() if l.strip().startswith("## ")}
    if not heads:
        return None
    best, best_n = None, 0
    for p in LOCALES_DIR.glob("*.json"):
        sec = _load(p.stem).get("sections", {})
        n = sum(1 for k in ("people", "projects", "commitments", "terms")
                if str(sec.get(k, "")).lower() in heads)
        if n > best_n:
            best, best_n = p.stem, n
    return best


def memory_placeholder(kind: str, lang: "str | None" = None) -> str:
    """Initial content of memory.md / longterm.md / glossary.md in the given language."""
    code = lang or output_lang()
    if code == "source":
        code = get_lang()
    s = sections(code)
    if kind == "memory":
        return f"# {s['memory_title']}\n\n<!-- {t('placeholder.memory', code)} -->\n"
    if kind == "longterm":
        return f"# {s['longterm_title']}\n\n<!-- {t('placeholder.longterm', code)} -->\n"
    if kind == "glossary":
        return (f"# {s['glossary_title']}\n\n<!-- {t('placeholder.glossary', code)} -->\n\n"
                f"## {s['names']}\n\n## {s['glossary_terms']}\n")
    raise ValueError(kind)
