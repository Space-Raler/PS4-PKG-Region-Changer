#!/usr/bin/env python3
"""
PS4 PKG Region / TitleID Changer  (GUI)

A Python + Tkinter port of ps4-pkg-dlc-region.cmd v1.4.
It batch-changes the Region, RegionID, TitleID, ContentName and SDK version of
PS4 PKG files (DLC, patch, app) by driving two external command-line tools:

    * orbis-pub-cmd   (orbis-pub-gen)
    * sfo             (by Hippie68)

Both tools must be reachable via PATH, or you can point the GUI at the
folders that contain them.

Platforms: Windows (native orbis-pub-cmd.exe), Linux and macOS (orbis-pub-cmd.exe
through wine).  Only the Python standard library is used (tkinter ships with
Python on Windows/macOS; on Debian/Ubuntu: sudo apt install python3-tk).
"""

import base64
import gzip
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:  # allows the core logic to be imported/tested headless
    tk = None

APP_TITLE = "PS4 PKG Region Changer"
__version__ = "1.3.0"            # bumped automatically by scripts/release.py
APP_SLUG = "ps4-pkg-region-changer"

# GitHub "owner/repo" that publishes the releases the updater looks at.
# Override at runtime with the PS4PKG_UPDATE_REPO environment variable.
UPDATE_REPO = "YOUR-GITHUB-USER/ps4-pkg-region-changer"
# Where "Get sfo..." looks for Hippie68's sfo releases.
SFO_REPO = "hippie68/sfo"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))      # PyInstaller build
MONO_FONT = "Consolas" if IS_WIN else ("Menlo" if IS_MAC else "DejaVu Sans Mono")


def _app_dir() -> Path:
    """Folder that holds the app: the AppImage / exe / script itself (not a temp dir)."""
    if FROZEN:
        return Path(os.environ.get("APPIMAGE") or sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _user_dirs():
    """Per-user (config, data) folders for this OS."""
    if IS_WIN:
        cfg = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_SLUG
        data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_SLUG
    elif IS_MAC:
        cfg = data = Path.home() / "Library/Application Support" / APP_SLUG
    else:
        cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_SLUG
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_SLUG
    return cfg, data


def _data_dirs():
    """Return (settings file, tools dir).  Order of precedence:

    1. PS4PKG_DATA_DIR env var - use that folder for everything.
    2. Portable mode - a file called 'portable.txt' next to the AppImage / exe / script
       (or PS4PKG_PORTABLE=1): everything lives in a 'data' folder beside it.
    3. Running from source / a zip in a writable folder: next to the script (legacy).
    4. Otherwise (installed .deb, frozen builds, read-only mounts): per-user folders.
    """
    override = os.environ.get("PS4PKG_DATA_DIR")
    app = _app_dir()
    portable = bool(os.environ.get("PS4PKG_PORTABLE")) or (app / "portable.txt").exists()
    base = None
    if override:
        base = Path(override)
    elif portable:
        base = app / "data"
    elif not FROZEN and os.access(app, os.W_OK):
        return app / "ps4_pkg_region_gui.json", app / "tools"
    if base is not None:
        try:
            base.mkdir(parents=True, exist_ok=True)
            return base / "settings.json", base / "tools"
        except OSError:
            pass
    cfg, data = _user_dirs()
    try:
        cfg.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return cfg / "settings.json", data / "tools"


SETTINGS_FILE, TOOLS_DIR = _data_dirs()      # drop orbis-pub-cmd / sfo in TOOLS_DIR
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

INFO_KEYS = [
    "CATEGORY", "CONTENT_ID", "FORMAT", "PUBTOOLINFO", "PUBTOOLVER",
    "TITLE", "TITLE_ID", "APP_VER", "VERSION", "USER_DEFINED_PARAM_1",
]
STORAGE_TYPES = ["digital50", "digital25", "bd50_50", "bd50_25", "bd50", "bd25"]
SC0_SKIP = {"license.dat", "license.info", "psreserved.dat",
            "origin-deltainfo.dat", "pubtoolinfo.dat"}

# GUI colour palettes (used by App.apply_theme)
LIGHT = dict(bg="#eef0f5", fg="#1b2333", field="#ffffff", sel="#2f6fed", sel_fg="#ffffff",
             btn="#e2e6ef", btn_active="#d3d9e6", border="#c4cbdb", light="#ffffff",
             dark="#c4cbdb", muted="#5b667c", ok="#118a11", bad="#c00000",
             info="#0b5cad", warn="#b36b00", cmd="#808080",
             side="#1b2333", side_fg="#aab4c8", accent="#2f6fed", accent_fg="#ffffff")
DARK = dict(bg="#12161c", fg="#e4e8ee", field="#0b0e12", sel="#1f6f54", sel_fg="#ffffff",
            btn="#1a2028", btn_active="#252d38", border="#2a313c", light="#2a313c",
            dark="#0b0e12", muted="#8a95a5", ok="#5be08f", bad="#ff6b6b",
            info="#6cb6ff", warn="#e0a040", cmd="#8b9097",
            side="#0b0e12", side_fg="#8a95a5", accent="#3ddc97", accent_fg="#06210f")


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    orbis_dir: str = ""
    sfo_dir: str = ""
    new_region: str = ""          # 2 chars: JP / EP / UP / HP / KP
    new_region_id: str = ""       # 4 digits
    new_title_id: str = ""        # 9 chars, e.g. CUSA01234
    new_content_name: str = ""    # 16 chars
    new_sdk_ver: str = ""         # 8 chars, e.g. 05050000
    new_app_ver: str = ""         # e.g. 01.00 (empty = keep; merge defaults to 01.00)
    gen_dir_name: str = "new"
    icon0_path: str = ""
    passcode: str = "0" * 32
    cleanup: bool = False
    show_status: bool = True
    overwrite_unpacked: bool = False
    overwrite_gp4: bool = True
    pkg_create: bool = True
    pfs_compression: bool = True
    pkg_digest: bool = True
    dark_mode: bool = True
    auto_update_check: bool = True
    merge_base: str = ""          # base game PKG for the merge feature
    merge_update: str = ""        # update/patch PKG for the merge feature
    files: list = field(default_factory=list)


def validate(s: Settings) -> list:
    errs = []
    if s.new_region and len(s.new_region) != 2:
        errs.append("Region must be exactly 2 characters (JP, EP, UP, HP, KP) or empty.")
    if s.new_region_id and not re.fullmatch(r"\d{4}", s.new_region_id):
        errs.append("RegionID must be exactly 4 digits or empty.")
    if s.new_title_id and len(s.new_title_id) != 9:
        errs.append("TitleID must be exactly 9 characters (e.g. CUSA01234) or empty.")
    if s.new_content_name and len(s.new_content_name) != 16:
        errs.append("ContentName must be exactly 16 characters or empty.")
    if s.new_sdk_ver and len(s.new_sdk_ver) != 8:
        errs.append("SdkVer must be exactly 8 characters (e.g. 05050000) or empty.")
    if s.new_app_ver and not re.fullmatch(r"\d{2}\.\d{2}", s.new_app_ver):
        errs.append("App ver must look like 01.00 (two digits, dot, two digits) or be empty.")
    if len(s.passcode) != 32:
        errs.append("Passcode must be exactly 32 characters.")
    if not s.gen_dir_name.strip():
        errs.append("Output folder name must not be empty.")
    return errs


# --------------------------------------------------------------------------- #
#  Core logic (port of the batch script)
# --------------------------------------------------------------------------- #
class Cancelled(Exception):
    pass


class ToolError(Exception):
    pass


def find_bundled(name: str):
    """Look for `name` (or name.exe) inside the local tools folder, 3 levels deep."""
    want = {name.lower(), name.lower() + ".exe"}
    if not TOOLS_DIR.is_dir():
        return None
    base_depth = len(TOOLS_DIR.parts)
    for dirpath, dirnames, filenames in os.walk(TOOLS_DIR):
        if len(Path(dirpath).parts) - base_depth >= 3:
            dirnames[:] = []
        for fn in sorted(filenames):
            if fn.lower() in want:
                return str(Path(dirpath) / fn)
    return None


def make_executable(path: Path):
    if os.name != "nt":
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            pass


def resolve_tool(folder: str, name: str):
    """Find an executable in `folder` first, then in ./tools, then on PATH."""
    if folder:
        for cand in (Path(folder) / (name + ".exe"), Path(folder) / name):
            if cand.is_file():
                return str(cand)
    return find_bundled(name) or shutil.which(name)


# Bundled tools, written into TOOLS_DIR on first start (Linux only):
#  * sfo           - Hippie68's sfo (x86-64 Linux build), gzip + base64
#  * orbis-pub-cmd - a small wrapper that runs orbis-pub-cmd.exe through wine;
#                    the real orbis-pub-cmd.exe still has to be placed next to it
#                    (use "Install tools..." and pick your PS4 Fake PKG Tools folder).
BUNDLED_SFO_B64 = """
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
"""
ORBIS_WRAPPER = """#!/bin/sh
D="$(cd "$(dirname "$0")" && pwd -P)"
EXE="${ORBIS_PUB_CMD_EXE:-$D/orbis-pub-cmd.exe}"
if [ ! -f "$EXE" ]; then
  echo "orbis-pub-cmd.exe not found in $D (use Install tools... or set ORBIS_PUB_CMD_EXE)" >&2
  exit 127
fi
WINE="${WINE:-}"
if [ -z "$WINE" ]; then
  for c in wine wine64 wine-stable \
           "/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine64" \
           "/Applications/Wine Staging.app/Contents/Resources/wine/bin/wine64" \
           /opt/homebrew/bin/wine /usr/local/bin/wine; do
    if command -v "$c" >/dev/null 2>&1; then WINE="$c"; break; fi
  done
fi
if [ -z "$WINE" ]; then
  echo "wine not found. Install it (Linux: your package manager; macOS: brew install --cask wine-stable)." >&2
  exit 127
fi
export WINEDEBUG="${WINEDEBUG:--all}"
exec "$WINE" "$EXE" "$@"
"""


def clean_env() -> dict:
    """Environment for child processes (sfo, orbis-pub-cmd/wine).

    PyInstaller / AppImage builds point LD_LIBRARY_PATH at their bundled libraries, which
    breaks the system's wine; restore the original value.  macOS apps started from Finder
    get a minimal PATH, so add the usual Homebrew locations."""
    env = os.environ.copy()
    if FROZEN:
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
    if IS_MAC:
        env["PATH"] = env.get("PATH", "") + os.pathsep + "/opt/homebrew/bin:/usr/local/bin"
    return env


def find_wine():
    """Locate a wine binary (used only on Linux/macOS), or None."""
    if IS_WIN:
        return None
    if os.environ.get("WINE") and shutil.which(os.environ["WINE"]):
        return shutil.which(os.environ["WINE"])
    for c in ("wine", "wine64", "wine-stable",
              "/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine64",
              "/Applications/Wine Staging.app/Contents/Resources/wine/bin/wine64",
              "/opt/homebrew/bin/wine", "/usr/local/bin/wine"):
        p = shutil.which(c)
        if p:
            return p
    return None


def bundled_sfo_supported() -> bool:
    """The embedded sfo is an x86-64 Linux ELF; nothing else can run it."""
    return sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64")


def bundled_tools_dir() -> Path:
    """Folder with tools that were packed into this build (see packaging/ + README)."""
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) if (FROZEN and meipass) else _app_dir()
    return base / "bundled-tools"


