"""AI analysis layer.

Every prompt goes through :mod:`providers` (claude CLI by default, or any
Anthropic- / OpenAI-compatible endpoint). Prompts come from :mod:`prompts` in
the output language; all functions accept ``on_delta`` so the web UI can stream
text as it is generated.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
import threading

from . import prompts
from .config import (BRIEFS_DIR, GLOSSARY_FILE, LONGTERM_FILE, MEMORY_FILE,
                     PREP_DIR, TRACKING_DIR, WEEKLY_DIR, load_config)
from .i18n import (canonical_section, detect_headings_lang, get_lang, normalize,
                   output_lang, sections, t)
from .library import Meeting, list_meetings, meetings_on
from .providers import (ModelError, context_limit, profile_for_task, resolve_claude_bin,
                        run_model)
from .templates import (resolve_template, resolve_track_mode, template_label,
                        track_label)

AIError = ModelError  # one exception type for callers, whichever layer raised it

MAX_LONGTERM_INJECT = 30_000  # cap on long-term memory injected into analyses
MEMORY_LOCK = threading.Lock()  # memory / glossary read-modify-write is serialised
TRANSCRIPT_LOCK = threading.Lock()  # transcript.md writes (name fix / manual save / restore)

__all__ = ["AIError", "resolve_claude_bin"]


# ---------- language helpers ----------

def _lang_ctx() -> "tuple[object, str, dict]":
    """(prompt module, language directive line, section headings) for the current output language."""
    lang = output_lang()
    P = prompts.get(get_lang() if lang == "source" else lang)
    return P, prompts.language_directive(lang), sections(None if lang != "source" else get_lang())


def _fill(text: str, sec: dict, **extra) -> str:
    return text.format(**{**sec, **extra})


# ---------- name / term correction ----------

def _run_name_fix(text: str, what: str = "transcript") -> str:
    """Correct names/terms in a transcript or report, glossary + memory as reference.

    Only names change, so length and line count should stay ~constant; a large
    deviation means the model drifted – raise so the caller keeps the original.
    """
    if not text.strip():
        raise AIError(t("ai.fix_empty"))
    if len(text) > 80_000:
        raise AIError(t("ai.fix_too_long"))
    P, _, _ = _lang_ctx()
    out = run_model(f"{_memory_block()}{P.NAME_FIX_PROMPT}\n\n<{what}>\n{text}\n</{what}>",
                    task="namefix", timeout=1200)
    ratio = len(out) / max(len(text), 1)
    if not 0.8 <= ratio <= 1.2:
        raise AIError(t("ai.fix_length_drift"))
    if abs(out.count("\n") - text.count("\n")) > 2:
        raise AIError(t("ai.fix_lines_drift"))
    return out.rstrip() + "\n"


def fix_names(mtg: "Meeting") -> str:
    """Single meeting: correct the transcript; the original is kept as transcript.raw.md (first time only)."""
    text = mtg.transcript_text()
    if not text:
        raise AIError(t("ai.no_transcript", id=mtg.id))
    fixed = _run_name_fix(text, "transcript")
    with TRANSCRIPT_LOCK:
        if mtg.transcript_text() != text:  # edited / re-transcribed meanwhile: do not clobber
            raise AIError(t("ai.fix_changed_meanwhile"))
        raw = mtg.path / "transcript.raw.md"
        if not raw.exists():
            raw.write_text(text, encoding="utf-8")
        mtg.transcript_md.write_text(fixed, encoding="utf-8")
        mtg.save_meta(names_fixed_at=dt.datetime.now().isoformat(timespec="seconds"))
    return fixed


def fix_all_names(meetings=None, progress=lambda msg: None) -> dict:
    """Whole library: correct names in every transcript and report in place.

    Originals are backed up (transcript → transcript.raw.md; report → <name>.md.bak).
    A failing item is recorded and skipped. Returns {meetings, transcripts, reports, errors}.
    """
    targets = (meetings if meetings is not None
               else [m for m in list_meetings() if m.transcript_md.exists()])
    stats = {"meetings": 0, "transcripts": 0, "reports": 0, "errors": []}
    n = len(targets)
    for i, m in enumerate(targets, 1):
        progress(t("ai.fixing_progress", i=i, n=n, title=m.title))
        try:
            fix_names(m)
            stats["transcripts"] += 1
        except AIError as e:
            stats["errors"].append(f"{m.title} · {t('files.transcript')}: {e}")
        for rp in m.reports():
            try:
                rtext = rp.read_text(encoding="utf-8")
                rfixed = _run_name_fix(rtext, "report")
                bak = rp.with_name(rp.name + ".bak")
                if not bak.exists():
                    bak.write_text(rtext, encoding="utf-8")
                rp.write_text(rfixed, encoding="utf-8")
                stats["reports"] += 1
            except AIError as e:
                stats["errors"].append(f"{m.title} · {rp.stem}: {e}")
        stats["meetings"] += 1
    return stats


def restore_raw(mtg: "Meeting") -> str:
    """Undo name correction: transcript.raw.md → transcript.md, backup removed."""
    raw = mtg.path / "transcript.raw.md"
    if not raw.exists():
        raise AIError(t("ai.no_raw"))
    original = raw.read_text(encoding="utf-8")
    with TRANSCRIPT_LOCK:
        mtg.transcript_md.write_text(original.rstrip() + "\n", encoding="utf-8")
        raw.unlink()
        mtg.save_meta(names_fixed_at="")
    return original


# ---------- context assembly ----------

def _memory_block() -> str:
    P, _, _ = _lang_ctx()
    parts = []
    if GLOSSARY_FILE.exists():
        text = GLOSSARY_FILE.read_text(encoding="utf-8").strip()
        from .glossary import glossary_stats
        s = glossary_stats(text)
        if s["names"] or s["terms"]:  # skip the empty template
            parts.append(f"<glossary note=\"{P.CTX['glossary_note']}\">\n{text[:10_000]}\n</glossary>")
    if MEMORY_FILE.exists():
        text = re.sub(r"<!--.*?-->", "", MEMORY_FILE.read_text(encoding="utf-8"), flags=re.S).strip()
        if text and not re.fullmatch(r"#[^\n]*", text):  # title only = empty
            parts.append(f"<memory>\n{text}\n</memory>")
    if LONGTERM_FILE.exists():
        text = LONGTERM_FILE.read_text(encoding="utf-8").strip()
        if text and "## " in text:
            parts.append(f"<longterm note=\"{P.CTX['longterm_note']}\">\n"
                         f"{text[:MAX_LONGTERM_INJECT]}\n</longterm>")
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _context_block(meetings: list[Meeting], per_meeting: int | None = None,
                   limit: int | None = None) -> str:
    """Concatenate transcripts. ``per_meeting`` caps one meeting so an early long one
    cannot push later meetings out entirely; ``limit`` is the provider's context budget."""
    P, _, _ = _lang_ctx()
    limit = limit or context_limit()
    parts, total = [], 0
    for m in meetings:
        text = m.transcript_text()
        if not text:
            continue
        if per_meeting and len(text) > per_meeting:
            text = text[:per_meeting] + "\n" + P.CTX["truncated_one"]
        if total + len(text) > limit:
            text = text[: max(limit - total, 0)] + "\n" + P.CTX["truncated_all"]
        who = " ".join(str(m.meta.get("participants", "")).split())
        attr = f' participants="{who.replace(chr(34), chr(39))}"' if who else ""
        parts.append(f"<meeting id=\"{m.id}\"{attr}>\n{text}\n</meeting>")
        total += len(text)
        if total >= limit:
            break
    return "\n\n".join(parts)


