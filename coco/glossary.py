"""词表：人名与专有名词的标准写法（memory/glossary.md）。

格式约定（宽松解析，手写不必完全规范）：
    ## 人名
    - 林炜（误写：林伟、林薇）｜产品负责人
    ## 专有名词
    - 智舱（误写：置仓、智仓）｜车机智能座舱项目

「正确写法」注入 whisper 转写提示提高识别率；全文注入人名与术语校正、AI 分析。
"""
from __future__ import annotations

import re

from .config import GLOSSARY_FILE, ensure_dirs

# 条目行：- 正确写法（误写：a、b）｜备注   （括号/竖线均兼容全半角，误写与备注可省略）
_ENTRY = re.compile(
    r"^[-*]\s*(?P<term>[^（(｜|]+?)"
    r"(?:[（(]\s*误写[:：]\s*(?P<wrong>[^）)]*)[）)])?"
    r"\s*(?:[｜|](?P<note>.*))?$"
)


def read_glossary() -> str:
    ensure_dirs()
    return GLOSSARY_FILE.read_text(encoding="utf-8")


def parse_glossary(text: str | None = None) -> dict:
    """解析词表 → {"人名": [{term, wrong, note}], "专有名词": [...]}。

    未识别的章节名归入「专有名词」，保证手写标题不规范时词条也不丢。
    """
    text = read_glossary() if text is None else text
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # 注释里的格式示例不算词条
    out: dict[str, list[dict]] = {"人名": [], "专有名词": []}
    section = "专有名词"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            head = line.lstrip("#").strip()
            if "人名" in head:
                section = "人名"
            elif head and not head.startswith("词表"):
                section = "专有名词"
            continue
        m = _ENTRY.match(line)
        if not m:
            continue
        term = m.group("term").strip()
        if not term:
            continue
        wrong = [w.strip() for w in re.split(r"[、,，/；;]", m.group("wrong") or "")
                 if w.strip()]
        out[section].append({"term": term, "wrong": wrong,
                             "note": (m.group("note") or "").strip()})
    return out


def initial_prompt_terms(max_chars: int = 200) -> str:
    """给 whisper 转写提示用的正确写法列表（人名在前），控制总长避免挤掉提示窗口。"""
    parsed = parse_glossary()
    terms, total = [], 0
    for e in parsed["人名"] + parsed["专有名词"]:
        t = e["term"]
        if total + len(t) + 1 > max_chars:
            break
        terms.append(t)
        total += len(t) + 1
    return "、".join(terms)


def glossary_stats(text: str | None = None) -> dict:
    p = parse_glossary(text)
    return {"names": len(p["人名"]), "terms": len(p["专有名词"])}
