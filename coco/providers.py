"""Model providers: how coco talks to a language model.

Four provider types, all streaming where the backend allows it:

- ``claude-cli``  the local ``claude`` command (Claude Code) – no API key needed when the CLI
                  is logged in with a Claude subscription. With a ``base_url`` + ``api_key`` the
                  same CLI can be pointed at any Anthropic-compatible endpoint
                  (e.g. DeepSeek's ``https://api.deepseek.com/anthropic``).
- ``codex-cli``   the local ``codex`` command (OpenAI Codex CLI) logged in with a ChatGPT
                  account: analysis runs on the ChatGPT subscription, no API key. Codex answers
                  a turn in one piece, so the UI shows the whole text when it is ready.
- ``anthropic``   direct HTTPS to an Anthropic-compatible ``/v1/messages`` endpoint
                  (Anthropic, DeepSeek /anthropic, …) – standard library only.
- ``openai``      direct HTTPS to an OpenAI-compatible ``/chat/completions`` endpoint
                  (OpenAI, DeepSeek, Qwen, Kimi, GLM, Gemini, OpenRouter, Ollama / LM Studio…).

Two profiles: ``primary`` (reports, insights, prep, chat) and ``fast`` (background
work: long-term memory, glossary, name fixing). Each task is mapped to a profile in
``ai_tasks``; when ``fast`` is not configured it simply equals ``primary``. When nothing is
configured at all, the primary profile picks whichever CLI is installed and logged in.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import load_config
from .i18n import t


class ModelError(RuntimeError):
    def __init__(self, message: str = "", status: int = 0):
        super().__init__(message)
        self.status = status  # HTTP status for API errors, 0 otherwise


PROFILE_TYPES = ("claude-cli", "codex-cli", "anthropic", "openai")
CLI_TYPES = ("claude-cli", "codex-cli")
PROFILE_NAMES = ("primary", "fast")
PROFILE_DEFAULTS = {
    "type": "claude-cli",
    "model": "",             # empty = provider default (claude-cli: the CLI's configured model)
    "base_url": "",          # claude-cli / anthropic: Anthropic-compatible root; openai: incl. /v1 if needed
    "api_key": "",           # literal key (stored in coco.config.json, which is git-ignored)
    "api_key_env": "",       # or the name of an environment variable holding the key
    "max_context_chars": 0,  # 0 = default (400k chars); lower it for small-context models
    "max_tokens": 16000,     # anthropic / openai output cap
    "extra_args": [],        # claude-cli / codex-cli: appended to the command line
    "reasoning": "",         # codex-cli: reasoning effort (low / medium / high); empty = model default
    "preset": "",            # which preset the profile was built from (UI only)
}
TASK_PROFILE_DEFAULTS = {
    "report": "primary", "ask": "primary", "track": "primary", "prep": "primary",
    "brief": "primary", "weekly": "primary",
    "memory": "fast", "glossary": "fast", "namefix": "fast", "participants": "fast",
}
# Ready-made channels for the settings dialog / `coco ai preset`.
# Model names: each vendor's documented recommendation as checked on 2026-09-25 ("model" for the
# main channel, "fast_model" for background work). Vendors rename models often – the settings
# dialog therefore lists the live models of the key (GET /models) and these are only a start.
# group: subscription (a CLI logged in with an account) | api (paste a key) | local | custom
PRESETS = {
    "claude-cli": {"type": "claude-cli", "label": "Claude", "group": "subscription",
                   "base_url": "", "model": "", "fast_model": "haiku", "api_key_env": ""},
    "codex-cli": {"type": "codex-cli", "label": "ChatGPT", "group": "subscription",
                  "base_url": "", "model": "", "fast_model": "", "api_key_env": ""},
    "deepseek": {"type": "openai", "label": "DeepSeek", "group": "api", "region": "cn",
                 "base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro",
                 "fast_model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY",
                 "key_url": "https://platform.deepseek.com/api_keys"},
    "qwen": {"type": "openai", "label": "通义千问 Qwen", "group": "api", "region": "cn",
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.7-plus",
             "fast_model": "qwen3.8-flash", "api_key_env": "DASHSCOPE_API_KEY",
             "key_url": "https://bailian.console.aliyun.com/model/settings/api-key"},
    "qwen-intl": {"type": "openai", "label": "Qwen (Alibaba Cloud International)", "group": "api",
                  "region": "intl", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen3.7-plus", "fast_model": "qwen3.8-flash", "api_key_env": "DASHSCOPE_API_KEY",
                  "key_url": "https://modelstudio.console.alibabacloud.com/ap-southeast-1/settings/api-key"},
    "kimi": {"type": "openai", "label": "Kimi", "group": "api", "region": "cn",
             "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k3", "fast_model": "kimi-k2.6",
             "api_key_env": "MOONSHOT_API_KEY", "key_url": "https://platform.kimi.com/console/api-keys"},
    "kimi-intl": {"type": "openai", "label": "Kimi (International)", "group": "api", "region": "intl",
                  "base_url": "https://api.moonshot.ai/v1", "model": "kimi-k3", "fast_model": "kimi-k2.6",
                  "api_key_env": "MOONSHOT_API_KEY", "key_url": "https://platform.kimi.ai/console/api-keys"},
    "glm": {"type": "openai", "label": "智谱 GLM", "group": "api", "region": "cn",
            "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3", "fast_model": "glm-5.3-flash",
            "api_key_env": "ZHIPUAI_API_KEY", "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"},
    "glm-intl": {"type": "openai", "label": "GLM (Z.ai)", "group": "api", "region": "intl",
                 "base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3", "fast_model": "glm-5.3-flash",
                 "api_key_env": "ZAI_API_KEY", "key_url": "https://z.ai/manage-apikey/apikey-list"},
    "doubao": {"type": "openai", "label": "豆包 Doubao", "group": "api", "region": "cn",
               "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-seed-2-1-pro-260915",
               "fast_model": "doubao-seed-2-1-lite-260915", "api_key_env": "ARK_API_KEY",
               "key_url": "https://ark.volcengine.com/region:cn-beijing/apiKey"},
    "minimax": {"type": "openai", "label": "MiniMax", "group": "api", "region": "cn",
                "base_url": "https://api.minimax.cn/v1", "model": "MiniMax-M3",
                "fast_model": "MiniMax-M2.7-highspeed", "api_key_env": "MINIMAX_API_KEY",
                "key_url": "https://platform.minimax.cn/user-center/basic-information/interface-key"},
    "siliconflow": {"type": "openai", "label": "硅基流动 SiliconFlow", "group": "api", "region": "cn",
                    "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V4-Flash",
                    "fast_model": "deepseek-ai/DeepSeek-V4-Flash", "api_key_env": "SILICONFLOW_API_KEY",
                    "key_url": "https://cloud.siliconflow.cn/account/ak"},
    "openai": {"type": "openai", "label": "OpenAI API", "group": "api", "region": "intl",
               "base_url": "https://api.openai.com/v1", "model": "gpt-6-sol", "fast_model": "gpt-6-luna",
               "api_key_env": "OPENAI_API_KEY", "key_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"type": "anthropic", "label": "Anthropic API", "group": "api", "region": "intl",
                  "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5",
                  "fast_model": "claude-haiku-4-5", "api_key_env": "ANTHROPIC_API_KEY",
                  "key_url": "https://platform.claude.com/settings/keys"},
    "gemini": {"type": "openai", "label": "Google Gemini", "group": "api", "region": "intl",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-3.8-flash",
               "fast_model": "gemini-3.5-flash-lite", "api_key_env": "GEMINI_API_KEY",
               "key_url": "https://aistudio.google.com/apikey"},
    "openrouter": {"type": "openai", "label": "OpenRouter", "group": "api", "region": "intl",
                   "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-6-sol",
                   "fast_model": "google/gemini-3.8-flash", "api_key_env": "OPENROUTER_API_KEY",
                   "key_url": "https://openrouter.ai/keys"},
    "ollama": {"type": "openai", "label": "Ollama", "group": "local",
               "base_url": "http://localhost:11434/v1", "model": "", "fast_model": "", "api_key_env": "",
               "max_context_chars": 60000, "key_url": "https://ollama.com/download"},
    "lmstudio": {"type": "openai", "label": "LM Studio", "group": "local",
                 "base_url": "http://localhost:1234/v1", "model": "", "fast_model": "", "api_key_env": "",
                 "max_context_chars": 60000, "key_url": "https://lmstudio.ai"},
    "custom-openai": {"type": "openai", "label": "OpenAI-compatible", "group": "custom",
                      "base_url": "", "model": "", "fast_model": "", "api_key_env": ""},
    "custom-anthropic": {"type": "anthropic", "label": "Anthropic-compatible", "group": "custom",
                         "base_url": "", "model": "", "fast_model": "", "api_key_env": ""},
    # names used before September 2026 (`coco ai preset …`, old configs); not listed in the UI
    "claude-cli-fast": {"type": "claude-cli", "label": "Claude · Haiku", "group": "hidden",
                        "base_url": "", "model": "haiku", "api_key_env": ""},
    "deepseek-openai": {"type": "openai", "label": "DeepSeek", "group": "hidden",
                        "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                        "api_key_env": "DEEPSEEK_API_KEY", "key_url": "https://platform.deepseek.com/api_keys"},
    "deepseek-anthropic": {"type": "anthropic", "label": "DeepSeek (Anthropic API)", "group": "hidden",
                           "base_url": "https://api.deepseek.com/anthropic", "model": "deepseek-v4-pro",
                           "api_key_env": "DEEPSEEK_API_KEY", "key_url": "https://platform.deepseek.com/api_keys"},
    "deepseek-via-claude-cli": {"type": "claude-cli", "label": "DeepSeek via Claude Code", "group": "hidden",
                                "base_url": "https://api.deepseek.com/anthropic", "model": "deepseek-v4-pro",
                                "api_key_env": "DEEPSEEK_API_KEY",
                                "key_url": "https://platform.deepseek.com/api_keys"},
}
DEFAULT_CONTEXT_CHARS = 400_000
CODEX_CONTEXT_CHARS = 300_000


def preset_profile(name: str, fast: bool = False) -> dict:
    """Profile fields of a preset (label / group / urls are UI-only)."""
    pr = PRESETS[name]
    d = {k: v for k, v in pr.items() if k in PROFILE_DEFAULTS}
    if fast and pr.get("fast_model"):
        d["model"] = pr["fast_model"]
    d["preset"] = name
    return d


def get_profile(name: str = "primary", cfg: "dict | None" = None) -> dict:
    cfg = cfg or load_config()
    profiles = cfg.get("ai_profiles") or {}
    if name != "primary" and not profiles.get(name):
        return get_profile("primary", cfg)  # unset secondary profile = same as primary
    p = dict(PROFILE_DEFAULTS)
    stored = profiles.get(name) or {}
    if name == "primary" and not stored:
        p.update(auto_profile(cfg))
    p.update({k: v for k, v in stored.items() if k in PROFILE_DEFAULTS})
    if p["type"] not in PROFILE_TYPES:
        p["type"] = "claude-cli"
    p["name"] = name
    return p


def auto_profile(cfg: "dict | None" = None) -> dict:
    """Nothing configured yet: use a subscription the user already has. Claude Code first
    (coco's original default), then ChatGPT through Codex; otherwise claude-cli, which the
    first-run guide then helps to set up."""
    cfg = cfg or load_config()
    if claude_found(cfg):
        return {"type": "claude-cli", "auto": True}
    if codex_found(cfg) and codex_login(cfg):
        return {"type": "codex-cli", "auto": True}
    return {"type": "claude-cli", "auto": True}


def profile_for_task(task: str, cfg: "dict | None" = None) -> dict:
    cfg = cfg or load_config()
    tasks = dict(TASK_PROFILE_DEFAULTS)
    tasks.update({k: v for k, v in (cfg.get("ai_tasks") or {}).items() if v in PROFILE_NAMES})
    return get_profile(tasks.get(task, "primary"), cfg)


def resolve_api_key(p: dict) -> str:
    if p.get("api_key"):
        return str(p["api_key"])
    if p.get("api_key_env"):
        return os.environ.get(str(p["api_key_env"]), "")
    return ""


def context_limit(p: "dict | None" = None) -> int:
    p = p or get_profile()
    try:
        explicit = int(p.get("max_context_chars") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit:
        return explicit
    # Codex models take ~272k tokens and the agent's own instructions use ~15k of them
    return CODEX_CONTEXT_CHARS if p.get("type") == "codex-cli" else DEFAULT_CONTEXT_CHARS


def supports_web(p: dict) -> bool:
    """Only Claude Code's built-in WebSearch tool is available; HTTP providers cannot browse."""
    return p["type"] == "claude-cli" and not p.get("base_url")


def public_profile(p: dict) -> dict:
    """Profile as shown to the UI: never returns the key itself."""
    return {**{k: p.get(k) for k in PROFILE_DEFAULTS if k != "api_key"},
            "has_key": bool(resolve_api_key(p)), "api_key_set": bool(p.get("api_key")),
            "name": p.get("name", ""), "auto": bool(p.get("auto")), "label": profile_label(p)}


def profile_label(p: dict) -> str:
    """Short human name of a channel for the header chip: 'Claude', 'ChatGPT · gpt-5.5', 'DeepSeek · …'."""
    typ = p.get("type")
    if typ == "claude-cli" and not p.get("base_url"):
        name = "Claude"
    elif typ == "codex-cli":
        name = "ChatGPT"
    else:
        preset = PRESETS.get(p.get("preset") or "") or {}
        name = preset.get("label") or _host_label(p.get("base_url") or "") or str(typ)
    model = p.get("model") or ""
    return f"{name} · {model}" if model else name


def _host_label(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    for part in ("deepseek", "openai", "anthropic", "moonshot", "bigmodel", "z.ai", "dashscope",
                 "siliconflow", "openrouter", "volces", "googleapis", "minimax", "localhost", "127.0.0.1"):
        if part in host:
            return {"bigmodel": "GLM", "z.ai": "GLM", "dashscope": "Qwen", "volces": "Doubao",
                    "googleapis": "Gemini", "localhost": "Local", "127.0.0.1": "Local"}.get(part, part.capitalize())
    return host


# ---------- claude CLI ----------

def _claude_fallback_paths() -> "list[Path]":
    """Well-known install locations, for processes whose PATH misses them.

    A server started by double-clicking coco.command / run.bat inherits a minimal PATH
    that often lacks ~/.local/bin (the official installer's target), so `claude` would
    stay “not found” forever even after a correct install + restart."""
    home = Path.home()
    cands = [home / ".local" / "bin" / "claude",
             home / ".claude" / "local" / "claude",
             Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    if os.name == "nt":
        cands = [home / ".local" / "bin" / "claude.exe"]
        appdata = os.environ.get("APPDATA")
        if appdata:
            cands.append(Path(appdata) / "npm" / "claude.cmd")
    return cands


def resolve_claude_bin(cfg: "dict | None" = None) -> str:
    """Locate the claude executable. On Windows npm installs claude.cmd and the official
    installer claude.exe; CreateProcess does not consult PATHEXT, so resolve through
    shutil.which; when PATH does not cover the usual install dirs, probe them directly.
    Unknown → returned as-is so subprocess raises FileNotFoundError."""
    cfg = cfg or load_config()
    raw = cfg.get("claude_bin") or "claude"
    hit = shutil.which(raw)
    if hit:
        return hit
    if raw == "claude":  # only for the default name; an explicit path stays as typed
        for cand in _claude_fallback_paths():
            if cand.exists():
                return str(cand)
    return raw


def claude_found(cfg: "dict | None" = None) -> bool:
    resolved = resolve_claude_bin(cfg)
    return Path(resolved).exists() or shutil.which(resolved) is not None


def _no_key_needed(base_url: str) -> bool:
    """Endpoints that plausibly run without a key: localhost and private-network hosts
    (Ollama / LM Studio on this or another machine in the LAN)."""
    from urllib.parse import urlparse
    host = (urlparse(base_url).hostname or "") if base_url else ""
    if not host:
        return False
    if host in ("localhost", "::1", "0.0.0.0", "host.docker.internal") or host.endswith(".local"):
        return True
    if host.startswith(("127.", "10.", "192.168.")):
        return True
    if host.startswith("172."):
        try:
            return 16 <= int(host.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def is_ready(cfg: "dict | None" = None) -> bool:
    """Can the primary channel plausibly answer? Drives the first-run guidance.
    Heuristic only (binary / login / key presence) — no model round-trip on every
    /api/config; the real check is the per-profile Test button."""
    cfg = cfg or load_config()
    p = get_profile("primary", cfg)
    if p["type"] == "claude-cli":
        ok = claude_found(cfg)
        if p.get("base_url"):  # CLI pointed at a third-party endpoint also needs its key
            ok = ok and (bool(resolve_api_key(p)) or _no_key_needed(p["base_url"]))
        return ok
    if p["type"] == "codex-cli":
        return codex_found(cfg) and bool(codex_login(cfg))
    return bool(resolve_api_key(p)) or _no_key_needed(p.get("base_url") or "")


def detect_clis(cfg: "dict | None" = None) -> dict:
    """What the onboarding screen shows: which subscription CLIs are installed / logged in."""
    cfg = cfg or load_config()
    return {"claude": claude_found(cfg), "codex": codex_found(cfg),
            "codex_login": codex_login(cfg) if codex_found(cfg) else ""}


def _claude_cwd() -> str:
    """Run claude in an empty scratch directory so it does not pick up CLAUDE.md files
    of whatever project coco happens to live in (they would leak into every prompt)."""
    d = Path(tempfile.gettempdir()) / "coco-claude-cwd"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _run_claude_cli(prompt: str, p: dict, cfg: dict, timeout: int,
                    allowed_tools: "list[str] | None", on_delta) -> str:
    cmd = [resolve_claude_bin(cfg), "-p", "--output-format", "stream-json", "--verbose",
           "--include-partial-messages", "--no-session-persistence",
           # no MCP servers and no tools for plain analysis: smaller system prompt, no side effects
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    key = resolve_api_key(p)
    if p.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = str(p["base_url"]).rstrip("/")
    if key:
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = key
        # --bare: skip hooks / plugins / keychain / CLAUDE.md discovery, authenticate with the
        # key only. Starts in well under a second instead of several. Not usable for OAuth logins.
        cmd.append("--bare")
    if p.get("model"):
        cmd += ["--model", str(p["model"])]
    if allowed_tools:
        cmd += ["--tools", ",".join(allowed_tools), "--allowedTools", ",".join(allowed_tools)]
    else:
        cmd += ["--tools", ""]
    cmd += [str(a) for a in (cfg.get("claude_extra_args") or [])]
    cmd += [str(a) for a in (p.get("extra_args") or [])]

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=_claude_cwd(), env=env,
            # explicit UTF-8: Windows pipes default to GBK and would garble Chinese prompts
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        raise ModelError(t("ai.claude_not_found", bin=cfg.get("claude_bin") or "claude"))

    stderr_buf: list[str] = []

    def feed():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def drain_err():
        try:
            for line in proc.stderr:
                stderr_buf.append(line)
        except (ValueError, OSError):
            pass

    threading.Thread(target=feed, daemon=True).start()
    threading.Thread(target=drain_err, daemon=True).start()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    chunks: list[str] = []
    final: "str | None" = None
    error_text = ""
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            typ = ev.get("type")
            if typ == "stream_event":
                e = ev.get("event") or {}
                if e.get("type") == "content_block_delta":
                    d = e.get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        chunks.append(d["text"])
                        if on_delta:
                            on_delta(d["text"])
            elif typ == "assistant" and not chunks:
                # CLI without partial-message support: whole message arrives at once
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        chunks.append(block["text"])
                        if on_delta:
                            on_delta(block["text"])
            elif typ == "result":
                if ev.get("is_error"):
                    error_text = str(ev.get("result") or ev.get("error") or "")
                elif isinstance(ev.get("result"), str):
                    final = ev["result"]
        proc.wait()
    finally:
        timer.cancel()
    if proc.returncode == -9 or (proc.returncode is not None and proc.returncode < 0 and not chunks):
        raise ModelError(t("ai.timeout", seconds=timeout))
    if error_text:
        raise ModelError(t("ai.call_failed", detail=error_text.strip()[:500]))
    if proc.returncode != 0:
        detail = ("".join(stderr_buf) or "".join(chunks)).strip()[:500]
        raise ModelError(t("ai.call_failed", detail=detail))
    text = final if (final is not None and final.strip()) else "".join(chunks)
    return text.strip()


# ---------- codex CLI (ChatGPT subscription) ----------

def _codex_fallback_paths() -> "list[Path]":
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        cands = [home / ".local" / "bin" / "codex.exe"]
        if appdata:
            cands.append(Path(appdata) / "npm" / "codex.cmd")
        return cands
    return [home / ".local" / "bin" / "codex", Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex"), home / ".npm-global" / "bin" / "codex"]


def resolve_codex_bin(cfg: "dict | None" = None) -> str:
    cfg = cfg or load_config()
    raw = cfg.get("codex_bin") or "codex"
    hit = shutil.which(raw)
    if hit:
        return hit
    if raw == "codex":
        for cand in _codex_fallback_paths():
            if cand.exists():
                return str(cand)
    return raw


def codex_found(cfg: "dict | None" = None) -> bool:
    resolved = resolve_codex_bin(cfg)
    return Path(resolved).exists() or shutil.which(resolved) is not None


_LOGIN_CACHE: dict = {}


def codex_login(cfg: "dict | None" = None) -> str:
    """'chatgpt' | 'apikey' | '' – from `codex login status` (fast, local). Cached briefly
    because /api/config asks on every page load."""
    exe = resolve_codex_bin(cfg)
    hit = _LOGIN_CACHE.get(exe)
    # a logged-in state is kept 20 s; "not logged in" only 3 s, so one /api/config asks once but
    # "check again" right after `codex login` already sees the new state
    if hit and time.monotonic() - hit[1] < (20 if hit[0] else 3):
        return hit[0]
    state = ""
    try:
        r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=5,
                           encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        out = (r.stdout + r.stderr).lower()
        if r.returncode == 0 and "logged in" in out:
            state = "chatgpt" if "chatgpt" in out else "apikey"
    except (OSError, subprocess.SubprocessError):
        state = ""
    _LOGIN_CACHE[exe] = (state, time.monotonic())
    return state


def codex_models(cfg: "dict | None" = None) -> "list[dict]":
    """Models offered to this ChatGPT account (Codex's own catalog), best first."""
    try:
        r = subprocess.run([resolve_codex_bin(cfg), "debug", "models"], capture_output=True, text=True,
                           timeout=20, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        items = json.loads(r.stdout).get("models") or []
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return []
    items = [m for m in items if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    items.sort(key=lambda m: m.get("priority", 99))
    return [{"id": m["slug"], "name": m.get("display_name") or m["slug"],
             "desc": m.get("description") or "", "context": m.get("context_window") or 0,
             "reasoning": [r.get("effort") if isinstance(r, dict) else r
                           for r in (m.get("supported_reasoning_levels") or [])]} for m in items]


# Codex is an agent; coco only needs its model. No commands, no files, no web.
CODEX_PREAMBLE = ("You are used as a plain text model by coco, a meeting-notes tool. Do not run "
                  "commands, do not read or write files and do not browse: everything you need is "
                  "in this message. Reply with the requested text only.\n\n")
_CODEX_MODERN = ["--ephemeral", "--ignore-user-config", "--ignore-rules", "-c", "project_doc_max_bytes=0",
                 "--color", "never"]
_CODEX_LEGACY: set = set()  # binaries that rejected the modern flags


def _codex_cmd(exe: str, p: dict, cfg: dict) -> "list[str]":
    cmd = [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only"]
    if exe not in _CODEX_LEGACY:
        # --ignore-user-config: skip the user's MCP servers / plugins (≈10 s faster per call);
        # the ChatGPT login is still read from CODEX_HOME
        cmd += _CODEX_MODERN
    if p.get("model"):
        cmd += ["-m", str(p["model"])]
    if p.get("reasoning"):
        cmd += ["-c", f'model_reasoning_effort="{p["reasoning"]}"']
    cmd += [str(a) for a in (p.get("extra_args") or [])]
    return cmd + ["-"]


def _run_codex_cli(prompt: str, p: dict, cfg: dict, timeout: int, on_delta) -> str:
    exe = resolve_codex_bin(cfg)
    cwd = Path(tempfile.gettempdir()) / "coco-codex-cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CODEX_SANDBOX")}
    for attempt in range(2):
        try:
            proc = subprocess.Popen(_codex_cmd(exe, p, cfg), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=str(cwd), env=env, text=True,
                                    encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise ModelError(t("ai.codex_not_found", bin=cfg.get("codex_bin") or "codex"))
        stderr_buf: list[str] = []

        def feed():
            try:
                proc.stdin.write(CODEX_PREAMBLE + prompt)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def drain_err():
            try:
                for line in proc.stderr:
                    stderr_buf.append(line)
            except (ValueError, OSError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        threading.Thread(target=drain_err, daemon=True).start()
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        messages: list[str] = []
        error_text = ""
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                typ = ev.get("type")
                item = ev.get("item") or {}
                if typ == "item.completed" and item.get("type") in ("agent_message", "assistant_message"):
                    text = item.get("text") or ""
                    if text:
                        messages.append(text)
                        if on_delta:
                            on_delta(("\n\n" if len(messages) > 1 else "") + text)
                elif typ == "turn.failed":
                    error_text = str((ev.get("error") or {}).get("message") or ev.get("error") or "")
                elif typ == "error":
                    error_text = str(ev.get("message") or ev.get("error") or "")
                elif isinstance(ev.get("msg"), dict):  # codex < 0.40 event schema
                    msg = ev["msg"]
                    if msg.get("type") == "agent_message" and msg.get("message"):
                        messages.append(msg["message"])
                        if on_delta:
                            on_delta(msg["message"])
                    elif msg.get("type") in ("error", "stream_error"):
                        error_text = str(msg.get("message") or "")
            proc.wait()
        finally:
            timer.cancel()
        err = "".join(stderr_buf)
        if (proc.returncode not in (0, None) and attempt == 0 and exe not in _CODEX_LEGACY
                and ("unexpected argument" in err or "unrecognized" in err or "unknown option" in err)):
            _CODEX_LEGACY.add(exe)  # an older codex: retry with the flags every version knows
            continue
        break
    if proc.returncode is not None and proc.returncode < 0 and not messages:
        raise ModelError(t("ai.timeout", seconds=timeout))
    if not messages:
        detail = (error_text or err or "").strip()
        low = detail.lower()
        if "login" in low or "logged in" in low or "unauthorized" in low or "401" in low:
            raise ModelError(t("ai.codex_login"))
        if "usage limit" in low or "rate limit" in low or "429" in low:
            raise ModelError(t("ai.codex_limit", detail=detail[:300]))
        raise ModelError(t("ai.call_failed", detail=detail[:500] or f"exit {proc.returncode}"))
    return "\n\n".join(messages).strip()


# ---------- HTTP providers (standard library, streaming SSE) ----------

def _api_error_detail(raw: str) -> str:
    """Vendor error bodies are JSON of various shapes; keep the human message."""
    try:
        d = json.loads(raw)
    except ValueError:
        return raw.strip()[:400]
    err = d.get("error") if isinstance(d, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)[:400]
    if isinstance(d, dict):
        return str(err or d.get("message") or d.get("msg") or d.get("detail") or raw)[:400]
    return raw.strip()[:400]


def _http_error(status: int, detail: str, model: str = "") -> ModelError:
    """Turn an HTTP status into advice a non-developer can act on; keep the vendor's words."""
    hint = {401: "ai.http_401", 403: "ai.http_403", 404: "ai.http_404", 402: "ai.http_402",
            429: "ai.http_429"}.get(status) or ("ai.http_5xx" if status >= 500 else "")
    msg = t("ai.http_error", status=status, detail=detail)
    if hint:
        msg = t(hint, model=model or "?") + "\n" + msg
    return ModelError(msg, status=status)


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, headers: dict, body: "dict | None", timeout: int, model: str = ""):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": "coco", **headers})
    try:
        if _no_key_needed(url):  # Ollama / LM Studio on this machine or the LAN: bypass http(s)_proxy
            return _DIRECT.open(req, timeout=min(timeout, 120))
        return urllib.request.urlopen(req, timeout=min(timeout, 120))
    except urllib.error.HTTPError as e:
        raise _http_error(e.code, _api_error_detail(e.read().decode("utf-8", errors="replace")), model)
    except urllib.error.URLError as e:
        raise ModelError(t("ai.connect_failed", url=url, detail=str(e.reason)[:200]))
    except (OSError, http.client.HTTPException) as e:  # reset / disconnect / timeout
        raise ModelError(t("ai.connect_failed", url=url, detail=str(e)[:200]))


def _post_stream(url: str, headers: dict, body: dict, timeout: int, model: str = ""):
    return _request(url, headers, body, timeout, model)


def _iter_sse(resp, deadline: float):
    """Yield parsed JSON objects from an SSE stream; stop at [DONE] / EOF."""
    for raw in resp:
        if time.monotonic() > deadline:
            raise ModelError(t("ai.timeout", seconds=int(deadline)))
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                return
            continue
        try:
            yield json.loads(payload)
        except ValueError:
            continue


def _run_anthropic(prompt: str, p: dict, timeout: int, on_delta) -> str:
    base = (p.get("base_url") or "https://api.anthropic.com").rstrip("/")
    key = resolve_api_key(p)
    if not key:
        raise ModelError(t("ai.key_required", profile=p.get("name", "")))
    if not p.get("model"):
        raise ModelError(t("ai.model_required", profile=p.get("name", "")))
    body = {"model": p["model"], "max_tokens": int(p.get("max_tokens") or 16000),
            "stream": True, "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    with _post_stream(base + "/v1/messages", headers, body, timeout, p["model"]) as resp:
        for ev in _iter_sse(resp, deadline):
            typ = ev.get("type")
            if typ == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    chunks.append(d["text"])
                    if on_delta:
                        on_delta(d["text"])
            elif typ == "error":
                err = ev.get("error") or {}
                raise ModelError(t("ai.call_failed", detail=str(err.get("message") or err)[:400]))
            elif typ == "message_stop":
                break
    return "".join(chunks).strip()


def _openai_limit_param(base: str) -> str:
    """OpenAI's current models reject `max_tokens` (they want `max_completion_tokens`);
    every other compatible server still uses `max_tokens`."""
    return "max_completion_tokens" if "api.openai.com" in base else "max_tokens"


class _ThinkFilter:
    """Reasoning models behind OpenAI-compatible servers (MiniMax, Ollama / LM Studio running
    qwen3, deepseek-r1 …) may put their chain of thought into the answer as <think>…</think>.
    Drop a leading think block from the stream; everything else passes through untouched."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf, self.state = "", "start"  # start → think → pass

    def feed(self, text: str) -> str:
        if self.state == "pass":
            return text
        self.buf += text
        if self.state == "start":
            head = self.buf.lstrip()
            if len(head) < len(self.OPEN) and self.OPEN.startswith(head):
                return ""  # could still become "<think>"
            if not head.startswith(self.OPEN):
                self.state, out, self.buf = "pass", self.buf, ""
                return out
            self.state, self.buf = "think", head[len(self.OPEN):]
        i = self.buf.find(self.CLOSE)
        if i < 0:
            self.buf = self.buf[-len(self.CLOSE):]  # the closing tag may arrive split
            return ""
        out, self.buf, self.state = self.buf[i + len(self.CLOSE):].lstrip("\n"), "", "pass"
        return out

    def flush(self) -> str:
        return self.buf if self.state == "start" else ""


def _run_openai(prompt: str, p: dict, timeout: int, on_delta) -> str:
    base = (p.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    if not p.get("model"):
        raise ModelError(t("ai.model_required", profile=p.get("name", "")))
    key = resolve_api_key(p)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": p["model"], "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    # an explicit output cap: some servers default to ~1–4k tokens and would cut reports short
    limit = int(p.get("max_tokens") or 0)
    if limit:
        body[_openai_limit_param(base)] = limit
    if "minimax" in base:  # keep MiniMax's reasoning out of the answer text
        body["reasoning_split"] = True
    think = _ThinkFilter()
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    try:
        resp = _post_stream(base + "/chat/completions", headers, body, timeout, p["model"])
    except ModelError as e:
        # the model's own output ceiling is lower than ours → let the server pick its maximum
        if e.status == 400 and limit and "token" in str(e).lower():
            body.pop("max_tokens", None)
            body.pop("max_completion_tokens", None)
            resp = _post_stream(base + "/chat/completions", headers, body, timeout, p["model"])
        else:
            raise
    with resp:
        for ev in _iter_sse(resp, deadline):
            if ev.get("error"):
                err = ev["error"]
                raise ModelError(t("ai.call_failed", detail=str(err.get("message") if isinstance(err, dict) else err)[:400]))
            for ch in ev.get("choices") or []:
                text = think.feed((ch.get("delta") or {}).get("content") or "")
                if text:
                    chunks.append(text)
                    if on_delta:
                        on_delta(text)
    rest = think.flush()
    if rest:
        chunks.append(rest)
        if on_delta:
            on_delta(rest)
    return "".join(chunks).strip()


def list_models(p: dict, cfg: "dict | None" = None) -> "list[dict]":
    """Models the channel offers, for the settings dropdown: [{id, name, desc}].
    Asked live, so coco does not go stale when vendors rename their models."""
    typ = p.get("type")
    if typ == "codex-cli":
        return codex_models(cfg)
    if typ == "claude-cli" and not p.get("base_url"):
        return [{"id": "", "name": "default", "desc": ""}] + [
            {"id": a, "name": a, "desc": ""} for a in ("opus", "sonnet", "haiku")]
    key = resolve_api_key(p)
    if typ in ("anthropic", "claude-cli"):
        base = (p.get("base_url") or "https://api.anthropic.com").rstrip("/")
        url, headers = base + "/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        base = (p.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url, headers = base + "/models", ({"Authorization": f"Bearer {key}"} if key else {})
    with _request(url, headers, None, 30) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))
    items = d.get("data") if isinstance(d, dict) else d
    if not isinstance(items, list) and isinstance(d, dict):
        items = d.get("models") or []  # a few servers (Ollama native) use "models"
    out = []
    for m in items or []:
        if isinstance(m, dict):
            mid = str(m.get("id") or m.get("name") or m.get("model") or "")
            if mid:
                out.append({"id": mid, "name": str(m.get("display_name") or mid), "desc": ""})
        elif isinstance(m, str):
            out.append({"id": m, "name": m, "desc": ""})
    out.sort(key=lambda m: m["id"])
    return out


# ---------- entry point ----------

def run_model(prompt: str, *, task: str = "report", profile: "dict | None" = None,
              timeout: int = 900, allowed_tools: "list[str] | None" = None,
              on_delta=None) -> str:
    """Run one prompt through the profile mapped to ``task`` (or an explicit profile).

    ``on_delta(text)`` is called with each streamed fragment; the complete text is returned.
    """
    cfg = load_config()
    p = profile or profile_for_task(task, cfg)
    if allowed_tools and not supports_web(p):
        raise ModelError(t("ai.web_needs_claude"))
    if p["type"] == "anthropic":
        return _run_anthropic(prompt, p, timeout, on_delta)
    if p["type"] == "openai":
        return _run_openai(prompt, p, timeout, on_delta)
    if p["type"] == "codex-cli":
        return _run_codex_cli(prompt, p, cfg, timeout, on_delta)
    return _run_claude_cli(prompt, p, cfg, timeout, allowed_tools, on_delta)


def test_profile(name: str = "primary", profile: "dict | None" = None) -> dict:
    """One tiny round-trip to check the channel works; returns latency and the reply."""
    p = profile or get_profile(name)
    t0 = time.monotonic()
    try:
        reply = run_model("Reply with exactly one word: pong", profile=p, timeout=120)
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "latency_ms": ms, "reply": reply[:80],
                "type": p["type"], "model": p.get("model") or "(default)"}
    except ModelError as e:
        return {"ok": False, "latency_ms": int((time.monotonic() - t0) * 1000),
                "error": str(e), "type": p["type"], "model": p.get("model") or "(default)"}
