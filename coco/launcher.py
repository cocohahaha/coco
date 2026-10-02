"""One launcher for every platform: ``coco start`` / ``stop`` / ``status`` / ``setup`` /
``shortcut`` / ``update``.

Standard library only – it runs right after the virtual environment is created, before
any dependency is installed. The double-click files (``coco.command`` on macOS / Linux,
``coco.bat`` on Windows) only find or create ``.venv`` and then hand over to this module,
so the start-up behaviour is written once:

1. already running (this copy of coco) → just open the browser;
2. dependencies missing or ``requirements.txt`` changed → install them (uv when present,
   a PyPI mirror when it measures clearly faster than pypi.org, core-only fallback when the
   transcription engine cannot be installed);
3. pick a free port, start the server in the background (no terminal window needed),
   wait until it answers, open the browser;
4. first run: add coco to Applications / Start menu so the next start is one click.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__
from .config import ROOT, load_config, save_config
from .i18n import set_lang, system_lang, configured_lang, t

CODE_DIR = Path(__file__).resolve().parent.parent   # where coco/ lives (≠ ROOT with COCO_ROOT)
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "coco.log"
RUNTIME_FILE = LOG_DIR / "coco.runtime.json"        # written by the server: {pid, port, root}
LOCK_FILE = LOG_DIR / "coco.starting"
REQS = CODE_DIR / "requirements.txt"
REQS_STAMP = Path(sys.prefix) / ".coco-requirements"  # hash of the last installed requirements
REPO_URL = "https://github.com/cocohahaha/coco"
OBSOLETE_FILES = ("run.sh", "run.bat")  # launchers replaced by coco.command / coco.bat in 0.2.0
CORE_PACKAGES = ["fastapi>=0.110", "uvicorn>=0.29", "python-multipart>=0.0.9", "pypdf>=4.0"]
CORE_MODULES = ("fastapi", "uvicorn", "multipart")
PYPI_MIRRORS = ("https://pypi.tuna.tsinghua.edu.cn/simple", "https://mirrors.aliyun.com/pypi/simple")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

QUIET = False  # --quiet: started from the app icon, no terminal – errors go to a dialog
# Requests to our own server must never go through http(s)_proxy (Clash & co. are common where
# pypi / GitHub are slow, and urllib would happily send 127.0.0.1 through them).
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def say(msg: str) -> None:
    if not QUIET and sys.stdout is not None:
        print(msg, flush=True)


# ---------- is coco already running? ----------

def health(port: int, timeout: float = 2.0) -> "dict | None":
    """Ask a port whether it is coco. Older coco versions have no /api/health: fall back
    to the /api/config probe the old launch scripts used (root unknown → None)."""
    for path in ("/api/health", "/api/config"):
        try:
            with _LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):  # some other local service answering JSON
            continue
        if path == "/api/health" and d.get("app") == "coco":
            return d
        if path == "/api/config" and "whisper_model" in d:
            return {"app": "coco", "root": None, "version": "?"}
    return None


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def port_free(port: int) -> bool:
    with socket.socket() as s:
        if not IS_WIN:  # like uvicorn: a just-closed socket in TIME_WAIT does not block the port
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _same_root(info: dict) -> bool:
    """A coco of *another* folder (a second copy, a test data set) must not be mistaken for
    ours – it would show someone else's library. Old servers do not report their root."""
    root = info.get("root")
    if not root:
        return True
    try:
        return Path(root).resolve() == ROOT.resolve()
    except OSError:
        return False


def _runtime() -> dict:
    try:
        return json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def preferred_port() -> int:
    env = os.environ.get("COCO_PORT")
    if env and env.isdigit():
        return int(env)
    try:
        return int(load_config().get("port") or 8765)
    except (TypeError, ValueError):
        return 8765


def find_running() -> "int | None":
    """Port of *this* coco if it is up: last known port first, then the usual range."""
    base = preferred_port()
    cands = []
    rt = _runtime().get("port")
    if isinstance(rt, int):
        cands.append(rt)
    cands += [p for p in range(base, base + 21) if p not in cands]
    for p in cands:
        if listening(p):
            info = health(p, timeout=1.0)
            if info and _same_root(info):
                return p
    return None