def _dedupe_title(title: str, content: str) -> str:
    """Drop a leading H1 that repeats the topic of the title coco adds itself."""
    stripped = content.lstrip("\n")
    if stripped.startswith("# "):
        first, _, rest = stripped.partition("\n")
        topic = title.lstrip("# ").split("·")[0].strip()[:4]
        if topic and topic.lower() in first.lower():
            return rest.lstrip("\n")
    return content


def _unique_path(folder, name: str, ext: str = ".md"):
    path = folder / f"{name}{ext}"
    n = 2
    while path.exists():  # same-minute repeats must not silently overwrite
        path = folder / f"{name}-{n}{ext}"
        n += 1
    return path


# ---------- chat / reports ----------

def _participants_block(meetings: list[Meeting]) -> str:
    """Names + roles confirmed by the user: the authority for who said what."""
    P, _, _ = _lang_ctx()
    who = [f"{m.title}: {' '.join(str(m.meta.get('participants')).split())}"
           for m in meetings if str(m.meta.get("participants") or "").strip()]
    return P.PARTICIPANTS_LINE.format(people="; ".join(who)) + "\n\n" if who else ""


def detect_participants(mtg: Meeting) -> str:
    """Ask the model for 'Name (role), …' from the transcript; the user confirms before saving."""
    text = mtg.transcript_text()
    if not text:
        raise AIError(t("ai.no_transcript", id=mtg.id))
    P, directive, _ = _lang_ctx()
    prompt = (f"{_memory_block()}{P.PARTICIPANTS_PROMPT}\n{directive}\n\n"
              f"<speaker_material id=\"{mtg.id}\">\n{text[:20_000]}\n</speaker_material>")
    out = _strip_fence(run_model(prompt, task="participants", timeout=300)).strip()
    out = " ".join(l.strip(" -*") for l in out.splitlines() if l.strip())
    if not out or len(out) > 400:
        raise AIError(t("ai.participants_bad"))
    return out


