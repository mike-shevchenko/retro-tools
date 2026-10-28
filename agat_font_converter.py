#!/usr/bin/env python3
"""
Agat-7 font tool.

Four modes:

  1. C export (original behavior):
         agat_font_converter.py agat7-1.png font.c

  2. Text rendering to PNG:
         agat_font_converter.py agat7-1.png out.png --text "КИБЕР-МУЗЕЙ, МУРОМ"

  3. Font sheet for pxfont.py, laid out as CP1251 (an output .png and no other flags):
         agat_font_converter.py "Agat-7 font main.png" "Agat 7x8px.png"

  4. Files of a TrueType font for pxfont.py, of every char that a glyph of the chart stands
     for (an output directory named FONT.ttf.files, which tells the family, FONT; without
     an output, "CHART 7x8px.ttf.files" beside the chart):
         agat_font_converter.py agat7-2.png
     and then "pxfont.py ttf "agat7-2 7x8px.ttf.files"" makes the font. An existing
     directory is renamed to .BAK.

The input is either chart: the full one of 256 labeled cells, or the main one of the
96 glyphs 0x20..0x7F alone. A full chart holds one of three sets, which the glyphs that it
repeats tell apart:
  agat7-1: the Agat-7's own, repeating 0x20..0x3F at 0x00..0x1F and 0x00..0x7F at
    0x80..0xFF: ASCII at 0x20..0x5F, the Cyrillic capitals at 0x60..0x7F.
  agat7-2: graphics at 0x00..0x3F, small letters at 0x40..0x7F, signs at 0x80..0x9F, ASCII
    at 0xA0..0xDF, the Cyrillic capitals at 0xE0..0xFF.
  agat9: as agat7-2, but with graphics of its own, the small Yo at 0x0F, and a copy of its
    Latin capitals 0xC0..0xDD at 0x80..0x9D, followed by a dot and the capital Yo.
Every glyph with ink but for the copies has to stand for a char of the tables here; the
glyphs of agat7-2 and agat9 that have no Unicode char get one of a like look, so that every
glyph of the Agat can be typed. The chars that CP1251 has are in its places in the PNG of
the files of a TrueType font, and the others after them, as pxfont.py lays them out.

Extra options for mode 2:
  --scale N: output pixels per font pixel (default 1)
  --spacing N: blank font-pixel columns between glyphs (default 0)
  --margin N: blank font-pixel border around the text (default 0)
  --invert: swap foreground/background
  --charmap: dump the whole 96-glyph set with indices, for checking
"""

import argparse
import json
import os
import sys

from PIL import Image

CHAR_W, CHAR_H = 7, 8  # glyph size in font pixels
CELL_W, CELL_H = 15, 17  # cell pitch in image pixels (2x2 pixels + grid line)
ORIGIN_X, ORIGIN_Y = 15, 17  # first glyph pixel, past the label row/column
COLS, ROWS = 16, 16  # 256 cells; index == character code
FIRST_CODE = 0

# The main chart: the 96 glyphs 0x20..0x7F alone, in 6 rows with no labels.
MAIN_SIZE = (241, 103)
MAIN_ORIGIN = (1, 1)
MAIN_ROWS = 6

# The sheet for pxfont.py: the codes 0x20..0xFF, 32 to a row, on a checkerboard of papers.
SHEET_COLS, SHEET_ROWS = 32, 7
SHEET_FIRST_CODE = 0x20
SHEET_PAPERS = ((255, 255, 255, 255), (192, 192, 192, 255))
SHEET_INK = (0, 0, 0, 255)
# A code with no Agat-7 glyph gets a box, as high as a capital letter, so that a text shows
# what the font lacks. These codes are blank: one that CP1251 leaves undefined, NBSP and the
# soft hyphen.
SHEET_BOX = [[int(y < 7 and 1 <= x <= 5 and (y in (0, 6) or x in (1, 5))) for x in range(7)]
    for y in range(8)]
SHEET_BLANK = (0x98, 0xA0, 0xAD)
# The blank chars of a TrueType font: NBSP and the soft hyphen. CP1251's undefined code has
# no char in it.
BLANK_CHARS = (0x00A0, 0x00AD)
# The cell without paper: the box that pxfont.py takes for the glyph of a missing character.
SHEET_NO_PAPER = 0x7F

# The files of a TrueType font for pxfont.py: the directory, and the name of the glyph of a
# missing char, which has the place SHEET_NO_PAPER in the bitmaps.
PXFONT_SUFFIX = ".ttf.files"
NOTDEF = ".notdef"


