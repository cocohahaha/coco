"""Transcription channels: who turns a recording into text.

- ``agy``      Google Antigravity CLI logged in with a Google account (Gemini). No API key; this
               computer only cuts the audio. The audio is sent to Google.
- ``gemini``   the Gemini API with a key from Google AI Studio (same models, for people without agy).
- ``openai``   any OpenAI-compatible ``/audio/transcriptions`` endpoint (OpenAI, Groq, SiliconFlow,
               a transcription server on the office network …).
- ``bailian``  Alibaba Cloud Bailian (DashScope) Paraformer, reachable from mainland China.
- ``local``    Whisper on this computer (mlx-whisper on Apple Silicon, faster-whisper elsewhere).

Cloud channels cut the recording into pieces of about 20 minutes at silences, transcribe the pieces
in parallel and put the timeline back together. Finished pieces are cached in the meeting folder
(``.asr/``), so a run interrupted by a quota or network problem continues where it stopped.

Rules carried over from the agy pipeline that is used in daily work: mp3 input (re-encoded m4a is
rejected as audio/mp4a-latm); a piece blocked by Gemini's content filter is retried on the same
piece with other agy models only, never split further and never sent to another engine; a piece
that still fails becomes a visible gap in the transcript.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import load_config
from .i18n import get_lang, t

CHANNELS = ("agy", "gemini", "openai", "bailian", "local")
CLOUD_CHANNELS = ("agy", "gemini", "openai", "bailian")
ASR_DEFAULTS: dict[str, dict] = {
    "agy": {"bin": "agy", "model": "gemini-3.8-flash-medium",
            "rescue": ["gemini-3.8-flash-high", "gemini-3.8-flash-low"], "workers": 3},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-3.8-flash",
               "api_key": "", "api_key_env": "GEMINI_API_KEY", "workers": 3},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-transcribe",
               "api_key": "", "api_key_env": "OPENAI_API_KEY", "workers": 3},
    "bailian": {"model": "paraformer-realtime-v2", "api_key": "", "api_key_env": "DASHSCOPE_API_KEY",
                "workers": 2},
}
CHUNK_SECONDS = 1200    # target piece length
LOOK_BACK, LOOK_AHEAD, MIN_TAIL = 90, 30, 240
STAMP = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*")
FATAL = ("credits balance is too low", "AI credits", "exhausted daily quota", "Individual quota reached")
TRANSIENT = ("RESOURCE_EXHAUSTED", "429", "rate limit", "Rate limit", "quota", "UNAVAILABLE", "503",
             "timed out", "Timeout", "timeout")
FILTERED = ("blocked by Gemini's filters", "PROHIBITED_CONTENT", "SAFETY", "blockReason")


class ASRError(RuntimeError):
    """A channel could not produce a transcript (message is shown to the user)."""


class QuotaExhausted(ASRError):
    """The account ran out of quota: stop every piece, the next run resumes from the cache."""


# ---------- configuration ----------

def channel_cfg(name: str, cfg: "dict | None" = None) -> dict:
    cfg = cfg or load_config()
    out = dict(ASR_DEFAULTS.get(name, {}))
    out.update({k: v for k, v in ((cfg.get("asr") or {}).get(name) or {}).items() if v not in (None,)})
    return out


def api_key(c: dict) -> str:
    if c.get("api_key"):
        return str(c["api_key"])
    env = c.get("api_key_env")
    return os.environ.get(str(env), "") if env else ""


def agy_bin(cfg: "dict | None" = None) -> str:
    raw = channel_cfg("agy", cfg).get("bin") or "agy"
    hit = shutil.which(raw)
    if hit:
        return hit
    if raw == "agy":  # only for the default name; an explicit path stays as typed
        for cand in (Path.home() / ".local" / "bin" / "agy", Path("/opt/homebrew/bin/agy"),
                     Path("/usr/local/bin/agy")):
            if cand.exists():
                return str(cand)
    return raw


def agy_found(cfg: "dict | None" = None) -> bool:
    b = agy_bin(cfg)
    return Path(b).exists() or shutil.which(b) is not None


def agy_logged_in() -> bool:
    """agy keeps its Google login in two files; an expired scope only shows on a real call (Test)."""
    g = Path(os.environ.get("GEMINI_HOME") or Path.home() / ".gemini")
    return (g / "oauth_creds.json").exists()


def channel_ready(name: str, cfg: "dict | None" = None) -> bool:
    cfg = cfg or load_config()
    if name == "agy":
        return agy_found(cfg) and agy_logged_in()
    if name in ("gemini", "openai", "bailian"):
        c = channel_cfg(name, cfg)
        if name == "openai" and _private_host(c.get("base_url", "")):
            return True
        return bool(api_key(c))
    if name == "local":
        from .transcriber import detect_backend
        return bool(detect_backend(cfg))
    return False


def _private_host(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "") if url else ""
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith(("10.", "192.168.")) or host.endswith(".local")


def resolve_channel(cfg: "dict | None" = None) -> str:
    """Channel used for new recordings. ``auto`` keeps audio on this computer when it can transcribe
    at a usable speed; otherwise the first ready cloud channel (agy, then keys); '' = none."""
    cfg = cfg or load_config()
    want = str(cfg.get("asr_channel") or "auto").strip().lower()
    if want == "none":
        return ""
    if want in CHANNELS:
        return want
    from .transcriber import detect_device
    device = detect_device(cfg)
    if device in ("mlx", "cuda"):
        return "local"
    for name in CLOUD_CHANNELS:
        if channel_ready(name, cfg):
            return name
    return "local" if device else ""


def status(cfg: "dict | None" = None) -> dict:
    """Everything the settings page shows about transcription channels."""
    cfg = cfg or load_config()
    from .transcriber import detect_backend, detect_device
    chans = {}
    for name in CHANNELS:
        c = channel_cfg(name, cfg)
        chans[name] = {"ready": channel_ready(name, cfg), "model": c.get("model", ""),
                       "base_url": c.get("base_url", ""), "has_key": bool(api_key(c)),
                       "api_key_set": bool(c.get("api_key")), "api_key_env": c.get("api_key_env", ""),
                       "cloud": name in CLOUD_CHANNELS}
    chans["agy"]["found"] = agy_found(cfg)
    chans["agy"]["login"] = agy_logged_in()
    chans["local"].update(engine=detect_backend(cfg), device=detect_device(cfg),
                          model=cfg.get("whisper_model", "turbo"))
    return {"setting": str(cfg.get("asr_channel") or "auto"), "active": resolve_channel(cfg),
            "channels": chans}


# ---------- audio helpers ----------

def ffmpeg() -> str:
    from .sysenv import ensure_ffmpeg
    exe = ensure_ffmpeg()
    if not exe:
        raise ASRError(t("asr.no_ffmpeg"))
    return exe


def probe_duration(path: Path) -> float:
    r = subprocess.run([ffmpeg(), "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _silence_midpoints(src: Path) -> list[float]:
    r = subprocess.run([ffmpeg(), "-nostdin", "-hide_banner", "-i", str(src), "-vn", "-ac", "1",
                        "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(s + e) / 2 for s, e in zip(starts, ends)]


def cut_points(total: float, mids: list[float], target: int = CHUNK_SECONDS) -> list[float]:
    """Cut every ~target seconds, at the silence closest to the target point."""
    cuts, at = [], float(target)
    while at < total - MIN_TAIL:
        near = [m for m in mids if at - LOOK_BACK <= m <= at + LOOK_AHEAD]
        cut = min(near, key=lambda m: abs(m - at)) if near else at
        cuts.append(round(cut, 2))
        at = cut + target
    return cuts


def split_audio(src: Path, workdir: Path, target: int = CHUNK_SECONDS) -> list[dict]:
    """16 kHz mono 32 kbps mp3 pieces → [{file, offset, duration}] (cached in workdir/chunks.json)."""
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = workdir / "chunks.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text(encoding="utf-8"))["chunks"]
        except (ValueError, KeyError):
            pass
    total = probe_duration(src)
    cuts = cut_points(total, _silence_midpoints(src), target) if total > target + MIN_TAIL else []
    cmd = [ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-vn",
           "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k"]
    if cuts:
        cmd += ["-f", "segment", "-segment_times", ",".join(map(str, cuts)), "-reset_timestamps", "1",
                str(workdir / "%02d.mp3")]
    else:
        cmd += [str(workdir / "00.mp3")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise ASRError(t("asr.convert_failed", detail=(r.stderr or "")[-300:]))
    chunks, offset = [], 0.0
    for f in sorted(workdir.glob("*.mp3")):
        d = probe_duration(f)
        chunks.append({"file": f.name, "offset": round(offset, 2), "duration": round(d, 2)})
        offset += d
    if not chunks:
        raise ASRError(t("asr.convert_failed", detail="no audio"))
    manifest.write_text(json.dumps({"total": round(total, 2), "chunks": chunks}, ensure_ascii=False),
                        encoding="utf-8")
    return chunks


# ---------- prompt / parsing for audio-capable language models ----------

def prompt_terms(limit: int = 15) -> str:
    """A short list of names / terms for the model: entries with known mishearings first.
    Long lists make models insert names they did not hear, so the list stays short."""
    try:
        from .glossary import parse_glossary
        g = parse_glossary()
    except Exception:
        return ""
    first = [e["term"] for e in g["terms"] + g["names"] if e["wrong"]]
    rest = [e["term"] for e in g["names"] + g["terms"] if not e["wrong"]]
    out: list[str] = []
    for term in first + rest:
        if term not in out:
            out.append(term)
        if len(out) >= limit:
            break
    return "、".join(out)


def audio_prompt(title: str, date: str, n: int, total: int, minutes: int, path: "Path | None" = None,
                 lang: "str | None" = None) -> str:
    cfg = load_config()
    zh = (lang or get_lang()).lower().startswith("zh")
    context = str(cfg.get("asr_context") or "").strip()
    terms = prompt_terms()
    if zh:
        head = f"用 view_file 打开 {path}（音频），逐字转录全部内容。" if path else "逐字转录这段音频的全部内容。"
        lines = [head, f"背景：录音「{title}」（{date or '日期未知'}）的第 {n}/{total} 段，约 {minutes} 分钟。"
                 + (f"{context}" if context else "")]
        if terms:
            lines.append(f"可能出现的专名（只在确实听到时使用这些写法）：{terms}。")
        lines += ["要求：",
                  "1. 保留原语言，中英混说照原样写，不翻译。",
                  "2. 加标点：中文用中文标点，英文用英文标点。",
                  "3. 说话人换人时另起一段。不写说话人名字或标签。",
                  "4. 每段开头写 [mm:ss] 时间戳，从本段音频开头算。",
                  "5. 听不清写（听不清），长时间无人说话写（静音）或（环境声）。",
                  "6. 不总结、不润色、不删口头语中的实义内容；明显的重复口吃可以略去。",
                  "7. 不用破折号，话说一半或停顿用「……」或逗号。",
                  "只输出转录正文，第一行就以时间戳开头。不要前言、说明或总结，不要读其他文件，不要运行命令。"]
    else:
        head = (f"Open {path} (an audio file) with view_file and transcribe everything said, verbatim."
                if path else "Transcribe everything said in this audio, verbatim.")
        lines = [head, f"Context: recording \"{title}\" ({date or 'date unknown'}), part {n} of {total}, "
                       f"about {minutes} minutes." + (f" {context}" if context else "")]
        if terms:
            lines.append(f"Names and terms that may occur (use these spellings only when you hear them): "
                         f"{terms.replace('、', ', ')}.")
        lines += ["Rules:",
                  "1. Keep the original language; write mixed-language speech as spoken, never translate.",
                  "2. Punctuate (Chinese punctuation for Chinese, English punctuation for English).",
                  "3. Start a new paragraph when the speaker changes. Do not write speaker names or labels.",
                  "4. Begin every paragraph with a [mm:ss] timestamp counted from the start of this audio.",
                  "5. Write (inaudible) for unclear speech, (silence) or (background noise) for long gaps.",
                  "6. Do not summarise, polish or drop meaningful words; obvious stutters may be omitted.",
                  "7. Do not use dashes; use \"…\" or a comma for pauses.",
                  "Output only the transcript, starting with a timestamp on the first line. No preamble, "
                  "notes or summary; do not read other files or run commands."]
    return "\n".join(lines)


_QUIET = ("静音", "环境声", "silence", "background noise", "听不清", "inaudible")


def valid_transcript(text: str, duration: float) -> bool:
    body = text.strip()
    if any(k in body for k in FILTERED[:1]):
        return False
    if not STAMP.match(body):
        return False
    stamps = len(re.findall(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]", body, re.M))
    # a spoken piece has at least one timestamp every 3 minutes; a silent piece may have only one
    return stamps >= 1 and (stamps >= duration / 180 or any(k in body for k in _QUIET))


def extract_transcript(resp: str, duration: float) -> "str | None":
    """Strip a code fence; when the model's reasoning leaks into the reply, take the last part that
    starts at an opening timestamp and passes validation."""
    body = re.sub(r"^```\w*\n|\n```\s*$", "", (resp or "").strip())
    if valid_transcript(body, duration):
        return body
    for m in reversed(list(re.finditer(r"^\[00:0\d\]", body, re.M))):
        tail = re.sub(r"\n```\s*$", "", body[m.start():].strip())
        if valid_transcript(tail, duration):
            return tail
    return None


def parse_paragraphs(text: str) -> list[list]:
    """'[mm:ss] text' paragraphs → [[seconds, text]]; lines without a stamp join the paragraph above."""
    paras: list[list] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = STAMP.match(line)
        if m:
            a, b, c = m.groups()
            sec = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
            paras.append([sec, line[m.end():].strip()])
        elif paras:
            paras[-1][1] = (paras[-1][1] + ("" if _cjk_end(paras[-1][1]) else " ") + line).strip()
        else:
            paras.append([0, line])
    return [p for p in paras if p[1]]


def _cjk_end(s: str) -> bool:
    return bool(s) and bool(re.match(r"[　-鿿＀-￯]", s[-1]))


def assemble(pieces: "list[tuple[dict, str]]") -> list[dict]:
    """[(chunk, text)] → segments on the recording's timeline.

    Audio models write approximate timestamps that drift late (a 3-minute piece ending at [03:43]);
    when a piece's last stamp passes its duration, every stamp of that piece is scaled back."""
    segs: list[dict] = []
    last = 0.0
    for chunk, text in pieces:
        paras = parse_paragraphs(text)
        dur = float(chunk.get("duration") or 0)
        top = max((p[0] for p in paras), default=0)
        scale = (dur * 0.98 / top) if dur and top > dur * 1.02 else 1.0
        cend = chunk["offset"] + dur
        for sec, para in paras:
            at = max(chunk["offset"] + min(sec * scale, dur or sec), last)
            last = at
            seg = {"start": round(at, 2), "text": para, "_cend": cend}
            if chunk.get("gap"):
                seg["gap"] = True
            segs.append(seg)
    for i, s in enumerate(segs):
        cend = s.pop("_cend")
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) and segs[i + 1]["start"] <= cend else cend
        s["end"] = round(max(nxt, s["start"] + 0.5), 2)
    return segs