def ask(question: str, meetings: list[Meeting], on_delta=None) -> str:
    P, directive, _ = _lang_ctx()
    ctx = _context_block(meetings, limit=context_limit(profile_for_task("ask")))
    if not ctx:
        raise AIError(t("ai.refs_no_transcript"))
    prompt = (f"{P.CHAT_SYSTEM}\n{directive}\n\n{_memory_block()}{_participants_block(meetings)}{ctx}\n\n"
              f"<question>\n{question}\n</question>")
    return run_model(prompt, task="ask", on_delta=on_delta)


def generate_report(mtg: Meeting, template: str, on_delta=None) -> "tuple[str, str]":
    """Generate a template report into reports/; returns (path, content)."""
    tid = resolve_template(template)
    if not tid:
        raise AIError(t("ai.unknown_template", name=template))
    text = mtg.transcript_text()
    if not text:
        raise AIError(t("ai.no_transcript", id=mtg.id))
    P, directive, _ = _lang_ctx()
    limit = context_limit(profile_for_task("report"))
    prompt = (
        f"{P.CHAT_SYSTEM}\n\n{_memory_block()}{_participants_block([mtg])}"
        f"<meeting id=\"{mtg.id}\">\n{text[:limit]}\n</meeting>\n\n"
        f"<task>\n{P.TEMPLATES[tid]}\n</task>\n\n{P.REPORT_TAIL}\n{directive}"
    )
    content = run_model(prompt, task="report", on_delta=on_delta)
    mtg.reports_dir.mkdir(exist_ok=True)
    path = _unique_path(mtg.reports_dir, f"{tid}-{dt.datetime.now().strftime('%H%M')}")
    path.write_text(f"# {mtg.title} · {template_label(tid)}\n\n{content}\n", encoding="utf-8")
    return str(path), content


# ---------- long-term memory ----------

_CANON_ORDER = ("people", "projects", "commitments", "terms")


def _parse_memory(text: str) -> "tuple[str, list[dict]]":
    """Split a memory file into (preamble, [{heading, canon, entries}]).

    An entry is a top-level bullet plus its indented continuation lines; other
    non-blank top-level lines inside a section are kept verbatim as their own entry.
    """
    preamble: list[str] = []
    secs: list[dict] = []
    cur: "dict | None" = None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = {"heading": line[3:].strip(), "canon": canonical_section(line), "entries": []}
            secs.append(cur)
            continue
        if cur is None:
            preamble.append(line)
            continue
        if not line.strip():
            continue
        if line[:1].isspace() and cur["entries"]:
            cur["entries"][-1] += "\n" + line.rstrip()  # indented continuation of the entry above
        else:
            cur["entries"].append(line.rstrip())
    return "\n".join(preamble).rstrip(), secs


