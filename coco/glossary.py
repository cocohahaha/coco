"""Glossary: standard spelling of names and proper nouns (memory/glossary.md).

Format (parsed leniently – hand-written entries need not be perfect)::

    ## Names            (## 人名 / ## Noms …)
    - Lin Wei (misheard: Lin Way, Lynn Wei) | product lead
    ## Terms            (## 专有名词 / ## Termes …)
    - SmartCabin (misheard: Smart Cabinet) | in-car cockpit project

Headings and the "misheard" label are accepted in every shipped locale, so a
glossary written in one language keeps working after switching the interface.
"Correct spellings" are injected into the whisper prompt; the whole glossary is
injected into name/term correction and every AI analysis.
"""
from __future__ import annotations

import re

from .config import GLOSSARY_FILE, ensure_dirs
from .i18n import LOCALES_DIR, _load, canonical_section

_EXTRA_MISHEARD = ("误写", "misheard", "mis-heard", "variants", "variant", "aka",
                   "erreurs", "variantes", "erreur")


def _misheard_labels() -> list[str]:
    labels = set(_EXTRA_MISHEARD)
    for p in LOCALES_DIR.glob("*.json"):
        v = (_load(p.stem).get("sections") or {}).get("misheard")
        if v:
            labels.add(v)
    return sorted(labels, key=len, reverse=True)


def _wrong_re() -> re.Pattern:
    alts = "|".join(re.escape(l) for l in _misheard_labels())
    return re.compile(rf"[（(]\s*(?:{alts})\s*[:：]\s*([^）)]*)[）)]", re.I)


def read_glossary() -> str:
    ensure_dirs()
    # an external editor may save in another encoding; never let the glossary break transcription
    return GLOSSARY_FILE.read_text(encoding="utf-8", errors="replace")


def _section_kind(head: str) -> str:
    canon = canonical_section(head)
    if canon == "names":
        return "names"
    if canon == "glossary_terms":
        return "terms"
    h = head.lower()
    if "人名" in h or "name" in h or "nom" in h or "person" in h or "people" in h:
        return "names"
    return "terms"


def parse_glossary(text: str | None = None) -> dict:
    """Parse the glossary → {"names": [{term, wrong, note}], "terms": [...]}.

    Lenient: `- Lin Wei (product lead)` (plain parentheses become the note), `- **Name**`,
    missing bar / missing misspellings all parse; rules and long explanatory sentences
    are not entries. Unknown section headings count as terms.
    """
    text = read_glossary() if text is None else text
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # format examples in comments are not entries
    wrong_re = _wrong_re()
    out: dict[str, list[dict]] = {"names": [], "terms": []}
    section = "terms"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            head = line.lstrip("#").strip()
            if head and canonical_section(head) not in ("glossary_title", "longterm_title",
                                                         "memory_title"):
                section = _section_kind(head)
            continue
        if not (line.startswith("- ") or line.startswith("* ")):
            continue
        body = line[2:].strip().replace("**", "")
        m = wrong_re.search(body)
        wrong = ([w.strip() for w in re.split(r"[、,，/；;]", m.group(1)) if w.strip()]
                 if m else [])
        body = wrong_re.sub("", body)
        parts = re.split(r"[｜|]", body, maxsplit=1)
        term = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else ""
        pm = re.match(r"(.+?)[（(]([^）)]*)[）)]\s*$", term)
        if pm:  # plain parenthesis = note: - Lin Wei (product lead)
            term = pm.group(1).strip()
            note = f"{pm.group(2).strip()} {note}".strip()
        term = term.strip(" -—·*＝=~～")
        if not term or len(term) > 40:  # blank / rule / explanatory sentence
            continue
        out[section].append({"term": term, "wrong": wrong, "note": note})
    return out


def initial_prompt_terms(max_chars: int = 200) -> str:
    """Correct spellings for the whisper prompt (names first), capped so the prompt window
    is not flooded."""
    parsed = parse_glossary()
    terms, total = [], 0
    for e in parsed["names"] + parsed["terms"]:
        t = e["term"]
        if total + len(t) + 1 > max_chars:
            break
        terms.append(t)
        total += len(t) + 1
    return "、".join(terms)


def glossary_stats(text: str | None = None) -> dict:
    p = parse_glossary(text)
    return {"names": len(p["names"]), "terms": len(p["terms"])}
