# coco one-line installer (Windows PowerShell):
#   irm https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.ps1 | iex
# Installs into %USERPROFILE%\coco (override: $env:COCO_HOME), or updates an existing install in
# place — library\, memory\ and coco.config.json are never touched. Then starts coco and adds it to
# the Start menu and the desktop.
# 一行安装：装到 %USERPROFILE%\coco（已安装则原地更新，不动会议库、记忆与配置），然后启动并创建开始菜单/桌面快捷方式。
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is 10x slower with the progress bar
$repo = if ($env:COCO_REPO) { $env:COCO_REPO } else { 'https://github.com/cocohahaha/coco' }
$dest = if ($env:COCO_HOME) { $env:COCO_HOME } else { Join-Path $HOME 'coco' }
$git = Get-Command git -ErrorAction SilentlyContinue

function Update-FromZip {
  $tmp = Join-Path $env:TEMP ("coco-" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $zip = Join-Path $tmp 'coco.zip'
  Invoke-WebRequest "$repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
  Expand-Archive $zip -DestinationPath $tmp -Force
  $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
  New-Item -ItemType Directory -Path $dest -Force | Out-Null
  # robocopy exit codes 0-7 mean success
  robocopy $src.FullName $dest /E /NFL /NDL /NJH /NJS /NP /XD library memory logs .venv /XF coco.config.json | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "copy failed ($LASTEXITCODE)" }
  Remove-Item $tmp -Recurse -Force
  # old launchers, replaced by coco.bat / coco.command
  Remove-Item (Join-Path $dest 'run.bat'), (Join-Path $dest 'run.sh') -ErrorAction SilentlyContinue
}

if ((Test-Path (Join-Path $dest '.git')) -and $git) {
  Write-Host "[coco] Updating $dest ... / 更新中..."
  git -C $dest pull --ff-only
} elseif (Test-Path (Join-Path $dest 'coco\__init__.py')) {
  Write-Host "[coco] Updating $dest ... / 更新中..."
  Update-FromZip
} elseif ($git) {
  Write-Host "[coco] Downloading into $dest ... / 下载到 $dest ..."
  git clone --depth 1 "$repo.git" $dest
} else {
  Write-Host "[coco] Downloading into $dest ... / 下载到 $dest ..."
  Update-FromZip
}
& (Join-Path $dest 'coco.bat')