_KEY_RE = re.compile(r"^[-*]\s+(?:\*\*(.+?)\*\*|([^:：]{1,40}))\s*[:：]")


def _entry_key(entry: str) -> "str | None":
    m = _KEY_RE.match(entry.splitlines()[0])
    if not m:
        return None
    return re.sub(r"\s+", " ", (m.group(1) or m.group(2)).replace("*", "")).strip().lower()


def _first_line(entry: str) -> str:
    return re.sub(r"[*\[\]（）()：:，,。.\s]+", " ", entry.splitlines()[0][2:]).strip().lower()


def apply_delta(old: str, delta: str, lang: "str | None" = None) -> str:
    """Merge a delta (new / updated entries under the same headings) into the memory file.

    People / projects / terms match on the bolded name before the colon; commitments
    (no stable key) match by text similarity. Unmatched entries are appended.
    """
    pre, secs = _parse_memory(old)
    _, dsecs = _parse_memory(delta)
    sec_names = sections(lang)
    by_canon = {s["canon"]: s for s in secs if s["canon"]}
    added: list[dict] = []  # sections that did not exist yet, appended in canonical order
    for ds in dsecs:
        canon = ds["canon"]
        if not canon or not ds["entries"]:
            continue
        target = by_canon.get(canon)
        if target is None:
            target = {"heading": sec_names.get(canon, ds["heading"]), "canon": canon, "entries": []}
            added.append(target)
            by_canon[canon] = target
        replaced: set[int] = set()  # an existing entry is replaced at most once per merge
        for entry in ds["entries"]:
            if not entry.startswith(("- ", "* ")):
                continue
            key = _entry_key(entry) if canon != "commitments" else None
            idx = None
            if key:
                for i, ex in enumerate(target["entries"]):
                    if _entry_key(ex) == key and i not in replaced:
                        idx = i
                        break
            if idx is None:
                a = _first_line(entry)
                best, best_r = None, 0.0
                for i, ex in enumerate(target["entries"]):
                    if i in replaced:
                        continue
                    r = difflib.SequenceMatcher(None, a, _first_line(ex)).ratio()
                    if r > best_r:
                        best, best_r = i, r
                if best is not None and best_r >= (0.6 if canon == "commitments" else 0.8):
                    idx = best
            if idx is None:
                target["entries"].append(entry)
            else:
                target["entries"][idx] = entry
                replaced.add(idx)
    if not pre.strip():
        pre = f"# {sec_names['longterm_title']}"
    # keep the file's own section order; brand-new sections follow in canonical order
    added.sort(key=lambda s: _CANON_ORDER.index(s["canon"]) if s["canon"] in _CANON_ORDER else 9)
    out = [pre, ""]
    for s in secs + added:
        out.append(f"## {s['heading']}")
        out.append("")
        for e in s["entries"]:
            out.append(e)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _memory_headings_lang(old: str) -> "str | None":
    """Existing files define the heading language; empty/placeholder files use the output language."""
    return detect_headings_lang(old) or None


