"""本地 Whisper 转写。

两套引擎，同一套模型别名（turbo / large / tiny）：
- mlx-whisper：Apple Silicon 原生加速（macOS arm64）
- faster-whisper：CTranslate2，Windows / Linux / Intel Mac；有 NVIDIA 显卡走 CUDA，否则 CPU int8

两者都装不上的机器（或配置 transcribe_backend=none）不能转写音频，
但文字稿导入（txt / docx / srt / vtt / json）与全部分析功能照常可用。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import platform
import sys
from pathlib import Path

from .config import load_config, resolve_model_repo, setup_hf_endpoint
from .library import Meeting


class TranscriberUnavailable(RuntimeError):
    """本机没有可用的转写引擎。"""


NO_ENGINE_HINT = (
    "本机没有可用的转写引擎，无法转写音频。"
    "可以先用其他工具（腾讯会议 / 飞书妙记 / 讯飞听见 / Whisper 等）把录音转成文字稿，"
    "再以 txt / docx / srt / vtt / json 上传或导入，分析功能不受影响。"
    "要在本机转写，请安装 faster-whisper：pip install faster-whisper"
)


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


_PROBE: dict[str, bool] = {}  # 模块名 -> 是否真的能导入（进程内只探一次）


def _has(module: str) -> bool:
    """引擎是否真的可用：不只看包目录在不在，而是真的 import 一次。

    Windows 上 faster-whisper 装好了但缺 VC 运行库 / CUDA DLL 时，find_spec 仍为真，
    结果是 /api/config 报「能转写」、音频被收下、后台才失败。真实导入能提前暴露这些问题。
    结果缓存，避免每次 /api/config 都重新加载。
    """
    if module in _PROBE:
        return _PROBE[module]
    try:
        if importlib.util.find_spec(module) is None:
            _PROBE[module] = False
            return False
    except (ImportError, ValueError):
        _PROBE[module] = False
        return False
    setup_hf_endpoint()  # huggingface_hub 在导入时读 HF_ENDPOINT：镜像切换必须在这之前
    try:
        importlib.import_module(module)
        _PROBE[module] = True
    except Exception:  # ImportError / OSError（DLL 缺失）等都算不可用
        _PROBE[module] = False
    return _PROBE[module]


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def detect_backend(cfg: dict | None = None) -> str:
    """返回本机实际会使用的转写引擎：'mlx' | 'faster' | ''（没有）。

    配置 transcribe_backend：auto（默认，按平台与已安装的包探测）/ mlx / faster / none。
    只探测是否可导入，不真的加载模型，供启动时给前端报告能力。
    """
    cfg = cfg or load_config()
    want = str(cfg.get("transcribe_backend") or "auto").strip().lower()
    if want not in ("auto", "none", "mlx", "faster"):
        want = "auto"  # 配置写错时按自动探测处理，不让一个拼写错误把整个服务弄成 500
    if want == "none":
        return ""
    if want == "mlx":
        return "mlx" if _has("mlx_whisper") else ""
    if want == "faster":
        return "faster" if _has("faster_whisper") else ""
    # auto：Apple Silicon 优先 mlx，其余平台用 faster-whisper；装了哪个用哪个
    order = ["mlx_whisper", "faster_whisper"] if is_apple_silicon() else ["faster_whisper", "mlx_whisper"]
    for mod in order:
        if _has(mod):
            return "mlx" if mod == "mlx_whisper" else "faster"
    return ""


def _build_prompt(cfg: dict, lang: str | None) -> dict:
    """专有名词提示 = 词表里的正确写法 + 配置里手填的补充，提高人名/术语识别率。"""
    try:  # 词表读取失败绝不拖垮转写
        from .glossary import initial_prompt_terms
        gterms = initial_prompt_terms()
    except Exception:
        gterms = ""
    terms = "、".join(t for t in [gterms, cfg.get("initial_prompt_extra", "")] if t)
    # whisper 提示窗只保留【最后】约 223 个 token：控制总长，且中文指令放末尾，
    # 溢出时先丢词表开头、不丢「简体中文」指令
    if len(terms) > 160:
        terms = terms[:160].rsplit("、", 1)[0]
    kwargs: dict = {}
    if lang == "zh":
        prompt = (f"本次对话可能涉及：{terms}。" if terms else "")
        prompt += "以下是普通话的句子，请用简体中文输出。"
        kwargs["initial_prompt"] = prompt
    elif terms:
        # 非中文/自动识别时也注入专有名词，但不强制语言
        kwargs["initial_prompt"] = terms
    return kwargs


def _beam(cfg: dict) -> int:
    try:
        return int(cfg.get("beam_size") or 0)
    except (TypeError, ValueError):
        return 0


def _transcribe_mlx(audio: Path, repo: str, lang: str | None, cfg: dict,
                    progress) -> dict:
    import mlx_whisper  # 延迟导入：加载 mlx 较慢

    progress("转写中…")
    kwargs = _build_prompt(cfg, lang)
    beam = _beam(cfg)
    if beam > 0:
        kwargs["beam_size"] = beam
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=repo,
        language=lang,
        verbose=None,
        # 长音频防复读/幻觉：不把上一段输出当作下一段的条件
        condition_on_previous_text=False,
        **kwargs,
    )
    segments = [
        {"start": round(s["start"], 2), "end": round(s["end"], 2),
         "text": s["text"].strip()}
        for s in result.get("segments", [])
        if s["text"].strip()
    ]
    return {"segments": segments, "text": result.get("text", "").strip(),
            "language": result.get("language", lang)}


_FASTER_MODEL: dict = {}  # {"repo": str, "model": WhisperModel}：只驻留当前这一个（大模型数 GB，不能越切越多）


def _faster_device() -> "tuple[str, str]":
    """有 NVIDIA 显卡走 CUDA float16，否则 CPU int8（Windows 笔记本最常见）。"""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _load_faster_model(repo: str, progress):
    """加载 faster-whisper 模型；能枚举到显卡不代表 cuBLAS/cuDNN 齐全，CUDA 失败就退回 CPU。"""
    from faster_whisper import WhisperModel

    device, compute = _faster_device()
    progress(f"加载模型（{device} / {compute}）…")
    try:
        return WhisperModel(repo, device=device, compute_type=compute)
    except Exception as e:
        if device == "cpu":
            raise
        progress(f"CUDA 不可用（{str(e)[:80]}），改用 CPU…")
        return WhisperModel(repo, device="cpu", compute_type="int8")


def _transcribe_faster(audio: Path, repo: str, lang: str | None, cfg: dict,
                       progress) -> dict:
    if _FASTER_MODEL.get("repo") != repo:
        _FASTER_MODEL.clear()  # 先释放旧模型再加载新的，避免两个大模型同时驻留
        _FASTER_MODEL.update(repo=repo, model=_load_faster_model(repo, progress))
    model = _FASTER_MODEL["model"]
    kwargs = _build_prompt(cfg, lang)
    beam = _beam(cfg)
    seg_iter, info = model.transcribe(
        str(audio),
        language=lang,
        beam_size=beam if beam > 0 else 1,  # 与 mlx 一致：默认贪心
        condition_on_previous_text=False,
        vad_filter=True,  # 跳过静音段，减少长音频的幻觉与复读
        **kwargs,
    )
    total = info.duration or 0
    progress(f"转写中… 0:00 / {_fmt_ts(total)}")
    segments = []
    for s in seg_iter:  # 生成器：边解码边报进度，长会议不再是黑盒
        text = s.text.strip()
        if text:
            segments.append({"start": round(s.start, 2), "end": round(s.end, 2),
                             "text": text})
        if segments and len(segments) % 10 == 0:
            progress(f"转写中… {_fmt_ts(s.end)} / {_fmt_ts(total)}")
    return {"segments": segments, "text": "\n".join(s["text"] for s in segments),
            "language": info.language or lang}


def transcribe_file(audio: Path, model: str | None = None,
                    language: str | None = None,
                    progress=lambda msg: None) -> dict:
    """转写音频/视频文件，返回 {text, segments, duration, model, language}。"""
    cfg = load_config()
    backend = detect_backend(cfg)
    if not backend:
        raise TranscriberUnavailable(NO_ENGINE_HINT)
    model = model or cfg["whisper_model"]
    # 语言：显式参数 > 配置；"auto" 或留空 = 交给 Whisper 自动识别
    lang = language if language is not None else cfg.get("language")
    if not lang or lang == "auto":
        lang = None
    setup_hf_endpoint(cfg)  # 必须在 import 引擎之前：huggingface_hub 在导入时读 HF_ENDPOINT
    repo = resolve_model_repo(model, backend)

    progress(f"加载模型 {model}（首次使用会自动下载，large 约 3GB）…")
    runner = _transcribe_mlx if backend == "mlx" else _transcribe_faster
    result = runner(audio, repo, lang, cfg, progress)
    segments = result["segments"]
    duration = segments[-1]["end"] if segments else 0
    return {
        "text": result["text"],
        "segments": segments,
        "duration": duration,
        "model": model,
        "language": result.get("language") or lang,
        "backend": backend,
    }


def transcribe_meeting(mtg: Meeting, model: str | None = None,
                       progress=lambda msg: None) -> None:
    """转写会议音频并写入 transcript.md / transcript.json，更新 meta。"""
    audio = mtg.audio_file
    if audio is None:
        raise FileNotFoundError(f"会议 {mtg.id} 没有音频文件")
    mtg.save_meta(status="transcribing")
    try:
        result = transcribe_file(audio, model=model, progress=progress)
    except Exception as e:
        mtg.save_meta(status="error", error=str(e))
        raise

    mtg.transcript_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    lines = [
        f"# {mtg.title}",
        "",
        f"- 日期：{mtg.meta.get('created', '')}",
        f"- 时长：{_fmt_ts(result['duration'])}",
        f"- 来源：{mtg.meta.get('source', '')}（{result['model']} 转写）",
        f"- 语言：{result.get('language') or '自动'}",
        "",
        "## 转写",
        "",
    ]
    for s in result["segments"]:
        lines.append(f"[{_fmt_ts(s['start'])}] {s['text']}")
    mtg.transcript_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtg.save_meta(
        status="done",
        duration=_fmt_ts(result["duration"]),
        whisper_model=result["model"],
        language=result.get("language") or "auto",
        transcribed_at=dt.datetime.now().isoformat(timespec="seconds"),
        error=None,
    )
