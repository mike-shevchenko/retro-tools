## apple2_disk_converter.py

[apple2_disk_converter.py](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/apple2_disk_converter.py)

This tool converts various floppy disk image formats of 140K Apple2/Agat/Pravetz disks (also known as Disk II).

It supports such formats as .dsk, .do and .po, .nib, .nic and .hfe. It currently does not support a mucm more advanced .woz.

## Retro fonts

Here are some fonts created by [pxfont.py](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/pxfont.py) from raster images.

### pxfont.py

[pxfont.py](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/pxfont.py)

This script unpacks .ttf, .fon or .bdf font into a folder containing the following:
- A .png image with all font glyphs.
- A .json file describing all metadata needed to reconstruct the font.
- A .svg image which shows all font glyphs with their Unicode codepoints and their standard Unicode look in Arial Unicode MS.

Such directories can be then packed back into the font.

The tool is useful to create new fonts from bitmap images (e.g. captured from retro computers), to convert fonts, and to patch existing font glyphs.

It only supports TrueType fonts which have glyphs consisting of rectangular pixels rather than regular curves. All fonts below are produced by this tool.  

### agat_font_converter.py

[agat_font_converter.py](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/agat_font_converter.py)

This script converts Agat character ROM .png images from agatcomp.ru to unpacked fonts (.png with .json) in the format accepted by pxfont.py for further .ttf generation.

### "ZX Spectrum" fonts

[ZX Spectrum 8x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Spectrum.8x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Spectrum.8x8px.svg)

The exact ZX Spectrum font, with all its 96 glyphs. Contains ASCII characters, with the following placement/glyph exceptions corresponding to the Spectrum usage:
- The Spectrum's "arrow up" is drawn in the place of the circumflex.
- The pound sign is drawn in the place of the grave (backtick).
- The copyright sign is placed in its natural codepoint U+00A9, and registered as a substitute glyph for undefined characters.

[ZX Cyr 8x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.8x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.8x8px.svg)

An extended version in the style of the classic ZX Spectrum font. Contains the following:
- Classic ZX Spectrum glyphs for all ASCII positions except the following:
  - Spectrum's "arrow up" is moved to its natural codepoint U+2191, while in its ASCII place the circumflex is drawn in the same style.
  - Spectrum's pound sign is moved to its natural codepoint U+00A3, while in its ASCII place the grave (backtick) is drawn.
- Cyrillic letters and all other characters from the Win-1251 encoding are drawn in the Spectrum style, and placed on their natural codepoints.

[ZX Cyr 8x8px Bold](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.8x8px.Bold.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.8x8px.Bold.svg)

A bold version of the "ZX Cyr 8x8px" font, created strictly by overlaying each glyph with itself shifted to the right by one pixel.

[ZX Cyr 8x8px Italic](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.8x8px.Italic.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.8x8px.Italic.svg)

An italic version of the "ZX Cyr 8x8px" font, created strictly by shifting the upper half of the characters to the right by one pixel.

[ZX Cyr 6x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.6x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/ZX.Cyr.6x8px.svg)

A 6-pixel-wide condensed version of the "ZX Cyr 8x8px" font, with typical letters drawn in 5 pixels with the 6th as an interval.

### Agat fonts

[Agat-7-128 7x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat-7-128.7x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat-7-128.7x8px.svg)

The exact Agat-7 font from its 128-character ROM, with all its glyphs (note that many glyphs in this ROM are duplicates). Contains upper-case English, upper-case Russian except Yo, and some ASCII symbols, with the following placement exceptions:
- The currency sign is drawn in the place of the dollar sign (as on Agat), and duplicated at its natural codepoint U+00A4.

[Agat-7-256 7x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat-7-256.7x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat-7-256.7x8px.svg)

The exact Agat-7 font from its 256-character ROM, with all its glyphs. Contains a full ASCII set, and upper-case and lower-case Russian except Yo/yo, with the following placement exceptions:
- The currency sign is drawn in the place of the dollar sign (as on Agat), and duplicated at its natural codepoint U+00A4.

[Agat-9 7x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat-9.7x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat-9.7x8px.svg)

The exact Agat-9 font, with all its glyphs. Contains a full ASCII set, and upper-case and lower-case Russian with Yo/yo, with the following placement/glyph exceptions corresponding to the Agat usage:
- The currency sign is drawn in the place of the dollar sign, and duplicated at its natural codepoint U+00A4.
- The paragraph sign is drawn in the place of the grave (backtick).
- The tilde has a horizontal line under it.

[Agat 7x8px](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat.7x8px.ttf) [SVG](https://github.com/mike-shevchenko/retro-tools/releases/download/release-1.0/Agat.7x8px.svg)

A combined font for the above fonts, which takes all the characters and glyphs from the Agat-7-128 font, then adds missing characters from Agat-7-256, and then from Agat-9. Note that the characters which are drawn differently in the Agat-9 font, like the tilde and the grave (backtick), have their glyphs from Agat-7-256, since Agat-7-128 has neither.