def seed_bundled_tools():
    """Copy tools shipped inside the app (bundled-tools/sfo, bundled-tools/orbis-pub-cmd)
    into TOOLS_DIR, unless a copy is already there (so the user's own install wins)."""
    root = bundled_tools_dir()
    if not root.is_dir():
        return
    for name in ("sfo", "orbis-pub-cmd"):
        src = root / name
        if not src.is_dir() or find_bundled(name):
            continue
        try:
            shutil.copytree(src, TOOLS_DIR / name, dirs_exist_ok=True)
            exe = find_bundled(name)
            if not exe:
                continue
            make_executable(Path(exe))
            if not IS_WIN and exe.lower().endswith(".exe"):
                wrapper = Path(exe).parent / name       # run the .exe through wine
                if not wrapper.exists():
                    wrapper.write_text(ORBIS_WRAPPER, encoding="utf-8")
                make_executable(wrapper)
        except (OSError, shutil.Error):
            pass


def seed_tools():
    """Create ./tools and drop in the bundled sfo + orbis wrapper if they are missing."""
    sfo_dir, orbis_dir = TOOLS_DIR / "sfo", TOOLS_DIR / "orbis-pub-cmd"
    try:
        sfo_dir.mkdir(parents=True, exist_ok=True)
        orbis_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    seed_bundled_tools()
    if IS_WIN:
        return
    try:
        if bundled_sfo_supported() and not find_bundled("sfo") and not shutil.which("sfo"):
            dst = sfo_dir / "sfo"
            dst.write_bytes(gzip.decompress(base64.b64decode(BUNDLED_SFO_B64)))
            make_executable(dst)
        if not find_bundled("orbis-pub-cmd") and not shutil.which("orbis-pub-cmd"):
            dst = orbis_dir / "orbis-pub-cmd"
            dst.write_text(ORBIS_WRAPPER, encoding="utf-8")
            make_executable(dst)
    except (OSError, ValueError):
        pass


# --------------------------------------------------------------------------- #
#  Updates (GitHub Releases) + "Get sfo" downloader
# --------------------------------------------------------------------------- #
class UpdateError(Exception):
    pass


def update_repo() -> str:
    return os.environ.get("PS4PKG_UPDATE_REPO") or UPDATE_REPO


def updater_configured(repo=None) -> bool:
    repo = repo or update_repo()
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)) \
        and not repo.upper().startswith("YOUR-")


def parse_version(v) -> tuple:
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", str(v))
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def _norm(t: tuple) -> tuple:
    return tuple(t) + (0,) * (4 - len(t)) if len(t) < 4 else tuple(t)


def _http_get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_SLUG}/{__version__}",
        "Accept": "application/vnd.github+json, application/octet-stream, */*"})
    return urllib.request.urlopen(req, timeout=timeout)


def _check_url(url: str):
    from urllib.parse import urlparse
    u = urlparse(url)
    host = (u.hostname or "").lower()
    ok = host == "github.com" or host.endswith(".github.com") \
        or host.endswith(".githubusercontent.com")
    if u.scheme != "https" or not ok:
        raise UpdateError(f"Refusing to download from {url}")


