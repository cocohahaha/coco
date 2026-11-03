"""Long-term memory as structured entries (memory/knowledge.json).

Every person, project / client, commitment / decision and term is one entry:

    {"id", "type": person|project|commitment|term, "name", "aliases": [...],
     "summary": current state in a sentence or two,
     "facts": [{"text", "date", "source"}],          # dated, with the meeting they came from
     "status", "owner", "due",                        # commitments
     "created", "updated"}

``memory/longterm.md`` is rendered from it after every change, so the Markdown view, the download
and older tools keep working; editing that Markdown (memory dialog, ``POST /api/memory``) is parsed
back into entries. The first start after the upgrade migrates an existing longterm.md losslessly:
the bold name becomes the entry, its first line the summary, indented lines become facts, and the
[meeting-id] references become sources.

Why: one Markdown file had grown to 258 KB. Analyses only saw its first 30 000 characters, every
merge sent the whole file to the model, and there was no way to look up one person. Now merges
send only the entries the new material mentions, analyses get the relevant entries, and the UI
can list, search and edit entries one by one.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import threading
from pathlib import Path

from .config import LONGTERM_FILE
from .i18n import canonical_section, detect_headings_lang, sections, t

KNOWLEDGE_FILE = LONGTERM_FILE.parent / "knowledge.json"
TYPES = ("person", "project", "commitment", "term")
SECTION_OF = {"person": "people", "project": "projects", "commitment": "commitments", "term": "terms"}
TYPE_OF = {v: k for k, v in SECTION_OF.items()}
STATUSES = ("open", "done", "cancelled", "unknown")
LOCK = threading.RLock()
_REF = re.compile(r"(?<!\[)\[([^\[\]\n]{4,90})\](?!\])")  # [[wiki links]] are text, not sources
_STATUS_WORDS = {
    "done": ("已兑现", "已完成", "已生效", "已部署", "已定", "已执行", "done", "fulfilled", "completed", "réalisé"),
    "cancelled": ("已取消", "已放弃", "作废", "cancelled", "canceled", "dropped", "annulé"),
    "open": ("进行中", "待办", "待执行", "待测试", "待定", "未兑现", "推进中", "in progress", "open", "pending",
             "en cours"),
    "unknown": ("未知", "unknown", "inconnu"),
}


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def norm(s: str) -> str:
    return re.sub(r"[\s*_`'\"“”‘’·・.,，。:：;；!！?？()（）\[\]【】<>《》—–\-]+", "", str(s)).lower()


def slug(s: str) -> str:
    v = re.sub(r"[^\w㐀-鿿]+", "-", str(s).strip().lower()).strip("-")
    return v[:40] or "x"


# ---------- storage ----------

def _empty() -> dict:
    return {"version": 1, "updated": now(), "entities": []}


def load() -> dict:
    """The store; migrates longterm.md on first use."""
    with LOCK:
        if KNOWLEDGE_FILE.exists():
            try:
                data = json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
                if isinstance(data.get("entities"), list):
                    return data
            except ValueError:
                broken = KNOWLEDGE_FILE.with_name(f"knowledge.damaged-{dt.date.today().isoformat()}.json")
                KNOWLEDGE_FILE.replace(broken)
        data = _empty()
        if LONGTERM_FILE.exists():
            md = LONGTERM_FILE.read_text(encoding="utf-8")
            data["entities"] = from_markdown(md)
            if data["entities"]:
                backup = LONGTERM_FILE.with_name("longterm.pre-structured.md")
                if not backup.exists():
                    backup.write_text(md, encoding="utf-8")
        save(data, render_md=bool(data["entities"]))
        return data


def save(data: dict, render_md: bool = True) -> None:
    from .library import write_atomic
    with LOCK:
        data["updated"] = now()
        KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(KNOWLEDGE_FILE, json.dumps(data, ensure_ascii=False, indent=1))
        if render_md:
            write_atomic(LONGTERM_FILE, to_markdown(data["entities"]))


def entities(kind: "str | None" = None) -> list[dict]:
    ents = load()["entities"]
    return [e for e in ents if not kind or e["type"] == kind]


def get(eid: str) -> "dict | None":
    return next((e for e in load()["entities"] if e["id"] == eid), None)


def _new_id(kind: str, name: str, taken: set) -> str:
    base = f"{kind[0]}-{slug(name)}"
    eid, n = base, 2
    while eid in taken:
        eid, n = f"{base}-{n}", n + 1
    return eid


def _sources(e: dict) -> list[str]:
    src = {f.get("source") for f in e.get("facts") or [] if f.get("source")}
    src.update(e.get("sources") or [])
    return sorted(s for s in src if s)


def _finish(e: dict) -> dict:
    e["sources"] = _sources(e)
    e.setdefault("aliases", [])
    e.setdefault("facts", [])
    e.setdefault("summary", "")
    if e["type"] == "commitment":
        e.setdefault("status", "unknown")
        e.setdefault("owner", "")
        e.setdefault("due", "")
    return e


# ---------- Markdown → entries (migration, manual edits) ----------

def _refs(text: str, known: "set[str] | None" = None) -> list[str]:
    out = []
    for m in _REF.finditer(text):
        v = m.group(1).strip()
        if re.match(r"\d{1,2}:\d{2}", v):
            continue  # a timestamp, not a source
        if (known and v in known) or re.match(r"(\d{4}-\d{2}-\d{2}|brief-|每日简报|周报|weekly)", v):
            out.append(v)
    return out


def _date_of(source: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", source or "")
    return m.group(1) if m else ""


def _strip_refs(text: str, refs: list[str]) -> str:
    for r in refs:
        text = text.replace(f"[{r}]", "")
    return re.sub(r"\s{2,}", " ", text).strip()


def _split_name(line: str, kind: "str | None" = None) -> "tuple[str, list[str], str]":
    """'- **林炜（LW，转写亦作「林伟」）**：产品负责人 …' → ('林炜', ['LW', '林伟'], '产品负责人 …')."""
    body = re.sub(r"^[-*]\s+", "", line.strip())
    m = re.match(r"\*\*(.+?)\*\*\s*(?:[:：]|——|—|-)?\s*(.*)$", body)
    if m:
        raw, rest = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.match(r"([^:：]{1,40})[:：]\s*(.*)$", body)
        if not m:
            return "", [], body
        raw, rest = m.group(1).strip(), m.group(2).strip()
    pm = re.match(r"(.+?)\s*[（(](.+)[）)]\s*$", raw)
    if not pm or kind == "commitment":  # '张三 承诺（8/20 周会）' is one title
        return raw.strip(), [], rest
    name, inner = pm.group(1).strip(), pm.group(2).strip()
    aliases, keep = _aliases_from(inner, kind)
    if keep:  # anything that is not clearly another spelling of the name stays readable
        rest = f"（{keep}）{rest}"
    return name, [a for a in aliases if a != name][:8], rest


_SPLIT = re.compile(r"[、，,；;]|(?<!\d)[／/](?!\d)|\s+or\s+")
_DESCRIPTIVE = re.compile(r"存疑|待核|未具名|成员|负责|之一|客户|集团|周会|会议|标题|说话人|\d")


def _clean_alias(p: str) -> str:
    p = re.sub(r"[「」『』\"“”]", "", p).strip()
    return re.sub(r"\s*(存疑|待核|待确认|\?|？)$", "", p).strip()


def _aliases_from(inner: str, kind: "str | None") -> "tuple[list[str], str]":
    """Split a parenthesis after a name into (aliases, text to keep in the summary).

    Explicit hints ('转写亦作「林伟」', 'aka Sam') give aliases; without a hint, a person / project
    parenthesis made only of short names ('林伟/Lin Way') does too. Everything else is kept."""
    hint = re.search(r"(?:转写亦作|转写作|亦作|又称|误写|aka|also)\s*[:：]?\s*(.+)$", inner, re.I)
    if hint:
        aliases = [_clean_alias(p) for p in _SPLIT.split(hint.group(1))]
        aliases = [a for a in aliases if 0 < len(a) <= 24]
        before = inner[: hint.start()].strip(" ，,;；、")
        if before and kind in ("person", "project"):
            bits = [b.strip() for b in _SPLIT.split(before) if b.strip()]
            if bits and all(len(b) <= 8 and not _DESCRIPTIVE.search(b) for b in bits):
                return bits + aliases, ""
        return aliases, before
    if kind in ("person", "project"):
        bits = [b.strip() for b in _SPLIT.split(inner) if b.strip()]
        if bits and all(len(b) <= 12 and not _DESCRIPTIVE.search(b) for b in bits):
            return [_clean_alias(b) for b in bits], ""
    return [], inner


def _status_of(text: str) -> str:
    m = re.search(r"(?:状态|status|statut)\s*[:：]\s*([^。.\n\[]+)", text, re.I)
    probe = (m.group(1) if m else text[-60:]).lower()
    for st in ("cancelled", "done", "open", "unknown"):
        if any(w.lower() in probe for w in _STATUS_WORDS[st]):
            return st
    return "unknown"


def from_markdown(md: str, known_ids: "set[str] | None" = None) -> list[dict]:
    """Parse a longterm.md (any shipped language) into entries; nothing is dropped."""
    out: list[dict] = []
    taken: set = set()
    kind = None
    cur: "dict | None" = None
    stamp = now()
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            canon = canonical_section(line)
            kind = TYPE_OF.get(canon or "")
            cur = None
            continue
        if not kind or not line.strip() or line.lstrip().startswith("<!--"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and re.match(r"[-*]\s+", line):
            name, aliases, rest = _split_name(line, kind)
            refs = _refs(line, known_ids)
            summary = _strip_refs(rest, refs)
            if kind == "commitment" or not name:
                title = name or re.sub(r"[*]+", "", summary)[:48]
                e = {"type": kind, "name": title.strip() or "-", "aliases": aliases, "summary": summary}
            else:
                e = {"type": kind, "name": name, "aliases": aliases, "summary": summary}
            if kind == "commitment":
                e["status"] = _status_of(line)
                own = re.match(r"([A-Za-z㐀-鿿]{1,12})\s*(?:承诺|待办|答应|promised|to-do)", name or "")
                e["owner"] = own.group(1) if own else ""
                e["due"] = ""
            e["facts"] = []
            e["sources"] = refs
            e["id"] = _new_id(kind, e["name"], taken)
            taken.add(e["id"])
            e["created"] = e["updated"] = stamp
            out.append(e)
            cur = e
        elif cur is not None:
            text = re.sub(r"^[-*]\s+", "", line.strip())
            refs = _refs(text, known_ids)
            clean = _strip_refs(text, refs)
            if not clean and refs:  # a line holding only the entry's sources
                cur["sources"] = sorted(set(cur.get("sources", [])) | set(refs))
                continue
            if not re.match(r"[-*]\s+", line.strip()) and cur["facts"]:
                cur["facts"][-1]["text"] += " " + clean  # wrapped continuation of the bullet above
                continue
            src = refs[-1] if refs else ""
            cur["facts"].append({"text": clean, "date": _date_of(src), "source": src,
                                 **({"depth": 1} if indent >= 4 else {})})
    for e in out:
        srcs = e.get("sources") or []
        year = (_date_of(srcs[-1]) if srcs else "") or now()[:4]
        year = year[:4]
        for f in e["facts"]:
            if not f["source"] and len(srcs) == 1:
                f["source"] = srcs[0]
            if not f["date"]:
                m = re.match(r"\**\s*(\d{1,2})/(\d{1,2})(?!\d)", f["text"])
                if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
                    f["date"] = f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
                else:
                    f["date"] = _date_of(f["source"])
            f.pop("depth", None)
    return _dedupe([_finish(e) for e in out])


def _dedupe(ents: list[dict]) -> list[dict]:
    """Same type + same name twice (older merges appended instead of replacing): keep one entry,
    the longer summary stays the summary, the other becomes a fact."""
    by: dict = {}
    out: list[dict] = []
    for e in ents:
        key = (e["type"], norm(e["name"]))
        keep = by.get(key)
        if keep is None:
            by[key] = e
            out.append(e)
            continue
        a, b = sorted((keep.get("summary") or "", e.get("summary") or ""), key=len, reverse=True)
        keep["summary"] = a
        if b and b != a:
            src = (e.get("sources") or [""])[-1]
            keep["facts"].append({"text": b, "date": _date_of(src), "source": src})
        keep["facts"] += e.get("facts") or []
        keep["aliases"] = list(dict.fromkeys((keep.get("aliases") or []) + (e.get("aliases") or [])))
        keep["sources"] = sorted(set(keep.get("sources") or []) | set(e.get("sources") or []))
        if e["type"] == "commitment" and e.get("status") not in ("", "unknown"):
            keep["status"] = e["status"]
        _finish(keep)
    return out


# ---------- entries → Markdown ----------

def _md_line(e: dict) -> str:
    alias = f"（{'、'.join(e['aliases'])}）" if e.get("aliases") else ""
    extra = ""
    if e["type"] == "commitment":
        bits = [b for b in (e.get("owner"), e.get("due")) if b]
        st = t(f"memory.status.{e.get('status') or 'unknown'}")
        extra = f" · {st}" + (f" · {' · '.join(bits)}" if bits else "")
    return f"- **{e['name']}**{alias}{extra}：{e.get('summary', '')}".rstrip("：")


def to_markdown(ents: list[dict], lang: "str | None" = None, facts: bool = True) -> str:
    sec = sections(lang)
    out = [f"# {sec['longterm_title']}", "", f"<!-- {t('memory.md_note')} -->", ""]
    for kind in TYPES:
        group = [e for e in ents if e["type"] == kind]
        if not group:
            continue
        out += [f"## {sec[SECTION_OF[kind]]}", ""]
        for e in group:
            out.append(_md_line(e))
            if facts:
                for f in e.get("facts") or []:
                    src = f" [{f['source']}]" if f.get("source") else ""
                    when = f"{f['date']}：" if f.get("date") else ""
                    out.append(f"  - {when}{f['text']}{src}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def replace_from_markdown(md: str) -> int:
    """Manual edit of longterm.md (memory dialog): keep ids / created times where names match."""
    from .library import list_meetings
    known = {m.id for m in list_meetings()}
    with LOCK:
        data = load()
        old = {(e["type"], norm(e["name"])): e for e in data["entities"]}
        new = from_markdown(md, known)
        for e in new:
            prev = old.get((e["type"], norm(e["name"])))
            if prev:
                e["id"], e["created"] = prev["id"], prev.get("created", e["created"])
                if e.get("facts") == prev.get("facts") and e.get("summary") == prev.get("summary"):
                    e["updated"] = prev.get("updated", e["updated"])
        data["entities"] = new
        save(data)
        return len(new)


# ---------- lookup ----------

def _names(e: dict) -> list[str]:
    return [n for n in [e["name"], *e.get("aliases", [])] if n]


def find(kind: str, name: str, ents: "list[dict] | None" = None) -> "dict | None":
    ents = load()["entities"] if ents is None else ents
    key = norm(name)
    if not key:
        return None
    for e in ents:
        if e["type"] == kind and key in {norm(n) for n in _names(e)}:
            return e
    if kind == "commitment":  # no stable key: closest wording
        best, score = None, 0.0
        for e in ents:
            if e["type"] != "commitment":
                continue
            r = difflib.SequenceMatcher(None, key, norm(e["name"])).ratio()
            if r > score:
                best, score = e, r
        return best if score >= 0.72 else None
    return None


def _mentions(e: dict, low_text: str) -> int:
    """How often the entry's name or aliases occur: whole words for Latin script ('Nora' must not
    match 'ignorance'), plain substrings for Chinese; two-letter Latin names are too ambiguous."""
    n = 0
    for name in _names(e):
        k = name.strip().lower()
        if len(k) < 2:
            continue
        if re.search(r"[a-z0-9]", k) and not re.search(r"[\u3400-\u9fff]", k):
            if len(k) < 3 and k != "ai":
                continue
            n += len(re.findall(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", low_text))
        else:
            n += low_text.count(k)
    return n


def relevant(text: str, extra: "list[str] | None" = None, limit: int = 40) -> list[dict]:
    """Entries the text talks about, most mentioned first; open commitments of those people too."""
    low = (text + "\n" + "\n".join(extra or [])).lower()
    ents = load()["entities"]
    scored = [(_mentions(e, low), e) for e in ents]
    hits = [(s, e) for s, e in scored if s > 0]
    people = {norm(n) for s, e in hits if e["type"] == "person" for n in _names(e)}
    for e in ents:
        if e["type"] == "commitment" and e.get("status") in ("open", "unknown") and norm(e.get("owner", "")) in people:
            if all(e is not h for _, h in hits):
                hits.append((1, e))
    hits.sort(key=lambda x: (-x[0], x[1].get("updated", "")), reverse=False)
    return [e for _, e in hits[:limit]]


def context_block(text: str, extra: "list[str] | None" = None, budget: int = 6000,
                  lang: "str | None" = None, recent_facts: int = 2) -> str:
    """Compact Markdown of the relevant entries for a prompt, within ``budget`` characters."""
    picked = relevant(text, extra)
    if not picked:
        return ""
    sec = sections(lang)
    lines: list[str] = []
    used = 0
    for kind in TYPES:
        group = [e for e in picked if e["type"] == kind]
        if not group:
            continue
        head = f"## {sec[SECTION_OF[kind]]}"
        block = [head]
        for e in group:
            line = _md_line(e)
            facts = [f for f in (e.get("facts") or []) if f.get("text")][-recent_facts:]
            if facts:
                line += " " + "；".join(
                    f"{f['date'] + ' ' if f.get('date') else ''}{f['text'][:120]}" for f in facts)
            src = e.get("sources") or []
            if src:
                line += f" [{src[-1]}]"
            if used + len(line) > budget:
                break
            block.append(line)
            used += len(line)
        if len(block) > 1:
            lines += block + [""]
        if used >= budget:
            break
    return "\n".join(lines).strip()


def search(q: str, limit: int = 30) -> list[dict]:
    """Case-insensitive search over names, aliases, summaries and facts."""
    k = q.strip().lower()
    if not k:
        return []
    out = []
    for e in load()["entities"]:
        where, snippet = "", ""
        if any(k in n.lower() for n in _names(e)):
            where, snippet = "name", e.get("summary", "")
        elif k in (e.get("summary") or "").lower():
            where, snippet = "summary", e["summary"]
        else:
            for f in e.get("facts") or []:
                if k in f["text"].lower():
                    where, snippet = "fact", f["text"]
                    break
        if where:
            out.append({**brief(e), "where": where, "snippet": _around(snippet, k)})
    rank = {"name": 0, "summary": 1, "fact": 2}
    out.sort(key=lambda r: (rank[r["where"]], r.get("updated", "")), reverse=False)
    return out[:limit]


def _around(text: str, k: str, width: int = 90) -> str:
    i = text.lower().find(k)
    if i < 0 or len(text) <= width:
        return text[:width]
    a = max(0, i - width // 3)
    return ("…" if a else "") + text[a:a + width] + ("…" if a + width < len(text) else "")


def brief(e: dict) -> dict:
    return {"id": e["id"], "type": e["type"], "name": e["name"], "aliases": e.get("aliases", []),
            "summary": e.get("summary", ""), "status": e.get("status", ""), "owner": e.get("owner", ""),
            "due": e.get("due", ""), "facts": len(e.get("facts") or []), "sources": len(e.get("sources") or []),
            "updated": e.get("updated", ""), "last_source": (e.get("sources") or [""])[-1]}


def stats() -> dict:
    ents = load()["entities"]
    c = {k: sum(1 for e in ents if e["type"] == k) for k in TYPES}
    c["open"] = sum(1 for e in ents if e["type"] == "commitment" and e.get("status") == "open")
    return c


# ---------- edits ----------

def upsert(item: dict, source: str = "", date: str = "", data: "dict | None" = None) -> "dict | None":
    """Merge one entry from the model (or the UI) into the store; returns the stored entry."""
    kind = item.get("type")
    name = str(item.get("name") or "").strip()
    if kind not in TYPES or not name:
        return None
    own = data is None
    data = data or load()
    ents = data["entities"]
    e = find(kind, name, ents)
    for alias in item.get("aliases") or []:
        e = e or find(kind, alias, ents)
    stamp = now()
    if e is None:
        e = {"type": kind, "name": name[:80], "aliases": [], "summary": "", "facts": [],
             "id": _new_id(kind, name, {x["id"] for x in ents}), "created": stamp}
        ents.append(e)
    for alias in item.get("aliases") or []:
        a = str(alias).strip()
        if a and norm(a) not in {norm(n) for n in _names(e)}:
            e.setdefault("aliases", []).append(a[:40])
    if norm(name) not in {norm(n) for n in _names(e)}:
        e.setdefault("aliases", []).append(name[:40])
    if str(item.get("summary") or "").strip():
        e["summary"] = str(item["summary"]).strip()
    for f in item.get("facts") or []:
        text = str(f.get("text") if isinstance(f, dict) else f).strip()
        if not text:
            continue
        existing = [x["text"] for x in e.get("facts") or []]
        if any(difflib.SequenceMatcher(None, norm(text), norm(x)).ratio() >= 0.85 for x in existing[-30:]):
            continue
        e.setdefault("facts", []).append({"text": text, "date": (f.get("date") if isinstance(f, dict) else "") or date,
                                          "source": (f.get("source") if isinstance(f, dict) else "") or source})
    if kind == "commitment":
        st = str(item.get("status") or "").strip().lower()
        if st in STATUSES:
            e["status"] = st
        for k in ("owner", "due"):
            if str(item.get(k) or "").strip():
                e[k] = str(item[k]).strip()[:60]
    e["updated"] = stamp
    _finish(e)
    if own:
        save(data)
    return e


def update(eid: str, fields: dict) -> dict:
    with LOCK:
        data = load()
        e = next((x for x in data["entities"] if x["id"] == eid), None)
        if e is None:
            raise LookupError(t("memory.not_found"))
        if "name" in fields and str(fields["name"]).strip():
            e["name"] = str(fields["name"]).strip()[:80]
        if "aliases" in fields:
            e["aliases"] = [str(a).strip()[:40] for a in fields["aliases"] or [] if str(a).strip()]
        if "summary" in fields:
            e["summary"] = str(fields["summary"] or "").strip()
        if "facts" in fields:
            e["facts"] = [{"text": str(f.get("text", "")).strip(), "date": str(f.get("date", "") or ""),
                           "source": str(f.get("source", "") or "")}
                          for f in fields["facts"] or [] if str(f.get("text", "")).strip()]
        if e["type"] == "commitment":
            if fields.get("status") in STATUSES:
                e["status"] = fields["status"]
            for k in ("owner", "due"):
                if k in fields:
                    e[k] = str(fields[k] or "").strip()[:60]
        if fields.get("type") in TYPES:
            e["type"] = fields["type"]
        e["updated"] = now()
        _finish(e)
        save(data)
        return e


def create(fields: dict) -> dict:
    with LOCK:
        if fields.get("type") not in TYPES or not str(fields.get("name") or "").strip():
            raise ValueError(t("memory.need_name"))
        e = upsert({k: v for k, v in fields.items() if k != "facts"})
        if fields.get("facts"):
            e = update(e["id"], {"facts": fields["facts"]})
        return e


def delete(eid: str) -> None:
    with LOCK:
        data = load()
        n = len(data["entities"])
        data["entities"] = [e for e in data["entities"] if e["id"] != eid]
        if len(data["entities"]) == n:
            raise LookupError(t("memory.not_found"))
        _backup()
        save(data)


def merge(eid: str, into: str) -> dict:
    """Fold a duplicate entry into another: names become aliases, facts are combined by date."""
    with LOCK:
        data = load()
        a = next((e for e in data["entities"] if e["id"] == eid), None)
        b = next((e for e in data["entities"] if e["id"] == into), None)
        if a is None or b is None or a is b:
            raise LookupError(t("memory.not_found"))
        for n in _names(a):
            if norm(n) not in {norm(x) for x in _names(b)}:
                b.setdefault("aliases", []).append(n)
        if a.get("summary") and a["summary"] not in (b.get("summary") or ""):
            b["summary"] = (b.get("summary") or a["summary"]) if b.get("summary") else a["summary"]
            if b["summary"] != a["summary"]:
                b.setdefault("facts", []).append({"text": a["summary"], "date": "", "source": ""})
        b["facts"] = sorted((b.get("facts") or []) + (a.get("facts") or []), key=lambda f: f.get("date") or "")
        b["updated"] = now()
        _finish(b)
        data["entities"] = [e for e in data["entities"] if e is not a]
        _backup()
        save(data)
        return b


def clear() -> None:
    with LOCK:
        _backup()
        save(_empty())


def _backup() -> None:
    if KNOWLEDGE_FILE.exists():
        KNOWLEDGE_FILE.with_name("knowledge.bak.json").write_text(
            KNOWLEDGE_FILE.read_text(encoding="utf-8"), encoding="utf-8")


def heading_lang() -> "str | None":
    return detect_headings_lang(LONGTERM_FILE.read_text(encoding="utf-8")) if LONGTERM_FILE.exists() else None


# ---------- model output ----------

def parse_model_json(out: str) -> dict:
    """Lenient: strip fences / prose around the JSON object the merge prompt asks for."""
    s = (out or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    if not s.startswith("{"):
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("no JSON object")
        s = s[a:b + 1]
    data = json.loads(s)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def apply_model_output(data: dict, source: str, date: str) -> int:
    """Apply {"entities": [...]} from the merge prompt; returns how many entries changed."""
    n = 0
    with LOCK:
        store = load()
        for item in data.get("entities") or []:
            if isinstance(item, dict) and upsert(item, source=source, date=date, data=store) is not None:
                n += 1
        if n:
            _backup()
            save(store)
    return n


def candidates_json(text: str, extra: "list[str] | None" = None, limit: int = 40) -> str:
    """Existing entries the merge model should update rather than duplicate."""
    picked = relevant(text, extra, limit=limit)
    rows = []
    for e in picked:
        row = {"type": e["type"], "name": e["name"]}
        if e.get("aliases"):
            row["aliases"] = e["aliases"][:6]
        if e.get("summary"):
            row["summary"] = e["summary"][:240]
        facts = [f["text"][:120] for f in (e.get("facts") or [])][-3:]
        if facts:
            row["recent_facts"] = facts
        if e["type"] == "commitment":
            row.update(status=e.get("status", "unknown"), owner=e.get("owner", ""))
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=0)


def name_index(limit: int = 400) -> str:
    """'person: 林炜, 张三 …' – every known name, so the model reuses spellings."""
    out = []
    for kind in ("person", "project", "term"):
        names = [e["name"] for e in entities(kind)][:limit]
        if names:
            out.append(f"{kind}: " + ", ".join(names))
    return "\n".join(out)


def tidy_targets(min_facts: int = 12) -> list[dict]:
    return [e for e in entities() if len(e.get("facts") or []) >= min_facts or len(e.get("summary") or "") > 400]


def path() -> Path:
    return KNOWLEDGE_FILE