def gap_text(minutes: int, why: str) -> str:
    return "[00:00] " + t("asr.gap", minutes=minutes, why=why)


# ---------- runners per piece ----------

def _agy_piece(path: Path, prompt: str, model: str, cfg: dict) -> "tuple[str, str]":
    """One agy call → (status, text|detail). status: ok | filtered | fatal | transient | failed."""
    try:
        proc = subprocess.run(
            [agy_bin(cfg), "-p", prompt, "--model", model, "--sandbox", "--output-format", "json",
             "--print-timeout", "900s"],
            cwd=str(path.parent), capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=960)
    except FileNotFoundError:
        return "fatal", t("asr.agy_missing")
    except subprocess.TimeoutExpired:
        return "transient", "timeout"
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    resp = data.get("response") or ""
    blob = resp + str(data.get("error") or "") + (proc.stderr or "")
    if any(k in blob for k in FATAL):
        return "fatal", t("asr.quota")
    if data.get("status") == "SUCCESS":
        return "ok", resp
    if FILTERED[0] in blob:
        return "filtered", blob.strip()[-160:]
    if any(k in blob for k in TRANSIENT):
        return "transient", blob.strip()[-160:]
    if "login" in blob.lower() or "auth" in blob.lower():
        return "fatal", t("asr.agy_login")
    return "failed", blob.strip()[-200:] or f"exit {proc.returncode}"


