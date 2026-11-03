#!/usr/bin/env python3
"""Run tests, rebuild the fonts and collect the files of a release."""

# Written with the help of Kimi.

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "fonts"
BACKUPS = FONTS / "BAK"

# Source paths relative to the repository root, and their names in the release.
FILES = (
    ("agat_font_converter.py", "agat_font_converter.py"),
    ("apple2_disk_converter.py", "apple2_disk_converter.py"),
    ("pxfont.py", "pxfont.py"),
    ("fonts/ZX Spectrum 8x8px.ttf", "ZX.Spectrum.8x8px.ttf"),
    ("fonts/ZX Spectrum 8x8px.ttf.files/ZX Spectrum 8x8px.svg",
        "ZX.Spectrum.8x8px.svg"),
    ("fonts/ZX Cyr 6x8px.ttf", "ZX.Cyr.6x8px.ttf"),
    ("fonts/ZX Cyr 6x8px.ttf.files/ZX Cyr 6x8px.svg", "ZX.Cyr.6x8px.svg"),
    ("fonts/ZX Cyr 8x8px.ttf", "ZX.Cyr.8x8px.ttf"),
    ("fonts/ZX Cyr 8x8px.ttf.files/ZX Cyr 8x8px.svg", "ZX.Cyr.8x8px.svg"),
    ("fonts/ZX Cyr 8x8px Bold.ttf", "ZX.Cyr.8x8px.Bold.ttf"),
    ("fonts/ZX Cyr 8x8px Bold.ttf.files/ZX Cyr 8x8px Bold.svg",
        "ZX.Cyr.8x8px.Bold.svg"),
    ("fonts/ZX Cyr 8x8px Italic.ttf", "ZX.Cyr.8x8px.Italic.ttf"),
    ("fonts/ZX Cyr 8x8px Italic.ttf.files/ZX Cyr 8x8px Italic.svg",
        "ZX.Cyr.8x8px.Italic.svg"),
    ("fonts/Agat/Agat-7-128 7x8px.ttf", "Agat-7-128.7x8px.ttf"),
    ("fonts/Agat/Agat-7-128 7x8px.ttf.files/Agat-7-128 7x8px.svg",
        "Agat-7-128.7x8px.svg"),
    ("fonts/Agat/Agat-7-256 7x8px.ttf", "Agat-7-256.7x8px.ttf"),
    ("fonts/Agat/Agat-7-256 7x8px.ttf.files/Agat-7-256 7x8px.svg",
        "Agat-7-256.7x8px.svg"),
    ("fonts/Agat/Agat-9 7x8px.ttf", "Agat-9.7x8px.ttf"),
    ("fonts/Agat/Agat-9 7x8px.ttf.files/Agat-9 7x8px.svg", "Agat-9.7x8px.svg"),
    ("fonts/Agat/Agat 7x8px.ttf", "Agat.7x8px.ttf"),
    ("fonts/Agat/Agat 7x8px.ttf.files/Agat 7x8px.svg", "Agat.7x8px.svg"),
)


def run(script, *args):
    subprocess.run([sys.executable, str(ROOT / script), *args], cwd=ROOT, check=True)


def move_backups():
    if BACKUPS.exists():
        shutil.rmtree(BACKUPS)
    BACKUPS.mkdir()

    backups = sorted(FONTS.rglob("*.BAK"), key=lambda path: len(path.parts))
    for backup in backups:
        destination = BACKUPS / backup.relative_to(FONTS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"Moving {os.fspath(backup.relative_to(ROOT))} to "
              f"{os.fspath(destination.relative_to(ROOT))}", flush=True)
        shutil.move(os.fspath(backup), os.fspath(destination))


def make_release(version):
    release = ROOT / "release" / version
    if release.exists():
        sys.exit(f"release.py: {os.fspath(release)} already exists")

    missing = [source for source, _ in FILES if not (ROOT / source).is_file()]
    if missing:
        sys.exit("release.py: missing files: " + ", ".join(missing))

    release.mkdir(parents=True)
    for source, name in FILES:
        shutil.copy2(ROOT / source, release / name)
    print(f"Created {os.fspath(release)} with {len(FILES)} files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="release version, for example 2.0")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", args.version):
        parser.error("version must consist of numbers separated by dots")

    subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
        cwd=ROOT, check=True)
    run("fonts/make_fonts.py")
    move_backups()
    make_release(args.version)


if __name__ == "__main__":
    main()
