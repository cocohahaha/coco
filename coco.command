#!/bin/zsh
# 在 Finder 里双击此文件即可启动 coco
exec "$(cd "$(dirname "$0")" && pwd)/run.sh"
