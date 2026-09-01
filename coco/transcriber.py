"""Local Whisper transcription.

Two engines, one set of model aliases (turbo / large / tiny):
- mlx-whisper: Apple Silicon native acceleration (macOS arm64)
- faster-whisper: CTranslate2 on Windows / Linux / Intel Mac; CUDA with an NVIDIA GPU, else CPU int8

Machines with neither engine (or ``transcribe_backend=none``) cannot transcribe audio,
but importing transcripts (txt / docx / pdf / srt / vtt / json …) and every analysis
feature keep working.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import platform
import sys
from pathlib import Path

import re

from .config import load_config, resolve_model_repo, setup_hf_endpoint
from .i18n import sections, t
from .library import Meeting, participants_line


class TranscriberUnavailable(RuntimeError):
    """No usable transcription engine on this machine."""


def no_engine_hint() -> str:
    return t("transcribe.no_engine")


# ---------- hallucination filter ----------
# Whisper invents text on silence, music and noise: YouTube-style greetings, subtitle credits,
# repeated one-word segments, and it echoes the initial prompt. These rules remove the known
# shapes; they are conservative on purpose so that short real utterances ("对", "ok") survive.
HALLUCINATION_PATTERNS = [re.compile(p, re.I) for p in (
    r"welcome back to (my|the) channel", r"thanks? (for|4) watching", r"takk for watching",
    r"please (like|subscribe)", r"don'?t forget to subscribe", r"see you (in the )?next (video|time)",
    r"like,? share,? (and )?subscribe", r"subtitles? by", r"amara\.org", r"www\.\S+\.(org|com)",
    r"untertitel(ung)? (von|der|im auftrag)", r"sous-titr(es|age) (par|réalisés)", r"subtítulos (por|realizados)",
    r"ご視聴ありがとう", r"チャンネル登録", r"시청해?\s*주셔서 감사", r"구독",
    r"请不吝点赞", r"点赞[、 ]?订阅", r"订阅[、 ]?转发", r"打赏支持", r"明镜与?点点", r"明镜.*栏目",
    r"字幕志愿者", r"字幕由.*提供", r"中文字幕", r"感谢(观看|收看|您的观看)", r"谢谢(观看|收看)",
    r"优优独播剧场", r"^\s*字幕\s*$",
)]
# plausible as real speech, so only removed when the model itself rates the window as silence
WEAK_PATTERNS = [re.compile(p, re.I) for p in (
    r"^(谢谢大家|谢谢观看|感谢大家|谢谢|再见)[。！!]?$", r"^(thank you|thanks|bye|goodbye|okay|ok)[.!]*$",
)]
_PUNCT_ONLY = re.compile(r"^[\s!！.。?？…\-—–,，、;；:：'\"“”‘’()（）\[\]【】·•*~～]*$")


def _norm(text: str) -> str:
    return re.sub(r"[\s!！.。?？…\-—–,，、;；:：'\"“”‘’()（）\[\]【】·•*~～]+", "", text).lower()


def is_hallucination(text: str, prompt: str = "", no_speech: "float | None" = None,
                     avg_logprob: "float | None" = None) -> str:
    """Return the rule that flags this segment text as a hallucination, or '' when it looks fine."""
    t = text.strip()
    if not t or _PUNCT_ONLY.match(t):
        return "punctuation"
    n = _norm(t)
    if prompt and len(n) >= 4:
        pn = _norm(prompt)
        if n in pn or (len(n) >= 12 and pn in n):
            return "prompt-leak"
    for rx in HALLUCINATION_PATTERNS:
        if rx.search(t):
            return "phrase"
    if no_speech is not None and no_speech > 0.5:
        for rx in WEAK_PATTERNS:
            if rx.search(t):
                return "phrase"
    # a short "utterance" inside a window the model itself rates as silence; CJK packs a whole
    # sentence into a dozen characters, so the length cap is tighter there
    short = len(n) <= (6 if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", n) else 12)
    if no_speech is not None and no_speech > 0.85 and short:
        return "silence"
    if avg_logprob is not None and no_speech is not None and no_speech > 0.6 and avg_logprob < -1.0:
        return "silence"
    return ""


def clean_segments(segments: list[dict], prompt: str = "") -> "tuple[list[dict], list[dict]]":
    """Drop hallucinated segments and collapse runs of ≥3 identical short segments.

    Returns (kept, removed); removed items carry a 'reason'.
    """
    kept: list[dict] = []
    removed: list[dict] = []
    run_text, run_start = None, -1
    for s in segments:
        text = (s.get("text") or "").strip()
        why = is_hallucination(text, prompt, s.get("no_speech_prob"), s.get("avg_logprob"))
        if why:
            removed.append({**s, "reason": why})
            continue
        key = _norm(text)
        if key and key == run_text:
            # identical to the previous kept segment: keep at most 2 in a row (3+ is looping)
            if len(kept) - run_start >= 2:
                removed.append({**s, "reason": "repeat"})
                continue
        else:
            run_text, run_start = key, len(kept)
        kept.append(s)
    # a run of ≥3 of the same one-word segment is noise even after collapsing: drop the survivors
    # when the word is a tiny English filler ("are", "so") and nothing else was said around it
    return kept, removed


def clean_transcript_lines(lines: list[str], prompt: str = "") -> "tuple[list[str], list[str]]":
    """Same rules applied to the '[mm:ss] text' lines of an existing transcript.md body."""
    segs = []
    for l in lines:
        m = re.match(r"^(\[\d+:\d{2}(?::\d{2})?\])\s*(.*)$", l)
        segs.append({"line": l, "text": m.group(2) if m else l, "stamped": bool(m)})
    body = [s for s in segs if s["line"].strip()]
    kept, removed = clean_segments(body, prompt)
    keep_ids = {id(s) for s in kept}
    out = [s["line"] for s in segs if not s["line"].strip() or id(s) in keep_ids]
    return out, [f"{s['line']}  ⟵ {s['reason']}" for s in removed]


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


_PROBE: dict[str, bool] = {}  # module -> importable (probed once per process)


def _has(module: str) -> bool:
    """Really import the engine once: on Windows faster-whisper may be installed but missing
    the VC runtime / CUDA DLLs – find_spec says yes, import fails. Cached."""
    if module in _PROBE:
        return _PROBE[module]
    try:
        if importlib.util.find_spec(module) is None:
            _PROBE[module] = False
            return False
    except (ImportError, ValueError):
        _PROBE[module] = False
        return False
    setup_hf_endpoint()  # huggingface_hub reads HF_ENDPOINT at import time
    try:
        importlib.import_module(module)
        _PROBE[module] = True
    except Exception:  # ImportError / OSError (missing DLL) … all mean "unavailable"
        _PROBE[module] = False
    return _PROBE[module]


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def detect_backend(cfg: dict | None = None) -> str:
    """Engine this machine will use: 'mlx' | 'faster' | '' (none).
    transcribe_backend: auto (platform + installed packages) / mlx / faster / none."""
    cfg = cfg or load_config()
    want = str(cfg.get("transcribe_backend") or "auto").strip().lower()
    if want not in ("auto", "none", "mlx", "faster"):
        want = "auto"  # a typo in the config must not turn the whole service into a 500
    if want == "none":
        return ""
    if want == "mlx":
        return "mlx" if _has("mlx_whisper") else ""
    if want == "faster":
        return "faster" if _has("faster_whisper") else ""
    order = ["mlx_whisper", "faster_whisper"] if is_apple_silicon() else ["faster_whisper", "mlx_whisper"]
    for mod in order:
        if _has(mod):
            return "mlx" if mod == "mlx_whisper" else "faster"
    return ""


_DEVICE: dict[str, str] = {}


def detect_device(cfg: dict | None = None) -> str:
    """'mlx' | 'cuda' | 'cpu' | '' – lets the UI warn that CPU transcription is slow."""
    backend = detect_backend(cfg)
    if not backend:
        return ""
    if backend == "mlx":
        return "mlx"
    if "faster" not in _DEVICE:
        _DEVICE["faster"] = _faster_device()[0]
    return _DEVICE["faster"]


def _build_prompt(cfg: dict, lang: str | None) -> dict:
    """Whisper prompt = correct spellings from the glossary + extra terms from the config."""
    try:  # a broken glossary must never stop transcription
        from .glossary import initial_prompt_terms
        gterms = initial_prompt_terms()
    except Exception:
        gterms = ""
    terms = "、".join(x for x in [gterms, cfg.get("initial_prompt_extra", "")] if x)
    # whisper keeps only the LAST ~223 tokens of the prompt: keep it short and put the
    # Simplified-Chinese instruction at the end so overflow drops glossary words first
    if len(terms) > 160:
        terms = terms[:160].rsplit("、", 1)[0]
    kwargs: dict = {}
    if lang == "zh":
        prompt = (f"本次对话可能涉及：{terms}。" if terms else "")
        prompt += "以下是普通话的句子，请用简体中文输出。"
        kwargs["initial_prompt"] = prompt
    elif terms:
        kwargs["initial_prompt"] = terms.replace("、", ", ")
    return kwargs


def _hallucination_filter_on(cfg: dict) -> bool:
    v = cfg.get("hallucination_filter", True)
    return str(v).lower() not in ("0", "false", "no", "off")


def _beam(cfg: dict) -> int:
    try:
        return int(cfg.get("beam_size") or 0)
    except (TypeError, ValueError):
        return 0


def _speech_bounds(samples, sr: int = 16000, frame_s: float = 0.1) -> "tuple[float, float]":
    """(first, last) second of speech-like energy in a mono float array, by RMS against the
    recording's own noise floor. Whisper primed with a prompt turns a silent first window into an
    echo of the prompt and drops the speech that follows – so we start decoding at the first voice."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float32)
    n = int(sr * frame_s)
    if len(x) < n * 10:
        return 0.0, len(x) / sr
    frames = x[: len(x) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 20))
    thr = max(floor * 4.0, 0.004)
    loud = rms > thr
    onset = 0
    for i in range(len(loud) - 2):
        if loud[i] and loud[i + 1] and loud[i + 2]:
            onset = i
            break
    else:
        return 0.0, len(x) / sr  # nothing that looks like speech: leave it to the model + filter
    last = len(loud) - 1
    for j in range(len(loud) - 1, 1, -1):
        if loud[j] and loud[j - 1]:
            last = j
            break
    start = max(0.0, onset * frame_s - 0.5)
    end = min(len(x) / sr, (last + 1) * frame_s + 1.0)
    return start, end


