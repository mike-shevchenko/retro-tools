#!/usr/bin/env python3
"""
Convert SDISK II .nic disk images, Apple II or Agat 140 KB, to HFE for FlashFloppy's Apple II
firmware.

    nic2hfe.py GAME.nic ... ->  GAME.hfe ...

A .nic image holds 35 tracks of 16 blocks of 512 bytes. Each block carries one sector as a
ready-made bit stream - sync bytes, address field, GCR 6-and-2 data field - MSB first in its
first 416 bytes, the rest being zero padding. That bit stream goes into the HFE verbatim, so
gaps, volume number and sector order stay exactly as SDISK II plays them.

The HFE is laid out as Greaseweazle writes Apple II images: one side, 35 cylinders, the
double-step flag set, and two flux cells per disk bit, a 1 becoming "01" and a 0 "00".

Every HFE written is read back, and its sectors are decoded and compared with those of the
source; --no-verify skips that.
"""
# Written with the help of Claude Opus 5.5.

import argparse
import os
import struct
import sys

TRACKS = 35
SECTORS = 16
BLOCK = 512  # a .nic block, and the unit of every offset in an HFE
BITSTREAM_BYTES = 416  # the meaningful start of each .nic block
NIC_SIZE = TRACKS * SECTORS * BLOCK

# 2 us cells, so 4 us disk bits, as on the Disk II and in SDISK II.
DEFAULT_RATE_KBPS = 250
UNUSED_SIDE_FILL = 0x88  # what Greaseweazle fills the absent second side with

ADDRESS_PROLOGUE = b"\xD5\xAA\x96"
DATA_PROLOGUE = b"\xD5\xAA\xAD"
ADDRESS_FIELD_NIBBLES = 8  # volume, track, sector and checksum, each in 4-and-4 encoding
DATA_PROLOGUE_REACH = 64  # the nibbles after the address field that must hold the prologue
DATA_FIELD_NIBBLES = 343  # 342 of 6-and-2 data and the checksum

NIBBLE_OF_SIX_BITS = bytes([
    0x96, 0x97, 0x9A, 0x9B, 0x9D, 0x9E, 0x9F, 0xA6,
    0xA7, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB2, 0xB3,
    0xB4, 0xB5, 0xB6, 0xB7, 0xB9, 0xBA, 0xBB, 0xBC,
    0xBD, 0xBE, 0xBF, 0xCB, 0xCD, 0xCE, 0xCF, 0xD3,
    0xD6, 0xD7, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE,
    0xDF, 0xE5, 0xE6, 0xE7, 0xE9, 0xEA, 0xEB, 0xEC,
    0xED, 0xEE, 0xEF, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6,
    0xF7, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF])
SIX_BITS_OF_NIBBLE = {nibble: i for i, nibble in enumerate(NIBBLE_OF_SIX_BITS)}


class Failure(Exception):
    """A problem with one image, reported in one line before going on to the next."""


def cells_of_byte(byte):
    """The 16 HFE cells of 8 disk bits: the bits MSB first, the cells packed LSB first."""
    cells = 0
    for i in range(8):
        cells |= ((byte >> (7 - i)) & 1) << (2 * i + 1)
    return cells.to_bytes(2, "little")


CELLS_OF_BYTE = [cells_of_byte(byte) for byte in range(256)]


# --- writing ----------------------------------------------------------------------------------

def read_nic(path):
    """Return the bit stream bytes of each track of a .nic image."""
    with open(path, "rb") as f:
        nic = f.read()
    if len(nic) != NIC_SIZE:
        raise Failure(f"{path}: {len(nic)} bytes, not the {NIC_SIZE} of a .nic image")
    blocks = [nic[offset:offset + BITSTREAM_BYTES] for offset in range(0, NIC_SIZE, BLOCK)]
    return [b"".join(blocks[t * SECTORS:(t + 1) * SECTORS]) for t in range(TRACKS)]