def _gemini_piece(path: Path, prompt: str, model: str, c: dict) -> "tuple[str, str]":
    key = api_key(c)
    if not key:
        return "fatal", t("asr.need_key", channel="Gemini")
    base = str(c.get("base_url") or ASR_DEFAULTS["gemini"]["base_url"]).rstrip("/")
    body = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "audio/mp3", "data": base64.b64encode(path.read_bytes()).decode()}},
        {"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
    req = urllib.request.Request(f"{base}/models/{model}:generateContent",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 429 or e.code >= 500:
            return "transient", f"HTTP {e.code} {detail}"
        if e.code in (401, 403):
            return "fatal", t("asr.bad_key", channel="Gemini", detail=detail[:160])
        return "failed", f"HTTP {e.code} {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return "transient", str(e)
    if (data.get("promptFeedback") or {}).get("blockReason"):
        return "filtered", str(data["promptFeedback"]["blockReason"])
    cands = data.get("candidates") or []
    if not cands:
        return "failed", json.dumps(data)[:200]
    if cands[0].get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST"):
        return "filtered", cands[0]["finishReason"]
    text = "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts") or [])
    return "ok", text


def _multipart(fields: dict, file_field: str, path: Path) -> "tuple[bytes, str]":
    boundary = "coco" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{path.name}\"\r\n"
            "Content-Type: audio/mpeg\r\n\r\n").encode()
    out += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _openai_piece(path: Path, duration: float, c: dict, language: "str | None") -> "tuple[str, object]":
    """OpenAI-compatible transcription → ('ok', [segments relative to the piece]) or an error."""
    key = api_key(c)
    base = str(c.get("base_url") or ASR_DEFAULTS["openai"]["base_url"]).rstrip("/")
    if not key and not _private_host(base):
        return "fatal", t("asr.need_key", channel="OpenAI")
    model = str(c.get("model") or ASR_DEFAULTS["openai"]["model"])
    fmt = "verbose_json" if "whisper" in model.lower() or "sensevoice" in model.lower() else "json"
    for attempt_fmt in (fmt, "json"):
        fields = {"model": model, "response_format": attempt_fmt}
        if language:
            fields["language"] = language
        terms = prompt_terms(10)
        if terms:
            fields["prompt"] = terms.replace("、", ", ")
        body, ctype = _multipart(fields, "file", path)
        headers = {"Content-Type": ctype}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(f"{base}/audio/transcriptions", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and attempt_fmt != "json" and "response_format" in detail:
                continue
            if e.code == 429 or e.code >= 500:
                return "transient", f"HTTP {e.code} {detail}"
            if e.code in (401, 403):
                return "fatal", t("asr.bad_key", channel="OpenAI", detail=detail[:160])
            return "failed", f"HTTP {e.code} {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return "transient", str(e)
        segs = [{"start": float(s.get("start") or 0), "text": str(s.get("text") or "").strip()}
                for s in data.get("segments") or [] if str(s.get("text") or "").strip()]
        if not segs and str(data.get("text") or "").strip():
            segs = _split_text(str(data["text"]).strip(), duration)
        return "ok", segs
    return "failed", "response_format"


def _split_text(text: str, duration: float) -> list[dict]:
    """Plain text without timings → sentences spread evenly over the piece (approximate stamps)."""
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?.])\s*", text) if p.strip()]
    paras, cur = [], ""
    for p in parts:
        cur += p if _cjk_end(cur) or not cur else " " + p
        if len(cur) >= 120:
            paras.append(cur)
            cur = ""
    if cur:
        paras.append(cur)
    total = sum(len(p) for p in paras) or 1
    out, pos = [], 0
    for p in paras:
        out.append({"start": round(duration * pos / total, 2), "text": p})
        pos += len(p)
    return out