def _merge_longterm(source_id: str, text: str, kind: str = "transcript") -> str:
    """Merge one piece of material (transcript / daily brief) into memory/longterm.md."""
    cfg = load_config()
    mode = str(cfg.get("memory_merge") or "delta")
    with MEMORY_LOCK:
        old = LONGTERM_FILE.read_text(encoding="utf-8") if LONGTERM_FILE.exists() else ""
        P, directive, sec = _lang_ctx()
        file_lang = _memory_headings_lang(old)
        if file_lang:
            sec = sections(file_lang)
        kind_label = P.CTX["kind_brief" if kind == "brief" else "kind_transcript"]
        material = (f"<new_material kind=\"{kind_label}\" id=\"{source_id}\">\n"
                    f"{text[:100_000]}\n</new_material>")
        current = f"<current_memory>\n{old.strip() or P.CTX['empty_memory']}\n</current_memory>"
        if mode == "full":
            prompt = f"{_fill(P.LONGTERM_FULL_PROMPT, sec)}\n{directive}\n\n{current}\n\n{material}"
            out = run_model(prompt, task="memory", timeout=1200)
            new = _validate_full(old, out)
        else:
            prompt = f"{_fill(P.LONGTERM_DELTA_PROMPT, sec)}\n{directive}\n\n{current}\n\n{material}"
            out = run_model(prompt, task="memory", timeout=1200).strip()
            if re.fullmatch(r"`*\s*NO_CHANGE\s*`*\.?", out) or not out:
                return old
            out = _strip_fence(out)
            if "## " not in out:
                raise AIError(t("ai.memory_bad_format"))
            new = apply_delta(old, out, file_lang or (get_lang() if output_lang() == "source" else output_lang()))
        if old.strip() and new != old:
            LONGTERM_FILE.with_suffix(".bak.md").write_text(old, encoding="utf-8")
        LONGTERM_FILE.write_text(new, encoding="utf-8")
    return new


def _has_all_sections(text: str) -> bool:
    """A complete memory file starts with a title and has all four canonical sections."""
    canons = {canonical_section(l) for l in text.splitlines() if l.startswith("## ")}
    return text.lstrip().startswith("# ") and set(_CANON_ORDER) <= canons


def _validate_full(old: str, out: str) -> str:
    """A full rewrite must be a complete file. A truncated model response (missing title or
    sections) once passed a looser check and wiped half of a memory file – never again."""
    out = _strip_fence(out)
    if not _has_all_sections(out):
        raise AIError(t("ai.memory_bad_format"))
    if len(old) > 2000 and len(out) < len(old) * 0.5:
        raise AIError(t("ai.memory_too_short"))
    return out.rstrip() + "\n"


def compact_longterm(progress=lambda msg: None) -> str:
    """Rewrite the whole long-term memory once to merge and compress it (the delta merge
    only ever appends / replaces entries, so run this when the file has grown)."""
    with MEMORY_LOCK:
        old = LONGTERM_FILE.read_text(encoding="utf-8") if LONGTERM_FILE.exists() else ""
        if old.count("\n- ") < 5:
            raise AIError(t("ai.memory_nothing_to_compact"))
        P, directive, sec = _lang_ctx()
        file_lang = _memory_headings_lang(old)
        if file_lang:
            sec = sections(file_lang)
        progress(t("ai.compacting"))
        prompt = (f"{_fill(P.LONGTERM_FULL_PROMPT, sec)}\n{directive}\n\n"
                  f"<current_memory>\n{old.strip()}\n</current_memory>\n\n"
                  f"<new_material kind=\"none\" id=\"compaction\">\n</new_material>")
        out = _strip_fence(run_model(prompt, task="memory", timeout=1800))
        if not _has_all_sections(out):
            raise AIError(t("ai.memory_bad_format"))
        if out.count("\n- ") < old.count("\n- ") * 0.5:
            raise AIError(t("ai.memory_compaction_dropped"))
        LONGTERM_FILE.with_suffix(".bak.md").write_text(old, encoding="utf-8")
        LONGTERM_FILE.write_text(out.rstrip() + "\n", encoding="utf-8")
    return out


def update_longterm(mtg: Meeting, force: bool = False) -> str:
    """Extract long-term memory from one meeting. Each meeting is processed once
    (meta.memorized_at); returns the new memory text, or '' when skipped."""
    if mtg.meta.get("memorized_at") and not force:
        return ""
    text = mtg.transcript_text()
    if len(text) < 200:  # too short to be worth it (tests / empty transcripts)
        mtg.save_meta(memorized_at="skipped-too-short")
        return ""
    out = _merge_longterm(mtg.id, text)
    mtg.save_meta(memorized_at=dt.datetime.now().isoformat(timespec="seconds"))
    return out


