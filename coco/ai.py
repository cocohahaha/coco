"""AI 分析层：通过本机 claude CLI（claude -p）调用，无需 API key。"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading

from .config import (BRIEFS_DIR, LONGTERM_FILE, MEMORY_FILE, TRACKING_DIR,
                     load_config)
from .library import Meeting, list_meetings, meetings_on
from .templates import (BRIEF_PROMPT, CHAT_SYSTEM, LONGTERM_PROMPT,
                        TRACK_PROMPT, TEMPLATES)

MAX_CONTEXT_CHARS = 400_000  # 控制注入 claude 的转写总量
MAX_LONGTERM_INJECT = 30_000  # 长期记忆注入分析时的长度上限
MEMORY_LOCK = threading.Lock()  # 长期记忆读改写串行，避免并发转写互相覆盖
TRANSCRIPT_LOCK = threading.Lock()  # transcript.md 写入串行（人名校正/手动保存/恢复原稿）


class AIError(RuntimeError):
    pass


NAME_FIX_PROMPT = (
    "下面是一份会议转写或基于它生成的分析报告。请只做一件事：统一并修正其中的【人名】写法。\n"
    "1. 以下面给出的全局记忆 / 长期记忆中出现的人名写法为准；记忆里没有的人名，"
    "在全篇内部统一为最可能正确、最一致的一种写法\n"
    "2. 只改人名（中文名、英文名、音译名），其他任何字词、标点、内容一律保持原样\n"
    "3. 严格保留所有 [时间戳]、原有的行结构与行数；文件头部的标题与元信息行原样不动\n"
    "4. 不增删信息、不改写说话内容、不做任何润色\n"
    "直接输出处理后的全文 Markdown，不要任何解释或开场白。"
)


def _run_name_fix(text: str, what: str = "转写") -> str:
    """对一段文本（转写或报告）做人名校正，以记忆中的人名为准；返回校正后文本（含尾换行）。

    只改人名，长度与行数应基本不变；偏差过大视为模型跑偏，抛错放弃（调用方据此保护原文）。
    """
    if not text.strip():
        raise AIError("内容为空，无需校正")
    if len(text) > 80_000:
        raise AIError("内容超过 8 万字，暂不支持一次性校正")
    out = run_claude(
        f"{_memory_block()}{NAME_FIX_PROMPT}\n\n<{what}>\n{text}\n</{what}>",
        timeout=1200,
    )
    ratio = len(out) / max(len(text), 1)
    if not 0.8 <= ratio <= 1.2:
        raise AIError("人名校正输出与原文长度差异过大，疑似改动了正文，已放弃")
    if abs(out.count("\n") - text.count("\n")) > 2:
        raise AIError("人名校正改变了行数，疑似破坏了结构，已放弃")
    return out.rstrip() + "\n"


def fix_names(mtg: "Meeting") -> str:
    """单场：以记忆中的人名为准校正该会议【转写】，原稿备份到 transcript.raw.md（仅首次）。"""
    text = mtg.transcript_text()
    if not text:
        raise AIError(f"会议 {mtg.id} 还没有转写内容")
    fixed = _run_name_fix(text, "转写")
    with TRANSCRIPT_LOCK:
        # 校正耗时较长，期间转写若被手动编辑或重新转写，放弃写回，避免用旧快照覆盖新内容
        if mtg.transcript_text() != text:
            raise AIError("转写在校正期间被改动过，已放弃写回，原稿未被覆盖")
        raw = mtg.path / "transcript.raw.md"
        if not raw.exists():
            raw.write_text(text, encoding="utf-8")  # 只备份最初的机器原稿
        mtg.transcript_md.write_text(fixed, encoding="utf-8")
        mtg.save_meta(names_fixed_at=dt.datetime.now().isoformat(timespec="seconds"))
    return fixed


def fix_all_names(meetings=None, progress=lambda msg: None) -> dict:
    """全库：以长期记忆中的人名为准，原地校正所有会议的【转写与报告】里的人名。

    原文自动备份（转写→transcript.raw.md；报告→<name>.md.bak，均仅首次）。
    单项失败只记录并跳过，不中断整批。返回统计 {meetings, transcripts, reports, errors}。
    meetings 参数仅供测试限定范围，正常调用为 None=全部已转写会议。
    """
    from .library import list_meetings
    targets = (meetings if meetings is not None
               else [m for m in list_meetings() if m.transcript_md.exists()])
    stats = {"meetings": 0, "transcripts": 0, "reports": 0, "errors": []}
    n = len(targets)
    for i, m in enumerate(targets, 1):
        progress(f"校正中 {i}/{n}：{m.title}")
        try:  # 转写：复用单场逻辑（含备份与并发保护）
            fix_names(m)
            stats["transcripts"] += 1
        except AIError as e:
            stats["errors"].append(f"{m.title}·转写：{e}")
        for rp in m.reports():  # 报告：原地改名，原报告备份为 <name>.md.bak（不被 reports() 收录）
            try:
                rtext = rp.read_text(encoding="utf-8")
                rfixed = _run_name_fix(rtext, "报告")
                bak = rp.with_name(rp.name + ".bak")
                if not bak.exists():
                    bak.write_text(rtext, encoding="utf-8")
                rp.write_text(rfixed, encoding="utf-8")
                stats["reports"] += 1
            except AIError as e:
                stats["errors"].append(f"{m.title}·报告{rp.stem}：{e}")
        stats["meetings"] += 1
    return stats


def restore_raw(mtg: "Meeting") -> str:
    """恢复人名校正前的原稿：把 transcript.raw.md 写回 transcript.md 并移除备份。"""
    raw = mtg.path / "transcript.raw.md"
    if not raw.exists():
        raise AIError("没有可恢复的原稿（transcript.raw.md 不存在）")
    original = raw.read_text(encoding="utf-8")
    with TRANSCRIPT_LOCK:
        mtg.transcript_md.write_text(original.rstrip() + "\n", encoding="utf-8")
        raw.unlink()  # 原稿已回到 transcript.md，移除备份，前端「恢复」按钮随之消失
        mtg.save_meta(names_fixed_at="")
    return original


def run_claude(prompt: str, timeout: int = 900) -> str:
    cfg = load_config()
    cmd = [cfg["claude_bin"], "-p", "--output-format", "text",
           *cfg.get("claude_extra_args", [])]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        raise AIError(f"找不到 claude CLI（{cfg['claude_bin']}），请确认已安装并登录")
    except subprocess.TimeoutExpired:
        raise AIError(f"claude 响应超时（>{timeout}s）")
    if proc.returncode != 0:
        raise AIError(f"claude 调用失败：{(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout.strip()


def _memory_block() -> str:
    parts = []
    if MEMORY_FILE.exists():
        text = MEMORY_FILE.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"<全局记忆>\n{text}\n</全局记忆>")
    if LONGTERM_FILE.exists():
        text = LONGTERM_FILE.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"<长期记忆 说明=\"从历史会议自动累积\">\n"
                         f"{text[:MAX_LONGTERM_INJECT]}\n</长期记忆>")
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _context_block(meetings: list[Meeting]) -> str:
    parts, total = [], 0
    for m in meetings:
        t = m.transcript_text()
        if not t:
            continue
        if total + len(t) > MAX_CONTEXT_CHARS:
            t = t[: MAX_CONTEXT_CHARS - total] + "\n…（转写过长，已截断）"
        parts.append(f"<会议 id=\"{m.id}\">\n{t}\n</会议>")
        total += len(t)
        if total >= MAX_CONTEXT_CHARS:
            break
    return "\n\n".join(parts)


def ask(question: str, meetings: list[Meeting]) -> str:
    ctx = _context_block(meetings)
    if not ctx:
        raise AIError("所引用的会议还没有转写内容")
    prompt = f"{CHAT_SYSTEM}\n\n{_memory_block()}{ctx}\n\n<问题>\n{question}\n</问题>"
    return run_claude(prompt)


def generate_report(mtg: Meeting, template: str) -> "tuple[str, str]":
    """生成模板报告，写入会议 reports/，返回 (报告路径, 内容)。"""
    if template not in TEMPLATES:
        raise AIError(f"未知模板：{template}（可用：{'、'.join(TEMPLATES)}）")
    text = mtg.transcript_text()
    if not text:
        raise AIError(f"会议 {mtg.id} 还没有转写内容")
    prompt = (
        f"{CHAT_SYSTEM}\n\n{_memory_block()}"
        f"<会议 id=\"{mtg.id}\">\n{text[:MAX_CONTEXT_CHARS]}\n</会议>\n\n"
        f"<任务>\n{TEMPLATES[template]['prompt']}\n</任务>\n\n"
        "直接输出 Markdown 报告正文，不要客套开场白。"
    )
    content = run_claude(prompt)
    mtg.reports_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%H%M")
    path = mtg.reports_dir / f"{template}-{stamp}.md"
    n = 2
    while path.exists():  # 同模板同分钟重复生成时让位，避免静默覆盖上一份
        path = mtg.reports_dir / f"{template}-{stamp}-{n}.md"
        n += 1
    path.write_text(f"# {mtg.title} · {template}\n\n{content}\n", encoding="utf-8")
    return str(path), content


def _merge_longterm(source_id: str, text: str, kind: str = "会议转写") -> str:
    """把一份材料（会议转写/每日简报）合并进 memory/longterm.md，返回新全文。"""
    with MEMORY_LOCK:
        old = (LONGTERM_FILE.read_text(encoding="utf-8")
               if LONGTERM_FILE.exists() else "")
        prompt = (
            f"{LONGTERM_PROMPT}\n\n"
            f"<当前长期记忆>\n{old.strip() or '（还是空的）'}\n</当前长期记忆>\n\n"
            f"<新材料 类型=\"{kind}\" id=\"{source_id}\">\n"
            f"{text[:100_000]}\n</新材料>"
        )
        out = run_claude(prompt, timeout=1200)
        if "## " not in out:
            raise AIError("长期记忆输出格式异常，本次未更新")
        if len(old) > 2000 and len(out) < len(old) * 0.3:
            raise AIError("长期记忆输出比原有内容短太多，疑似丢失信息，本次未更新")
        if old.strip():
            LONGTERM_FILE.with_suffix(".bak.md").write_text(old, encoding="utf-8")
        LONGTERM_FILE.write_text(out.rstrip() + "\n", encoding="utf-8")
    return out


def update_longterm(mtg: Meeting, force: bool = False) -> str:
    """从一场会议提取长期记忆并合并进 memory/longterm.md。

    返回更新后的全文；跳过时返回空串。每场会议只提取一次（meta.memorized_at），
    覆盖前自动备份到 longterm.bak.md。
    """
    if mtg.meta.get("memorized_at") and not force:
        return ""
    text = mtg.transcript_text()
    if len(text) < 200:  # 过短（测试/空转写）没有提取价值
        mtg.save_meta(memorized_at="skipped-too-short")
        return ""
    out = _merge_longterm(mtg.id, text)
    mtg.save_meta(memorized_at=dt.datetime.now().isoformat(timespec="seconds"))
    return out


BRIEFS_MEMORIZED = LONGTERM_FILE.parent / ".briefs_memorized.json"


def memorize_brief(path, force: bool = False) -> str:
    """把一份每日简报合并进长期记忆。简报常含跨会议的行动项汇总与战略洞察。

    已合并过的简报记录在 memory/.briefs_memorized.json，重复调用跳过（force 重做）。
    """
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
    out = _merge_longterm(f"每日简报-{path.stem}", text, kind="每日简报")
    done.add(path.name)
    BRIEFS_MEMORIZED.write_text(_json.dumps(sorted(done), ensure_ascii=False),
                                encoding="utf-8")
    return out


def track(focus: str = "") -> "tuple[str, str]":
    """跨会议追踪：承诺履行、表态变化、反复未决的问题。返回 (路径, 内容)。"""
    meetings = [m for m in list_meetings() if m.transcript_md.exists()]
    if len(meetings) < 2:
        raise AIError("至少需要两场已转写的会议才能做跨会议追踪")
    ctx = _context_block(list(reversed(meetings)))  # 按时间正序
    focus_line = (f"\n本次追踪聚焦：{focus}。其余内容仅在与之相关时提及。\n"
                  if focus.strip() else "")
    prompt = f"{_memory_block()}{TRACK_PROMPT}{focus_line}\n\n{ctx}"
    content = run_claude(prompt, timeout=1200)
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    name = f"{stamp}-{focus.strip()[:20]}" if focus.strip() else stamp
    path = TRACKING_DIR / f"{name}.md"
    title = f"# 跨会议追踪 · {focus.strip() or '全局'} · {stamp}"
    content = _dedupe_title(title, content)
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content


def _dedupe_title(title: str, content: str) -> str:
    """claude 输出若以同主题的一级标题开头，去掉它，避免和拼接的规范标题重复。"""
    stripped = content.lstrip("\n")
    if stripped.startswith("# "):
        first, _, rest = stripped.partition("\n")
        topic = title.lstrip("# ").split("·")[0].strip()[:4]
        if topic and topic in first:
            return rest.lstrip("\n")
    return content


def daily_brief(date: str | None = None) -> "tuple[str, str]":
    """汇总某天全部会议生成每日简报，返回 (路径, 内容)。"""
    date = date or dt.date.today().isoformat()
    meetings = [m for m in meetings_on(date) if m.transcript_md.exists()]
    if not meetings:
        raise AIError(f"{date} 没有已转写的会议")
    ctx = _context_block(list(reversed(meetings)))  # 按时间正序
    prompt = f"{_memory_block()}{BRIEF_PROMPT}\n\n日期：{date}\n\n{ctx}"
    title = f"# 每日简报 · {date}"
    content = _dedupe_title(title, run_claude(prompt))
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{date}.md"
    path.write_text(f"{title}\n\n{content}\n", encoding="utf-8")
    return str(path), content
