"""Process environment fixes for a server started without a terminal.

Started from the app icon / Start-menu shortcut, coco inherits the minimal PATH of the
desktop session: `claude`, `codex` (often installed through npm / nvm) and Homebrew's
`ffmpeg` are invisible although they work in the user's terminal. ``fix_path`` merges the
login shell's PATH and the usual install folders in, once per process tree.

``ensure_ffmpeg`` exposes the static ffmpeg shipped by the ``imageio-ffmpeg`` wheel under
the plain name ``ffmpeg`` when the system has none: mlx-whisper decodes every audio file
by calling ``ffmpeg`` and the server-side recorder needs it too, but most Macs never had
Homebrew.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_MARK = "__COCO_PATH__"


def _login_shell_path() -> str:
    shell = os.environ.get("SHELL") or ("/bin/zsh" if sys.platform == "darwin" else "/bin/bash")
    if not Path(shell).exists():
        return ""
    try:
        out = subprocess.run([shell, "-l", "-i", "-c", f'printf "{_MARK}%s{_MARK}" "$PATH"'],
                             capture_output=True, text=True, timeout=4, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = out.split(_MARK)
    return parts[1] if len(parts) >= 3 else ""


def _common_dirs() -> "list[str]":
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        dirs = [home / ".local" / "bin"]
        if appdata:
            dirs.append(Path(appdata) / "npm")
        return [str(d) for d in dirs]
    dirs = [home / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
            home / ".npm-global" / "bin", home / ".volta" / "bin", home / ".bun" / "bin",
            home / ".cargo" / "bin"]
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.is_dir():  # newest node first
        dirs += sorted((p / "bin" for p in nvm.iterdir()), reverse=True)
    return [str(d) for d in dirs]


def fix_path() -> None:
    if os.environ.get("COCO_PATH_FIXED"):
        return
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra: list[str] = []
    if os.name != "nt":  # the login shell knows where nvm / Homebrew / installers put things
        extra += [p for p in _login_shell_path().split(os.pathsep) if p]
    extra += _common_dirs()
    seen = set(current)
    merged = list(current)
    for d in extra:
        if d and d not in seen and Path(d).is_dir():
            merged.append(d)
            seen.add(d)
    os.environ["PATH"] = os.pathsep.join(p for p in merged if p)
    os.environ["COCO_PATH_FIXED"] = "1"


def ensure_ffmpeg() -> "str | None":
    """Path of a usable ffmpeg (system first, else the imageio-ffmpeg binary linked as
    ``ffmpeg`` into a folder put on PATH). None when there is neither."""
    hit = shutil.which("ffmpeg")
    if hit:
        return hit
    try:
        import imageio_ffmpeg  # optional dependency (macOS: see requirements.txt)
        exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None
    if not exe.exists():
        return None
    bindir = Path(sys.prefix) / "coco-bin"
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    link = bindir / name
    try:
        bindir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            if not link.exists() or link.stat().st_size != exe.stat().st_size:
                shutil.copyfile(exe, link)
        else:
            # the wheel's binary name carries its version: an upgrade leaves a dangling link behind
            if link.is_symlink() and Path(os.readlink(link)) != exe:
                link.unlink()
            if not os.path.lexists(link):
                try:
                    link.symlink_to(exe)
                except OSError:  # file systems without symlinks: a copy works as well
                    shutil.copyfile(exe, link)
                    link.chmod(0o755)
    except OSError:
        return None
    os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
    return str(link)


def prepare() -> None:
    fix_path()
    ensure_ffmpeg()
