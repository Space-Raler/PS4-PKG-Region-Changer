# PS4 PKG Region Changer

A small desktop GUI (Python + Tkinter, no third-party Python packages) that batch-changes the
**Region, RegionID, TitleID, ContentName, SDK version and app version** of PS4 PKG files
(base game, patch, DLC), and can **merge a base game + its update** into one full-game PKG.

It drives two command-line tools for you:

| Tool | What it is | Included? |
|------|------------|-----------|
| `orbis-pub-cmd` | Sony's PKG tool (Windows program) | **No** – bring your own copy |
| `sfo` | Hippie68's `param.sfo` editor | Embedded for Linux x86-64; **Get sfo...** button downloads it elsewhere |

Works on **Windows**, **Linux** and **macOS**. Use it only with content you legally own. This project
is not affiliated with or endorsed by Sony.

---

## Download

Grab the latest build from the **Releases** page:

| Platform | File | Notes |
|----------|------|-------|
| Linux (any distro) | `PS4-PKG-Region-Changer-<ver>-x86_64.AppImage` | Portable, nothing to install |
| Windows 10/11 | `PS4-PKG-Region-Changer-<ver>-windows-x64.exe` | Portable single file |
| macOS | `PS4-PKG-Region-Changer-<ver>-macos-<arch>.zip` | Unsigned `.app` |
| Debian / Ubuntu / Mint | `ps4-pkg-region-changer_<ver>_all.deb` | `sudo apt install ./file.deb` |
| Any (from source) | `ps4-pkg-region-changer-<ver>-source.zip` | Needs Python 3 + tkinter |

`SHA256SUMS.txt` is attached to every release.

### Linux AppImage
```sh
chmod +x PS4-PKG-Region-Changer-*.AppImage
./PS4-PKG-Region-Changer-*.AppImage
```
If it complains about FUSE, install `libfuse2` (Ubuntu 22.04+: `sudo apt install libfuse2`) or run it
with `--appimage-extract-and-run`.

### Windows
Run the `.exe`. Because it is an unsigned, PyInstaller-packed program, Windows SmartScreen may show
"Windows protected your PC" (click *More info* → *Run anyway*) and some antivirus tools flag
PyInstaller files as false positives. If that bothers you, run from source instead (`run.bat`).

### macOS
The app is not code-signed. After unzipping, right-click the app → **Open** the first time, or run
`xattr -dr com.apple.quarantine "PS4 PKG Region Changer.app"`.

### From source (all platforms)
Needs Python 3.8+ with tkinter (included in the python.org installers for Windows/macOS;
Debian/Ubuntu: `sudo apt install python3-tk`).

```sh
./run.sh            # Linux / macOS   (run.command also works by double-click on macOS)
run.bat             # Windows
python3 ps4_pkg_region_gui.py
```

---

## Setting up the tools

Open the **Tools** page.

1. **orbis-pub-cmd** – click **Install tools...** and choose the folder that contains
   `orbis-pub-cmd.exe` (your PS4 Fake PKG Tools folder). The whole folder is copied into the app's
   tools directory. It is searched 3 levels deep.
2. **sfo** – on Linux x86-64 it is already embedded. Otherwise click **Get sfo...** to download the
   build for your system from the sfo GitHub releases, or build/download it yourself and use
   **Install tools...** (or point *sfo folder* at it).

**Windows:** `orbis-pub-cmd.exe` runs natively – nothing else needed.

**Linux / macOS:** `orbis-pub-cmd.exe` runs through **wine**, which must be installed:

```sh
sudo apt install wine                 # Debian / Ubuntu / Mint
sudo dnf install wine                 # Fedora
brew install --cask wine-stable       # macOS (Homebrew)
```
The sidebar shows `found / NOT FOUND` for `orbis-pub-cmd`, `sfo` and (on Linux/macOS) `wine`.
You can force a specific wine with the `WINE` environment variable.

---

## Embedding the tools in your own build

You can bake the tools into the AppImage / exe / app / `.deb` so a fresh machine needs no setup:

1. Make a folder `bundled-tools/` next to `ps4_pkg_region_gui.py`:
   ```
   bundled-tools/
     orbis-pub-cmd/    <- your whole PS4 Fake PKG Tools folder (orbis-pub-cmd.exe + its files)
     sfo/              <- sfo (or run: python packaging/fetch_sfo.py  on the OS you build for)
   ```
2. Run the build script for your OS (see *Building locally*). It prints `Embedding bundled-tools/`.
3. On first start the app copies the embedded tools into its tools folder. A tool you installed
   yourself is never overwritten, and tools stay in place across in-app updates.

**Important:** `orbis-pub-cmd` is proprietary Sony software. `bundled-tools/` is in `.gitignore` so it
is never committed, and the GitHub Actions release builds do **not** contain it. Build embedded
versions locally and keep them for your own use; do not attach them to a public release.
Linux/macOS builds still need wine installed on the machine (wine is not embedded).

---

## Portable mode and where data lives

The app stores a settings file and the `tools` folder. Where depends on how you run it:

| How you run it | Settings + tools |
|----------------|------------------|
| **Portable** – a file named `portable.txt` sits next to the AppImage / exe / script (or `PS4PKG_PORTABLE=1`) | `data/` folder next to the app |
| From source / source zip in a writable folder | Next to `ps4_pkg_region_gui.py` |
| Installed `.deb`, or a frozen build without `portable.txt` | Linux `~/.config/ps4-pkg-region-changer` + `~/.local/share/ps4-pkg-region-changer`<br>Windows `%APPDATA%\ps4-pkg-region-changer` + `%LOCALAPPDATA%\ps4-pkg-region-changer`<br>macOS `~/Library/Application Support/ps4-pkg-region-changer` |
| Anything, with `PS4PKG_DATA_DIR=/some/folder` | That folder |