def _transcribe_mlx(audio: Path, repo: str, lang: str | None, cfg: dict, progress) -> dict:
    import mlx_whisper  # lazy: importing mlx is slow
    from mlx_whisper.audio import SAMPLE_RATE, load_audio

    progress(t("transcribe.running"))
    kwargs = _build_prompt(cfg, lang)
    source = str(audio)
    offset = 0.0
    if _hallucination_filter_on(cfg):
        try:  # trim leading / trailing silence so the first decoding window starts on speech
            samples = load_audio(str(audio), sr=SAMPLE_RATE)
            start, end = _speech_bounds(samples, SAMPLE_RATE)
            if start >= 1.0 or end < len(samples) / SAMPLE_RATE - 3.0:
                source = samples[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)]
                offset = start
        except Exception:  # decoding problems fall back to the plain file path
            source, offset = str(audio), 0.0
    beam = _beam(cfg)
    if beam > 0:
        kwargs["beam_size"] = beam
    if _hallucination_filter_on(cfg):
        # whisper's own silence-hallucination guard needs word timestamps; skips text invented
        # inside silent gaps of ≥ 2 s (openai-whisper ≥ 20231117 / mlx-whisper)
        kwargs.update(word_timestamps=True, hallucination_silence_threshold=2.0)
    result = mlx_whisper.transcribe(
        source, path_or_hf_repo=repo, language=lang, verbose=None,
        condition_on_previous_text=False,  # long audio: avoid repetition loops / hallucination
        no_speech_threshold=0.6, logprob_threshold=-1.0, compression_ratio_threshold=2.4,
        **kwargs,
    )
    segments = [
        {"start": round(s["start"] + offset, 2), "end": round(s["end"] + offset, 2), "text": s["text"].strip(),
         "no_speech_prob": round(float(s.get("no_speech_prob", 0.0)), 3),
         "avg_logprob": round(float(s.get("avg_logprob", 0.0)), 3),
         "compression_ratio": round(float(s.get("compression_ratio", 0.0)), 2)}
        for s in result.get("segments", []) if s["text"].strip()
    ]
    return {"segments": segments, "text": result.get("text", "").strip(),
            "language": result.get("language", lang)}


