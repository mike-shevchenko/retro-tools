#!/usr/bin/env python3
"""Rebuild the TrueType fonts of this directory from their .psd sources with pxfont.py.

Each font is made, then unpacked into FONT.ttf.files/ to check what it has. A font and a
directory that a run replaces are kept as .BAK; the .BAK of the run before is deleted.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PXFONT = os.path.join(HERE, os.pardir, "pxfont.py")
CODEPAGE = "cp1251"
# The source of each font, the chars that it has at places where the code page has none
# or has NBSP, and the chars that show the glyph of another.
FONTS = (
    ("ZX Spectrum 8x8px.psd", None, "00A9=.notdef,00A3=0060,2191=005E"),
    ("ZX Cyr 6x8px.psd", "98=00A3,A0=2191", None),
    ("ZX Cyr 8x8px.psd", "98=00A3,A0=2191", None),
    ("Agat-7 Cyr 7x8px.psd", None, None),
)


def remove_backups(font):
    """Delete the .BAK of a font and of its directory: pxfont.py does not overwrite one."""
    for path in (font + ".BAK", font + ".files.BAK"):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)


def main():
    failed = []
    for source, patches, aliases in FONTS:
        remove_backups(os.path.join(HERE, os.path.splitext(source)[0] + ".ttf"))
        command = [sys.executable, PXFONT, "ttf", "--unpack", "--codepage", CODEPAGE]
        if patches:
            command += ["--codepage-patches", patches]
        if aliases:
            command += ["--aliases", aliases]
        if subprocess.run(command + [source], cwd=HERE).returncode:
            failed.append(source)
    if failed:
        sys.exit("make_fonts.py: failed for " + ", ".join(failed))


if __name__ == "__main__":
    main()