# КОИ-7 Н2: uppercase Cyrillic occupies 0x60..0x7F
KOI7_CYRILLIC = "ЮАБЦДЕФГХИЙКЛМНОПЯРСТУЖВЬЫЗШЭЩЧЪ"

# The three sets of glyphs. agat7-1, the Agat-7's own, has ASCII at 0x20..0x5F and the
# Cyrillic capitals at 0x60..0x7F. agat7-2 and agat9 have graphics at 0x00..0x3F, the small
# letters at 0x40..0x7F, signs at 0x80..0x9F, ASCII at 0xA0..0xDF, the dollar drawn as the
# currency sign, and the Cyrillic capitals at 0xE0..0xFF; agat9 has other graphics, and in
# place of the signs a copy of its Latin capitals, but for a dot and the capital Yo.
#
# A set is told by the glyphs that it repeats: (first code, first code of the copy, count).
REPEATS = {
    "agat7-1": ((0x20, 0x00, 0x20), (0x00, 0x80, 0x80)),
    "agat9": ((0xC0, 0x80, 0x1E),),
}

# The graphics and signs of agat7-2 and agat9, by the code point of the char that each draws:
# the char that it stands for, or one of a like look, for each glyph to be typed. All are in
# the Basic Multilingual Plane, which programs handle better than the planes above it. The
# left blocks 0x1A..0x1F of agat7-2 are 1..6 of 7 columns, as near as eighths come; the
# glyphs that agat9 shares with it have the same code points.
AGAT7_2_GRAPHICS = {
    0x00: 0x25A8, 0x01: 0x259A, 0x02: 0x25A7, 0x03: 0x25A4, 0x04: 0x2592, 0x05: 0x25A5,
    0x06: 0x2504, 0x07: 0x2506, 0x08: 0x2508, 0x09: 0x23BB, 0x0A: 0x25AC, 0x0B: 0x2572,
    0x0C: 0x25E3, 0x0D: 0x2571, 0x0E: 0x25E2, 0x0F: 0x2573,
    0x10: 0x29C4, 0x11: 0x259E, 0x12: 0x29C5, 0x13: 0x2581, 0x14: 0x2582, 0x15: 0x2583,
    0x16: 0x2584, 0x17: 0x2585, 0x18: 0x2586, 0x19: 0x2587, 0x1A: 0x258F, 0x1B: 0x258E,
    0x1C: 0x258D, 0x1D: 0x258C, 0x1E: 0x258B, 0x1F: 0x258A,
    0x21: 0x256D, 0x22: 0x256E, 0x23: 0x2570, 0x24: 0x256F, 0x25: 0x250C, 0x26: 0x2510,
    0x27: 0x2514, 0x28: 0x2518, 0x29: 0x2500, 0x2A: 0x2502, 0x2B: 0x251C, 0x2C: 0x2524,
    0x2D: 0x252C, 0x2E: 0x2534, 0x2F: 0x253C,
    0x31: 0x25DC, 0x32: 0x25DD, 0x33: 0x25DF, 0x34: 0x25DE, 0x35: 0x250F, 0x36: 0x2513,
    0x37: 0x2517, 0x38: 0x251B, 0x39: 0x2501, 0x3A: 0x2503, 0x3B: 0x2523, 0x3C: 0x252B,
    0x3D: 0x2533, 0x3E: 0x253B, 0x3F: 0x254B,
    0x80: 0x263A, 0x81: 0x00B7, 0x82: 0x2B1E, 0x83: 0x22A1, 0x84: 0x29C8, 0x85: 0x25A1,
    0x86: 0x2610, 0x87: 0x2302, 0x88: 0x2190, 0x89: 0x00AC, 0x8A: 0x2200, 0x8B: 0x2203,
    0x8C: 0x2196, 0x8D: 0x2913, 0x8E: 0xC6C3, 0x8F: 0xC637,
    0x90: 0x2300, 0x91: 0x2660, 0x92: 0x2663, 0x93: 0x2666, 0x94: 0x2665, 0x95: 0x2192,
    0x96: 0x046A, 0x97: 0x046C, 0x98: 0x2199, 0x99: 0x2191, 0x9A: 0x2193, 0x9B: 0x00B0,
    0x9C: 0x02F3, 0x9D: 0x2197, 0x9E: 0x2198, 0x9F: 0x04DC,
}
AGAT9_GRAPHICS = {
    0x01: 0x2582, 0x02: 0x2584, 0x03: 0x2587, 0x04: 0x2228, 0x05: 0x00AC, 0x06: 0x2200,
    0x07: 0x2203, 0x08: 0x2190, 0x09: 0x256D, 0x0A: 0x256E, 0x0B: 0x256F, 0x0C: 0x2570,
    0x0D: 0x2913, 0x0E: 0x2198, 0x0F: 0x0451,
    0x10: 0x250C, 0x11: 0x252C, 0x12: 0x2510, 0x13: 0x251C, 0x14: 0x253C, 0x15: 0x2192,
    0x16: 0x2197, 0x17: 0x2199, 0x18: 0x2196, 0x19: 0x2191, 0x1A: 0x2193, 0x1B: 0x2500,
    0x1C: 0x2524, 0x1D: 0x2514, 0x1E: 0x2534, 0x1F: 0x2518,
    0x21: 0x03B1, 0x22: 0x03B2, 0x23: 0x03B3, 0x24: 0x03B4, 0x25: 0x03BB, 0x26: 0x03BC,
    0x27: 0x03C0, 0x28: 0x03C1, 0x29: 0x03C4, 0x2A: 0x03C6, 0x2B: 0x03C8, 0x2C: 0x03C9,
    0x2D: 0x03A3, 0x2E: 0x0394, 0x2F: 0x00F7,
    0x30: 0x2070, 0x31: 0x00B9, 0x32: 0x00B2, 0x33: 0x00B3, 0x34: 0x2074, 0x35: 0x2075,
    0x36: 0x2076, 0x37: 0x2077, 0x38: 0x2078, 0x39: 0x2079, 0x3A: 0x207B, 0x3B: 0x2261,
    0x3C: 0x2264, 0x3D: 0x2260, 0x3E: 0x2265, 0x3F: 0x00B1,
    0x9E: 0x00B7, 0x9F: 0x0401,
}
FULL_BLOCK = 0x2588  # at 0x5F, where ASCII has DEL
CURRENCY_SIGN = 0x00A4  # for the dollar, which every set draws so
# The bar of agat7-2 is drawn broken, and that of agat9 is its line of the box drawing too.
BARS = {"agat7-2": 0x00A6, "agat9": 0x2502}