def pick_port() -> "tuple[int, str]":
    """(free port, owner of the preferred port if it was taken by something else)."""
    base = preferred_port()
    if port_free(base):
        return base, ""
    owner = _port_owner(base)
    for p in range(base + 1, base + 21):
        if port_free(p):
            return p, owner
    raise RuntimeError(t("launcher.no_free_port", port=base))


def _port_owner(port: int) -> str:
    if IS_WIN or not shutil.which("lsof"):
        return "?"
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5).stdout.splitlines()
        if len(out) > 1:
            cols = out[1].split()
            return f"{cols[0]} (pid {cols[1]})"
    except (OSError, subprocess.SubprocessError):
        pass
    return "?"


# ---------- dependencies ----------

def _reqs_hash() -> str:
    try:
        return hashlib.sha256(REQS.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def deps_ok() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in CORE_MODULES)


def _missing_requirements() -> "list[str]":
    """Requirement names that apply to this platform but are not installed. Markers in
    requirements.txt only use sys_platform / platform_machine with ==, != and and/or, which
    is valid Python, so they are evaluated without the `packaging` library."""
    import importlib.metadata as md
    import platform
    import re
    env = {"sys_platform": sys.platform, "platform_machine": platform.machine(),
           "platform_system": platform.system(), "os_name": os.name}
    missing = []
    try:
        lines = REQS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return missing
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        spec, _, marker = line.partition(";")
        if marker.strip():
            try:
                if not eval(marker.strip(), {"__builtins__": {}}, env):  # noqa: S307 – our own file
                    continue
            except Exception:
                continue
        name = re.split(r"[<>=!~\[ ]", spec.strip(), 1)[0]
        try:
            md.version(name)
        except md.PackageNotFoundError:
            missing.append(name)
    return missing


def deps_current() -> bool:
    """Core importable and requirements.txt unchanged since the last install attempt."""
    if not deps_ok():
        return False
    try:
        return REQS_STAMP.read_text(encoding="utf-8").split(":")[0].strip() == _reqs_hash()
    except OSError:
        pass
    # environment built by the old run.sh (no stamp): accept it if nothing is missing
    if _missing_requirements():
        return False
    _stamp()
    return True


def _stamp(core_only: bool = False) -> None:
    """Remember the requirements we installed – also after a core-only fallback, so a machine
    that cannot build the transcription engine is not retried on every start (`coco setup
    --force` retries on purpose)."""
    try:
        REQS_STAMP.write_text(_reqs_hash() + (":core" if core_only else ""), encoding="utf-8")
    except OSError:
        pass


def _throughput(index: str, timeout: float = 6.0) -> float:
    """Download speed (bytes/s) of a 512 KB slice of a real wheel listed by this index. Index
    pages are tiny and answer fast even when bulk downloads crawl (typical from mainland China),
    so the page alone says nothing about how long the 300 MB of wheels will take."""
    import html
    import re
    from urllib.parse import urljoin
    try:
        page_url = index.rstrip("/") + "/pip/"
        page = urllib.request.urlopen(page_url, timeout=timeout).read().decode("utf-8", "replace")
        hrefs = re.findall(r'href="([^"]+?\.whl[^"]*)"', page)
        if not hrefs:
            return 0.0
        url = urljoin(page_url, html.unescape(hrefs[-1]))
        req = urllib.request.Request(url, headers={"Range": "bytes=0-524287", "User-Agent": "coco"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = len(r.read(524288))
        return n / max(time.monotonic() - t0, 1e-3)
    except Exception:
        return 0.0


def pip_index() -> "str | None":
    """PyPI index to use: explicit settings win; otherwise measure pypi.org and a mainland-China
    mirror and take the mirror when it is clearly faster."""
    for var in ("COCO_PIP_INDEX", "PIP_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX"):
        if os.environ.get(var):
            return os.environ[var]
    pypi = _throughput("https://pypi.org/simple")
    if pypi >= 1_500_000:  # ≥ 1.5 MB/s: good enough, stay on the source
        return None
    best, best_speed = None, pypi
    for mirror in PYPI_MIRRORS:
        speed = _throughput(mirror)
        if speed > max(best_speed * 1.5, 50_000):
            best, best_speed = mirror, speed
        if best_speed >= 1_500_000:
            break
    return best


def _installer(index: "str | None") -> "list[str]":
    """uv is ~10× faster than pip for these wheels; fall back to pip."""
    uv = shutil.which("uv") or _uv_fallback()
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable]
        return cmd + (["--index-url", index] if index else [])
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    return cmd + (["-i", index] if index else [])


def _uv_fallback() -> "str | None":
    home = Path.home()
    for c in (home / ".local" / "bin" / ("uv.exe" if IS_WIN else "uv"), home / ".cargo" / "bin" / "uv"):
        if c.exists():
            return str(c)
    return None


def ensure_deps(force: bool = False) -> None:
    if not force and deps_current():
        return
    index = pip_index()
    say(t("launcher.installing"))
    if QUIET:
        notify(t("launcher.installing"))
    if index:
        say("   " + t("launcher.using_mirror", url=index))
    base = _installer(index)
    if not base[0].lower().endswith(("uv", "uv.exe")):  # Windows: which() may return uv.EXE
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                       capture_output=True, check=False)
    # no terminal (started from the app icon): the installer's output goes to the log
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = open(LOG_FILE, "ab") if QUIET else None
    try:
        ok = subprocess.run(base + ["-r", str(REQS)], stdout=out, stderr=out, check=False).returncode == 0
        if not ok:
            say(t("launcher.engine_failed"))
            subprocess.run(base + CORE_PACKAGES, stdout=out, stderr=out, check=False)
    finally:
        if out:
            out.close()
    importlib.invalidate_caches()
    if not deps_ok():
        raise RuntimeError(t("launcher.deps_failed", log=str(LOG_FILE)))
    _stamp(core_only=not ok)


