"""AI analysis layer.

Every prompt goes through :mod:`providers` (claude CLI by default, or any Anthropic- / OpenAI-
compatible endpoint). Prompts come from :mod:`prompts` in the output language; all functions accept
``on_delta`` so the web UI can stream text as it is generated.

Context is kept small on purpose, because prompt size drives both waiting time and verbosity:
only the long-term memory entries and glossary entries the material mentions are injected,
Whisper's two-second fragments are joined into paragraphs before they reach the model, and
questions over the whole library read the most relevant meetings in full plus a one-line index
of the rest.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

from . import memory, prompts
from .config import (BRIEFS_DIR, GLOSSARY_FILE, MEMORY_FILE, PREP_DIR, TRACKING_DIR, WEEKLY_DIR,
                     load_config)
from .i18n import canonical_section, detect_headings_lang, get_lang, output_lang, sections, t
from .library import Meeting, list_meetings, meetings_on
from .providers import ModelError, context_limit, profile_for_task, resolve_claude_bin, run_model
from .templates import resolve_template, resolve_track_mode, template_label, track_label

AIError = ModelError  # one exception type for callers, whichever layer raised it

MEMORY_BUDGET = 6_000      # characters of long-term memory injected into one analysis
GLOSSARY_BUDGET = 3_000    # characters of glossary injected into one analysis
CROSS_BUDGET = 160_000     # transcript characters read in full by one cross-meeting task
MEMORY_LOCK = threading.Lock()      # glossary read-modify-write is serialised
TRANSCRIPT_LOCK = threading.Lock()  # transcript.md writes (name fix / manual save / restore)
# report length per template, relative to the minutes budget
TEMPLATE_WEIGHT = {"minutes": 1.0, "actions": 0.5, "mood": 0.6, "tension": 0.7, "bias": 0.6,
                   "topics": 0.6, "client": 0.8, "hiring": 0.8, "followup": 0.7}

__all__ = ["AIError", "resolve_claude_bin"]


# ---------- language helpers ----------

def _lang_ctx() -> "tuple[object, str, dict]":
    """(prompt module, language directive line, section headings) for the current output language."""
    lang = output_lang()
    P = prompts.get(get_lang() if lang == "source" else lang)
    return P, prompts.language_directive(lang), sections(None if lang != "source" else get_lang())


def _fill(text: str, sec: dict, **extra) -> str:
    return text.format(**{**sec, **extra})


def _zh_out() -> bool:
    lang = output_lang()
    return (get_lang() if lang == "source" else lang).lower().startswith("zh")


def _budget(material: str, weight: float = 1.0, wide: bool = False) -> int:
    """Length cap for one answer, from the size of the material: Chinese characters for Chinese
    output, words otherwise. A 45-minute meeting gets about one page; cross-meeting reports
    (``wide``) may run to about two."""
    n = len(material)
    if _zh_out():
        return int(min(2800 if wide else 1600, max(400, n / 20 * weight)))
    return int(min(1700 if wide else 1000, max(250, n / 45 * weight)))


def _style(material: str, weight: float = 1.0, wide: bool = False) -> str:
    P, _, _ = _lang_ctx()
    return P.STYLE.format(budget=_budget(material, weight, wide)) + "\n" + P.ATTRIBUTION


# ---------- transcript shaping ----------

_STAMPED = re.compile(r"^\[(\d+:\d{2}(?::\d{2})?)\]\s*(.*)$")


def compact_transcript(md: str, max_len: int = 160) -> str:
    """Join consecutive short '[mm:ss] text' lines into paragraphs (first timestamp kept).

    Older Whisper transcripts carry a timestamp every 14 characters; joining them removes about a
    third of the prompt without losing words. Header lines and long lines pass through unchanged."""
    out: list[str] = []
    cur_stamp, cur = "", ""

    def flush():
        nonlocal cur_stamp, cur
        if cur_stamp:
            out.append(f"[{cur_stamp}] {cur}")
        cur_stamp, cur = "", ""

    for line in md.splitlines():
        m = _STAMPED.match(line)
        if not m:
            flush()
            out.append(line)
            continue
        stamp, text = m.groups()
        text = text.strip()
        if cur_stamp and len(cur) + len(text) <= max_len and not re.match(r"[^:：]{1,24}[:：]", text):
            sep = "，" if re.search(r"[㐀-鿿]$", cur) and re.match(r"[㐀-鿿]", text) else (
                "" if re.search(r"[　-〿＀-￯]$", cur) else " ")
            cur = f"{cur}{sep}{text}"
        else:
            flush()
            cur_stamp, cur = stamp, text
    flush()
    return "\n".join(out)


def _transcript(m: Meeting) -> str:
    return compact_transcript(m.transcript_text())


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
    out = run_model(f"{_memory_block(text)}{P.NAME_FIX_PROMPT}\n\n<{what}>\n{text}\n</{what}>",
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

def _glossary_block(text: str, budget: int = GLOSSARY_BUDGET) -> str:
    """Glossary lines whose spelling or known mishearing occurs in the material, then the other
    names; the whole glossary when there is no material to match against."""
    if not GLOSSARY_FILE.exists():
        return ""
    from .glossary import glossary_stats, parse_glossary
    raw = GLOSSARY_FILE.read_text(encoding="utf-8")
    s = glossary_stats(raw)
    if not (s["names"] or s["terms"]):
        return ""
    if not text:
        return re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()[:budget * 3]
    g = parse_glossary(raw)
    low = text.lower()
    lines_hit, lines_rest = [], []
    for kind in ("names", "terms"):
        for e in g[kind]:
            variants = [e["term"], *e["wrong"]]
            line = f"- {e['term']}" + (f"（{'、'.join(e['wrong'])}）" if e["wrong"] else "") + (
                f"｜{e['note'][:60]}" if e["note"] else "")
            if any(v and v.lower() in low for v in variants):
                lines_hit.append(line)
            elif kind == "names":
                lines_rest.append(f"- {e['term']}")
    out, used = [], 0
    for line in lines_hit + lines_rest:
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _memory_block(text: str = "", extra: "list[str] | None" = None, budget: int = MEMORY_BUDGET) -> str:
    """Glossary + hand-written memory + the long-term memory entries related to ``text``."""
    P, _, _ = _lang_ctx()
    parts = []
    gl = _glossary_block(text)
    if gl:
        parts.append(f"<glossary note=\"{P.CTX['glossary_note']}\">\n{gl}\n</glossary>")
    if MEMORY_FILE.exists():
        manual = re.sub(r"<!--.*?-->", "", MEMORY_FILE.read_text(encoding="utf-8"), flags=re.S).strip()
        if manual and not re.fullmatch(r"#[^\n]*", manual):  # title only = empty
            parts.append(f"<memory>\n{manual[:8000]}\n</memory>")
    try:
        lt = memory.context_block(text, extra, budget=budget, lang=memory.heading_lang())
    except Exception:  # a damaged store never blocks an analysis
        lt = ""
    if lt:
        parts.append(f"<longterm note=\"{P.CTX['longterm_note']}\">\n{lt}\n</longterm>")
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _people(meetings: "list[Meeting]") -> list[str]:
    return [str(m.meta.get("participants") or "") for m in meetings if m.meta.get("participants")]


def _context_block(meetings: list[Meeting], per_meeting: int | None = None,
                   limit: int | None = None) -> str:
    """Concatenate (compacted) transcripts. ``per_meeting`` caps one meeting so an early long one
    cannot push later meetings out entirely; ``limit`` is the character budget."""
    P, _, _ = _lang_ctx()
    limit = limit or context_limit()
    parts, total = [], 0
    for m in meetings:
        text = _transcript(m)
        if not text:
            continue
        if per_meeting and len(text) > per_meeting:
            text = text[:per_meeting] + "\n" + P.CTX["truncated_one"]
        if total + len(text) > limit:
            text = text[: max(limit - total, 0)] + "\n" + P.CTX["truncated_all"]
        who = " ".join(str(m.meta.get("participants", "")).split())
        attr = f' participants="{who.replace(chr(34), chr(39))}"' if who else ""
        parts.append(f"<meeting id=\"{m.id}\" date=\"{m.date}\"{attr}>\n{text}\n</meeting>")
        total += len(text)
        if total >= limit:
            break
    return "\n\n".join(parts)


def _index_block(meetings: list[Meeting]) -> str:
    """One line per meeting that is not attached in full: date, title, digest."""
    P, _, _ = _lang_ctx()
    if not meetings:
        return ""
    rows = [f"- [{m.id}] {m.date} {m.title}" + (f": {m.meta['digest']}" if m.meta.get("digest") else "")
            for m in meetings]
    return f"<library_index note=\"{P.CTX['index_note']}\">\n" + "\n".join(rows) + "\n</library_index>\n\n"


def _keywords(text: str) -> list[str]:
    words = [w for w in re.split(r"[\s,，。、;；:：?？!！/()（）\"“”'‘’\[\]]+", text) if len(w) >= 2]
    stop = {"什么", "怎么", "哪些", "这个", "那个", "我们", "他们", "有没有", "是否", "the", "and", "what",
            "who", "how", "did", "does", "with", "about", "for", "that", "this", "所有", "全部", "会议"}
    out = [w for w in words if w.lower() not in stop]
    for w in list(out):  # long Chinese phrases: also their two-character pieces
        if re.fullmatch(r"[㐀-鿿]{4,}", w):
            out += [w[i:i + 2] for i in range(len(w) - 1)]
    return out


def rank_meetings(query: str, meetings: "list[Meeting]", extra: "list[str] | None" = None) -> "list[Meeting]":
    """Most relevant first: keyword hits in title / digest / transcript, plus the sources of the
    long-term memory entries the query mentions; recency breaks ties."""
    keys = _keywords(query + " " + " ".join(extra or []))
    boost: dict[str, int] = {}
    try:
        for e in memory.relevant(query, extra, limit=12):
            for s in e.get("sources") or []:
                boost[s] = boost.get(s, 0) + 3
    except Exception:
        pass
    scored = []
    for i, m in enumerate(meetings):
        head = (m.title + " " + str(m.meta.get("digest") or "")).lower()
        body = m.transcript_text().lower()
        score = sum(head.count(k.lower()) * 5 + min(body.count(k.lower()), 20) for k in keys) + boost.get(m.id, 0)
        scored.append((score, -i, m))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [m for _, _, m in scored]


def _select(meetings: "list[Meeting]", query: str, limit: int, extra=None) -> "tuple[list[Meeting], list[Meeting]]":
    """(read in full, index only) within ``limit`` characters; chronological for the full set."""
    total = sum(len(_transcript(m)) for m in meetings)
    if total <= limit:
        return meetings, []
    ranked = rank_meetings(query, meetings, extra) if query.strip() else list(meetings)
    full, used = [], 0
    for m in ranked:
        n = len(_transcript(m))
        if used + n > limit and full:
            continue
        full.append(m)
        used += n
        if used >= limit:
            break
    keep = {m.id for m in full}
    return ([m for m in meetings if m.id in keep], [m for m in meetings if m.id not in keep])


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


def _cross_limit(task: str) -> int:
    p = profile_for_task(task)
    explicit = int(p.get("max_context_chars") or 0)
    return explicit or min(CROSS_BUDGET, context_limit(p))


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
    sample = compact_transcript(text)[:20_000]
    prompt = (f"{_memory_block(sample, budget=2500)}{P.PARTICIPANTS_PROMPT}\n{directive}\n\n"
              f"<speaker_material id=\"{mtg.id}\">\n{sample}\n</speaker_material>")
    out = _strip_fence(run_model(prompt, task="participants", timeout=300)).strip()
    out = " ".join(l.strip(" -*") for l in out.splitlines() if l.strip())
    if not out or len(out) > 400:
        raise AIError(t("ai.participants_bad"))
    return out


def ask(question: str, meetings: list[Meeting], on_delta=None) -> str:
    P, directive, _ = _lang_ctx()
    if len(meetings) > 1:
        full, rest = _select(meetings, question, _cross_limit("ask"), _people(meetings))
    else:
        full, rest = meetings, []
    ctx = _context_block(full, limit=context_limit(profile_for_task("ask")))
    if not ctx:
        raise AIError(t("ai.refs_no_transcript"))
    prompt = (f"{P.CHAT_SYSTEM}\n{P.ATTRIBUTION}\n{directive}\n\n"
              f"{_memory_block(question + chr(10) + ctx[:60_000], _people(full))}{_participants_block(full)}"
              f"{_index_block(rest)}{ctx}\n\n<question>\n{question}\n</question>")
    return run_model(prompt, task="ask", on_delta=on_delta)


def generate_report(mtg: Meeting, template: str, on_delta=None) -> "tuple[str, str]":
    """Generate a template report into reports/; returns (path, content)."""
    tid = resolve_template(template)
    if not tid:
        raise AIError(t("ai.unknown_template", name=template))
    text = _transcript(mtg)
    if not text:
        raise AIError(t("ai.no_transcript", id=mtg.id))
    P, directive, _ = _lang_ctx()
    limit = context_limit(profile_for_task("report"))
    body = text[:limit]
    prompt = (
        f"{P.CHAT_SYSTEM}\n\n{_memory_block(body, _people([mtg]))}{_participants_block([mtg])}"
        f"<meeting id=\"{mtg.id}\" date=\"{mtg.date}\">\n{body}\n</meeting>\n\n"
        f"<task>\n{P.TEMPLATES[tid]}\n</task>\n\n{_style(body, TEMPLATE_WEIGHT.get(tid, 1.0))}\n"
        f"{P.REPORT_TAIL}\n{directive}"
    )
    content = _dedupe_title(f"# {template_label(tid)}", run_model(prompt, task="report", on_delta=on_delta))
    mtg.reports_dir.mkdir(exist_ok=True)
    path = _unique_path(mtg.reports_dir, f"{tid}-{dt.datetime.now().strftime('%H%M')}")
    path.write_text(f"# {mtg.title} · {template_label(tid)}\n\n{content}\n", encoding="utf-8")
    return str(path), content


# ---------- long-term memory ----------

def _merge_longterm(source_id: str, text: str, kind: str = "transcript", date: str = "",
                    participants: str = "") -> "tuple[str, int]":
    """Merge one piece of material into the structured memory → (digest, entries changed).

    The model sees the existing entries the material mentions (not the whole memory) and answers
    with JSON; coco merges it."""
    P, directive, _ = _lang_ctx()
    material = compact_transcript(text)[:100_000]
    extra = [participants] if participants else []
    kind_label = P.CTX["kind_brief" if kind == "brief" else "kind_transcript"]
    prompt = (f"{P.MEMORY_JSON_PROMPT}\n{directive}\n\n"
              f"<existing_entries>\n{memory.candidates_json(material, extra)}\n</existing_entries>\n\n"
              f"<name_index>\n{memory.name_index()}\n</name_index>\n\n"
              f"<new_material kind=\"{kind_label}\" id=\"{source_id}\" date=\"{date}\">\n{material}\n</new_material>")
    out = run_model(prompt, task="memory", timeout=900)
    try:
        data = memory.parse_model_json(out)
    except ValueError:
        raise AIError(t("ai.memory_bad_format"))
    n = memory.apply_model_output(data, source_id, date)
    return str(data.get("digest") or "").strip()[:200], n


def update_longterm(mtg: Meeting, force: bool = False) -> str:
    """Extract long-term memory from one meeting. Each meeting is processed once
    (meta.memorized_at); returns the meeting's one-line digest ('' when skipped)."""
    if mtg.meta.get("memorized_at") and not force:
        return ""
    text = mtg.transcript_text()
    if len(text) < 200:  # too short to be worth it (tests / empty transcripts)
        mtg.save_meta(memorized_at="skipped-too-short")
        return ""
    digest, n = _merge_longterm(mtg.id, text, date=mtg.date, participants=str(mtg.meta.get("participants") or ""))
    mtg.save_meta(memorized_at=dt.datetime.now().isoformat(timespec="seconds"), memory_entries=n,
                  **({"digest": digest} if digest else {}), memory_error=None)
    return digest


