"""
Tests for the BDF fonts of pxfont.py, on a small font written here that has what the format
allows and packing does not make by itself: comments before STARTFONT, a quote in a string,
a stated FONTBOUNDINGBOX, a glyph of another name, one of a box larger than its ink, one of
a code taken, one without a code and one without a width. Then tests for merging TrueType
fonts, made of Unicode BDF fonts written here.

Run from the repository root as "python -m unittest discover tests".
"""
# Written with the help of Claude Opus 5.5.

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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


def run_verb(verb, *args):
    """Run a verb; return its exit status and what it printed."""
    class Output(io.StringIO):
        def reconfigure(self, **_kwargs):
            pass

    output = Output()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        status = verb(*args)
    return status, output.getvalue()


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
        return run_verb(verb, *args)

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

    def test_transparent_slots_are_spaces_in_text(self):
        status, output = self.run_verb(pxfont.unpack, self.path, None, None)
        self.assertEqual(status, 0, output)
        model = self.model()
        path = os.path.join(self.path + ".files", model["txt"])
        with open(path, encoding="ascii") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[9], "    |....")
        self.assertEqual(pxfont.text_lines([[769]], {769: (0, [[]])}, 1, 4), ["    "])

    def test_edited_glyph_gets_new_box(self):
        self.run_verb(pxfont.unpack, self.path, None, None)
        files = self.path + ".files"
        model = self.model()
        os.remove(os.path.join(files, model["png"]))
        text = os.path.join(files, model["txt"])
        with open(text, encoding="ascii") as handle:
            lines = handle.read().splitlines()
        # The second row of chars has an empty place, then the A, whose top row is blank.
        self.assertEqual(lines[9], "    |....")
        lines[9] = "    |X..."
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

    @unittest.skipIf(pxfont.FontBuilder is None, "needs fontTools")
    def test_existing_ttf_backup_is_replaced(self):
        status, output = self.run_verb(pxfont.ttf, self.path, None, None, None, None, None,
            False)
        self.assertEqual(status, 0, output)
        font = os.path.join(self.directory, "tiny.ttf")
        backup = font + pxfont.BACKUP_SUFFIX
        with open(font, "rb") as handle:
            previous = handle.read()
        with open(backup, "wb") as handle:
            handle.write(b"stale backup")
        status, output = self.run_verb(pxfont.ttf, self.path, None, None, None, None, None,
            False)
        self.assertEqual(status, 0, output)
        with open(backup, "rb") as handle:
            self.assertEqual(handle.read(), previous)

    def test_vertical_font_is_refused(self):
        path = self.write("tall.bdf", TINY.replace("CHARS 6", "METRICSSET 1\nCHARS 6"))
        with self.assertRaises(pxfont.Failure):
            self.run_verb(pxfont.unpack, path, None, None)