def _bailian_piece(path: Path, c: dict, language: "str | None") -> "tuple[str, object]":
    key = api_key(c)
    if not key:
        return "fatal", t("asr.need_key", channel="Bailian")
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except ImportError:
        return "fatal", t("asr.need_dashscope")
    dashscope.api_key = key
    hints = [language] if language in ("zh", "en", "ja", "yue", "ko") else ["zh", "en"]
    kwargs = {"language_hints": hints}
    if c.get("vocabulary_id"):
        kwargs["vocabulary_id"] = c["vocabulary_id"]
    try:
        rec = Recognition(model=str(c.get("model") or "paraformer-realtime-v2"), format="mp3",
                          sample_rate=16000, callback=None, **kwargs)
        result = rec.call(str(path))
    except Exception as e:  # network / SDK errors
        return "transient", str(e)[:200]
    if getattr(result, "status_code", None) != 200:
        msg = str(getattr(result, "message", "") or "error")
        return ("transient" if "Throttl" in msg or "429" in msg else "failed"), msg[:200]
    segs = [{"start": round((s.get("begin_time") or 0) / 1000, 2), "text": (s.get("text") or "").strip()}
            for s in result.get_sentence() or [] if (s.get("text") or "").strip()]
    return "ok", segs


# ---------- orchestration ----------