_FASTER_MODEL: dict = {}  # only the current model stays resident (several GB each)


def _faster_device() -> "tuple[str, str]":
    """CUDA float16 with an NVIDIA GPU, else CPU int8 (the usual Windows laptop)."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _load_faster_model(repo: str, progress):
    from faster_whisper import WhisperModel

    device, compute = _faster_device()
    progress(t("transcribe.loading_device", device=device, compute=compute))
    try:
        return WhisperModel(repo, device=device, compute_type=compute)
    except Exception as e:
        if device == "cpu":
            raise
        progress(t("transcribe.cuda_fallback", detail=str(e)[:80]))
        return WhisperModel(repo, device="cpu", compute_type="int8")


def _transcribe_faster(audio: Path, repo: str, lang: str | None, cfg: dict, progress) -> dict:
    if _FASTER_MODEL.get("repo") != repo:
        _FASTER_MODEL.clear()
        _FASTER_MODEL.update(repo=repo, model=_load_faster_model(repo, progress))
    model = _FASTER_MODEL["model"]
    kwargs = _build_prompt(cfg, lang)
    beam = _beam(cfg)
    seg_iter, info = model.transcribe(
        str(audio), language=lang, beam_size=beam if beam > 0 else 1,
        condition_on_previous_text=False, vad_filter=True, **kwargs,
    )
    total = info.duration or 0
    progress(t("transcribe.progress", done="0:00", total=_fmt_ts(total)))
    segments = []
    for s in seg_iter:  # generator: report progress while decoding
        text = s.text.strip()
        if text:
            segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text,
                             "no_speech_prob": round(float(getattr(s, "no_speech_prob", 0.0)), 3),
                             "avg_logprob": round(float(getattr(s, "avg_logprob", 0.0)), 3),
                             "compression_ratio": round(float(getattr(s, "compression_ratio", 0.0)), 2)})
        if segments and len(segments) % 10 == 0:
            progress(t("transcribe.progress", done=_fmt_ts(s.end), total=_fmt_ts(total)))
    return {"segments": segments, "text": "\n".join(s["text"] for s in segments),
            "language": info.language or lang}


def transcribe_file(audio: Path, model: str | None = None, language: str | None = None,
                    progress=lambda msg: None) -> dict:
    """Transcribe an audio/video file → {text, segments, duration, model, language, backend}."""
    cfg = load_config()
    backend = detect_backend(cfg)
    if not backend:
        raise TranscriberUnavailable(no_engine_hint())
    model = model or cfg["whisper_model"]
    lang = language if language is not None else cfg.get("language")
    if not lang or lang == "auto":
        lang = None
    setup_hf_endpoint(cfg)
    repo = resolve_model_repo(model, backend)

    from .config import WHISPER_MODELS
    sizes = {k: v["size"] for k, v in WHISPER_MODELS.items()}
    if model in sizes:  # 提前给出下载量与等待预期，首次下载不至于像卡死
        progress(t("transcribe.loading_model_sized", model=model, size=sizes[model]))
    else:
        progress(t("transcribe.loading_model", model=model))
    runner = _transcribe_mlx if backend == "mlx" else _transcribe_faster
    result = runner(audio, repo, lang, cfg, progress)
    segments = result["segments"]
    removed: list[dict] = []
    if _hallucination_filter_on(cfg):
        prompt = _build_prompt(cfg, lang).get("initial_prompt", "")
        segments, removed = clean_segments(segments, prompt)
        if removed:
            progress(t("transcribe.filtered", n=len(removed)))
    duration = segments[-1]["end"] if segments else 0
    return {"text": "\n".join(s["text"] for s in segments), "segments": segments, "duration": duration,
            "model": model, "language": result.get("language") or lang, "backend": backend,
            "filtered": len(removed), "filtered_examples": [r["text"] for r in removed[:5]]}


def transcribe_meeting(mtg: Meeting, model: str | None = None,
                       progress=lambda msg: None) -> None:
    """Transcribe the meeting's audio into transcript.md / transcript.json and update meta."""
    audio = mtg.audio_file
    if audio is None:
        raise FileNotFoundError(t("transcribe.no_audio", id=mtg.id))
    mtg.save_meta(status="transcribing")
    try:
        result = transcribe_file(audio, model=model, progress=progress)
    except Exception as e:
        mtg.save_meta(status="error", error=str(e))
        raise

    mtg.transcript_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    src = mtg.meta.get("source", "")
    lines = [
        f"# {mtg.title}", "",
        f"- {t('transcript.date')}: {mtg.meta.get('created', '')}",
        f"- {t('transcript.duration')}: {_fmt_ts(result['duration'])}",
        f"- {t('transcript.source')}: {t('source.' + src) if src else ''} "
        f"({t('transcript.by_model', model=result['model'])})",
        f"- {t('transcript.language')}: {result.get('language') or 'auto'}",
        participants_line(mtg.meta.get("participants", "")),
        "", f"## {sections()['transcript']}", "",
    ]
    for s in result["segments"]:
        lines.append(f"[{_fmt_ts(s['start'])}] {s['text']}")
    mtg.transcript_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtg.save_meta(status="done", duration=_fmt_ts(result["duration"]), whisper_model=result["model"],
                  language=result.get("language") or "auto",
                  hallucinations_removed=result.get("filtered", 0),
                  transcribed_at=dt.datetime.now().isoformat(timespec="seconds"), error=None)
