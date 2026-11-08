#!/usr/bin/env bash
# Build a macOS .app and zip it.  Needs: python.org Python 3 (with tkinter) + pip install pyinstaller
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import ps4_pkg_region_gui as g; print(g.__version__)')"
ARCH="$(uname -m)"
APP="PS4 PKG Region Changer"
OUT="dist/PS4-PKG-Region-Changer-${VERSION}-macos-${ARCH}.zip"

rm -rf "dist/${APP}.app" "$OUT"
EXTRA=()
[ -d bundled-tools ] && EXTRA=(--add-data "$PWD/bundled-tools:bundled-tools") && echo "Embedding bundled-tools/"
python3 -m PyInstaller --noconfirm --clean --windowed --name "$APP" \
    --icon "$PWD/packaging/icon.icns" --osx-bundle-identifier com.example.ps4pkgregionchanger \
    --distpath dist --workpath build/mac --specpath build/mac ${EXTRA[@]+"${EXTRA[@]}"} "$PWD/ps4_pkg_region_gui.py"
ditto -c -k --sequesterRsrc --keepParent "dist/${APP}.app" "$OUT"
echo "Built $OUT"
