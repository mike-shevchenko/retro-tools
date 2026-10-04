#!/usr/bin/env python3
"""Unpack a pixel font, a Windows .fon file or a TrueType font drawn of pixels, into editable
files, make the font of them again, make a new one, or convert a .fon to TrueType.

See pxfont --help.
"""
# Written with the help of Claude Fable 5.1.

import argparse
import codecs
import collections
import functools
import io
import json
import logging
import math
import os
import re
import struct
import sys
import unicodedata

# Pillow reads and writes the PNG files. The import is checked when a verb runs, so that
# --help works without it.
try:
    from PIL import Image
except ImportError:
    Image = None

# fontTools reads and writes the TrueType files. The import is checked when a verb needs it.
try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont, newTable
except ImportError:
    FontBuilder = None

USAGE = """\
Usage:
pxfont unpack [--encoding NAME] FILE.fon|FILE.ttf
pxfont pack [--encoding NAME] [--rows N] DIR.files|FONT.png|FONT.psd|FONT.txt
pxfont create [--encoding NAME] FONT
pxfont ttf [--encoding NAME] [--em N] [--rows N] FILE.fon|DIR.files|FONT.png|...
pxfont expand|contract [--rows N] COLUMNS DIR.files|FONT.png|FONT.psd|FONT.txt
pxfont bold|italic|bold-italic [--rows N] DIR.files|FONT.png|FONT.psd|FONT.txt

unpack: write a 16-bit .fon file, or a TrueType font drawn of pixels, as files to edit, into
  FILE.files/. It reports what is broken, and whether packing gives the same file back.
pack: make the font file of DIR.files/, which any inconsistency in the files stops; or make
  FONT.fon, one fixed-pitch font of the chars from 32 on, of its bitmaps alone.
create: write FONT.fon.files/ of a blank font, 8x8 or of the size in the name: "zx 6x10px".
ttf: make a TrueType file of every font, each pixel a square, exact at --em pixels to the em
  (unless told, the height) and its multiples. Chars get Unicode by dfCharSet or --encoding.
expand, contract: repeat, or leave out, the pixel columns COLUMNS of every glyph: "0,3,7".
bold, italic, bold-italic: lay each glyph over itself a pixel to the right; move the upper
  half of each cell a pixel to the right. These five write a new file or directory.

The files of a directory, for each font:
  fon.json or ttf.json: every field. One named _x is computed by pack, which ignores it.
  NAME.png: the bitmaps, 32 places to a row, a char at its code modulo 32. The papers are
    white and C0C0C0 by turns; the ink is 000080 on white and black on gray. A place of no
    char or of a zero-width one is transparent, and so is the paper of the default char, 127.
  NAME.txt: the same as text: X ink, `.` paper or a space for none, `|` and dashes between.
  NAME.svg: a picture to lay under the PNG, with the char and the code in each cell.
  pack takes the PNG or the text; when both are there they must agree: delete the old one.

A fixed-pitch font may have plainer bitmaps: text without the `|` and the dashes, and a PNG
  or a Photoshop .psd in any colors. Its first cell, blank, tells the paper: a color within
  64 of it in every channel is paper, 120 or more away in some is ink; transparent is paper.

Bitmaps alone: the file is named as the font, "zx 6x8px Bold.png". The size in the name
  tells the rows, or --rows N does: 7 unless told, the chars 32..255. E, F, H, L, T and Z
  give the ascent. The glyph at 127 is shown for a missing char. The face ends with " FON".

Names: a font is named by its face, its size, "6x8px" or "13px" when the widths differ, and
  Bold, Italic. The bitmap files, the .ttf files and what the altering verbs write are named
  so; a new width gives a new size, or "wide" or "narrow" when the name tells no size.

TrueType: a glyph is the pixels whose centers its outline holds; glyphs off the grid are
  reported, hinting and kerning dropped. The places 0..255 follow --encoding, or the one
  code page that the font declares, or cp1251; .notdef is at 127 when that place is free.

--encoding NAME: the encoding of texts and chars, a Python codec; cp1251 unless recorded.
"""

JSON_NAME = "fon.json"
TTF_JSON = "ttf.json"
FILES_SUFFIX = ".files"
BACKUP_SUFFIX = ".BAK"
LINE_WIDTH = 99
DEFAULT_ENCODING = "cp1251"

CHARS_PER_ROW = 32
# The image files that hold the bitmaps of a font alone; of a .psd, the flattened image.
IMAGE_EXTENSIONS = (".png", ".psd")
MAX_FONT_PIXELS = 1 << 24
# (paper, ink) of a char cell. Chars that have pixels take the two by turns along a row, and
# each row starts with the other one than the row above.
CELL_COLORS = (
    ((0xFF, 0xFF, 0xFF, 0xFF), (0x00, 0x00, 0x80, 0xFF)),
    ((0xC0, 0xC0, 0xC0, 0xFF), (0x00, 0x00, 0x00, 0xFF)),
)
CLEAR = (0, 0, 0, 0)
FULL_COLORING = frozenset(CELL_COLORS[0] + CELL_COLORS[1])
# A PNG in other colors takes the color of its first char, which must be blank, for paper.
# How far a color is from it, at most to be paper as well and at least to be ink, is counted
# in the channel that differs most; between the two a color is neither.
CODE_PREFIX = "U+"
BLANK_FIRST_CHARS = (0, 32, CODE_PREFIX + "0000", CODE_PREFIX + "0020")
# How many times larger than a pixel of the bitmaps the SVG picture of them shows it.
SVG_SCALE = 8
PAPER_DISTANCE = 64
INK_DISTANCE = 120
INK, PAPER, CHAR_SEPARATOR, ROW_SEPARATOR = "X", ".", "|", "-"
# What a text file has for the paper of the cell that an image shows without paper, and
# what a column of that cell is told by, where a column of a cell has its number.
NO_PAPER = " "
NO_CELL = -1

RT_FONTDIR, RT_FONT, RT_VERSION = 0x8007, 0x8008, 0x8010
RESOURCE_KINDS = (("fontdir", RT_FONTDIR), ("font", RT_FONT), ("version", RT_VERSION))

# A structure as (field, struct code, kind). The kind says how fon.json shows the value:
# "dec" and "hex" are numbers, "bytes" is a hex dump, "text" is a NUL-padded string.
MZ_HEADER = (
    ("e_magic", "H", "hex"), ("e_cblp", "H", "dec"), ("e_cp", "H", "dec"),
    ("e_crlc", "H", "dec"), ("e_cparhdr", "H", "dec"), ("e_minalloc", "H", "dec"),
    ("e_maxalloc", "H", "dec"), ("e_ss", "H", "hex"), ("e_sp", "H", "hex"),
    ("e_csum", "H", "hex"), ("e_ip", "H", "hex"), ("e_cs", "H", "hex"),
    ("e_lfarlc", "H", "dec"), ("e_ovno", "H", "dec"), ("e_res", "8s", "bytes"),
    ("e_oemid", "H", "hex"), ("e_oeminfo", "H", "hex"), ("e_res2", "20s", "bytes"),
    ("e_lfanew", "I", "dec"),
)
MZ_SIZE = 64

NE_HEADER = (
    ("ne_magic", "H", "hex"), ("ne_ver", "B", "dec"), ("ne_rev", "B", "dec"),
    ("ne_enttab", "H", "dec"), ("ne_cbenttab", "H", "dec"), ("ne_crc", "I", "hex"),
    ("ne_flags", "H", "hex"), ("ne_autodata", "H", "dec"), ("ne_heap", "H", "dec"),
    ("ne_stack", "H", "dec"), ("ne_csip", "I", "hex"), ("ne_sssp", "I", "hex"),
    ("ne_cseg", "H", "dec"), ("ne_cmod", "H", "dec"), ("ne_cbnrestab", "H", "dec"),
    ("ne_segtab", "H", "dec"), ("ne_rsrctab", "H", "dec"), ("ne_restab", "H", "dec"),
    ("ne_modtab", "H", "dec"), ("ne_imptab", "H", "dec"), ("ne_nrestab", "I", "dec"),
    ("ne_cmovent", "H", "dec"), ("ne_align", "H", "dec"), ("ne_cres", "H", "dec"),
    ("ne_exetyp", "B", "hex"), ("ne_flagsothers", "B", "hex"), ("ne_pretthunks", "H", "dec"),
    ("ne_psegrefbytes", "H", "dec"), ("ne_swaparea", "H", "dec"), ("ne_expver", "H", "hex"),
)
NE_SIZE = 64

RESOURCE = (
    ("rnOffset", "H", "dec"), ("rnLength", "H", "dec"), ("rnFlags", "H", "hex"),
    ("rnID", "H", "hex"), ("rnHandle", "H", "dec"), ("rnUsage", "H", "dec"),
)
RESOURCE_SIZE = 12

# The part of a font header that its FONTDIR entry repeats.
FNT_COMMON = (
    ("dfVersion", "H", "hex"), ("dfSize", "I", "dec"), ("dfCopyright", "60s", "text"),
    ("dfType", "H", "hex"), ("dfPoints", "H", "dec"), ("dfVertRes", "H", "dec"),
    ("dfHorizRes", "H", "dec"), ("dfAscent", "H", "dec"), ("dfInternalLeading", "H", "dec"),
    ("dfExternalLeading", "H", "dec"), ("dfItalic", "B", "dec"), ("dfUnderline", "B", "dec"),
    ("dfStrikeOut", "B", "dec"), ("dfWeight", "H", "dec"), ("dfCharSet", "B", "dec"),
    ("dfPixWidth", "H", "dec"), ("dfPixHeight", "H", "dec"),
    ("dfPitchAndFamily", "B", "hex"), ("dfAvgWidth", "H", "dec"), ("dfMaxWidth", "H", "dec"),
    ("dfFirstChar", "B", "dec"), ("dfLastChar", "B", "dec"), ("dfDefaultChar", "B", "dec"),
    ("dfBreakChar", "B", "dec"), ("dfWidthBytes", "H", "dec"), ("dfDevice", "I", "dec"),
    ("dfFace", "I", "dec"),
)
FNT_HEADER = FNT_COMMON + (
    ("dfBitsPointer", "I", "dec"), ("dfBitsOffset", "I", "dec"), ("dfReserved", "B", "hex"),
)
FNT_HEADER_SIZE = 118
FNT_HEADER_3 = (
    ("dfFlags", "I", "hex"), ("dfAspace", "H", "dec"), ("dfBspace", "H", "dec"),
    ("dfCspace", "H", "dec"), ("dfColorPointer", "I", "dec"), ("dfReserved1", "16s", "bytes"),
)
FNT_HEADER_3_SIZE = 148
FONTDIR_ENTRY = FNT_COMMON + (("dfReserved", "I", "hex"),)
FONTDIR_ENTRY_SIZE = 113

FIXED_FILE_INFO = tuple((name, "I", "hex") for name in (
    "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
    "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags", "dwFileOS",
    "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS"))
FIXED_FILE_INFO_SIZE = 52

# The fields that packing computes. fon.json shows each under its name with COMPUTED_MARK in
# front, and packing does not read it.
COMPUTED = frozenset(("e_lfanew", "ne_cbenttab", "ne_cbnrestab", "rnOffset", "rnLength",
    "dfSize", "dfBitsOffset", "dfDevice", "dfFace", "dfWidthBytes"))
COMPUTED_MARK = "_"

# What packing bitmaps alone makes: a font of the chars from the space on, as many rows of
# them as the bitmaps hold.
NEW_FIRST_CHAR = 32
NEW_ROWS = 7
# The letters whose top and bottom rows are those of every capital letter in any design.
NEW_LETTERS = "EFHLTZ"
NEW_DPI = 96
# The char whose glyph is shown for a char that the font lacks, when the font has it.
NEW_DEFAULT_CHAR = 127
# The size of a char in the blank font that the create verb makes.
BLANK_WIDTH = 8
BLANK_HEIGHT = 8

