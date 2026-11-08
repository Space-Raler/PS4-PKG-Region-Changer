#!/usr/bin/env python3
"""Release pusher: bump the version, update the changelog, commit, tag and push.

Pushing the tag starts .github/workflows/release.yml, which builds the AppImage,
Windows exe, macOS app and .deb, then publishes them as a GitHub Release. Every
installed copy of the app (AppImage / Windows exe update themselves, the others are
pointed at the download page) picks that release up through its "Check for updates".

Usage:
    python scripts/release.py patch                  # 1.3.0 -> 1.3.1
    python scripts/release.py minor --notes "Added X; Fixed Y"
    python scripts/release.py 2.0.0 --yes
    python scripts/release.py patch --dry-run        # show what would happen
    python scripts/release.py patch --no-push        # commit + tag locally only

Helper modes used by CI:
    python scripts/release.py --check-tag v1.3.1     # fail if tag != __version__
    python scripts/release.py --notes-for 1.3.1      # print that CHANGELOG section
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ps4_pkg_region_gui.py"
CHANGELOG = ROOT / "CHANGELOG.md"
VER_RE = re.compile(r'^(__version__\s*=\s*")([^"]+)(")', re.M)
REPO_RE = re.compile(r'^UPDATE_REPO\s*=\s*"([^"]*)"', re.M)


def die(msg):
    sys.exit(f"error: {msg}")


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if check and r.returncode:
        die(f"git {' '.join(args)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def current_version():
    m = VER_RE.search(APP.read_text(encoding="utf-8"))
    if not m:
        die(f"__version__ not found in {APP.name}")
    return m.group(2)


def next_version(cur, how):
    if re.fullmatch(r"\d+\.\d+\.\d+", how):
        return how
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", cur)
    if not m:
        die(f"current version {cur!r} is not X.Y.Z")
    major, minor, patch = map(int, m.groups())
    if how == "major":
        return f"{major + 1}.0.0"
    if how == "minor":
        return f"{major}.{minor + 1}.0"
    if how == "patch":
        return f"{major}.{minor}.{patch + 1}"
    die("bump must be patch, minor, major or an explicit X.Y.Z")


def version_tuple(v):
    return tuple(int(x) for x in v.split("."))


def changelog_section(version):
    text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def update_changelog(new, notes):
    text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else "# Changelog\n"
    if "## [Unreleased]" not in text:
        head, _, rest = text.partition("\n")
        text = f"{head}\n\n## [Unreleased]\n{rest}"
    m = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    body = m.group(1).strip()
    if notes:
        items = [n.strip() for n in re.split(r"[;\n]", notes) if n.strip()]
        body = (body + "\n" if body else "") + "\n".join(f"- {i}" for i in items)
    if not body:
        body = "- Maintenance release."
    today = datetime.date.today().isoformat()
    replacement = f"## [Unreleased]\n\n## [{new}] - {today}\n\n{body}\n\n"
    CHANGELOG.write_text(text[:m.start()] + replacement + text[m.end():].lstrip("\n"),
                         encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("bump", nargs="?", help="patch | minor | major | X.Y.Z")
    ap.add_argument("--notes", help="release notes; separate items with ';'")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true", help="commit and tag locally only")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--check-tag", metavar="TAG")
    ap.add_argument("--notes-for", metavar="VERSION")
    a = ap.parse_args()

    if a.check_tag:
        if a.check_tag.lstrip("v") != current_version():
            die(f"tag {a.check_tag} does not match __version__ {current_version()}")
        print("tag matches version")
        return
    if a.notes_for:
        print(changelog_section(a.notes_for.lstrip("v")) or f"Release {a.notes_for.lstrip('v')}")
        return
    if not a.bump:
        ap.error("give a bump: patch | minor | major | X.Y.Z")

    cur = current_version()
    new = next_version(cur, a.bump)
    tag = f"v{new}"
    if version_tuple(new) <= version_tuple(cur):
        die(f"new version {new} is not higher than {cur}")

    if git("rev-parse", "--is-inside-work-tree", check=False) != "true":
        die("not a git repository - run `git init` and add your GitHub remote first")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        die("detached HEAD - check out a branch first")
    if git("status", "--porcelain") and not a.allow_dirty:
        die("working tree has uncommitted changes - commit or stash them first "
            "(or pass --allow-dirty to include them in the release commit)")
    if git("tag", "--list", tag):
        die(f"tag {tag} already exists")
    if not a.no_push and "origin" not in git("remote").split():
        die("no 'origin' remote - add one (git remote add origin <url>) or use --no-push")

    m = REPO_RE.search(APP.read_text(encoding="utf-8"))
    if not m or m.group(1).upper().startswith("YOUR-"):
        print("warning: UPDATE_REPO in ps4_pkg_region_gui.py is still the placeholder, so the "
              "in-app updater will not find this release.\n")

    print(f"Release {cur} -> {new}   (tag {tag}, branch {branch}, "
          f"{'local only' if a.no_push else 'push to origin'})")
    if a.dry_run:
        print("dry run - nothing changed.")
        return
    if not a.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        die("aborted")

    src = APP.read_text(encoding="utf-8")
    APP.write_text(VER_RE.sub(lambda mm: f"{mm.group(1)}{new}{mm.group(3)}", src, count=1),
                   encoding="utf-8")
    update_changelog(new, a.notes)
    git("add", "-A" if a.allow_dirty else "ps4_pkg_region_gui.py", "CHANGELOG.md")
    git("commit", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"Release {new}")
    print(f"committed and tagged {tag}")
    if a.no_push:
        print(f"push later with:  git push origin {branch} && git push origin {tag}")
        return
    git("push", "origin", branch)
    git("push", "origin", tag)
    print(f"pushed. GitHub Actions is now building {tag}; watch the 'Actions' tab, then the "
          "release appears under 'Releases' and installed apps will offer it.")


if __name__ == "__main__":
    main()