BRIEFS_MEMORIZED = memory.KNOWLEDGE_FILE.parent / ".briefs_memorized.json"


def memorize_brief(path, force: bool = False) -> str:
    """Merge a daily brief into long-term memory (once; memory/.briefs_memorized.json)."""
    from pathlib import Path as _Path
    path = _Path(path)
    done = set(json.loads(BRIEFS_MEMORIZED.read_text(encoding="utf-8"))
               if BRIEFS_MEMORIZED.exists() else [])
    if path.name in done and not force:
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) < 200:
        return ""
    digest, _ = _merge_longterm(f"brief-{path.stem}", text, kind="brief", date=path.stem[:10])
    done.add(path.name)
    BRIEFS_MEMORIZED.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")
    return digest


def tidy_entries(ids: "list[str]", progress=lambda msg: None) -> int:
    """Rewrite the summaries and merge the facts of the given entries (8 per model call)."""
    P, directive, _ = _lang_ctx()
    changed = 0
    batches = [ids[i:i + 8] for i in range(0, len(ids), 8)]
    for bi, batch in enumerate(batches, 1):
        progress(t("ai.tidy_progress", i=bi, n=len(batches)))
        rows = []
        for eid in batch:
            e = memory.get(eid)
            if e:
                rows.append({"id": e["id"], "type": e["type"], "name": e["name"], "summary": e.get("summary", ""),
                             "facts": e.get("facts") or []})
        if not rows:
            continue
        prompt = (f"{P.TIDY_PROMPT}\n{directive}\n\n<tidy_entries>\n"
                  f"{json.dumps(rows, ensure_ascii=False)}\n</tidy_entries>")
        try:
            data = memory.parse_model_json(run_model(prompt, task="memory", timeout=900))
        except ValueError:
            raise AIError(t("ai.memory_bad_format"))
        by_id = {r["id"]: r for r in rows}
        for item in data.get("entities") or []:
            old = by_id.get(str(item.get("id")))
            if not old:
                continue
            facts = [f for f in item.get("facts") or [] if isinstance(f, dict) and str(f.get("text", "")).strip()]
            # a tidy that drops most facts is a truncated answer, not a tidy: keep the entry as it was
            if len(old["facts"]) >= 6 and len(facts) < len(old["facts"]) * 0.3:
                continue
            fields = {"facts": facts} if facts or not old["facts"] else {}
            if str(item.get("summary") or "").strip():
                fields["summary"] = item["summary"]
            if fields:
                memory.update(old["id"], fields)
                changed += 1
    return changed


