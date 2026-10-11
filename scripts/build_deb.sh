#!/usr/bin/env bash
# Builds a .deb that installs to /opt/linux-doctor and creates its venv in postinst (needs internet at install time).
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml")"
OUT="$SRC/dist"; ROOT="$(mktemp -d)"; PKG="$ROOT/linux-doctor_${VER}_all"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/linux-doctor/src" "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" "$OUT"
cp -r "$SRC/linux_doctor" "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" "$PKG/opt/linux-doctor/src/"
find "$PKG" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$SRC/packaging/linux-doctor.desktop" "$PKG/usr/share/applications/"
cp "$SRC/linux_doctor/resources/linux-doctor.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cat > "$PKG/usr/bin/linux-doctor" <<'WRAP'
#!/bin/sh
exec /opt/linux-doctor/venv/bin/linux-doctor "$@"
WRAP
chmod 0755 "$PKG/usr/bin/linux-doctor"
cat > "$PKG/DEBIAN/control" <<CTL
Package: linux-doctor
Version: $VER
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-venv, libegl1, libxcb-cursor0
Maintainer: Linux Doctor Maintainers <maintainers@example.invalid>
Description: Local-first Linux diagnostics and safe troubleshooting
 Read-only diagnostics, explainable findings and an optional local AI assistant.
CTL
cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
python3 -m venv /opt/linux-doctor/venv
/opt/linux-doctor/venv/bin/pip install --quiet --upgrade pip
/opt/linux-doctor/venv/bin/pip install --quiet "/opt/linux-doctor/src[gui]"
POST
cat > "$PKG/DEBIAN/prerm" <<'PRE'
#!/bin/sh
set -e
rm -rf /opt/linux-doctor/venv
PRE
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/linux-doctor_${VER}_all.deb"
( cd "$OUT" && sha256sum "linux-doctor_${VER}_all.deb" > "linux-doctor_${VER}_all.deb.sha256" )
echo "Built $OUT/linux-doctor_${VER}_all.deb"
