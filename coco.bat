@echo off
chcp 65001 >nul
rem coco — double-click to start (Windows).
rem Only finds or builds the Python environment; dependencies, port, background server, browser and
rem the Start-menu / desktop shortcut are handled by "python -m coco start", the same on every OS.
rem 双击启动：这里只负责准备 Python 环境，其余交给 coco 自己的启动器。启动后本窗口可以关闭。
setlocal
pushd "%~dp0" || (echo [coco] Cannot enter the folder / 无法进入目录：%~dp0 & pause & exit /b 1)
set "ROOT=%CD%"
set PYTHONUTF8=1
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)"

if exist "%PY%" (
  "%PY%" -c "%CHECK%" >nul 2>&1
  if errorlevel 1 (
    echo [coco] Rebuilding the Python environment ^(old Python^)... / 重建 Python 环境...
    rmdir /s /q "%ROOT%\.venv"
  )
)
if exist "%PY%" goto run

echo [coco] First run: preparing Python... / 首次运行：准备 Python 环境...
rem 1) the Python launcher "py" (python.org installs), newest first
for %%V in (3.12 3.11 3.13 3.10) do (
  if not exist "%PY%" (
    py -%%V -c "%CHECK%" >nul 2>&1 && py -%%V -m venv "%ROOT%\.venv" >nul 2>&1
  )
)
rem an environment that cannot install anything (no pip, no uv) is useless: start over
if exist "%PY%" (
  "%PY%" -m pip --version >nul 2>&1 || (where uv >nul 2>&1 || rmdir /s /q "%ROOT%\.venv")
)
rem 2) uv — installs its own Python, no Microsoft Store / python.org step needed
if not exist "%PY%" (
  set "UV="
  for /f "delims=" %%U in ('where uv 2^>nul') do if not defined UV set "UV=%%U"
  if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not defined UV (
    echo [coco] Installing uv, a small Python manager ^(one time^)... / 安装 uv（一次性）...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "$env:UV_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  )
)
if not exist "%PY%" if defined UV (
  "%UV%" venv --seed --quiet --python 3.12 "%ROOT%\.venv"
)
if not exist "%PY%" (
  echo.
  echo [coco] Could not set up Python. Install Python 3.12 from https://www.python.org/downloads/
  echo        ^(tick "Add python.exe to PATH"^), then double-click coco.bat again.
  echo        无法准备 Python 环境。请从 python.org 安装 Python 3.12（勾选 Add python.exe to PATH），再双击 coco.bat。
  pause
  exit /b 1
)

:run
"%PY%" -m coco start %*
if errorlevel 1 (
  echo.
  echo [coco] Start failed, see the message above. / 启动失败，请查看上面的信息。
  pause
  exit /b 1
)
popd