BRIEFS_MEMORIZED = LONGTERM_FILE.parent / ".briefs_memorized.json"


def memorize_brief(path, force: bool = False) -> str:
    """Merge a daily brief into long-term memory (once; memory/.briefs_memorized.json)."""
    import json as _json
    from pathlib import Path as _Path
    path = _Path(path)
    done = set(_json.loads(BRIEFS_MEMORIZED.read_text(encoding="utf-8"))
               if BRIEFS_MEMORIZED.exists() else [])
    if path.name in done and not force:
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) < 200:
        return ""
    out = _merge_longterm(f"brief-{path.stem}", text, kind="brief")
    done.add(path.name)
    BRIEFS_MEMORIZED.write_text(_json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")
    return out


# ---------- cross-meeting insight ----------

def track(focus: str = "", mode: str = "track",
          meetings: "list[Meeting] | None" = None, on_delta=None) -> "tuple[str, str]":
    """Cross-meeting insight. mode: track / signals / synthesis (legacy Chinese names accepted).
    meetings=None → all transcribed meetings. Returns (path, content)."""
    mid = resolve_track_mode(mode)
    if not mid:
        raise AIError(t("ai.unknown_mode", name=mode))
    if meetings is None:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    else:
        uniq = {}
        for m in meetings:
            if m.transcript_md.exists():
                uniq.setdefault(m.id, m)
        meetings = sorted(uniq.values(),
                          key=lambda m: (m.date, m.meta.get("created", ""), m.id), reverse=True)
    if len(meetings) < 2:
        raise AIError(t("ai.need_two_meetings"))
    P, directive, _ = _lang_ctx()
    ctx = _context_block(list(reversed(meetings)), limit=context_limit(profile_for_task("track")))
    focus_line = P.FOCUS_LINE.format(focus=focus.strip()) if focus.strip() else ""
    prompt = f"{_memory_block()}{P.TRACK_PROMPTS[mid]}{focus_line}\n{directive}\n\n{ctx}"
    content = run_model(prompt, task="track", timeout=1200, on_delta=on_delta)
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    from .library import _slug
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    name = f"{stamp}-{mid}" + (f"-{_slug(focus)[:20]}" if focus.strip() else "")
    path = _unique_path(TRACKING_DIR, name)
    title = (f"# {t('files.tracking')} · {track_label(mid)} · "
             f"{focus.strip() or t('files.global')} · {stamp}")
    content = _dedupe_title(title, content)
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content


# ---------- pre-meeting prep ----------

def _split_words(s: str) -> list[str]:
    """'Zhang San, Li Si' / '智舱 复盘' → keyword list (≥ 2 chars)."""
    return [w for w in re.split(r"[、,，;；/\s]+", s) if len(w.strip()) >= 2]


def _related_meetings(keywords: list[str], top: int = 6) -> "tuple[list[Meeting], str]":
    """Pick the most relevant past meetings by keyword hits; returns (meetings, how)."""
    ready = [m for m in list_meetings() if m.transcript_md.exists()]

    def rank(keys: list[str], min_score: int = 1) -> list[Meeting]:
        scored = []
        for m in ready:
            text = (m.title + "\n" + m.transcript_text()).lower()
            score = sum(text.count(k.lower()) for k in keys)
            if score >= min_score:
                scored.append((score, m))
        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:top]]

    if not keywords:
        return ready[:3], t("ai.picked_recent_nokw")
    hits = rank(keywords)
    if not hits:  # long CJK phrases: retry with 2-character shingles
        shingles = sorted({k[i:i + 2] for k in keywords
                           if re.fullmatch(r"[一-鿿]{4,}", k) for i in range(len(k) - 1)})
        if shingles:
            hits = rank(shingles, min_score=3)
    if hits:
        return hits, t("ai.picked_by_relevance")
    return ready[:3], t("ai.picked_recent_nohit")


