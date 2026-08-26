"""i18n completeness check: every key used in Python / the web UI must exist in every locale.

Run: .venv/bin/python tests/check_i18n.py   (also executed by tests/smoke.py)
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "coco"
LOCALES = sorted((PKG / "locales").glob("*.json"))

# keys whose last segment is built at runtime: prefix -> list of suffixes that must exist
DYNAMIC = {
    "msg.source.": ["upload", "import", "record", "text", "paste", "watch"],
    "msg.files.": ["transcript", "brief", "weekly", "tracking", "prep", "prep_sources", "global",
                   "export", "memory", "longterm", "glossary"],
    "ui.memory.hint_": ["content", "longterm", "glossary"],
    "ui.memory.label_": ["content", "longterm", "glossary"],
    "ui.settings.task_": ["report", "ask", "track", "prep", "brief", "weekly", "memory", "glossary", "namefix"],
    "ui.settings.preset_": ["claude_cli", "claude_cli_fast", "deepseek_anthropic", "deepseek_openai",
                            "deepseek_via_claude_cli", "openai", "ollama", "custom"],
    "ui.audiohelp.body_": ["windows", "mac", "linux"],
}
TEMPLATE_IDS = ["minutes", "actions", "mood", "tension", "bias", "topics", "client", "hiring", "followup"]
TRACK_IDS = ["track", "signals", "synthesis"]
SECTION_KEYS = ["memory_title", "longterm_title", "people", "projects", "commitments", "terms",
                "glossary_title", "names", "glossary_terms", "misheard", "transcript"]


def collect_keys() -> set[str]:
    keys = set()
    for p in PKG.rglob("*.py"):
        for m in re.finditer(r"""\bt\(\s*["']([a-z_]+(?:\.[a-z_]+)+)["']""", p.read_text(encoding="utf-8")):
            k = m.group(1)
            keys.add(k if k.split(".")[0] in ("ui", "msg") else "msg." + k)
    html = (PKG / "static" / "index.html").read_text(encoding="utf-8")
    for m in re.finditer(r"""\bt\(\s*['"]([a-z_]+(?:\.[a-z_]+)+)['"]""", html):
        keys.add("ui." + m.group(1))
    for m in re.finditer(r"""data-i18n(?:-html|-title|-placeholder)?="([^"]+)\"""", html):
        keys.add("ui." + m.group(1))
    keys = {k for k in keys if not k.endswith(("_", "."))}  # dynamic prefixes are covered below
    for prefix, suffixes in DYNAMIC.items():
        for s in suffixes:
            keys.add(prefix + s)
    for tid in TEMPLATE_IDS:
        keys.update({f"templates.{tid}.name", f"templates.{tid}.desc"})
    for tid in TRACK_IDS:
        keys.update({f"track_modes.{tid}.name", f"track_modes.{tid}.desc"})
    for s in SECTION_KEYS:
        keys.add("sections." + s)
    return keys


def lookup(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def placeholders(s: str) -> set[str]:
    return set(re.findall(r"\{([a-z_]+)\}", s))


def main() -> int:
    keys = collect_keys()
    data = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in LOCALES}
    problems = []
    for code, d in data.items():
        for k in sorted(keys):
            v = lookup(d, k)
            if not isinstance(v, str):
                problems.append(f"{code}: missing {k}")
    # placeholders must match across locales (a {name} missing in one translation breaks formatting)
    ref = data.get("en", {})
    for code, d in data.items():
        if code == "en":
            continue
        for k in sorted(keys):
            a, b = lookup(ref, k), lookup(d, k)
            if isinstance(a, str) and isinstance(b, str) and placeholders(a) != placeholders(b):
                problems.append(f"{code}: placeholders differ for {k}: en={sorted(placeholders(a))} {code}={sorted(placeholders(b))}")
    for pr in problems:
        print("✗", pr)
    print(f"i18n: {len(keys)} keys × {len(data)} locales — {'OK' if not problems else str(len(problems)) + ' problems'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
