#!/usr/bin/env bash
# Build the Debian/Ubuntu/Mint package.  Output: dist/ps4-pkg-region-changer_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import ps4_pkg_region_gui as g; print(g.__version__)')"
PKG="ps4-pkg-region-changer"
ROOT="build/deb/${PKG}_${VERSION}"
OUT="dist/${PKG}_${VERSION}_all.deb"

rm -rf "$ROOT" "$OUT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/$PKG" \
         "$ROOT/usr/share/applications" "$ROOT/usr/share/icons/hicolor/512x512/apps" dist

install -m 644 ps4_pkg_region_gui.py "$ROOT/usr/lib/$PKG/ps4_pkg_region_gui.py"
[ -d bundled-tools ] && cp -r bundled-tools "$ROOT/usr/lib/$PKG/bundled-tools" && echo "Embedding bundled-tools/"
install -m 644 packaging/ps4-pkg-region-changer.desktop "$ROOT/usr/share/applications/$PKG.desktop"
install -m 644 packaging/icon.png "$ROOT/usr/share/icons/hicolor/512x512/apps/$PKG.png"
cat > "$ROOT/usr/bin/$PKG" <<'LAUNCH'
#!/bin/sh
exec python3 /usr/lib/ps4-pkg-region-changer/ps4_pkg_region_gui.py "$@"
LAUNCH
chmod 755 "$ROOT/usr/bin/$PKG"

SIZE="$(du -sk "$ROOT/usr" | cut -f1)"
cat > "$ROOT/DEBIAN/control" <<CTL
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3, python3-tk
Recommends: wine
Installed-Size: $SIZE
Maintainer: ${DEB_MAINTAINER:-Your Name <you@example.com>}
Description: PS4 PKG Region Changer
 GUI wrapper around orbis-pub-cmd and sfo for changing the region / TitleID of
 PS4 PKGs and merging a base game with its update. Install your own copy of
 orbis-pub-cmd with the "Install tools..." button; it runs through wine.
CTL
(cd "$ROOT" && find usr -type f -exec md5sum {} + > DEBIAN/md5sums)

dpkg-deb --root-owner-group -Zxz -b "$ROOT" "$OUT"
echo "Built $OUT"