def hfe_header(cylinders, rate_kbps, track_list_block):
    header = b"HXCPICFE" + struct.pack(
        "<BBBBHHBBHBBBBBB",
        0,  # format revision
        cylinders,
        1,  # sides
        0xFF,  # track encoding: unknown, as Greaseweazle writes for Apple II
        rate_kbps,
        0,  # RPM, unused
        0xFF,  # interface mode: unset
        1,  # unused, 1 as Greaseweazle writes
        track_list_block,
        0xFF,  # write allowed
        0x00,  # single step off, so double step
        0xFF, 0xFF, 0xFF, 0xFF)  # no alternative encodings for track 0
    return header.ljust(BLOCK, b"\xFF")


def write_hfe(path, tracks, rate_kbps):
    """Write single-sided HFE v1 holding each track's bit stream bytes as cells."""
    track_list = bytearray()
    body = bytearray()
    first_block = 2  # after the header and the track list
    for track in tracks:
        cells = b"".join(CELLS_OF_BYTE[byte] for byte in track)
        # Each 512-byte block holds 256 bytes of side 0, then 256 of side 1.
        interleaved = bytearray()
        for k in range(0, len(cells), BLOCK // 2):
            interleaved += cells[k:k + BLOCK // 2].ljust(BLOCK, bytes([UNUSED_SIDE_FILL]))
        track_list += struct.pack("<HH", first_block + len(body) // BLOCK, 2 * len(cells))
        body += interleaved
    with open(path, "wb") as f:
        f.write(hfe_header(len(tracks), rate_kbps, 1))
        f.write(track_list.ljust(BLOCK, b"\xFF"))
        f.write(body)


# --- reading back -----------------------------------------------------------------------------

def disk_bits(track):
    """Return a track's bit stream bytes as a list of bits, MSB first."""
    return [(byte >> i) & 1 for byte in track for i in range(7, -1, -1)]


def read_hfe(path):
    """Return side 0 of every cylinder of an HFE as disk bits, two cells to a bit."""
    with open(path, "rb") as f:
        hfe = f.read()
    if hfe[:8] != b"HXCPICFE":
        raise Failure(f"{path}: not an HFE v1 file")
    cylinders = hfe[9]
    track_list = struct.unpack_from("<H", hfe, 18)[0] * BLOCK
    result = []
    for cylinder in range(cylinders):
        block, length = struct.unpack_from("<HH", hfe, track_list + 4 * cylinder)
        start = block * BLOCK
        side0 = b"".join(
            hfe[start + k:start + k + BLOCK // 2] for k in range(0, length, BLOCK))
        cells = [(byte >> i) & 1 for byte in side0[:length // 2] for i in range(8)]
        result.append([cells[i] | cells[i + 1] for i in range(0, len(cells) - 1, 2)])
    return result


def nibbles(bits):
    """Return the bytes the Disk II read latch would deliver from a bit stream."""
    result = bytearray()
    latch = 0
    for bit in bits:
        latch = ((latch << 1) | bit) & 0xFF
        if latch & 0x80:
            result.append(latch)
            latch = 0
    return bytes(result)


def decode_6_and_2(field):
    """Return the 256 bytes of a data field, or None if it is not valid 6-and-2."""
    if len(field) != DATA_FIELD_NIBBLES:
        return None
    six_bits = []
    checksum = 0
    for nibble in field:
        if nibble not in SIX_BITS_OF_NIBBLE:
            return None
        checksum ^= SIX_BITS_OF_NIBBLE[nibble]
        six_bits.append(checksum)
    # The last nibble is the checksum, which leaves the running XOR at zero when it matches.
    if checksum != 0:
        return None
    data = bytearray(256)
    for j in range(256):
        low_pairs = six_bits[j % 86] >> (2 * (j // 86))
        high = six_bits[86 + j] << 2
        data[j] = (high | ((low_pairs & 1) << 1) | ((low_pairs >> 1) & 1)) & 0xFF
    return bytes(data)


def decode_4_and_4(high, low):
    return ((high << 1) | 1) & low


def sectors(bits):
    """Return {(track, sector): data} for every sector of a track that decodes cleanly."""
    track = nibbles(bits)
    # The track is a circle, so a sector straddling the index is read from the wrapped copy.
    circle = track + track[:1024]
    found = {}
    i = circle.find(ADDRESS_PROLOGUE)
    while 0 <= i < len(track):
        address = i + len(ADDRESS_PROLOGUE)
        fields = circle[address:address + ADDRESS_FIELD_NIBBLES]
        volume, track_number, sector, checksum = (
            decode_4_and_4(fields[k], fields[k + 1]) for k in range(0, 8, 2))
        after = address + ADDRESS_FIELD_NIBBLES
        data = circle.find(DATA_PROLOGUE, after, after + DATA_PROLOGUE_REACH)
        if volume ^ track_number ^ sector == checksum and data >= 0:
            start = data + len(DATA_PROLOGUE)
            content = decode_6_and_2(circle[start:start + DATA_FIELD_NIBBLES])
            if content is not None:
                found[(track_number, sector)] = content
        i = circle.find(ADDRESS_PROLOGUE, after)
    return found


def verify(tracks, hfe_path):
    """Return a line for each way the HFE fails to hold the source's sectors; none if it does."""
    problems = []
    written = read_hfe(hfe_path)
    if len(written) != len(tracks):
        return [f"{len(written)} cylinders written, not {len(tracks)}"]
    for t, (track, back) in enumerate(zip(tracks, written)):
        expected = sectors(disk_bits(track))
        wanted = {(t, s) for s in range(SECTORS)}
        if set(expected) != wanted:
            good = len(set(expected) & wanted)
            problems.append(f"track {t}: {good} of {SECTORS} sectors good in the source")
        if sectors(back) != expected:
            problems.append(f"track {t}: HFE differs from the source")
    return problems


# --- entry point ------------------------------------------------------------------------------

def convert(nic_path, rate_kbps, check):
    """Write the HFE next to the .nic image; return whether it verified, or was not checked."""
    hfe_path = os.path.splitext(nic_path)[0] + ".hfe"
    if os.path.normcase(os.path.abspath(hfe_path)) == os.path.normcase(os.path.abspath(nic_path)):
        raise Failure(f"{nic_path}: would be overwritten by its own HFE")
    tracks = read_nic(nic_path)
    write_hfe(hfe_path, tracks, rate_kbps)
    print(f"{nic_path} -> {hfe_path}")
    if not check:
        return True
    problems = verify(tracks, hfe_path)
    for problem in problems:
        print(f"  {problem}")
    print(f"  verify: {'FAILED' if problems else 'OK, all sectors of all tracks match'}")
    return not problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(prog, width=99))
    parser.add_argument("images", nargs="+", metavar="IMAGE.nic",
        help="written as IMAGE.hfe beside it")
    parser.add_argument("--rate", type=int, default=DEFAULT_RATE_KBPS, metavar="KBPS",
        help=f"HFE bit rate in kbit/s, default {DEFAULT_RATE_KBPS}: 4 us disk bits")
    parser.add_argument("--no-verify", action="store_true",
        help="skip reading each HFE back and comparing its sectors with the source")
    args = parser.parse_args(argv)
    if not 0 < args.rate <= 0xFFFF:
        parser.error(f"--rate {args.rate} does not fit in the HFE header")

    errors = 0
    unverified = 0
    for path in args.images:
        try:
            if not convert(path, args.rate, not args.no_verify):
                unverified += 1
        except Failure as failure:
            errors += 1
            sys.stderr.write(f"nic2hfe: {failure}\n")
        except OSError as error:
            errors += 1
            where = f"{error.filename}: " if error.filename else ""
            sys.stderr.write(f"nic2hfe: {where}{error.strerror or error}\n")
    return 2 if errors else 1 if unverified else 0


if __name__ == "__main__":
    sys.exit(main())
