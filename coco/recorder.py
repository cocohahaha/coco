"""ffmpeg microphone recording (macOS avfoundation).

System audio (the other side of a call) needs a virtual audio device such as
BlackHole; ``coco devices`` lists inputs, ``audio_device`` in the config selects one.
"""
from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import load_config
from .i18n import t


def record_unsupported_hint() -> str:
    return t("record.unsupported")


def record_supported() -> bool:
    """Can this system record from the web UI / CLI: macOS with ffmpeg installed."""
    return sys.platform == "darwin" and shutil.which("ffmpeg") is not None


def _check_supported() -> None:
    if sys.platform != "darwin":
        raise RuntimeError(record_unsupported_hint())
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(t("record.no_ffmpeg"))


def list_devices() -> str:
    """ffmpeg avfoundation device list (human readable)."""
    _check_supported()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    lines = [l for l in proc.stderr.splitlines() if "AVFoundation" in l]
    return "\n".join(lines) or proc.stderr


def _ffmpeg_cmd(out: Path, device: str) -> list[str]:
    # 16 kHz mono wav: whisper's native input; survives an interrupted recording
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", device,
            "-ac", "1", "-ar", "16000", "-y", str(out)]


class Recorder:
    """Recording process manager for the web server (one recording at a time)."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.out: Path | None = None
        self.started_at: float = 0
        self.title: str = ""
        self.meeting_id: str = ""

    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, out: Path, title: str, device: str | None = None) -> None:
        if self.active:
            raise RuntimeError(t("record.already_recording"))
        _check_supported()
        device = device or load_config()["audio_device"]
        self.proc = subprocess.Popen(_ffmpeg_cmd(out, device), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(1.0)  # give ffmpeg a moment so device errors surface immediately
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode(errors="ignore")
            self.proc = None
            raise RuntimeError(t("record.ffmpeg_failed", detail=err.strip() or "?"))
        self.out = out
        self.title = title
        self.started_at = time.time()

    def stop(self) -> Path:
        if not self.active:
            raise RuntimeError(t("record.not_recording"))
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        out = self.out
        self.proc, self.out = None, None
        if not out.exists() or out.stat().st_size < 1024:
            raise RuntimeError(t("record.empty_file"))
        return out

    def elapsed(self) -> int:
        return int(time.time() - self.started_at) if self.active else 0


def record_blocking(out: Path, device: str | None = None) -> Path:
    """CLI: record in the foreground until Ctrl+C."""
    _check_supported()
    device = device or load_config()["audio_device"]
    proc = subprocess.Popen(_ffmpeg_cmd(out, device), stdin=subprocess.DEVNULL)
    start = time.time()
    try:
        while proc.poll() is None:
            mins, secs = divmod(int(time.time() - start), 60)
            print("\r" + t("record.recording_status", time=f"{mins:02d}:{secs:02d}"), end="", flush=True)
            time.sleep(1)
        raise RuntimeError(t("record.ffmpeg_exited"))
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print()
    if not out.exists() or out.stat().st_size < 1024:
        raise RuntimeError(t("record.empty_file"))
    return out
