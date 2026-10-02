#!/bin/bash
# coco one-line installer (macOS / Linux):
#   curl -fsSL https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.sh | bash
# Installs into ~/coco (override: COCO_HOME=/path), or updates an existing install in place —
# library/, memory/ and coco.config.json are never touched. Then starts coco and adds it to
# Applications, so from now on it opens from Launchpad / Spotlight like any app.
# 一行安装：装到 ~/coco（已安装则原地更新，不动会议库、记忆与配置），然后启动并加入「应用程序」。
set -e
REPO="${COCO_REPO:-https://github.com/cocohahaha/coco}"
DEST="${COCO_HOME:-$HOME/coco}"

have_git() {
  command -v git >/dev/null 2>&1 || return 1
  # macOS: /usr/bin/git is a stub that pops up the developer-tools installer when they are missing
  if [ "$(uname -s)" = "Darwin" ] && [ "$(command -v git)" = "/usr/bin/git" ]; then
    xcode-select -p >/dev/null 2>&1 || return 1
  fi
  return 0
}

overlay_tarball() {  # download the latest code and copy it over $DEST, keeping user data
  tmp="$(mktemp -d)"
  curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$tmp"
  src="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -1)"
  mkdir -p "$DEST"
  (cd "$src" && tar -cf - --exclude ./library --exclude ./memory --exclude ./logs --exclude ./.venv \
      --exclude ./coco.config.json .) | (cd "$DEST" && tar -xf -)
  rm -rf "$tmp"
  rm -f "$DEST/run.sh" "$DEST/run.bat"  # old launchers, replaced by coco.command / coco.bat
}

if [ -d "$DEST/.git" ] && have_git; then
  echo "[coco] Updating $DEST … / 更新中…"
  git -C "$DEST" pull --ff-only
elif [ -f "$DEST/coco/__init__.py" ]; then
  echo "[coco] Updating $DEST … / 更新中…"
  overlay_tarball
elif have_git; then
  echo "[coco] Downloading into $DEST … / 下载到 $DEST …"
  git clone --depth 1 "$REPO.git" "$DEST"
else
  echo "[coco] Downloading into $DEST … / 下载到 $DEST …"
  overlay_tarball
fi
chmod +x "$DEST/coco.command" "$DEST/bin/coco" 2>/dev/null || true
exec "$DEST/coco.command"