def prep(topic: str, people: str = "", goal: str = "",
         use_web: bool = False, on_delta=None) -> "tuple[str, str]":
    """Pre-meeting brief from past meetings + memory (+ optional web search). Saved in library/_prep/."""
    topic = topic.strip()
    if not topic:
        raise AIError(t("ai.prep_need_topic"))
    keywords = _split_words(people) + _split_words(topic)
    meetings, picked = _related_meetings(keywords)
    meetings = sorted(meetings, key=lambda m: (m.date, m.meta.get("created", ""), m.id))
    P, directive, _ = _lang_ctx()
    ctx = (_context_block(meetings, per_meeting=60_000, limit=context_limit(profile_for_task("prep")))
           if meetings else P.CTX["empty_library"])
    head = [f"{P.PREP_HEAD['topic']}: {topic}"]
    if people.strip():
        head.append(f"{P.PREP_HEAD['people']}: {people.strip()}")
    if goal.strip():
        head.append(f"{P.PREP_HEAD['goal']}: {goal.strip()}")
    prompt_text = P.PREP_PROMPT.format(web_hint=P.PREP_WEB_HINT if use_web else "")
    prompt = (f"{_memory_block()}{prompt_text}\n{directive}\n\n<this_meeting>\n"
              + "\n".join(head) + "\n</this_meeting>\n\n"
              f"<past_meetings note=\"{picked}\">\n{ctx}\n</past_meetings>"
              + (P.PREP_WEB_GUARD if use_web else ""))
    content = run_model(prompt, task="prep", timeout=1800,
                        # WebSearch only, never WebFetch: links in the material must not be opened
                        allowed_tools=["WebSearch"] if use_web else None, on_delta=on_delta)
    PREP_DIR.mkdir(parents=True, exist_ok=True)
    from .library import _slug
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    path = _unique_path(PREP_DIR, f"{stamp}-{_slug(topic)[:24]}")
    title = f"# {t('files.prep')} · {topic} · {stamp}"
    content = _dedupe_title(title, content)
    src = ", ".join(m.id for m in meetings) or "-"
    path.write_text(f"{title}\n\n> {t('files.prep_sources')}: {src}\n\n{content}\n", encoding="utf-8")
    return str(path), content


def list_preps() -> list[dict]:
    if not PREP_DIR.exists():
        return []
    return [{"name": p.stem, "content": p.read_text(encoding="utf-8")}
            for p in sorted(PREP_DIR.glob("*.md"), reverse=True)]


# ---------- glossary extraction ----------

GLOSSARY_EXTRACT_LOCK = threading.Lock()


def _strip_fence(s: str) -> str:
    """Remove an accidental ```markdown fence so fence lines never become entries."""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[^\n]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def _has_glossary_sections(text: str) -> bool:
    canons = {canonical_section(l) for l in text.splitlines() if l.startswith("## ")}
    return "names" in canons and "glossary_terms" in canons


