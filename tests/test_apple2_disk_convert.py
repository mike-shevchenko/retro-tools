"""
Tests for apple2-disk-convert.py, on disks made up here, since the contents of real disks may
be copyrighted:

- A disk of made-up sectors, each holding its track and DOS 3.3 sector number, then bytes that
  step by 37, so that each sector holds nearly every value and no two sectors match.
- A DOS 3.3 disk with one file, a two-line Applesoft program written here, for what needs a
  disk's catalog. It holds no DOS, so it doesn't boot; booted from another disk, DOS lists it
  and runs the program.
- Fields worked out by hand from the formats, as every real disk checked has them.

Run from the repository root as "python -m unittest discover tests".
"""
# Written with the help of Claude Opus 5.5.

import contextlib
import functools
import importlib
import io
import os
import random
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

tool = importlib.import_module("apple2-disk-convert")

TRACK_BYTES = 16 * 256
NIB_TRACK_BYTES = 6656


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def sector_bytes(track, sector):
    """A made-up sector: its track and DOS 3.3 sector number, then bytes that step by 37."""
    start = track * 16 + sector
    return bytes([track, sector]) + bytes((start + 37 * i) % 256 for i in range(254))


def made_up_dsk(tracks=35):
    return b"".join(sector_bytes(t, s) for t in range(tracks) for s in range(16))


def dsk_sector(dsk, track, physical_sector):
    offset = track * TRACK_BYTES + tool.DOS_ORDER[physical_sector] * 256
    return dsk[offset:offset + 256]


@functools.lru_cache(maxsize=None)
def made_up_disk(volume=254):
    """The made-up disk's sectors, each with the volume number."""
    return tool.with_volume(tool.read_image(made_up_dsk(), "dsk", "x")[0], volume)


@functools.lru_cache(maxsize=None)
def made_up_nic():
    return tool.nic_bytes(made_up_disk())


@functools.lru_cache(maxsize=None)
def made_up_tracks():
    return tool.tracks_of_sector_disk(made_up_disk())


def applesoft(*lines):
    """An Applesoft program as BASIC keeps it in memory from $801 on: for each line the address
    of the next one, its number, its tokens and a 0; two 0s after the last line."""
    program = bytearray()
    for number, tokens in lines:
        line = struct.pack("<H", number) + tokens + b"\0"
        program += struct.pack("<H", 0x801 + len(program) + 2 + len(line)) + line
    return bytes(program) + b"\0\0"


# 10 PRINT "HELLO FROM A MADE-UP DISK" and 20 END; PRINT is the token BA, END the token 80.
HELLO = applesoft((10, b"\xBA\"HELLO FROM A MADE-UP DISK\""), (20, b"\x80"))


def dos33_dsk(volume=254):
    """A DOS 3.3 disk of 35 tracks with one file, HELLO, the Applesoft program above: its VTOC
    and catalog on track 17, the file's track/sector list and its sector of data on track 18,
    every other sector zero."""
    image = bytearray(35 * TRACK_BYTES)

    def put(track, sector, data):
        offset = track * TRACK_BYTES + sector * 256
        image[offset:offset + len(data)] = data

    vtoc = bytearray(256)
    vtoc[1:4] = bytes([17, 15, 3])  # the catalog's first track and sector, the DOS release
    vtoc[6] = volume
    vtoc[0x27] = 122  # track/sector pairs in a list
    vtoc[0x30:0x32] = bytes([18, 1])  # the track allocated last, and the way onward
    vtoc[0x34:0x38] = bytes([35, 16, 0, 1])  # tracks, sectors a track, 256 bytes a sector
    for track in range(35):
        # A bit for each free sector, 15 to 0: the catalog takes track 17, the file two sectors.
        free = {17: 0x0000, 18: 0x3FFF}.get(track, 0xFFFF)
        vtoc[0x38 + 4 * track:0x3A + 4 * track] = free.to_bytes(2, "big")
    put(17, 0, vtoc)
    for sector in range(15, 0, -1):
        put(17, sector, bytes([0, 17, sector - 1]) if sector > 1 else bytes(3))
    name = bytes(c | 0x80 for c in b"HELLO".ljust(30))
    put(17, 15, bytes([0, 17, 14]) + bytes(8) + bytes([18, 15, 0x02]) + name + b"\x02\x00")
    put(18, 15, bytes(12) + bytes([18, 14]))  # the track/sector list
    put(18, 14, struct.pack("<H", len(HELLO)) + HELLO)
    return bytes(image)