# ---------- start / stop ----------

def _server_cmd(port: int) -> "list[str]":
    py = sys.executable
    if IS_WIN:  # pythonw: no console window for the background server
        w = Path(py).with_name("pythonw.exe")
        if w.exists():
            py = str(w)
    return [py, "-m", "coco", "web", "--port", str(port)]


def _spawn(port: int) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CODE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUTF8", "1")
    env.pop("CLAUDECODE", None)
    log = open(LOG_FILE, "ab")
    log.write(f"\n===== coco {__version__} start {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"port {port} =====\n".encode("utf-8"))
    log.flush()
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "cwd": str(CODE_DIR), "env": env}
    if IS_WIN:
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kw["start_new_session"] = True  # survives closing the terminal / the app launcher
    return subprocess.Popen(_server_cmd(port), **kw)


def _tail_log(n: int = 15) -> str:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


class _StartLock:
    """Double-clicking twice must not start two servers: the second launcher waits."""

    def __init__(self):
        self.held = False

    def __enter__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > 180:  # stale lock from a crash
                    LOCK_FILE.unlink()
                    return self.__enter__()
            except OSError:
                pass
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                LOCK_FILE.unlink()
            except OSError:
                pass


def open_browser(url: str) -> None:
    if os.environ.get("COCO_NO_BROWSER"):
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def start(open_page: bool = True, wait_s: int = 150) -> int:
    running = find_running()
    if running:
        url = f"http://127.0.0.1:{running}"
        old = (health(running) or {}).get("version")
        if old == __version__ or os.environ.get("COCO_KEEP_RUNNING"):
            say(t("launcher.already_running", url=url))
            if open_page:
                open_browser(url)
            return running
        # the code was updated while an older server kept running: restart it, unless it is
        # recording or transcribing right now (then the update simply waits for the next start)
        say(t("launcher.restarting_old", old=old or "?", new=__version__))
        if not _stop_port(running, force=False):
            say(t("launcher.update_later", url=url))
            if open_page:
                open_browser(url)
            return running
    with _StartLock() as lock:
        if not lock.held:  # someone else is starting coco right now: wait for it
            say(t("launcher.waiting_other"))
            for _ in range(wait_s * 2):
                p = find_running()
                if p:
                    if open_page:
                        open_browser(f"http://127.0.0.1:{p}")
                    return p
                time.sleep(0.5)
            raise RuntimeError(t("launcher.start_failed", log=str(LOG_FILE), tail=_tail_log()))
        ensure_deps()
        port, owner = pick_port()
        if owner:
            say(t("launcher.port_taken", port=preferred_port(), owner=owner, new=port))
        say(t("launcher.starting"))
        proc = _spawn(port)
        url = f"http://127.0.0.1:{port}"
        t0 = time.monotonic()
        hinted = False
        while time.monotonic() - t0 < wait_s:
            info = health(port, timeout=1.5) if listening(port) else None
            if info and _same_root(info):
                break
            if proc.poll() is not None:
                raise RuntimeError(t("launcher.start_failed", log=str(LOG_FILE), tail=_tail_log()))
            if not hinted and time.monotonic() - t0 > 12:
                say(t("launcher.slow_first_start"))
                if QUIET:
                    notify(t("launcher.slow_first_start"))
                hinted = True
            time.sleep(0.5)
        else:
            raise RuntimeError(t("launcher.start_failed", log=str(LOG_FILE), tail=_tail_log()))
    say(t("launcher.ready", url=url))
    if open_page:
        open_browser(url)
    return port