def _get_json(url: str):
    try:
        with _http_get(url) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No release found (wrong repo name, or the repo is private).")
        raise UpdateError(f"GitHub answered HTTP {e.code}.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(f"Could not reach GitHub: {e}")


def check_for_update(repo=None, current=None):
    """Return None when up to date, else a dict describing the newer release."""
    repo = repo or update_repo()
    if not updater_configured(repo):
        raise UpdateError("The updater is not configured. Set UPDATE_REPO in "
                          "ps4_pkg_region_gui.py (or the PS4PKG_UPDATE_REPO environment "
                          "variable) to 'your-user/your-repo'.")
    data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = str(data.get("tag_name") or "")
    if _norm(parse_version(tag)) <= _norm(parse_version(current or __version__)):
        return None
    return {
        "version": tag.lstrip("vV"), "tag": tag,
        "url": data.get("html_url") or f"https://github.com/{repo}/releases/latest",
        "notes": data.get("body") or "",
        "assets": [{"name": a.get("name", ""), "url": a.get("browser_download_url", ""),
                    "size": a.get("size", 0)} for a in data.get("assets", [])],
    }


def can_self_update() -> bool:
    """True for builds that can replace themselves: the AppImage and the Windows exe."""
    return bool(os.environ.get("APPIMAGE")) or (IS_WIN and FROZEN)


def pick_update_asset(assets, appimage=None, win_frozen=None, machine=None):
    appimage = os.environ.get("APPIMAGE") if appimage is None else appimage
    win_frozen = (IS_WIN and FROZEN) if win_frozen is None else win_frozen
    machine = (machine or platform.machine()).lower()
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
    for a in assets:
        n = a["name"].lower()
        if appimage and n.endswith(".appimage") and arch in n:
            return a
        if win_frozen and n.endswith(".exe") and "windows" in n:
            return a
    return None


def parse_sums(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def download_file(url: str, dest: Path, progress=None) -> str:
    """Download to `dest`, return its sha256 hex digest. progress(done, total)."""
    _check_url(url)
    h = hashlib.sha256()
    try:
        with _http_get(url, timeout=30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Download failed: {e}")
    return h.hexdigest()


def self_update(info, progress=None, target=None):
    """Replace the running AppImage / Windows exe with the release's build, after
    verifying its SHA-256 against the release's SHA256SUMS.txt.  Returns the path."""
    asset = pick_update_asset(info["assets"])
    if not asset:
        raise UpdateError("This release has no download that matches this build.")
    sums = next((a for a in info["assets"]
                 if a["name"].lower() in ("sha256sums.txt", "sha256sums")), None)
    if not sums:
        raise UpdateError("The release has no SHA256SUMS.txt - refusing to install "
                          "an unverified file.")
    _check_url(sums["url"])
    try:
        with _http_get(sums["url"]) as r:
            want = parse_sums(r.read().decode("utf-8", "replace")).get(asset["name"])
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Could not fetch SHA256SUMS.txt: {e}")
    if not want:
        raise UpdateError(f"{asset['name']} is not listed in SHA256SUMS.txt.")
    if target is None:
        target = Path(os.environ.get("APPIMAGE") or sys.executable)
    target = Path(target)
    new = target.with_name(target.name + ".new")
    try:
        got = download_file(asset["url"], new, progress)
        if got != want:
            raise UpdateError("Checksum mismatch - the download was discarded.")
        make_executable(new)
        if IS_WIN:      # a running exe can be renamed but not overwritten
            old = target.with_name(target.name + ".old")
            try:
                old.unlink()
            except OSError:
                pass
            os.replace(target, old)
            try:
                os.replace(new, target)
            except OSError:
                os.replace(old, target)
                raise
        else:
            os.replace(new, target)
    except OSError as e:
        raise UpdateError(f"Could not write {target}: {e}")
    finally:
        try:
            new.unlink()
        except OSError:
            pass
    return target


def cleanup_old_update():
    if IS_WIN and FROZEN:
        try:
            exe = Path(sys.executable)
            exe.with_name(exe.name + ".old").unlink()
        except OSError:
            pass


def pick_sfo_asset(assets, system=None, machine=None):
    system = system or ("windows" if IS_WIN else "darwin" if IS_MAC else "linux")
    machine = (machine or platform.machine()).lower()
    keys = {"windows": ("win",), "darwin": ("mac", "darwin", "osx"),
            "linux": ("linux",)}[system]
    arm = machine in ("arm64", "aarch64")
    cands = []
    for a in assets:
        n = a["name"].lower()
        if n.endswith((".sig", ".asc", ".txt", ".md", ".sha256", ".sha256sum")):
            continue
        if any(k in n for k in keys) or (system == "windows" and n == "sfo.exe"):
            cands.append(a)
    for a in cands:     # prefer the matching CPU architecture
        n = a["name"].lower()
        if arm == any(t in n for t in ("arm64", "aarch64")):
            return a
    return cands[0] if cands else None


def download_sfo(dest_dir: Path, progress=None, repo=None) -> Path:
    import tarfile
    import tempfile
    import zipfile
    repo = repo or SFO_REPO
    data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    asset = pick_sfo_asset([{"name": a.get("name", ""), "url": a.get("browser_download_url", "")}
                            for a in data.get("assets", [])])
    if not asset:
        raise UpdateError(f"The latest sfo release has no build for this system. Get or "
                          f"build it from https://github.com/{repo} and use Install tools...")
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        tmp = td / asset["name"]
        download_file(asset["url"], tmp, progress)
        n = asset["name"].lower()
        found = None
        if n.endswith(".zip"):
            with zipfile.ZipFile(tmp) as z:
                z.extractall(td / "x")
        elif n.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar")):
            with tarfile.open(tmp) as t:
                if hasattr(tarfile, "data_filter"):
                    t.extractall(td / "x", filter="data")
                else:
                    t.extractall(td / "x")
        else:
            found = tmp
        if found is None:
            for dp, _dn, fns in os.walk(td / "x"):
                for fn in fns:
                    if fn.lower() in ("sfo", "sfo.exe"):
                        found = Path(dp) / fn
                        break
                if found:
                    break
        if found is None:
            raise UpdateError("The sfo download did not contain an sfo executable.")
        out = dest_dir / ("sfo.exe" if IS_WIN else "sfo")
        shutil.copy2(found, out)
        make_executable(out)
    return out


def pad4(value: str) -> str:
    return ("0000" + value.replace(".", ""))[-4:]


def rel_parts(name: str):
    return [p for p in name.replace("\\", "/").split("/") if p]


@dataclass
class Plan:
    content_new_id: str
    new_pkg_name: str
    new_title_id: str
    old_title_id: str
    storage_type: str


# --------------------------------------------------------------------------- #
#  Native PKG parser (no external tools): reads the header, the entry table,
#  param.sfo and icon0.png, like the "Info" view of a PKG viewer.
# --------------------------------------------------------------------------- #
class PkgError(Exception):
    pass


PKG_MAGIC = b"\x7fCNT"
PKG_ENTRY_NAMES = {
    0x0001: "digests", 0x0010: "entry_keys", 0x0020: "image_key",
    0x0080: "general_digests", 0x0100: "metas", 0x0200: "entry_names",
    0x0400: "license.dat", 0x0401: "license.info", 0x0402: "nptitle.dat",
    0x0403: "npbind.dat", 0x1000: "param.sfo", 0x1001: "playgo-chunk.dat",
    0x1003: "playgo-manifest.xml", 0x1200: "icon0.png", 0x1220: "pic0.png",
}
SFO_LANGS = [
    "Japanese", "English (US)", "French", "Spanish", "German", "Italian", "Dutch",
    "Portuguese (PT)", "Russian", "Korean", "Chinese (Trad.)", "Chinese (Simp.)",
    "Finnish", "Swedish", "Danish", "Norwegian", "Polish", "Portuguese (BR)",
    "English (UK)", "Turkish", "Spanish (LA)", "Arabic", "French (CA)", "Czech",
    "Hungarian", "Greek", "Romanian", "Thai", "Vietnamese", "Indonesian",
]
REGION_NAMES = {"UP": "USA", "EP": "Europe", "JP": "Japan", "HP": "Hong Kong / Asia",
                "KP": "Korea"}
CATEGORIES = {"gd": "Base Game", "gp": "Patch / Update", "ac": "Additional Content (DLC)"}


@dataclass
class PkgEntry:
    id: int
    name: str
    offset: int
    size: int
    encrypted: bool


@dataclass
class PkgInfo:
    path: str
    file_size: int
    header: dict
    entries: list
    sfo: dict
    icon: object = None            # PNG bytes or None
    warnings: list = field(default_factory=list)
    header_bytes: bytes = b""


def parse_sfo(data: bytes) -> dict:
    """Parse a PARAM.SFO (PSF) blob into {key: str | int}."""
    if len(data) < 20 or data[:4] != b"\x00PSF":
        raise PkgError("param.sfo has no PSF header.")
    key_off, data_off, count = struct.unpack_from("<III", data, 8)
    out = {}
    for i in range(min(count, 512)):
        pos = 20 + i * 16
        if pos + 16 > len(data):
            break
        ko, fmt, ln, _mx, do = struct.unpack_from("<HHIII", data, pos)
        k0 = key_off + ko
        k1 = data.find(b"\x00", k0)
        if k1 < 0:
            continue
        key = data[k0:k1].decode("utf-8", "replace")
        raw = data[data_off + do:data_off + do + ln]
        if fmt == 0x0404 and len(raw) >= 4:
            out[key] = struct.unpack("<I", raw[:4])[0]
        else:
            out[key] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return out


def parse_pkg(path) -> PkgInfo:
    path = str(path)
    size = os.path.getsize(path)
    warnings = []
    with open(path, "rb") as f:
        head = f.read(0x500)
        if len(head) < 0x100 or head[:4] != PKG_MAGIC:
            raise PkgError("Not a PS4 PKG (the \\x7fCNT header is missing).")

        def u16(o):
            return struct.unpack_from(">H", head, o)[0]

        def u32(o):
            return struct.unpack_from(">I", head, o)[0]

        def u64(o):
            return struct.unpack_from(">Q", head, o)[0] if o + 8 <= len(head) else 0

        hdr = {
            "entry_count": u32(0x10),
            "sc_entry_count": u16(0x14),
            "table_offset": u32(0x18),
            "content_id": head[0x40:0x64].split(b"\x00")[0].decode("ascii", "replace"),
            "drm_type": u32(0x70),
            "content_type": u32(0x74),
            "content_flags": u32(0x78),
            "built": u32(0x80),
            "package_size": u64(0x430),
        }

        raw_entries = []
        if 0 < hdr["entry_count"] <= 20000 and 0 < hdr["table_offset"] < size:
            f.seek(hdr["table_offset"])
            raw = f.read(hdr["entry_count"] * 32)
            for i in range(len(raw) // 32):
                raw_entries.append(struct.unpack_from(">IIIIIIQ", raw, i * 32))
        else:
            warnings.append("The entry table offset/count in the header look invalid.")

        names_blob = b""
        for e in raw_entries:
            if e[0] == 0x200 and 0 < e[4] < size:
                f.seek(e[4])
                names_blob = f.read(min(e[5], 1 << 20))
                break

        entries = []
        for eid, noff, fl1, _fl2, off, sz, _pad in raw_entries:
            name = PKG_ENTRY_NAMES.get(eid, "")
            if not name and names_blob and noff < len(names_blob):
                name = names_blob[noff:].split(b"\x00", 1)[0].decode("utf-8", "replace")
            if not name:
                name = f"entry_0x{eid:04X}"
            entries.append(PkgEntry(eid, name, off, sz, bool((fl1 >> 31) & 1)))

        def read_entry(eid, cap):
            for e in entries:
                if e.id != eid:
                    continue
                if e.encrypted:
                    warnings.append(f"{e.name} is encrypted, so it was not read.")
                    return None
                if e.offset + e.size > size:
                    warnings.append(f"{e.name} lies beyond the end of the file (truncated?).")
                    return None
                f.seek(e.offset)
                return f.read(min(e.size, cap))
            return None

        sfo = {}
        sfo_raw = read_entry(0x1000, 1 << 20)
        if sfo_raw:
            try:
                sfo = parse_sfo(sfo_raw)
            except PkgError as e:
                warnings.append(str(e))
        else:
            warnings.append("param.sfo was not found or could not be read.")
        icon = read_entry(0x1200, 8 << 20)

    ps = hdr["package_size"]
    if 0 < ps < (1 << 45) and size < ps:
        warnings.append(f"The file is smaller than the package size in its header "
                        f"({fmt_size(ps)} vs {fmt_size(size)}); the download may be incomplete.")
    return PkgInfo(path, size, hdr, entries, sfo, icon, warnings, head[:0x200])


def fmt_size(n: int) -> str:
    return f"{n / 1e9:,.2f} GB" if n >= 1e9 else f"{n / 1e6:,.1f} MB"


def fmt_built(v: int) -> str:
    s = f"{v:08x}"
    if s.isdigit():
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:])
        if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"
    return f"0x{v:08X}"


def sys_ver_str(n: int) -> str:
    return f"{(n >> 24) & 0xFF:x}.{(n >> 16) & 0xFF:02x}"


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}  {hexs:<{width * 3 - 1}}  {asc}")
    return "\n".join(lines)


def pkg_summary(info: PkgInfo) -> list:
    """(label, value) rows for the Info view."""
    s, h = info.sfo, info.header
    cid = s.get("CONTENT_ID") or h["content_id"]
    cat = s.get("CATEGORY", "")
    rows = []

    def add(k, v):
        if v not in ("", None):
            rows.append((k, str(v)))

    add("Title", s.get("TITLE"))
    add("Content ID", cid)
    add("Title ID", s.get("TITLE_ID"))
    if cat:
        add("Type", f"{CATEGORIES.get(cat, 'Unknown')} ({cat})")
    if cid:
        add("Region", f"{REGION_NAMES.get(cid[:2], 'Unknown')} ({cid[:2]})")
    add("Version", s.get("VERSION"))
    add("App version", s.get("APP_VER"))
    sv = s.get("SYSTEM_VER")
    if isinstance(sv, int):
        add("Min. system", sys_ver_str(sv))
    langs = sorted(int(k[6:]) for k, v in s.items()
                   if re.fullmatch(r"TITLE_\d\d", k) and v)
    if langs:
        names = ", ".join(SFO_LANGS[i] if i < len(SFO_LANGS) else f"#{i}" for i in langs)
        add("Languages", f"{len(langs)}: {names}")
    add("Built", fmt_built(h["built"]))
    add("Entries", f"{h['entry_count']} ({h['sc_entry_count']} sc)")
    add("Size", f"{fmt_size(info.file_size)}  ({info.file_size:,} bytes)")
    if h["package_size"] and h["package_size"] != info.file_size:
        add("Header size field", f"{h['package_size']:,} bytes")
    pti = s.get("PUBTOOLINFO", "")
    storage = next((t for t in STORAGE_TYPES if t in str(pti)), "")
    add("Storage type", storage)
    add("Pub tool info", pti)
    add("DRM type", f"0x{h['drm_type']:X}")
    add("Content type", f"0x{h['content_type']:X}")
    add("Content flags", f"0x{h['content_flags']:08X}")
    return rows


def pkg_summary_text(info: PkgInfo) -> str:
    rows = pkg_summary(info)
    w = max((len(k) for k, _ in rows), default=0)
    lines = [f"{k:<{w}}  {v}" for k, v in rows]
    for msg in info.warnings:
        lines.append(f"[Warn] {msg}")
    return "\n".join(lines)


class Processor:
    def __init__(self, settings: Settings, log, stop_event, ask_base=None, ask_choice=None):
        self.s = settings
        self.ask_choice = ask_choice or (lambda title, prompt, options: 0)
        self.log = log                  # log(text, tag="")
        self.stop = stop_event
        self.ask_base = ask_base or (lambda title: None)
        self.proc = None
        self.orbis = None
        self.sfo = None

    # ----- plumbing ------------------------------------------------------- #
    def check_tools(self):
        self.orbis = resolve_tool(self.s.orbis_dir, "orbis-pub-cmd")
        self.sfo = resolve_tool(self.s.sfo_dir, "sfo")
        if not self.orbis:
            raise ToolError("orbis-pub-cmd not found (set its folder or add it to PATH).")
        if not self.sfo:
            raise ToolError("sfo not found (set its folder or add it to PATH).")

    def kill(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def run(self, args, capture_only=False, quiet=False, hide_stderr=False):
        """Run a command, return (returncode, output)."""
        if self.stop.is_set():
            raise Cancelled()
        args = [str(a) for a in args]
        if not quiet:
            self.log("> " + subprocess.list2cmdline(args), "cmd")
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if hide_stderr else subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=clean_env(),
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        self.proc = p
        lines = []
        for line in p.stdout:
            line = line.rstrip("\r\n")
            lines.append(line)
            if not capture_only and line.strip():
                self.log("    " + line)
        p.wait()
        self.proc = None
        if self.stop.is_set():
            raise Cancelled()
        return p.returncode, "\n".join(lines)

    # ----- sfo ------------------------------------------------------------ #
    def sfo_query(self, key, path):
        rc, out = self.run([self.sfo, "-q", key, path], capture_only=True,
                           quiet=True, hide_stderr=True)
        out = out.strip()
        if rc != 0 or not out:
            return ""
        return out.splitlines()[-1].strip()

    def query_info(self, path, base=None):
        info = dict(base) if base else {k: "" for k in INFO_KEYS}
        for k in INFO_KEYS:
            v = self.sfo_query(k, path)
            if v or k not in info:
                info[k] = v
        return info

    def make_plan(self, info) -> Plan:
        s = self.s
        cid = info["CONTENT_ID"]
        c_region, c_number = cid[0:2], cid[2:6]
        c_title, c_inum, c_name = cid[7:16], cid[16:20], cid[20:]
        region = s.new_region or c_region
        number = s.new_region_id or c_number
        title = s.new_title_id or c_title
        name = s.new_content_name or c_name
        new_id = f"{region}{number}-{title}{c_inum}{name}"
        pkg_name = f"{new_id}-A{pad4(info['APP_VER'])}-V{pad4(info['VERSION'])}"
        pti = info["PUBTOOLINFO"]
        storage = next((t for t in STORAGE_TYPES if t in pti), "")
        return Plan(new_id, pkg_name, title, c_title, storage)

    def log_info(self, info, plan=None):
        self.log("==================== SFO Info ====================", "info")
        for k in INFO_KEYS:
            self.log(f"  {k}: {info[k]}")
        if plan:
            self.log(f"  StorageType: {plan.storage_type}")
            self.log(f"  New CONTENT_ID: {plan.new_content_id if hasattr(plan, 'new_content_id') else plan.content_new_id}", "ok")
            self.log(f"  New PKG name:   {plan.new_pkg_name}.pkg", "ok")

    # ----- inspect only --------------------------------------------------- #
    def inspect(self, pkgs):
        self.check_tools()
        for pkg in pkgs:
            self.log(f"\n### {pkg}", "info")
            info = self.query_info(pkg)
            if not info["CONTENT_ID"] or len(info["CONTENT_ID"]) < 20:
                self.log("[Error] Could not read a valid CONTENT_ID from this file.", "error")
                continue
            self.log_info(info, self.make_plan(info))

    # ----- full job ------------------------------------------------------- #
    def process_all(self, pkgs, progress=lambda i, n: None):
        self.check_tools()
        n = len(pkgs)
        for i, pkg in enumerate(pkgs, 1):
            progress(i - 1, n)
            self.log(f"\n[{i}/{n}] {pkg}", "info")
            try:
                self.process_one(Path(pkg))
            except Cancelled:
                self.log("[Warn] Cancelled by user.", "warn")
                return
            except Exception as e:  # keep batch going
                self.log(f"[Error] {e}", "error")
                self.log(traceback.format_exc(), "error")
        progress(n, n)

    def process_one(self, pkg: Path):
        s = self.s
        work = pkg.parent

        # --- query the pkg ------------------------------------------------
        info = self.query_info(pkg)
        if len(info["CONTENT_ID"]) < 20:
            self.log("[Error] Sfo invalid CONTENT_ID.", "error")
            return
        plan = self.make_plan(info)
        root = work / plan.new_pkg_name
        image0 = root / "Image0"
        sce = image0 / "sce_sys"
        gp4 = root / f"{plan.content_new_id}.gp4"

        # resume case: already-extracted param.sfo wins
        if (sce / "param.sfo").is_file():
            info = self.query_info(sce / "param.sfo", base=info)
            plan = self.make_plan(info)
            root = work / plan.new_pkg_name
            image0 = root / "Image0"
            sce = image0 / "sce_sys"
            gp4 = root / f"{plan.content_new_id}.gp4"

        self.log_info(info, plan)
        category = info["CATEGORY"]
        new_title = plan.new_title_id

        # --- extract ------------------------------------------------------
        do_extract = s.overwrite_unpacked or not root.exists()
        root.mkdir(parents=True, exist_ok=True)
        if do_extract:
            self.log("==================== Extract ====================", "info")
            self.log(f"[Info] extract pkg to {plan.new_pkg_name}", "info")
            self.extract(pkg, root)

        volume_type = "pkg_ps4_ac_data"
        if not image0.exists():
            volume_type = "pkg_ps4_ac_nodata"
        sce.mkdir(parents=True, exist_ok=True)

        # --- Sc0 -> Image0/sce_sys -----------------------------------------
        chunk_count = self.move_sc0(root, sce)

        self.build_package(work, root, image0, sce, gp4, plan, info, category,
                           volume_type, chunk_count)

    def build_package(self, work, root, image0, sce, gp4, plan, info, category,
                      volume_type, chunk_count, force_sfo=False, sfo_extra=None):
        """param.sfo edits -> gp4 -> img_create. Shared by process_one and merge."""
        s = self.s
        new_title = plan.new_title_id
        param = sce / "param.sfo"
        if not param.is_file():
            self.log("[Error] param.sfo missing after extraction.", "error")
            return
        sfo_extra = dict(sfo_extra or {})
        if s.new_app_ver:
            sfo_extra["APP_VER"] = s.new_app_ver
        if force_sfo or info["CONTENT_ID"] != plan.content_new_id or sfo_extra:
            self.log("==================== param.sfo ====================", "info")
            self.log("[Info] edit param.sfo", "info")
            cmd = [self.sfo, "-e", "CONTENT_ID", plan.content_new_id,
                   "-e", "TITLE_ID", new_title]
            for k, v in (sfo_extra or {}).items():
                cmd += ["-e", k, v]
            self.run(cmd + [param])

        pti = info["PUBTOOLINFO"]
        if "sdk_ver" in pti and s.new_sdk_ver:
            parts = []
            for part in pti.split(","):
                part = part.strip().strip('"')
                parts.append(f"sdk_ver={s.new_sdk_ver}" if "sdk_ver" in part else part)
            new_pti = ",".join(parts)
            if new_pti != pti:
                self.log("==================== PUBTOOLINFO ====================", "info")
                self.log(f"[Info] new PUBTOOLINFO: {new_pti}", "info")
                self.run([self.sfo, "-e", "PUBTOOLINFO", new_pti, param])

        # --- gp4 ----------------------------------------------------------
        if gp4.exists() and s.overwrite_gp4:
            gp4.unlink()
        if not gp4.exists():
            self.log("==================== gp4 create ====================", "info")
            self.create_gp4(gp4, plan, category, volume_type, info["TITLE"], chunk_count)
            if not gp4.exists():
                self.log("[Error] gp4 proj create failed...", "error")
                return
            self.log(f"  volumeType: {volume_type}")
            self.log("[Info] Generating gp4 file now...", "info")
            self.add_files(image0, root, gp4, plan)

            if not (sce / "icon0.png").exists() and s.icon0_path and Path(s.icon0_path).is_file():
                self.run([self.orbis, "gp4_file_add", "--force", s.icon0_path,
                          "sce_sys/icon0.png", gp4])

        # --- build pkg ----------------------------------------------------
        if s.pkg_create:
            self.log("==================== PKG Create ====================", "info")
            self.log(f"[Info] Creating {plan.new_pkg_name}.pkg...", "info")
            out_dir = work / s.gen_dir_name
            out_dir.mkdir(parents=True, exist_ok=True)
            out_pkg = out_dir / f"{plan.new_pkg_name}.pkg"
            cmd = [self.orbis, "img_create", "--no_progress_bar"]
            if not s.pkg_digest:
                cmd.append("--skip_digest")
            cmd += [gp4, out_pkg]
            self.run(cmd)
            if out_pkg.is_file():
                self.log(f"[OK] Created {out_pkg}", "ok")
            else:
                self.log("[Error] PKG was not created - see output above.", "error")

        if s.cleanup:
            self.log("==================== Temp files removed ====================", "info")
            shutil.rmtree(root, ignore_errors=True)

    def move_sc0(self, root: Path, sce: Path) -> int:
        """Move root/Sc0 into Image0/sce_sys. Returns the playgo chunk count."""
        self.log("==================== Sc0 ====================", "info")
        self.log("[Info] move Sc0 to Image0/sce_sys/", "info")
        chunk_count = 0
        scenario_count = 0
        sc0 = root / "Sc0"
        if sc0.is_dir():
            for dirpath, dirnames, filenames in os.walk(sc0):
                rel = Path(dirpath).relative_to(sc0)
                for d in dirnames:
                    (sce / rel / d).mkdir(parents=True, exist_ok=True)
                for fn in filenames:
                    low = fn.lower()
                    src = Path(dirpath) / fn
                    if low == "playgo-manifest.xml":
                        try:
                            text = src.read_text(encoding="utf-8", errors="replace")
                            m = re.search(r"<chunk_info\b[^>]*>", text)
                            if m:
                                c = re.search(r'chunk_count="(\d+)"', m.group(0))
                                sc = re.search(r'scenario_count="(\d+)"', m.group(0))
                                chunk_count = int(c.group(1)) if c else 0
                                scenario_count = int(sc.group(1)) if sc else 0
                                self.log(f"[Info] chunkCount:{chunk_count}, scenarioCount:{scenario_count}", "info")
                        except OSError as e:
                            self.log(f"[Warn] could not read {fn}: {e}", "warn")
                    elif low in SC0_SKIP:
                        continue
                    else:
                        dst = sce / rel / fn
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        if dst.exists():
                            dst.unlink()
                        shutil.move(str(src), str(dst))
        return chunk_count

    # ----- merge base + update -------------------------------------------- #
    def merge(self, base_pkg: Path, upd_pkg: Path):
        """Overlay an update/patch PKG onto its base game and repackage the result
        as a single full-game ('gd') PKG carrying the update's version numbers."""
        self.check_tools()
        s = self.s
        work = base_pkg.parent

        binfo = self.query_info(base_pkg)
        uinfo = self.query_info(upd_pkg)
        if len(binfo["CONTENT_ID"]) < 20:
            self.log("[Error] Could not read a valid CONTENT_ID from the base PKG.", "error")
            return
        if len(uinfo["CONTENT_ID"]) < 20:
            self.log("[Error] Could not read a valid CONTENT_ID from the update PKG.", "error")
            return
        self.log("==================== Base PKG ====================", "info")
        self.log_info(binfo)
        self.log("==================== Update PKG ====================", "info")
        self.log_info(uinfo)

        # --- which TitleID? -------------------------------------------------
        # Box filled in -> use it. Box empty and base/update differ -> ask.
        b_tid, u_tid = binfo["TITLE_ID"], uinfo["TITLE_ID"]
        if not s.new_title_id and b_tid and u_tid and b_tid != u_tid:
            idx = self.ask_choice(
                "Merge: choose TitleID",
                "The base and the update have different TitleIDs.\n"
                "Which one should the merged PKG use?",
                [f"Base game:  {b_tid}   ({binfo['CONTENT_ID']})",
                 f"Update:  {u_tid}   ({uinfo['CONTENT_ID']})"])
            if idx is None:
                self.log("[Info] Merge cancelled (no TitleID chosen).", "info")
                return
            s = self.s = replace(s, new_title_id=(b_tid, u_tid)[idx])
            self.log(f"[Info] using TitleID {s.new_title_id} "
                     f"({'base' if idx == 0 else 'update'})", "ok")
        target_title = s.new_title_id or b_tid
        if target_title and u_tid and target_title != u_tid:
            self.log(f"[Warn] merged PKG will be {target_title} but the update files are "
                     f"for {u_tid}; the game may not accept them.", "warn")

        if binfo["CATEGORY"] != "gd":
            self.log(f"[Warn] Base PKG category is '{binfo['CATEGORY']}', expected 'gd' (game).", "warn")
        if uinfo["CATEGORY"] != "gp":
            self.log(f"[Warn] Update PKG category is '{uinfo['CATEGORY']}', expected 'gp' (patch).", "warn")

        # merged identity: base content id + the update's version numbers
        info = dict(binfo)
        info["CATEGORY"] = "gd"
        use_base_ver = False
        b_ver = f"APP_VER {binfo['APP_VER']} / VERSION {binfo['VERSION']}"
        u_ver = f"APP_VER {uinfo['APP_VER']} / VERSION {uinfo['VERSION']}"
        if b_ver != u_ver and uinfo["VERSION"]:
            idx = self.ask_choice(
                "Merge: choose version",
                "The base and the update have different versions.\n"
                "Which version should the merged PKG carry?",
                [f"Update (recommended):  {u_ver}", f"Base game:  {b_ver}"])
            if idx is None:
                self.log("[Info] Merge cancelled (no version chosen).", "info")
                return
            use_base_ver = (idx == 1)
        if not use_base_ver:
            for k in ("APP_VER", "VERSION"):
                if uinfo[k]:
                    info[k] = uinfo[k]
        self.log(f"[Info] merged version: {'base' if use_base_ver else 'update'} "
                 f"(APP_VER {info['APP_VER']}, VERSION {info['VERSION']})", "info")
        plan = self.make_plan(info)
        root = work / f"{plan.new_pkg_name}_merge"
        upd_root = work / f"{plan.new_pkg_name}_merge_update"
        image0 = root / "Image0"
        sce = image0 / "sce_sys"
        gp4 = root / f"{plan.content_new_id}.gp4"
        self.log(f"[Info] merged PKG will be {plan.new_pkg_name}.pkg", "ok")
        self.log(f"[Info] needs free disk for both extractions plus the new PKG.", "info")

        # --- extract base, then update -------------------------------------
        for label, pkg, dest in (("base", base_pkg, root), ("update", upd_pkg, upd_root)):
            if s.overwrite_unpacked or not dest.exists():
                self.log(f"==================== Extract {label} ====================", "info")
                dest.mkdir(parents=True, exist_ok=True)
                self.extract(pkg, dest)
            else:
                self.log(f"[Info] reusing already-extracted {label}: {dest.name}", "info")

        sce.mkdir(parents=True, exist_ok=True)
        chunk_count = self.move_sc0(root, sce)

        upd_image0 = upd_root / "Image0"
        upd_sce = upd_image0 / "sce_sys"
        upd_sce.mkdir(parents=True, exist_ok=True)
        upd_chunks = self.move_sc0(upd_root, upd_sce)
        chunk_count = upd_chunks or chunk_count

        # --- overlay the update's files onto the base -----------------------
        self.log("==================== Merge ====================", "info")
        if not any(upd_image0.rglob("*")):
            self.log("[Error] The update PKG extracted no files, nothing to merge.", "error")
            return
        n = 0
        for src in upd_image0.rglob("*"):
            if self.stop.is_set():
                raise Cancelled()
            if not src.is_file():
                continue
            dst = image0 / src.relative_to(upd_image0)
            dst.parent.mkdir(parents=True, exist_ok=True)
            replaced = dst.exists()
            shutil.copy2(src, dst)
            n += 1
            self.log(f"    {'replace' if replaced else 'add    '}: {dst.relative_to(image0).as_posix()}")
        self.log(f"[Info] merged {n} file(s) from the update into the base.", "ok")
        if s.cleanup:
            shutil.rmtree(upd_root, ignore_errors=True)

        # --- param.sfo comes from the update; make it a full-game sfo -------
        extra = {"CATEGORY": "gd"}
        if use_base_ver:
            # update's param.sfo was overlaid onto the base: restore base versions
            extra["APP_VER"] = s.new_app_ver or binfo["APP_VER"] or "01.00"
            if binfo["VERSION"]:
                extra["VERSION"] = binfo["VERSION"]
        else:
            # orbis-pub-cmd rejects the patch's APP_VER in a full-game (gd) param.sfo;
            # use 01.00 unless overridden
            extra["APP_VER"] = s.new_app_ver or "01.00"
        self.build_package(work, root, image0, sce, gp4, plan, info, "gd",
                           "pkg_ps4_app", chunk_count,
                           force_sfo=True, sfo_extra=extra)

    # ----- pieces --------------------------------------------------------- #
    def extract(self, pkg: Path, root: Path):
        s = self.s
        pc = s.passcode
        if not s.show_status:
            self.run([self.orbis, "img_extract", "--passcode", pc, pkg, root])
            return
        rc, out = self.run([self.orbis, "img_file_list", "--passcode", pc,
                            "--oformat", "long", pkg], capture_only=True, quiet=True,
                           hide_stderr=True)
        count = 0
        for line in out.splitlines():
            if self.stop.is_set():
                raise Cancelled()
            tok = line.split()
            if len(tok) < 3:
                continue
            pkg_name = tok[2]
            file_ = tok[4] if len(tok) >= 5 else ""
            if file_:
                count += 1
                self.log(f"    {count:05d}: {file_}")
                target = root.joinpath(*rel_parts(file_))
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _, res = self.run([self.orbis, "img_extract", "--passcode", pc,
                                       f"{pkg}:{file_}", target],
                                      capture_only=True, quiet=True)
                    if "error" in res.lower():
                        self.log(res, "error")
            else:
                parts = rel_parts(pkg_name)
                d = root.joinpath(*parts)
                if not d.exists():
                    d.mkdir(parents=True, exist_ok=True)
                    if len(parts) > 2:
                        self.log(f"[DIR] extract: {pkg_name}")
                        _, res = self.run([self.orbis, "img_extract", "--passcode", pc,
                                           f"{pkg}:{pkg_name}", d],
                                          capture_only=True, quiet=True)
                        if "error" in res.lower():
                            self.log(res, "error")

    def create_gp4(self, gp4, plan, category, volume_type, title, chunk_count):
        pc = self.s.passcode
        base = [self.orbis, "gp4_proj_create", "--volume_type", None,
                "--content_id", plan.content_new_id,
                "--passcode", pc, "--entitlement_key", pc]
        storage = ["--storage_type", plan.storage_type] if plan.storage_type else []

        def build(vt, extra):
            cmd = list(base)
            cmd[cmd.index(None)] = vt
            return cmd + extra + [gp4]

        if category == "gp":
            volume_type = "pkg_ps4_patch"
            self.log(f"Patch detected - the original {plan.new_title_id}-app PKG is required.", "warn")
            app = self.ask_base(title or plan.new_title_id)
            if not app or not Path(app).exists():
                raise RuntimeError("No base game PKG selected for the patch - skipped.")
            self.run(build(volume_type, storage + ["--app_path", app]))
            self.add_chunks(gp4, chunk_count)
        elif category == "gd":
            volume_type = "pkg_ps4_app"
            self.run(build(volume_type, storage))
            self.add_chunks(gp4, chunk_count)
        else:
            self.run(build(volume_type, []))

    def add_chunks(self, gp4, chunk_count):
        if chunk_count > 1:
            for c in range(1, chunk_count):
                self.run([self.orbis, "gp4_chunk_add", "--id", c,
                          "--label", f"remaining_data{c}", gp4])

    def add_files(self, image0: Path, root: Path, gp4: Path, plan: Plan):
        comp = "enable" if self.s.pfs_compression else "disable"
        files = sorted(p for p in image0.rglob("*") if p.is_file())
        for f in files:
            rel = f.relative_to(image0).as_posix()      # sce_sys/param.sfo
            relw = rel.replace("/", "\\").lower()        # sce_sys\param.sfo
            ext = f.suffix.lower()
            fix_title = plan.old_title_id != plan.new_title_id

            if relw == "sce_sys\\nptitle.dat":
                if fix_title:
                    self.fix_title_id(f, root, plan)
                self.run([self.orbis, "gp4_file_add", "--force", "--pfs_compression",
                          comp, f, rel, gp4])
            elif relw in ("sce_sys\\playgo-chunk.dat", "sce_sys\\app\\playgo-chunk.dat"):
                if fix_title:
                    self.fix_title_id(f, root, plan)
            elif ("sce_sys\\about" not in relw
                  and "sce_sys\\playgo-" not in relw
                  and relw not in ("sce_discmap.plt", "sce_discmap_patch.plt",
                                   "sce_sys\\pubtoolinfo.dat")
                  and ext != ".dds"):
                self.run([self.orbis, "gp4_file_add", "--force", "--pfs_compression",
                          comp, f, rel, gp4], quiet=False)

    def fix_title_id(self, f: Path, root: Path, plan: Plan):
        old, new = plan.old_title_id.encode(), plan.new_title_id.encode()
        data = f.read_bytes()
        if old in data:
            self.log(f"[Warn] Fix the TitleID [{plan.old_title_id} to {plan.new_title_id}] of {f.name}", "warn")
            bak_dir = root / "_titleid_backup"
            bak_dir.mkdir(exist_ok=True)
            shutil.copy2(f, bak_dir / f.name)
            f.write_bytes(data.replace(old, new))


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #
class DetailsWindow:
    """PKG details window: cover + Info / Entries / param.sfo / Header tabs."""

    def __init__(self, app, info: PkgInfo):
        pal = app.pal
        self.info = info
        self.app = app
        top = tk.Toplevel(app.root)
        self.top = top
        top.title(f"PKG details - {Path(info.path).name}")
        top.geometry("960x620")
        top.minsize(720, 440)
        top.configure(background=pal["bg"])

        outer = ttk.Frame(top, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # left: cover + headline
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="n", padx=(0, 10))
        self.cover = None
        if info.icon:
            try:
                img = tk.PhotoImage(data=base64.b64encode(info.icon).decode("ascii"))
                if img.width() > 300:
                    img = img.subsample(max(1, img.width() // 256))
                self.cover = img
                ttk.Label(left, image=img).pack()
            except tk.TclError:
                self.cover = None
        if self.cover is None:
            ttk.Label(left, text="(no cover)").pack(pady=40)
        s = info.sfo
        cid = s.get("CONTENT_ID") or info.header["content_id"]
        ttk.Label(left, text=s.get("TITLE", Path(info.path).name), wraplength=260,
                  font=("", 11, "bold")).pack(pady=(8, 2))
        cat = s.get("CATEGORY", "")
        badges = [REGION_NAMES.get(cid[:2], "") if cid else "",
                  CATEGORIES.get(cat, ""), fmt_size(info.file_size)]
        ttk.Label(left, text="  ·  ".join(b for b in badges if b),
                  wraplength=260).pack()
        ttk.Button(left, text="Copy info", command=self.copy).pack(pady=10)

        # right: tabs
        nb = ttk.Notebook(outer)
        nb.grid(row=0, column=1, sticky="nsew")

        text = pkg_summary_text(info) or "(no information could be read)"
        nb.add(self._text(nb, text, pal), text="Info")
        nb.add(self._entries(nb), text=f"Entries ({len(info.entries)})")
        nb.add(self._sfo(nb), text="param.sfo")
        nb.add(self._text(nb, hexdump(info.header_bytes), pal), text="Header")

    def _text(self, parent, content, pal):
        box = ScrolledText(parent, wrap="none", relief="flat", borderwidth=0,
                           font=(MONO_FONT, 9),
                           background=pal["field"], foreground=pal["fg"],
                           insertbackground=pal["fg"], selectbackground=pal["sel"],
                           selectforeground=pal["sel_fg"])
        box.insert("1.0", content)
        box.configure(state="disabled")
        return box

    def _tree(self, parent, cols, widths):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=cols[1:], selectmode="extended")
        tree.heading("#0", text=cols[0])
        tree.column("#0", width=widths[0])
        for c, w in zip(cols[1:], widths[1:]):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        return frame, tree

    def _entries(self, parent):
        frame, tree = self._tree(parent, ("Name", "ID", "Size", "Offset", "Encrypted"),
                                 (300, 70, 110, 110, 80))
        for e in self.info.entries:
            tree.insert("", "end", text=e.name,
                        values=(f"0x{e.id:04X}", f"{e.size:,}", f"0x{e.offset:X}",
                                "yes" if e.encrypted else ""))
        return frame

    def _sfo(self, parent):
        frame, tree = self._tree(parent, ("Key", "Value"), (220, 600))
        for k, v in self.info.sfo.items():
            tree.insert("", "end", text=k,
                        values=(f"0x{v:08X}" if isinstance(v, int) else v,))
        return frame

    def copy(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(pkg_summary_text(self.info))


class App:
    REGIONS = ["", "JP", "EP", "UP", "HP", "KP"]

    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(1120, sw - 40)}x{min(780, sh - 100)}")
        root.minsize(min(940, sw - 40), min(620, sh - 100))

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.processor = None
        self.file_set = {}   # iid -> Path
        self.pal = LIGHT
        self.style = ttk.Style()
        self.default_theme = self.style.theme_use()

        st = self.load_settings()
        self.v = {}
        for f in fields(Settings):
            if f.name == "files":
                continue
            val = getattr(st, f.name)
            self.v[f.name] = tk.BooleanVar(value=val) if isinstance(val, bool) \
                else tk.StringVar(value=val)

        seed_tools()
        self.build_ui()
        for p in st.files:
            self.add_path(Path(p))
        self.update_tool_status()
        self.root.after(60, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- settings ------------------------------------------------------- #
    def load_settings(self) -> Settings:
        st = Settings()
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for f in fields(Settings):
                if f.name in data:
                    setattr(st, f.name, data[f.name])
        except (OSError, ValueError):
            pass
        return st

    def collect(self) -> Settings:
        st = Settings()
        for name, var in self.v.items():
            val = var.get()
            if isinstance(val, str):
                val = val.strip()
            setattr(st, name, val)
        st.new_region = st.new_region.upper()
        st.files = [str(p) for p in self.file_set.values()]
        return st

    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(asdict(self.collect()), indent=2),
                                     encoding="utf-8")
        except OSError:
            pass

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "A job is still running. Stop it and quit?"):
                return
            self.stop_job()
        self.save_settings()
        self.root.destroy()

    # ----- UI construction ------------------------------------------------ #
    def build_ui(self):
        self.all_combos = []
        self.id_combos = []          # (combobox, "rid" | "tid") refreshed from the loaded PKGs
        self._cid_cache = {}
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        # ----- sidebar ---------------------------------------------------- #
        side = ttk.Frame(shell, style="Side.TFrame", width=190)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="PKG Region\nChanger", style="SideTitle.TLabel",
                  justify="left").pack(anchor="w", padx=16, pady=(18, 16))
        self.nav = {}
        for name in ("Convert", "Merge", "Tools", "Settings"):
            b = ttk.Button(side, text=name, style="Nav.TButton",
                           command=lambda n=name: self.show_page(n))
            b.pack(fill="x", padx=8, pady=1)
            self.nav[name] = b
        self.tool_label = ttk.Label(side, text="", style="Side.TLabel", wraplength=170,
                                    justify="left")
        self.tool_label.pack(side="bottom", anchor="w", padx=14, pady=14)

        # ----- content ---------------------------------------------------- #
        content = ttk.Frame(shell)
        content.pack(side="left", fill="both", expand=True)
        self.page_title = ttk.Label(content, text="", style="Title.TLabel")
        self.page_title.pack(side="top", anchor="w", padx=20, pady=(14, 6))

        # log (bottom), then the action bar above it, then the pages fill the rest
        self.log_frame = ttk.LabelFrame(content, text="Log", padding=4)
        self.log_frame.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        ab = ttk.Frame(content)
        ab.pack(side="bottom", fill="x", padx=16, pady=8)
        ab.columnconfigure(3, weight=1)
        self.btn_start = ttk.Button(ab, text="\u25b6  Start", style="Pri.TButton",
                                    command=self.start_job, width=12)
        self.btn_inspect = ttk.Button(ab, text="Inspect", command=self.inspect_job, width=10)
        self.btn_stop = ttk.Button(ab, text="\u25a0  Stop", command=self.stop_job, width=10,
                                   state="disabled")
        self.btn_start.grid(row=0, column=0, padx=(0, 4))
        self.btn_inspect.grid(row=0, column=1, padx=4)
        self.btn_stop.grid(row=0, column=2, padx=4)
        self.progress = ttk.Progressbar(ab, mode="determinate")
        self.progress.grid(row=0, column=3, sticky="ew", padx=8)
        self.status = ttk.Label(ab, text="Idle", width=18)
        self.status.grid(row=0, column=4)

        host = ttk.Frame(content)
        host.pack(side="top", fill="both", expand=True, padx=16)
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.pages = {}
        for name in self.nav:
            pg = ttk.Frame(host)
            pg.grid(row=0, column=0, sticky="nsew")
            pg.columnconfigure(0, weight=1)
            self.pages[name] = pg

        # ----- Convert page ------------------------------------------------ #
        pg = self.pages["Convert"]
        pg.rowconfigure(0, weight=1)
        f = ttk.LabelFrame(pg, text="PKG queue  (select rows to process only those; "
                                    "none selected = all)", padding=6)
        f.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(f, columns=("folder", "size"), height=5,
                                 selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("folder", text="Folder")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=330)
        self.tree.column("folder", width=300)
        self.tree.column("size", width=90, anchor="e")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        bf = ttk.Frame(f)
        bf.grid(row=0, column=2, sticky="n", padx=(6, 0))
        for text, cmd in (("Add folder...", self.add_folder), ("Add files...", self.add_files),
                          ("Remove", self.remove_selected), ("Clear", self.clear_files),
                          ("Details...", self.show_details)):
            ttk.Button(bf, text=text, command=cmd, width=14).pack(pady=1)
        self.tree.bind("<Double-1>", self.show_details)
        f = ttk.LabelFrame(pg, text="Changes  (empty = keep original)", padding=8)
        f.grid(row=1, column=0, sticky="ew")
        self.build_changes(f, hint=True)

        # ----- Merge page -------------------------------------------------- #
        pg = self.pages["Merge"]
        f = ttk.LabelFrame(pg, text="Merge base + update  (combine a base game PKG and "
                                    "its update PKG into one full-game PKG)", padding=8)
        f.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        f.columnconfigure(1, weight=1)
        self.entry_row(f, 0, "Base game PKG:", "merge_base", browse="pkg")
        self.entry_row(f, 1, "Update PKG:", "merge_update", browse="pkg")
        mf = ttk.Frame(f)
        mf.grid(row=2, column=1, sticky="w", pady=(6, 0))
        self.btn_merge = ttk.Button(mf, text="Merge PKGs", style="Pri.TButton",
                                    command=self.merge_job, width=14)
        self.btn_merge.pack(side="left")
        ttk.Label(mf, text="  If TitleID is empty and the PKGs differ, you will be asked "
                           "which TitleID and version to use.").pack(side="left")
        f = ttk.LabelFrame(pg, text="Changes for the merged PKG  (pick from the loaded PKGs "
                                    "or type your own)", padding=8)
        f.grid(row=1, column=0, sticky="ew")
        self.build_changes(f, hint=False)

        # ----- Tools page -------------------------------------------------- #
        pg = self.pages["Tools"]
        f = ttk.LabelFrame(pg, text="Tools (leave empty to use the ./tools folder or PATH)",
                           padding=8)
        f.grid(row=0, column=0, sticky="ew")
        f.columnconfigure(1, weight=1)
        self.entry_row(f, 0, "orbis-pub folder:", "orbis_dir", browse="dir")
        self.entry_row(f, 1, "sfo folder:", "sfo_dir", browse="dir")
        bf = ttk.Frame(f)
        bf.grid(row=2, column=1, sticky="w", padx=4, pady=(8, 0))
        ttk.Button(bf, text="Install tools...", command=self.install_tools,
                   width=16).pack(side="left")
        ttk.Button(bf, text="Get sfo...", command=self.get_sfo,
                   width=12).pack(side="left", padx=(8, 0))
        if IS_WIN:
            hint = ("orbis-pub-cmd.exe runs natively on Windows: use Install tools... and "
                    "pick your PS4 Fake PKG Tools folder. Get sfo... downloads Hippie68's sfo.")
        else:
            hint = ("orbis-pub-cmd is a Windows program, so it runs through wine here "
                    "(install wine first). Use Install tools... to pick your PS4 Fake PKG "
                    "Tools folder; Get sfo... downloads sfo if none is bundled for your system.")
        ttk.Label(f, text=hint, justify="left", wraplength=640).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for k in ("orbis_dir", "sfo_dir"):
            self.v[k].trace_add("write", lambda *_: self.update_tool_status())

        # ----- Settings page ------------------------------------------------ #
        pg = self.pages["Settings"]
        f = ttk.LabelFrame(pg, text="Options", padding=8)
        f.grid(row=0, column=0, sticky="ew")
        f.columnconfigure(1, weight=1)
        self.entry_row(f, 0, "Output folder name:", "gen_dir_name")
        self.entry_row(f, 1, "icon0.png fallback:", "icon0_path", browse="file")
        self.entry_row(f, 2, "Passcode (32):", "passcode")
        cf = ttk.Frame(f)
        cf.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        checks = [
            ("pkg_create", "Create new PKG after extract"),
            ("pfs_compression", "PFS compression"),
            ("pkg_digest", "Calculate digest"),
            ("show_status", "Show extract status (slower on big PKGs)"),
            ("overwrite_unpacked", "Re-extract if unpacked folder exists"),
            ("overwrite_gp4", "Regenerate existing .gp4"),
            ("cleanup", "Delete temp files when done"),
            ("dark_mode", "Dark mode"),
            ("auto_update_check", "Check for updates on startup"),
        ]
        for i, (k, txt) in enumerate(checks):
            cmd = self.apply_theme if k == "dark_mode" else None
            ttk.Checkbutton(cf, text=txt, variable=self.v[k], command=cmd).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 24), pady=1)

        af = ttk.LabelFrame(pg, text="About", padding=8)
        af.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(af, text=f"{APP_TITLE}  v{__version__}").grid(row=0, column=0, sticky="w")
        ttk.Button(af, text="Check for updates", command=lambda: self.start_update_check(True),
                   width=18).grid(row=0, column=1, sticky="e", padx=(16, 0))
        af.columnconfigure(0, weight=1)

        # ----- log ---------------------------------------------------------- #
        f = self.log_frame
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        self.log_box = ScrolledText(f, height=9, state="disabled", wrap="none",
                                    font=(MONO_FONT, 9))
        self.log_box.grid(row=0, column=0, sticky="nsew")
        for tag, col in (("info", "#0b5cad"), ("warn", "#b36b00"), ("error", "#c00000"),
                         ("ok", "#118a11"), ("cmd", "#808080")):
            self.log_box.tag_config(tag, foreground=col)

        for k in ("merge_base", "merge_update"):
            self.v[k].trace_add("write", lambda *_: self.root.after_idle(self.refresh_ids))
        self.apply_theme()
        self.show_page("Convert")

    def show_page(self, name):
        self.pages[name].tkraise()
        self.page_title.configure(text=name)
        for n, b in self.nav.items():
            b.configure(style="NavOn.TButton" if n == name else "Nav.TButton")

    def build_changes(self, parent, hint=True):
        """Region / RegionID / TitleID / ContentName / SDK / App ver editor (shared vars)."""
        for c in (1, 3, 5):
            parent.columnconfigure(c, weight=1)
        ttk.Label(parent, text="Region (2):").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        cb = ttk.Combobox(parent, textvariable=self.v["new_region"], values=self.REGIONS,
                          width=10)
        cb.grid(row=0, column=1, sticky="w", pady=3)
        self.all_combos.append(cb)
        if hint:
            self.cb_region = cb
            self.hint_label = ttk.Label(
                parent, text="JP Japan \u00b7 EP Europe \u00b7 UP America \u00b7 "
                             "HP Hong Kong \u00b7 KP Korea")
            self.hint_label.grid(row=0, column=2, columnspan=4, sticky="w", padx=8)
        ttk.Label(parent, text="RegionID (4):").grid(row=1, column=0, sticky="e", padx=4, pady=3)
        rid = ttk.Combobox(parent, textvariable=self.v["new_region_id"], width=10)
        rid.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(parent, text="TitleID (9):").grid(row=1, column=2, sticky="e", padx=4, pady=3)
        tid = ttk.Combobox(parent, textvariable=self.v["new_title_id"], width=14)
        tid.grid(row=1, column=3, sticky="w", pady=3)
        for combo, kind in ((rid, "rid"), (tid, "tid")):
            self.all_combos.append(combo)
            self.id_combos.append((combo, kind))
        self.small_entry(parent, 1, 4, "App ver (01.00):", "new_app_ver", width=8)
        self.small_entry(parent, 2, 0, "ContentName (16):", "new_content_name", width=22)
        self.small_entry(parent, 2, 2, "SDK ver (8):", "new_sdk_ver", width=14)
        self.refresh_ids()

    def refresh_ids(self):
        """Fill the RegionID / TitleID drop-downs with the IDs found in the loaded PKGs."""
        paths = list(self.file_set.values()) if hasattr(self, "file_set") else []
        for k in ("merge_base", "merge_update"):
            val = self.v[k].get().strip()
            if val:
                paths.append(Path(val))
        rids, tids = [], []
        for p in paths:
            key = str(p)
            if key not in self._cid_cache:
                cid = ""
                try:
                    if p.is_file():
                        cid = str(parse_pkg(p).sfo.get("CONTENT_ID", ""))
                except Exception:
                    cid = ""
                self._cid_cache[key] = cid
            cid = self._cid_cache[key]
            if len(cid) >= 16:
                if cid[2:6] not in rids:
                    rids.append(cid[2:6])
                if cid[7:16] not in tids:
                    tids.append(cid[7:16])
        for combo, kind in self.id_combos:
            combo.configure(values=[""] + (rids if kind == "rid" else tids))

    def apply_theme(self):
        """Switch between the light and dark palettes (saved in settings)."""
        dark = bool(self.v["dark_mode"].get())
        st = self.style
        use_native = False
        if use_native:
            st.theme_use("vista")
            pal = LIGHT
        else:
            st.theme_use("clam" if "clam" in st.theme_names() else self.default_theme)
            pal = DARK if dark else LIGHT
        self.pal = pal

        if not use_native:
            st.configure(".", background=pal["bg"], foreground=pal["fg"],
                         fieldbackground=pal["field"], bordercolor=pal["border"],
                         lightcolor=pal["light"], darkcolor=pal["dark"],
                         troughcolor=pal["bg"], focuscolor=pal["sel"],
                         insertcolor=pal["fg"], selectbackground=pal["sel"],
                         selectforeground=pal["sel_fg"])
            st.configure("TFrame", background=pal["bg"])
            st.configure("TLabel", background=pal["bg"], foreground=pal["fg"])
            st.configure("TLabelframe", background=pal["bg"], bordercolor=pal["border"])
            st.configure("TLabelframe.Label", background=pal["bg"], foreground=pal["fg"])
            st.configure("TButton", background=pal["btn"], foreground=pal["fg"])
            st.map("TButton",
                   background=[("disabled", pal["bg"]), ("active", pal["btn_active"])],
                   foreground=[("disabled", pal["muted"])])
            st.configure("TCheckbutton", background=pal["bg"], foreground=pal["fg"],
                         indicatorbackground=pal["field"], indicatorforeground=pal["fg"])
            st.map("TCheckbutton",
                   background=[("active", pal["bg"])],
                   foreground=[("disabled", pal["muted"])],
                   indicatorbackground=[("pressed", pal["btn_active"]),
                                        ("disabled", pal["bg"])])
            st.configure("TEntry", fieldbackground=pal["field"], foreground=pal["fg"],
                         insertcolor=pal["fg"])
            st.configure("TCombobox", fieldbackground=pal["field"], background=pal["btn"],
                         foreground=pal["fg"], arrowcolor=pal["fg"],
                         selectbackground=pal["sel"], selectforeground=pal["sel_fg"])
            st.map("TCombobox",
                   fieldbackground=[("readonly", pal["field"]), ("disabled", pal["bg"])],
                   foreground=[("readonly", pal["fg"])],
                   background=[("active", pal["btn_active"])])
            st.configure("Treeview", background=pal["field"], fieldbackground=pal["field"],
                         foreground=pal["fg"])
            st.map("Treeview", background=[("selected", pal["sel"])],
                   foreground=[("selected", pal["sel_fg"])])
            st.configure("Treeview.Heading", background=pal["btn"], foreground=pal["fg"],
                         bordercolor=pal["border"])
            st.map("Treeview.Heading", background=[("active", pal["btn_active"])])
            st.configure("TScrollbar", background=pal["btn"], troughcolor=pal["bg"],
                         arrowcolor=pal["fg"], bordercolor=pal["border"])
            st.map("TScrollbar", background=[("active", pal["btn_active"])])
            st.configure("TNotebook", background=pal["bg"], bordercolor=pal["border"])
            st.configure("TNotebook.Tab", background=pal["btn"], foreground=pal["fg"],
                         padding=(10, 4))
            st.map("TNotebook.Tab",
                   background=[("selected", pal["sel"]), ("active", pal["btn_active"])],
                   foreground=[("selected", pal["sel_fg"])])
            st.configure("Horizontal.TProgressbar", background=pal["accent"],
                         troughcolor=pal["field"], bordercolor=pal["border"])
            # sidebar + accents
            st.configure("Side.TFrame", background=pal["side"])
            st.configure("Side.TLabel", background=pal["side"], foreground=pal["side_fg"])
            st.configure("SideTitle.TLabel", background=pal["side"], foreground=pal["fg"],
                         font=("TkDefaultFont", 13, "bold"))
            st.configure("Title.TLabel", font=("TkDefaultFont", 14, "bold"))
            for sname, bg, fg in (("Nav.TButton", pal["side"], pal["side_fg"]),
                                  ("NavOn.TButton", pal["sel"], "#ffffff")):
                st.configure(sname, background=bg, foreground=fg, anchor="w",
                             padding=(12, 8), relief="flat", borderwidth=0)
                st.map(sname, background=[("active", pal["btn_active"] if sname == "Nav.TButton"
                                           else pal["sel"])])
            st.configure("Pri.TButton", background=pal["accent"], foreground=pal["accent_fg"],
                         font=("TkDefaultFont", 10, "bold"))
            st.map("Pri.TButton", background=[("disabled", pal["btn"]),
                                              ("active", pal["accent"])],
                   foreground=[("disabled", pal["muted"])])

        # plain tk widgets
        self.root.configure(background=pal["bg"])
        self.log_box.configure(background=pal["field"], foreground=pal["fg"],
                               insertbackground=pal["fg"], selectbackground=pal["sel"],
                               selectforeground=pal["sel_fg"])
        try:
            self.log_box.vbar.configure(background=pal["btn"], troughcolor=pal["bg"],
                                        activebackground=pal["btn_active"],
                                        highlightbackground=pal["bg"])
        except (tk.TclError, AttributeError):
            pass
        for tag in ("info", "warn", "ok", "cmd"):
            self.log_box.tag_config(tag, foreground=pal[tag])
        self.log_box.tag_config("error", foreground=pal["bad"])
        self.hint_label.configure(foreground=pal["muted"])

        # combobox drop-down list
        for opt, val in (("background", pal["field"]), ("foreground", pal["fg"]),
                         ("selectBackground", pal["sel"]),
                         ("selectForeground", pal["sel_fg"])):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)
        for cbx in self.all_combos:
            try:
                popdown = self.root.tk.call("ttk::combobox::PopdownWindow", str(cbx))
                self.root.tk.call(f"{popdown}.f.l", "configure",
                                  "-background", pal["field"], "-foreground", pal["fg"],
                                  "-selectbackground", pal["sel"],
                                  "-selectforeground", pal["sel_fg"])
            except tk.TclError:
                pass

        self.update_tool_status()

    def entry_row(self, parent, row, label, key, browse=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(parent, textvariable=self.v[key]).grid(row=row, column=1, sticky="ew", pady=2)
        if browse:
            ttk.Button(parent, text="Browse...", width=10,
                       command=lambda: self.browse(key, browse)).grid(row=row, column=2, padx=4)

    def small_entry(self, parent, row, col, label, key, width=24):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e", padx=4, pady=2)
        ttk.Entry(parent, textvariable=self.v[key], width=width).grid(
            row=row, column=col + 1, sticky="w", pady=2)

    def browse(self, key, kind):
        if kind == "dir":
            p = filedialog.askdirectory(title="Select folder")
        elif kind == "pkg":
            p = filedialog.askopenfilename(title="Select PKG",
                                           filetypes=[("PS4 PKG", "*.pkg"), ("All", "*.*")])
        else:
            p = filedialog.askopenfilename(title="Select file",
                                           filetypes=[("PNG image", "*.png"), ("All", "*.*")])
        if p:
            self.v[key].set(p)

    def install_tools(self):
        """Copy orbis-pub-cmd / sfo into ./tools so no PATH or folder setup is needed."""
        d = filedialog.askdirectory(
            title="Select the folder that contains orbis-pub-cmd and/or sfo")
        if not d:
            return
        src = Path(d)

        def find(name):
            want = {name, name + ".exe"}
            for dp, dn, fns in os.walk(src):
                if len(Path(dp).parts) - len(src.parts) >= 3:
                    dn[:] = []
                for fn in fns:
                    if fn.lower() in want:
                        return Path(dp) / fn
            return None
        o_exe, s_exe = find("orbis-pub-cmd"), find("sfo")
        if not o_exe and not s_exe:
            messagebox.showwarning(APP_TITLE, "Neither orbis-pub-cmd nor sfo was found in that "
                                              "folder (looked 3 levels deep).")
            return
        done = []
        try:
            if o_exe:
                dest = TOOLS_DIR / "orbis-pub-cmd"
                # orbis-pub-cmd ships with extra files next to it, so copy its whole folder
                shutil.copytree(o_exe.parent, dest, dirs_exist_ok=True)
                make_executable(dest / o_exe.name)
                if os.name != "nt" and o_exe.name.lower().endswith(".exe") \
                        and not (dest / "orbis-pub-cmd").exists():
                    (dest / "orbis-pub-cmd").write_text(ORBIS_WRAPPER, encoding="utf-8")
                    make_executable(dest / "orbis-pub-cmd")
                done.append("orbis-pub-cmd")
            if s_exe:
                dest = TOOLS_DIR / "sfo"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s_exe, dest / s_exe.name)
                make_executable(dest / s_exe.name)
                done.append("sfo")
        except (OSError, shutil.Error) as e:
            messagebox.showerror(APP_TITLE, f"Install failed:\n{e}")
            return
        self.log(f"[Info] installed {', '.join(done)} into {TOOLS_DIR}", "ok")
        self.update_tool_status()

    def update_tool_status(self):
        o = resolve_tool(self.v["orbis_dir"].get().strip(), "orbis-pub-cmd")
        s = resolve_tool(self.v["sfo_dir"].get().strip(), "sfo")
        txt = (f"orbis-pub-cmd: {'found' if o else 'NOT FOUND'}\n"
               f"sfo: {'found' if s else 'NOT FOUND'}")
        ok = bool(o and s)
        if not IS_WIN:
            w = find_wine()
            txt += f"\nwine: {'found' if w else 'NOT FOUND'}"
            ok = ok and bool(w)
        self.tool_label.configure(text=txt,
                                  foreground=self.pal["ok"] if ok else self.pal["bad"])

    # ----- updates / tool download ---------------------------------------- #
    def start_update_check(self, manual=False):
        def work():
            try:
                info = check_for_update()
                self.q.put(("call", lambda: self.on_update_info(manual, info, None)))
            except Exception as e:      # noqa: BLE001 - never let the checker crash the app
                self.q.put(("call", lambda e=e: self.on_update_info(manual, None, e)))
        threading.Thread(target=work, daemon=True).start()

    def on_update_info(self, manual, info, err):
        if err is not None:
            if manual:
                messagebox.showwarning(APP_TITLE, f"Update check failed:\n{err}")
            return
        if info is None:
            if manual:
                messagebox.showinfo(APP_TITLE, f"You are up to date (v{__version__}).")
            return
        notes = info["notes"].strip()
        if len(notes) > 800:
            notes = notes[:800] + "..."
        msg = f"Version {info['version']} is available (you have {__version__}).\n\n"
        if notes:
            msg += notes + "\n\n"
        if can_self_update() and pick_update_asset(info["assets"]):
            if messagebox.askyesno(APP_TITLE, msg + "Download and install it now?"):
                self.run_self_update(info)
        elif messagebox.askyesno(APP_TITLE, msg + "Open the download page?"):
            webbrowser.open(info["url"])

    def run_self_update(self, info):
        last = [-1]

        def progress(done, total):
            pct = int(done * 100 / total) if total else 0
            if pct != last[0]:
                last[0] = pct
                self.q.put(("call", lambda: self.status.configure(
                    text=f"Downloading update... {pct}%")))

        def work():
            try:
                self_update(info, progress)
            except UpdateError as e:
                def fail(e=e):
                    self.status.configure(text="Update failed")
                    if messagebox.askyesno(APP_TITLE, f"{e}\n\nOpen the download page instead?"):
                        webbrowser.open(info["url"])
                self.q.put(("call", fail))
                return

            def ok():
                self.status.configure(text="Update installed - restart to use it")
                messagebox.showinfo(APP_TITLE, f"Updated to {info['version']}.\n"
                                               "Please close and reopen the app.")
            self.q.put(("call", ok))
        threading.Thread(target=work, daemon=True).start()

    def get_sfo(self):
        if not messagebox.askyesno(
                APP_TITLE, f"Download the latest sfo build for this system from "
                           f"github.com/{SFO_REPO} into\n{TOOLS_DIR}?"):
            return
        self.log("[Info] downloading sfo ...", "info")

        def work():
            try:
                out = download_sfo(TOOLS_DIR / "sfo")
                self.log(f"[Info] sfo installed: {out}", "ok")
            except (UpdateError, OSError, ValueError) as e:
                self.log(f"[Error] could not get sfo: {e}", "error")
            self.q.put(("call", self.update_tool_status))
        threading.Thread(target=work, daemon=True).start()

    # ----- file list ------------------------------------------------------ #
    def add_path(self, p: Path):
        p = Path(p)
        if not p.is_file() or str(p) in self.file_set:
            return
        try:
            size = f"{p.stat().st_size / 1024 / 1024:,.1f} MB"
        except OSError:
            size = "?"
        self.tree.insert("", "end", iid=str(p), text=p.name, values=(str(p.parent), size))
        self.file_set[str(p)] = p
        if hasattr(self, "id_combos"):
            self.refresh_ids()

    def add_folder(self):
        d = filedialog.askdirectory(title="Folder containing .pkg files")
        if d:
            found = sorted(Path(d).glob("*.pkg"))
            if not found:
                messagebox.showinfo(APP_TITLE, "No .pkg files found in that folder.")
            for p in found:
                self.add_path(p)

    def add_files(self):
        for p in filedialog.askopenfilenames(title="Select PKG files",
                                             filetypes=[("PS4 PKG", "*.pkg"), ("All", "*.*")]):
            self.add_path(Path(p))

    def remove_selected(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.file_set.pop(iid, None)
        self.refresh_ids()

    def clear_files(self):
        for iid in list(self.file_set):
            self.tree.delete(iid)
        self.file_set.clear()
        self.refresh_ids()

    def show_details(self, _event=None):
        ids = self.tree.selection() or list(self.file_set)
        if not ids:
            messagebox.showwarning(APP_TITLE, "Add a .pkg file first.")
            return
        path = self.file_set.get(ids[0])
        try:
            info = parse_pkg(path)
        except (PkgError, OSError, struct.error) as e:
            messagebox.showerror(APP_TITLE, f"Could not read {Path(path).name}:\n{e}")
            return
        DetailsWindow(self, info)

    def target_files(self):
        sel = self.tree.selection()
        ids = sel if sel else list(self.file_set)
        return [self.file_set[i] for i in ids if i in self.file_set]

    # ----- logging / queue ------------------------------------------------ #
    def log(self, text, tag=""):
        """Thread-safe: may be called from the worker."""
        if not tag:
            if text.startswith("[Error]"):
                tag = "error"
            elif text.startswith("[Warn]"):
                tag = "warn"
        self.q.put(("log", text, tag))

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", item[1] + "\n", item[2] or ())
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "progress":
                    _, i, n = item
                    self.progress.configure(maximum=max(n, 1), value=i)
                    self.status.configure(text=f"{i}/{n} done")
                elif kind == "ask_base":
                    _, title, answer = item
                    p = filedialog.askopenfilename(
                        title=f"Select the ORIGINAL base-game PKG for: {title}",
                        filetypes=[("PS4 PKG", "*.pkg"), ("All", "*.*")])
                    answer.put(p or None)
                elif kind == "ask_choice":
                    _, title, prompt, options, answer = item
                    answer.put(self.choice_dialog(title, prompt, options))
                elif kind == "call":
                    try:
                        item[1]()
                    except Exception:       # noqa: BLE001
                        self.log("[Error] " + traceback.format_exc(limit=3), "error")
                elif kind == "done":
                    self.set_running(False)
        except queue.Empty:
            pass
        self.root.after(60, self.poll)

    def ask_base(self, title):
        """Called from the worker thread; blocks until the dialog is answered."""
        answer = queue.Queue()
        self.q.put(("ask_base", title, answer))
        return answer.get()

    def ask_choice(self, title, prompt, options):
        """Called from the worker thread; blocks until a choice is made (None = cancel)."""
        answer = queue.Queue()
        self.q.put(("ask_choice", title, prompt, options, answer))
        return answer.get()

    def choice_dialog(self, title, prompt, options):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        var = tk.IntVar(value=0)
        result = {"v": None}
        ttk.Label(win, text=prompt, justify="left").pack(anchor="w", padx=14, pady=(12, 6))
        for i, o in enumerate(options):
            ttk.Radiobutton(win, text=o, variable=var, value=i).pack(anchor="w", padx=24, pady=2)

        def ok():
            result["v"] = var.get()
            win.destroy()
        bf = ttk.Frame(win)
        bf.pack(pady=12)
        ttk.Button(bf, text="OK", command=ok, width=10).pack(side="left", padx=6)
        ttk.Button(bf, text="Cancel", command=win.destroy, width=10).pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        self.root.wait_window(win)
        return result["v"]

    # ----- jobs ----------------------------------------------------------- #
    def set_running(self, running: bool):
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_inspect.configure(state="disabled" if running else "normal")
        self.btn_merge.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        if not running:
            self.status.configure(text="Idle" if not self.stop_event.is_set() else "Stopped")

    def prepare(self, need_valid=True):
        if self.worker and self.worker.is_alive():
            return None
        st = self.collect()
        files = self.target_files()
        if not files:
            messagebox.showwarning(APP_TITLE, "Add at least one .pkg file first.")
            return None
        if need_valid:
            errs = validate(st)
            if errs:
                messagebox.showerror(APP_TITLE, "\n".join(errs))
                return None
        self.save_settings()
        self.stop_event.clear()
        self.processor = Processor(st, self.log, self.stop_event, self.ask_base, self.ask_choice)
        return files

    def start_job(self):
        files = self.prepare()
        if not files:
            return
        self.log(f"=== Starting: {len(files)} file(s) ===", "info")
        self.progress.configure(maximum=len(files), value=0)
        self.set_running(True)
        self.status.configure(text="Working...")
        self.run_thread(lambda: self.processor.process_all(
            files, lambda i, n: self.q.put(("progress", i, n))))

    def inspect_job(self):
        files = self.prepare(need_valid=False)
        if not files:
            return
        self.set_running(True)
        self.status.configure(text="Inspecting...")
        self.run_thread(lambda: self.processor.inspect(files))

    def merge_job(self):
        if self.worker and self.worker.is_alive():
            return
        st = self.collect()
        base, upd = Path(st.merge_base), Path(st.merge_update)
        if not st.merge_base or not base.is_file():
            messagebox.showwarning(APP_TITLE, "Select the base game PKG first.")
            return
        if not st.merge_update or not upd.is_file():
            messagebox.showwarning(APP_TITLE, "Select the update PKG first.")
            return
        if base.resolve() == upd.resolve():
            messagebox.showwarning(APP_TITLE, "Base and update are the same file.")
            return
        errs = validate(st)
        if errs:
            messagebox.showerror(APP_TITLE, "\n".join(errs))
            return
        self.save_settings()
        self.stop_event.clear()
        self.processor = Processor(st, self.log, self.stop_event, self.ask_base, self.ask_choice)
        self.log(f"=== Merging: {base.name} + {upd.name} ===", "info")
        self.progress.configure(maximum=1, value=0)
        self.set_running(True)
        self.status.configure(text="Merging...")
        self.run_thread(lambda: self.processor.merge(base, upd))

    def run_thread(self, fn):
        def target():
            try:
                fn()
            except ToolError as e:
                self.log(f"[Error] {e}", "error")
            except Cancelled:
                self.log("[Warn] Cancelled by user.", "warn")
            except Exception as e:
                self.log(f"[Error] {e}", "error")
                self.log(traceback.format_exc(), "error")
            finally:
                self.log("=== Finished ===", "info")
                self.q.put(("done",))
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def stop_job(self):
        self.stop_event.set()
        if self.processor:
            self.processor.kill()
        self.status.configure(text="Stopping...")


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("--version", "-V"):
        print(f"{APP_TITLE} {__version__}")
        return
    # headless: python ps4_pkg_region_gui.py --parse file.pkg [more.pkg ...]
    if len(sys.argv) >= 3 and sys.argv[1] == "--parse":
        for p in sys.argv[2:]:
            print(f"### {p}")
            try:
                print(pkg_summary_text(parse_pkg(p)))
            except (PkgError, OSError, struct.error) as e:
                print(f"[Error] {e}")
        return
    if tk is None:
        sys.exit("tkinter is not installed. On Debian/Ubuntu: sudo apt install python3-tk")
    if IS_WIN:      # crisp text on HiDPI displays
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:       # noqa: BLE001
            pass
    cleanup_old_update()
    root = tk.Tk()
    app = App(root)
    if app.v["auto_update_check"].get() and updater_configured():
        root.after(2000, app.start_update_check)
    root.mainloop()


if __name__ == "__main__":
    main()
