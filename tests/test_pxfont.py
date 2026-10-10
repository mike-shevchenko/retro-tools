"""
Tests for the BDF fonts of pxfont.py, on a small font written here that has what the format
allows and packing does not make by itself: comments before STARTFONT, a quote in a string,
a stated FONTBOUNDINGBOX, a glyph of another name, one of a box larger than its ink, one of
a code taken, one without a code and one without a width.

Run from the repository root as "python -m unittest discover tests".
"""
# Written with the help of Claude Opus 5.5.

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pxfont  # noqa: E402

TINY = """\
COMMENT Before STARTFONT
STARTFONT 2.1
COMMENT After STARTFONT
FONT -test-tiny-medium-r-normal--8-80-72-72-c-40-iso8859-1
SIZE 8 72 72
FONTBOUNDINGBOX 4 8 0 -2
STARTPROPERTIES 4
FONT_ASCENT 6
FONT_DESCENT 2
COPYRIGHT "A ""quoted"" word"
_EXTRA 1
ENDPROPERTIES
CHARS 6
STARTCHAR space
ENCODING 32
SWIDTH -1 0
DWIDTH 4 0
BBX 0 0 0 0
BITMAP
ENDCHAR
STARTCHAR char65
ENCODING 65
SWIDTH -1 0
DWIDTH 4 0
BBX 3 5 0 0
BITMAP
40
A0
E0
A0
A0
ENDCHAR
STARTCHAR char103
ENCODING 103
SWIDTH -1 0
DWIDTH 4 0
BBX 4 6 0 -2
BITMAP
00
60
A0
60
20
C0
ENDCHAR
STARTCHAR char769
ENCODING 769
SWIDTH -1 0
DWIDTH 0 0
BBX 0 0 0 0
BITMAP
ENDCHAR
STARTCHAR other65
ENCODING 65
SWIDTH -1 0
DWIDTH 4 0
BBX 2 2 1 1
BITMAP
C0
C0
ENDCHAR
STARTCHAR blob
ENCODING -1
SWIDTH -1 0
DWIDTH 4 0
BBX 3 3 0 0
BITMAP
E0
A0
E0
ENDCHAR
ENDFONT
"""


@unittest.skipIf(pxfont.Image is None, "pxfont needs Pillow")
class BdfTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.path = self.write("tiny.bdf", TINY)

    def write(self, name, text):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="ascii", newline="\n") as handle:
            handle.write(text)
        return path

    def run_verb(self, verb, *args):
        """Run a verb; return its exit status and what it printed."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = verb(*args)
        return status, output.getvalue()

    def model(self):
        with open(os.path.join(self.path + ".files", "bdf.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_unpack_gives_original_back(self):
        status, output = self.run_verb(pxfont.unpack, self.path, None, None)
        self.assertEqual(status, 0, output)
        self.assertIn("byte for byte", output)
        model = self.model()
        self.assertEqual(model["comments_before_startfont"], ["Before STARTFONT"])
        self.assertEqual(model["font_bounding_box"], [4, 8, 0, -2])
        self.assertIn(["COPYRIGHT", 'A "quoted" word'], model["properties"])
        self.assertFalse(model["unicode"])
        self.assertEqual(model["codepage"], "iso8859-1")
        self.assertEqual(model["chars"], "32, 65, 103")
        self.assertEqual(model["zero_width"], "769")
        self.assertEqual(model["glyphs"], ["other65", "blob"])
        self.assertEqual(model["glyph_names"], "char%d")
        self.assertEqual(model["swidth"], "-1 0")
        self.assertEqual(model["overrides"], {"32": {"STARTCHAR": "space"},
            "103": {"BBX": "4 6 0 -2"}, "other65": {"ENCODING": "65"}})

    def test_edited_glyph_gets_new_box(self):
        self.run_verb(pxfont.unpack, self.path, None, None)
        files = self.path + ".files"
        model = self.model()
        os.remove(os.path.join(files, model["png"]))
        text = os.path.join(files, model["txt"])
        with open(text, encoding="ascii") as handle:
            lines = handle.read().splitlines()
        # The second row of chars has an empty place, then the A, whose top row is blank.
        self.assertEqual(lines[9], "|....")
        lines[9] = "|X..."
        with open(text, "w", encoding="ascii", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        status, output = self.run_verb(pxfont.bdf, files, None, None, None, False)
        self.assertEqual(status, 0, output)
        with open(self.path, encoding="ascii") as handle:
            packed = handle.read()
        self.assertIn("STARTCHAR char65\nENCODING 65\nSWIDTH -1 0\nDWIDTH 4 0\nBBX 3 6 0 0\n"
            "BITMAP\n80\n40\nA0\nE0\nA0\nA0\nENDCHAR\n", packed)
        self.assertEqual(packed.replace("BBX 3 6 0 0\nBITMAP\n80\n", "BBX 3 5 0 0\nBITMAP\n"),
            TINY)

    def test_bold_names_the_font_bold(self):
        status, output = self.run_verb(pxfont.bold, self.path, None, None)
        self.assertEqual(status, 0, output)
        with open(os.path.join(self.directory, "tiny Bold.bdf"), encoding="ascii") as handle:
            packed = handle.read()
        self.assertIn("FONT -test-tiny-Bold-r-normal--8-80-72-72-c-40-iso8859-1\n", packed)
        self.assertIn("STARTCHAR char65\nENCODING 65\nSWIDTH -1 0\nDWIDTH 4 0\nBBX 4 5 0 0\n"
            "BITMAP\n60\nF0\nF0\nF0\nF0\nENDCHAR\n", packed)

    def test_bdf_of_fon_unpacks_to_same_font(self):
        name = os.path.join(self.directory, "blank 4x6px")
        self.run_verb(pxfont.create, name, None)
        status, output = self.run_verb(pxfont.fon, name + ".fon.files", None, None, False)
        self.assertEqual(status, 0, output)
        status, output = self.run_verb(pxfont.bdf, name + ".fon", None, None, "98=00A3", True)
        self.assertEqual(status, 0, output)
        self.assertIn("byte for byte", output)
        with open(os.path.join(self.directory, "blank 4x6px.bdf"), encoding="ascii") as handle:
            packed = handle.read()
        self.assertIn("\nCHARSET_REGISTRY \"ISO10646\"\n", packed)
        self.assertIn("\nSTARTCHAR uni00A3\nENCODING 163\n", packed)

    @unittest.skipIf(pxfont.FontBuilder is None, "needs fontTools")
    def test_ttf_of_bdf(self):
        status, output = self.run_verb(pxfont.ttf, self.path, None, None, None, None, None,
            True)
        self.assertEqual(status, 0, output)
        self.assertIn("3 chars", output)
        self.assertIn("same glyphs and metrics", output)

    def test_vertical_font_is_refused(self):
        path = self.write("tall.bdf", TINY.replace("CHARS 6", "METRICSSET 1\nCHARS 6"))
        with self.assertRaises(pxfont.Failure):
            self.run_verb(pxfont.unpack, path, None, None)


if __name__ == "__main__":
    unittest.main()
