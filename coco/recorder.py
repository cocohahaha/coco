"""ffmpeg 麦克风录音（macOS avfoundation）。

系统音频（会议对方声音）需要安装虚拟声卡（如 BlackHole），
然后用 coco devices 查看设备号并在配置中切换 audio_device。
"""
from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import load_config

RECORD_UNSUPPORTED = (
    "网页录音目前只支持 macOS（ffmpeg avfoundation）。"
    "请用系统自带的录音机 / 会议软件录好后，把音频文件上传或导入。"
)


def record_supported() -> bool:
    """当前系统能否用网页/CLI 录音：macOS 且装了 ffmpeg。"""
    return sys.platform == "darwin" and shutil.which("ffmpeg") is not None


def _check_supported() -> None:
    if sys.platform != "darwin":
        raise RuntimeError(RECORD_UNSUPPORTED)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("找不到 ffmpeg，无法录音：brew install ffmpeg")


def list_devices() -> str:
    """返回 ffmpeg avfoundation 设备列表（人类可读文本）。"""
    _check_supported()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    lines = [l for l in proc.stderr.splitlines() if "AVFoundation" in l]
    return "\n".join(lines) or proc.stderr


def _ffmpeg_cmd(out: Path, device: str) -> list[str]:
    # 16kHz 单声道 wav：whisper 的原生输入格式，中断也不会损坏文件
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", device,
        "-ac", "1", "-ar", "16000", "-y", str(out),
    ]


class Recorder:
    """供 Web 服务使用的录音进程管理（一次只允许一路录音）。"""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.out: Path | None = None
        self.started_at: float = 0
        self.title: str = ""

    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, out: Path, title: str, device: str | None = None) -> None:
        if self.active:
            raise RuntimeError("已有录音在进行中")
        _check_supported()
        device = device or load_config()["audio_device"]
        self.proc = subprocess.Popen(
            _ffmpeg_cmd(out, device),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(1.0)  # 给 ffmpeg 一点启动时间，便于立刻报告设备错误
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode(errors="ignore")
            self.proc = None
            raise RuntimeError(f"ffmpeg 启动失败：{err.strip() or '未知错误'}\n"
                               "提示：首次录音需在系统设置中允许终端访问麦克风；"
                               "coco devices 可查看输入设备")
        self.out = out
        self.title = title
        self.started_at = time.time()

    def stop(self) -> Path:
        if not self.active:
            raise RuntimeError("当前没有录音")
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        out = self.out
        self.proc, self.out = None, None
        if not out.exists() or out.stat().st_size < 1024:
            raise RuntimeError("录音文件为空——请检查麦克风权限与输入设备")
        return out

    def elapsed(self) -> int:
        return int(time.time() - self.started_at) if self.active else 0


def record_blocking(out: Path, device: str | None = None) -> Path:
    """CLI 用：前台录音直到 Ctrl+C。"""
    _check_supported()
    device = device or load_config()["audio_device"]
    proc = subprocess.Popen(_ffmpeg_cmd(out, device), stdin=subprocess.DEVNULL)
    start = time.time()
    try:
        while proc.poll() is None:
            mins, secs = divmod(int(time.time() - start), 60)
            print(f"\r⏺ 录音中 {mins:02d}:{secs:02d}（Ctrl+C 停止）", end="", flush=True)
            time.sleep(1)
        raise RuntimeError("ffmpeg 提前退出——首次使用请允许终端访问麦克风，"
                           "或用 coco devices 检查输入设备")
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print()
    if not out.exists() or out.stat().st_size < 1024:
        raise RuntimeError("录音文件为空——请检查麦克风权限与输入设备")
    return out
