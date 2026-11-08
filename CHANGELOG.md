# Changelog

All notable changes to this project are documented here.
The text under a version heading becomes that release's notes on GitHub.

## [Unreleased]

## [1.3.0] - 2026-11-08

- Windows, macOS and Linux support: native `orbis-pub-cmd.exe` on Windows, wine detection on Linux/macOS, per-OS settings folders, HiDPI-aware on Windows, per-OS monospace font.
- Portable builds: AppImage (Linux), single-file exe (Windows), `.app` (macOS). Create a `portable.txt` next to the app to keep all settings and tools beside it.
- Built-in updater: checks GitHub Releases, installs AppImage / Windows exe updates in place (SHA-256 verified), and opens the download page for the other builds.
- Optional embedded tools: drop `orbis-pub-cmd` / `sfo` into `bundled-tools/` before building and they are packed into the app (not committed or published by CI).
- New "Get sfo..." button that downloads Hippie68's sfo for the current system.
- Release tooling: `scripts/release.py` and a GitHub Actions workflow that builds and publishes all artifacts.

## [1.2.0]

- Initial public packaging (Debian package, Tkinter GUI, base + update merge).
