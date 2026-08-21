"""coco 命令行入口。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import ai
from .config import GLOSSARY_FILE, MEMORY_FILE, ensure_dirs, load_config, save_config
from .library import (AUDIO_EXTS, create_meeting, delete_meeting,
                      find_meeting, list_meetings, search_library)
from .recorder import list_devices, record_blocking
from .templates import TEMPLATES, TRACK_MODES
from .transcriber import transcribe_meeting


def _p(msg: str) -> None:
    print(msg, flush=True)


# ---------- 子命令 ----------

def cmd_record(args):
    title = args.title or f"录音-{dt.datetime.now().strftime('%H%M')}"
    mtg = create_meeting(title, source="录音")
    out = mtg.path / "audio.wav"
    _p(f"▶ 新会议：{mtg.id}")
    record_blocking(out, device=args.device)
    _p("✓ 录音已保存，开始转写…")
    transcribe_meeting(mtg, model=args.model, progress=_p)
    _p(f"✓ 转写完成 → {mtg.transcript_md}")


def cmd_transcribe(args):
    for f in args.files:
        src = Path(f).expanduser()
        if not src.exists():
            _p(f"✗ 文件不存在：{src}")
            continue
        title = args.title or src.stem
        mtg = create_meeting(title, audio_path=src, source="导入", move=args.move)
        _p(f"▶ 已导入：{mtg.id}")
        transcribe_meeting(mtg, model=args.model, progress=_p)
        _p(f"✓ 转写完成 → {mtg.transcript_md}")


def cmd_list(args):
    meetings = list_meetings()
    if not meetings:
        _p("会议库为空。coco transcribe <音频> 导入，或在网页版拖拽上传。")
        return
    for m in meetings:
        s = m.summary()
        mark = {"done": "✓", "transcribing": "…", "error": "✗"}.get(s["status"], "·")
        reports = f"  报告[{','.join(s['reports'])}]" if s["reports"] else ""
        _p(f"{mark} {s['id']}  {s['duration'] or '--'}{reports}")


def cmd_show(args):
    mtg = find_meeting(args.key)
    text = mtg.transcript_text()
    _p(text if text else f"{mtg.id} 还没有转写内容（状态：{mtg.meta.get('status')}）")


def cmd_ask(args):
    if args.refs:
        meetings = [find_meeting(r) for r in args.refs]
    else:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()][:1]
        if not meetings:
            _p("会议库中没有已转写的会议。")
            return
        _p(f"（未指定会议，默认引用最近一条：{meetings[0].id}）")
    _p("🤔 分析中…")
    _p("\n" + ai.ask(args.question, meetings))


def cmd_report(args):
    mtg = find_meeting(args.key)
    _p(f"▶ 生成「{args.template}」报告：{mtg.id} …")
    path, content = ai.generate_report(mtg, args.template)
    _p("\n" + content)
    _p(f"\n✓ 已保存 → {path}")


def cmd_templates(args):
    for name, t in TEMPLATES.items():
        _p(f"{name:　<6} {t['desc']}")


def cmd_brief(args):
    date = args.date or dt.date.today().isoformat()
    _p(f"▶ 生成 {date} 每日简报…")
    path, content = ai.daily_brief(date)
    _p("\n" + content)
    _p(f"\n✓ 已保存 → {path}")
    if load_config().get("auto_memory", True):
        try:
            _p("▶ 简报并入长期记忆…")
            ai.memorize_brief(Path(path), force=True)
            _p("✓ 已合并")
        except ai.AIError as e:
            _p(f"✗ 记忆合并失败（简报本身已保存）：{e}")


def cmd_memory(args):
    ensure_dirs()
    if args.add:
        with MEMORY_FILE.open("a", encoding="utf-8") as f:
            f.write(f"- {args.add}\n")
        _p(f"✓ 已记入全局记忆：{args.add}")
    else:
        _p(MEMORY_FILE.read_text(encoding="utf-8"))
        _p(f"（编辑该文件即可维护记忆：{MEMORY_FILE}）")


def cmd_delete(args):
    mtg = find_meeting(args.key)
    if not args.yes:
        ans = input(f"删除「{mtg.id}」？移入回收站 library/_trash [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            _p("已取消")
            return
    dest = delete_meeting(mtg)
    _p(f"✓ 已移入回收站：{dest}")


def cmd_search(args):
    results = search_library(args.query)
    if not results:
        _p(f"没有匹配「{args.query}」的内容")
        return
    for r in results:
        _p(f"\n● {r['id']}")
        for m in r["matches"]:
            _p(f"  [{m['where']}] {m['line']}")


def cmd_track(args):
    focus = args.focus or ""
    # --refs 用逗号分隔的单参数：nargs='*' 会把跟在后面的 focus 位置参数也吞进去
    refs = [r.strip() for r in (args.refs or "").replace("，", ",").split(",")
            if r.strip()]
    meetings = [find_meeting(r) for r in refs] if refs else None
    _p(f"▶ 跨会议洞察 · {args.mode}（{focus or '全局'}）"
       f"{f'，限定 {len(meetings)} 场' if meetings else ''}…")
    path, content = ai.track(focus, mode=args.mode, meetings=meetings)
    _p("\n" + content)
    _p(f"\n✓ 已保存 → {path}")


def cmd_prep(args):
    _p(f"▶ 会前调查：{args.topic}"
       + ("（含联网公开信息，可能需要几分钟）…" if args.web else "…"))
    path, content = ai.prep(args.topic, people=args.who or "",
                            goal=args.goal or "", use_web=args.web)
    _p("\n" + content)
    _p(f"\n✓ 已保存 → {path}")


def cmd_glossary(args):
    ensure_dirs()
    if args.extract:
        _p("▶ 从长期记忆与最近转写中提炼词表…")
        ai.extract_glossary(progress=_p)
        _p(f"✓ 已合并 → {GLOSSARY_FILE}（原文备份 .bak.md）")
        return
    if args.add:
        old = GLOSSARY_FILE.read_text(encoding="utf-8") if GLOSSARY_FILE.exists() else ""
        with GLOSSARY_FILE.open("a", encoding="utf-8") as f:
            # 文件若无结尾换行，先补一个，避免新词条接到上一行尾部
            f.write(("" if not old or old.endswith("\n") else "\n") + f"- {args.add}\n")
        _p(f"✓ 已记入词表：{args.add}")
    else:
        _p(GLOSSARY_FILE.read_text(encoding="utf-8"))
        _p(f"（编辑该文件即可维护词表：{GLOSSARY_FILE}）")


def cmd_import(args):
    """导入已有文字材料（txt/md/srt/vtt/json），不转写直接入库。"""
    from .ingest import TEXT_EXTS, import_transcript_file
    paths = []
    for f in args.files:
        p = Path(f).expanduser()
        if p.is_dir():
            entries = [x for x in sorted(p.iterdir()) if not x.name.startswith(".")]
            paths += [x for x in entries if x.suffix.lower() in TEXT_EXTS]
            audio = [x for x in entries if x.suffix.lower() in AUDIO_EXTS]
            if audio:  # 静默丢音频会让人误以为都导入了
                _p(f"· 跳过 {len(audio)} 个音频/视频（转写请用 coco transcribe，"
                   f"或在网页版「⌖ 导入」里粘贴该文件夹路径）")
        elif p.exists():
            paths.append(p)
        else:
            _p(f"✗ 文件不存在：{p}")
    for p in paths:
        if p.suffix.lower() not in TEXT_EXTS:
            _p(f"✗ 跳过（不是文字材料，音频请用 coco transcribe）：{p.name}")
            continue
        try:
            mtg = import_transcript_file(p, title=args.title if len(paths) == 1 else None)
        except Exception as e:
            _p(f"✗ {p.name} 导入失败：{e}")
            continue
        _p(f"✓ 已导入：{mtg.id}")
        if load_config().get("auto_memory", True):
            try:
                ai.update_longterm(mtg)
                _p("  ✓ 已并入长期记忆")
            except ai.AIError as e:
                _p(f"  ✗ 记忆合并失败（材料本身已入库）：{e}")


def cmd_memorize(args):
    if args.all:
        meetings = [m for m in list_meetings() if m.transcript_md.exists()]
        meetings.reverse()  # 按时间正序累积
    else:
        if not args.key:
            _p("用法：coco memorize <会议> 或 coco memorize --all")
            sys.exit(1)
        meetings = [find_meeting(args.key)]
    for m in meetings:
        if m.meta.get("memorized_at") and not args.force:
            _p(f"· 跳过（已提取过）：{m.id}")
            continue
        _p(f"▶ 提取长期记忆：{m.id} …")
        try:
            out = ai.update_longterm(m, force=args.force)
            _p("✓ 已合并" if out else "· 跳过（转写过短）")
        except ai.AIError as e:
            _p(f"✗ {e}")
    if args.all:
        from .config import BRIEFS_DIR
        for b in sorted(BRIEFS_DIR.glob("*.md")):
            _p(f"▶ 合并每日简报：{b.stem} …")
            try:
                out = ai.memorize_brief(b, force=args.force)
                _p("✓ 已合并" if out else "· 跳过（已合并过）")
            except ai.AIError as e:
                _p(f"✗ {e}")


def cmd_watch(args):
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        _p(f"✗ 不是文件夹：{folder}")
        sys.exit(1)
    state_file = folder / ".coco_seen.json"
    seen = set(json.loads(state_file.read_text()) if state_file.exists() else [])
    if not state_file.exists():
        # 首次运行：已有的旧文件不处理，只盯之后新增的
        seen = {f.name for f in folder.iterdir()
                if f.suffix.lower() in AUDIO_EXTS}
        state_file.write_text(json.dumps(sorted(seen)))
        _p(f"（首次监控，忽略已有 {len(seen)} 个旧文件）")
    _p(f"👀 监控 {folder}（每 15 秒扫描，Ctrl+C 退出）")
    try:
        while True:
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() not in AUDIO_EXTS or f.name in seen:
                    continue
                # 等文件写完（两次大小一致才处理）
                size = f.stat().st_size
                time.sleep(2)
                if f.stat().st_size != size:
                    continue
                seen.add(f.name)
                state_file.write_text(json.dumps(sorted(seen)))
                _p(f"▶ 发现新文件：{f.name}")
                try:
                    mtg = create_meeting(f.stem, audio_path=f, source=f"监控:{folder.name}")
                    transcribe_meeting(mtg, model=args.model, progress=_p)
                    _p(f"✓ {mtg.transcript_md}")
                except Exception as e:
                    _p(f"✗ 处理失败：{e}")
            time.sleep(15)
    except KeyboardInterrupt:
        _p("\n已停止监控")


def cmd_devices(args):
    _p(list_devices())
    _p("\n麦克风用 \":N\"（冒号+音频设备号），当前配置见 coco config")


def cmd_web(args):
    import uvicorn
    from .server import app
    port = args.port or load_config()["port"]
    _p(f"🌐 coco Web → http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_config(args):
    cfg = load_config()
    if args.key and args.value is not None:
        old = cfg.get(args.key)
        val = args.value
        if isinstance(old, int):
            val = int(val)
        elif isinstance(old, list):
            val = json.loads(val)
        cfg[args.key] = val
        save_config(cfg)
        _p(f"✓ {args.key} = {val}")
    else:
        _p(json.dumps(cfg, ensure_ascii=False, indent=2))


# ---------- 入口 ----------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="coco",
        description="coco — 本地会议录音 / 转写 / AI 分析工具（YouNavi 本地复刻）",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("record", help="录音并自动转写（Ctrl+C 停止）")
    p.add_argument("title", nargs="?", help="会议标题")
    p.add_argument("--model", help="whisper 模型：large(默认)/turbo")
    p.add_argument("--device", help="ffmpeg 音频设备，如 :0")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("transcribe", help="导入音频/视频文件并转写")
    p.add_argument("files", nargs="+")
    p.add_argument("--title", help="会议标题（默认用文件名）")
    p.add_argument("--model", help="whisper 模型：large(默认)/turbo")
    p.add_argument("--move", action="store_true", help="移动而非复制源文件")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("list", help="列出会议库")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="查看某条会议的转写")
    p.add_argument("key", help="会议 id 或标题关键词")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("ask", help="对会议内容提问（默认最近一条）")
    p.add_argument("question")
    p.add_argument("refs", nargs="*", help="引用的会议（可多个）")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("report", help="按模板生成分析报告")
    p.add_argument("key", help="会议 id 或标题关键词")
    p.add_argument("-t", "--template", default="纪要",
                   choices=list(TEMPLATES), help="模板（默认：纪要）")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("templates", help="查看可用分析模板")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("brief", help="生成每日简报")
    p.add_argument("date", nargs="?", help="日期 YYYY-MM-DD（默认今天）")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("memory", help="查看/追加全局记忆")
    p.add_argument("add", nargs="?", help="要追加的记忆内容")
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("delete", help="删除会议（移入回收站 library/_trash）")
    p.add_argument("key", help="会议 id 或标题关键词")
    p.add_argument("-y", "--yes", action="store_true", help="不再确认")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("search", help="全文搜索所有转写和报告")
    p.add_argument("query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("track", help="跨会议洞察：追踪/深层信号/调研综合")
    p.add_argument("focus", nargs="?", help="聚焦的人/项目/客户（留空=全局）")
    p.add_argument("--mode", default="追踪", choices=list(TRACK_MODES),
                   help="分析模式（默认：追踪）")
    p.add_argument("--refs", help="限定分析这几场会议，逗号分隔（默认全部）")
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("prep", help="会前调查：汇总历史会议与记忆生成会前简报")
    p.add_argument("topic", help="会议主题")
    p.add_argument("--who", help="参会人（逗号分隔）")
    p.add_argument("--goal", help="我这次的目标")
    p.add_argument("--web", action="store_true", help="联网搜索参会人/公司公开信息")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("glossary", help="查看/追加/提炼词表（人名与专有名词）")
    p.add_argument("add", nargs="?", help="要追加的词条，如「智舱（误写：置仓）｜车机项目」")
    p.add_argument("--extract", action="store_true",
                   help="AI 从长期记忆与最近转写中提炼词表")
    p.set_defaults(func=cmd_glossary)

    p = sub.add_parser("import", help="导入已有文字材料（txt/md/srt/vtt/json）入库")
    p.add_argument("files", nargs="+", help="文件或文件夹")
    p.add_argument("--title", help="标题（单文件时生效，默认用文件名）")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("memorize", help="从会议提取长期记忆（转写完成后默认自动做）")
    p.add_argument("key", nargs="?", help="会议 id 或标题关键词")
    p.add_argument("--all", action="store_true", help="处理所有未提取过的会议")
    p.add_argument("--force", action="store_true", help="已提取过的也重新提取")
    p.set_defaults(func=cmd_memorize)

    p = sub.add_parser("watch", help="监控文件夹，新音频自动转写入库")
    p.add_argument("folder")
    p.add_argument("--model", help="whisper 模型")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("devices", help="列出录音设备")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("web", help="启动本地 Web 界面")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("config", help="查看/修改配置")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

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
