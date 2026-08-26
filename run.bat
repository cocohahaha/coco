@echo off
chcp 65001 >nul
rem coco one-click start (Windows): first run creates the venv and installs dependencies, then starts the server and opens the browser.
rem Close this window to stop; double-clicking again only opens another browser tab. / 关闭窗口即停止服务
setlocal
pushd "%~dp0" || (echo [coco] Cannot enter the project folder / 无法进入项目目录：%~dp0 & pause & exit /b 1)
set "ROOT=%CD%"
set PYTHONUTF8=1
set "PYTHONPATH=%ROOT%"
if "%COCO_PORT%"=="" (set PORT=8765) else (set PORT=%COCO_PORT%)
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [coco] First run: creating the Python virtual environment... / 首次运行：创建虚拟环境…
  py -3 -m venv .venv 2>nul
  if not exist "%PY%" python -m venv .venv 2>nul
)
if not exist "%PY%" (
  echo [coco] Python not found. Install Python 3.10+ / 找不到 Python，请安装：https://www.python.org/downloads/
  echo        Tick "Add python.exe to PATH" during setup, then double-click run.bat again / 安装时勾选 Add python.exe to PATH
  pause
  exit /b 1
)
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [coco] Python is too old, 3.10+ is required. Upgrade, delete the .venv folder and run again / Python 版本过低
  pause
  exit /b 1
)

rem Always verify the core dependencies (an interrupted install must not start a half-built environment)
"%PY%" -c "import fastapi, uvicorn, multipart" >nul 2>&1
if errorlevel 1 (
  echo [coco] Installing dependencies (incl. faster-whisper, a few hundred MB, please wait)... / 安装依赖中…
  "%PY%" -m pip install --upgrade pip >nul 2>&1
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [coco] The transcription engine failed to install; installing the core only. Audio cannot be transcribed here, transcript import and all analysis features still work. / 转写引擎安装失败，只装核心依赖
    echo        Retry later with: .venv\Scripts\python.exe -m pip install faster-whisper
    "%PY%" -m pip install "fastapi>=0.110" "uvicorn>=0.29" "python-multipart>=0.0.9" "pypdf>=4.0"
  )
  "%PY%" -c "import fastapi, uvicorn, multipart" >nul 2>&1
  if errorlevel 1 (
    echo [coco] Dependency installation failed. Check your network and double-click run.bat again / 依赖安装失败
    pause
    exit /b 1
  )
)

where claude >nul 2>&1
if errorlevel 1 (
  echo [coco] Note: the claude command was not found. Analysis needs a logged-in Claude Code CLI (or an API channel in Settings): / 没有找到 claude 命令
  echo        https://claude.com/claude-code   install it, run claude once to log in, then double-click run.bat again
  echo.
)
echo [coco] Starting → http://127.0.0.1:%PORT%   (close this window to stop / 关闭本窗口即停止服务)
"%PY%" -m coco web --port %PORT% --open
if errorlevel 1 (
  echo.
  echo [coco] The server exited unexpectedly; please report the error above / 服务异常退出
  pause
)
popd
