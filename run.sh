#!/bin/zsh
# coco 一键启动：起服务（如未运行）并打开浏览器
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${COCO_PORT:-8765}"

if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  mkdir -p "$ROOT/logs"
  nohup "$ROOT/.venv/bin/python" -m coco web --port "$PORT" >> "$ROOT/logs/coco.log" 2>&1 &
  echo "启动 coco 服务中…"
  for i in {1..40}; do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
    sleep 0.5
  done
fi

open "http://127.0.0.1:$PORT"
echo "coco 已就绪 → http://127.0.0.1:$PORT（日志：logs/coco.log）"