# A pixel in a TrueType font is a square this many font units wide, or fewer when the em
# would otherwise be larger than the format takes.
PIXEL_UNITS = 64
MAX_UNITS_PER_EM = 16384
MAX_COORDINATE = 32767
# The glyph that a TrueType font shows for a char that it lacks.
MISSING_GLYPH = ".notdef"
# The Windows code pages by the bits with which a TrueType font declares them.
CODE_PAGES = ("cp1252", "cp1250", "cp1251", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257")
# The encoding of the chars by dfCharSet, for the charsets that name one.
CHARSET_ENCODINGS = {
    0: "cp1252", 161: "cp1253", 162: "cp1254", 177: "cp1255", 178: "cp1256", 186: "cp1257",
    204: "cp1251", 238: "cp1250",
}
# How a TrueType file begins.
SFNT_MAGICS = (b"\0\1\0\0", b"true", b"OTTO", b"ttcf")
# A glyph is one of pixels when its points lie within this of the corners of pixels, in
# pixels.
GRID_TOLERANCE = 1 / 3
# A font has pixels of a size when this share of its coordinates lie this near to the lines
# of the grid. It is nearer than a fourth, which a grid four times coarser would allow.
GRID_SHARE = 0.9
GRID_NEAR = 0.2
# How many straight pieces stand for a curve of an outline when it is turned into pixels.
CURVE_PIECES = 16
MIN_PIXEL_UNITS = 4
# The tables that unpacking a TrueType font reads; any other is dropped.
TTF_TABLES = frozenset(("head", "hhea", "maxp", "OS/2", "hmtx", "cmap", "loca", "glyf",
    "name", "post", "gasp"))
# The texts of a TrueType font in ttf.json, with the ids that the name table has for them.
TTF_NAMES = (("copyright", 0), ("family", 1), ("style", 2), ("unique_id", 3),
    ("full_name", 4), ("version", 5), ("postscript_name", 6), ("trademark", 7),
    ("manufacturer", 8), ("designer", 9), ("description", 10), ("vendor_url", 11),
    ("designer_url", 12), ("license", 13), ("license_url", 14))
STYLES = {(False, False): "Regular", (True, False): "Bold", (False, True): "Italic",
    (True, True): "Bold Italic"}
# What a font name ends with: the style, and before it the size, as in "zx 6x8px Bold".
NAME_STYLE = re.compile(r"(?i)( bold)?( italic)?$")
NAME_SIZE = re.compile(r"(?i) (?:(\d+)x)?(\d+)px$")
# A font made from its bitmaps alone has this at the end of its face name, to keep it apart
# from the TrueType font of the same family; the names made of a face name leave it out.
FON_SUFFIX = " FON"
# What a font name calls a charset that names no encoding.
CHARSET_NAMES = {2: "Symbol", 255: "OEM"}
ROWS_MISPLACED = "--rows goes with a .png, a .psd or a .txt, which hold the bitmaps alone"
NEW_FONT = {
    "dfVersion": 0x0200, "dfCopyright": "", "dfType": 0, "dfExternalLeading": 0,
    "dfItalic": 0, "dfUnderline": 0, "dfStrikeOut": 0, "dfWeight": 400,
    "dfPitchAndFamily": 0x30, "dfDefaultChar": 0, "dfBreakChar": 0, "dfBitsPointer": 0,
    "dfReserved": 0,
}
# dfCharSet by the encoding of the texts; 1, the default charset, for any other.
NEW_CHARSETS = {
    "cp1250": 238, "cp1251": 204, "cp1252": 0, "cp1253": 161, "cp1254": 162, "cp1257": 186,
    "iso8859-1": 0, "cp437": 255, "cp850": 255, "cp866": 255,
}
# The headers and the DOS stub are those of the fonts shipped with Windows.
NEW_MZ = {
    "e_magic": 0x5A4D, "e_cblp": 251, "e_cp": 1, "e_crlc": 0, "e_cparhdr": 4, "e_minalloc": 0,
    "e_maxalloc": 65535, "e_ss": 0, "e_sp": 0xB8, "e_csum": 0, "e_ip": 0, "e_cs": 0,
    "e_lfarlc": 64, "e_ovno": 0, "e_res": "00" * 8, "e_oemid": 0, "e_oeminfo": 0,
    "e_res2": "00" * 20,
}
NEW_STUB = ("0E1FBA0E00B409CD21B8014CCD21546869732070726F6772616D2063616E6E6F"
    "742062652072756E20696E20444F53206D6F64652E0D0A240000000000000000")
NEW_NE = {
    "ne_magic": 0x454E, "ne_ver": 5, "ne_rev": 1, "ne_crc": 0, "ne_flags": 0x8300,
    "ne_autodata": 0, "ne_heap": 0, "ne_stack": 0, "ne_csip": 0, "ne_sssp": 0, "ne_cseg": 0,
    "ne_cmod": 0, "ne_cmovent": 0, "ne_align": 4, "ne_cres": 0, "ne_exetyp": 2,
    "ne_flagsothers": 0, "ne_pretthunks": 0, "ne_psegrefbytes": 0, "ne_swaparea": 0,
    "ne_expver": 0x030A,
}
# Version 1.0 of a raster font file for 16-bit Windows.
NEW_VERSION = {
    "dwSignature": 0xFEEF04BD, "dwStrucVersion": 0x00010000, "dwFileVersionMS": 0x00010000,
    "dwFileVersionLS": 0, "dwProductVersionMS": 0x00010000, "dwProductVersionLS": 0,
    "dwFileFlagsMask": 0x3F, "dwFileFlags": 0, "dwFileOS": 0x00010001, "dwFileType": 4,
    "dwFileSubtype": 1, "dwFileDateMS": 0, "dwFileDateLS": 0,
}

MISSING = object()

# The encoding of the texts in the file being unpacked or packed.
encoding = DEFAULT_ENCODING


class Failure(Exception):
    """A reported error."""

    def __init__(self, message, code=2, problems=()):
        Exception.__init__(self, message)
        self.code = code
        self.problems = list(problems)


def die(message, code=2):
    raise Failure(message, code)


def note(message):
    sys.stderr.write("[pxfont] " + message + "\n")


def exit_with(main):
    """Run main, and report a Failure or an unreadable file in one line each. An empty line
    ends the output of a run, to tell it from that of the next one; the help, after which
    main returns nothing, has none."""
    ends = True
    try:
        code = main()
        ends = code is not None
        sys.exit(code or 0)
    except Failure as failure:
        note(str(failure))
        for problem in failure.problems:
            note("  " + problem)
        sys.exit(failure.code)
    except OSError as error:
        what = error.strerror or str(error)
        if error.filename:
            what = "%s: %s" % (shown(str(error.filename)), what)
        note(what)
        sys.exit(2)
    except KeyboardInterrupt:
        note("interrupted")
        sys.exit(130)
    finally:
        sys.stdout.flush()
        if ends:
            sys.stderr.write("\n")


def shown(path):
    """Display form: forward slashes survive every shell, so a printed path can be pasted."""
    return path.replace("\\", "/")


def shown_directory(path):
    return shown(path).rstrip("/") + "/"


class Report:
    """What unpacking found wrong with the file, said as it is found and counted."""

    def __init__(self, quiet=False):
        self.errors = self.warnings = 0
        self.quiet = quiet

    def error(self, message):
        self.errors += 1
        if not self.quiet:
            note("Error: " + message)

    def warning(self, message):
        self.warnings += 1
        if not self.quiet:
            note("Warning: " + message)


def align(value, to):
    return (value + to - 1) // to * to


def set_encoding(name):
    global encoding
    try:
        encoding = codecs.lookup(name).name
    except (LookupError, TypeError):
        die("there is no encoding named %r" % (name,))


def text_of(data):
    """Bytes as text. Bytes that do not come back from the text unchanged, as those which
    the encoding lacks, are kept as "hex:" and hex digits."""
    data = bytes(data)
    try:
        text = data.decode(encoding)
        if not text.startswith("hex:") and text.encode(encoding) == data:
            return text
    except UnicodeError:
        pass
    return "hex:" + data.hex().upper()


def bytes_of(value, where):
    if not isinstance(value, str):
        die("%s: must be a string" % where)
    if value.startswith("hex:"):
        return unhex(value[4:], where)
    try:
        return value.encode(encoding)
    except UnicodeError:
        die("%s: has a character that the encoding %s lacks" % (where, encoding))


def hex_of(data):
    """A hex dump, as one string or, when long, as a list of 32-byte lines."""
    digits = bytes(data).hex().upper()
    if len(digits) <= 64:
        return digits
    return [digits[at:at + 64] for at in range(0, len(digits), 64)]


def unhex(value, where):
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        value = "".join(value)
    if not isinstance(value, str):
        die("%s: must be a string of hex digits, or a list of them" % where)
    try:
        return bytes.fromhex(value)
    except ValueError:
        die("%s: is not a string of hex digit pairs" % where)


def as_int(value, where, limit):
    """A number given in decimal, or as a "0x..." string, within 0..limit."""
    if isinstance(value, str):
        try:
            value = int(value, 16) if value.lower().startswith("0x") else int(value)
        except ValueError:
            die("%s: %r is not a number" % (where, value))
    if isinstance(value, bool) or not isinstance(value, int):
        die("%s: must be a number" % where)
    if not 0 <= value <= limit:
        die("%s: %d is out of the range 0..%d" % (where, value, limit))
    return value


class Fields:
    """An object of fon.json being read: every key must be taken, and none may be left,
    other than the computed ones, which are not read at all."""

    def __init__(self, value, where):
        if not isinstance(value, dict):
            die("%s: must be an object" % where)
        self.left = dict((key, item) for key, item in value.items()
            if not key.startswith(COMPUTED_MARK))
        self.where = where

    def take(self, name, default=MISSING):
        if name not in self.left:
            if default is MISSING:
                die("%s: %s is missing" % (self.where, name))
            return default
        return self.left.pop(name)

    def take_list(self, name, default=MISSING):
        value = self.take(name, default)
        if not isinstance(value, list):
            die("%s.%s: must be a list" % (self.where, name))
        return value

    def done(self):
        if self.left:
            die("%s: unknown key %s" % (self.where, ", ".join(sorted(self.left))))


def struct_format(spec):
    return "<" + "".join(code for _name, code, _kind in spec)


def raw_fields(spec, data, at):
    """The structure at the offset as plain numbers and bytes, by field name."""
    return dict(zip((name for name, _code, _kind in spec),
        struct.unpack_from(struct_format(spec), data, at)))


def read_fields(spec, data, at, computed=COMPUTED):
    """The structure at the offset, as fon.json shows it."""
    out = {}
    for (name, code, kind), value in zip(spec, struct.unpack_from(struct_format(spec), data, at)):
        if kind == "hex":
            value = "0x%0*X" % (struct.calcsize(code) * 2, value)
        elif kind == "bytes":
            value = hex_of(value)
        elif kind == "text":
            value = fixed_text(value)
        out[COMPUTED_MARK + name if name in computed else name] = value
    return out


def fixed_text(data):
    """A NUL-padded string. One with more than padding after its end is kept as "hex:..."."""
    body = data.rstrip(b"\0")
    text = text_of(body)
    if b"\0" in body or text.startswith("hex:"):
        return "hex:" + data.hex().upper()
    return text


def pack_fields(spec, value, where, computed=None):
    """The structure as bytes, and its numbers by field name. The computed fields are given
    here, by name; the others are read from the fon.json object."""
    fields = Fields(value, where)
    values, numbers = [], {}
    for name, code, kind in spec:
        here = "%s.%s" % (where, name)
        size = struct.calcsize(code)
        if computed is not None and name in computed:
            item = numbers[name] = computed[name]
            if not 0 <= item < 1 << (8 * size):
                die("%s%s: comes to %d, more than the field holds"
                    % (here[:-len(name)], COMPUTED_MARK + name, item))
        elif kind in ("dec", "hex"):
            item = numbers[name] = as_int(fields.take(name), here, (1 << (8 * size)) - 1)
        elif kind == "bytes":
            item = unhex(fields.take(name), here)
            if len(item) != size:
                die("%s: must be %d bytes, not %d" % (here, size, len(item)))
        else:
            item = bytes_of(fields.take(name), here)
            if len(item) > size:
                die("%s: is longer than %d bytes" % (here, size))
        values.append(item)
    fields.done()
    return struct.pack(struct_format(spec), *values), numbers


class Layout:
    """A file as the pieces stored in it, each at its stated offset."""

    def __init__(self, where, complain):
        self.where = where
        self.complain = complain
        self.pieces = []

    def place(self, offset, data, label):
        if data:
            self.pieces.append((offset, bytes(data), label))

    def extent(self):
        return max((offset + len(data) for offset, data, _label in self.pieces), default=0)

    def finish(self, size):
        """The assembled bytes. A piece that sticks out or runs into another is complained
        about."""
        image = bytearray(size)
        end, before = 0, None
        for offset, data, label in sorted(self.pieces, key=lambda piece: piece[:2]):
            if offset + len(data) > size:
                self.complain("%s: %s at %d..%d does not fit into %d bytes"
                    % (self.where, label, offset, offset + len(data), size))
                data = data[:max(0, size - offset)]
            if offset < end:
                self.complain("%s: %s at %d..%d runs into %s, which ends at %d"
                    % (self.where, label, offset, offset + len(data), before, end))
            image[offset:offset + len(data)] = data
            if offset + len(data) > end:
                end, before = offset + len(data), label
        return image

    def gaps(self, original):
        """The bytes of the original that no piece covers and that are not zero."""
        out = []
        at = 0
        for offset, data, _label in sorted(self.pieces, key=lambda piece: piece[:2]):
            out.extend(gap_of(original, at, min(offset, len(original))))
            at = max(at, offset + len(data))
        out.extend(gap_of(original, at, len(original)))
        return out


def gap_of(original, start, end):
    """The non-zero stretch of original[start:end], if any, as a list of one gap."""
    data = bytes(original[start:end])
    body = data.strip(b"\0")
    if not body:
        return []
    return [{"offset": start + data.index(body), "data": hex_of(body)}]


def tail_of(data, end, unit):
    """What follows the contents of a resource, as fon.json shows it: None for the zeros
    that pad the resource to its unit, a count for other zeros, and hex for anything else."""
    tail = bytes(data[end:])
    if any(tail):
        return hex_of(tail)
    return None if len(tail) == align(end, unit) - end else len(tail)


def take_tail(fields):
    tail = fields.take("tail", None)
    if tail is None:
        return b""
    if isinstance(tail, int) and not isinstance(tail, bool):
        return bytes(as_int(tail, fields.where + ".tail", 0xFFFFFF))
    return unhex(tail, fields.where + ".tail")


def ranges_text(codes):
    """Char codes as ranges: "127..160, 240"."""
    runs = []
    for code in sorted(codes):
        if runs and runs[-1][1] == code - 1:
            runs[-1][1] = code
        else:
            runs.append([code, code])
    return ", ".join("%d" % low if low == high else "%d..%d" % (low, high)
        for low, high in runs)


def parse_ranges(value, where, first, last):
    """The char codes that ranges like "127..160, 240" name, all within first..last."""
    if not isinstance(value, str):
        die("%s: must be a string of char codes and ranges, like \"127..160, 240\"" % where)
    codes = set()
    for part in value.split(","):
        if not part.strip():
            continue
        try:
            ends = [int(end) for end in part.split("..")]
        except ValueError:
            ends = []
        if len(ends) not in (1, 2) or ends[0] > ends[-1]:
            die("%s: %r is not a char code or a range like 127..160" % (where, part.strip()))
        if ends[0] < first or ends[-1] > last:
            die("%s: %s is not within dfFirstChar..dfLastChar, which are %d..%d"
                % (where, part.strip(), first, last))
        codes.update(range(ends[0], ends[-1] + 1))
    return codes


# ----------------------------------------------------------------------------------------
# Bitmaps


def glyph_rows(data, width, height):
    """A bitmap as rows of 0 and 1. It is stored in columns of bytes, each top to bottom."""
    return [[data[x // 8 * height + y] >> (7 - x % 8) & 1 for x in range(width)]
        for y in range(height)]


def glyph_bytes(rows, width, height):
    data = bytearray((width + 7) // 8 * height)
    for y, row in enumerate(rows):
        for x, bit in enumerate(row):
            if bit:
                data[x // 8 * height + y] |= 0x80 >> x % 8
    return bytes(data)


def fon_sheet(first, last):
    """The rows of the bitmaps of a font, 32 slots each: a char is in the slot of its code
    modulo 32, and a slot before the first char holds None."""
    return [[code if code >= first else None
        for code in range(base, min(base + CHARS_PER_ROW, last + 1))]
        for base in range(first - first % CHARS_PER_ROW, last + 1, CHARS_PER_ROW)]


def sheet_keys(sheet):
    return [key for row in sheet for key in row if key is not None]


def key_label(key):
    """What a message calls the char or the glyph of a slot."""
    if isinstance(key, int):
        return "char %d" % key
    return key if key.startswith(CODE_PREFIX) else "glyph %s" % key


def sheet_boxes(sheet, cells, gap):
    """Where the cells of a sheet lie: the key, the row, the left edge, the width, and which
    of the two colorings, which alternate by slot. A slot without pixels, one of no char or
    of a char of no width, has no cell, and takes the gap."""
    for r, row in enumerate(sheet):
        left = 0
        for slot, key in enumerate(row):
            width = cells[key][0] if key is not None else 0
            if width:
                yield key, r, left, width, (r + slot) % 2
            left += width or gap


def bare_cell(cells, key, gap):
    """The key of the cell that is shown without paper: that of the default char, at its
    place, 127, when it is as wide as the chars usually are, which is what tells its width
    to a reader. None when there is no such cell."""
    return key if key in cells and gap and cells[key][0] == gap else None


def write_png(path, sheet, cells, height, gap, bare=None):
    boxes = list(sheet_boxes(sheet, cells, gap))
    image = Image.new("RGBA", (max(left + width for _key, _r, left, width, _turn in boxes),
        len(sheet) * height), CLEAR)
    pixels = image.load()
    for key, r, left, width, turn in boxes:
        paper, ink = CELL_COLORS[turn]
        if key == bare:
            paper = CLEAR
        glyph = cells[key][1]
        for y in range(height):
            for x in range(width):
                pixels[left + x, r * height + y] = ink if glyph[y][x] else paper
    image.save(path, "PNG")


def write_svg(path, sheet, cells, height, gap, legend, bare=None):
    """A picture of the sheet to lay under it in a graphics editor: the same cells, and in
    each the glyph at half its size in a hairline frame, the char that it stands for beside
    it, and its code below. The legend gives the char, or None, and the code of a key."""
    boxes = list(sheet_boxes(sheet, cells, gap))
    size = (max(left + width for _key, _r, left, width, _turn in boxes), len(sheet) * height)

    def escaped(text):
        return "".join(char if char.isascii() and (char.isalnum() or char in " ._-")
            else "&#x%X;" % ord(char) for char in text)

    papers, frames, inks, chars, codes = [], [], [], [], []
    inset = height / 32
    for key, r, left, width, turn in boxes:
        top = r * height
        if key != bare:
            papers.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#%s"/>'
                % (left, top, width, height, "C0C0C0" if turn else "FFFFFF"))
        frames.append('<rect x="%g" y="%g" width="%g" height="%g"/>'
            % (left + inset, top + inset, width / 2, height / 2))
        for y, row in enumerate(cells[key][1]):
            x = 0
            while x < width:
                end = x
                while end < width and row[end]:
                    end += 1
                if end > x:
                    inks.append("M%g %gh%gv.5h-%gz" % (left + inset + x / 2,
                        top + inset + y / 2, (end - x) / 2, (end - x) / 2))
                x = end + 1
        char, code = legend(key)
        if char is not None and unicodedata.category(char)[0] not in "CZ":
            chars.append('<text x="%g" y="%g" font-size="%g">%s</text>' % (left + width * 0.75,
                top + height * 0.42, min(height * 0.42, width * 0.55), escaped(char)))
        # A code too long for its cell is set smaller, down to half the size, then squeezed.
        letters, room, fit = height * 0.42, width * 0.94, ""
        if len(code) * 0.6 * letters > room:
            letters = max(room / (len(code) * 0.6), letters / 2)
            fit = ' font-size="%g"' % letters
            if len(code) * 0.6 * letters > room * 1.001:
                fit += ' textLength="%g" lengthAdjust="spacingAndGlyphs"' % room
        codes.append('<text x="%g" y="%g"%s>%s</text>'
            % (left + width / 2, top + height * 0.93, fit, escaped(code)))
    font = "font-family=\"'Arial Unicode MS', Arial, sans-serif\" text-anchor=\"middle\""
    mono = "font-family=\"Consolas, 'Courier New', monospace\" text-anchor=\"middle\""
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d"'
        ' height="%d">' % (size + (size[0] * SVG_SCALE, size[1] * SVG_SCALE))] + papers + [
        '<g fill="none" stroke="#808080" stroke-width="%g">' % (height / 200)] + frames + [
        '</g>', '<path fill="#000000" d="%s"/>' % "".join(inks),
        '<g fill="#B00000" %s>' % font] + chars + [
        '</g>', '<g fill="#000080" font-size="%g" %s>' % (height * 0.42, mono)] + codes + [
        '</g>', '</svg>']
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def color_name(color):
    return "transparent" if not color[3] else "%02X%02X%02X" % color[:3] + (
        "" if color[3] == 255 else " with alpha %d" % color[3])


def column_cell(pixels, x, top, height, path):
    """Which of the two cell colorings a column of a row of chars is painted in. None when
    all of it is transparent, and NO_CELL when it is ink on transparent pixels, as a column
    of the cell without paper is."""
    found, papered = set(), False
    for y in range(top, top + height):
        color = pixels[x, y]
        if color in CELL_COLORS[0] or color in CELL_COLORS[1]:
            found.add(int(color in CELL_COLORS[1]))
            papered = papered or color in (CELL_COLORS[0][0], CELL_COLORS[1][0])
        elif not color[3]:
            found.add(None)
        else:
            die("%s: the pixel at %d,%d is %s; it must be %s or %s for paper, %s or %s for"
                " ink, or transparent" % (shown(path), x, y, color_name(color),
                color_name(CELL_COLORS[0][0]), color_name(CELL_COLORS[1][0]),
                color_name(CELL_COLORS[0][1]), color_name(CELL_COLORS[1][1])))
        if len(found - {None}) > 1 or (None in found and papered):
            die("%s: the pixels at %d,%d and above it are in different colorings; the image"
                " has no colors but those that unpack paints with, and then a column of a"
                " char must be all white with %s, all %s with %s, or all transparent"
                % (shown(path), x, y, color_name(CELL_COLORS[0][1]),
                color_name(CELL_COLORS[1][0]), color_name(CELL_COLORS[1][1])))
    if found == {None}:
        return None
    return NO_CELL if None in found else found.pop()


def full_coloring(opaque):
    """Whether the colors of an image are those of the full coloring: both papers, nothing
    else but their inks, and the ink of the white cells unless there is no ink at all.
    Black ink alone may as well lie on both papers, so it does not tell."""
    (white, blue), (gray, black) = CELL_COLORS
    return opaque <= FULL_COLORING and white in opaque and gray in opaque and (
        blue in opaque or black not in opaque)


def read_png(path, sheet, zero, height, plain, bare=None, gap=None):
    """The widths and glyphs a PNG holds, in the order of the keys of the sheet. Without a
    height given, the chars are as high as the image makes them. bare is the key of the
    cell that may be without paper, and gap is how wide the chars usually are."""
    try:
        image = Image.open(path).convert("RGBA")
    except (OSError, ValueError) as error:
        die("%s cannot be read as an image: %s" % (shown(path), error))
    if height is None:
        height = image.size[1] // len(sheet)
        if not height or image.size[1] != len(sheet) * height:
            die("%s is %d pixels high, which is not %d row(s) of chars of one height"
                % (shown(path), image.size[1], len(sheet)))
    if image.size[1] != len(sheet) * height:
        die("%s is %d pixels high, but %d row(s) of chars %d pixels high take %d"
            % (shown(path), image.size[1], len(sheet), height, len(sheet) * height))
    # The full coloring shows by its colors alone. Any other image is a grid of equal cells.
    if full_coloring(set(color for _count, color
            in image.getcolors(image.size[0] * image.size[1]) if color[3])):
        return read_png_cells(path, image, sheet, zero, height, bare, gap)
    width = plain_width(shown(path), image.size[0], max(len(row) for row in sheet), plain,
        "pixels", "the full coloring")
    if sheet[0][0] is not None and sheet[0][0] not in BLANK_FIRST_CHARS:
        die("%s is without the full coloring, and its paper is then told by its first cell,"
            " which has to be blank: one of no char, of char 0 or of the space; this one"
            " holds %s" % (shown(path), key_label(sheet[0][0])))
    return read_png_grid(path, image, sheet, width, height)


def plain_width(where, length, chars, plain, units, missing):
    """The width of every char in a row of bitmaps that does not mark where its chars end.
    Whether that will do is told by plain: False for a font that is not of fixed pitch,
    the width of the chars when dfPixWidth states one, and None for any width."""
    if plain is False:
        die("%s is without %s, which only a font of fixed pitch may leave out; bit 0 of"
            " dfPitchAndFamily says this one is not" % (where, missing))
    width = plain or length // chars
    if not width or length != chars * width:
        die("%s is %d %s wide, which is not %d chars %s" % (where, length, units, chars,
            "%d wide, as dfPixWidth says they are" % plain if plain else "of one width"))
    return width


def read_png_cells(path, image, sheet, zero, height, bare, gap):
    """The widths and glyphs of a PNG in the full coloring. A char ends where the coloring
    changes, so the chars without pixels have to be told. Transparent columns are no
    char's, wherever in a row they are. The cell that may be without paper is the last of
    its row, and is read after the others, which tell how wide the chars usually are."""
    pixels = image.load()
    widths, glyphs, later = [], [], None
    for r, row in enumerate(sheet):
        top, x, empty = r * height, 0, 0
        for slot, key in enumerate(row):
            if key is None or key in zero:
                empty += 1
                if key is not None:
                    widths.append(0)
                    glyphs.append([[] for _y in range(height)])
                continue
            turn = (r + slot) % 2
            if key == bare and slot == len(row) - 1:
                later = (len(widths), top, x, empty, turn)
                widths.append(0)
                glyphs.append(None)
                break
            while x < image.size[0] and column_cell(pixels, x, top, height, path) is None:
                x += 1
            start, (paper, ink), empty = x, CELL_COLORS[turn], 0
            while x < image.size[0] and column_cell(pixels, x, top, height, path) == turn:
                x += 1
            if x == start:
                die("%s: row %d of chars has at pixel %d no cell for %s: the image has no"
                    " colors but those that unpack paints with, and then %s paper with %s"
                    " ink is expected there, as the papers alternate by the places of a row,"
                    " those without a char counted; the row has fewer chars with pixels"
                    " than the font has for it, or two neighbors in one coloring"
                    % (shown(path), r + 1, x, key_label(key), color_name(paper),
                    color_name(ink)))
            widths.append(x - start)
            glyphs.append([[int(pixels[at, top + y] == ink) for at in range(start, x)]
                for y in range(height)])
        else:
            row_ends(path, image, top, height, x)
    if later:
        at, top, x, empty, turn = later
        usual = collections.Counter(width for width in widths if width).most_common(1)
        widths[at], glyphs[at] = read_last_cell(path, image, top, height, x, turn, bare,
            empty, gap or (usual[0][0] if usual else 0))
    return widths, glyphs


def row_ends(path, image, top, height, x):
    """Refuse pixels in a row of chars past its last char."""
    pixels = image.load()
    for at in range(x, image.size[0]):
        if any(pixels[at, top + y][3] for y in range(height)):
            die("%s: the row of chars at %d has pixels at %d,%d, past its last char; the row"
                " has more chars than the font has for it" % (shown(path), top, at, top))


def read_last_cell(path, image, top, height, x, turn, key, empty, gap):
    """The width and the glyph of the last cell of a row, which may be without paper. With
    paper, it is read as any cell is. With ink alone on transparent pixels, it is as wide as
    the chars usually are, at the place that the cell before it and the empty slots between
    the two leave it."""
    pixels = image.load()
    paper, ink = CELL_COLORS[turn]

    def clear(at):
        return not any(pixels[at, top + y][3] for y in range(height))

    start = x
    while start < image.size[0] and clear(start):
        start += 1
    end = start
    while end < image.size[0] and not clear(end):
        end += 1
    if any(pixels[at, top + y] == paper for at in range(start, end) for y in range(height)):
        end = start
        while end < image.size[0] and column_cell(pixels, end, top, height, path) == turn:
            end += 1
        row_ends(path, image, top, height, end)
        return end - start, [[int(pixels[at, top + y] == ink) for at in range(start, end)]
            for y in range(height)]
    left = x + empty * gap
    if start < end and not (gap and left <= start and end <= left + gap):
        die("%s: the cell of %s, which is without paper, has ink at the pixels %d..%d of the"
            " row at %d; without paper it is %d pixels wide, as the chars usually are, from"
            " the pixel %d on" % (shown(path), key_label(key), start, end - 1, top, gap, left))
    for at in range(start, end):
        for y in range(height):
            if pixels[at, top + y][3] and pixels[at, top + y] != ink:
                die("%s: the pixel at %d,%d is %s; the cell of %s, which is without paper,"
                    " has %s for ink, and is transparent otherwise" % (shown(path), at,
                    top + y, color_name(pixels[at, top + y]), key_label(key), color_name(ink)))
    row_ends(path, image, top, height, end)
    return gap, [[int(at < image.size[0] and pixels[at, top + y] == ink)
        for at in range(left, left + gap)] for y in range(height)]


def color_distance(color, paper):
    """How far a pixel stands out of the paper, 0..255: the largest difference in a color
    channel, as much of it as the pixel is opaque. On transparent paper, its opacity."""
    if not paper[3]:
        return color[3]
    most = max(abs(one - other) for one, other in zip(color[:3], paper[:3]))
    return (most * color[3] + 127) // 255


def read_png_grid(path, image, sheet, width, height):
    """The widths and glyphs of a PNG without the full coloring: only its size tells the
    chars apart, so all are equally wide. The first cell is blank, and its color is the
    paper; any other color is paper or ink by its distance from that one."""
    pixels = image.load()
    paper = pixels[0, 0]
    for y in range(height):
        for x in range(width):
            if pixels[x, y] != paper:
                die("%s: its first cell must be blank, for its color to tell the paper, but"
                    " the pixel at %d,%d is %s and the one at 0,0 is %s"
                    % (shown(path), x, y, color_name(pixels[x, y]), color_name(paper)))
    kinds = {}

    def inked(x, y):
        color = pixels[x, y]
        if color not in kinds:
            distance = color_distance(color, paper)
            if PAPER_DISTANCE < distance < INK_DISTANCE:
                die("%s: the pixel at %d,%d is %s, which is neither paper nor ink: the paper"
                    " is %s, as the first cell has it, and this color is %d away from it,"
                    " where up to %d is paper and %d or more is ink" % (shown(path), x, y,
                    color_name(color), color_name(paper), distance, PAPER_DISTANCE,
                    INK_DISTANCE))
            kinds[color] = int(distance >= INK_DISTANCE)
        return kinds[color]

    glyphs = []
    for r, row in enumerate(sheet):
        for c, key in enumerate(row):
            cell = [[inked(x, y) for x in range(c * width, (c + 1) * width)]
                for y in range(r * height, (r + 1) * height)]
            if key is not None:
                glyphs.append(cell)
            elif any(any(line) for line in cell):
                die("%s: there is ink in the cell at %d,%d, where the font has no char"
                    % (shown(path), c * width, r * height))
    for x in range(len(sheet[-1]) * width, image.size[0]):
        if any(inked(x, y) for y in range(image.size[1] - height, image.size[1])):
            die("%s: there is ink right of the last char" % shown(path))
    inks = sum(kinds.values())
    print("%s: the paper is %s, as the first cell has it%s; the ink is %s."
        % (shown(path), color_name(paper), ", and %d more color(s) close to it"
        % (len(kinds) - inks - 1) if len(kinds) - inks > 1 else "",
        "%d color(s) far from it" % inks if inks else "nowhere"))
    return [width] * len(glyphs), glyphs


def text_lines(sheet, cells, height, bare=None):
    lines, before = [], None
    for row in sheet:
        body = [CHAR_SEPARATOR.join("" if key is None else "".join(
            INK if bit else NO_PAPER if key == bare else PAPER
            for bit in cells[key][1][y]) for key in row) for y in range(height)]
        if before is not None:
            lines.append(ROW_SEPARATOR * max(before, len(body[0])))
        lines.extend(body)
        before = len(body[0])
    return lines


def read_text(path, sheet, _zero, height, plain, bare=None, gap=None):
    """The widths and glyphs a text file holds, in the order of the keys of the sheet. The
    lines of dashes between the rows of chars may be left out, and so may the separators of
    chars when all chars are equally wide. Without a height given, the chars are as high as
    the file makes them. bare is the key of the cell that may have spaces for its paper,
    the last of its row: where the spaces that end a line are lost, they are put back up to
    the gap, which is how wide the chars usually are."""
    with open(path, encoding="ascii", errors="replace") as handle:
        lines = handle.read().splitlines()
    dashes = [at for at, line in enumerate(lines) if line and not line.strip(ROW_SEPARATOR)]
    if dashes:
        tops = [0] + [at + 1 for at in dashes]
        if len(tops) != len(sheet):
            die("%s has %d row(s) of chars between its lines of dashes, not %d"
                % (shown(path), len(tops), len(sheet)))
        if height is None:
            height = dashes[0]
        for top, end in zip(tops, dashes + [len(lines)]):
            if end - top != height or not height:
                die("%s:%d: this row of chars has %d line(s), not %d"
                    % (shown(path), top + 1, end - top, height))
    else:
        if height is None:
            height = len(lines) // len(sheet)
        if not height or len(lines) != len(sheet) * height:
            die("%s has %d lines, which is not %d row(s) of chars %s" % (shown(path), len(lines),
                len(sheet), "%d pixels high" % height if height else "of one height"))
        tops = [r * height for r in range(len(sheet))]
    barred = any(CHAR_SEPARATOR in line for line in lines)
    width = None
    widths, glyphs, later = [], [], None
    for row, top in zip(sheet, tops):
        cells = [[] for _key in row]
        for y in range(height):
            where = "%s:%d" % (shown(path), top + y + 1)
            line = lines[top + y]
            if barred:
                parts = line.split(CHAR_SEPARATOR)
            else:
                if width is None:
                    width = plain_width(where, len(line), len(row), plain, "characters",
                        "the %s between chars" % CHAR_SEPARATOR)
                if row[-1] == bare:
                    line = line.ljust(len(row) * width)
                if len(line) != len(row) * width:
                    die("%s: is %d characters wide, but %d chars %d wide take %d"
                        % (where, len(line), len(row), width, len(row) * width))
                parts = [line[at:at + width] for at in range(0, len(line), width)]
            if len(parts) != len(row):
                die("%s: has %d chars, not %d" % (where, len(parts), len(row)))
            for slot, (key, part, cell) in enumerate(zip(row, parts, cells)):
                if key is None:
                    if part.strip(PAPER):
                        die("%s: has %r in slot %d of the row, where the font has no char"
                            % (where, part, row.index(None, len(cell)) + 1))
                    continue
                if key == bare and slot == len(row) - 1:
                    if part.strip(INK + PAPER + NO_PAPER):
                        die("%s: %s must be made of %s and %s, or of %s and spaces, not %r"
                            % (where, key_label(key), INK, PAPER, INK, part))
                    cell.append(part)
                    continue
                if part.strip(INK + PAPER):
                    die("%s: %s must be made of %s and %s, not %r"
                        % (where, key_label(key), INK, PAPER, part))
                if cell and len(part) != len(cell[0]):
                    die("%s: %s is %d wide here, but %d in the line above"
                        % (where, key_label(key), len(part), len(cell[0])))
                cell.append([int(mark == INK) for mark in part])
        for slot, (key, cell) in enumerate(zip(row, cells)):
            if key == bare and slot == len(row) - 1:
                later = (len(widths), cell, "%s:%d" % (shown(path), top + 1))
            if key is not None:
                widths.append(len(cell[0]))
                glyphs.append(cell)
    if later:
        at, parts, where = later
        usual = collections.Counter(width for index, width in enumerate(widths)
            if width and index != at).most_common(1)
        wide = max(len(part) for part in parts)
        if not any(PAPER in part for part in parts):
            wide = max(wide, gap or (usual[0][0] if usual else 0))
        elif len(set(len(part) for part in parts)) > 1:
            die("%s: %s is not equally wide in all its lines" % (where, key_label(bare)))
        widths[at] = wide
        glyphs[at] = [[int(mark == INK) for mark in part.ljust(wide)] for part in parts]
    for index, at in enumerate(dashes):
        longer = max(len(lines[at - 1]), len(lines[at + 1]))
        # Around the row of the cell without paper, the spaces that end a line may be lost.
        loose = bare is not None and bare in sheet[index] + sheet[index + 1]
        if len(lines[at]) != longer and not (loose and len(lines[at]) > longer):
            die("%s:%d: must be a line of %d dashes, as long as the longer of the rows of"
                " chars around it" % (shown(path), at + 1, longer))
    return widths, glyphs


def load_glyphs(directory, names, sheet, zero, height, plain, where, listed=None, bare=None,
        gap=None):
    """The widths and bitmaps of a font from its PNG, its text file, or both when they
    agree, in the order of the keys of the sheet."""
    found = []
    for name, reader in zip(names, (read_png, read_text)):
        if name is None:
            continue
        if not isinstance(name, str) or os.path.basename(name) != name:
            die("%s: %r must be a file name without a directory, or null" % (where, name))
        path = os.path.join(directory, name)
        if os.path.exists(path):
            found.append((path,) + reader(path, sheet, zero, height, plain, bare, gap))
    if not found:
        die("%s: none of its bitmap files is there: %s"
            % (where, ", ".join(name for name in names if name is not None) or "none named"))
    keys = sheet_keys(sheet)
    path, widths, glyphs = found[0]
    for other, other_widths, other_glyphs in found[1:]:
        for key, one, two in zip(keys, zip(widths, glyphs), zip(other_widths, other_glyphs)):
            if one != two:
                die("%s differs between %s and %s; delete the one that was not edited"
                    % (key_label(key), shown(path), shown(other)))
    without = set(key for key, width in zip(keys, widths) if not width)
    if without != zero:
        listed = listed or ranges_text
        die("%s.zero_width: lists %s, but the chars without pixels in %s are %s"
            % (where, listed(zero) or "none", shown(path), listed(without) or "none"))
    return widths, glyphs


# ----------------------------------------------------------------------------------------
# Unpacking: the file into the model that fon.json stores


def parse_names(data, at, limit):
    """A table of names with ordinals, ended by an empty name: the entries and its size."""
    entries, p = [], at
    while p < limit and data[p]:
        end = p + 1 + data[p]
        if end + 2 > limit:
            return None
        entries.append({"name": text_of(data[p + 1:end]),
            "ordinal": struct.unpack_from("<H", data, end)[0]})
        p = end + 2
    return (entries, p + 1 - at) if p < limit else None


def build_names(entries, where):
    out = bytearray()
    for index, entry in enumerate(entries):
        here = "%s[%d]" % (where, index)
        fields = Fields(entry, here)
        name = bytes_of(fields.take("name"), here + ".name")
        if not 0 < len(name) < 256:
            die("%s.name: must be 1..255 bytes" % here)
        out += bytes([len(name)]) + name + struct.pack("<H",
            as_int(fields.take("ordinal"), here + ".ordinal", 0xFFFF))
        fields.done()
    return bytes(out + b"\0")


def fontdir_view(font):
    """What a FONTDIR entry repeats of a font: the header part, device name and face name."""
    names = []
    for field_at in (101, 105):
        at = struct.unpack_from("<I", font, field_at)[0] if len(font) >= 109 else 0
        end = font.find(b"\0", at) if 0 < at < len(font) else -1
        names.append(bytes(font[at:end]) if end >= 0 else b"")
    return bytes(font[:FONTDIR_ENTRY_SIZE]), names[0], names[1]


def parse_font(data, unit, where, report):
    """A raster font resource as its fon.json object, with its widths and glyphs; None when
    it is not supported."""
    if len(data) < FNT_HEADER_SIZE:
        report.error("%s: %d bytes are too few for a font; kept as raw data"
            % (where, len(data)))
        return None
    spec = FNT_HEADER
    raw = raw_fields(spec, data, 0)
    if raw["dfType"] & 1:
        report.error("%s: is a vector font, which is not supported; kept as raw data" % where)
        return None
    if raw["dfVersion"] not in (0x200, 0x300):
        report.error("%s: dfVersion 0x%04X is not supported; kept as raw data"
            % (where, raw["dfVersion"]))
        return None
    table_at, entry = FNT_HEADER_SIZE, "<HH"
    if raw["dfVersion"] == 0x300:
        spec, table_at, entry = FNT_HEADER + FNT_HEADER_3, FNT_HEADER_3_SIZE, "<HI"
    first, last, height = raw["dfFirstChar"], raw["dfLastChar"], raw["dfPixHeight"]
    count = last - first + 2
    table_end = table_at + count * struct.calcsize(entry)
    if last < first or table_end > len(data) or not height:
        report.error("%s: chars %d..%d, %d pixels high, make no font that fits into %d bytes;"
            " kept as raw data" % (where, first, last, height, len(data)))
        return None
    raw = raw_fields(spec, data, 0)
    if raw.get("dfAspace") or raw.get("dfBspace") or raw.get("dfCspace"):
        report.warning("%s: dfAspace, dfBspace, dfCspace are %d, %d, %d; they are not applied"
            " to the bitmaps" % (where, raw["dfAspace"], raw["dfBspace"], raw["dfCspace"]))
    table = list(struct.iter_unpack(entry, data[table_at:table_end]))
    # A table of garbage can ask for bitmaps that no memory holds.
    pixels = sum(width for width, _offset in table) * height
    if pixels > MAX_FONT_PIXELS:
        report.error("%s: its character table makes %d pixels of bitmaps, which cannot be"
            " right; kept as raw data" % (where, pixels))
        return None

    # Packing lays a font out in this order, each part right after the one before; a font
    # laid out otherwise is read where its offsets point, and will not come back the same.
    expected, extent = table_end, table_end
    usual = raw["dfBitsOffset"] == table_end
    widths, glyphs, stray, cut = [], [], [], []
    for index, (width, offset) in enumerate(table):
        size = (width + 7) // 8 * height
        bits = bytes(data[offset:offset + size])
        if len(bits) < size:
            cut.append("the absolute space" if index == count - 1 else "char %d" % (first + index))
            bits += bytes(size - len(bits))
        usual = usual and offset == expected
        expected, extent = expected + size, max(extent, min(offset + size, len(data)))
        if index == count - 1:
            if any(bits):
                report.warning("%s: the absolute space, the bitmap after the last char, is"
                    " not blank; its ink is not kept, and packing makes it blank" % where)
            break
        widths.append(width)
        glyphs.append(glyph_rows(bits, width, height))
        if glyph_bytes(glyphs[-1], width, height) != bits:
            stray.append(str(first + index))
    if cut:
        report.error("%s: the font ends at %d, which cuts the bitmap of %s%s; the missing"
            " bytes are taken as zeros" % (where, len(data), cut[0],
            " and %d more" % (len(cut) - 1) if len(cut) > 1 else ""))
    if stray:
        report.warning("%s: ink right of the char's width, which the bitmap files cannot"
            " hold, in char %s" % (where, ", ".join(stray)))

    font = {"png": None, "txt": None, "header": read_fields(spec, data, 0),
        "zero_width": ranges_text(first + index for index, width in enumerate(widths)
            if not width),
        "absolute_space_width": table[-1][0]}
    for key, field in (("device_name", "dfDevice"), ("face_name", "dfFace")):
        font[key] = None
        at = raw[field]
        if at:
            end = data.find(b"\0", at)
            if at >= len(data) or end < 0:
                report.error("%s: %s points at %d, where no string is" % (where, field, at))
            else:
                font[key] = text_of(data[at:end])
                usual = usual and at == expected
                expected, extent = expected + end + 1 - at, max(extent, end + 1)
    if not usual:
        report.warning("%s: is not laid out as packing lays a font out, which is the bitmaps"
            " back to back in char order after the character table, then the absolute"
            " space, the device name and the face name" % where)
    tail = tail_of(data, extent, unit)
    if tail is not None:
        font["tail"] = tail
    return font, widths, glyphs


def parse_fontdir(data, unit, where, fonts, report):
    """A FONTDIR resource as its fon.json object; None when it cannot be made sense of."""
    if len(data) < 2:
        return None
    entries, p = [], 2
    for _index in range(struct.unpack_from("<H", data, 0)[0]):
        names_at = p + 2 + FONTDIR_ENTRY_SIZE
        device_end = data.find(b"\0", names_at)
        face_end = data.find(b"\0", device_end + 1)
        if names_at > len(data) or device_end < 0 or face_end < 0:
            return None
        ordinal = struct.unpack_from("<H", data, p)[0]
        view = (bytes(data[p + 2:names_at]), bytes(data[names_at:device_end]),
            bytes(data[device_end + 1:face_end]))
        entry = {"fontOrdinal": ordinal, "same_as_font": fonts.get(ordinal) == view}
        if not entry["same_as_font"]:
            report.warning("%s: the entry of font %d %s" % (where, ordinal,
                "differs from the font" if ordinal in fonts else "has no font"))
            entry["header"] = read_fields(FONTDIR_ENTRY, view[0], 0, computed=())
            entry["szDeviceName"] = text_of(view[1])
            entry["szFaceName"] = text_of(view[2])
        entries.append(entry)
        p = face_end + 1
    fontdir = {"entries": entries}
    tail = tail_of(data, p, unit)
    if tail is not None:
        fontdir["tail"] = tail
    return fontdir


def parse_version_node(data, at, limit, depth, strings):
    """One block of a VERSIONINFO tree and where it ends; None when it is not laid out as
    such blocks are: header, key, value, then child blocks, each aligned to 4 bytes."""
    if at + 4 > limit:
        return None
    block, value_size = struct.unpack_from("<HH", data, at)
    end = at + block
    key_end = data.find(b"\0", at + 4, end)
    if end > limit or key_end < 0:
        return None
    key = bytes(data[at + 4:key_end])
    node = {"szKey": text_of(key), COMPUTED_MARK + "cbBlock": block,
        COMPUTED_MARK + "cbValue": value_size}
    value_at = align(key_end + 1, 4)
    if value_at >= end:
        # Nothing but the key: whatever follows it inside the block is padding.
        if value_size:
            return None
        if end > key_end + 1:
            node["key_padding"] = hex_of(data[key_end + 1:end])
        return node, end
    if any(data[key_end + 1:value_at]):
        node["key_padding"] = hex_of(data[key_end + 1:value_at])
    value_end = value_at + value_size
    if value_end > end:
        return None
    value = bytes(data[value_at:value_end])
    if value and depth == 0 and value_size == FIXED_FILE_INFO_SIZE:
        node["fixed"] = read_fields(FIXED_FILE_INFO, value, 0)
    elif value and key == b"Translation" and value_size % 2 == 0:
        node["words"] = ["0x%04X" % word
            for word in struct.unpack("<%dH" % (value_size // 2), value)]
    elif value and strings:
        # A string value counts the NULs that end it; some have none, some have many.
        node["text"] = text_of(value.rstrip(b"\0"))
        node["nuls"] = len(value) - len(value.rstrip(b"\0"))
    elif value:
        node["bytes"] = hex_of(value)
    if value_end == end:
        return node, end

    child_at = align(value_end, 4)
    if any(data[value_end:child_at]):
        node["value_padding"] = hex_of(data[value_end:child_at])
    node["children"] = []
    while True:
        child = parse_version_node(data, child_at, end, depth + 1,
            strings or key == b"StringFileInfo")
        if child is None:
            return None
        node["children"].append(child[0])
        if child[1] == end:
            return node, end
        child_at = align(child[1], 4)
        if child_at >= end:
            return None
        if any(data[child[1]:child_at]):
            child[0]["padding"] = hex_of(data[child[1]:child_at])


def version_paddings(node, path=""):
    """The keys of the blocks followed by padding that is not zero."""
    path = path + "/" + node["szKey"] if path else node["szKey"]
    out = [path] if "padding" in node or "value_padding" in node else []
    for child in node.get("children", ()):
        out.extend(version_paddings(child, path))
    return out


def parse_version(data, unit, where, report):
    """A VERSIONINFO resource as its fon.json object; None when packing it would not give
    the same bytes back."""
    parsed = parse_version_node(data, 0, len(data), 0, False)
    if parsed is None:
        return None
    node, end = parsed
    version = {"block": node}
    tail = tail_of(data, end, unit)
    if tail is not None:
        version["tail"] = tail
    problems = []
    built = build_version(version, where, problems)
    if built + bytes(align(len(built), unit) - len(built)) != bytes(data) or problems:
        return None
    for path in version_paddings(node):
        report.warning("%s: the padding after %s is not zero, as a value longer than its"
            " cbValue leaves it" % (where, path))
    return version


def parse_fon(data, report):
    """The whole file as the model fon.json stores, and for each raster font its object,
    widths and glyphs."""
    if len(data) < MZ_SIZE or data[:2] != b"MZ":
        die("not a .fon file: it has no MZ header")
    ne_at = raw_fields(MZ_HEADER, data, 0)["e_lfanew"]
    if ne_at < MZ_SIZE or data[ne_at:ne_at + 2] != b"NE" or ne_at + NE_SIZE > len(data):
        if data[ne_at:ne_at + 4] == b"PE\0\0":
            die("a 32-bit (PE) file; only 16-bit (NE) font files are supported")
        die("not a .fon file: it has no NE header")
    ne = raw_fields(NE_HEADER, data, ne_at)
    if ne["ne_cseg"] or ne["ne_cmod"]:
        report.warning("the file has %d segment(s) and %d imported module(s), which a font"
            " file should not; they are not unpacked" % (ne["ne_cseg"], ne["ne_cmod"]))
    layout = Layout("the file", report.error)
    layout.place(0, data[:MZ_SIZE], "the MZ header")
    layout.place(MZ_SIZE, data[MZ_SIZE:ne_at], "the DOS stub")
    layout.place(ne_at, data[ne_at:ne_at + NE_SIZE], "the NE header")

    # The resource table: the shift, then per type its resources, then the names.
    table_at = ne_at + ne["ne_rsrctab"]
    table = {"rscAlignShift": 0, "types": [], "names": []}
    resources = []
    p = table_at + 2
    if p <= len(data):
        table["rscAlignShift"] = struct.unpack_from("<H", data, table_at)[0]
    shift = min(table["rscAlignShift"], 15)
    while p + 2 <= len(data) and data[p:p + 2] != b"\0\0":
        if p + 8 > len(data):
            break
        type_id, count, reserved = struct.unpack_from("<HHI", data, p)
        if p + 8 + count * RESOURCE_SIZE > len(data):
            break
        listed = []
        for at in range(p + 8, p + 8 + count * RESOURCE_SIZE, RESOURCE_SIZE):
            listed.append(read_fields(RESOURCE, data, at))
            resources.append((type_id, listed[-1], raw_fields(RESOURCE, data, at)))
        table["types"].append({"rtTypeID": "0x%04X" % type_id, "rtReserved": reserved,
            "resources": listed})
        p += 8 + count * RESOURCE_SIZE
    if data[p:p + 2] != b"\0\0":
        report.error("the resource table at %d is cut by the end of the file" % table_at)
    p = min(p + 2, len(data))
    layout.place(table_at, data[table_at:p], "the resource table")
    names_end = max(ne_at + ne["ne_restab"], p)
    while p < min(names_end, len(data)) and data[p] and p + 1 + data[p] <= names_end:
        name = text_of(data[p + 1:p + 1 + data[p]])
        table["names"].append({"offset": p - table_at, "name": name})
        layout.place(p, data[p:p + 1 + data[p]], "the resource name %r" % name)
        p += 1 + data[p]

    model = {"encoding": encoding, COMPUTED_MARK + "size": len(data),
        "mz_header": read_fields(MZ_HEADER, data, 0), "dos_stub": hex_of(data[MZ_SIZE:ne_at]),
        "ne_header": read_fields(NE_HEADER, data, ne_at), "resource_table": table}
    for key, at, limit, what in (
            ("resident_names", ne_at + ne["ne_restab"], len(data), "the resident name table"),
            ("nonresident_names", ne["ne_nrestab"], ne["ne_nrestab"] + ne["ne_cbnrestab"],
                "the non-resident name table")):
        parsed = parse_names(data, at, min(limit, len(data)))
        if parsed is None:
            report.error("%s at %d is not a list of names ended by an empty one; it is not"
                " unpacked" % (what, at))
            parsed = ([], 0)
        model[key] = parsed[0]
        layout.place(at, data[at:at + parsed[1]], what)
    entries_at = ne_at + ne["ne_enttab"]
    model["entry_table"] = hex_of(data[entries_at:entries_at + ne["ne_cbenttab"]])
    layout.place(entries_at, data[entries_at:entries_at + ne["ne_cbenttab"]], "the entry table")

    # Fonts go first: a FONTDIR entry is shown as a reference to the font it repeats.
    unit = 1 << shift
    bodies = []
    for type_id, resource, raw in resources:
        at, size = raw["rnOffset"] << shift, raw["rnLength"] << shift
        where = "resource %s of type 0x%04X" % (resource["rnID"], type_id)
        if at + size > len(data):
            report.error("%s at %d..%d is cut by the end of the file, at %d"
                % (where, at, at + size, len(data)))
        layout.place(at, data[at:at + size], where)
        bodies.append(data[at:at + size])
    views, fonts = {}, []
    for (type_id, resource, raw), body in zip(resources, bodies):
        if type_id == RT_FONT:
            views[raw["rnID"] & 0x7FFF] = fontdir_view(body)
            where = "font %d" % (raw["rnID"] & 0x7FFF)
            parsed = parse_font(body, unit, where, report)
            if parsed is not None:
                resource["font"] = parsed[0]
                fonts.append(parsed + (where,))
    for (type_id, resource, raw), body in zip(resources, bodies):
        where = "resource %s of type 0x%04X" % (resource["rnID"], type_id)
        if type_id == RT_FONTDIR:
            resource["fontdir"] = parse_fontdir(body, unit, "FONTDIR", views, report)
        elif type_id == RT_VERSION:
            resource["version"] = parse_version(body, unit, "VERSIONINFO", report)
        for kind, _type_id in RESOURCE_KINDS:
            if kind in resource and resource[kind] is None:
                report.error("%s is not laid out as expected; kept as raw data" % where)
                del resource[kind]
        if not any(kind in resource for kind, _type_id in RESOURCE_KINDS):
            resource["data"] = hex_of(body)

    # Packing puts the resources back to back after everything else, so only the stray
    # bytes ahead of them can be put back where they were.
    layout.finish(len(data))
    ahead = min([raw["rnOffset"] << shift for _type_id, _resource, raw in resources]
        + [len(data)])
    gaps = layout.gaps(data)
    lost = [gap for gap in gaps if gap["offset"] >= ahead]
    if lost:
        report.warning("the bytes at %d and %d more place(s) belong to no structure and are"
            " not zero; they are not kept" % (lost[0]["offset"], len(lost) - 1))
    if len(gaps) > len(lost):
        model["gaps"] = [gap for gap in gaps if gap["offset"] < ahead]
    return model, fonts


# ----------------------------------------------------------------------------------------
# Packing: the model and the bitmap files into the file


def build_version_node(value, at, where, problems):
    """A VERSIONINFO block placed at the offset, and the padding stated to follow it."""
    node = Fields(value, where)
    key = bytes_of(node.take("szKey"), where + ".szKey")
    padding = node.take("padding", None)
    values = []
    if "fixed" in node.left:
        values.append(pack_fields(FIXED_FILE_INFO, node.take("fixed"), where + ".fixed")[0])
    if "words" in node.left:
        values.append(b"".join(struct.pack("<H", as_int(word, where + ".words", 0xFFFF))
            for word in node.take_list("words")))
    if "text" in node.left:
        values.append(bytes_of(node.take("text"), where + ".text")
            + bytes(as_int(node.take("nuls"), where + ".nuls", 0xFFFF)))
    if "bytes" in node.left:
        values.append(unhex(node.take("bytes"), where + ".bytes"))
    if len(values) > 1:
        die("%s: has more than one of fixed, words, text, bytes" % where)
    data = values[0] if values else b""
    children = node.take_list("children", None) if "children" in node.left else None
    out = bytearray(4) + key + b"\0"

    def pad(name):
        """Padding to the next multiple of 4: zeros, or the bytes stated under the name."""
        size = align(at + len(out), 4) - at - len(out)
        stated = node.take(name, None)
        if stated is None:
            return bytes(size)
        stated = unhex(stated, "%s.%s" % (where, name))
        if len(stated) != size:
            problems.append("%s.%s: is %d bytes, but %d bytes pad the block there"
                % (where, name, len(stated), size))
        return stated

    if data or children is not None:
        out += pad("key_padding")
    else:
        out += unhex(node.take("key_padding", ""), where + ".key_padding")
    out += data
    if children is not None:
        if not children:
            die("%s.children: must not be empty" % where)
        out += pad("value_padding")
        for index, child in enumerate(children):
            here = "%s.children[%d]" % (where, index)
            body, after = build_version_node(child, at + len(out), here, problems)
            out += body
            gap = align(at + len(out), 4) - at - len(out)
            if index == len(children) - 1:
                gap = 0
            if after is not None and len(unhex(after, here + ".padding")) != gap:
                problems.append("%s.padding: is %d bytes, but %d bytes pad the block there"
                    % (here, len(unhex(after, here + ".padding")), gap))
            out += unhex(after, here + ".padding") if after is not None else bytes(gap)
    node.done()
    if len(out) > 0xFFFF or len(data) > 0xFFFF:
        die("%s: takes %d bytes, more than a block holds" % (where, len(out)))
    struct.pack_into("<HH", out, 0, len(out), len(data))
    return bytes(out), padding


def build_version(value, where, problems):
    fields = Fields(value, where)
    body, after = build_version_node(fields.take("block"), 0, where + ".block", problems)
    if after is not None:
        die("%s.block.padding: the outermost block has none" % where)
    body += take_tail(fields)
    fields.done()
    return body


def default_place(header):
    """The char 127 when it is the default char of a font, as the fonts shipped with Windows
    have it: the char whose cell may be without paper. None for any other font."""
    return NEW_DEFAULT_CHAR if header["dfFirstChar"] + header["dfDefaultChar"] == (
        NEW_DEFAULT_CHAR) <= header["dfLastChar"] else None


def build_font(value, where, load, problems, warn):
    """A font resource from its fon.json object and its bitmap files."""
    fields = Fields(value, where)
    names = (fields.take("png"), fields.take("txt"))
    header_value = fields.take("header")
    version = as_int(Fields(header_value, where + ".header").take("dfVersion"),
        where + ".header.dfVersion", 0xFFFF)
    if version not in (0x200, 0x300):
        die("%s.header.dfVersion: must be 0x0200 or 0x0300" % where)
    spec, table_at, entry = FNT_HEADER, FNT_HEADER_SIZE, "<HH"
    if version == 0x300:
        spec, table_at, entry = FNT_HEADER + FNT_HEADER_3, FNT_HEADER_3_SIZE, "<HI"
    header = pack_fields(spec, header_value, where + ".header", dict.fromkeys(COMPUTED, 0))[1]
    if header["dfType"] & 1:
        die("%s.header.dfType: says a vector font, which is not supported" % where)
    first, last, height = header["dfFirstChar"], header["dfLastChar"], header["dfPixHeight"]
    if last < first or not height:
        die("%s.header: dfLastChar must not be less than dfFirstChar, and dfPixHeight must"
            " not be 0" % where)
    codes = list(range(first, last + 1))
    sheet = fon_sheet(first, last)
    zero = parse_ranges(fields.take("zero_width"), where + ".zero_width", first, last)
    space_width = as_int(fields.take("absolute_space_width"),
        where + ".absolute_space_width", 0xFFFF)
    strings = [None if name is None else bytes_of(name, "%s.%s" % (where, key)) + b"\0"
        for key, name in (("device_name", fields.take("device_name")),
            ("face_name", fields.take("face_name")))]
    tail = take_tail(fields)
    fields.done()

    # The layout: header, character table, bitmaps in char order, absolute space, names.
    # A font of fixed pitch may have bitmaps that do not mark where a char ends.
    plain = False if header["dfPitchAndFamily"] & 1 else header["dfPixWidth"] or None
    widths, glyphs = load(names, sheet, zero, height, plain, where, None,
        default_place(header), header["dfPixWidth"] or header["dfAvgWidth"])
    bitmaps = [glyph_bytes(glyph, width, height) for glyph, width in zip(glyphs, widths)]
    bitmaps.append(bytes((space_width + 7) // 8 * height))
    at = table_at + len(bitmaps) * struct.calcsize(entry)
    computed = {"dfBitsOffset": at, "dfWidthBytes": len(b"".join(bitmaps)) // height}
    table = bytearray()
    for width, bitmap in zip(widths + [space_width], bitmaps):
        if at >= 1 << (8 * struct.calcsize(entry[2])):
            die("%s: the bitmaps do not fit into a font of version 0x%04X, which holds"
                " offsets up to %d" % (where, version, (1 << (8 * struct.calcsize(entry[2]))) - 1))
        table += struct.pack(entry, width, at)
        at += len(bitmap)
    for field, string in zip(("dfDevice", "dfFace"), strings):
        computed[field] = at if string is not None else 0
        at += len(string or b"")
    computed["dfSize"] = at
    header_bytes = pack_fields(spec, header_value, where + ".header", computed)[0]

    def check(good, message):
        if not good:
            problems.append("%s: %s" % (where, message))

    # Fonts shipped with Windows break these two, so they are said and not refused.
    odd = [str(code) for code, width in zip(codes, widths) if width != header["dfPixWidth"]]
    if header["dfPixWidth"] and odd:
        warn("%s: dfPixWidth is %d, but %s not that wide" % (where, header["dfPixWidth"],
            "char %s is" % odd[0] if len(odd) == 1 else "chars %s are" % ", ".join(odd)))
    elif not header["dfPitchAndFamily"] & 1 and len(set(widths)) > 1:
        warn("%s: bit 0 of dfPitchAndFamily is clear, which says fixed pitch, but the widths"
            " differ" % where)
    check(header["dfMaxWidth"] >= max(widths), "dfMaxWidth is %d, but the widest char is %d"
        % (header["dfMaxWidth"], max(widths)))
    check(header["dfAscent"] <= height,
        "dfAscent is %d, above dfPixHeight, which is %d" % (header["dfAscent"], height))
    for field in ("dfDefaultChar", "dfBreakChar"):
        check(header[field] <= last - first, "%s is %d, which counts from dfFirstChar and is"
            " past dfLastChar" % (field, header[field]))
    return header_bytes + bytes(table) + b"".join(bitmaps) + b"".join(
        string or b"" for string in strings) + tail


def build_fontdir(value, where, views, problems):
    fields = Fields(value, where)
    entries = fields.take_list("entries")
    out = bytearray(struct.pack("<H", as_int(len(entries), where + ".entries", 0xFFFF)))
    listed = set()
    for index, entry in enumerate(entries):
        here = "%s.entries[%d]" % (where, index)
        one = Fields(entry, here)
        ordinal = as_int(one.take("fontOrdinal"), here + ".fontOrdinal", 0xFFFF)
        listed.add(ordinal)
        view = views.get(ordinal)
        if view is None:
            problems.append("%s: there is no font %d" % (here, ordinal))
        if one.take("same_as_font") is not True:
            stated = (pack_fields(FONTDIR_ENTRY, one.take("header"), here + ".header")[0],
                bytes_of(one.take("szDeviceName"), here + ".szDeviceName"),
                bytes_of(one.take("szFaceName"), here + ".szFaceName"))
            if view is not None and stated != view:
                problems.append("%s: differs from font %d, which it must repeat"
                    % (here, ordinal))
            view = stated
        one.done()
        if view is not None:
            out += struct.pack("<H", ordinal) + view[0] + view[1] + b"\0" + view[2] + b"\0"
    for ordinal in sorted(set(views) - listed):
        problems.append("%s: has no entry for font %d" % (where, ordinal))
    out += take_tail(fields)
    fields.done()
    return bytes(out)


def build_fon(model, load, warn):
    """The bytes of the file. Whatever in the model or the bitmaps does not add up is
    collected, and reported together in one Failure."""
    problems = []
    top = Fields(model, JSON_NAME)
    top.take("encoding")
    stub = unhex(top.take("dos_stub"), "dos_stub")
    ne_at = MZ_SIZE + len(stub)
    mz_bytes, mz = pack_fields(MZ_HEADER, top.take("mz_header"), "mz_header",
        {"e_lfanew": ne_at})
    entries = unhex(top.take("entry_table"), "entry_table")
    names = build_names(top.take_list("nonresident_names"), "nonresident_names")
    ne_bytes, ne = pack_fields(NE_HEADER, top.take("ne_header"), "ne_header",
        {"ne_cbenttab": len(entries), "ne_cbnrestab": len(names)})
    layout = Layout("the file", problems.append)
    layout.place(0, mz_bytes, "the MZ header")
    layout.place(MZ_SIZE, stub, "the DOS stub")
    layout.place(ne_at, ne_bytes, "the NE header")
    layout.place(ne_at + ne["ne_restab"],
        build_names(top.take_list("resident_names"), "resident_names"),
        "the resident name table")
    layout.place(ne["ne_nrestab"], names, "the non-resident name table")
    layout.place(ne_at + ne["ne_enttab"], entries, "the entry table")

    def check(good, message):
        if not good:
            problems.append(message)

    check(mz["e_magic"] == 0x5A4D, "mz_header.e_magic is not 0x5A4D, \"MZ\"")
    check(ne["ne_magic"] == 0x454E, "ne_header.ne_magic is not 0x454E, \"NE\"")

    # The resource table is read first and written last: it holds where the resources are,
    # which is known only once they are built.
    table = Fields(top.take("resource_table"), "resource_table")
    table_at = ne_at + ne["ne_rsrctab"]
    shift = as_int(table.take("rscAlignShift"), "resource_table.rscAlignShift", 15)
    types, resources = [], []
    for t, one in enumerate(table.take_list("types")):
        here = "resource_table.types[%d]" % t
        fields = Fields(one, here)
        type_id = as_int(fields.take("rtTypeID"), here + ".rtTypeID", 0xFFFF)
        if not type_id:
            die("%s.rtTypeID: 0 ends the table, and cannot be a type" % here)
        listed = fields.take_list("resources")
        types.append((len(listed), struct.pack("<HHI", type_id,
            as_int(len(listed), here, 0xFFFF),
            as_int(fields.take("rtReserved"), here + ".rtReserved", 0xFFFFFFFF))))
        fields.done()
        for r, resource in enumerate(listed):
            where = "%s.resources[%d]" % (here, r)
            if not isinstance(resource, dict):
                die("%s: must be an object" % where)
            content = [(key, resource[key]) for key in ("fontdir", "font", "version", "data")
                if key in resource]
            if len(content) != 1:
                die("%s: must have one of fontdir, font, version, data" % where)
            check(dict(RESOURCE_KINDS).get(content[0][0], type_id) == type_id,
                "%s: a %s is in a resource of type 0x%04X" % (where, content[0][0], type_id))
            entry = dict((key, item) for key, item in resource.items() if key != content[0][0])
            rn_id = as_int(entry.get("rnID", 0), where + ".rnID", 0xFFFF)
            resources.append([type_id, rn_id, entry, content[0], where, None])
    for index, name in enumerate(table.take_list("names")):
        here = "resource_table.names[%d]" % index
        fields = Fields(name, here)
        text = bytes_of(fields.take("name"), here + ".name")
        if len(text) > 255:
            die("%s.name: is longer than 255 bytes" % here)
        layout.place(table_at + as_int(fields.take("offset"), here + ".offset", 0xFFFF),
            bytes([len(text)]) + text, "the resource name %r" % text_of(text))
        fields.done()
    table.done()
    for index, gap in enumerate(top.take_list("gaps", [])):
        here = "gaps[%d]" % index
        fields = Fields(gap, here)
        layout.place(as_int(fields.take("offset"), here + ".offset", 0xFFFFFFFF),
            unhex(fields.take("data"), here + ".data"), "a gap")
        fields.done()
    top.done()

    # Fonts go first, since a FONTDIR entry may refer to the font it repeats.
    unit = 1 << shift
    views = {}
    for resource in resources:
        type_id, rn_id, _entry, (kind, content), where, _body = resource
        here = "%s.%s" % (where, kind)
        if kind == "data":
            resource[5] = unhex(content, here)
        elif kind == "font":
            resource[5] = build_font(content, here, load, problems, warn)
        elif kind == "version":
            resource[5] = build_version(content, here, problems)
        if type_id == RT_FONT:
            check(rn_id & 0x7FFF not in views, "%s: another font has the same rnID" % where)
            views[rn_id & 0x7FFF] = fontdir_view(resource[5])
    table_bytes = bytearray(struct.pack("<H", shift))
    table_size = 4 + 8 * len(types) + RESOURCE_SIZE * len(resources)
    at = align(max(layout.extent(), table_at + table_size), unit)
    listed = iter(resources)
    for count, head in types:
        table_bytes += head
        for _index in range(count):
            _type_id, _rn_id, entry, (kind, content), where, body = next(listed)
            if kind == "fontdir":
                body = build_fontdir(content, "%s.%s" % (where, kind), views, problems)
            body += bytes(align(len(body), unit) - len(body))
            table_bytes += pack_fields(RESOURCE, entry, where,
                {"rnOffset": at >> shift, "rnLength": len(body) >> shift})[0]
            layout.place(at, body, where)
            at += len(body)
    layout.place(table_at, table_bytes + b"\0\0", "the resource table")
    image = layout.finish(max(at, layout.extent()))
    if problems:
        raise Failure("%s and the bitmap files do not add up:" % JSON_NAME, problems=problems)
    return bytes(image)


def build_from(directory, warn, name=None):
    """The .fon file that the files in the directory describe. Its texts are in the encoding
    named, or else in the one that fon.json records."""
    path = os.path.join(directory, JSON_NAME)
    try:
        with open(path, encoding="utf-8") as handle:
            model = json.load(handle)
    except ValueError as error:
        die("%s is not valid JSON: %s" % (shown(path), error))
    if not isinstance(model, dict) or "encoding" not in model:
        die("%s: encoding is missing" % JSON_NAME)
    set_encoding(name or model["encoding"])
    return build_fon(model, functools.partial(load_glyphs, directory), warn)


# ----------------------------------------------------------------------------------------
# The verbs


def json_text(value, indent=0, room=LINE_WIDTH):
    """JSON in two-space indents, with whatever fits into the room left kept on one line."""
    flat = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, (dict, list)) or not value or len(flat) <= room:
        return flat
    inner = indent + 2
    if isinstance(value, dict):
        items = ["%s: %s" % (json.dumps(key), json_text(item, inner,
            LINE_WIDTH - inner - len(json.dumps(key)) - 3)) for key, item in value.items()]
    else:
        items = [json_text(item, inner, LINE_WIDTH - inner - 1) for item in value]
    return "%s\n%s\n%s%s" % ("{" if isinstance(value, dict) else "[",
        ",\n".join(" " * inner + item for item in items), " " * indent,
        "}" if isinstance(value, dict) else "]")


def computed_differences(one, other, path=""):
    """The computed fields that differ between two models, as (path, value, other value)."""
    out = []
    if isinstance(one, dict) and isinstance(other, dict):
        for key, item in one.items():
            if key not in other:
                continue
            here = "%s.%s" % (path, key) if path else key
            if key.startswith(COMPUTED_MARK) and item != other[key]:
                out.append((here, item, other[key]))
            out.extend(computed_differences(item, other[key], here))
    elif isinstance(one, list) and isinstance(other, list):
        for index, (item, twin) in enumerate(zip(one, other)):
            out.extend(computed_differences(item, twin, "%s[%d]" % (path, index)))
    return out


def file_name(text):
    """A name any file system takes: no control chars, and none of what Windows forbids."""
    safe = "".join(char if char.isprintable() and char not in '\\/:*?"<>|' else "_"
        for char in text).strip(" .")
    return safe or "font"


def size_suffix(header, widths):
    """The size of a font as its name has it: " 6x8px", or " 13px" when the chars differ in
    width and the font states no width for them."""
    present = set(width for width in widths if width)
    width = present.pop() if len(present) == 1 else header["dfPixWidth"]
    return " %s%dpx" % ("%dx" % width if width else "", header["dfPixHeight"])


def split_name(name):
    """A font name as its stem, the width and the height that it tells, each None when it
    does not tell one, and whether it says bold and italic."""
    bold, italic = NAME_STYLE.search(name).groups()
    stem = name[:len(name) - len(bold or "") - len(italic or "")]
    size = NAME_SIZE.search(stem)
    if not size:
        return stem, None, None, bool(bold), bool(italic)
    return (stem[:size.start()], int(size.group(1)) if size.group(1) else None,
        int(size.group(2)), bool(bold), bool(italic))


def full_name(family, style):
    return family if style == STYLES[(False, False)] else "%s %s" % (family, style)


def font_names(fonts):
    """The family and the style of each font of a file. The family is the face name with
    the size after it, unless it ends with the size already. Fonts that would share both
    and differ in charset get the charset into the family."""
    out = []
    for font, widths, _glyphs, _where in fonts:
        header = font["header"]
        family = font["face_name"]
        if (family or "hex:").startswith("hex:"):
            family = "font"
        if family.lower().endswith(FON_SUFFIX.lower()):
            family = family[:-len(FON_SUFFIX)]
        size = size_suffix(header, widths)
        if not family.lower().endswith(size.lower()):
            family = (family + size).strip()
        out.append([family, STYLES[(header["dfWeight"] > 500, bool(header["dfItalic"]))]])
    charsets = [font["header"]["dfCharSet"] for font, _widths, _glyphs, _where in fonts]
    keys = [(family.lower(), style) for family, style in out]
    for at, charset in enumerate(charsets):
        if len(set(other for other, key in zip(charsets, keys) if key == keys[at])) > 1:
            out[at][0] += " " + (CHARSET_ENCODINGS.get(charset) or CHARSET_NAMES.get(charset)
                or "charset %d" % charset)
    return [tuple(names) for names in out]


def name_fonts(fonts):
    """Give each font its file names: the name of the font, and a number after it when
    another font of the file has that name as well."""
    taken = set()
    for (font, widths, _glyphs, _where), names in zip(fonts, font_names(fonts)):
        base = file_name(full_name(*names))
        name, serial = base, 1
        while name.lower() in taken:
            serial += 1
            name = "%s (%d)" % (base, serial)
        taken.add(name.lower())
        font["txt"] = name + ".txt"
        if any(widths):
            font["png"] = name + ".png"


def check_backup(path):
    """Refuse to go on when the target exists and its backup name is taken as well."""
    if os.path.lexists(path) and os.path.lexists(path + BACKUP_SUFFIX):
        show = shown_directory if os.path.isdir(path) else shown
        die("%s exists, and so does %s; move one of them away"
            % (show(path), show(path + BACKUP_SUFFIX)))


def back_up(path):
    """Move an existing file or directory out of the way, to its backup name."""
    check_backup(path)
    if os.path.lexists(path):
        show = shown_directory if os.path.isdir(path) else shown
        os.rename(path, path + BACKUP_SUFFIX)
        print("Renamed the existing %s to %s" % (show(path), show(path + BACKUP_SUFFIX)))


def write_files(directory, data, report):
    """Write what a .fon file holds into the directory: the model and the fonts written."""
    model, fonts = parse_fon(data, report)
    name_fonts(fonts)
    back_up(directory)
    os.mkdir(directory)
    for font, widths, glyphs, _where in fonts:
        header = font["header"]
        height, first = header["dfPixHeight"], header["dfFirstChar"]
        sheet = fon_sheet(first, first + len(glyphs) - 1)
        cells = dict(zip(range(first, first + len(glyphs)), zip(widths, glyphs)))
        bare = bare_cell(cells, default_place(header),
            header["dfPixWidth"] or header["dfAvgWidth"])
        if font["png"]:
            gap = header["dfPixWidth"] or header["dfAvgWidth"] or max(widths)
            chars = CHARSET_ENCODINGS.get(header["dfCharSet"], encoding)
            path = os.path.join(directory, font["png"])
            write_png(path, sheet, cells, height, gap, bare)
            write_svg(os.path.splitext(path)[0] + ".svg", sheet, cells, height, gap,
                lambda code, chars=chars: (bytes([code]).decode(chars, "ignore") or None,
                "%02X" % code), bare)
        with open(os.path.join(directory, font["txt"]), "w", encoding="ascii",
                newline="\n") as handle:
            handle.write("\n".join(text_lines(sheet, cells, height, bare)) + "\n")
    with open(os.path.join(directory, JSON_NAME), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(model) + "\n")
    return model, fonts


def unpack(path, name):
    set_encoding(name or DEFAULT_ENCODING)
    directory = path + FILES_SUFFIX
    check_backup(directory)
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] in SFNT_MAGICS:
        return unpack_ttf(path, data, encoding if name else None)
    report = Report()
    model, fonts = write_files(directory, data, report)
    print("Unpacked %s into %s: %d font(s)."
        % (shown(path), shown_directory(directory), len(fonts)))

    # The files just written are packed again, in memory: that finds what packing refuses,
    # which computed fields the file had otherwise, and whether the original comes back.
    exact = False
    try:
        packed = build_from(directory, report.warning)
        exact = packed == data
        if not exact:
            for where, was, now in computed_differences(model,
                    parse_fon(packed, Report(quiet=True))[0]):
                report.warning("%s is %s in the file, but packing makes it %s"
                    % (where, json.dumps(was), json.dumps(now)))
            report.warning("packing these files gives a file that differs from the original,"
                " first at offset %d" % next((at for at, (one, other)
                in enumerate(zip(data, packed)) if one != other), min(len(data), len(packed))))
    except Failure as failure:
        report.error("packing these files will be refused. %s" % failure)
        for problem in failure.problems:
            note("  " + problem)
    if report.errors or report.warnings:
        print("%d error(s), %d warning(s)." % (report.errors, report.warnings))
    if exact:
        print("Packing these files gives the original file back, byte for byte.")
    return 1 if report.errors else 0


def is_bitmaps(target):
    """Whether the argument is an image or a text file, which hold the bitmaps alone."""
    return os.path.splitext(target)[1].lower() in IMAGE_EXTENSIONS + (".txt",) and (
        not os.path.isdir(target))


def fon_path_of(directory):
    """The font file that a directory of unpacked files stands for."""
    if not directory.lower().endswith(FILES_SUFFIX) or len(os.path.basename(directory)) <= len(
            FILES_SUFFIX):
        die("%s: the directory must be named as the font file plus %s"
            % (shown_directory(directory), FILES_SUFFIX))
    return directory[:-len(FILES_SUFFIX)]


def pack(target, name, rows):
    if is_bitmaps(target):
        return pack_bitmaps(target, name, rows)
    if rows is not None:
        die(ROWS_MISPLACED)
    directory = target.rstrip("/\\")
    if not os.path.isdir(directory):
        die("%s is neither a directory that unpack made, nor a .png, a .psd or a .txt"
            % shown(directory))
    path = fon_path_of(directory)
    check_backup(path)
    if os.path.isfile(os.path.join(directory, TTF_JSON)):
        data = build_ttf_from(directory)
    else:
        data = build_from(directory, lambda message: note("Warning: " + message), name)
    back_up(path)
    with open(path, "wb") as handle:
        handle.write(data)
    print("Packed %s into %s." % (shown_directory(directory), shown(path)))
    return 0


def letter_rows(where, codes, glyphs, height):
    """The ascent and the internal leading, measured on the letters that start on one row
    and end on one row in any design: the ascent is where they end, and the leading is the
    rows above them. A font that stops short of the letters takes its whole height."""
    spans = []
    for letter in NEW_LETTERS:
        if ord(letter) not in codes:
            return height, 0
        inked = [y for y, row in enumerate(glyphs[codes.index(ord(letter))]) if any(row)]
        if not inked:
            die("%s: the letter %s is blank, and the letters %s are what the ascent is"
                " measured on" % (where, letter, NEW_LETTERS))
        spans.append((inked[0], inked[-1]))
    if len(set(spans)) != 1:
        die("%s: the letters %s must all start on one row and end on one row, for the ascent"
            " to be measured on them, but %s" % (where, NEW_LETTERS, ", ".join(
            "%s takes the rows %d..%d" % (letter, top + 1, bottom + 1)
            for letter, (top, bottom) in zip(NEW_LETTERS, spans))))
    return spans[0][1] + 1, spans[0][0]


def new_model(face, file, width, height, codes, ascent, leading, bold, italic):
    """What fon.json would hold for a file of one fixed-pitch font, with every field that
    the bitmaps do not give set as the fonts shipped with Windows have it. The face is the
    family of the font, which its face name marks as that of a raster font."""
    points = max(1, round((height - leading) * 72 / NEW_DPI))
    module = "".join(char for char in face.upper() if char.isascii() and char.isalnum())
    header = dict(NEW_FONT, dfPoints=points, dfVertRes=NEW_DPI, dfHorizRes=NEW_DPI,
        dfAscent=ascent, dfInternalLeading=leading, dfPixWidth=width, dfPixHeight=height,
        dfAvgWidth=width, dfMaxWidth=width, dfFirstChar=codes[0], dfLastChar=codes[-1],
        dfDefaultChar=NEW_DEFAULT_CHAR - codes[0] if codes[-1] >= NEW_DEFAULT_CHAR else 0,
        dfCharSet=NEW_CHARSETS.get(encoding, 1), dfWeight=700 if bold else 400,
        dfItalic=int(italic))
    strings = (("CompanyName", ""), ("FileDescription", face + " font"),
        ("FileVersion", "1.0"), ("InternalName", face), ("LegalCopyright", ""),
        ("OriginalFilename", file), ("ProductName", face), ("ProductVersion", "1.0"))
    version = {"szKey": "VS_VERSION_INFO", "fixed": NEW_VERSION, "children": [
        {"szKey": "StringFileInfo", "children": [{"szKey": "040904E4", "children": [
            {"szKey": key, "text": text, "nuls": 1} for key, text in strings]}]},
        {"szKey": "VarFileInfo", "children": [
            {"szKey": "Translation", "words": ["0x0409", "0x04E4"]}]}]}

    # The tables follow the NE header back to back: resources, their one name, the
    # module name, the entry table, the description.
    names_at = NE_SIZE + 2 + 3 * (8 + RESOURCE_SIZE) + 2
    resident_at = names_at + 1 + len("FONTDIR")
    entries_at = resident_at + 1 + len(module or "FONT") + 3
    resource = {"rnHandle": 0, "rnUsage": 0}
    return {
        "encoding": encoding,
        "mz_header": NEW_MZ,
        "dos_stub": NEW_STUB,
        "ne_header": dict(NEW_NE, ne_segtab=NE_SIZE, ne_rsrctab=NE_SIZE,
            ne_restab=resident_at, ne_modtab=entries_at, ne_imptab=entries_at,
            ne_enttab=entries_at, ne_nrestab=MZ_SIZE + len(NEW_STUB) // 2 + entries_at + 2),
        "resource_table": {"rscAlignShift": 4, "types": [
            {"rtTypeID": RT_FONTDIR, "rtReserved": 0, "resources": [dict(resource,
                rnFlags=0x0C50, rnID=names_at - NE_SIZE,
                fontdir={"entries": [{"fontOrdinal": 1, "same_as_font": True}]})]},
            {"rtTypeID": RT_FONT, "rtReserved": 0, "resources": [dict(resource,
                rnFlags=0x1C30, rnID=0x8001, font={"png": None, "txt": None, "header": header,
                    "zero_width": "", "absolute_space_width": 8, "device_name": None,
                    "face_name": face + FON_SUFFIX})]},
            {"rtTypeID": RT_VERSION, "rtReserved": 0, "resources": [dict(resource,
                rnFlags=0x0C30, rnID=0x8001, version={"block": version})]}],
            "names": [{"offset": names_at - NE_SIZE, "name": "FONTDIR"}]},
        "resident_names": [{"name": module or "FONT", "ordinal": 0}],
        "nonresident_names": [{"name": "FONTRES 100,%d,%d : %s %d"
            % (NEW_DPI, NEW_DPI, face, points), "ordinal": 0}],
        "entry_table": "0000",
    }, points


def bitmaps_height(path):
    """How many pixels high the bitmaps of a file are, all its rows of chars together."""
    if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
        try:
            return Image.open(path).size[1]
        except (OSError, ValueError) as error:
            die("%s cannot be read as an image: %s" % (shown(path), error))
    with open(path, encoding="ascii", errors="replace") as handle:
        return sum(1 for line in handle.read().splitlines() if line.strip(ROW_SEPARATOR))


def named_size(where, width, height):
    """The size that a font name tells, checked to be one that a font can have."""
    if height == 0 or width == 0:
        die("%s: the size in the name must be of 1 pixel or more" % where)
    return width, height


def lone_bitmaps(path, rows):
    """The bitmaps of a file that holds them alone, the chars of one fixed-pitch font from
    32 on: the stem of the font name that the file has, whether the name says bold and
    italic, the codes, the width and the height of a char, and the glyphs."""
    base, extension = os.path.splitext(path)
    reader = read_text if extension.lower() == ".txt" else read_png
    stem, named_width, named_height, bold, italic = split_name(os.path.basename(base))
    named_size(shown(path), named_width, named_height)
    if not os.path.isfile(path):
        die("there is no file %s" % shown(path))
    most = (256 - NEW_FIRST_CHAR) // CHARS_PER_ROW
    told = "--rows says so"
    if rows is None and named_height:
        high = bitmaps_height(path)
        rows, told = high // named_height, "the size in its name gives that"
        if high != rows * named_height or not 1 <= rows <= most:
            die("%s is %d pixels high, which is not 1..%d row(s) of chars %d pixels high, as"
                " its name has them" % (shown(path), high, most, named_height))
    elif rows is None:
        rows, told = NEW_ROWS, "that is so unless --rows tells"
    if not 1 <= rows <= most:
        die("--rows must be 1..%d: the chars start at %d, %d to a row, and end at 255 at most"
            % (most, NEW_FIRST_CHAR, CHARS_PER_ROW))
    if named_height and bitmaps_height(path) != rows * named_height:
        die("%s is %d pixels high, which is not %d row(s) of chars %d pixels high, as its name"
            " has them and --rows counts them"
            % (shown(path), bitmaps_height(path), rows, named_height))
    codes = list(range(NEW_FIRST_CHAR, NEW_FIRST_CHAR + rows * CHARS_PER_ROW))
    try:
        widths, glyphs = reader(path, fon_sheet(codes[0], codes[-1]), set(), None, None,
            NEW_DEFAULT_CHAR if codes[-1] >= NEW_DEFAULT_CHAR else None)
    except Failure as failure:
        die("%s; without %s, the bitmaps are %d row(s) of %d chars: %s"
            % (failure, JSON_NAME, rows, CHARS_PER_ROW, told))
    if len(set(widths)) != 1:
        die("%s: without %s, the font is of fixed pitch, but the chars are %d to %d pixels"
            " wide" % (shown(path), JSON_NAME, min(widths), max(widths)))
    width, height = widths[0], len(glyphs[0])
    if named_height and (named_width or width, named_height) != (width, height):
        die("%s: its name says chars of %s%d pixels, but its bitmaps are of chars %dx%d"
            % (shown(path), "%dx" % named_width if named_width else "", named_height, width,
            height))
    return stem, bold, italic, codes, width, height, glyphs


def fon_of_bitmaps(path, name, rows):
    """The file of one fixed-pitch font that its bitmaps alone make, and what the font is.
    The name of the file is the name of the font: its style, and its size if it has one."""
    set_encoding(name or DEFAULT_ENCODING)
    stem, bold, italic, codes, width, height, glyphs = lone_bitmaps(path, rows)
    family = (stem + " %dx%dpx" % (width, height)).strip()
    ascent, leading = letter_rows(shown(path), codes, glyphs, height)
    model, points = new_model(family, os.path.basename(os.path.splitext(path)[0]) + ".fon",
        width, height, codes, ascent, leading, bold, italic)
    data = build_fon(model, lambda *_font: ([width] * len(glyphs), glyphs),
        lambda message: note("Warning: " + message))
    return data, ("the face %r, %s, chars %d..%d, %d points, ascent %d, internal leading %d"
        % (family + FON_SUFFIX, STYLES[(bold, italic)], codes[0], codes[-1], points, ascent,
        leading))


def pack_bitmaps(path, name, rows):
    """Make a file of one fixed-pitch font from its bitmaps alone."""
    target = os.path.splitext(path)[0] + ".fon"
    check_backup(target)
    data, what = fon_of_bitmaps(path, name, rows)
    back_up(target)
    with open(target, "wb") as handle:
        handle.write(data)
    print("Packed %s into %s: %s." % (shown(path), shown(target), what))
    return 0


def create(name, encoding_name):
    """Write the files of a blank font, as unpacking a file of that font would."""
    set_encoding(encoding_name or DEFAULT_ENCODING)
    path = name if name.lower().endswith(".fon") else name + ".fon"
    if not os.path.basename(path)[:-len(".fon")] or os.path.isdir(name):
        die("%s must be the name of the font to create, which its file takes as well"
            % shown(name))
    stem, width, height, bold, italic = split_name(os.path.basename(path)[:-len(".fon")])
    width, height = named_size(shown(name), width, height)
    width, height = width or BLANK_WIDTH, height or BLANK_HEIGHT
    family = (stem + " %dx%dpx" % (width, height)).strip()
    directory = path + FILES_SUFFIX
    if not os.path.isdir(os.path.dirname(path) or "."):
        die("there is no directory %s to create %s in"
            % (shown_directory(os.path.dirname(path)), shown_directory(directory)))
    check_backup(directory)
    codes = list(range(NEW_FIRST_CHAR, NEW_FIRST_CHAR + NEW_ROWS * CHARS_PER_ROW))
    widths = [width] * len(codes)
    glyphs = [[[0] * width for _y in range(height)] for _code in codes]
    model, points = new_model(family, os.path.basename(path), width, height, codes, height, 0,
        bold, italic)
    data = build_fon(model, lambda *_font: (widths, glyphs),
        lambda message: note("Warning: " + message))
    write_files(directory, data, Report())
    print("Created %s: the files of a blank font, the face %r, %s, chars %d..%d, %d points;"
        " draw the chars, set the fields in %s, and pack makes %s of them."
        % (shown_directory(directory), family + FON_SUFFIX, STYLES[(bold, italic)], codes[0],
        codes[-1], points, JSON_NAME, shown(path)))
    return 0


def glyph_contours(rows):
    """The outline of the ink of a bitmap as closed loops of pixel corners, with y going up
    from the bottom row. The ink is on the right of every edge, so the outline of a hole
    runs the other way round."""
    height = len(rows)
    ink = set((x, height - 1 - y) for y, row in enumerate(rows) for x, bit in enumerate(row)
        if bit)
    edges = {}
    for x, y in ink:
        for start, end, beyond in (((x, y + 1), (x + 1, y + 1), (x, y + 1)),
                ((x + 1, y + 1), (x + 1, y), (x + 1, y)), ((x + 1, y), (x, y), (x, y - 1)),
                ((x, y), (x, y + 1), (x - 1, y))):
            if beyond not in ink:
                edges.setdefault(start, []).append(end)
    loops = []
    while edges:
        start = at = min(edges)
        loop, heading = [], None
        while True:
            ends = edges[at]
            end = ends[0]
            # Where two pixels touch by a corner, turning right keeps each in its own loop.
            if len(ends) > 1 and heading:
                end = (at[0] + heading[1], at[1] - heading[0])
            ends.remove(end)
            if not ends:
                del edges[at]
            turned = (end[0] - at[0], end[1] - at[1]) != heading
            heading = (end[0] - at[0], end[1] - at[1])
            if turned:
                loop.append(at)
            at = end
            if at == start:
                break
        # The walk began in the middle of an edge when it ends heading as it set out.
        if len(loop) > 1 and (loop[1][0] - loop[0][0]) * (loop[0][1] - loop[-1][1]) == (
                loop[1][1] - loop[0][1]) * (loop[0][0] - loop[-1][0]):
            del loop[0]
        loops.append(loop)
    return loops


def ink_top(rows):
    """How many rows of a bitmap there are from its first row with ink down to its end."""
    inked = [y for y, row in enumerate(rows) if any(row)]
    return len(rows) - inked[0] if inked else 0


def make_ttf(info, glyphs, cmap):
    """A TrueType file. Of info, units is the font units of a pixel, and em, ascent, descent,
    line_gap, the underline and the average width are in pixels; below is how many rows
    of a bitmap lie below the baseline; names are texts by their ids in the name table;
    encoding is the one by which the chars have their codes in the bitmaps.
    glyphs lists (name, bitmap): the outline of a glyph is that of the pixels of its bitmap,
    and a glyph without a bitmap is empty, of no width. cmap gives the glyph names of the
    codes."""
    need_fonttools()
    units, em, below = info["units"], info["em"], info["below"]
    ascent, descent, line_gap = info["ascent"], info["descent"], info["line_gap"]
    outlines, advances, bitmaps = {}, {}, {}
    for name, rows in glyphs:
        if name in outlines:
            die("%s: more than one glyph is named %s" % (info["label"], name))
        pen = TTGlyphPen(None)
        for loop in glyph_contours(rows or []):
            pen.moveTo((loop[0][0] * units, (loop[0][1] - below) * units))
            for x, y in loop[1:]:
                pen.lineTo((x * units, (y - below) * units))
            pen.closePath()
        outlines[name], advances[name] = pen.glyph(), len(rows[0]) * units if rows else 0
        if rows:
            bitmaps[name] = rows
    extent = max([em, ascent, descent] + [len(rows) for rows in bitmaps.values()]) * units
    if units < 1 or em * units > MAX_UNITS_PER_EM or max([extent] + list(advances.values())) > (
            MAX_COORDINATE):
        die("%s is too large for TrueType with an em of %d pixels" % (info["label"], em))

    def top_of(char):
        rows = bitmaps.get(cmap.get(ord(char)))
        return max(ink_top(rows) - below, 0) * units if rows else 0

    wide = [advance for advance in advances.values() if advance]
    fixed = len(set(wide)) == 1
    average = info.get("average")
    bold, italic = info["bold"], info["italic"]
    builder = FontBuilder(em * units, isTTF=True)
    builder.setupGlyphOrder([name for name, _rows in glyphs])
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(dict((name, (advance,
        getattr(builder.font["glyf"][name], "xMin", 0))) for name, advance in advances.items()))
    builder.setupHorizontalHeader(ascent=ascent * units, descent=-descent * units,
        lineGap=line_gap * units)
    builder.setupNameTable({}, mac=False)
    for name_id, text in sorted(info["names"].items()):
        builder.font["name"].setName(text, name_id, 3, 1, 0x409)
    # Bit 7 of fsSelection has the line spacing taken from the typographic metrics.
    builder.setupOS2(version=4, sTypoAscender=ascent * units, sTypoDescender=-descent * units,
        sTypoLineGap=line_gap * units, usWinAscent=ascent * units,
        usWinDescent=descent * units, usWeightClass=info["weight"],
        fsSelection=0x80 | (0x20 if bold else 0) | (0x01 if italic else 0)
        | (0 if bold or italic else 0x40),
        xAvgCharWidth=average * units if average else sum(wide) // max(len(wide), 1),
        sxHeight=top_of("x"), sCapHeight=top_of("H"), yStrikeoutSize=units,
        yStrikeoutPosition=(ascent // 3 + 1) * units)
    os2 = builder.font["OS/2"]
    os2.panose.bFamilyType = 2
    os2.panose.bProportion = 9 if fixed else 0
    os2.recalcUnicodeRanges(builder.font)
    os2.recalcCodePageRanges(builder.font)
    # The code page of the encoding is declared whatever chars of it the font has.
    if info["encoding"] in CODE_PAGES:
        os2.ulCodePageRange1 |= 1 << CODE_PAGES.index(info["encoding"])
    builder.setupPost(isFixedPitch=int(fixed),
        underlinePosition=info["underline"][0] * units,
        underlineThickness=info["underline"][1] * units)
    builder.font["head"].lowestRecPPEM = em
    builder.font["head"].macStyle = (1 if bold else 0) | (2 if italic else 0)
    # Bit 0 alone at every size asks for no smoothing.
    builder.font["gasp"] = gasp = newTable("gasp")
    gasp.version, gasp.gaspRange = 1, {0xFFFF: 0x0001}
    data = io.BytesIO()
    builder.save(data)
    return data.getvalue()


def need_fonttools():
    if FontBuilder is None:
        die("pxfont needs fontTools for TrueType fonts: python -m pip install fonttools")
    # What fontTools remarks on in a font is not about what is done with it here.
    logging.getLogger("fontTools").setLevel(logging.ERROR)


def build_ttf(font, widths, glyphs, chars_encoding, family, style, em):
    """A TrueType file of one font of a .fon file, and how many chars it has."""
    header = font["header"]
    height, ascent, first = header["dfPixHeight"], header["dfAscent"], header["dfFirstChar"]
    default = header["dfDefaultChar"]
    blank = [[0] * (header["dfAvgWidth"] or max(widths)) for _y in range(height)]
    bitmaps = {MISSING_GLYPH: glyphs[default] if default < len(glyphs) and widths[default]
        else blank}
    cmap = {}
    for code, rows, width in zip(range(first, first + len(glyphs)), glyphs, widths):
        try:
            char = bytes([code]).decode(chars_encoding)
        except UnicodeError:
            continue
        if width and len(char) == 1 and unicodedata.category(char) != "Cc" and (
                ord(char) not in cmap):
            cmap[ord(char)] = "uni%04X" % ord(char)
            bitmaps[cmap[ord(char)]] = rows
    plain = ["".join(char for char in text if char.isascii() and char.isalnum())
        for text in (family, style)]
    names = {1: family, 2: style, 3: "%s %s, TrueType" % (family, style),
        4: full_name(family, style), 5: "Version 1.0",
        6: "%s-%s" % (plain[0] or "Font", plain[1])}
    copyright = header["dfCopyright"]
    if copyright and not copyright.startswith("hex:"):
        names[0] = copyright
    info = {"label": "the font %r" % full_name(family, style), "names": names,
        "units": min(PIXEL_UNITS, MAX_UNITS_PER_EM // em), "em": em, "ascent": ascent,
        "descent": height - ascent, "below": height - ascent,
        "line_gap": header["dfExternalLeading"], "underline": (-1, 1),
        "weight": min(max(header["dfWeight"], 1), 1000) if header["dfWeight"] else 400,
        "bold": style.startswith("Bold"), "italic": style.endswith("Italic"),
        "average": header["dfAvgWidth"], "encoding": chars_encoding}
    return make_ttf(info, list(bitmaps.items()), cmap), len(cmap)


# ----------------------------------------------------------------------------------------
# TrueType fonts of pixels


def hex_ranges(codes):
    """Code points as ranges of hex numbers: "0020..007E, 00A0"."""
    runs = []
    for code in sorted(codes):
        if runs and runs[-1][1] == code - 1:
            runs[-1][1] = code
        else:
            runs.append([code, code])
    return ", ".join("%04X" % low if low == high else "%04X..%04X" % (low, high)
        for low, high in runs)


def parse_hex_ranges(value, where):
    if not isinstance(value, str):
        die("%s: must be a string of hex codes and ranges, like \"0020..007E, 00A0\"" % where)
    codes = set()
    for part in value.split(","):
        if not part.strip():
            continue
        try:
            ends = [int(end, 16) for end in part.split("..")]
        except ValueError:
            ends = []
        if len(ends) not in (1, 2) or ends[0] > ends[-1] or ends[-1] > 0x10FFFF:
            die("%s: %r is not a hex code or a range like 0020..007E" % (where, part.strip()))
        codes.update(range(ends[0], ends[-1] + 1))
    return codes


def code_key(code):
    return CODE_PREFIX + "%04X" % code


def keys_text(keys):
    """The chars and the glyphs of some keys, for a message."""
    codes = [int(key[len(CODE_PREFIX):], 16) for key in keys if key.startswith(CODE_PREFIX)]
    names = sorted(key for key in keys if not key.startswith(CODE_PREFIX))
    return "; ".join(text for text in ("chars " + hex_ranges(codes) if codes else "",
        "glyphs " + ", ".join(names) if names else "") if text)


def encoded_chars(chars_encoding):
    """The chars of an encoding of single bytes, by their codes."""
    chars = {}
    for code in range(256):
        try:
            char = bytes([code]).decode(chars_encoding)
        except UnicodeError:
            continue
        if len(char) == 1:
            chars[code] = ord(char)
    return chars


def declared_encoding(declared):
    """The encoding of the places 0..255 for a font that is unpacked without one told: the
    Windows code page that the font declares by the bits given, when it declares one
    alone, and otherwise the one that texts are in unless told. Nothing is made of which
    chars the font has."""
    pages = [page for bit, page in enumerate(CODE_PAGES) if declared >> bit & 1]
    return pages[0] if len(pages) == 1 else DEFAULT_ENCODING


def missing_place(codes, names, chars_encoding):
    """Whether the glyph of a missing char is in the place of the default char in the
    bitmaps of a TrueType font: it is when the font has the glyph and no char there."""
    return MISSING_GLYPH in names and (
        encoded_chars(chars_encoding).get(NEW_DEFAULT_CHAR) not in codes)


def ttf_sheet(codes, names, chars_encoding):
    """The rows of the bitmaps of a TrueType font: the chars that the encoding has for the
    codes 0..255, each in the slot of that code modulo 32, without the rows of no char;
    then the other chars, 32 to a row with no gaps; then the glyphs without a code. Of
    these, the glyph of a missing char is in the slot of the default char of a font made
    of bitmaps, when no char is there."""
    sheet, placed, names = [], {}, list(names)
    for code, char in encoded_chars(chars_encoding).items():
        if char in codes:
            placed[code] = code_key(char)
    if MISSING_GLYPH in names and NEW_DEFAULT_CHAR not in placed:
        names.remove(MISSING_GLYPH)
        placed[NEW_DEFAULT_CHAR] = MISSING_GLYPH
    for base in range(0, 256, CHARS_PER_ROW):
        row = [placed.get(code) for code in range(base, base + CHARS_PER_ROW)]
        while row and row[-1] is None:
            row.pop()
        if row:
            sheet.append(row)
    others = [code_key(code) for code in sorted(codes)]
    for keys in ([key for key in others if key not in placed.values()], names):
        sheet.extend(keys[at:at + CHARS_PER_ROW] for at in range(0, len(keys), CHARS_PER_ROW))
    return sheet


def pixel_unit(values, em_units):
    """The size of a pixel in font units, of the coordinates of a font: the one whose grid
    they agree with best, or one that does nearly as well and has whole pixels to the em.
    Then a multiple of it, when nearly all of them lie near that coarser grid as well: the
    few that do not are not of pixels. None when even the finest of these grids has less
    than the share of the coordinates that a font of pixels has near it."""
    counts = collections.Counter(abs(value) for value in values if value)
    total = sum(counts.values())
    if not total:
        return None

    def near(size):
        return sum(count for value, count in counts.items()
            if abs(value - size * round(value / size)) <= GRID_NEAR * size) / total

    # A value on a line of the grid adds 1, one halfway between two lines takes 1 away.
    scores = dict((size, sum(count * math.cos(2 * math.pi * value / size)
        for value, count in counts.items()) / total)
        for size in range(MIN_PIXEL_UNITS, max(counts) + 1))
    if not scores:
        return None
    best = max(scores.values())
    close = [size for size, score in scores.items() if score >= best - 0.01]
    whole = [size for size in close if em_units % size == 0] or close
    top = max(scores[size] for size in whole)
    unit = max(size for size in whole if scores[size] >= top - 1e-6)
    if near(unit) < GRID_SHARE:
        return None
    return max(times * unit for times in range(1, max(counts) // unit + 1)
        if near(times * unit) >= GRID_SHARE)


def outline_edges(contour, unit):
    """A contour of an outline as straight edges in pixels: the points on the curve joined,
    and each curve, which has a point off it for its control, in pieces."""
    points = [(x / unit, y / unit, on) for x, y, on in contour]
    # Between two points off the curve there is one on it, halfway.
    full = []
    for at, point in enumerate(points):
        before = points[at - 1]
        if not point[2] and not before[2]:
            full.append(((before[0] + point[0]) / 2, (before[1] + point[1]) / 2, 1))
        full.append(point)
    start = next((at for at, point in enumerate(full) if point[2]), None)
    if start is None:
        return []
    full = full[start:] + full[:start + 1]
    path, at = [full[0][:2]], 1
    while at < len(full):
        if full[at][2]:
            path.append(full[at][:2])
            at += 1
            continue
        (x0, y0), (x1, y1), (x2, y2) = path[-1], full[at][:2], full[at + 1][:2]
        for piece in range(1, CURVE_PIECES + 1):
            t = piece / CURVE_PIECES
            path.append(((1 - t) ** 2 * x0 + 2 * t * (1 - t) * x1 + t * t * x2,
                (1 - t) ** 2 * y0 + 2 * t * (1 - t) * y1 + t * t * y2))
        at += 2
    return list(zip(path, path[1:]))


def outline_pixels(contours, unit):
    """The pixels that an outline shows at one pixel to a pixel without smoothing: those with
    any winding of it around their centers."""
    edges = [edge for contour in contours for edge in outline_edges(contour, unit)
        if edge[0][1] != edge[1][1]]
    pixels = set()
    for y in range(math.floor(min([min(a[1], b[1]) for a, b in edges], default=0)),
            math.ceil(max([max(a[1], b[1]) for a, b in edges], default=0))):
        center = y + 0.5
        winding, before = 0, None
        for x, way in sorted((a[0] + (center - a[1]) / (b[1] - a[1]) * (b[0] - a[0]),
                1 if b[1] > a[1] else -1) for a, b in edges
                if (a[1] <= center) != (b[1] <= center)):
            if winding:
                pixels.update((at, y) for at in range(math.ceil(before - 0.5),
                    math.ceil(x - 0.5)))
            winding, before = winding + way, x
    return pixels


def grid_fit(contours, advance, unit):
    """How a glyph lies on the grid of pixels: 0 when all of it does, 1 when it is of pixels
    with points or an advance that lie near the grid, 2 when it is not drawn of pixels."""
    values = [advance] + [value for contour in contours for x, y, _on in contour
        for value in (x, y)]
    if max(abs(value / unit - round(value / unit)) for value in values) > GRID_TOLERANCE:
        return 2
    for contour in contours:
        corners = [(round(x / unit), round(y / unit)) for x, y, _on in contour]
        if not all(on for _x, _y, on in contour) or any(a[0] != b[0] and a[1] != b[1]
                for a, b in zip(corners, corners[1:] + corners[:1])):
            return 2
    return int(any(value % unit for value in values))


def parse_ttf(data, warn, where, chars_encoding=None):
    """A TrueType font of pixels as the object that ttf.json stores, the sheet of its
    bitmaps, and their cells by the keys of the sheet. The encoding is that of the places
    0..255 of the sheet; without one given, the font tells it."""
    need_fonttools()
    if data[:4] == SFNT_MAGICS[-1]:
        die("%s is a collection of fonts, which is not supported" % where)
    try:
        font = TTFont(io.BytesIO(data))
        order = font.getGlyphOrder()
        cmap = font.getBestCmap()
    except Exception as error:
        die("%s cannot be read as a TrueType font: %s" % (where, error))
    if "glyf" not in font or cmap is None:
        die("%s has no TrueType outlines, or no Unicode chars; such fonts are not supported"
            % where)
    glyf, head, hhea, os2, post = (font[tag] for tag in ("glyf", "head", "hhea", "OS/2", "post"))
    shapes, hinted = {}, 0
    for name in order:
        glyph = glyf[name]
        coordinates, ends, flags = glyph.getCoordinates(glyf)
        shapes[name] = [[(coordinates[at][0], coordinates[at][1], flags[at] & 1)
            for at in range(start, end + 1)] for start, end in zip([0] + [end + 1
            for end in ends], ends)]
        program = getattr(glyph, "program", None)
        hinted += bool(program and (getattr(program, "bytecode", None)
            or getattr(program, "assembly", None)))
    advances = dict((name, font["hmtx"][name][0]) for name in order)
    dropped = sorted(tag for tag in font.keys() if tag not in TTF_TABLES and tag != "GlyphOrder")
    if dropped or hinted:
        warn("%s: dropped, and not written back by packing: %s" % (where, "; ".join(
            text for text in ("the tables %s" % ", ".join(dropped) if dropped else "",
            "the instructions of %d glyph(s)" % hinted if hinted else "") if text)))

    unit = pixel_unit([value for name in order
        if all(on for contour in shapes[name] for _x, _y, on in contour)
        for contour in shapes[name] for x, y, _on in contour for value in (x, y)],
        head.unitsPerEm)
    if unit is None:
        die("%s is not a font of pixels: no grid of %d font units or more has %d%% of its"
            " points on it" % (where, MIN_PIXEL_UNITS, GRID_SHARE * 100))
    codes_of = {}
    for code, name in sorted(cmap.items()):
        codes_of.setdefault(name, []).append(code)
    rasters, zero, aliases, pixels_of = {}, [], {}, {}
    moved, unlike, cut = [], [], []
    for name in order:
        codes = codes_of.get(name, [])
        key = code_key(codes[0]) if codes else name
        aliases.update(("%04X" % code, "%04X" % codes[0]) for code in codes[1:])
        width = math.floor(advances[name] / unit + 0.5)
        pixels = outline_pixels(shapes[name], unit)
        inside = set((x, y) for x, y in pixels if 0 <= x < width)
        if width:
            rasters[key], pixels_of[key] = width, inside
        elif codes:
            zero.append(codes[0])
        if width or codes or pixels:
            fit = grid_fit(shapes[name], advances[name], unit)
            (moved if fit == 1 else unlike if fit == 2 else []).append(key)
            if inside != pixels:
                cut.append(key)
    drawn = sum(1 for name in order if shapes[name])
    if not rasters or 2 * len(unlike) > drawn:
        die("%s is not a font of pixels: of its %d glyph(s) with outlines, %d are not drawn"
            " of pixels of %d font units" % (where, drawn, len(unlike), unit))

    rounded = []

    def whole(what, value):
        if value != round(value):
            rounded.append("%s %g to %d" % (what, value, round(value)))
        return round(value)

    em = max(whole("the em", head.unitsPerEm / unit), 1)
    ascent = max(whole("the ascent", hhea.ascent / unit), 0)
    descent = max(whole("the descent", -hhea.descent / unit), 0)
    line_gap = max(whole("the line gap", hhea.lineGap / unit), 0)
    # A cell holds the line of the font, and all the ink that goes beyond it.
    rows = [y for pixels in pixels_of.values() for _x, y in pixels]
    above = max([ascent] + [y + 1 for y in rows])
    below = max([descent] + [-y for y in rows])
    above = max(above, 1 - below)
    cells = dict((key, (width, [[int((x, y) in pixels_of[key]) for x in range(width)]
        for y in range(above - 1, -below - 1, -1)])) for key, width in rasters.items())
    if moved:
        warn("%s: moved onto the grid of pixels, by a third of a pixel at most: %s"
            % (where, keys_text(moved)))
    if unlike:
        warn("%s: not drawn of pixels, and made of the pixels whose centers they hold: %s"
            % (where, keys_text(unlike)))
    if cut:
        warn("%s: wider than their advance, and cut to it: %s" % (where, keys_text(cut)))
    if rounded:
        warn("%s: rounded to whole pixels: %s" % (where, ", ".join(rounded)))

    names = {}
    for key, name_id in TTF_NAMES:
        text = font["name"].getDebugName(name_id)
        if text:
            names[key] = text
    chars = sorted(int(key[len(CODE_PREFIX):], 16) for key in rasters
        if key.startswith(CODE_PREFIX))
    glyphs = [name for name in order if name in rasters and name not in codes_of]
    chars_encoding = chars_encoding or declared_encoding(getattr(os2, "ulCodePageRange1", 0))
    if MISSING_GLYPH in glyphs and encoded_chars(chars_encoding).get(NEW_DEFAULT_CHAR) in chars:
        warn("%s: the place %d of the bitmaps holds a char, so the glyph of a missing char,"
            " %s, is in their last row" % (where, NEW_DEFAULT_CHAR, MISSING_GLYPH))
    model = {
        "names": names,
        "units_per_pixel": unit,
        "em": em,
        "ascent": ascent,
        "descent": descent,
        "line_gap": line_gap,
        "rows_above_baseline": above,
        "rows_below_baseline": below,
        "weight": os2.usWeightClass,
        "bold": bool(os2.fsSelection & 0x20 or head.macStyle & 1),
        "italic": bool(os2.fsSelection & 0x01 or head.macStyle & 2),
        "underline_position": round(post.underlinePosition / unit),
        "underline_thickness": max(round(post.underlineThickness / unit), 1),
        "png": None,
        "txt": None,
        "encoding": chars_encoding,
        "chars": hex_ranges(chars),
        "zero_width": hex_ranges(zero),
        "aliases": aliases,
        "glyphs": glyphs,
    }
    return model, ttf_sheet(set(chars), glyphs, chars_encoding), cells


def usual_width(cells):
    """The width that most cells have."""
    return collections.Counter(width for width, _rows in cells.values()).most_common(1)[0][0]


def write_ttf_files(directory, model, sheet, cells, fallback):
    """Write the files of a TrueType font into a new directory. The bitmap files are named
    after the font, or after the fallback when it has no name."""
    names = model["names"]
    base = file_name(names.get("full_name") or names.get("family") or fallback)
    model["png"], model["txt"] = base + ".png", base + ".txt"
    height = model["rows_above_baseline"] + model["rows_below_baseline"]
    gap = usual_width(cells)
    bare = bare_cell(cells, MISSING_GLYPH if missing_place(
        parse_hex_ranges(model["chars"], "chars"), model["glyphs"], model["encoding"])
        else None, gap)
    back_up(directory)
    os.mkdir(directory)
    write_png(os.path.join(directory, model["png"]), sheet, cells, height, gap, bare)
    write_svg(os.path.join(directory, base + ".svg"), sheet, cells, height, gap,
        lambda key: (chr(int(key[len(CODE_PREFIX):], 16)), key[len(CODE_PREFIX):])
        if key.startswith(CODE_PREFIX) else (None, key), bare)
    with open(os.path.join(directory, model["txt"]), "w", encoding="ascii",
            newline="\n") as handle:
        handle.write("\n".join(text_lines(sheet, cells, height, bare)) + "\n")
    with open(os.path.join(directory, TTF_JSON), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(model) + "\n")


def unpack_ttf(path, data, chars_encoding):
    directory = path + FILES_SUFFIX
    report = Report()
    model, sheet, cells = parse_ttf(data, report.warning, shown(path), chars_encoding)
    height = model["rows_above_baseline"] + model["rows_below_baseline"]
    write_ttf_files(directory, model, sheet, cells,
        os.path.splitext(os.path.basename(path))[0])
    print("Unpacked %s into %s: %d glyph(s), with the chars of %s in the places 0..255; a"
        " pixel is %d font units, the em %d pixels, a cell %d pixels high."
        % (shown(path), shown_directory(directory), len(cells), model["encoding"],
        model["units_per_pixel"], model["em"], height))

    # The files just written are packed in memory, and the result unpacked: that finds what
    # packing refuses, and whether the glyphs and the metrics come back.
    try:
        again, _sheet, cells_again = parse_ttf(build_ttf_from(directory),
            lambda _message: None, "the font that packing makes", model["encoding"])
        if dict(again, png=model["png"], txt=model["txt"]) != model or cells_again != cells:
            report.warning("packing these files gives a font of other glyphs or metrics: %s"
                % (", ".join(key for key in model if key not in ("png", "txt")
                and again[key] != model[key]) or "the bitmaps"))
        else:
            print("Packing these files gives a font of the same glyphs and metrics.")
    except Failure as failure:
        report.error("packing these files will be refused. %s" % failure)
    if report.errors or report.warnings:
        print("%d error(s), %d warning(s)." % (report.errors, report.warnings))
    return 1 if report.errors else 0


def made_names(family, style):
    """The texts of a TrueType font that are made of its family and its style, by their ids
    in the name table: the unique name, the full name and the PostScript name."""
    return {3: "%s %s, TrueType" % (family, style), 4: full_name(family, style),
        6: "%s-%s" % tuple("".join(char for char in text if char.isascii() and char.isalnum())
        or "Font" for text in (family, style))}


def whole_number(fields, name, low, high):
    value = fields.take(name)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        die("%s.%s: must be a whole number within %d..%d" % (fields.where, name, low, high))
    return value


def build_ttf_from(directory):
    """The TrueType file that the files in the directory describe."""
    path = os.path.join(directory, TTF_JSON)
    try:
        with open(path, encoding="utf-8") as handle:
            model = json.load(handle)
    except ValueError as error:
        die("%s is not valid JSON: %s" % (shown(path), error))
    top = Fields(model, TTF_JSON)
    texts = Fields(top.take("names"), "names")
    names = {}
    for key, name_id in TTF_NAMES:
        text = texts.take(key, None)
        if text is not None:
            if not isinstance(text, str):
                die("names.%s: must be a string" % key)
            names[name_id] = text
    texts.done()
    if 1 not in names:
        die("names: family is missing")
    flags = {}
    for key in ("bold", "italic"):
        flags[key] = top.take(key)
        if not isinstance(flags[key], bool):
            die("%s: must be true or false" % key)
    names.setdefault(2, STYLES[(flags["bold"], flags["italic"])])
    for name_id, text in made_names(names[1], names[2]).items():
        names.setdefault(name_id, text)
    names.setdefault(5, "Version 1.0")
    unit = whole_number(top, "units_per_pixel", 1, MAX_UNITS_PER_EM)
    above = whole_number(top, "rows_above_baseline", -255, 4096)
    below = whole_number(top, "rows_below_baseline", -255, 4096)
    if above + below < 1:
        die("rows_above_baseline and rows_below_baseline must make a cell of 1 row or more")
    info = dict(flags, label="the font %r" % names[4], names=names, units=unit, below=below,
        em=whole_number(top, "em", 1, MAX_UNITS_PER_EM),
        ascent=whole_number(top, "ascent", 0, 4096),
        descent=whole_number(top, "descent", 0, 4096),
        line_gap=whole_number(top, "line_gap", 0, 4096),
        weight=whole_number(top, "weight", 1, 1000),
        underline=(whole_number(top, "underline_position", -4096, 4096),
        whole_number(top, "underline_thickness", 0, 4096)))
    files = (top.take("png"), top.take("txt"))
    chars_encoding = top.take("encoding")
    try:
        chars_encoding = codecs.lookup(chars_encoding).name
    except (LookupError, TypeError):
        die("encoding: there is no encoding named %r" % (chars_encoding,))
    info["encoding"] = chars_encoding
    chars = parse_hex_ranges(top.take("chars"), "chars")
    zero = parse_hex_ranges(top.take("zero_width"), "zero_width")
    extra = top.take_list("glyphs")
    if any(not isinstance(name, str) or not name or name.startswith(CODE_PREFIX)
            for name in extra) or len(set(extra)) != len(extra):
        die("glyphs: must be a list of glyph names, each different")
    shared = top.take("aliases")
    if not isinstance(shared, dict):
        die("aliases: must be an object of hex codes, each with the hex code whose glyph it"
            " shares")
    top.done()

    sheet = ttf_sheet(chars, extra, chars_encoding)
    if not sheet:
        die("%s: chars and glyphs name no glyph of pixels" % TTF_JSON)
    height = above + below
    _widths, bitmaps = load_glyphs(directory, files, sheet, set(), height, None, TTF_JSON,
        lambda keys: ", ".join(sorted(keys)),
        MISSING_GLYPH if missing_place(chars, extra, chars_encoding) else None)
    cells = dict(zip(sheet_keys(sheet), bitmaps))
    if chars & zero:
        die("%s: %s are listed both in chars and in zero_width"
            % (TTF_JSON, hex_ranges(chars & zero)))

    def name_of(code):
        return "uni%04X" % code if code <= 0xFFFF else "u%05X" % code

    codes = sorted(chars | zero)
    glyphs = [(MISSING_GLYPH, cells.get(MISSING_GLYPH))]
    glyphs += [(name_of(code), cells.get(code_key(code))) for code in codes]
    glyphs += [(name, cells[name]) for name in extra if name != MISSING_GLYPH]
    cmap = dict((code, name_of(code)) for code in codes)
    for code, target in shared.items():
        here = "aliases.%s" % code
        code = as_int("0x%s" % code, here, 0x10FFFF)
        target = as_int("0x%s" % target if isinstance(target, str) else target, here, 0x10FFFF)
        if code in cmap or target not in codes:
            die("%s: must be a code without a glyph of its own, for a code that has one" % here)
        cmap[code] = name_of(target)
    return make_ttf(info, glyphs, cmap)


def ttf_targets(source, fonts):
    """The file, the family and the style for each font of a file: the file is beside the
    source, and has the name of the font."""
    taken, out = set(), []
    for family, style in font_names(fonts):
        name = full_name(family, style)
        if name.lower() in taken:
            die("%s has more than one font named %r; tell them apart by their face names in"
                " %s, which unpack writes" % (shown(source), name, JSON_NAME))
        taken.add(name.lower())
        out.append((os.path.join(os.path.dirname(source), file_name(name) + ".ttf"), family,
            style))
    return out


def ttf(target, name, rows, em):
    need_fonttools()
    if em is not None and em < 1:
        die("--em must be 1 or more pixels")
    source = target
    if is_bitmaps(target):
        data = fon_of_bitmaps(target, name, rows)[0]
    elif rows is not None:
        die(ROWS_MISPLACED)
    elif os.path.isdir(target):
        source = fon_path_of(target.rstrip("/\\"))
        data = build_from(target.rstrip("/\\"), lambda message: note("Warning: " + message),
            name)
    else:
        set_encoding(name or DEFAULT_ENCODING)
        with open(target, "rb") as handle:
            data = handle.read()
    report = Report(quiet=True)
    _model, fonts = parse_fon(data, report)
    if not fonts:
        die("%s has no raster font to convert" % shown(target))
    targets = ttf_targets(source, fonts)
    for path, _family, _style in targets:
        check_backup(path)
    for (font, widths, glyphs, _where), (path, family, style) in zip(fonts, targets):
        header = font["header"]
        chars = encoding if name else CHARSET_ENCODINGS.get(header["dfCharSet"], encoding)
        size = em or header["dfPixHeight"]
        made, count = build_ttf(font, widths, glyphs, chars, family, style, size)
        back_up(path)
        with open(path, "wb") as handle:
            handle.write(made)
        print("Made %s: the family %r, %s, %d chars by %s, ascent %d; exact at %d pixels to"
            " the em, which is %g points at 96 dpi, and at its multiples."
            % (shown(path), family, style, count, chars, header["dfAscent"], size,
            size * 72 / 96))
    if report.errors:
        note("%s has %d error(s), so its fonts may be damaged; unpack tells what they are"
            % (shown(target), report.errors))
    return 1 if report.errors else 0


# ----------------------------------------------------------------------------------------
# Altering the bitmaps


class Change:
    """What a verb does to every glyph: its columns left out or repeated, then the overlay
    that makes it bold, then the slant that makes it italic. The word is what the change of
    the columns is called in a name."""

    def __init__(self, word=None, columns=(), repeat=False, bold=False, italic=False):
        self.word, self.columns, self.repeat = word, list(columns), repeat
        self.bold, self.italic = bold, italic

    def sources(self, width):
        """The columns of a glyph of a width that make the columns of the new glyph. A
        glyph that would be left with none keeps them all."""
        if self.repeat:
            return [x for x in range(width) for _copy in range(1 + self.columns.count(x))]
        return [x for x in range(width) if x not in self.columns] or list(range(width))

    def width(self, width):
        return len(self.sources(width))

    def glyph(self, rows):
        columns = self.sources(len(rows[0]) if rows else 0)
        rows = [[row[x] for x in columns] for row in rows]
        if self.bold:
            rows = [[int(bit or (x > 0 and row[x - 1])) for x, bit in enumerate(row)]
                for row in rows]
        if self.italic:
            rows = [[0] + row[:-1] if y < len(rows) // 2 and row else row
                for y, row in enumerate(rows)]
        return rows

    def image(self, image, width, height, glyphs):
        """An image of bitmaps alone after the change, each pixel in the color that it had.
        A pixel that becomes ink takes the color of the ink at its left; the paper that
        comes in at the left of a slanted row is that of the cell."""
        image = image.convert("RGBA")
        old = image.load()
        columns = self.sources(width)
        new = Image.new("RGBA", (CHARS_PER_ROW * len(columns), image.size[1]), CLEAR)
        pixels = new.load()
        for at, glyph in enumerate(glyphs):
            left, top = at % CHARS_PER_ROW, at // CHARS_PER_ROW * height
            colors = [[old[left * width + x, top + y] for x in columns]
                for y in range(height)]
            inked = [[row[x] for x in columns] for row in glyph]
            if self.bold:
                colors = [[line[x - 1] if x and bits[x - 1] and not bits[x] else color
                    for x, color in enumerate(line)] for line, bits in zip(colors, inked)]
                inked = [[int(bit or (x > 0 and bits[x - 1])) for x, bit in enumerate(bits)]
                    for bits in inked]
            if self.italic and columns:
                papers = collections.Counter(color for line, bits in zip(colors, inked)
                    for color, bit in zip(line, bits) if not bit)
                paper = papers.most_common(1)[0][0] if papers else old[0, 0]
                for y in range(height // 2):
                    colors[y] = [paper if inked[y][0] else colors[y][0]] + colors[y][:-1]
            for y, line in enumerate(colors):
                for x, color in enumerate(line):
                    pixels[left * len(columns) + x, top + y] = color
        return new


def parse_columns(text):
    try:
        columns = [int(part) for part in text.split(",")]
    except ValueError:
        columns = []
    if not columns or min(columns) < 0:
        die("%r must be a list of pixel columns, counted from 0, like 0,3,7" % text)
    return columns


def changed_name(name, change, size):
    """The name of what a change makes of a font, for a file or a directory named after the
    font: the size in the name becomes the new one, or is added to it, and the style gains
    what the change adds. Without a new size to tell, as when the size is as it was or the
    fonts are several, the word for the change goes before the size."""
    style = NAME_STYLE.search(name)
    bold, italic = (bool(word) for word in style.groups())
    stem = name[:style.start()]
    named = NAME_SIZE.search(stem)
    if change.word and size:
        stem = (stem[:named.start()] if named else stem) + size
    elif change.word:
        stem = "%s %s%s" % (stem[:named.start()] if named else stem, change.word,
            named.group() if named else "")
    return full_name(stem.strip(), STYLES[(bold or change.bold, italic or change.italic)])


def resized(name, old, new, word):
    """A face name or a family after a change of the columns that has the word given: its
    size, when it ends with the old one, is the new one; when it tells no size, or the
    size is as it was, the word goes into it, before that size."""
    if not word:
        return name
    suffix = name[len(name) - len(FON_SUFFIX):] if name.lower().endswith(
        FON_SUFFIX.lower()) else ""
    base = name[:len(name) - len(suffix)]
    size = base[len(base) - len(old):] if base.lower().endswith(old.lower()) else ""
    stem = base[:len(base) - len(size)]
    return (stem + new if size and old != new else "%s %s%s" % (stem, word, size)) + suffix


def alter_file(path, rows, change):
    """Write the bitmaps alone of a file, changed, into a new file beside it."""
    _stem, was_bold, was_italic, _codes, width, height, glyphs = lone_bitmaps(path, rows)
    base, extension = os.path.splitext(path)
    new_width = change.width(width)
    for said, does, what in ((was_bold, change.bold, "bold"),
            (was_italic, change.italic, "italic")):
        if said and does:
            note("Warning: %s is %s already, by its name" % (shown(path), what))
    target = os.path.join(os.path.dirname(path), changed_name(os.path.basename(base), change,
        " %dx%dpx" % (new_width, height) if new_width != width else None)) + (
        ".txt" if extension.lower() == ".txt" else ".png")
    check_backup(target)
    changed = [change.glyph(glyph) for glyph in glyphs]
    sheet = fon_sheet(NEW_FIRST_CHAR, NEW_FIRST_CHAR + len(glyphs) - 1)
    if extension.lower() == ".txt":
        cells = dict(zip(sheet_keys(sheet), ((new_width, glyph) for glyph in changed)))
        back_up(target)
        with open(target, "w", encoding="ascii", newline="\n") as handle:
            handle.write("\n".join(text_lines(sheet, cells, height,
                bare_cell(cells, NEW_DEFAULT_CHAR, new_width))) + "\n")
    else:
        image = change.image(Image.open(path), width, height, glyphs)
        back_up(target)
        image.save(target, "PNG")
    print("Made %s of %s: chars of %dx%d pixels." % (shown(target), shown(path), new_width,
        height))
    return 0


def alter_fon(directory, name, change, warn):
    """The .fon file of the fonts of a directory, changed, and the new size of its font as
    a name has it, when it holds one font alone and the size is another than it was."""
    build_from(directory, lambda _message: None, name)
    with open(os.path.join(directory, JSON_NAME), encoding="utf-8") as handle:
        model = json.load(handle)
    changed, sizes, same = {}, [], True
    for t, one in enumerate(model["resource_table"]["types"]):
        for r, resource in enumerate(one["resources"]):
            if "font" not in resource:
                continue
            where = "resource_table.types[%d].resources[%d].font" % (t, r)
            font = resource["font"]
            header = font["header"]
            stated = dict((key, as_int(header[key], where, 0xFFFF)) for key in (
                "dfFirstChar", "dfLastChar", "dfPixHeight", "dfPixWidth", "dfAvgWidth",
                "dfMaxWidth", "dfPitchAndFamily", "dfWeight", "dfItalic", "dfDefaultChar"))
            first, last = stated["dfFirstChar"], stated["dfLastChar"]
            widths, glyphs = load_glyphs(directory, (font["png"], font["txt"]),
                fon_sheet(first, last), parse_ranges(font["zero_width"], where, first, last),
                stated["dfPixHeight"], False if stated["dfPitchAndFamily"] & 1
                else stated["dfPixWidth"] or None, where, None, default_place(stated),
                stated["dfPixWidth"] or stated["dfAvgWidth"])
            glyphs = [change.glyph(glyph) for glyph in glyphs]
            new_widths = [len(glyph[0]) for glyph in glyphs]
            old = size_suffix(stated, widths)
            for key in ("dfPixWidth", "dfAvgWidth", "dfMaxWidth"):
                header[key] = change.width(stated[key])
            # A narrow glyph that is not changed may be left wider than the widest one is.
            header["dfMaxWidth"] = max([header["dfMaxWidth"]] + new_widths)
            sizes.append(size_suffix(dict(stated, dfPixWidth=header["dfPixWidth"]), new_widths))
            same = same and old == sizes[-1]
            if isinstance(font["face_name"], str) and not font["face_name"].startswith("hex:"):
                font["face_name"] = resized(font["face_name"], old, sizes[-1], change.word)
            if change.bold and stated["dfWeight"] > 500:
                warn("%s: is bold already, of the weight %d" % (where, stated["dfWeight"]))
            elif change.bold:
                header["dfWeight"] = 700
            if change.italic and stated["dfItalic"]:
                warn("%s: is italic already" % where)
            elif change.italic:
                header["dfItalic"] = 1
            changed[where] = (new_widths, glyphs)
    data = build_fon(model, lambda *font: changed[font[5]], warn)
    return data, sizes[0] if len(sizes) == 1 and not same else None


def alter_ttf(directory, change, warn):
    """The ttf.json object, the sheet and the cells of the font of a directory, changed, and
    the new size of the font as a name has it, when it is another than it was."""
    build_ttf_from(directory)
    with open(os.path.join(directory, TTF_JSON), encoding="utf-8") as handle:
        model = json.load(handle)
    chars = parse_hex_ranges(model["chars"], "chars")
    height = model["rows_above_baseline"] + model["rows_below_baseline"]
    sheet = ttf_sheet(chars, model["glyphs"], model["encoding"])
    _widths, bitmaps = load_glyphs(directory, (model["png"], model["txt"]), sheet, set(),
        height, None, TTF_JSON, lambda keys: ", ".join(sorted(keys)),
        MISSING_GLYPH if missing_place(chars, model["glyphs"], model["encoding"]) else None)
    keys = sheet_keys(sheet)

    def size_of(glyphs):
        widths = set(len(glyph[0]) for glyph in glyphs)
        return " %s%dpx" % ("%dx" % widths.pop() if len(widths) == 1 else "", height)

    old = size_of(bitmaps)
    cells = dict((key, (len(glyph[0]), glyph)) for key, glyph
        in zip(keys, (change.glyph(glyph) for glyph in bitmaps)))
    new = size_of([glyph for _width, glyph in cells.values()])
    names = model["names"]
    before = (names["family"], names.get("style", STYLES[(model["bold"], model["italic"])]))
    for flag, does in (("bold", change.bold), ("italic", change.italic)):
        if does and model[flag]:
            warn("%s: the font is %s already" % (TTF_JSON, flag))
        model[flag] = bool(model[flag] or does)
    if change.bold and model["weight"] < 600:
        model["weight"] = 700
    names["family"] = resized(names["family"], old, new, change.word)
    if before[1] in STYLES.values():
        names["style"] = STYLES[(model["bold"], model["italic"])]
    # The texts that were made of the family and the style are made of the new ones.
    was, now = made_names(*before), made_names(names["family"], names.get("style", before[1]))
    for key, name_id in TTF_NAMES:
        if name_id in was and names.get(key) == was[name_id]:
            names[key] = now[name_id]
    return model, sheet, cells, new if new != old else None


def alter(target, name, rows, change):
    if is_bitmaps(target):
        return alter_file(target, rows, change)
    if rows is not None:
        die(ROWS_MISPLACED)
    directory = target.rstrip("/\\")
    if not os.path.isdir(directory):
        die("%s is neither a directory that unpack made, nor a .png, a .psd or a .txt"
            % shown(directory))
    root, extension = os.path.splitext(os.path.basename(fon_path_of(directory)))

    def warn(message):
        note("Warning: " + message)

    if os.path.isfile(os.path.join(directory, TTF_JSON)):
        model, sheet, cells, size = alter_ttf(directory, change, warn)
    else:
        data, size = alter_fon(directory, name, change, warn)
    target = os.path.join(os.path.dirname(directory),
        changed_name(root, change, size) + extension + FILES_SUFFIX)
    check_backup(target)
    if os.path.isfile(os.path.join(directory, TTF_JSON)):
        write_ttf_files(target, model, sheet, cells, root)
        count = 1
    else:
        count = len(write_files(target, data, Report())[1])
    print("Made %s of %s: %d font(s)." % (shown_directory(target), shown_directory(directory),
        count))
    return 0


def expand(target, name, columns, rows):
    return alter(target, name, rows, Change("wide", parse_columns(columns), True))


def contract(target, name, columns, rows):
    return alter(target, name, rows, Change("narrow", parse_columns(columns)))


def bold(target, name, rows):
    return alter(target, name, rows, Change(bold=True))


def italic(target, name, rows):
    return alter(target, name, rows, Change(italic=True))


def bold_italic(target, name, rows):
    return alter(target, name, rows, Change(bold=True, italic=True))


ALTERED = ("FILE%s|FONT.png|FONT.psd|FONT.txt" % FILES_SUFFIX,
    "the directory that unpack made, or the bitmaps of one fixed-pitch font",
    "the encoding of the texts of a .fon file (default: the one %s records)" % JSON_NAME)
# A verb as its name, its function, its argument and the help for it, the help for
# --encoding, and its other options: columns, which is an argument before the other one,
# and numbers, all passed on in this order.
VERBS = (
    ("unpack", unpack, "FILE.fon|FILE.ttf", "the .fon file or the TrueType font to unpack",
        "the encoding of the texts in a .fon file (default: %s), or of the places 0..255 in"
        " the bitmaps of a TrueType font (default: the code page that the font declares,"
        " if one alone, or %s)" % (DEFAULT_ENCODING, DEFAULT_ENCODING), ()),
    ("pack", pack, "FILE%s|FONT.png|FONT.psd|FONT.txt" % FILES_SUFFIX,
        "the directory that unpack made, or the bitmaps of one fixed-pitch font",
        "the encoding to write the texts in (default: the one %s records, or %s without it)"
        % (JSON_NAME, DEFAULT_ENCODING), ("rows",)),
    ("create", create, "FONT", "the name of the font, as \"zx 6x8px\"; the directory is"
        " FONT.fon%s" % FILES_SUFFIX,
        "the encoding to write the texts in (default: %s)" % DEFAULT_ENCODING, ()),
    ("ttf", ttf, "FILE.fon|FILE.fon%s|FONT.png|FONT.psd|FONT.txt" % FILES_SUFFIX,
        "the .fon file, the directory that unpack made, or the bitmaps of one fixed-pitch font",
        "the encoding of the chars and of the texts (default: for the chars, the one that"
        " dfCharSet names)", ("rows", "em")),
    ("expand", expand) + ALTERED + (("columns", "rows"),),
    ("contract", contract) + ALTERED + (("columns", "rows"),),
    ("bold", bold) + ALTERED + (("rows",),),
    ("italic", italic) + ALTERED + (("rows",),),
    ("bold-italic", bold_italic) + ALTERED + (("rows",),),
)
OPTIONS = {
    "rows": "for the bitmaps alone: how many rows of %d chars they are (default: what the"
        " size in the file name gives, or %d)" % (CHARS_PER_ROW, NEW_ROWS),
    "em": "the height of the em in pixels (default: the height of the chars)",
    "columns": "the pixel columns of a glyph to repeat or to leave out, counted from 0, as"
        " 0,3,7",
}


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return None
    verbs = dict((verb[0], verb[1:]) for verb in VERBS)
    if argv[0] not in verbs:
        die("no verb %r; run pxfont --help for the list" % argv[0])
    run, target, target_help, encoding_help, options = verbs[argv[0]]
    parser = argparse.ArgumentParser(prog="pxfont " + argv[0],
        epilog="pxfont --help describes the files.")
    if "columns" in options:
        parser.add_argument("columns", metavar="COLUMNS", help=OPTIONS["columns"])
    parser.add_argument("target", metavar=target, help=target_help)
    parser.add_argument("--encoding", metavar="NAME", help=encoding_help)
    for option in options:
        if option != "columns":
            parser.add_argument("--" + option, metavar="N", type=int, help=OPTIONS[option])
    args = parser.parse_args(argv[1:])
    if Image is None:
        die("pxfont needs Pillow for the PNG files: python -m pip install pillow")
    # A name in another script must not stop a message on a console that lacks it.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    return run(args.target, args.encoding, *(getattr(args, option) for option in options))


if __name__ == "__main__":
    exit_with(main)