def _pid_on_port(port: int) -> "int | None":
    """PID of the process listening on a port, only if it is a coco server (`-m coco web`)."""
    try:
        if IS_WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                cols = line.split()
                if len(cols) >= 5 and cols[1].endswith(f":{port}") and cols[3].upper() == "LISTENING":
                    return int(cols[4])
            return None
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        for pid in out:
            cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True,
                                 timeout=5).stdout
            if "coco" in cmd and " web" in cmd:
                return int(pid)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _old_server_busy(port: int) -> bool:
    """Servers older than /api/shutdown cannot say they are busy; ask the endpoints they do have.
    Unknown counts as busy – better to postpone an update than to cut a meeting recording."""
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/api/record/status", timeout=3) as r:
            if json.loads(r.read().decode("utf-8")).get("active"):
                return True
        with _LOCAL.open(f"http://127.0.0.1:{port}/api/meetings", timeout=5) as r:
            meetings = json.loads(r.read().decode("utf-8"))
        return any(isinstance(m, dict) and m.get("status") == "transcribing" for m in meetings)
    except Exception:
        return True


def _stop_port(port: int, force: bool = True) -> bool:
    """Ask the server on `port` to quit; with force=False a busy server (recording, transcribing)
    is left running. Servers older than /api/shutdown are ended through their PID."""
    answered = False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST",
                                     data=json.dumps({"force": force}).encode(),
                                     headers={"Content-Type": "application/json"})
        with _LOCAL.open(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
        answered = True
        if not d.get("ok"):
            return False  # busy and not forced
    except Exception:
        pass
    for _ in range(40 if answered else 0):  # a server without /api/shutdown will not stop by itself
        if not listening(port):
            return True
        time.sleep(0.25)
    if not answered and not force and _old_server_busy(port):
        return False  # a pre-0.2 server in the middle of a recording / transcription: leave it
    rt = _runtime()
    pid = rt.get("pid") if rt.get("port") == port else None
    pid = pid if isinstance(pid, int) and pid > 0 else _pid_on_port(port)
    if pid:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
            else:
                os.kill(pid, 15)
        except OSError:
            pass
        for _ in range(20):
            if not listening(port):
                return True
            time.sleep(0.25)
    return not listening(port) and (answered or pid is not None)


def stop() -> bool:
    port = find_running()
    if not port:
        say(t("launcher.not_running"))
        return False
    stopped = _stop_port(port, force=True)
    say(t("launcher.stopped") if stopped else t("launcher.stop_failed", port=port))
    return stopped


def status() -> None:
    port = find_running()
    if port:
        info = health(port) or {}
        say(t("launcher.status_running", url=f"http://127.0.0.1:{port}",
              version=info.get("version", "?"), pid=info.get("pid", "?")))
    else:
        say(t("launcher.not_running"))
    say(t("launcher.status_paths", root=str(ROOT), log=str(LOG_FILE)))


# ---------- desktop integration: Applications / Start menu / .desktop ----------

def _icon(ext: str) -> Path:
    return CODE_DIR / "assets" / f"coco.{ext}"


def mac_app_path() -> Path:
    return Path.home() / "Applications" / "coco.app"


MAC_LAUNCHER = """#!/bin/bash
# coco app launcher – generated by `coco shortcut`; points at the copy of coco below.
ROOT={root}
MSG={missing}
if [ ! -d "$ROOT/coco" ]; then
  osascript -e 'on run argv' -e 'display alert "coco" message (item 1 of argv)' -e 'end run' "$MSG" >/dev/null 2>&1
  exit 1
fi
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  open -a Terminal "$ROOT/coco.command"
  exit 0
fi
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$ROOT" && exec "$ROOT/.venv/bin/python" -m coco start --quiet
"""

MAC_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>coco</string>
  <key>CFBundleDisplayName</key><string>coco</string>
  <key>CFBundleIdentifier</key><string>io.github.cocohahaha.coco</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>coco</string>
  <key>CFBundleIconFile</key><string>coco</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSMicrophoneUsageDescription</key><string>coco records meetings with your microphone.</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def install_mac_app() -> Path:
    app = mac_app_path()
    macos = app / "Contents" / "MacOS"
    res = app / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(MAC_PLIST.format(version=__version__), encoding="utf-8")
    exe = macos / "coco"
    missing = _sh_quote(t("launcher.app_root_missing", root=str(CODE_DIR)))
    exe.write_text(MAC_LAUNCHER.format(root=_sh_quote(str(CODE_DIR)), missing=missing), encoding="utf-8")
    exe.chmod(0o755)
    if _icon("icns").exists():
        shutil.copyfile(_icon("icns"), res / "coco.icns")
    # make Finder / Launchpad pick up the new bundle and icon right away
    subprocess.run(["touch", str(app)], check=False)
    lsreg = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
             "/Support/lsregister")
    if Path(lsreg).exists():
        subprocess.run([lsreg, "-f", str(app)], capture_output=True, check=False)
    return app