def chart_layout(glyphs):
    """The set that a chart holds, told by the glyphs that it repeats: agat7-1, agat9, or
    agat7-2, which repeats none. A chart that repeats most of the glyphs of a set but not all
    of them is refused: a glyph fixed in one copy only."""
    for layout, repeats in REPEATS.items():
        pairs = [(code + i, copy + i) for code, copy, count in repeats for i in range(count)]
        differ = [copy for code, copy in pairs if glyphs[code] != glyphs[copy]]
        if not differ:
            return layout
        if 4 * len(differ) <= len(pairs):
            sys.exit("The chart repeats the glyphs of the %s set but these, which differ from"
                " their originals: %s" % (layout, " ".join("%02X" % code for code in differ)))
    return "agat7-2"


def layout_chars(layout):
    """Map each code of a set to the chars that its glyph stands for."""
    if layout == "agat7-1":
        chars = dict((code, chr(code)) for code in range(0x20, 0x60))
        chars.update((0x60 + i, ch) for i, ch in enumerate(KOI7_CYRILLIC))
        dollar = 0x24
    else:
        graphics = AGAT7_2_GRAPHICS if layout == "agat7-2" else AGAT9_GRAPHICS
        chars = dict((code, chr(point)) for code, point in graphics.items())
        chars.update((code, chr(code + 0x20)) for code in range(0x40, 0x5F))
        chars[0x5C] += chr(BARS[layout])
        chars[0x5F] = chr(FULL_BLOCK)
        chars.update((0x60 + i, ch) for i, ch in enumerate(KOI7_CYRILLIC.lower()))
        chars.update((code, chr(code - 0x80)) for code in range(0xA0, 0xE0))
        chars.update((0xE0 + i, ch) for i, ch in enumerate(KOI7_CYRILLIC))
        dollar = 0xA4
    chars[dollar] += chr(CURRENCY_SIGN)
    return chars