def extract_glossary(progress=lambda msg: None) -> str:
    """Extract names / proper nouns from long-term memory and recent transcripts into glossary.md.

    The model call runs outside MEMORY_LOCK (minutes long; must not block transcript memory
    merges); before writing back we check the glossary was not edited meanwhile.
    """
    if not GLOSSARY_EXTRACT_LOCK.acquire(blocking=False):
        raise AIError(t("ai.glossary_busy"))
    try:
        materials = []
        if LONGTERM_FILE.exists():
            lt = LONGTERM_FILE.read_text(encoding="utf-8").strip()
            if lt and "## " in lt:
                materials.append(f"<longterm>\n{lt[:MAX_LONGTERM_INJECT]}\n</longterm>")
        recent = [m for m in list_meetings() if m.transcript_md.exists()][:6]
        for m in recent:
            materials.append(f"<excerpt id=\"{m.id}\">\n{m.transcript_text()[:8_000]}\n</excerpt>")
        if not materials:
            raise AIError(t("ai.glossary_no_material"))
        progress(t("ai.glossary_extracting"))
        from .config import ensure_dirs
        ensure_dirs()
        old = GLOSSARY_FILE.read_text(encoding="utf-8")
        P, directive, sec = _lang_ctx()
        file_lang = detect_headings_lang(old)
        # glossary headings: keep the file's language if it already has entries in one
        gl = None
        for l in old.splitlines():
            if l.startswith("## ") and canonical_section(l) == "names":
                gl = l
        if gl:
            from .i18n import available
            for loc in available():
                if sections(loc["code"])["names"].lower() == gl[3:].strip().lower():
                    sec = sections(loc["code"])
                    break
        prompt = (f"{_fill(P.GLOSSARY_PROMPT, sec)}\n{directive}\n\n"
                  f"<current_glossary>\n{old.strip()}\n</current_glossary>\n\n"
                  + "\n\n".join(materials))
        out = _strip_fence(run_model(prompt, task="glossary", timeout=1200))
        if not _has_glossary_sections(out):
            raise AIError(t("ai.glossary_bad_format"))
        if len(old) > 1000 and len(out) < len(old) * 0.6:
            raise AIError(t("ai.glossary_too_short"))
        with MEMORY_LOCK:
            if GLOSSARY_FILE.read_text(encoding="utf-8") != old:
                raise AIError(t("ai.glossary_changed_meanwhile"))
            GLOSSARY_FILE.with_suffix(".bak.md").write_text(old, encoding="utf-8")
            GLOSSARY_FILE.write_text(out.rstrip() + "\n", encoding="utf-8")
        return out
    finally:
        GLOSSARY_EXTRACT_LOCK.release()


# ---------- daily / weekly briefs ----------

def daily_brief(date: str | None = None, on_delta=None) -> "tuple[str, str]":
    date = date or dt.date.today().isoformat()
    meetings = [m for m in meetings_on(date) if m.transcript_md.exists()]
    if not meetings:
        raise AIError(t("ai.no_meetings_on", date=date))
    P, directive, _ = _lang_ctx()
    ctx = _context_block(list(reversed(meetings)), limit=context_limit(profile_for_task("brief")))
    prompt = f"{_memory_block()}{P.BRIEF_PROMPT}\n{directive}\n\n{P.CTX['date']}: {date}\n\n{ctx}"
    title = f"# {t('files.brief')} · {date}"
    content = _dedupe_title(title, run_model(prompt, task="brief", on_delta=on_delta))
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{date}.md"
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content


def week_bounds(date: str | None = None) -> "tuple[str, str, str]":
    """(monday, sunday, 'YYYY-Www') of the ISO week containing ``date``."""
    d = dt.date.fromisoformat(date) if date else dt.date.today()
    monday = d - dt.timedelta(days=d.weekday())
    sunday = monday + dt.timedelta(days=6)
    year, week, _ = d.isocalendar()
    return monday.isoformat(), sunday.isoformat(), f"{year}-W{week:02d}"


def weekly_brief(date: str | None = None, on_delta=None) -> "tuple[str, str]":
    start, end, week = week_bounds(date)
    meetings = [m for m in list_meetings()
                if m.transcript_md.exists() and start <= m.date <= end]
    if not meetings:
        raise AIError(t("ai.no_meetings_in_week", week=week, start=start, end=end))
    P, directive, _ = _lang_ctx()
    ctx = _context_block(list(reversed(meetings)), per_meeting=60_000,
                         limit=context_limit(profile_for_task("weekly")))
    prompt = (f"{_memory_block()}{P.WEEKLY_PROMPT}\n{directive}\n\n"
              f"{P.CTX['week']}: {week} ({start} ~ {end}), "
              f"{P.CTX['meetings_count'].format(n=len(meetings))}\n\n{ctx}")
    title = f"# {t('files.weekly')} · {week} ({start[5:]} ~ {end[5:]})"
    content = _dedupe_title(title, run_model(prompt, task="weekly", timeout=1200, on_delta=on_delta))
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    path = WEEKLY_DIR / f"{week}.md"
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content
