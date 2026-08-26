#!/bin/zsh
# coco one-click start: launch the server (if not running) and open the browser / 一键启动
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${COCO_PORT:-8765}"

# 关键：必须在项目根目录下运行。coco 包没有装进 venv，python -m coco 依赖
# 「当前工作目录是项目根」才能 import；双击 coco.command 时 cwd 是用户主目录，
# 不 cd 进来就会报 No module named coco、服务静默起不来（左侧面板因此空白）。
cd "$ROOT" || { echo "❌ Project directory not found / 找不到项目目录：$ROOT"; exit 1; }

if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  mkdir -p "$ROOT/logs"
  PYTHONPATH="$ROOT" nohup "$ROOT/.venv/bin/python" -m coco web --port "$PORT" \
    >> "$ROOT/logs/coco.log" 2>&1 &
  echo "Starting coco… / 启动 coco 服务中…"
  for i in {1..40}; do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
    sleep 0.5
  done
fi

# 等待循环结束后再确认一次：仍未监听说明启动失败，明确报错而不是打开一个死页面
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "❌ coco failed to start, see the log / 启动失败，请查看日志：$ROOT/logs/coco.log"
  exit 1
fi

open "http://127.0.0.1:$PORT"
echo "coco ready / 已就绪 → http://127.0.0.1:$PORT  (log: logs/coco.log)"
