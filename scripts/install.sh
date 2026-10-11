#!/usr/bin/env bash
# Per-user install. Needs NO root privileges. Everything lives under ~/.local.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${LD_PREFIX:-$HOME/.local/share/linux-doctor-app}"
BIN="$HOME/.local/bin"

[ "$(id -u)" -ne 0 ] || { echo "Do not run this as root. Run as your normal user."; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Python 3.10+ required"; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null || { echo "Missing venv support. Run: sudo apt install python3-venv"; exit 1; }

echo "==> Creating isolated environment in $APP/venv"
mkdir -p "$APP" "$BIN"
python3 -m venv "$APP/venv"
"$APP/venv/bin/python" -m pip install --quiet --upgrade pip
EXTRA="${LD_EXTRAS:-gui}"          # LD_EXTRAS=none for CLI-only installs
if [ "$EXTRA" = "none" ]; then "$APP/venv/bin/pip" install --quiet "$SRC"; else "$APP/venv/bin/pip" install --quiet "$SRC[$EXTRA]"; fi

ln -sf "$APP/venv/bin/linux-doctor" "$BIN/linux-doctor"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$SRC/packaging/linux-doctor.desktop" "$HOME/.local/share/applications/"
cp "$SRC/linux_doctor/resources/linux-doctor.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true

echo "==> Verifying"
"$BIN/linux-doctor" selfcheck
case ":$PATH:" in *":$BIN:"*) ;; *) echo "NOTE: add $BIN to your PATH (log out/in on Ubuntu usually does this).";; esac
echo "Installed. Run: linux-doctor gui    (or: linux-doctor scan full_health)"
