"""Prompt sets per output language.

Chinese output uses the Chinese prompts (tuned for 中文 meetings); every other
language uses the English prompts plus an explicit "write in <language>"
directive, so any language the model knows works (French, Japanese, …).
Section headings inside the prompts are filled from the locale files so the
generated Markdown matches what coco's parsers expect.
"""
from __future__ import annotations

from . import en, zh


def get(lang: str):
    """Return the prompt module for an output language code."""
    return zh if (lang or "").lower().startswith("zh") else en


def language_directive(lang: str) -> str:
    """One line appended to every prompt: which language to write in."""
    from ..i18n import lang_name
    mod = get(lang)
    if lang == "source":
        return mod.SOURCE_LANG_LINE
    return mod.OUTPUT_LANG_LINE.format(lang=lang_name(lang))
