"""
Tests for nic2hfe.py, on the Agat 140 KB disk KLAD140 in tests/data:

- KLAD140.dsk: the sectors, in DOS 3.3 order.
- KLAD140.nic: the same disk as SDISK II bit streams.
- KLAD140.hfe: KLAD140.nic as nic2hfe.py converted it, the reference for its output.
- KLAD140_gw.hfe: the disk as Greaseweazle converted it, a reference independent of nic2hfe.py.

Run from the repository root as "python -m unittest discover tests".
"""
# Written with the help of Claude Opus 5.5.

import contextlib
import io
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
sys.path.insert(0, os.path.dirname(HERE))

import nic2hfe  # noqa: E402

# The logical sector, as a .dsk file orders them, of each physical sector on a track.
DOS_33_LOGICAL_SECTOR = [0, 7, 14, 6, 13, 5, 12, 4, 11, 3, 10, 2, 9, 1, 8, 15]
SECTOR_BYTES = 256


def data_path(name):
    return os.path.join(DATA, name)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def dsk_sector(dsk, track, physical_sector):
    offset = (track * nic2hfe.SECTORS + DOS_33_LOGICAL_SECTOR[physical_sector]) * SECTOR_BYTES
    return dsk[offset:offset + SECTOR_BYTES]


class ConversionTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)

    def convert(self, nic_path, *options):
        """Run the tool on a copy of the image; return the exit status, output and HFE path."""
        copy = os.path.join(self.directory, "IMAGE.NIC")
        shutil.copyfile(nic_path, copy)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = nic2hfe.main([*options, copy])
        return status, output.getvalue(), os.path.join(self.directory, "IMAGE.hfe")

    def test_output_matches_reference_hfe(self):
        status, output, hfe = self.convert(data_path("KLAD140.nic"))
        self.assertEqual(status, 0, output)
        self.assertIn("verify: OK", output)
        self.assertEqual(read(hfe), read(data_path("KLAD140.hfe")))

    def test_header_is_single_sided_double_stepped(self):
        status, _, hfe = self.convert(data_path("KLAD140.nic"), "--no-verify", "--rate", "300")
        self.assertEqual(status, 0)
        header = read(hfe)[:nic2hfe.BLOCK]
        self.assertEqual(header[:8], b"HXCPICFE")
        self.assertEqual(header[9], nic2hfe.TRACKS)  # cylinders
        self.assertEqual(header[10], 1)  # sides
        self.assertEqual(struct.unpack_from("<H", header, 12)[0], 300)  # rate
        self.assertEqual(header[21], 0)  # single step off
        self.assertEqual(header[26:], b"\xFF" * (nic2hfe.BLOCK - 26))

    def test_wrong_size_is_rejected_without_writing(self):
        short = os.path.join(self.directory, "short.nic")
        with open(short, "wb") as f:
            f.write(read(data_path("KLAD140.nic"))[:-1])
        status, output, hfe = self.convert(short)
        self.assertEqual(status, 2)
        self.assertIn("not the 286720 of a .nic image", output)
        self.assertFalse(os.path.exists(hfe))

    def test_missing_file_is_reported_in_one_line(self):
        missing = os.path.join(self.directory, "missing.nic")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            status = nic2hfe.main([missing])
        self.assertEqual(status, 2)
        self.assertEqual(output.getvalue().count("\n"), 1, output.getvalue())

    def test_input_named_hfe_is_not_overwritten(self):
        victim = os.path.join(self.directory, "image.hfe")
        shutil.copyfile(data_path("KLAD140.nic"), victim)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            status = nic2hfe.main([victim])
        self.assertEqual(status, 2)
        self.assertEqual(read(victim), read(data_path("KLAD140.nic")))

    def test_verify_reports_damaged_source_sector(self):
        nic = bytearray(read(data_path("KLAD140.nic")))
        track, block = 17, 5
        # Flip one bit in the data field, which starts well past the first 100 bytes.
        nic[(track * nic2hfe.SECTORS + block) * nic2hfe.BLOCK + 200] ^= 0x10
        damaged = os.path.join(self.directory, "damaged.nic")
        with open(damaged, "wb") as f:
            f.write(nic)
        status, output, _ = self.convert(damaged)
        self.assertEqual(status, 1)
        self.assertIn(f"track {track}: 15 of 16 sectors good in the source", output)
        self.assertIn("verify: FAILED", output)

    def test_verify_reports_hfe_that_differs(self):
        tracks = nic2hfe.read_nic(data_path("KLAD140.nic"))
        hfe = os.path.join(self.directory, "other.hfe")
        nic2hfe.write_hfe(hfe, tracks[:1] + tracks[:-1], nic2hfe.DEFAULT_RATE_KBPS)
        problems = nic2hfe.verify(tracks, hfe)
        self.assertEqual(len(problems), nic2hfe.TRACKS - 1)
        self.assertEqual(problems[0], "track 1: HFE differs from the source")


class DecodingTest(unittest.TestCase):

    def assert_holds_dsk(self, tracks):
        dsk = read(data_path("KLAD140.dsk"))
        self.assertEqual(len(tracks), nic2hfe.TRACKS)
        for track, bits in enumerate(tracks):
            found = nic2hfe.sectors(bits)
            self.assertEqual(
                sorted(found), [(track, sector) for sector in range(nic2hfe.SECTORS)])
            for (_, sector), data in found.items():
                self.assertEqual(data, dsk_sector(dsk, track, sector), (track, sector))

    def test_nic_holds_dsk_sectors(self):
        tracks = nic2hfe.read_nic(data_path("KLAD140.nic"))
        self.assert_holds_dsk([nic2hfe.disk_bits(track) for track in tracks])

    def test_reference_hfe_holds_dsk_sectors(self):
        self.assert_holds_dsk(nic2hfe.read_hfe(data_path("KLAD140.hfe")))

    def test_greaseweazle_hfe_holds_dsk_sectors(self):
        self.assert_holds_dsk(nic2hfe.read_hfe(data_path("KLAD140_gw.hfe")))

    def test_address_field_uses_volume_254(self):
        track = nic2hfe.nibbles(nic2hfe.read_hfe(data_path("KLAD140.hfe"))[0])
        address = track.find(nic2hfe.ADDRESS_PROLOGUE) + len(nic2hfe.ADDRESS_PROLOGUE)
        self.assertEqual(nic2hfe.decode_4_and_4(track[address], track[address + 1]), 254)

    def test_cells_of_byte(self):
        self.assertEqual(nic2hfe.CELLS_OF_BYTE[0x00], b"\x00\x00")
        self.assertEqual(nic2hfe.CELLS_OF_BYTE[0xFF], b"\xAA\xAA")
        self.assertEqual(nic2hfe.CELLS_OF_BYTE[0x80], b"\x02\x00")
        self.assertEqual(nic2hfe.CELLS_OF_BYTE[0x01], b"\x00\x80")

    def test_nibbles_skip_leading_zeros(self):
        bits = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
        self.assertEqual(nic2hfe.nibbles(bits), b"\xFF\xAA")

    def test_bad_checksum_is_rejected(self):
        field = bytes([nic2hfe.NIBBLE_OF_SIX_BITS[0]] * nic2hfe.DATA_FIELD_NIBBLES)
        self.assertEqual(nic2hfe.decode_6_and_2(field), bytes(SECTOR_BYTES))
        self.assertIsNone(nic2hfe.decode_6_and_2(field[:-1] + b"\x97"))
        self.assertIsNone(nic2hfe.decode_6_and_2(field[:-1] + b"\x00"))


if __name__ == "__main__":
    unittest.main()