def compact_longterm(progress=lambda msg: None) -> int:
    """Tidy every entry whose summary is long or whose facts have piled up."""
    ids = [e["id"] for e in memory.tidy_targets()]
    if not ids:
        raise AIError(t("ai.memory_nothing_to_compact"))
    progress(t("ai.compacting"))
    return tidy_entries(ids, progress=progress)


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
    # newest first for selection without a focus; the model reads them in chronological order
    full, rest = _select(meetings, focus, _cross_limit("track"))
    full = list(reversed(full))
    ctx = _context_block(full, per_meeting=60_000, limit=context_limit(profile_for_task("track")))
    focus_line = P.FOCUS_LINE.format(focus=focus.strip()) if focus.strip() else ""
    commitments = ""
    if mid == "track":
        open_c = [e for e in memory.entities("commitment") if e.get("status") in ("open", "unknown")][-60:]
        if open_c:
            rows = "\n".join(f"- {e['name']}：{e.get('summary', '')[:160]} [{(e.get('sources') or [''])[-1]}]"
                             for e in open_c)
            commitments = f"<open_commitments note=\"{P.CTX['open_commitments']}\">\n{rows}\n</open_commitments>\n\n"
    material = focus + "\n" + ctx[:60_000]
    prompt = (f"{_memory_block(material, budget=4000)}{P.TRACK_PROMPTS[mid]}{focus_line}\n"
              f"{_style(ctx, 1.3, wide=True)}\n{directive}\n\n{commitments}{_index_block(rest)}{ctx}")
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
    """Pick the most relevant past meetings; returns (meetings, how)."""
    ready = [m for m in list_meetings() if m.transcript_md.exists()]
    if not keywords:
        return ready[:3], t("ai.picked_recent_nokw")
    ranked = rank_meetings(" ".join(keywords), ready)
    keys = [k.lower() for k in _keywords(" ".join(keywords))]
    hits = [m for m in ranked[:top]
            if any(k in (m.title + m.transcript_text() + str(m.meta.get("digest") or "")).lower() for k in keys)]
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
    ctx = (_context_block(meetings, per_meeting=60_000, limit=_cross_limit("prep"))
           if meetings else P.CTX["empty_library"])
    head = [f"{P.PREP_HEAD['topic']}: {topic}"]
    if people.strip():
        head.append(f"{P.PREP_HEAD['people']}: {people.strip()}")
    if goal.strip():
        head.append(f"{P.PREP_HEAD['goal']}: {goal.strip()}")
    prompt_text = P.PREP_PROMPT.format(web_hint=P.PREP_WEB_HINT if use_web else "")
    material = "\n".join(head) + "\n" + ctx[:60_000]
    prompt = (f"{_memory_block(material, [people], budget=8000)}{prompt_text}\n{_style(ctx, 1.0, wide=True)}\n"
              f"{directive}\n\n<this_meeting>\n" + "\n".join(head) + "\n</this_meeting>\n\n"
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
        ents = [e for e in memory.entities() if e["type"] in ("person", "project", "term")]
        if ents:
            materials.append(f"<longterm>\n{memory.to_markdown(ents, facts=False)[:30_000]}\n</longterm>")
        recent = [m for m in list_meetings() if m.transcript_md.exists()][:6]
        for m in recent:
            materials.append(f"<excerpt id=\"{m.id}\">\n{_transcript(m)[:8_000]}\n</excerpt>")
        if not materials:
            raise AIError(t("ai.glossary_no_material"))
        progress(t("ai.glossary_extracting"))
        from .config import ensure_dirs
        ensure_dirs()
        old = GLOSSARY_FILE.read_text(encoding="utf-8")
        P, directive, sec = _lang_ctx()
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
    ctx = _context_block(list(reversed(meetings)), limit=_cross_limit("brief"))
    prompt = (f"{_memory_block(ctx[:60_000], _people(meetings), budget=3000)}{P.BRIEF_PROMPT}\n"
              f"{_style(ctx, 0.6)}\n{directive}\n\n{P.CTX['date']}: {date}\n\n{ctx}")
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
    full, rest = _select(list(reversed(meetings)), "", _cross_limit("weekly"))
    ctx = _context_block(full, per_meeting=60_000, limit=context_limit(profile_for_task("weekly")))
    prompt = (f"{_memory_block(ctx[:60_000], _people(meetings), budget=4000)}{P.WEEKLY_PROMPT}\n"
              f"{_style(ctx, 0.8, wide=True)}\n{directive}\n\n"
              f"{P.CTX['week']}: {week} ({start} ~ {end}), "
              f"{P.CTX['meetings_count'].format(n=len(meetings))}\n\n{_index_block(rest)}{ctx}")
    title = f"# {t('files.weekly')} · {week} ({start[5:]} ~ {end[5:]})"
    content = _dedupe_title(title, run_model(prompt, task="weekly", timeout=1200, on_delta=on_delta))
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    path = WEEKLY_DIR / f"{week}.md"
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content