To make the AppImage or exe fully portable (e.g. on a USB stick), create an empty `portable.txt` next
to it.

---

## Updates

**Settings → About → Check for updates** (and an automatic check at startup, which you can turn off
under *Settings → Options*).

* **AppImage** and **Windows exe**: offers to download and install the new version in place. The
  download is verified against the release's `SHA256SUMS.txt` and is discarded if it does not match.
  Restart the app afterwards.
* **.deb, macOS, source**: opens the release page so you can download the new version.

---

## For maintainers: publishing updates ("update pusher")

Updates are shipped through **GitHub Releases**. You push one command; GitHub Actions builds every
package and publishes the release; installed apps then find it.

### One-time setup
1. Create a GitHub repository and push this project to it:
   ```sh
   git init -b main && git add -A && git commit -m "Initial commit"
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. Open `ps4_pkg_region_gui.py` and set `UPDATE_REPO = "<you>/<repo>"`. (Until you do, the updater is
   disabled.) Optionally also edit `LICENSE` (your name) and `DEB_MAINTAINER` in
   `packaging/build_deb.sh`.
3. In the repo: *Settings → Actions → General → Workflow permissions* must allow the workflow to
   create releases (the workflow requests `contents: write`).
4. The repository must be **public** – the in-app updater calls the unauthenticated GitHub API.

### Shipping a release
```sh
python scripts/release.py patch --notes "Fixed X; Added Y"     # 1.3.0 -> 1.3.1
python scripts/release.py minor                                 # 1.3.0 -> 1.4.0
python scripts/release.py 2.0.0                                 # explicit
python scripts/release.py patch --dry-run                       # preview only
```
`scripts/release.py` checks the working tree is clean, bumps `__version__`, moves the
`## [Unreleased]` section of `CHANGELOG.md` under the new version, commits, tags `vX.Y.Z` and pushes.
The tag triggers `.github/workflows/release.yml`, which builds the AppImage, `.deb`, Windows exe and
macOS zip, creates `SHA256SUMS.txt`, and publishes everything with the changelog as release notes.
Write user-facing changes under `## [Unreleased]` in `CHANGELOG.md` as you work.

**First release:** the project already declares version 1.3.0, so tag it directly:
`git tag -a v1.3.0 -m "Release 1.3.0" && git push origin v1.3.0`.

### What the updater relies on
Keep these conventions or the in-place update stops matching files:

* Release tag `vX.Y.Z` and `__version__ = "X.Y.Z"` must agree (CI enforces it).
* Asset names: AppImage ends in `.AppImage` and contains the CPU arch (`x86_64`/`aarch64`); the Windows
  exe ends in `.exe` and contains `windows`.
* A `SHA256SUMS.txt` asset in `sha256sum` format listing every file. Without it the app refuses to
  self-install and just opens the release page.
* Only releases that are *published* and not marked pre-release/draft are offered.
* Unauthenticated GitHub API limit is 60 requests/hour per IP – plenty for a startup check.

### Hosting somewhere other than GitHub
Replace `check_for_update()` / `self_update()` in `ps4_pkg_region_gui.py`. They only need a JSON
document with a version, notes and per-platform download URLs plus a SHA-256 for each file.

---

## Building locally

Needs Python 3 with tkinter, and `pip install -r requirements-build.txt`.

```sh
bash packaging/build_appimage.sh     # Linux  -> dist/*.AppImage  (downloads appimagetool if missing)
bash packaging/build_deb.sh          # Linux  -> dist/*.deb       (needs dpkg-deb)
bash packaging/build_macos.sh        # macOS  -> dist/*.zip
packaging\build_windows.bat          # Windows -> dist\*.exe
```
Build on an older distro (CI uses Ubuntu 22.04) for the AppImage to run on more systems. Each target
must be built on its own OS; PyInstaller does not cross-compile.

Developer helpers: `python ps4_pkg_region_gui.py --version`, and
`python ps4_pkg_region_gui.py --parse file.pkg` prints a PKG's header/SFO info without opening a window.

---

## Troubleshooting

* **`tkinter is not installed`** – `sudo apt install python3-tk` (Linux), or reinstall Python from
  python.org with tcl/tk (Windows/macOS).
* **`wine: NOT FOUND`** – install wine (see above). On macOS the app also looks in
  `/opt/homebrew/bin`, `/usr/local/bin` and `/Applications/Wine*.app`.
* **`sfo: NOT FOUND`** – use **Get sfo...** or **Install tools...**. The embedded sfo only runs on
  Linux x86-64.
* **`orbis-pub-cmd: NOT FOUND`** – use **Install tools...** and pick the folder containing
  `orbis-pub-cmd.exe`.
* **Update check says "No release found"** – `UPDATE_REPO` is wrong or the repo is private / has no
  published release yet.
* **AppImage will not start** – install `libfuse2`, or run with `--appimage-extract-and-run`.

---

## Third-party software and license

This project is MIT licensed (see `LICENSE`). It downloads/uses, but does not own:

* **orbis-pub-cmd** – proprietary Sony software, not distributed here.
* **sfo** by Hippie68 – embedded for Linux x86-64 and downloadable via the app; check its repository
  for its license terms before redistributing a build that contains it.
* **Python, Tk, PyInstaller** – bundled into the AppImage/exe/app builds under their own licenses
  (PSF, Tcl/Tk, GPL-with-bootloader-exception respectively).
