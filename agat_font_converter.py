#!/usr/bin/env python3
"""
Agat-7 font tool.
 
Three modes:
 
  1. C export (original behaviour):
         agat_font_converter.py "Agat-7 font.png" font.c
 
  2. Text rendering to PNG:
         agat_font_converter.py "Agat-7 font.png" out.png --text "КИБЕР-МУЗЕЙ, МУРОМ"

  3. Font sheet for fon.py, laid out as CP1251 (an output .png and no other flags):
         agat_font_converter.py "Agat-7 font main.png" "Agat 7x8px.png"

The input is either chart: the full one of 256 labeled cells, or the main one of the
96 glyphs 0x20..0x7F alone.
 
Extra options for mode 2:
  --scale N      output pixels per font pixel (default 1)
  --spacing N    blank font-pixel columns between glyphs (default 0)
  --margin N     blank font-pixel border around the text (default 0)
  --invert       swap foreground/background
  --charmap      dump the whole 96-glyph set with indices, for checking
"""
 
from PIL import Image
import argparse
import sys
 
CHAR_W, CHAR_H = 7, 8          # glyph size in font pixels
CELL_W, CELL_H = 15, 17        # cell pitch in image pixels (2x2 pixels + grid line)
ORIGIN_X, ORIGIN_Y = 15, 17    # first glyph pixel, past the label row/column
COLS, ROWS = 16, 16            # 256 cells; index == character code
FIRST_CODE = 0

# The main chart: the 96 glyphs 0x20..0x7F alone, in 6 rows with no labels.
MAIN_SIZE = (241, 103)
MAIN_ORIGIN = (1, 1)
MAIN_ROWS = 6

# The sheet for fon.py: the codes 0x20..0xFF, 32 to a row, on a checkerboard of papers.
SHEET_COLS, SHEET_ROWS = 32, 7
SHEET_FIRST_CODE = 0x20
SHEET_PAPERS = ((255, 255, 255, 255), (192, 192, 192, 255))
SHEET_INK = (0, 0, 0, 255)
 
 
# КОИ-7 Н2: uppercase Cyrillic occupies 0x60..0x7F
KOI7_CYRILLIC = "ЮАБЦДЕФГХИЙКЛМНОПЯРСТУЖВЬЫЗШЭЩЧЪ"
 
 
def build_charmap():
    """Map a character to its glyph index (0..95)."""
    m = {}
    for code in range(0x20, 0x60):             # punctuation, digits, Latin
        m[chr(code)] = code
    for i, ch in enumerate(KOI7_CYRILLIC):     # Cyrillic at 0x60..0x7F
        m[ch] = 0x60 + i
        m[ch.lower()] = 0x60 + i               # accept lowercase input
    for code in range(0x61, 0x7b):             # Latin lowercase -> uppercase glyph
        m.setdefault(chr(code), code - 32)
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
    img = Image.open(image_path).convert("L")
    if (img.width, img.height) == MAIN_SIZE:
        main = read_cells(img, MAIN_ORIGIN, MAIN_ROWS)
        return [main[code & 0x7F if code & 0x7F < 0x20 else (code & 0x7F) - 0x20]
            for code in range(256)]
    if (img.width, img.height) != (254, 288):
        print(f"Warning: expected 254x288, got {img.width}x{img.height}",
              file=sys.stderr)
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
    charmap = build_charmap()
 
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
    for code in range(0x20, 0x60):             # punctuation, digits, Latin: as they are
        m[code] = glyphs[code]
    for i, ch in enumerate(KOI7_CYRILLIC):     # Cyrillic to its CP1251 codes
        m[ch.encode("cp1251")[0]] = glyphs[0x60 + i]
    return m


def render_cp1251(glyphs):
    """The font as a sheet for fon.py: the codes 0x20..0xFF of CP1251, 32 to a row.

    Each glyph is in a 7x8 cell whose paper is white or light gray, by turns
    along a row and down a column, and its ink is black. The cell of a code
    that has no Agat-7 glyph is left transparent.
    """
    img = Image.new("RGBA", (SHEET_COLS * CHAR_W, SHEET_ROWS * CHAR_H), (0, 0, 0, 0))
    for code, glyph in cp1251_glyphs(glyphs).items():
        row, col = divmod(code - SHEET_FIRST_CODE, SHEET_COLS)
        paper = SHEET_PAPERS[(row + col) % 2]
        for y in range(CHAR_H):
            for x in range(CHAR_W):
                img.putpixel((col * CHAR_W + x, row * CHAR_H + y),
                    SHEET_INK if glyph[y][x] else paper)
    return img


def main():
    ap = argparse.ArgumentParser(description="Agat-7 font converter / text renderer")
    ap.add_argument("input_image")
    ap.add_argument("output")
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
 
    glyphs = load_font(args.input_image)
 
    if args.charmap:
        img = render_charmap(glyphs, args.scale, args.gap)
        img.save(args.output)
        print(f"Charset written to {args.output} ({img.width}x{img.height})")
    elif args.text is not None:
        img = render(glyphs, args.text, args.scale, args.spacing,
                     args.margin, args.invert)
        img.save(args.output)
        print(f"Text written to {args.output} ({img.width}x{img.height})")
    elif args.output.lower().endswith(".png"):
        img = render_cp1251(glyphs)
        img.save(args.output)
        print(f"CP1251 sheet written to {args.output} ({img.width}x{img.height})")
    else:
        write_c(glyphs, args.output)
 
 
if __name__ == "__main__":
    main()
 