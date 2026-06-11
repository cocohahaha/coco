"""AI 分析层：通过本机 claude CLI（claude -p）调用，无需 API key。"""
from __future__ import annotations

import datetime as dt
import os
import subprocess

from .config import BRIEFS_DIR, MEMORY_FILE, load_config
from .library import Meeting, meetings_on
from .templates import BRIEF_PROMPT, CHAT_SYSTEM, TEMPLATES

MAX_CONTEXT_CHARS = 400_000  # 控制注入 claude 的转写总量


class AIError(RuntimeError):
    pass


PROOFREAD_PROMPT = (
    "下面是一份 whisper 自动转写的会议记录，可能存在同音字错误、专有名词错误、"
    "标点断句问题。请逐行校对：\n"
    "1. 只修正明显的同音字/专有名词/标点错误，不改写说话内容，不增删信息\n"
    "2. 严格保留所有 [时间戳] 和原有的行结构、行数\n"
    "3. 保留文件头部的标题与元信息行原样不动\n"
    "4. 全局记忆里给出的人名、品牌、术语写法以记忆为准\n"
    "直接输出校对后的全文 Markdown，不要任何解释或开场白。"
)


def proofread(mtg: "Meeting") -> str:
    """AI 校对转写：修同音字/专名/标点，备份原稿到 transcript.raw.md。"""
    text = mtg.transcript_text()
    if not text:
        raise AIError(f"会议 {mtg.id} 还没有转写内容")
    if len(text) > 60_000:
        raise AIError("转写超过 6 万字，暂不支持整篇校对（可先编辑或分段处理）")
    out = run_claude(
        f"{_memory_block()}{PROOFREAD_PROMPT}\n\n<转写>\n{text}\n</转写>",
        timeout=1200,
    )
    if len(out) < len(text) * 0.5:
        raise AIError("校对输出异常（比原文短一半以上），已放弃，原稿未改动")
    raw = mtg.path / "transcript.raw.md"
    if not raw.exists():
        raw.write_text(text, encoding="utf-8")  # 只备份最初的机器原稿
    mtg.transcript_md.write_text(out.rstrip() + "\n", encoding="utf-8")
    mtg.save_meta(proofread_at=dt.datetime.now().isoformat(timespec="seconds"))
    return out


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
    if MEMORY_FILE.exists():
        text = MEMORY_FILE.read_text(encoding="utf-8").strip()
        if text:
            return f"<全局记忆>\n{text}\n</全局记忆>\n\n"
    return ""


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
    path.write_text(f"# {mtg.title} · {template}\n\n{content}\n", encoding="utf-8")
    return str(path), content


def daily_brief(date: str | None = None) -> "tuple[str, str]":
    """汇总某天全部会议生成每日简报，返回 (路径, 内容)。"""
    date = date or dt.date.today().isoformat()
    meetings = [m for m in meetings_on(date) if m.transcript_md.exists()]
    if not meetings:
        raise AIError(f"{date} 没有已转写的会议")
    ctx = _context_block(list(reversed(meetings)))  # 按时间正序
    prompt = f"{_memory_block()}{BRIEF_PROMPT}\n\n日期：{date}\n\n{ctx}"
    content = run_claude(prompt)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{date}.md"
    path.write_text(f"# 每日简报 · {date}\n\n{content}\n", encoding="utf-8")
    return str(path), content