def _backoff(status_: str, attempt: int) -> float:
    """Seconds before the next try: long for rate limits, shorter otherwise (tests set COCO_FAST_RETRY)."""
    base = (90 if status_ == "transient" else 20) * attempt + random.randint(0, 20)
    return base / 1000 if os.environ.get("COCO_FAST_RETRY") else base


def transcribe_cloud(audio: Path, channel: str, workdir: Path, title: str = "", date: str = "",
                     language: "str | None" = None, progress=lambda msg: None) -> dict:
    """Transcribe with a cloud channel → {segments, text, duration, model, language, channel, gaps}."""
    cfg = load_config()
    c = channel_cfg(channel, cfg)
    progress(t("asr.preparing"))
    chunks = split_audio(audio, workdir)
    total = len(chunks)
    done = {"n": sum(1 for ch in chunks if (workdir / (Path(ch["file"]).stem + ".txt")).exists())}
    stop = threading.Event()
    errors: list[str] = []
    lock = threading.Lock()
    lang = None if not language or language == "auto" else language
    model = str(c.get("model") or "")

    def note():
        progress(t("asr.progress", channel=label(channel), done=done["n"], total=total))

    def run(i: int) -> None:
        ch = chunks[i]
        piece = workdir / ch["file"]
        out = workdir / (piece.stem + ".txt")
        if out.exists() or stop.is_set():
            return
        minutes = max(1, round(ch["duration"] / 60))
        status_, detail = "", ""
        models = [model] if channel != "agy" else [model] + list(c.get("rescue") or [])
        max_tries = 4
        for m in models:
            per_model = max_tries if m == model else 2
            for attempt in range(1, per_model + 1):
                if stop.is_set():
                    return
                if channel in ("agy", "gemini"):
                    prompt = audio_prompt(title, date, i + 1, total, minutes,
                                          path=piece if channel == "agy" else None)
                    status_, detail = (_agy_piece(piece, prompt, m, cfg) if channel == "agy"
                                       else _gemini_piece(piece, prompt, m, c))
                    if status_ == "ok":
                        body = extract_transcript(str(detail), ch["duration"])
                        if body:
                            out.write_text(body.strip() + "\n", encoding="utf-8")
                            with lock:
                                done["n"] += 1
                            note()
                            return
                        status_, detail = "failed", t("asr.bad_output")
                else:
                    status_, detail = (_openai_piece(piece, ch["duration"], c, lang) if channel == "openai"
                                       else _bailian_piece(piece, c, lang))
                    if status_ == "ok":
                        segs = detail if isinstance(detail, list) else []
                        text = "\n".join(f"[{int(s['start']) // 60:02d}:{int(s['start']) % 60:02d}] {s['text']}"
                                         for s in segs) or f"[00:00] {t('asr.empty_piece')}"
                        out.write_text(text + "\n", encoding="utf-8")
                        with lock:
                            done["n"] += 1
                        note()
                        return
                if status_ == "fatal":
                    with lock:
                        errors.append(str(detail))
                    stop.set()
                    return
                if status_ == "filtered":
                    break  # same piece, next agy model (no further splitting, no other engine)
                time.sleep(_backoff(status_, attempt))
            if status_ != "filtered":
                break
        if status_ == "filtered":
            out.write_text(gap_text(minutes, t("asr.gap_filtered")) + "\n", encoding="utf-8")
            out.with_suffix(".gap").write_text(str(detail), encoding="utf-8")
            with lock:
                done["n"] += 1
            note()
            return
        with lock:
            errors.append(f"{ch['file']}: {detail}")

    note()
    workers = max(1, min(int(c.get("workers") or 3), 6))
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(run, range(total)))
    if errors:
        quota = any(t("asr.quota") in e for e in errors)
        msg = errors[0] if quota or len(errors) == 1 else f"{errors[0]} (+{len(errors) - 1})"
        raise (QuotaExhausted if quota else ASRError)(t("asr.failed_pieces", done=done["n"], total=total, detail=msg))
    pieces = []
    for ch in chunks:
        text = (workdir / (Path(ch["file"]).stem + ".txt")).read_text(encoding="utf-8")
        ch = dict(ch, gap=(workdir / (Path(ch["file"]).stem + ".gap")).exists())
        pieces.append((ch, text))
    segs = assemble(pieces)
    gaps = sum(1 for ch, _ in pieces if ch.get("gap"))
    duration = sum(ch["duration"] for ch in chunks)
    full = "\n".join(s["text"] for s in segs)
    return {"segments": segs, "text": full, "duration": duration, "model": model or channel,
            "language": detect_text_language(full), "channel": channel, "gaps": gaps,
            "backend": channel, "filtered": 0, "filtered_examples": []}


