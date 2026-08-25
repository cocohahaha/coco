"""coco 全局配置：路径、默认值、模型映射。"""
from __future__ import annotations

import json
import os
from pathlib import Path

# 项目根目录：默认为代码所在目录；COCO_ROOT 环境变量可覆盖（测试/多套数据用）
ROOT = Path(os.environ.get("COCO_ROOT") or Path(__file__).resolve().parent.parent)
LIBRARY_DIR = ROOT / "library"
BRIEFS_DIR = LIBRARY_DIR / "_briefs"
WEEKLY_DIR = LIBRARY_DIR / "_weekly"  # 周报
TRACKING_DIR = LIBRARY_DIR / "_tracking"
PREP_DIR = LIBRARY_DIR / "_prep"  # 会前调查简报
TRASH_DIR = LIBRARY_DIR / "_trash"
MEMORY_FILE = ROOT / "memory" / "memory.md"
LONGTERM_FILE = ROOT / "memory" / "longterm.md"  # AI 自动维护的长期记忆
GLOSSARY_FILE = ROOT / "memory" / "glossary.md"  # 词表：人名 + 专有名词
CONFIG_FILE = ROOT / "coco.config.json"

# whisper 模型别名 -> HuggingFace 仓库（mlx-whisper，Apple Silicon）
MODEL_REPOS = {
    "large": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "tiny": "mlx-community/whisper-tiny",  # 仅用于快速自检
}
# 同一套别名在 faster-whisper（Windows / Linux / Intel Mac，CTranslate2）下的仓库
FASTER_MODEL_REPOS = {
    "large": "Systran/faster-whisper-large-v3",
    "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "tiny": "Systran/faster-whisper-tiny",
}

MEMORY_PLACEHOLDER = (
    "# 全局记忆\n\n"
    "<!-- 这里的内容会注入每一次 AI 分析：人名、术语、公司背景、个人偏好等 -->\n"
)
LONGTERM_PLACEHOLDER = (
    "# 长期记忆\n\n"
    "<!-- coco 自动维护：每次转写完成后从会议中提取人物、项目、承诺、术语。"
    "可手动编辑，下次更新会在此基础上合并。 -->\n"
)
GLOSSARY_PLACEHOLDER = (
    "# 词表\n\n"
    "<!-- 人名与专有名词的标准写法。每行一条：\n"
    "- 正确写法（误写：错1、错2）｜备注\n"
    "「正确写法」会注入转写提示提高识别率；整个词表会作为人名与术语校正、AI 分析的依据。 -->\n\n"
    "## 人名\n\n"
    "## 专有名词\n"
)

DEFAULTS = {
    "whisper_model": "turbo",   # turbo（快） | large（最准） | tiny（自检）
    "transcribe_backend": "auto",  # auto | mlx | faster | none（none = 只导入文字稿，不转写）
    "language": "zh",           # 默认中文；可在界面手动换语言或填 auto 自动识别/其他 ISO 码
    "initial_prompt_extra": "",  # 追加到转写提示的专有名词/人名，提高识别准确率
    "beam_size": 0,             # 0 = 贪心（快）。设 5 启用 beam search：更准但更慢
    "audio_device": ":0",       # ffmpeg avfoundation 音频输入设备（coco devices 可查看）
    "claude_bin": "claude",
    "claude_extra_args": [],    # 例如 ["--model", "claude-sonnet-4-6"]
    "auto_memory": True,        # 转写完成后自动提取长期记忆

    "port": 8765,
    "hf_endpoint": "",          # 留空 = 自动探测；国内可设 https://hf-mirror.com
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    keys = {k: cfg[k] for k in DEFAULTS if k in cfg}
    CONFIG_FILE.write_text(
        json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_model_repo(name: str, backend: str = "mlx") -> str:
    table = FASTER_MODEL_REPOS if backend == "faster" else MODEL_REPOS
    return table.get(name, name)  # 允许直接传 HF 仓库名


def ensure_dirs() -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(MEMORY_PLACEHOLDER, encoding="utf-8")
    if not LONGTERM_FILE.exists():
        LONGTERM_FILE.write_text(LONGTERM_PLACEHOLDER, encoding="utf-8")
    if not GLOSSARY_FILE.exists():
        GLOSSARY_FILE.write_text(GLOSSARY_PLACEHOLDER, encoding="utf-8")


def setup_hf_endpoint(cfg: dict | None = None) -> None:
    """模型下载源：优先用户配置 / 环境变量；否则探测 huggingface.co，不通则切国内镜像。"""
    if os.environ.get("HF_ENDPOINT"):
        return
    cfg = cfg or load_config()
    if cfg.get("hf_endpoint"):
        os.environ["HF_ENDPOINT"] = cfg["hf_endpoint"]
        return
    import socket

    try:
        socket.create_connection(("huggingface.co", 443), timeout=4).close()
    except OSError:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
