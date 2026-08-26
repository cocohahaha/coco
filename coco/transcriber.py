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

from .config import load_config, resolve_model_repo, setup_hf_endpoint
from .i18n import sections, t
from .library import Meeting


class TranscriberUnavailable(RuntimeError):
    """No usable transcription engine on this machine."""


def no_engine_hint() -> str:
    return t("transcribe.no_engine")


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


def _beam(cfg: dict) -> int:
    try:
        return int(cfg.get("beam_size") or 0)
    except (TypeError, ValueError):
        return 0


def _transcribe_mlx(audio: Path, repo: str, lang: str | None, cfg: dict, progress) -> dict:
    import mlx_whisper  # lazy: importing mlx is slow

    progress(t("transcribe.running"))
    kwargs = _build_prompt(cfg, lang)
    beam = _beam(cfg)
    if beam > 0:
        kwargs["beam_size"] = beam
    result = mlx_whisper.transcribe(
        str(audio), path_or_hf_repo=repo, language=lang, verbose=None,
        condition_on_previous_text=False,  # long audio: avoid repetition loops / hallucination
        **kwargs,
    )
    segments = [
        {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
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
            segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
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

    progress(t("transcribe.loading_model", model=model))
    runner = _transcribe_mlx if backend == "mlx" else _transcribe_faster
    result = runner(audio, repo, lang, cfg, progress)
    segments = result["segments"]
    duration = segments[-1]["end"] if segments else 0
    return {"text": result["text"], "segments": segments, "duration": duration,
            "model": model, "language": result.get("language") or lang, "backend": backend}


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
        "", f"## {sections()['transcript']}", "",
    ]
    for s in result["segments"]:
        lines.append(f"[{_fmt_ts(s['start'])}] {s['text']}")
    mtg.transcript_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtg.save_meta(status="done", duration=_fmt_ts(result["duration"]), whisper_model=result["model"],
                  language=result.get("language") or "auto",
                  transcribed_at=dt.datetime.now().isoformat(timespec="seconds"), error=None)