def _win_shortcut_paths() -> "list[Path]":
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "coco.lnk"
    return [menu, Path.home() / "Desktop" / "coco.lnk"]


def install_win_shortcuts() -> "list[Path]":
    py = Path(sys.executable)
    target = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
    done = []
    for lnk in _win_shortcut_paths():
        if not lnk.parent.exists():
            continue
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:COCO_LNK);"
              "$s.TargetPath=$env:COCO_TARGET;$s.Arguments='-m coco start --quiet';"
              "$s.WorkingDirectory=$env:COCO_DIR;$s.IconLocation=$env:COCO_ICON;"
              "$s.Description='coco';$s.Save()")
        env = dict(os.environ, COCO_LNK=str(lnk), COCO_TARGET=str(target), COCO_DIR=str(CODE_DIR),
                   COCO_ICON=str(_icon("ico")))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           env=env, capture_output=True, check=False)
        if r.returncode == 0 and lnk.exists():
            done.append(lnk)
    return done


def linux_desktop_path() -> Path:
    return Path.home() / ".local" / "share" / "applications" / "coco.desktop"


def install_linux_desktop() -> Path:
    p = linux_desktop_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[Desktop Entry]\nType=Application\nName=coco\nComment=Meeting recorder & analyst\n"
                 f"Exec=\"{sys.executable}\" -m coco start --quiet\nPath={CODE_DIR}\n"
                 f"Icon={_icon('png')}\nTerminal=false\nCategories=Office;AudioVideo;\n", encoding="utf-8")
    return p


def shortcut_paths() -> "list[Path]":
    if IS_MAC:
        return [mac_app_path()]
    if IS_WIN:
        return _win_shortcut_paths()
    return [linux_desktop_path()]


def install_shortcut() -> "list[Path]":
    if IS_MAC:
        return [install_mac_app()]
    if IS_WIN:
        return install_win_shortcuts()
    return [install_linux_desktop()]


def remove_shortcut() -> "list[Path]":
    removed = []
    for p in shortcut_paths():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)
        elif p.exists():
            p.unlink()
            removed.append(p)
    return removed


def _shortcut_points_here() -> bool:
    if IS_MAC:
        exe = mac_app_path() / "Contents" / "MacOS" / "coco"
        try:
            return _sh_quote(str(CODE_DIR)) in exe.read_text(encoding="utf-8")
        except OSError:
            return False
    return True  # .lnk / .desktop are rewritten whenever we run install anyway


