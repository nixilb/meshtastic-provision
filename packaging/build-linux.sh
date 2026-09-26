#!/usr/bin/env bash
# Build the Linux package of the window: a PyInstaller directory under
# dist/meshtastic-provision/ and a Debian package dist/meshtastic-provision_<version>_<arch>.deb
# that installs it under /opt/meshtastic-provision with a menu entry and an
# icon. Needs uv, dpkg-deb and fakeroot (Debian, Ubuntu, Raspberry Pi OS);
# builds for the machine's own architecture (x86_64 here, aarch64 on a Pi).
#
#     packaging/build-linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)"/\1/')
case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64) arch=arm64 ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

echo "== PyInstaller"
uv sync --group build
rm -rf build dist/meshtastic-provision
uv run --group build pyinstaller --noconfirm --distpath dist --workpath build packaging/meshtastic-provision.spec

echo "== icon"
# Render the SVG to the PNG sizes desktops look for, with Qt (no extra tool).
uv run python - <<'PY'
from pathlib import Path
from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QIcon
app = QGuiApplication([])
icon = QIcon("packaging/meshtastic-provision.svg")
for size in (48, 128, 256):
    out = Path(f"build/icons/{size}x{size}/apps"); out.mkdir(parents=True, exist_ok=True)
    icon.pixmap(QSize(size, size)).save(str(out / "meshtastic-provision.png"))
PY

echo "== .deb"
pkg=build/deb
rm -rf "$pkg"
mkdir -p "$pkg/DEBIAN" "$pkg/opt" "$pkg/usr/share/applications" "$pkg/usr/share/icons/hicolor"
cp -r dist/meshtastic-provision "$pkg/opt/meshtastic-provision"
cp packaging/meshtastic-provision.desktop "$pkg/usr/share/applications/"
cp -r build/icons/* "$pkg/usr/share/icons/hicolor/"
mkdir -p "$pkg/usr/share/icons/hicolor/scalable/apps" "$pkg/usr/lib/udev/rules.d"
cp packaging/meshtastic-provision.svg "$pkg/usr/share/icons/hicolor/scalable/apps/"
cp packaging/70-meshtastic-provision.rules "$pkg/usr/lib/udev/rules.d/"
# After install: apply the udev rule to nodes already plugged in.
cat > "$pkg/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=tty --action=add || true
fi
POSTINST
chmod 755 "$pkg/DEBIAN/postinst"
size_kb=$(du -sk "$pkg/opt" | cut -f1)
cat > "$pkg/DEBIAN/control" <<CONTROL
Package: meshtastic-provision
Version: $version
Section: utils
Priority: optional
Architecture: $arch
Installed-Size: $size_kb
Depends: libc6, libglib2.0-0t64 | libglib2.0-0, libxkbcommon0, libfontconfig1, libfreetype6, libdbus-1-3, libegl1, libgl1, libxcb-cursor0 | libxcb-cursor-dev
Recommends: policykit-1
Maintainer: meshtastic-provision
Description: Flash a Meshtastic node over USB and apply its settings
 Prepares a Meshtastic node plugged in over USB: installs the firmware
 and applies a settings profile, for the meshtastic-desktop app.
CONTROL
fakeroot dpkg-deb --build --root-owner-group "$pkg" "dist/meshtastic-provision_${version}_${arch}.deb"
ls -la dist/*.deb