@unittest.skipIf(pxfont.Image is None, "pxfont needs Pillow")
class BitmapTest(unittest.TestCase):
    WHITE = pxfont.CELL_COLORS[0][0]
    GRAY = pxfont.CELL_COLORS[1][0]
    BLACK = pxfont.CELL_COLORS[1][1]

    def test_nonblank_first_cell_gives_prevailing_paper(self):
        image = pxfont.Image.new("RGBA", (4, 2), self.WHITE)
        pixels = image.load()
        for y in range(2):
            for x in range(2, 4):
                pixels[x, y] = self.GRAY
        pixels[0, 1] = self.BLACK
        pixels[3, 0] = self.BLACK
        widths, glyphs = pxfont.read_png_grid("test.png", image, [[32, 33]], 2, 2)
        self.assertEqual(widths, [2, 2])
        self.assertEqual(glyphs, [[[0, 0], [1, 0]], [[0, 1], [0, 0]]])

    def test_invert_swaps_the_heuristic_result(self):
        image = pxfont.Image.new("RGBA", (2, 2), self.BLACK)
        image.load()[0, 0] = self.WHITE
        _widths, glyphs = pxfont.read_png_grid("test.png", image, [[32]], 2, 2)
        self.assertEqual(glyphs[0], [[1, 0], [0, 0]])
        _widths, glyphs = pxfont.read_png_grid("test.png", image, [[32]], 2, 2, True)
        self.assertEqual(glyphs[0], [[0, 1], [1, 1]])

    def test_single_color_first_cell_is_blank_or_ink(self):
        image = pxfont.Image.new("RGBA", (2, 2), self.WHITE)
        _widths, glyphs = pxfont.read_png_grid("test.png", image, [[32]], 2, 2)
        self.assertEqual(glyphs[0], [[0, 0], [0, 0]])
        _widths, glyphs = pxfont.read_png_grid("test.png", image, [[32]], 2, 2, True)
        self.assertEqual(glyphs[0], [[1, 1], [1, 1]])

    def test_invert_option(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "inverse.png")
            image = pxfont.Image.new("RGBA", (64, 2), self.WHITE)
            image.load()[0, 1] = self.BLACK
            image.save(path)
            result = subprocess.run([sys.executable, pxfont.__file__, "fon", "--rows", "1",
                "--invert", path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with open(os.path.splitext(path)[0] + ".fon", "rb") as handle:
                fonts = pxfont.parse_fon(handle.read(), pxfont.Report(quiet=True))[1]
            self.assertEqual(fonts[0][2][0], [[1, 1], [0, 1]])


class BackupTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.target = os.path.join(self.directory, "font")
        self.backup = self.target + pxfont.BACKUP_SUFFIX

    def write(self, path, text):
        with open(path, "w", encoding="ascii") as handle:
            handle.write(text)

    def test_old_file_backup_is_replaced(self):
        self.write(self.target, "current")
        self.write(self.backup, "stale")
        status, output = run_verb(pxfont.back_up, self.target)
        self.assertIsNone(status, output)
        self.assertFalse(os.path.exists(self.target))
        with open(self.backup, encoding="ascii") as handle:
            self.assertEqual(handle.read(), "current")

    def test_old_directory_backup_is_replaced(self):
        os.mkdir(self.target)
        self.write(os.path.join(self.target, "current"), "current")
        os.makedirs(os.path.join(self.backup, "stale"))
        self.write(os.path.join(self.backup, "stale", "old"), "stale")
        status, output = run_verb(pxfont.back_up, self.target)
        self.assertIsNone(status, output)
        self.assertFalse(os.path.exists(self.target))
        self.assertFalse(os.path.exists(os.path.join(self.backup, "stale")))
        with open(os.path.join(self.backup, "current"), encoding="ascii") as handle:
            self.assertEqual(handle.read(), "current")


class WinInstallTest(unittest.TestCase):

    def test_registers_all_font_extensions(self):
        registry = mock.Mock()
        registry.HKEY_CURRENT_USER = object()
        registry.REG_SZ = 1
        registry.REG_EXPAND_SZ = 2
        key, command_key = mock.MagicMock(), mock.MagicMock()
        key.__enter__.return_value = key
        command_key.__enter__.return_value = command_key
        registry.CreateKey.side_effect = [key, command_key] * 3
        with mock.patch.dict(sys.modules, {"winreg": registry}), mock.patch.object(
                pxfont.sys, "platform", "win32"):
            status, output = run_verb(pxfont.win_install)
        self.assertEqual(status, 0, output)
        keys = registry.CreateKey.call_args_list
        self.assertEqual([call.args[1] for call in keys[::2]],
            [pxfont.WIN_MENU_KEY % extension for extension in pxfont.WIN_MENU_EXTENSIONS])
        self.assertEqual([call.args[1] for call in keys[1::2]], ["command"] * 3)
        values = registry.SetValueEx.call_args_list
        self.assertEqual([call.args[0] for call in values], [key, command_key] * 3)
        for extension in pxfont.WIN_MENU_EXTENSIONS:
            self.assertIn(extension, output)


@unittest.skipIf(pxfont.Image is None or pxfont.FontBuilder is None,
    "pxfont needs Pillow and fontTools")
class WinTtfInstallTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.source = os.path.join(self.directory, "tiny.bdf")
        with open(self.source, "w", encoding="ascii", newline="\n") as handle:
            handle.write(TINY)
        self.output = os.path.join(self.directory, "tiny.ttf")
        self.local = os.path.join(self.directory, "local")
        self.fonts = os.path.join(self.local, "Microsoft", "Windows", "Fonts")
        self.installed = os.path.join(self.fonts, "tiny.ttf")

    def run_install(self, previous=None):
        registry = mock.Mock()
        registry.HKEY_CURRENT_USER = object()
        registry.REG_SZ = 1
        key = mock.MagicMock()
        key.__enter__.return_value = key
        registry.CreateKey.return_value = key
        if previous is None:
            registry.QueryValueEx.side_effect = FileNotFoundError
        else:
            registry.QueryValueEx.return_value = (previous, registry.REG_SZ)
        gdi32 = mock.Mock()
        gdi32.AddFontResourceW.return_value = 1
        user32 = mock.Mock()
        argv = ["pxfont", "ttf", "--install", self.source]
        argv_patch = mock.patch.object(pxfont.sys, "argv", argv)
        with mock.patch.dict(sys.modules, {"winreg": registry}), mock.patch.object(
                pxfont.sys, "platform", "win32"), mock.patch.dict(
                os.environ, {"LOCALAPPDATA": self.local}), mock.patch.object(
                pxfont.ctypes, "WinDLL", side_effect=[gdi32, user32], create=True), argv_patch:
            status, output = run_verb(pxfont.main)
        self.registry = registry
        self.key = key
        self.gdi32 = gdi32
        self.user32 = user32
        return status, output

    def test_ttf_installs_generated_font_for_current_user(self):
        status, output = self.run_install()
        self.assertEqual(status, 0, output)
        self.registry.CreateKey.assert_called_once_with(
            self.registry.HKEY_CURRENT_USER, pxfont.WIN_FONTS_KEY)
        self.registry.QueryValueEx.assert_called_once_with(self.key, "tiny (TrueType)")
        self.registry.SetValueEx.assert_called_once_with(
            self.key, "tiny (TrueType)", 0, self.registry.REG_SZ, self.installed)
        with open(self.output, "rb") as handle:
            made = handle.read()
        with open(self.installed, "rb") as handle:
            self.assertEqual(handle.read(), made)
        self.gdi32.RemoveFontResourceW.assert_called_once_with(self.installed)
        self.gdi32.AddFontResourceW.assert_called_once_with(self.installed)
        self.user32.SendMessageTimeoutW.assert_called_once()
        self.assertEqual(os.listdir(self.fonts), ["tiny.ttf"])
        self.assertIn("for the current user", output)

    def test_install_replaces_previous_copy_and_registration(self):
        os.makedirs(self.fonts)
        with open(self.installed, "wb") as handle:
            handle.write(b"previous font")
        status, output = self.run_install(self.installed)
        self.assertEqual(status, 0, output)
        with open(self.output, "rb") as handle:
            made = handle.read()
        with open(self.installed, "rb") as handle:
            installed = handle.read()
        self.assertEqual(installed, made)
        self.assertNotEqual(installed, b"previous font")
        self.gdi32.RemoveFontResourceW.assert_called_once_with(self.installed)
        self.registry.SetValueEx.assert_called_once_with(
            self.key, "tiny (TrueType)", 0, self.registry.REG_SZ, self.installed)

    def test_install_is_refused_outside_windows(self):
        with mock.patch.object(pxfont.sys, "platform", "linux"):
            with self.assertRaisesRegex(pxfont.Failure, "--install is for Windows"):
                pxfont.ttf(self.source, None, None, None, None, None, False, False, True)
        self.assertFalse(os.path.exists(self.output))


def unicode_bdf(family, glyphs, ascent=5):
    """A Unicode BDF font of cells 4 pixels wide and 6 high, ascent of them above the
    baseline, of glyphs by code point, each the rows of its cell in hex from the top."""
    lines = ["STARTFONT 2.1",
        "FONT -test-%s-medium-r-normal--6-60-72-72-c-40-iso10646-1" % family,
        "SIZE 6 72 72", "FONTBOUNDINGBOX 4 6 0 %d" % (ascent - 6), "STARTPROPERTIES 4",
        "FONT_ASCENT %d" % ascent, "FONT_DESCENT %d" % (6 - ascent),
        'CHARSET_REGISTRY "ISO10646"', 'CHARSET_ENCODING "1"', "ENDPROPERTIES",
        "CHARS %d" % len(glyphs)]
    for code, rows in sorted(glyphs.items()):
        lines += ["STARTCHAR uni%04X" % code, "ENCODING %d" % code, "SWIDTH 667 0",
            "DWIDTH 4 0", "BBX 4 6 0 %d" % (ascent - 6), "BITMAP"] + rows + ["ENDCHAR"]
    return "\n".join(lines + ["ENDFONT"]) + "\n"


BLANK = ["00"] * 6
A_ROWS = ["60", "90", "F0", "90", "90", "00"]
B_ROWS = ["E0", "90", "E0", "90", "E0", "00"]
OTHER_B_ROWS = ["F0", "90", "E0", "90", "F0", "00"]
ZHE_ROWS = ["A0", "A0", "40", "A0", "A0", "00"]


class PixelUnitTest(unittest.TestCase):

    def test_coarse_grid_keeps_the_fine_grid_tolerance(self):
        values = list(range(100, 1500, 100)) + [7400]
        self.assertEqual(pxfont.pixel_unit(values, 800), 100)


@unittest.skipIf(pxfont.Image is None or pxfont.FontBuilder is None,
    "pxfont needs Pillow and fontTools")
class MergeTest(unittest.TestCase):
    """A font of A and B, merged with one of another B and of the Cyrillic Zhe."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.first = self.truetype("first", {0x20: BLANK, 0x41: A_ROWS, 0x42: B_ROWS})
        self.second = self.truetype("second", {0x20: BLANK, 0x42: OTHER_B_ROWS,
            0x416: ZHE_ROWS})

    def truetype(self, family, glyphs, ascent=5):
        """The path of a TrueType font made by the verb ttf of a Unicode BDF font."""
        home = os.path.join(self.directory, family)
        os.mkdir(home)
        path = os.path.join(home, family + ".bdf")
        with open(path, "w", encoding="ascii", newline="\n") as handle:
            handle.write(unicode_bdf(family, glyphs, ascent))
        status, output = run_verb(pxfont.ttf, path, None, None, None, None, None, False)
        self.assertEqual(status, 0, output)
        made = [name for name in os.listdir(home) if name.endswith(".ttf")]
        self.assertEqual(len(made), 1, made)
        return os.path.join(home, made[0])

    def font(self, path):
        """What ttf.json stores of a TrueType font, and the cells of its glyphs."""
        with open(path, "rb") as handle:
            model, _sheet, cells = pxfont.parse_ttf(handle.read(), self.fail, path)
        return model, cells

    def merged(self, *sources):
        name = os.path.join(self.directory, "both 4x6px")
        status, output = run_verb(pxfont.merge, name, None, list(sources))
        self.assertEqual(status, 0, output)
        self.assertTrue(os.path.isdir(name + ".ttf.files"))
        return output, self.font(name + ".ttf")

    def test_char_of_two_fonts_is_of_first(self):
        output, (model, cells) = self.merged(self.first, self.second)
        self.assertEqual(model["chars"], "0020, 0041..0042, 0416")
        self.assertEqual(model["names"]["family"], "both 4x6px")
        _model, first = self.font(self.first)
        _model, second = self.font(self.second)
        self.assertEqual(cells["U+0042"], first["U+0042"])
        self.assertNotEqual(cells["U+0042"], second["U+0042"])
        self.assertEqual(cells["U+0416"], second["U+0416"])
        self.assertIn("draws them otherwise: 0042", output)

    def test_directory_is_a_source(self):
        status, output = run_verb(pxfont.unpack, self.second, None, None)
        self.assertEqual(status, 0, output)
        _output, (model, _cells) = self.merged(self.first, self.second + ".files")
        self.assertEqual(model["chars"], "0020, 0041..0042, 0416")

    def test_cells_of_other_rows_are_refused(self):
        lower = self.truetype("lower", {0x20: BLANK, 0x43: B_ROWS}, ascent=4)
        with self.assertRaisesRegex(pxfont.Failure, "cells of merged fonts must be alike"):
            self.merged(self.first, lower)

    def test_font_of_other_kind_is_refused(self):
        bdf = os.path.join(self.directory, "second", "second.bdf")
        with self.assertRaisesRegex(pxfont.Failure, "merge takes TrueType fonts"):
            self.merged(self.first, bdf)


if __name__ == "__main__":
    unittest.main()