def chart_chars(glyphs):
    """Map each code of a chart to the chars that its glyph stands for, after checking that
    every glyph with ink, but for the copies that its set repeats, stands for chars, and no
    char for two glyphs or beyond the Basic Multilingual Plane."""
    layout = chart_layout(glyphs)
    chars = layout_chars(layout)
    copies = set(copy + i for _code, copy, count in REPEATS.get(layout, ())
        for i in range(count))
    lost = [code for code in range(256) if code not in copies and code not in chars
        and any(any(row) for row in glyphs[code])]
    if lost:
        sys.exit("The %s set has no char for the glyphs %s" % (layout,
            " ".join("%02X" % code for code in lost)))
    seen = {}
    for code, text in sorted(chars.items()):
        for ch in text:
            if ch in seen:
                sys.exit("The %s set has U+%04X for both %02X and %02X"
                    % (layout, ord(ch), seen[ch], code))
            if ord(ch) > 0xFFFF:
                sys.exit("The %s set has U+%04X for %02X, beyond the Basic Multilingual Plane"
                    % (layout, ord(ch), code))
            seen[ch] = code
    return chars


def build_charmap(glyphs):
    """Map a character to the code of its glyph; a small letter that the chart lacks, to the
    glyph of its capital."""
    m = {}
    for code, chars in chart_chars(glyphs).items():
        for ch in chars:
            m.setdefault(ch, code)
    for ch, code in list(m.items()):
        m.setdefault(ch.lower(), code)
    return m


def load_font(image_path):
    """Return 256 glyphs, each an 8-row list of 7 bits; index == char code.

    The chart holds 16x16 cells on a 15x17 px pitch with blue grid lines
    between them. Each cell is 14x16 px: a 7x8 glyph drawn at 2x scale,
    so every second pixel is sampled. The origin is (15, 17), past the
    yellow label row and column.

    The main chart holds the codes 0x20..0x7F alone, in 6 rows from the
    origin (1, 1). The other codes get the glyphs that the full chart
    repeats for them: a code above 0x7F is as the code without its high
    bit, and a code below 0x20 is as the code 0x20 above it.
    """
    try:
        img = Image.open(image_path).convert("L")
    except FileNotFoundError:
        sys.exit(f"{image_path}: no such file")
    except OSError as error:
        sys.exit(f"{image_path} cannot be read as an image: {error}")
    if (img.width, img.height) == MAIN_SIZE:
        main = read_cells(img, MAIN_ORIGIN, MAIN_ROWS)
        return [main[code & 0x7F if code & 0x7F < 0x20 else (code & 0x7F) - 0x20]
            for code in range(256)]
    if (img.width, img.height) != (254, 288):
        print(f"Warning: expected 254x288, got {img.width}x{img.height}", file=sys.stderr)
    return read_cells(img, (ORIGIN_X, ORIGIN_Y), ROWS)


def read_cells(img, origin, rows):
    """Return the glyphs of the cells of a chart, row by row."""
    glyphs = []
    for row in range(rows):
        for col in range(COLS):
            start_x = origin[0] + col * CELL_W
            start_y = origin[1] + row * CELL_H
            glyph = []
            for py in range(CHAR_H):
                bits = []
                for px in range(CHAR_W):
                    value = img.getpixel((start_x + px * 2, start_y + py * 2))
                    bits.append(1 if value > 128 else 0)
                glyph.append(bits)
            glyphs.append(glyph)
    return glyphs


