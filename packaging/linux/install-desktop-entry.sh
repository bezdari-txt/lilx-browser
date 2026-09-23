#!/bin/sh
# Install the lilx icon and launcher for the current user (Linux, X11 and Wayland).
#
#   packaging/linux/install-desktop-entry.sh            # run from a source checkout (uses ./venv)
#   packaging/linux/install-desktop-entry.sh --binary   # for a PyInstaller build: Exec=lilx on PATH
#
# Icons come from packaging/linux/icons (generated from logo.png by scripts/build_icons.py).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

mkdir -p "$DATA/icons" "$DATA/applications"
cp -R "$HERE/icons/hicolor" "$DATA/icons/"

if [ "${1:-}" = "--binary" ]; then
    EXEC="lilx %U"
else
    EXEC="\"$ROOT/venv/bin/python\" -m lilx %U"
fi
# The checkout's location is only known here, at install time, so it is filled in now.
sed -e "s|^Exec=.*|Exec=$EXEC|" "$HERE/lilx.desktop" > "$DATA/applications/lilx.desktop"
if [ "${1:-}" != "--binary" ]; then
    printf 'Path=%s\n' "$ROOT" >> "$DATA/applications/lilx.desktop"
fi

command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$DATA/applications" || true
echo "Installed $DATA/applications/lilx.desktop and icons in $DATA/icons/hicolor"
