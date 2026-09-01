#!/bin/zsh
# coco one-click start (macOS): first run builds the environment, then starts the
# server (if not running) and opens the browser.
# 一键启动：首次运行自动创建环境并安装依赖，之后起服务并打开浏览器，重复运行不重复起服务。
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${COCO_PORT:-8765}"

# 关键：必须在项目根目录下运行。coco 包没有装进 venv，python -m coco 依赖
# 「当前工作目录是项目根」才能 import；双击 coco.command 时 cwd 是用户主目录，
# 不 cd 进来就会报 No module named coco、服务静默起不来（左侧面板因此空白）。
cd "$ROOT" || { echo "❌ Project directory not found / 找不到项目目录：$ROOT"; exit 1; }
# 双击 .command 时是非登录 shell，PATH 常缺 claude / python 的安装目录：补上常见位置
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY="$ROOT/.venv/bin/python"

# 已经在跑（重复双击最常见）：直接开浏览器就走，不再花时间做环境预检
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  open "http://127.0.0.1:$PORT"
  echo "coco already running / 已在运行 → http://127.0.0.1:$PORT"
  exit 0
fi

# ---------- first run: build the venv / 首次运行自动建环境 ----------
if [[ ! -x "$PY" ]]; then
  # macOS 自带的 python3 长期停在 3.9，coco 需要 3.10+：按新到旧找一个可用的
  BASE=""
  for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1 \
       && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      BASE="$cand"; break
    fi
  done
  if [[ -z "$BASE" ]]; then
    echo "❌ Python 3.10+ not found / 没有找到 Python 3.10 或更新版本。"
    echo "   请先安装：https://www.python.org/downloads/macos/（下载 → 双击安装），装完后重新双击 coco.command"
    exit 1
  fi
  echo "[coco] First run: creating the Python environment… / 首次运行：创建 Python 虚拟环境（$BASE）…"
  "$BASE" -m venv .venv || { echo "❌ venv creation failed / 创建虚拟环境失败"; exit 1; }
fi

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null || {
  echo "❌ The .venv uses an old Python / 现有 .venv 的 Python 版本过低（需 3.10+）。"
  echo "   请删除项目里的 .venv 文件夹后重新双击（会用新版 Python 重建）"
  exit 1
}

# 每次都确认核心依赖在：上次安装中断/失败时不带着半个环境去起服务
if ! "$PY" -c 'import fastapi, uvicorn, multipart' 2>/dev/null; then
  echo "[coco] Installing dependencies (first time takes a few minutes)… / 安装依赖（首次需要几分钟）…"
  "$PY" -m pip install --upgrade pip >/dev/null 2>&1
  if ! "$PY" -m pip install -r requirements.txt; then
    echo "[coco] Transcription engine failed to install; core only (audio transcription off, transcript import + analysis unaffected)."
    echo "       转写引擎安装失败，改为只装核心依赖：音频转写不可用，文字稿导入与全部分析功能不受影响。"
    "$PY" -m pip install 'fastapi>=0.110' 'uvicorn>=0.29' 'python-multipart>=0.0.9' 'pypdf>=4.0'
  fi
  "$PY" -c 'import fastapi, uvicorn, multipart' 2>/dev/null || {
    echo "❌ Dependency install failed / 依赖安装失败，请检查网络后重新运行"; exit 1; }
fi

# claude CLI 只是提示，不阻塞启动：界面里也会引导（可改用 API 通道）
if ! command -v claude >/dev/null 2>&1; then
  echo "[coco] Tip: no 'claude' command found. AI analysis needs Claude Code CLI (or an API channel in ⚙ Settings):"
  echo "       提示：没有找到 claude 命令。安装：curl -fsSL https://claude.ai/install.sh | bash  然后运行 claude 登录；"
  echo "       或启动后在界面「⚙ 设置」里改用 DeepSeek / OpenAI 等 API 通道。"
fi

# ---------- start / 启动 ----------
mkdir -p "$ROOT/logs"
PYTHONPATH="$ROOT" nohup "$PY" -m coco web --port "$PORT" \
  >> "$ROOT/logs/coco.log" 2>&1 &
SRV=$!
echo "Starting coco… / 启动 coco 服务中…"
# 首次启动要冷加载依赖、macOS 还会扫描新装的二进制，可能要 30-60 秒，别过早判死；
# 但进程一死就立刻退出等待，不让用户对着死端口干等一分钟
for i in {1..120}; do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
  kill -0 "$SRV" 2>/dev/null || break
  [[ $i == 30 ]] && echo "(first start can take up to a minute… / 首次启动可能需要一分钟，请稍等)"
  sleep 0.5
done

# 等待循环结束后再确认一次：仍未监听说明启动失败，明确报错而不是打开一个死页面
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "❌ coco failed to start, see the log / 启动失败，请查看日志：$ROOT/logs/coco.log"
  exit 1
fi

open "http://127.0.0.1:$PORT"
echo "coco ready / 已就绪 → http://127.0.0.1:$PORT  (log: logs/coco.log)"