def write_c(glyphs, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Agat-7 font data - 7x8 characters, 256 glyphs, index == code\n")
        f.write("// Generated from bitmap image\n\n")
        f.write("uint8_t agat7_font[256][8] = {\n")
        for i, glyph in enumerate(glyphs):
            code = i + FIRST_CODE
            name = chr(code) if 32 <= code <= 126 else '?'
            f.write(f"    // Character {code} ('{name}')\n    {{\n")
            for bits in glyph:
                f.write("        0b" + "".join(str(b) for b in bits) + "0,\n")
            f.write("    },\n\n" if i < len(glyphs) - 1 else "    }\n")
        f.write("};\n")
    print(f"Font data written to {output_path}")


def render(glyphs, text, scale=1, spacing=0, margin=0, invert=False):
    charmap = build_charmap(glyphs)

    indices = []
    for ch in text:
        if ch not in charmap:
            print(f"Warning: no glyph for {ch!r}, substituting space", file=sys.stderr)
            indices.append(0x20)
        else:
            indices.append(charmap[ch])

    step = CHAR_W + spacing
    w = len(indices) * step - spacing + 2 * margin
    h = CHAR_H + 2 * margin
    if w <= 0:
        w = 1

    fg, bg = (0, 255) if not invert else (255, 0)
    img = Image.new("L", (w, h), bg)

    for n, idx in enumerate(indices):
        glyph = glyphs[idx]
        ox = margin + n * step
        for y in range(CHAR_H):
            for x in range(CHAR_W):
                if glyph[y][x]:
                    img.putpixel((ox + x, margin + y), fg)

    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    return img


def render_charmap(glyphs, scale=1, gap=0):
    """Whole set as a 16x16 grid of 7x8 glyphs, butted together by default."""
    step_x, step_y = CHAR_W + gap, CHAR_H + gap
    img = Image.new("L", (COLS * step_x, ROWS * step_y), 255)
    for i, glyph in enumerate(glyphs):
        ox = (i % COLS) * step_x
        oy = (i // COLS) * step_y
        for y in range(CHAR_H):
            for x in range(CHAR_W):
                if glyph[y][x]:
                    img.putpixel((ox + x, oy + y), 0)
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    return img


def cp1251_glyphs(glyphs):
    """Map a CP1251 code to the glyph of the character that it stands for."""
    m = {}
    for code, chars in chart_chars(glyphs).items():
        for ch in chars:
            try:
                m.setdefault(ch.encode("cp1251")[0], glyphs[code])
            except UnicodeEncodeError:
                pass  # a char that CP1251 lacks
    return m


def render_cp1251(glyphs):
    """The font as a sheet for pxfont.py: the codes 0x20..0xFF of CP1251, 32 to a row.

    Each glyph is in a 7x8 cell whose paper is white or light gray, by turns
    along a row and down a column, and its ink is black. A code that has no
    Agat-7 glyph gets a box, or is blank; the cell of 0x7F has no paper.
    """
    img = Image.new("RGBA", (SHEET_COLS * CHAR_W, SHEET_ROWS * CHAR_H), (0, 0, 0, 0))
    known = cp1251_glyphs(glyphs)
    blank = [[0] * CHAR_W for _ in range(CHAR_H)]
    for code in range(SHEET_FIRST_CODE, SHEET_FIRST_CODE + SHEET_COLS * SHEET_ROWS):
        glyph = blank if code in SHEET_BLANK else known.get(code, SHEET_BOX)
        row, col = divmod(code - SHEET_FIRST_CODE, SHEET_COLS)
        paper = (0, 0, 0, 0) if code == SHEET_NO_PAPER else SHEET_PAPERS[(row + col) % 2]
        for y in range(CHAR_H):
            for x in range(CHAR_W):
                img.putpixel((col * CHAR_W + x, row * CHAR_H + y),
                    SHEET_INK if glyph[y][x] else paper)
    return img


def hex_ranges(points):
    """Code points as pxfont.py lists them: "0020..007E, 00A0"."""
    runs = []
    for point in sorted(points):
        if runs and runs[-1][1] == point - 1:
            runs[-1][1] = point
        else:
            runs.append([point, point])
    return ", ".join("%04X" % low if low == high else "%04X..%04X" % (low, high)
        for low, high in runs)


def pxfont_rows(points):
    """The places of the bitmaps of a font of these code points, row by row, as pxfont.py
    lays them out: a char that CP1251 has in the place of its code there, the glyph of a
    missing char in the place SHEET_NO_PAPER, then the other chars by code point, 32 to a
    row with no gaps. A row of no place is left out, and so are the empty places that end
    one."""
    placed, others = {SHEET_NO_PAPER: NOTDEF}, []
    for point in sorted(points):
        try:
            code = chr(point).encode("cp1251")[0]
        except UnicodeEncodeError:
            others.append(point)
            continue
        if code in placed:
            sys.exit(f"U+{point:04X} takes the place {code:#04x}, which is taken already")
        placed[code] = point
    rows = []
    for base in range(0, 256, SHEET_COLS):
        row = [placed.get(code) for code in range(base, base + SHEET_COLS)]
        while row and row[-1] is None:
            row.pop()
        if row:
            rows.append(row)
    return rows + [others[at:at + SHEET_COLS] for at in range(0, len(others), SHEET_COLS)]


def write_pxfont_ttf(glyphs, directory):
    """The chart as the files of a TrueType font for pxfont.py: its ttf.json, and the PNG
    that it names, of the chars that the glyphs of the chart stand for. The cells of the PNG
    have the papers and the ink of the CP1251 sheet."""
    family = os.path.basename(os.path.normpath(directory))[:-len(PXFONT_SUFFIX)]
    glyph_of = dict((point, [[0] * CHAR_W for _ in range(CHAR_H)]) for point in BLANK_CHARS)
    for code, chars in chart_chars(glyphs).items():
        for ch in chars:
            glyph_of[ord(ch)] = glyphs[code]
    rows = pxfont_rows(glyph_of)
    img = Image.new("RGBA", (SHEET_COLS * CHAR_W, len(rows) * CHAR_H), (0, 0, 0, 0))
    for r, row in enumerate(rows):
        for c, place in enumerate(row):
            if place is None:
                continue
            glyph = SHEET_BOX if place == NOTDEF else glyph_of[place]
            paper = (0, 0, 0, 0) if place == NOTDEF else SHEET_PAPERS[(r + c) % 2]
            for y in range(CHAR_H):
                for x in range(CHAR_W):
                    img.putpixel((c * CHAR_W + x, r * CHAR_H + y),
                        SHEET_INK if glyph[y][x] else paper)
    # A cell of 7x8 pixels has its baseline under the 7th row, and the em is the cell.
    model = {
        "names": {"family": family},
        "units_per_pixel": 64,
        "em": CHAR_H,
        "ascent": CHAR_H - 1,
        "descent": 1,
        "line_gap": 0,
        "rows_above_baseline": CHAR_H - 1,
        "rows_below_baseline": 1,
        "weight": 400,
        "bold": False,
        "italic": False,
        "underline_position": -1,
        "underline_thickness": 1,
        "png": family + ".png",
        "txt": None,
        "codepage": "cp1251",
        "codepage_patches": {},
        "chars": hex_ranges(glyph_of),
        "zero_width": "",
        "aliases": {},
        "glyphs": [NOTDEF],
    }
    backup = os.path.normpath(directory) + ".BAK"
    if os.path.exists(directory):
        if os.path.exists(backup):
            sys.exit(f"Both {directory} and {backup} exist: move one of them away first")
        os.rename(directory, backup)
        print(f"Renamed the existing {directory} to {backup}")
    os.mkdir(directory)
    img.save(os.path.join(directory, model["png"]))
    with open(os.path.join(directory, "ttf.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(model, indent=2) + "\n")
    print(f"Files of a TrueType font of the {chart_layout(glyphs)} set, {len(glyph_of)} chars,"
        f" written to {directory}")


def default_output(image_path):
    """The files of a TrueType font of a chart, beside it, named as pxfont.py names a font:
    by its face, the name of the chart, and its size."""
    face = os.path.splitext(os.path.basename(image_path))[0].strip()
    return os.path.normpath(os.path.join(os.path.dirname(image_path),
        f"{face} {CHAR_W}x{CHAR_H}px{PXFONT_SUFFIX}"))


def main():
    ap = argparse.ArgumentParser(description="Agat-7 font converter / text renderer")
    ap.add_argument("input_image")
    ap.add_argument("output", nargs="?",
        help="unless given, the files of a TrueType font beside the input, as of mode 4")
    ap.add_argument("--text")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--spacing", type=int, default=0)
    ap.add_argument("--margin", type=int, default=0)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--charmap", action="store_true",
        help="render the full glyph set instead of text")
    ap.add_argument("--gap", type=int, default=0,
        help="blank pixels between glyphs in --charmap output")
    args = ap.parse_args()
    if args.output is None:
        if args.charmap or args.text is not None:
            ap.error("--charmap and --text need an output file")
        args.output = default_output(args.input_image)

    glyphs = load_font(args.input_image)

    if args.charmap:
        img = render_charmap(glyphs, args.scale, args.gap)
        img.save(args.output)
        print(f"Charset written to {args.output} ({img.width}x{img.height})")
    elif args.text is not None:
        img = render(glyphs, args.text, args.scale, args.spacing, args.margin, args.invert)
        img.save(args.output)
        print(f"Text written to {args.output} ({img.width}x{img.height})")
    elif os.path.normpath(args.output).lower().endswith(PXFONT_SUFFIX):
        write_pxfont_ttf(glyphs, args.output)
    elif args.output.lower().endswith(".png"):
        img = render_cp1251(glyphs)
        img.save(args.output)
        print(f"CP1251 sheet written to {args.output} ({img.width}x{img.height})")
    else:
        write_c(glyphs, args.output)


if __name__ == "__main__":
    main()
