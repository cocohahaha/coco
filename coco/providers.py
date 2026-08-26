"""Model providers: how coco talks to a language model.

Three provider types, all streaming:

- ``claude-cli``  the local ``claude`` command (Claude Code) – the default, no API key
                  needed when the CLI is logged in. With a ``base_url`` + ``api_key`` the
                  same CLI can be pointed at any Anthropic-compatible endpoint
                  (e.g. DeepSeek's ``https://api.deepseek.com/anthropic``).
- ``anthropic``   direct HTTPS to an Anthropic-compatible ``/v1/messages`` endpoint
                  (Anthropic, DeepSeek /anthropic, …) – standard library only.
- ``openai``      direct HTTPS to an OpenAI-compatible ``/chat/completions`` endpoint
                  (DeepSeek, OpenAI, Ollama / LM Studio on localhost, …).

Two profiles: ``primary`` (reports, insights, prep, chat) and ``fast`` (background
work: long-term memory, glossary, name fixing). Each task is mapped to a profile in
``ai_tasks``; when ``fast`` is not configured it simply equals ``primary``.
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
    pass


PROFILE_TYPES = ("claude-cli", "anthropic", "openai")
PROFILE_NAMES = ("primary", "fast")
PROFILE_DEFAULTS = {
    "type": "claude-cli",
    "model": "",             # empty = provider default (claude-cli: the CLI's configured model)
    "base_url": "",          # claude-cli / anthropic: Anthropic-compatible root; openai: incl. /v1 if needed
    "api_key": "",           # literal key (stored in coco.config.json, which is git-ignored)
    "api_key_env": "",       # or the name of an environment variable holding the key
    "max_context_chars": 0,  # 0 = default (400k chars); lower it for small-context models
    "max_tokens": 16000,     # anthropic / openai output cap
    "extra_args": [],        # claude-cli only: appended to the command line
}
TASK_PROFILE_DEFAULTS = {
    "report": "primary", "ask": "primary", "track": "primary", "prep": "primary",
    "brief": "primary", "weekly": "primary",
    "memory": "fast", "glossary": "fast", "namefix": "fast",
}
# Ready-made settings for the settings dialog / `coco ai preset`. Model names are the
# ones documented by each vendor at the time of writing (August 2026); check the
# vendor's docs if a request is rejected.
PRESETS = {
    "claude-cli": {"type": "claude-cli", "base_url": "", "model": "", "api_key_env": ""},
    "claude-cli-fast": {"type": "claude-cli", "base_url": "", "model": "haiku", "api_key_env": ""},
    "deepseek-anthropic": {"type": "anthropic", "base_url": "https://api.deepseek.com/anthropic",
                           "model": "deepseek-v4-pro", "api_key_env": "DEEPSEEK_API_KEY"},
    "deepseek-openai": {"type": "openai", "base_url": "https://api.deepseek.com",
                        "model": "deepseek-v4-flash", "api_key_env": "DEEPSEEK_API_KEY"},
    "deepseek-via-claude-cli": {"type": "claude-cli", "base_url": "https://api.deepseek.com/anthropic",
                                "model": "deepseek-v4-pro", "api_key_env": "DEEPSEEK_API_KEY"},
    "openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5",
               "api_key_env": "OPENAI_API_KEY"},
    "ollama": {"type": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen3:14b",
               "api_key_env": "", "max_context_chars": 60000},
}
DEFAULT_CONTEXT_CHARS = 400_000


def get_profile(name: str = "primary", cfg: "dict | None" = None) -> dict:
    cfg = cfg or load_config()
    profiles = cfg.get("ai_profiles") or {}
    if name != "primary" and not profiles.get(name):
        return get_profile("primary", cfg)  # unset secondary profile = same as primary
    p = dict(PROFILE_DEFAULTS)
    p.update({k: v for k, v in (profiles.get(name) or {}).items() if k in PROFILE_DEFAULTS})
    if p["type"] not in PROFILE_TYPES:
        p["type"] = "claude-cli"
    p["name"] = name
    return p


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
        return int(p.get("max_context_chars") or 0) or DEFAULT_CONTEXT_CHARS
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_CHARS


def supports_web(p: dict) -> bool:
    """Only Claude Code's built-in WebSearch tool is available; HTTP providers cannot browse."""
    return p["type"] == "claude-cli" and not p.get("base_url")


def public_profile(p: dict) -> dict:
    """Profile as shown to the UI: never returns the key itself."""
    return {**{k: p.get(k) for k in PROFILE_DEFAULTS if k != "api_key"},
            "has_key": bool(resolve_api_key(p)), "api_key_set": bool(p.get("api_key")),
            "name": p.get("name", "")}


# ---------- claude CLI ----------

def resolve_claude_bin(cfg: "dict | None" = None) -> str:
    """Locate the claude executable. On Windows npm installs claude.cmd and the official
    installer claude.exe; CreateProcess does not consult PATHEXT, so resolve through
    shutil.which. Unknown → returned as-is so subprocess raises FileNotFoundError."""
    cfg = cfg or load_config()
    raw = cfg.get("claude_bin") or "claude"
    return shutil.which(raw) or raw


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


# ---------- HTTP providers (standard library, streaming SSE) ----------

def _post_stream(url: str, headers: dict, body: dict, timeout: int):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        return urllib.request.urlopen(req, timeout=min(timeout, 120))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise ModelError(t("ai.http_error", status=e.code, detail=detail))
    except urllib.error.URLError as e:
        raise ModelError(t("ai.connect_failed", url=url, detail=str(e.reason)[:200]))
    except (OSError, http.client.HTTPException) as e:  # reset / disconnect / timeout
        raise ModelError(t("ai.connect_failed", url=url, detail=str(e)[:200]))


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
    with _post_stream(base + "/v1/messages", headers, body, timeout) as resp:
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


def _run_openai(prompt: str, p: dict, timeout: int, on_delta) -> str:
    base = (p.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    if not p.get("model"):
        raise ModelError(t("ai.model_required", profile=p.get("name", "")))
    key = resolve_api_key(p)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": p["model"], "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    with _post_stream(base + "/chat/completions", headers, body, timeout) as resp:
        for ev in _iter_sse(resp, deadline):
            if ev.get("error"):
                err = ev["error"]
                raise ModelError(t("ai.call_failed", detail=str(err.get("message") if isinstance(err, dict) else err)[:400]))
            for ch in ev.get("choices") or []:
                text = (ch.get("delta") or {}).get("content")
                if text:
                    chunks.append(text)
                    if on_delta:
                        on_delta(text)
    return "".join(chunks).strip()


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
