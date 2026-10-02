"""coco configuration: paths, defaults, model repositories."""
from __future__ import annotations

import json
import os
from pathlib import Path

# Project root: the code directory by default; COCO_ROOT overrides it (tests / several data sets)
ROOT = Path(os.environ.get("COCO_ROOT") or Path(__file__).resolve().parent.parent)
LIBRARY_DIR = ROOT / "library"
BRIEFS_DIR = LIBRARY_DIR / "_briefs"
WEEKLY_DIR = LIBRARY_DIR / "_weekly"
TRACKING_DIR = LIBRARY_DIR / "_tracking"
PREP_DIR = LIBRARY_DIR / "_prep"
TRASH_DIR = LIBRARY_DIR / "_trash"
MEMORY_FILE = ROOT / "memory" / "memory.md"          # hand-maintained global memory
LONGTERM_FILE = ROOT / "memory" / "longterm.md"      # long-term memory maintained by the AI
GLOSSARY_FILE = ROOT / "memory" / "glossary.md"      # glossary: names + proper nouns
CONFIG_FILE = ROOT / "coco.config.json"

# Whisper model registry — the ONE place a model alias is defined: repo per backend,
# download size (for the loading hint) and whether the UI / API may select it.
WHISPER_MODELS = {
    "turbo": {"mlx": "mlx-community/whisper-large-v3-turbo",
              "faster": "deepdml/faster-whisper-large-v3-turbo-ct2",
              "size": "1.6GB", "selectable": True},
    "large": {"mlx": "mlx-community/whisper-large-v3-mlx",
              "faster": "Systran/faster-whisper-large-v3",
              "size": "3GB", "selectable": True},
    # low-spec machines: light and fast, slightly less accurate
    "small": {"mlx": "mlx-community/whisper-small-mlx",
              "faster": "Systran/faster-whisper-small",
              "size": "0.5GB", "selectable": True},
    "tiny": {"mlx": "mlx-community/whisper-tiny",
             "faster": "Systran/faster-whisper-tiny",
             "size": "75MB", "selectable": False},  # quick self-test only
}
MODEL_REPOS = {k: v["mlx"] for k, v in WHISPER_MODELS.items()}
FASTER_MODEL_REPOS = {k: v["faster"] for k, v in WHISPER_MODELS.items()}
SELECTABLE_MODELS = tuple(k for k, v in WHISPER_MODELS.items() if v["selectable"])

DEFAULTS = {
    # --- language ---
    "ui_language": "auto",       # interface + CLI language: auto (browser / system) | zh-CN | en | fr
    "output_language": "ui",     # AI output: ui (= interface) | source (= language of the material) | ISO code
    # --- transcription ---
    "whisper_model": "turbo",    # turbo (fast) | large (most accurate) | small (low-spec) | tiny (self-test)
    "transcribe_backend": "auto",  # auto | mlx | faster | none (none = import transcripts only)
    "language": "auto",          # transcription language: auto-detect, or an ISO code (zh, en, fr …)
    "initial_prompt_extra": "",  # extra names / terms appended to the whisper prompt
    "beam_size": 0,              # 0 = greedy (fast). 5 = beam search: more accurate, slower
    "hallucination_filter": True,  # suppress + strip Whisper hallucinations (silence chatter, prompt echo)
    "audio_device": ":0",        # ffmpeg avfoundation input device (coco devices)
    "record_mode": "auto",       # auto | browser (MediaRecorder, every OS) | ffmpeg (server-side, macOS)
    "hf_endpoint": "",           # empty = auto-detect; mainland China: https://hf-mirror.com
    # --- AI ---
    "claude_bin": "claude",
    "codex_bin": "codex",        # OpenAI Codex CLI (ChatGPT subscription channel)
    "claude_extra_args": [],     # appended to every claude CLI call
    "ai_profiles": {},           # {"primary": {...}, "fast": {...}} – see providers.PROFILE_DEFAULTS
    "ai_tasks": {},              # task -> profile name, see providers.TASK_PROFILE_DEFAULTS
    "auto_memory": True,         # extract long-term memory after each transcript / import
    "memory_merge": "delta",     # delta (only changed entries, fast) | full (rewrite whole file)
    # --- server ---
    "port": 8765,
    "allowed_hosts": [],         # extra Host names allowed besides 127.0.0.1 / localhost (advanced)
    "desktop_shortcut": "auto",  # auto (add once on first start) | created | off
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy: nested dicts must not be shared
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
    return table.get(name, name)  # a full HF repo name is accepted as-is


def ensure_dirs() -> None:
    from .i18n import memory_placeholder  # lazy: i18n reads the config
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(memory_placeholder("memory"), encoding="utf-8")
    if not LONGTERM_FILE.exists():
        LONGTERM_FILE.write_text(memory_placeholder("longterm"), encoding="utf-8")
    if not GLOSSARY_FILE.exists():
        GLOSSARY_FILE.write_text(memory_placeholder("glossary"), encoding="utf-8")


def setup_hf_endpoint(cfg: dict | None = None) -> None:
    """Model download source: user config / env first; otherwise probe huggingface.co
    and fall back to the mainland-China mirror when it is unreachable."""
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
