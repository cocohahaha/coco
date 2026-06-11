"""本地 Whisper 转写（mlx-whisper，Apple Silicon 原生加速）。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .config import load_config, resolve_model_repo, setup_hf_endpoint
from .library import Meeting


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def transcribe_file(audio: Path, model: str | None = None,
                    language: str | None = None,
                    progress=lambda msg: None) -> dict:
    """转写音频/视频文件，返回 {text, segments, duration, model}。"""
    cfg = load_config()
    model = model or cfg["whisper_model"]
    language = language or cfg["language"] or None
    setup_hf_endpoint(cfg)
    repo = resolve_model_repo(model)

    progress(f"加载模型 {model}（首次使用会自动下载，large 约 3GB）…")
    import mlx_whisper  # 延迟导入：加载 mlx 较慢

    progress("转写中…")
    kwargs = {}
    if language == "zh":
        prompt = "以下是普通话的句子，请用简体中文输出。"
        if cfg.get("initial_prompt_extra"):
            prompt += "本次对话可能涉及：" + cfg["initial_prompt_extra"]
        kwargs["initial_prompt"] = prompt
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=repo,
        language=language,
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
    duration = segments[-1]["end"] if segments else 0
    return {
        "text": result.get("text", "").strip(),
        "segments": segments,
        "duration": duration,
        "model": model,
        "language": result.get("language", language),
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
        transcribed_at=dt.datetime.now().isoformat(timespec="seconds"),
        error=None,
    )
