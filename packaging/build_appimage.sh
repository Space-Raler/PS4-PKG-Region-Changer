#!/usr/bin/env bash
# Build a portable AppImage (Linux).  Needs: python3 with tkinter, pip install pyinstaller,
# curl (only if appimagetool is not already installed).  Output: dist/*.AppImage
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import ps4_pkg_region_gui as g; print(g.__version__)')"
ARCH="${ARCH:-$(uname -m)}"
NAME="ps4-pkg-region-changer"
OUT="dist/PS4-PKG-Region-Changer-${VERSION}-${ARCH}.AppImage"

rm -rf build/appimage dist/"$NAME" "$OUT"
mkdir -p build/appimage dist

EXTRA=()
[ -d bundled-tools ] && EXTRA=(--add-data "$PWD/bundled-tools:bundled-tools") && echo "Embedding bundled-tools/"

# 1) freeze the app (bundles Python + Tcl/Tk, so the target needs nothing installed)
python3 -m PyInstaller --noconfirm --clean --onedir --name "$NAME" \
    --distpath build/appimage/pyi-dist --workpath build/appimage/pyi-work \
    --specpath build/appimage ${EXTRA[@]+"${EXTRA[@]}"} "$PWD/ps4_pkg_region_gui.py"

# 2) assemble the AppDir
APPDIR="build/appimage/AppDir"
mkdir -p "$APPDIR/usr/lib"
cp -r "build/appimage/pyi-dist/$NAME" "$APPDIR/usr/lib/$NAME"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/ps4-pkg-region-changer/ps4-pkg-region-changer" "$@"
RUN
chmod +x "$APPDIR/AppRun"
cp packaging/ps4-pkg-region-changer.desktop "$APPDIR/"
cp packaging/icon.png "$APPDIR/$NAME.png"
ln -sf "$NAME.png" "$APPDIR/.DirIcon"

# 3) pack it
TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$TOOL" ]; then
    TOOL="build/appimagetool-${ARCH}.AppImage"
    if [ ! -x "$TOOL" ]; then
        echo "Downloading appimagetool ..."
        curl -fL -o "$TOOL" \
          "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
        chmod +x "$TOOL"
    fi
fi
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool run where FUSE is missing (CI, containers)
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "Built $OUT"