def hfe_of_cells(tracks, encoding=0xFF, rate=250):
    """An HFE version 1 of tracks given as strings of cells, as other converters write them."""
    track_list = bytearray()
    body = bytearray()
    for cells in tracks:
        data = tool.hfe_bytes_of_cells(cells + "0" * (-len(cells) % 8))
        track_list += struct.pack("<HH", 2 + len(body) // 512, 2 * len(data))
        for k in range(0, len(data), 256):
            body += data[k:k + 256].ljust(512, b"\x88")
    header = bytearray(tool.hfe_header(len(tracks), rate, 1))
    header[11] = encoding
    return bytes(header) + bytes(track_list).ljust(512, b"\xFF") + bytes(body)


def track_sectors(disk, track):
    """The sectors read from a track of a TrackDisk, by physical number."""
    return tool.read_track(tool.read_latch(disk.tracks[track])[0], track).sectors


class ToolTest(unittest.TestCase):
    """A test with a folder of its own, and the tool run as from the command line."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)

    def path(self, name):
        return os.path.join(self.directory, name)

    def make(self, name, data):
        """Write a file into the test's folder; return its path."""
        path = self.path(name)
        write(path, data)
        return path

    def run_tool(self, *args):
        """Run the tool; return its exit status and all it printed, its lines, which the tool
        wraps, joined."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = tool.main(list(args))
        return status, " ".join(output.getvalue().split())

    def converted(self, source, target, *options):
        """Run a conversion that must succeed; return the target's bytes."""
        status, output = self.run_tool(*options, source, target)
        self.assertEqual(status, 0, output)
        return read(tool.target_of(source, target))


class CodeTest(unittest.TestCase):

    def test_4_and_4_round_trip(self):
        for value in range(256):
            odd, even = tool.encode_4_and_4(value)
            self.assertEqual(odd & 0xAA, 0xAA)
            self.assertEqual(even & 0xAA, 0xAA)
            self.assertEqual(tool.decode_4_and_4(odd, even), value)

    def test_nibbles_are_the_bytes_the_rules_allow(self):
        def allowed(byte):
            bits = format(byte, "08b")
            return (bits[0] == "1" and "000" not in bits and bits.count("00") <= 1
                and "11" in bits[1:] and byte not in (0xAA, 0xD5))
        self.assertEqual(tool.NIBBLE_OF_SIX_BITS, bytes(b for b in range(256) if allowed(b)))

    def test_6_and_2_round_trip(self):
        rng = random.Random(1)
        for _ in range(50):
            data = bytes(rng.randrange(256) for _ in range(256))
            field = tool.encode_6_and_2(data)
            self.assertEqual(len(field), tool.DATA_NIBBLES)
            self.assertEqual(tool.decode_6_and_2(field), data)

    def test_spare_pairs_are_0_or_as_given(self):
        data = bytes([0x01, 0x02]) + bytes(254)
        self.assertEqual(tool.spare_pairs(tool.encode_6_and_2(data)), (0, 0))
        self.assertEqual(tool.spare_pairs(tool.encode_6_and_2(data, (2, 1))), (2, 1))
        self.assertEqual(tool.decode_6_and_2(tool.encode_6_and_2(data, (3, 3))), data)

    def test_bad_checksum_is_rejected(self):
        field = bytes([tool.NIBBLE_OF_SIX_BITS[0]] * tool.DATA_NIBBLES)
        self.assertEqual(tool.decode_6_and_2(field), bytes(256))
        self.assertIsNone(tool.decode_6_and_2(field[:-1] + b"\x97"))
        self.assertIsNone(tool.decode_6_and_2(field[:-1] + b"\x00"))

    def test_latch_skips_leading_zeros(self):
        bits = "0011111111010101010"
        self.assertEqual(tool.read_latch(bits), (b"\xFF\xAA", [2, 11]))

    def test_latch_falls_into_step_on_sync_bytes(self):
        # Started one bit into a byte, the latch reads it wrong, then sync bytes set it right.
        bits = "1" + "1111111100" * 5 + tool.bits_of_bytes(b"\xD5\xAA\x96") + "1111111100" * 5
        nibbles, _ = tool.read_latch(bits[3:] + bits[:3])
        self.assertIn(b"\xFF\xD5\xAA\x96\xFF", nibbles)

    def test_hfe_cells_of_bits(self):
        def cells(byte):
            return tool.hfe_bytes_of_cells(
                tool.bits_of_bytes(bytes([byte])).translate(tool.CELLS_OF_BITS))
        self.assertEqual(cells(0x00), b"\x00\x00")
        self.assertEqual(cells(0xFF), b"\xAA\xAA")
        self.assertEqual(cells(0x80), b"\x02\x00")
        self.assertEqual(cells(0x01), b"\x00\x80")


class KnownFieldTest(unittest.TestCase):
    """Fields and records worked out by hand, as every real disk checked has them."""

    def test_address_field(self):
        # Volume 254 is FF FE in 4-and-4, track 0 and sector 0 are AA AA, the checksum FF FE.
        self.assertEqual(tool.address_field(254, 0, 0),
            bytes.fromhex("d5aa96 fffe aaaa aaaa fffe deaaeb"))

    def test_data_field_of_zeros(self):
        self.assertEqual(tool.data_field(bytes(256)),
            b"\xD5\xAA\xAD" + b"\x96" * 343 + b"\xDE\xAA\xEB")

    def test_data_field_of_ones(self):
        # Of every byte 01 the low pair, swapped, is 2: three of them make 42 in values 0 to 83,
        # two make 10 in values 84 and 85, and the top values are 0. Each XORed with the one
        # before, that is 42, 0 83 times, 32, 0, 10, then 0 255 times and as the checksum.
        self.assertEqual(tool.encode_6_and_2(b"\x01" * 256),
            b"\xE6" + b"\x96" * 83 + b"\xD6\x96\xAC" + b"\x96" * 256)

    def test_nic_record(self):
        record = made_up_nic()[:512]  # track 0, physical sector 0
        self.assertEqual(record[:34], b"\xFF" * 22 + bytes.fromhex("03fcff3fcff3fcff3fcff3fc"))
        self.assertEqual(record[34:48], tool.address_field(254, 0, 0))
        self.assertEqual(record[48:53], b"\xFF" * 5)
        self.assertEqual(record[53:402], tool.data_field(sector_bytes(0, 0)))
        self.assertEqual(record[402:416], b"\xFF" * 14)
        self.assertEqual(record[416:], bytes(96))


class RoundTripTest(ToolTest):
    """The made-up disk through every format and back."""

    def test_every_format_and_back(self):
        dsk = made_up_dsk()
        source = self.make("DISK.dsk", dsk)
        for middle in ("do", "po", "nib", "nic", "hfe"):
            between = self.path("between.%s" % middle)
            self.converted(source, between)
            self.assertEqual(self.converted(between, self.path("back_%s.dsk" % middle)), dsk,
                middle)
            if middle in ("do", "po"):
                continue
            for other in ("nib", "nic", "hfe"):
                if other != middle:
                    again = self.path("again_%s.%s" % (middle, other))
                    self.converted(between, again)
                    self.assertEqual(self.converted(again, self.path("back_%s_%s.dsk"
                        % (middle, other))), dsk, (middle, other))

    def test_nic_hfe_nic_byte_for_byte(self):
        nic = self.make("DISK.nic", made_up_nic())
        self.converted(nic, "hfe")
        self.assertEqual(self.converted(self.path("DISK.hfe"), self.path("again.nic")),
            made_up_nic())

    def test_forty_tracks(self):
        dsk = made_up_dsk(tracks=40)
        source = self.make("DISK40.dsk", dsk)
        nic = self.converted(source, "nic")
        self.assertEqual(len(nic), 40 * 16 * 512)
        self.assertEqual(self.converted(self.path("DISK40.nic"), self.path("back.dsk")), dsk)

    def test_nib_and_hfe_go_across_bit_for_bit(self):
        nib = self.converted(self.make("DISK.nic", made_up_nic()), "nib")
        self.converted(self.path("DISK.nib"), self.path("again.hfe"))
        self.assertEqual(self.converted(self.path("again.hfe"), self.path("again.nib")), nib)

    def test_basic_program_goes_through(self):
        dsk = dos33_dsk()
        source = self.make("HELLO.dsk", dsk)
        for target in ("HELLO.nic", "HELLO.hfe", "HELLO.nib"):
            self.converted(source, self.path(target))
            self.assertEqual(self.converted(self.path(target), self.path(target + ".dsk")), dsk)
        program = dsk[18 * TRACK_BYTES + 14 * 256:][:2 + len(HELLO)]
        self.assertEqual(program[2:], HELLO)
        self.assertIn(b"HELLO FROM A MADE-UP DISK", program)


class SectorImageTest(ToolTest):

    def test_po_holds_physical_sectors_in_prodos_order(self):
        dsk = made_up_dsk()
        po = self.converted(self.make("DISK.dsk", dsk), "po")
        for track in (0, 34):
            for physical in range(16):
                offset = track * TRACK_BYTES + tool.PRODOS_ORDER[physical] * 256
                self.assertEqual(po[offset:offset + 256], dsk_sector(dsk, track, physical))

    def test_dsk_in_prodos_order_is_read_as_po(self):
        po = bytearray(made_up_dsk())
        # The header of a ProDOS volume directory, where a .po keeps block 2.
        header = bytearray(512)
        header[2] = 3  # the directory's next block, which ProDOS always puts after it
        header[4] = 0xF0 | 4
        header[5:9] = b"TEST"
        header[0x23], header[0x24] = 0x27, 0x0D
        struct.pack_into("<HH", header, 0x27, 6, 280)
        po[1024:1536] = header
        source = self.make("PRODOS.dsk", po)
        status, output = self.run_tool(source, self.path("back.po"))
        self.assertEqual(status, 0, output)
        self.assertIn("read as a .po", output)
        self.assertEqual(read(self.path("back.po")), bytes(po))

    def test_agat_label_stays_with_sector_images_only(self):
        label = b"AGAT-DISK" + bytes(103) + b"NAME".ljust(16, b"\0")
        source = self.make("LABEL.dsk", made_up_dsk() + label)
        status, output = self.run_tool(source, "po")
        self.assertEqual(status, 0, output)
        self.assertIn("Agat label \"NAME\"", output)
        self.assertEqual(self.converted(self.path("LABEL.po"), self.path("back.dsk")),
            read(source))
        status, output = self.run_tool(source, "nic")
        self.assertEqual(status, 0, output)
        self.assertIn("its Agat label has no place in a .nic", output)

    def test_volume_comes_from_option_catalog_or_default(self):
        cases = ((dos33_dsk(9), (), 9), (dos33_dsk(9), ("--volume", "7"), 7),
            (made_up_dsk(), (), 254))
        for number, (dsk, options, volume) in enumerate(cases):
            source = self.make("DISK%d.dsk" % number, dsk)
            nic = tool.read_image(self.converted(source, "nic", *options), "nic", "x")[0]
            self.assertEqual({s.volume for s in track_sectors(nic, 3).values()}, {volume})

    def test_catalog_is_told(self):
        status, output = self.run_tool(self.make("HELLO.dsk", dos33_dsk()), "nic")
        self.assertEqual(status, 0, output)
        self.assertIn("a DOS 3.3 disk of volume 254", output)


class NibTest(ToolTest):

    def test_tracks_are_sdisk_tracks_filled_up_with_ff_before_sector_0(self):
        nib = self.converted(self.make("DISK.nic", made_up_nic()), "nib")
        self.assertEqual(len(nib), 35 * NIB_TRACK_BYTES)
        for track in (0, 17):
            nibbles = tool.read_latch(made_up_tracks().tracks[track])[0]
            self.assertEqual(len(nibbles), 6608)
            at = len(nibbles) - 14  # where the gap before sector 0 begins
            self.assertEqual(nib[track * NIB_TRACK_BYTES:(track + 1) * NIB_TRACK_BYTES],
                nibbles[:at] + b"\xFF" * 48 + nibbles[at:])

    def test_runs_of_ff_in_data_are_no_gap(self):
        # Bytes 00 and FC by turns make a data field of FF nibbles, longer than any gap.
        dsk = bytearray(made_up_dsk())
        dsk[:256] = bytes([0x00, 0xFC] * 128)
        source = self.make("FF.dsk", dsk)
        self.converted(source, "nib")
        self.assertEqual(self.converted(self.path("FF.nib"), self.path("back.dsk")), dsk)

    def test_agat_layout_is_read(self):
        # 128 FF, then for each sector its address field, 5 FF, its data field and 40 FF.
        image = bytearray()
        for track in range(35):
            image += b"\xFF" * 128
            for number in range(16):
                image += (tool.address_field(254, track, number) + b"\xFF" * 5
                    + tool.data_field(made_up_disk().sectors[track][number].data)
                    + b"\xFF" * 40)
        source = self.make("AGAT.nib", image)
        self.assertEqual(self.converted(source, self.path("back.dsk")), made_up_dsk())
        self.assertEqual(self.converted(source, self.path("again.nib")), bytes(image))


class HfeTest(ToolTest):

    def test_header_and_cells(self):
        hfe = self.converted(self.make("DISK.nic", made_up_nic()), "hfe", "--rate", "300")
        header = hfe[:512]
        self.assertEqual(header[:8], b"HXCPICFE")
        self.assertEqual(header[9], 35)  # tracks
        self.assertEqual(header[10], 1)  # sides
        self.assertEqual(struct.unpack_from("<H", header, 12)[0], 300)  # rate
        self.assertEqual(header[21], 0)  # single step off
        self.assertEqual(header[26:], b"\xFF" * (512 - 26))
        # Each track from block 2 on, 52 blocks of a side's 256 bytes and the unused side's.
        self.assertEqual(struct.unpack_from("<HH", hfe, 512 + 4 * 3), (2 + 3 * 52, 26624))
        # A track starts with 22 bytes FF, a 1 being "01", so cells AA, LSB first.
        self.assertEqual(hfe[1024:1024 + 44], b"\xAA" * 44)
        self.assertEqual(hfe[1024 + 256:1024 + 512], b"\x88" * 256)

    def test_other_converters_hfe_is_read(self):
        bits = made_up_tracks().tracks
        # As the HxC software writes: a 1 as "10", encoding 7, 255 kbit/s.
        hxc = hfe_of_cells([t.translate(str.maketrans({"0": "00", "1": "10"})) for t in bits],
            encoding=7, rate=255)
        # As Greaseweazle writes: shorter tracks, 256 kbit/s; here 8 bytes FF less a sector.
        short = [t.replace("1" * 8 * 22, "1" * 8 * 14) for t in bits]
        gw = hfe_of_cells([t.translate(tool.CELLS_OF_BITS) for t in short], rate=256)
        for name, image in (("HXC.hfe", hxc), ("GW.hfe", gw)):
            self.assertEqual(self.converted(self.make(name, image), "dsk"), made_up_dsk(), name)

    def test_spare_pairs_stay_as_the_source_has_them(self):
        disk = made_up_disk()
        filled = tool.SectorDisk([[sector._replace(spare=(2, 1)) for sector in sectors]
            for sectors in disk.sectors])
        source = self.make("DOS.hfe", tool.hfe_bytes(tool.tracks_of_sector_disk(filled), 250))
        nic = tool.read_image(self.converted(source, "nic"), "nic", "x")[0]
        self.assertEqual({s.spare for s in track_sectors(nic, 9).values()}, {(2, 1)})

    def test_version_3_opcodes_are_done(self):
        cells, random_bytes = tool.cells_of_v3_bytes(
            bytes([0x8F, 0xAA, 0x4F, 0x48, 0x0F, 0x55, 0xCF, 0xC0, 0x77, 0x2F]))
        # C0, bit-reversed, is 3: the three first cells of the byte after are skipped.
        self.assertEqual(cells, "01010101" + "10101010" + "01110" + "00000000")
        self.assertEqual(random_bytes, 1)

    def test_version_3_file_is_read(self):
        v1 = tool.hfe_bytes(made_up_tracks(), 250)
        # The same tracks as version 3, each opening with an index mark and a do-nothing.
        body = bytearray()
        track_list = bytearray()
        for bits in made_up_tracks().tracks:
            cells = b"\x8F\x0F" + tool.hfe_bytes_of_cells(bits.translate(tool.CELLS_OF_BITS))
            track_list += struct.pack("<HH", 2 + len(body) // 512, 2 * len(cells))
            for k in range(0, len(cells), 256):
                body += cells[k:k + 256].ljust(512, b"\x88")
        v3 = b"HXCHFEV3" + v1[8:512] + bytes(track_list).ljust(512, b"\xFF") + bytes(body)
        self.assertEqual(self.converted(self.make("V3.hfe", v3), "dsk"), made_up_dsk())

    def test_cells_off_their_pairs_are_counted_by_a_clock(self):
        bits = made_up_tracks().tracks[5]
        rng = random.Random(5)
        for cells_a_bit in (1.96, 2.04):
            cells = ["0"] * int(len(bits) * cells_a_bit + 8)
            for i, bit in enumerate(bits):
                if bit == "1":
                    cells[int(round(i * cells_a_bit + 1 + rng.uniform(-0.1, 0.1)))] = "1"
            back = tool.bits_of_cells("".join(cells))
            reading = tool.read_track(tool.read_latch(back)[0], 5)
            self.assertTrue(reading.is_standard(), (cells_a_bit, reading.faults))

    def test_write_splices_shift_pairs(self):
        # Half the track with reversals in the first cell of each pair, half in the second.
        cells = made_up_tracks().tracks[7].translate(tool.CELLS_OF_BITS)
        middle = len(cells) // 2
        cells = cells[1:middle + 1] + cells[middle:]
        reading = tool.read_track(tool.read_latch(tool.bits_of_cells(cells))[0], 7)
        self.assertEqual(len(reading.sectors), 16, reading.faults)

    def test_fm_hfe_is_refused(self):
        hfe = bytearray(tool.hfe_bytes(made_up_tracks(), 250))
        hfe[11] = 2  # FM
        status, output = self.run_tool(self.make("FM.hfe", hfe), "dsk")
        self.assertEqual(status, 2)
        self.assertIn("FM (track encoding 2)", output)
        self.assertFalse(os.path.exists(self.path("FM.dsk")))

    def test_mfm_cells_of_unknown_encoding_are_refused(self):
        hfe = bytearray(tool.hfe_bytes(made_up_tracks(), 250))
        # Track 0, side 0, as cells 10010010..., never four 0 cells in a row.
        for block in range(1024, 1024 + 26624, 512):
            hfe[block:block + 256] = b"\x49" * 256
        status, output = self.run_tool(self.make("MFM.hfe", hfe), "dsk")
        self.assertEqual(status, 2)
        self.assertIn("holds no Apple2 GCR", output)


class FaultTest(ToolTest):
    """Tracks that are not standard: what the tool says, and what --salvage does."""

    def damaged_nic(self):
        nic = bytearray(made_up_nic())
        track, record = 17, 5
        # Flip a bit in the data field, which starts at byte 53 of a record.
        nic[(track * 16 + record) * 512 + 200] ^= 0x10
        return self.make("DAMAGED.nic", nic)

    def test_damaged_sector_goes_across_as_it_is(self):
        status, output = self.run_tool(self.damaged_nic(), "hfe")
        self.assertEqual(status, 0, output)
        self.assertIn("these go across as they are", output)
        self.assertIn("track 17: sector 5: its data field", output)
        self.assertIn("verified: 559 sectors and the nibbles of every track", output)

    def test_damaged_sector_keeps_a_sector_image_from_being_made(self):
        status, output = self.run_tool(self.damaged_nic(), "dsk")
        self.assertEqual(status, 2)
        self.assertIn("can't become a .dsk", output)
        self.assertIn("track 17: sector 5: its data field", output)
        self.assertIn("--salvage", output)
        self.assertFalse(os.path.exists(self.path("DAMAGED.dsk")))

    def test_salvage_writes_zeros_for_what_cant_be_read(self):
        dsk = self.converted(self.damaged_nic(), "dsk", "--salvage")
        expected = bytearray(made_up_dsk())
        offset = 17 * TRACK_BYTES + tool.DOS_ORDER[5] * 256
        expected[offset:offset + 256] = bytes(256)
        self.assertEqual(dsk, bytes(expected))

    def test_blank_tracks_past_the_35th_are_left_out(self):
        nib = tool.nib_bytes(made_up_tracks())
        source = self.make("FORTY.nib", nib + b"\xFF" * 5 * NIB_TRACK_BYTES)
        status, output = self.run_tool(source, "dsk")
        self.assertEqual(status, 0, output)
        self.assertIn("tracks 35 to 39 hold no fields at all", output)
        self.assertEqual(read(self.path("FORTY.dsk")), made_up_dsk())

    def test_dos_3_2_tracks_are_named(self):
        nibbles = (b"\xFF" * 40 + b"\xD5\xAA\xB5" + b"\xAA" * 8 + b"\xDE\xAA\xEB") * 13
        reading = tool.read_track(nibbles, 0)
        self.assertIn("DOS 3.2", reading.faults[0])

    def test_wrong_track_number_is_a_fault(self):
        bits = tool.track_of_sectors(4, made_up_disk().sectors[3])  # track 3's, numbered 4
        reading = tool.read_track(tool.read_latch(bits)[0], 3)
        self.assertEqual(len(reading.sectors), 16)
        self.assertIn("sector 0: its address field says track 4", reading.faults)


class CommandLineTest(ToolTest):

    def test_extension_alone_takes_the_source_name(self):
        source = self.make("GAME.NIC", made_up_nic())
        for target, name in (("hfe", "GAME.hfe"), (".DSK", "GAME.DSK")):
            status, output = self.run_tool(source, target)
            self.assertEqual(status, 0, output)
            self.assertIn(name, os.listdir(self.directory))

    def test_several_sources(self):
        first = self.make("ONE.nic", made_up_nic())
        second = self.make("TWO.dsk", made_up_dsk())
        status, output = self.run_tool(first, second, "nib")
        self.assertEqual(status, 0, output)
        self.assertTrue(os.path.exists(self.path("ONE.nib")))
        self.assertTrue(os.path.exists(self.path("TWO.nib")))
        status, output = self.run_tool(first, second, self.path("x.nib"))
        self.assertEqual(status, 2)
        self.assertIn("give the target as an extension alone", output)

    def test_targets_that_name_no_format_are_refused(self):
        source = self.make("DISK.nic", made_up_nic())
        for target in ("out", "out.txt"):
            status, output = self.run_tool(source, self.path(target))
            self.assertEqual(status, 2)
            self.assertIn("names no format", output)
        status, output = self.run_tool(source)
        self.assertEqual(status, 2)
        self.assertIn("give a target", output)

    def test_input_is_not_written_over(self):
        victim = self.make("image.hfe", made_up_nic())
        status, output = self.run_tool(victim, "hfe")
        self.assertEqual(status, 2)
        self.assertIn("would be written over itself", output)
        self.assertEqual(read(victim), made_up_nic())

    def test_wrong_size_is_explained_without_writing(self):
        source = self.make("short.nic", made_up_nic()[:-1])
        status, output = self.run_tool(source, "hfe")
        self.assertEqual(status, 2)
        self.assertIn("286'720 for 35 tracks", output)
        self.assertFalse(os.path.exists(self.path("short.hfe")))

    def test_size_of_another_format_is_named(self):
        source = self.make("WRONG.dsk", made_up_nic())
        status, output = self.run_tool(source, "nib")
        self.assertEqual(status, 2)
        self.assertIn("Its size is that of a .nic of 35 tracks", output)

    def test_missing_file_is_reported_without_traceback(self):
        status, output = self.run_tool(self.path("missing.nic"), "hfe")
        self.assertEqual(status, 2)
        self.assertIn("missing.nic", output)
        self.assertNotIn("Traceback", output)

    def test_options_that_dont_fit_are_explained(self):
        dsk = self.make("DISK.dsk", made_up_dsk())
        nic = self.make("DISK.nic", made_up_nic())
        cases = (
            (("--volume", "7", nic, "hfe"), "keeps the address fields"),
            (("--volume", "7", dsk, "po"), "no place for the volume number"),
            (("--rate", "300", dsk, "nic"), "sets the bit rate that an .hfe records"),
            (("--salvage", dsk, "nic"), "holds sectors already"),
            (("--salvage", nic, "hfe"), "nothing is decoded"),
            (("--dry-run", "--no-verify", dsk, "nic"), "would do nothing"),
        )
        for args, phrase in cases:
            status, output = self.run_tool(*args)
            self.assertEqual(status, 2, args)
            self.assertIn(phrase, output, args)
        self.assertEqual(sorted(os.listdir(self.directory)), ["DISK.dsk", "DISK.nic"])

    def test_dry_run_writes_nothing(self):
        source = self.make("DISK.dsk", made_up_dsk())
        status, output = self.run_tool("-n", source, "hfe")
        self.assertEqual(status, 0, output)
        self.assertIn("would be written", output)
        self.assertEqual(os.listdir(self.directory), ["DISK.dsk"])

    def test_backups(self):
        source = self.make("DISK.dsk", made_up_dsk())
        target, backup = self.path("DISK.nic"), self.path("DISK.nic.BAK")
        status, output = self.run_tool(source, "nic")
        self.assertEqual(status, 0, output)
        first = read(target)
        # Again: the old target becomes the backup, whatever it holds.
        status, output = self.run_tool("--volume", "1", source, "nic")
        self.assertEqual(status, 0, output)
        self.assertIn("the old one renamed to", output)
        self.assertEqual(read(backup), first)
        # Once more, the backup's name taken: refused before converting, a dry run too.
        second = read(target)
        for options in ((), ("--dry-run",)):
            status, output = self.run_tool(*options, "--volume", "2", source, "nic")
            self.assertEqual(status, 2)
            self.assertIn("exists, and so does", output)
            self.assertIn("move one of them away", output)
            self.assertNotIn("sector image", output)
            self.assertEqual((read(target), read(backup)), (second, first))

    def test_image_that_would_differ_is_not_written(self):
        source = self.make("DISK.nic", made_up_nic())
        original = tool.hfe_bytes

        def wrong(disk, rate):
            return original(tool.TrackDisk(disk.tracks[:1] + disk.tracks[:-1]), rate)

        tool.hfe_bytes = wrong
        try:
            status, output = self.run_tool(source, "hfe")
        finally:
            tool.hfe_bytes = original
        self.assertEqual(status, 1)
        self.assertIn("not written", output)
        self.assertIn("track 1: sectors 0 to 15 differ", output)
        self.assertFalse(os.path.exists(self.path("DISK.hfe")))


if __name__ == "__main__":
    unittest.main()
