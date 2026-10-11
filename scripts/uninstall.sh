#!/usr/bin/env bash
# Removes the app. Your history/settings are kept unless you pass --purge-data.
set -euo pipefail
APP="${LD_PREFIX:-$HOME/.local/share/linux-doctor-app}"
rm -rf "$APP"
rm -f "$HOME/.local/bin/linux-doctor" "$HOME/.local/share/applications/linux-doctor.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/linux-doctor.svg"
if [ "${1:-}" = "--purge-data" ]; then
  rm -rf "$HOME/.local/share/linux-doctor" "$HOME/.config/linux-doctor"
  echo "Application and ALL data removed."
else
  echo "Application removed. Data kept in ~/.local/share/linux-doctor and ~/.config/linux-doctor (use --purge-data to delete)."
fi
