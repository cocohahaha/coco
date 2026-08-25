@echo off
chcp 65001 >nul
rem coco 一键启动（Windows）：首次运行自动建虚拟环境并装依赖，之后起服务并打开浏览器。
rem 关闭这个窗口即停止服务；重复双击不会重复起服务，只会再开一个浏览器页。
setlocal
pushd "%~dp0" || (echo [coco] 无法进入项目目录：%~dp0 & pause & exit /b 1)
set "ROOT=%CD%"
set PYTHONUTF8=1
set "PYTHONPATH=%ROOT%"
if "%COCO_PORT%"=="" (set PORT=8765) else (set PORT=%COCO_PORT%)
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [coco] 首次运行：创建 Python 虚拟环境…
  py -3 -m venv .venv 2>nul
  if not exist "%PY%" python -m venv .venv 2>nul
)
if not exist "%PY%" (
  echo [coco] 找不到 Python。请安装 Python 3.10 或更新版本：https://www.python.org/downloads/
  echo        安装时勾选 "Add python.exe to PATH"，装完后重新双击 run.bat
  pause
  exit /b 1
)
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [coco] Python 版本过低，需要 3.10 或更新版本。请升级后删除 .venv 文件夹再重新运行
  pause
  exit /b 1
)

rem 每次都确认核心依赖在（上次安装中断/失败时不会带着半个环境去起服务）
"%PY%" -c "import fastapi, uvicorn, multipart" >nul 2>&1
if errorlevel 1 (
  echo [coco] 安装依赖（含转写引擎 faster-whisper，约几百 MB，请耐心等待）…
  "%PY%" -m pip install --upgrade pip >nul 2>&1
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [coco] 转写引擎安装失败，改为只装核心依赖：音频转写不可用，文字稿导入与全部分析功能不受影响。
    echo        之后可手动重试：.venv\Scripts\python.exe -m pip install faster-whisper
    "%PY%" -m pip install "fastapi>=0.110" "uvicorn>=0.29" "python-multipart>=0.0.9"
  )
  "%PY%" -c "import fastapi, uvicorn, multipart" >nul 2>&1
  if errorlevel 1 (
    echo [coco] 依赖安装失败，请检查网络后重新双击 run.bat
    pause
    exit /b 1
  )
)

where claude >nul 2>&1
if errorlevel 1 (
  echo [coco] 提示：没有找到 claude 命令。AI 分析需要已安装并登录的 Claude Code CLI：
  echo        https://claude.com/claude-code   安装后在任意终端运行一次 claude 完成登录，再重新双击 run.bat
  echo.
)
echo [coco] 启动服务 → http://127.0.0.1:%PORT%   （关闭本窗口即停止服务）
"%PY%" -m coco web --port %PORT% --open
if errorlevel 1 (
  echo.
  echo [coco] 服务异常退出，请把上面的报错信息截图反馈
  pause
)
popd
