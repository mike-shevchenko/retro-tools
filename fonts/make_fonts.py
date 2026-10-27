#!/usr/bin/env python3
"""Rebuild the TrueType fonts of this directory with pxfont.py: the ZX fonts from their .psd
sources, the Agat fonts from the charts of their glyphs.

Each ZX font is made, then unpacked into FONT.ttf.files/ to check what it has. Each Agat chart
is made into the files of a TrueType font by agat_font_converter.py, and those into the font;
the three are then merged into one font of all their chars. A font and a directory that a run
replaces are kept as .BAK; the .BAK of the run before is deleted.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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

# The source of each font, its code page patches and its aliases.
FONTS = (
    ("ZX Spectrum 8x8px.psd", None, SPECTRUM_ALIASES),
    ("ZX Cyr 6x8px.psd", CYR_PATCHES, None),
    ("ZX Cyr 8x8px.psd", CYR_PATCHES, None),
)

# The charts of the Agat fonts, as agatcomp.ru publishes them. agat_font_converter.py makes
# each into "CHART 7x8px.ttf.files" beside it. The font of all three, made beside them too,
# takes a char that more of them have from the first: Agat-7's own set, then its set of 256
# glyphs, then Agat-9's.
AGAT_CHARTS = ("Agat/Agat-7-128.png", "Agat/Agat-7-256.png", "Agat/Agat-9.png")
AGAT_SIZE = " 7x8px"
AGAT_MERGED = "Agat/Agat" + AGAT_SIZE


def find_script(script):
    """The command that runs a tool of this repository: its .py file in the directory above,
    or on PATH, or else a launcher on PATH, named as the tool or with hyphens for its
    underscores."""
    for path in (os.path.abspath(os.path.join(HERE, os.pardir, script + ".py")),
            shutil.which(script + ".py")):
        if path and os.path.isfile(path):
            return [sys.executable, path]
    # A launcher, such as a .cmd file, is no Python script: it runs by itself.
    for name in (script, script.replace("_", "-")):
        launcher = shutil.which(name)
        if launcher:
            return [launcher]
    sys.exit(f"make_fonts.py: unable to find {script!r}")


def remove_backups(font):
    """Delete the .BAK of a font and of its directory: pxfont.py does not overwrite one."""
    for path in (font + ".BAK", font + ".files.BAK"):
        if os.path.isdir(path):
            print(f"Deleting {path!r}", flush=True)
            shutil.rmtree(path)
        elif os.path.exists(path):
            print(f"Deleting {path!r}", flush=True)
            os.remove(path)


def run(command):
    """Run a command in this directory; whether it succeeded."""
    return subprocess.run(command, cwd=HERE).returncode == 0


def main():
    pxfont = find_script("pxfont")
    agat_font_converter = find_script("agat_font_converter")

    failed = []
    for source, patches, aliases in FONTS:
        remove_backups(os.path.join(HERE, os.path.splitext(source)[0] + ".ttf"))
        command = pxfont + ["ttf", "--unpack", "--codepage", CODEPAGE]
        if patches:
            command += ["--codepage-patches", patches]
        if aliases:
            command += ["--aliases", aliases]
        if not run(command + [source]):
            failed.append(source)

    # The files that the converter makes are the source of a font, so pxfont.py cannot
    # unpack the font into them for a check, as it does for the ZX fonts.
    agat_files = []
    for chart in AGAT_CHARTS:
        font = os.path.join(HERE, os.path.splitext(chart)[0] + AGAT_SIZE + ".ttf")
        remove_backups(font)
        files = font + ".files"
        if run(agat_font_converter + [chart]) and run(pxfont + ["ttf", files]):
            agat_files.append(files)
        else:
            failed.append(chart)
    if len(agat_files) == len(AGAT_CHARTS):
        remove_backups(os.path.join(HERE, AGAT_MERGED + ".ttf"))
        if not run(pxfont + ["merge", AGAT_MERGED] + agat_files):
            failed.append(AGAT_MERGED)

    if failed:
        sys.exit("make_fonts.py: failed for " + ", ".join(failed))


if __name__ == "__main__":
    main()