def detect_text_language(text: str) -> str:
    cjk = len(re.findall(r"[㐀-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]+", text))
    if not cjk and not latin:
        return "auto"
    zh_units = cjk / 2  # two characters ≈ one word
    if zh_units >= latin:
        return "zh+en" if latin >= 30 and latin / zh_units > 0.4 else "zh"
    return "zh+en" if cjk >= 60 and zh_units / latin > 0.4 else "en"


def label(channel: str) -> str:
    return t(f"asr.channel.{channel}") if channel in CHANNELS else channel


def cleanup(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


def test_channel(channel: str, cfg: "dict | None" = None) -> dict:
    """Cheap readiness check without audio: agy answers a two-word prompt, APIs list their models."""
    cfg = cfg or load_config()
    t0 = time.time()
    try:
        if channel == "agy":
            if not agy_found(cfg):
                return {"ok": False, "detail": t("asr.agy_missing")}
            r = subprocess.run([agy_bin(cfg), "-p", "Reply with the single word: ready", "--output-format", "json",
                                "--model", channel_cfg("agy", cfg).get("model") or "gemini-3.8-flash-low",
                                "--print-timeout", "90s"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", stdin=subprocess.DEVNULL, timeout=120, cwd=str(Path.home()))
            data = json.loads(r.stdout or "{}") if (r.stdout or "").strip().startswith("{") else {}
            ok = data.get("status") == "SUCCESS"
            return {"ok": ok, "ms": int((time.time() - t0) * 1000),
                    "detail": "" if ok else ((r.stderr or r.stdout or "").strip()[-200:] or t("asr.agy_login"))}
        if channel == "local":
            ok = channel_ready("local", cfg)
            return {"ok": ok, "ms": 0, "detail": "" if ok else t("transcribe.no_engine")}
        if channel == "gemini":
            c = channel_cfg("gemini", cfg)
            base = str(c.get("base_url")).rstrip("/")
            req = urllib.request.Request(f"{base}/models", headers={"x-goog-api-key": api_key(c)})
        elif channel == "openai":
            c = channel_cfg("openai", cfg)
            base = str(c.get("base_url")).rstrip("/")
            req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {api_key(c)}"})
        elif channel == "bailian":
            c = channel_cfg("bailian", cfg)
            try:
                import dashscope  # noqa: F401
            except ImportError:
                return {"ok": False, "detail": t("asr.need_dashscope")}
            return {"ok": bool(api_key(c)), "ms": 0, "detail": "" if api_key(c) else t("asr.need_key", channel="Bailian")}
        else:
            return {"ok": False, "detail": channel}
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "detail": ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:160]}"}
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": False, "detail": str(e)[:200]}


def agy_models(cfg: "dict | None" = None) -> list[str]:
    try:
        r = subprocess.run([agy_bin(cfg), "models"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=40, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.split("\t")[0].strip() for l in r.stdout.splitlines()
            if l.strip().startswith("gemini") and "\t" in l]
