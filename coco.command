#!/bin/bash
# coco — double-click to start (macOS). Linux / terminal: ./coco.command
#
# Only finds or builds the Python environment; everything else (dependencies, port, background
# server, browser, Applications icon) is done by `python -m coco start`, the same on every OS.
# 双击启动：这里只负责准备 Python 环境，其余交给 coco 自己的启动器。
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || { echo "❌ coco folder not found / 找不到 coco 目录：$ROOT"; exit 1; }
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY="$ROOT/.venv/bin/python"
ARM_MAC=""
[ "$(uname -s)" = "Darwin" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] && ARM_MAC=1

# Python 3.10+; on Apple Silicon it must be a native arm64 build (an Intel Python under Rosetta
# cannot install the fast mlx-whisper engine). 需要 3.10+，Apple 芯片上必须是原生 arm64 版。
good_python() {
  "$1" -c 'import sys, platform
ok = sys.version_info >= (3, 10) and sys.maxsize > 2**32
if "'"$ARM_MAC"'": ok = ok and platform.machine() == "arm64"
sys.exit(0 if ok else 1)' 2>/dev/null
}

# pip or uv is needed to install anything into the environment (Debian/Ubuntu without python3-venv
# leave a .venv without pip behind)
can_install() { "$1" -m pip --version >/dev/null 2>&1 || command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; }

if [ -x "$PY" ] && ! { good_python "$PY" && can_install "$PY"; }; then
  echo "[coco] Rebuilding the Python environment (old, non-native or incomplete)… / 重建 Python 环境（版本过旧、非原生或不完整）…"
  rm -rf "$ROOT/.venv"
fi

if [ ! -x "$PY" ]; then
  echo "[coco] First run: preparing Python… / 首次运行：准备 Python 环境…"
  for cand in python3.12 python3.11 python3.13 python3.10 python3; do
    c="$(command -v "$cand" 2>/dev/null)"
    # /usr/bin/python3 is Apple's 3.9 stub: running it without the developer tools pops up an installer
    [ -z "$c" ] || [ "$c" = "/usr/bin/python3" ] && continue
    if good_python "$c"; then
      if "$c" -m venv "$ROOT/.venv" >/dev/null 2>&1 && can_install "$PY"; then break; fi
      rm -rf "$ROOT/.venv"
    fi
  done
  if [ ! -x "$PY" ]; then
    # No suitable Python: uv (one small file) brings its own. 没有合适的 Python：用 uv 自带的
    UV="$(command -v uv || ls "$HOME/.local/bin/uv" 2>/dev/null)"
    if [ -z "$UV" ]; then
      echo "[coco] Installing uv, a small Python manager (one time)… / 安装 uv（一次性）…"
      curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1
      UV="$(command -v uv || ls "$HOME/.local/bin/uv" 2>/dev/null)"
    fi
    REQ="3.12"
    [ -n "$ARM_MAC" ] && REQ="cpython-3.12-macos-aarch64"
    [ -n "$UV" ] && { "$UV" venv --seed --quiet --python "$REQ" "$ROOT/.venv" || rm -rf "$ROOT/.venv"; }
  fi
  if [ ! -x "$PY" ]; then
    echo "❌ Could not set up Python / 无法准备 Python 环境。"
    echo "   Install Python 3.12 from https://www.python.org/downloads/ and double-click coco.command again."
    echo "   请从 https://www.python.org/downloads/ 安装 Python 3.12，然后重新双击 coco.command。"
    exit 1
  fi
fi

"$PY" -m coco start "$@"
code=$?
if [ $code -eq 0 ] && [ -t 1 ] && [ "$(uname -s)" = "Darwin" ]; then
  echo ""
  echo "✓ You can close this window. / 这个窗口可以关掉了。"
fi
exit $code
