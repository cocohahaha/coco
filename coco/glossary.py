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

_WRONG = re.compile(r"[（(]\s*误写[:：]\s*([^）)]*)[）)]")  # （误写：a、b），全半角兼容


def read_glossary() -> str:
    ensure_dirs()
    # 外部编辑器可能以非 UTF-8 保存；宽容读取，绝不让词表问题拖垮转写
    return GLOSSARY_FILE.read_text(encoding="utf-8", errors="replace")


def parse_glossary(text: str | None = None) -> dict:
    """解析词表 → {"人名": [{term, wrong, note}], "专有名词": [...]}。

    对手写格式宽容：`- 林炜（产品负责人）`（普通括号归入备注）、`- **张三**`、
    缺竖线/缺误写都能解析；分隔线、超长的说明性句子不算词条。
    未识别的章节名归入「专有名词」。
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
        if not (line.startswith("- ") or line.startswith("* ")):
            continue
        body = line[2:].strip().replace("**", "")
        m = _WRONG.search(body)
        wrong = ([w.strip() for w in re.split(r"[、,，/；;]", m.group(1)) if w.strip()]
                 if m else [])
        body = _WRONG.sub("", body)
        parts = re.split(r"[｜|]", body, maxsplit=1)
        term = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else ""
        pm = re.match(r"(.+?)[（(]([^）)]*)[）)]\s*$", term)
        if pm:  # 普通括号注释归入备注：- 林炜（产品负责人）
            term = pm.group(1).strip()
            note = f"{pm.group(2).strip()} {note}".strip()
        term = term.strip(" -—·*＝=~～")
        if not term or len(term) > 30:  # 空行/分隔线/说明性长句不算词条
            continue
        out[section].append({"term": term, "wrong": wrong, "note": note})
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
