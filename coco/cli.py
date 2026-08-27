"""coco command-line entry point."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import ai, i18n, providers
from .config import GLOSSARY_FILE, MEMORY_FILE, ensure_dirs, load_config, save_config
from .i18n import t
from .library import (AUDIO_EXTS, create_meeting, delete_meeting, find_meeting, list_meetings,
                      search_library)
from .recorder import list_devices, record_blocking, record_supported, record_unsupported_hint
from .templates import (all_template_names, all_track_mode_names, list_templates,
                        list_track_modes, resolve_template, resolve_track_mode)
from .transcriber import detect_backend, no_engine_hint, transcribe_meeting


def _p(msg: str) -> None:
    print(msg, flush=True)


def _need_transcriber() -> None:
    if not detect_backend():
        raise RuntimeError(no_engine_hint())


# ---------- commands ----------

def cmd_record(args):
    if not record_supported():
        raise RuntimeError(record_unsupported_hint())
    _need_transcriber()
    title = args.title or t("record.default_title", time=dt.datetime.now().strftime('%H%M'))
    mtg = create_meeting(title, source="record")
    out = mtg.path / "audio.wav"
    _p(t("cli.new_meeting", id=mtg.id))
    record_blocking(out, device=args.device)
    _p(t("cli.recording_saved"))
    transcribe_meeting(mtg, model=args.model, progress=_p)
    _p(t("cli.transcribed", path=mtg.transcript_md))


def cmd_transcribe(args):
    _need_transcriber()
    for f in args.files:
        src = Path(f).expanduser()
        if not src.exists():
            _p(t("cli.file_missing", path=src))
            continue
        title = args.title or src.stem
        mtg = create_meeting(title, audio_path=src, source="import", move=args.move)
        _p(t("cli.imported", id=mtg.id))
        transcribe_meeting(mtg, model=args.model, progress=_p)
        _p(t("cli.transcribed", path=mtg.transcript_md))


def cmd_list(args):
    meetings = list_meetings()
    if not meetings:
        _p(t("cli.library_empty"))
        return
    for m in meetings:
        s = m.summary()
        mark = {"done": "✓", "transcribing": "…", "error": "✗"}.get(s["status"], "·")
        reports = f"  [{','.join(s['reports'])}]" if s["reports"] else ""
        _p(f"{mark} {s['id']}  {s['duration'] or '--'}{reports}")


def cmd_show(args):
    mtg = find_meeting(args.key)
    text = mtg.transcript_text()
    _p(text if text else t("cli.no_transcript_yet", id=mtg.id, status=mtg.meta.get("status")))


def cmd_ask(args):
    if args.refs:
        meetings = [find_meeting(r) for r in args.refs]
    else:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()][:1]
        if not meetings:
            _p(t("cli.no_transcribed"))
            return
        _p(t("cli.default_ref", id=meetings[0].id))
    _p(t("cli.thinking"))
    print()
    ai.ask(args.question, meetings, on_delta=lambda s: print(s, end="", flush=True))
    print()


def cmd_report(args):
    mtg = find_meeting(args.key)
    tid = resolve_template(args.template)
    if not tid:
        raise RuntimeError(t("ai.unknown_template", name=args.template))
    _p(t("cli.generating_report", template=args.template, id=mtg.id))
    print()
    path, _ = ai.generate_report(mtg, tid, on_delta=lambda s: print(s, end="", flush=True))
    print()
    _p(t("cli.saved", path=path))


def cmd_templates(args):
    for tp in list_templates():
        _p(f"{tp['id']:<10} {tp['name']:<14} {tp['desc']}")
    _p("")
    for md in list_track_modes():
        _p(f"{md['id']:<10} {md['name']:<14} {md['desc']}")


def cmd_brief(args):
    date = args.date or dt.date.today().isoformat()
    _p(t("cli.generating_brief", date=date))
    print()
    path, _ = ai.daily_brief(date, on_delta=lambda s: print(s, end="", flush=True))
    print()
    _p(t("cli.saved", path=path))
    if load_config().get("auto_memory", True):
        try:
            _p(t("cli.merging_brief"))
            ai.memorize_brief(Path(path), force=True)
            _p(t("cli.merged"))
        except ai.AIError as e:
            _p(t("cli.merge_failed", detail=e))


def cmd_weekly(args):
    start, end, week = ai.week_bounds(args.date)
    _p(t("cli.generating_weekly", week=week, start=start, end=end))
    print()
    path, _ = ai.weekly_brief(args.date, on_delta=lambda s: print(s, end="", flush=True))
    print()
    _p(t("cli.saved", path=path))


def cmd_memory(args):
    ensure_dirs()
    if args.add:
        with MEMORY_FILE.open("a", encoding="utf-8") as f:
            f.write(f"- {args.add}\n")
        _p(t("cli.memory_added", text=args.add))
    else:
        _p(MEMORY_FILE.read_text(encoding="utf-8"))
        _p(t("cli.edit_file_hint", path=MEMORY_FILE))


def cmd_delete(args):
    mtg = find_meeting(args.key)
    if not args.yes:
        ans = input(t("cli.delete_confirm", id=mtg.id))
        if ans.strip().lower() not in ("y", "yes", "o", "oui", "是"):
            _p(t("cli.cancelled"))
            return
    dest = delete_meeting(mtg)
    _p(t("cli.moved_to_trash", path=dest))


def cmd_search(args):
    results = search_library(args.query)
    if not results:
        _p(t("cli.no_match", query=args.query))
        return
    for r in results:
        _p(f"\n● {r['id']}")
        for m in r["matches"]:
            _p(f"  [{m['where']}] {m['line']}")


def cmd_track(args):
    focus = args.focus or ""
    refs = [r.strip() for r in (args.refs or "").replace("，", ",").split(",") if r.strip()]
    meetings = [find_meeting(r) for r in refs] if refs else None
    mode = resolve_track_mode(args.mode)
    if not mode:
        raise RuntimeError(t("ai.unknown_mode", name=args.mode))
    _p(t("cli.tracking", mode=args.mode, focus=focus or t("files.global"),
         scope=t("cli.limited_to", n=len(meetings)) if meetings else ""))
    print()
    path, _ = ai.track(focus, mode=mode, meetings=meetings, on_delta=lambda s: print(s, end="", flush=True))
    print()
    _p(t("cli.saved", path=path))


def cmd_prep(args):
    _p(t("cli.prepping_web" if args.web else "cli.prepping", topic=args.topic))
    print()
    path, _ = ai.prep(args.topic, people=args.who or "", goal=args.goal or "", use_web=args.web,
                      on_delta=lambda s: print(s, end="", flush=True))
    print()
    _p(t("cli.saved", path=path))


def cmd_glossary(args):
    ensure_dirs()
    if args.extract:
        _p(t("cli.extracting_glossary"))
        ai.extract_glossary(progress=_p)
        _p(t("cli.glossary_merged", path=GLOSSARY_FILE))
        return
    if args.add:
        old = GLOSSARY_FILE.read_text(encoding="utf-8") if GLOSSARY_FILE.exists() else ""
        with GLOSSARY_FILE.open("a", encoding="utf-8") as f:
            f.write(("" if not old or old.endswith("\n") else "\n") + f"- {args.add}\n")
        _p(t("cli.glossary_added", text=args.add))
    else:
        _p(GLOSSARY_FILE.read_text(encoding="utf-8"))
        _p(t("cli.edit_file_hint", path=GLOSSARY_FILE))


def cmd_import(args):
    """Import existing text material (no transcription)."""
    from .ingest import TEXT_EXTS, import_transcript_file
    paths = []
    for f in args.files:
        p = Path(f).expanduser()
        if p.is_dir():
            entries = [x for x in sorted(p.iterdir()) if not x.name.startswith(".")]
            paths += [x for x in entries if x.suffix.lower() in TEXT_EXTS]
            audio = [x for x in entries if x.suffix.lower() in AUDIO_EXTS]
            if audio:
                _p(t("cli.skipped_audio", n=len(audio)))
        elif p.exists():
            paths.append(p)
        else:
            _p(t("cli.file_missing", path=p))
    for p in paths:
        if p.suffix.lower() not in TEXT_EXTS:
            _p(t("cli.skipped_not_text", name=p.name))
            continue
        try:
            mtg = import_transcript_file(p, title=args.title if len(paths) == 1 else None)
        except Exception as e:
            _p(t("cli.import_failed", name=p.name, detail=e))
            continue
        _p(t("cli.imported", id=mtg.id))
        if load_config().get("auto_memory", True):
            try:
                ai.update_longterm(mtg)
                _p("  " + t("cli.merged"))
            except ai.AIError as e:
                _p("  " + t("cli.merge_failed", detail=e))


def cmd_memorize(args):
    if args.compact:
        _p(t("ai.compacting"))
        ai.compact_longterm(progress=_p)
        _p(t("cli.compacted"))
        return
    if args.all:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()]
        meetings.reverse()  # chronological
    else:
        if not args.key:
            _p(t("cli.memorize_usage"))
            sys.exit(1)
        meetings = [find_meeting(args.key)]
    for m in meetings:
        if m.meta.get("memorized_at") and not args.force:
            _p(t("cli.skip_memorized", id=m.id))
            continue
        _p(t("cli.memorizing", id=m.id))
        try:
            out = ai.update_longterm(m, force=args.force)
            _p(t("cli.merged") if out else t("cli.skip_short"))
        except ai.AIError as e:
            _p(f"✗ {e}")
    if args.all:
        from .config import BRIEFS_DIR
        for b in sorted(BRIEFS_DIR.glob("*.md")):
            _p(t("cli.merging_brief_named", name=b.stem))
            try:
                out = ai.memorize_brief(b, force=args.force)
                _p(t("cli.merged") if out else t("cli.skip_merged"))
            except ai.AIError as e:
                _p(f"✗ {e}")


def cmd_watch(args):
    _need_transcriber()
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        _p(t("cli.not_a_folder", path=folder))
        sys.exit(1)
    state_file = folder / ".coco_seen.json"
    seen = set(json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else [])
    if not state_file.exists():
        seen = {f.name for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXTS}
        state_file.write_text(json.dumps(sorted(seen)))
        _p(t("cli.watch_first", n=len(seen)))
    _p(t("cli.watching", path=folder))
    try:
        while True:
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() not in AUDIO_EXTS or f.name in seen:
                    continue
                size = f.stat().st_size
                time.sleep(2)
                if f.stat().st_size != size:
                    continue
                seen.add(f.name)
                state_file.write_text(json.dumps(sorted(seen)))
                _p(t("cli.new_file", name=f.name))
                try:
                    mtg = create_meeting(f.stem, audio_path=f, source=f"watch:{folder.name}")
                    transcribe_meeting(mtg, model=args.model, progress=_p)
                    _p(f"✓ {mtg.transcript_md}")
                except Exception as e:
                    _p(t("cli.process_failed", detail=e))
            time.sleep(15)
    except KeyboardInterrupt:
        _p("\n" + t("cli.watch_stopped"))


def cmd_clean(args):
    """Preview / remove hallucinated lines in existing transcripts."""
    from .library import clean_meeting
    if args.all:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    elif args.key:
        meetings = [find_meeting(args.key)]
    else:
        _p(t("cli.clean_usage"))
        sys.exit(1)
    total = 0
    for m in meetings:
        r = clean_meeting(m, apply=args.apply)
        if not r["count"]:
            continue
        total += r["count"]
        _p(t("cli.clean_meeting", id=m.id, n=r["count"]))
        for l in r["removed"][:args.show]:
            _p("   " + l[:110])
        if r["count"] > args.show:
            _p(f"   … +{r['count'] - args.show}")
    _p(t("cli.clean_applied" if args.apply else "cli.clean_preview", n=total))


def cmd_devices(args):
    _p(list_devices())
    _p("\n" + t("cli.devices_hint"))


def _port_listening(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_coco(url: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url}/api/config", timeout=3) as r:
            return "whisper_model" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def cmd_web(args):
    import threading
    import webbrowser
    port = args.port or load_config()["port"]
    url = f"http://127.0.0.1:{port}"
    if _port_listening(port):
        if not _is_coco(url):
            raise RuntimeError(t("cli.port_taken", port=port))
        _p(t("cli.already_running", url=url))
        if args.open:
            webbrowser.open(url)
        return
    import uvicorn
    from .server import app
    backend = detect_backend()
    _p(f"🌐 coco → {url}")
    _p("   " + t("cli.engine_line", engine=backend or t("cli.engine_none")))
    if args.open:
        def _open_when_ready():
            for _ in range(120):
                if _port_listening(port):
                    webbrowser.open(url)
                    return
                time.sleep(0.5)
        threading.Thread(target=_open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_config(args):
    cfg = load_config()
    if args.key and args.value is not None:
        old = cfg.get(args.key)
        val = args.value
        if isinstance(old, bool):
            val = val.lower() in ("1", "true", "yes", "on")
        elif isinstance(old, int):
            val = int(val)
        elif isinstance(old, (list, dict)):
            val = json.loads(val)
        cfg[args.key] = val
        save_config(cfg)
        _p(f"✓ {args.key} = {json.dumps(val, ensure_ascii=False)}")
    else:
        _p(json.dumps(cfg, ensure_ascii=False, indent=2))


def cmd_lang(args):
    cfg = load_config()
    if args.code:
        code = "auto" if args.code == "auto" else i18n.normalize(args.code)
        if not code:
            raise RuntimeError(t("server.bad_ui_language",
                                 codes=", ".join(l["code"] for l in i18n.available())))
        cfg["ui_language"] = code
        if args.output:
            cfg["output_language"] = args.output
        save_config(cfg)
        i18n.set_lang(None if code == "auto" else code)
        _p(t("cli.lang_set", code=code, output=cfg.get("output_language", "ui")))
    else:
        _p(t("cli.lang_current", ui=cfg.get("ui_language", "auto"), effective=i18n.get_lang(),
             output=cfg.get("output_language", "ui")))
        for l in i18n.available():
            _p(f"  {l['code']:<6} {l['name']}")


def cmd_ai(args):
    cfg = load_config()
    if args.action == "show":
        for name in providers.PROFILE_NAMES:
            p = providers.public_profile(providers.get_profile(name, cfg))
            _p(f"[{name}] " + json.dumps(p, ensure_ascii=False))
        tasks = dict(providers.TASK_PROFILE_DEFAULTS)
        tasks.update(cfg.get("ai_tasks") or {})
        _p(t("cli.ai_tasks") + " " + json.dumps(tasks, ensure_ascii=False))
        _p(t("cli.ai_presets") + " " + ", ".join(providers.PRESETS))
    elif args.action == "test":
        _p(t("cli.ai_testing", profile=args.profile))
        r = providers.test_profile(args.profile)
        if r["ok"]:
            _p(t("cli.ai_test_ok", model=r["model"], ms=r["latency_ms"], reply=r["reply"]))
        else:
            raise RuntimeError(t("cli.ai_test_failed", detail=r["error"]))
    elif args.action == "preset":
        if not args.name or args.name not in providers.PRESETS:
            raise RuntimeError(t("cli.ai_unknown_preset", names=", ".join(providers.PRESETS)))
        profiles = dict(cfg.get("ai_profiles") or {})
        old = profiles.get(args.profile) or {}
        new = dict(providers.PRESETS[args.name])
        if old.get("api_key"):
            new["api_key"] = old["api_key"]
        if args.value:  # optional API key literal
            new["api_key"] = args.value
        profiles[args.profile] = new
        cfg["ai_profiles"] = profiles
        save_config(cfg)
        _p(t("cli.ai_preset_applied", name=args.name, profile=args.profile))
    elif args.action == "set":
        if not args.name or args.value is None:
            raise RuntimeError(t("cli.ai_set_usage"))
        if args.name not in providers.PROFILE_DEFAULTS:
            raise RuntimeError(t("cli.ai_unknown_key", keys=", ".join(providers.PROFILE_DEFAULTS)))
        profiles = dict(cfg.get("ai_profiles") or {})
        prof = dict(profiles.get(args.profile) or {})
        default = providers.PROFILE_DEFAULTS[args.name]
        val = args.value
        if isinstance(default, int):
            val = int(val)
        elif isinstance(default, list):
            val = json.loads(val)
        prof[args.name] = val
        profiles[args.profile] = prof
        cfg["ai_profiles"] = profiles
        save_config(cfg)
        _p(f"✓ [{args.profile}] {args.name} = {val if args.name != 'api_key' else '••••'}")
    elif args.action == "task":
        if not args.name or args.value not in providers.PROFILE_NAMES:
            raise RuntimeError(t("cli.ai_task_usage", tasks=", ".join(providers.TASK_PROFILE_DEFAULTS)))
        tasks = dict(cfg.get("ai_tasks") or {})
        tasks[args.name] = args.value
        cfg["ai_tasks"] = tasks
        save_config(cfg)
        _p(f"✓ {args.name} → {args.value}")


# ---------- entry ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coco", description=t("cli.description"))
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("record", help=t("cli.h_record"))
    p.add_argument("title", nargs="?", help=t("cli.h_title"))
    p.add_argument("--model", help=t("cli.h_model"))
    p.add_argument("--device", help=t("cli.h_device"))
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("transcribe", help=t("cli.h_transcribe"))
    p.add_argument("files", nargs="+")
    p.add_argument("--title", help=t("cli.h_title_default"))
    p.add_argument("--model", help=t("cli.h_model"))
    p.add_argument("--move", action="store_true", help=t("cli.h_move"))
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("list", help=t("cli.h_list"))
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help=t("cli.h_show"))
    p.add_argument("key", help=t("cli.h_key"))
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("ask", help=t("cli.h_ask"))
    p.add_argument("question")
    p.add_argument("refs", nargs="*", help=t("cli.h_refs"))
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("report", help=t("cli.h_report"))
    p.add_argument("key", help=t("cli.h_key"))
    p.add_argument("-t", "--template", default="minutes",
                   help=t("cli.h_template", names=", ".join(all_template_names()[:9])))
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("templates", help=t("cli.h_templates"))
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("brief", help=t("cli.h_brief"))
    p.add_argument("date", nargs="?", help=t("cli.h_date"))
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("weekly", help=t("cli.h_weekly"))
    p.add_argument("date", nargs="?", help=t("cli.h_week_date"))
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("memory", help=t("cli.h_memory"))
    p.add_argument("add", nargs="?", help=t("cli.h_memory_add"))
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("delete", help=t("cli.h_delete"))
    p.add_argument("key", help=t("cli.h_key"))
    p.add_argument("-y", "--yes", action="store_true", help=t("cli.h_yes"))
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("search", help=t("cli.h_search"))
    p.add_argument("query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("track", help=t("cli.h_track"))
    p.add_argument("focus", nargs="?", help=t("cli.h_focus"))
    p.add_argument("--mode", default="track", help=t("cli.h_mode", names=", ".join(all_track_mode_names()[:3])))
    p.add_argument("--refs", help=t("cli.h_track_refs"))
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("prep", help=t("cli.h_prep"))
    p.add_argument("topic", help=t("cli.h_topic"))
    p.add_argument("--who", help=t("cli.h_who"))
    p.add_argument("--goal", help=t("cli.h_goal"))
    p.add_argument("--web", action="store_true", help=t("cli.h_web"))
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("glossary", help=t("cli.h_glossary"))
    p.add_argument("add", nargs="?", help=t("cli.h_glossary_add"))
    p.add_argument("--extract", action="store_true", help=t("cli.h_glossary_extract"))
    p.set_defaults(func=cmd_glossary)

    p = sub.add_parser("import", help=t("cli.h_import"))
    p.add_argument("files", nargs="+", help=t("cli.h_import_files"))
    p.add_argument("--title", help=t("cli.h_title_single"))
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("memorize", help=t("cli.h_memorize"))
    p.add_argument("key", nargs="?", help=t("cli.h_key"))
    p.add_argument("--all", action="store_true", help=t("cli.h_memorize_all"))
    p.add_argument("--force", action="store_true", help=t("cli.h_memorize_force"))
    p.add_argument("--compact", action="store_true", help=t("cli.h_memorize_compact"))
    p.set_defaults(func=cmd_memorize)

    p = sub.add_parser("watch", help=t("cli.h_watch"))
    p.add_argument("folder")
    p.add_argument("--model", help=t("cli.h_model"))
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("clean", help=t("cli.h_clean"))
    p.add_argument("key", nargs="?", help=t("cli.h_key"))
    p.add_argument("--all", action="store_true", help=t("cli.h_clean_all"))
    p.add_argument("--apply", action="store_true", help=t("cli.h_clean_apply"))
    p.add_argument("--show", type=int, default=6, help=t("cli.h_clean_show"))
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("devices", help=t("cli.h_devices"))
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("web", help=t("cli.h_web_ui"))
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help=t("cli.h_open"))
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("config", help=t("cli.h_config"))
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("lang", help=t("cli.h_lang"))
    p.add_argument("code", nargs="?", help=t("cli.h_lang_code"))
    p.add_argument("--output", help=t("cli.h_lang_output"))
    p.set_defaults(func=cmd_lang)

    p = sub.add_parser("ai", help=t("cli.h_ai"))
    p.add_argument("action", choices=["show", "test", "preset", "set", "task"], help=t("cli.h_ai_action"))
    p.add_argument("name", nargs="?", help=t("cli.h_ai_name"))
    p.add_argument("value", nargs="?", help=t("cli.h_ai_value"))
    p.add_argument("--profile", default="primary", choices=list(providers.PROFILE_NAMES), help=t("cli.h_ai_profile"))
    p.set_defaults(func=cmd_ai)
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows redirects default to GBK
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    i18n.set_lang(i18n.configured_lang() or i18n.system_lang())
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return
    ensure_dirs()
    try:
        args.func(args)
    except (LookupError, RuntimeError, FileNotFoundError, ai.AIError) as e:
        _p(f"✗ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