def auto_shortcut() -> None:
    """First successful start: add coco to Applications / Start menu once. If the user later
    deletes it we respect that; if the folder moved we repoint it."""
    if os.environ.get("COCO_NO_SHORTCUT"):
        return
    cfg = load_config()
    state = cfg.get("desktop_shortcut", "auto")
    exists = any(p.exists() for p in shortcut_paths())
    if state == "off" or (state == "created" and not exists):
        return
    if state == "created" and exists and _shortcut_points_here():
        return
    try:
        paths = install_shortcut()
    except Exception as e:  # never block the start because of a shortcut
        say(t("launcher.shortcut_failed", detail=str(e)[:200]))
        return
    if paths:
        cfg["desktop_shortcut"] = "created"
        save_config(cfg)
        say(t("launcher.shortcut_mac" if IS_MAC else "launcher.shortcut_win" if IS_WIN
              else "launcher.shortcut_linux", path=str(paths[0])))


# ---------- update ----------

def update() -> None:
    """git checkout → fast-forward pull; downloaded ZIP → overlay the latest code, never
    touching library/, memory/, logs/, .venv or coco.config.json."""
    if (CODE_DIR / ".git").exists() and shutil.which("git"):
        say(t("launcher.updating_git"))
        r = subprocess.run(["git", "-C", str(CODE_DIR), "pull", "--ff-only"], check=False)
        if r.returncode != 0:
            raise RuntimeError(t("launcher.update_git_failed"))
    else:
        say(t("launcher.updating_zip"))
        import zipfile
        tmp = Path(tempfile.mkdtemp(prefix="coco-update-"))
        zpath = tmp / "coco.zip"
        urllib.request.urlretrieve(f"{REPO_URL}/archive/refs/heads/main.zip", zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        src = next(p for p in tmp.iterdir() if p.is_dir())
        keep = {"library", "memory", "logs", ".venv", "coco.config.json", ".git"}
        for item in src.iterdir():
            if item.name in keep:
                continue
            dest = CODE_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        for gone in OBSOLETE_FILES:  # an overlay never deletes: drop what newer versions removed
            (CODE_DIR / gone).unlink(missing_ok=True)
        for exe in ("coco.command", "bin/coco", "scripts/install.sh"):
            p = CODE_DIR / exe
            if p.exists():
                p.chmod(0o755)
        shutil.rmtree(tmp, ignore_errors=True)
    ensure_deps(force=not deps_current())
    if find_running():
        say(t("launcher.update_restart"))
    say(t("launcher.updated"))


# ---------- errors without a terminal ----------

def notify(msg: str) -> None:
    if IS_MAC:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(msg)} with title \"coco\""],
                       capture_output=True, check=False)


def show_error(msg: str) -> None:
    if not QUIET:
        return
    if IS_MAC:
        script = f"display alert \"coco\" message {json.dumps(msg[:900])} as critical"
        subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
    elif IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg[:1500], "coco", 0x10)
        except Exception:
            pass


# ---------- entry ----------

USAGE = "coco start [--no-browser] [--quiet] | stop | restart | status | setup | shortcut [--remove] | update"


def main(argv: "list[str]") -> int:
    global QUIET
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    set_lang(configured_lang() or system_lang())
    cmd = argv[0] if argv else "start"
    flags = set(argv[1:])
    QUIET = "--quiet" in flags
    try:
        if cmd == "start":
            start(open_page="--no-browser" not in flags)
            auto_shortcut()
        elif cmd == "stop":
            stop()
        elif cmd == "restart":
            stop()
            start(open_page="--no-browser" not in flags)
        elif cmd == "status":
            status()
        elif cmd == "setup":
            ensure_deps(force="--force" in flags)
            say(t("launcher.setup_done"))
        elif cmd == "shortcut":
            if "--remove" in flags:
                removed = remove_shortcut()
                cfg = load_config()
                cfg["desktop_shortcut"] = "off"
                save_config(cfg)
                say(t("launcher.shortcut_removed", n=len(removed)))
            else:
                paths = install_shortcut()
                cfg = load_config()
                cfg["desktop_shortcut"] = "created"
                save_config(cfg)
                for p in paths:
                    say(f"✓ {p}")
        elif cmd == "update":
            update()
        else:
            say(USAGE)
            return 2
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 – every failure must reach the user, terminal or not
        msg = str(e) or e.__class__.__name__
        say("✗ " + msg)
        show_error(msg)
        return 1
    return 0
