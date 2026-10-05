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

# ZX Spectrum keeps the codes of the machine, where three places of ASCII hold other signs.
# An application takes a char that a font lacks from another font, so each sign also gets
# its own Unicode char, as an alias of the glyph:
#   00A9=.notdef: the copyright sign, drawn at 0x7F, where it is the glyph of a missing char;
#   00A3=0060: the pound sign, drawn at the code of the backtick;
#   2191=005E: the up arrow, drawn at the code of the caret.
SPECTRUM_ALIASES = "00A9=.notdef,00A3=0060,2191=005E"

# The ZX Cyr fonts have the caret and the backtick of ASCII at those codes, so the two signs
# of Spectrum are drawn at places that CP1251 can spare, and a patch tells what they are:
#   98=00A3: the pound sign, at the one code that CP1251 leaves undefined;
#   A0=2191: the up arrow, at the place of NBSP, which needs no glyph of its own: it shows
#     the space instead.
# Their copyright sign is drawn where CP1251 has it, at 0xA9, and needs nothing.
CYR_PATCHES = "98=00A3,A0=2191"

# The source of each font, its code page patches and its aliases. Agat-7 needs neither: its
# currency sign is drawn both at the code of the dollar, as the machine has it, and at 0xA4.
FONTS = (
    ("ZX Spectrum 8x8px.psd", None, SPECTRUM_ALIASES),
    ("ZX Cyr 6x8px.psd", CYR_PATCHES, None),
    ("ZX Cyr 8x8px.psd", CYR_PATCHES, None),
    ("Agat-7 7x8px.psd", None, None),
)


def remove_backups(font):
    """Delete the .BAK of a font and of its directory: pxfont.py does not overwrite one."""
    for path in (font + ".BAK", font + ".files.BAK"):
        if os.path.isdir(path):
            print(f"Deleting {path!r}")
            shutil.rmtree(path)
        elif os.path.exists(path):
            print(f"Deleting {path!r}")
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
