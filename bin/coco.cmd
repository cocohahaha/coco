@echo off
rem coco 命令行入口（Windows）：用项目自带 venv 运行，如 bin\coco list
setlocal
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0.."
"%~dp0..\.venv\Scripts\python.exe" -m coco %*
