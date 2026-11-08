#!/usr/bin/env python3
"""Download sfo for THIS operating system into bundled-tools/sfo/ so the next build embeds it.
Run it on the OS you are building for (needs internet):  python packaging/fetch_sfo.py
orbis-pub-cmd cannot be fetched automatically: copy your own folder to bundled-tools/orbis-pub-cmd/."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ps4_pkg_region_gui as g  # noqa: E402

out = g.download_sfo(ROOT / "bundled-tools" / "sfo")
print(f"sfo saved to {out}")
