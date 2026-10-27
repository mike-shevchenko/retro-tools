#!/usr/bin/env python3
"""
Convert Apple2 and Agat 140 KB floppy disk images between the sector images .dsk, .do and .po
of emulators and disk archives and the track images .nib, .nic and .hfe, all of which
FlashFloppy plays on a Gotek in its Apple2 mode; unpack an image into a folder of its files,
and pack such a folder back into an image.

The extension of the target names its format. Given alone, as "hfe" or ".hfe", it takes the
folder and the name of each source, and the extension as typed; several sources may be given
then:

    apple2-disk-convert.py GAME.dsk nic
    apple2-disk-convert.py GAME.nic ../gotek/Game2.hfe
    apple2-disk-convert.py *.nic hfe

An existing target is renamed to TARGET.BAK. If that name is taken as well, the image is not
converted: move one of the two away first.

Apple2 disks in brief:

The Disk II drive of the Apple2, and the 140 KB drives of the Agat, a Soviet computer of the
same lineage, use one side of a 5.25" floppy: 35 tracks, on some disks 40, of 16 sectors of
256 bytes. On the disk each sector is two fields of specially coded bytes, an address field
with the disk's volume number and the sector's track and number, then a data field with the
256 bytes; runs of sync bytes between the fields let the controller find where bytes begin.
Copy-protected disks lay out some of their tracks in ways of their own.

The formats, by extension:

.dsk or .do: the 256 bytes of each sector in the order DOS 3.3 numbers them, 143'360 bytes for
    35 tracks. An Agat image may end with a 128-byte label starting with "AGAT-DISK", which a
    sector image made from it keeps. A .dsk that holds its sectors in ProDOS's order is read
    as a .po.
.po: the same in the order ProDOS numbers them.
.nib: each track as the 6'656 bytes the controller reads from it; any layout fits, copy
    protection included, but not where the sync bytes were.
.nic: the image of SDISK//, a floppy emulator on an SD card: each sector as the stream of bits
    to send, 16 to a track; only standard tracks fit.
.hfe: the image of the HxC floppy emulator: each track as a stream of flux cells; any layout
    and timing fit.

FlashFloppy plays all six in its Apple2 mode. It lays out the tracks of a sector image as this
tool does, but always with the volume number 254.

How images are converted:

From a sector image, each track is laid out as SDISK// lays it out. Into a sector image or a
.nic, each track is decoded and must hold its 16 sectors intact, or the tool lists what it
found wrong, and --salvage writes zeros for what can't be read. From .nib, .nic or .hfe into
.nib or .hfe, the tracks go across bit for bit.

Every image is read back from what is to be written and compared with the source: sector by
sector, and when tracks went across as they are, every byte the controller would read.

Unpacking and packing:

    apple2-disk-convert.py unpack GAME.nic
    apple2-disk-convert.py pack GAME.nic.files [[TARGET].]EXT

unpack writes an image as a folder beside it, GAME.nic.files. The files of a DOS 3.3 catalog,
the Agat's DOS included, go under files/, a folder to each directory of the Agat's DOS, each
named as the catalog has it, in the Agat's letters, with its type as the extension: T, I, A,
B, S and the Agat's own. A BASIC program and a text file also get their text beside them, as
NAME.txt, to read. What no file holds goes into hex dumps, as hexdump -Cv shows them: DOS
itself (boot.hex), the catalog (catalog.hex), sectors in no file (unused.hex), what follows
the end of a file in its sectors (tails.hex), an Agat label (label.hex); a disk with no
catalog is sectors.hex alone. disk.json holds the rest: the format, where each file's entry
is, and for a track image the volume number and the spare bits of each sector. Every unpack
checks that the folder packs back into the same image, or into the same sectors where a track
image had its tracks laid out otherwise.

pack writes a folder back as an image, of the format the folder came from unless told. A file
that was edited goes back into the sectors it had, its length told anew; one that no longer
fits them stops the pack, as the catalog is not rewritten. The .txt files are only for
reading, and pack says so if one was changed.
"""
# Written with the help of Claude Opus 5.5.

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import textwrap

PROGRAM = "apple2-disk-convert.py"
LINE_WIDTH = 99
BACKUP_SUFFIX = ".BAK"


class Failure(Exception):
    """A problem with one image: reported, then the tool goes on to the next one."""


class UsageError(Exception):
    """A problem with the command line: reported before anything is converted."""


# =============================================================================================
# How an Apple2 floppy holds its data
# =============================================================================================
#
# The drive. The Disk II drive of the Apple2 uses one side of a 5.25" floppy, in 35 tracks, 40
# on some disks, and turns it at 300 rpm, 200 ms a turn. The 140 KB drives of the Agat use the
# same format, as the Agat images seen show. The drive has no step and direction
# inputs as a PC's drive has: the controller drives the four phases of the head's stepper
# motor itself, two phase steps to a track, so the head can stop between tracks too, which
# some copy protection uses. FlashFloppy's Apple2 mode follows those phases; this tool handles
# whole tracks only.
#
# Bits. A track is a ring of magnetized stretches, and what the drive senses is where the
# magnetization flips, a flux reversal. The controller reads the ring in bit cells of 4
# microseconds, about 50'000 to a turn: a cell with a flux reversal is a 1, one without it a 0.
# The drive's read amplifier sets its gain by the signal it gets, so through a long stretch
# without reversals it turns noise into false 1s: data is never written with more than two 0s
# in a row.
#
# The read latch. The controller has no clock of its own to tell where a byte begins: it
# shifts the bits into an 8-bit latch, where a 0 shifted into the empty latch leaves it empty.
# Eight bits from the first 1 on, the top bit of the latch is 1: the program takes the byte and
# the latch starts over, as read_latch() does. So every byte on a disk has its top bit set;
# such bytes are called nibbles or disk bytes, and the scheme GCR, group code recording.
#
# Sync bytes. Before each field the disk holds a run of FF, each followed by two 0 bits: sync
# bytes of 10 bits. A latch that starts within a byte reads garbage up to such a run, where the
# 0s make it end a byte early or skip them, until it starts on the first bit of an FF, in step
# with the bytes that follow. A few sync bytes do it.
#
# Sectors. A track holds 16 sectors of 256 bytes, each as two fields of nibbles:
#
#   address field: D5 AA 96, the volume, the track, the sector and a checksum, DE AA EB
#   data field: D5 AA AD, 342 nibbles of data and one of checksum, DE AA EB
#
# The address field tells which sector follows: DOS reads address fields until it finds the
# sector it wants, then the data field after it. DE AA EB ends a field, though DOS checks only
# DE AA. D5 AA starts every field, and since the code of the data never yields D5 or AA, a
# field can't seem to start within another. A sector is written by rewriting its data field
# alone, right after its address field has been read; the address fields are written only
# when the disk is formatted, by DOS 3.3's INIT, in the order 0, 1, ... 15 around the track.
# Those are the sectors' physical numbers; how a DOS numbers them is told with the sector
# images.
#
# The volume number. DOS 3.3 writes the volume number given to INIT, 254 unless told otherwise,
# into every address field and into its catalog; a command may ask for a disk of a given
# volume.
#
# The index. A Disk II ignores the index hole of a disk, so a track has no start: an image
# begins each track somewhere on its ring, and the gap at its end runs on into its start.

STANDARD_TRACKS = 35
MAX_TRACKS = 40  # as far as a Disk II's head and FlashFloppy go
SECTORS = 16
SECTOR_BYTES = 256
TRACK_BYTES = SECTORS * SECTOR_BYTES
DEFAULT_VOLUME = 254


def bits_of_bytes(data):
    """The bits of bytes as a string of "0" and "1", each byte's top bit first."""
    return format(int.from_bytes(data, "big"), "0%db" % (8 * len(data))) if data else ""


def bytes_of_bits(bits):
    """The bytes of a string of "0" and "1" whose length is a multiple of 8, top bit first."""
    return int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""


def read_latch(bits):
    """Return the nibbles that the controller reads in one turn of a track, given as a ring of
    bits, and the bit at which each of them starts.

    The latch has to fall into step first, so it reads a turn of the ring before; the nibbles
    of the next turn are the track's, starting with the first one whose first bit is the ring's
    bit 0 or later. The last of them may run over the end of the ring into its start."""
    turn = len(bits)
    ring = bits * 3
    nibbles = bytearray()
    starts = []
    at = ring.find("1")
    while 0 <= at < 2 * turn:
        if at >= turn:
            nibbles.append(int(ring[at:at + 8], 2))
            starts.append(at - turn)
        at = ring.find("1", at + 8)
    return bytes(nibbles), starts


# =============================================================================================
# The codes of the fields
# =============================================================================================
#
# 4-and-4, for the address field: a byte becomes two nibbles, its odd bits and then its even
# bits, each with 1s in the places of the other bits: the byte shifted right by one, ORed with
# AA (10101010 in binary), then the byte ORed with AA.
#
# 6-and-2, for the data field: the 256 bytes become 342 values of 6 bits. First come 86
# values with the low two bits of every byte, three bytes' to a value: the two bits of byte j,
# swapped (bit 0 into bit 1 and bit 1 into bit 0, as DOS 3.3's code happens to produce them),
# go into value j % 86 at bit 2 * (j // 86). Then come 256 values with the top six bits of the
# bytes, in order. Each value is XORed with the one before it, the first with 0, and a 343rd
# value, the last value itself, ends the field: a reader XORs as it goes, and a field read
# right leaves it at 0. Each value goes on the disk as one of 64 nibbles, the bytes with the
# top bit set, no three 0 bits in a row and at most one pair of them, and two 1 bits side by
# side below the top bit, D5 and AA aside, which are kept for the marks.
#
# The 86 values have room for 258 pairs, so the top pairs of values 84 and 85 are spare, and
# readers ignore them. DOS 3.3's loop runs on past the last byte and puts the pairs of bytes 0
# and 1 there once more, as every sector seen that DOS wrote has them; converters leave them
# 0, as most images seen have them, from Greaseweazle and the HxC software among others, and
# as FlashFloppy sends a sector image. This tool keeps them as a source has them, and leaves
# them 0 in a sector it makes anew.

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
AUXILIARY_VALUES = 86
DATA_NIBBLES = 343  # 342 of data and the checksum


def encode_4_and_4(value):
    return bytes([(value >> 1) | 0xAA, value | 0xAA])


def decode_4_and_4(odd, even):
    return ((odd << 1) | 1) & even


def swapped_low_bits(byte):
    return ((byte & 1) << 1) | ((byte >> 1) & 1)


def encode_6_and_2(data, spare=None):
    """Return the 343 nibbles of a data field for 256 bytes: 342 of data and the checksum.
    spare gives the spare pairs of values 84 and 85, 0 by default."""
    auxiliary = [0] * AUXILIARY_VALUES
    for j, byte in enumerate(data):
        auxiliary[j % AUXILIARY_VALUES] |= swapped_low_bits(byte) << (2 * (j // AUXILIARY_VALUES))
    if spare is not None:
        auxiliary[84] |= spare[0] << 4
        auxiliary[85] |= spare[1] << 4
    nibbles = bytearray()
    previous = 0
    for value in auxiliary + [byte >> 2 for byte in data]:
        nibbles.append(NIBBLE_OF_SIX_BITS[value ^ previous])
        previous = value
    nibbles.append(NIBBLE_OF_SIX_BITS[previous])
    return bytes(nibbles)


def decode_6_and_2(field):
    """Return the 256 bytes of a data field's 343 nibbles, or None if it is not valid 6-and-2
    code or its checksum is wrong."""
    if len(field) != DATA_NIBBLES:
        return None
    values = []
    checksum = 0
    for nibble in field:
        if nibble not in SIX_BITS_OF_NIBBLE:
            return None
        checksum ^= SIX_BITS_OF_NIBBLE[nibble]
        values.append(checksum)
    if checksum != 0:
        return None
    data = bytearray(SECTOR_BYTES)
    for j in range(SECTOR_BYTES):
        pair = (values[j % AUXILIARY_VALUES] >> (2 * (j // AUXILIARY_VALUES))) & 3
        data[j] = (values[AUXILIARY_VALUES + j] << 2) | swapped_low_bits(pair)
    return bytes(data)


def spare_pairs(field):
    """The spare pairs of a valid data field's nibbles, those of values 84 and 85."""
    value = 0
    values = []
    for nibble in field[:AUXILIARY_VALUES]:
        value ^= SIX_BITS_OF_NIBBLE[nibble]
        values.append(value)
    return values[84] >> 4, values[85] >> 4


# =============================================================================================
# Fields, and how DOS finds them
# =============================================================================================

ADDRESS_PROLOGUE = b"\xD5\xAA\x96"
DATA_PROLOGUE = b"\xD5\xAA\xAD"
EPILOGUE = b"\xDE\xAA\xEB"
CHECKED_EPILOGUE = EPILOGUE[:2]  # what DOS 3.3 checks of it
DOS32_ADDRESS_PROLOGUE = b"\xD5\xAA\xB5"  # the 13-sector tracks of DOS 3.2
ADDRESS_FIELD_BYTES = 14
DATA_FIELD_BYTES = 349
# How far after an address field, in nibbles, the data field must start to be its sector's.
DATA_FIELD_REACH = 64

# A sector: its volume number, None when the image keeps none; its 256 bytes; and the spare
# pairs of its data field, None when unknown.
Sector = collections.namedtuple("Sector", "volume data spare", defaults=(None,))


def address_field(volume, track, sector):
    values = (volume, track, sector, volume ^ track ^ sector)
    return ADDRESS_PROLOGUE + b"".join(encode_4_and_4(v) for v in values) + EPILOGUE


def data_field(data, spare=None):
    return DATA_PROLOGUE + encode_6_and_2(data, spare) + EPILOGUE


class TrackReading:
    """What one turn of a track holds: its intact sectors by physical number, a line for each
    fault found, and whether it holds no fields at all."""

    def __init__(self):
        self.sectors = {}
        self.faults = []
        self.blank = False

    def is_standard(self):
        return len(self.sectors) == SECTORS and not self.faults


def read_sector(ring, at, track):
    """Read the address field at ring[at] and the data field after it: return the sector's
    number (None when the address field can't tell it), the Sector (None when its data can't
    be read) and a list of what is wrong with them."""
    pairs = ring[at + 3:at + 11]
    if any(nibble & 0xAA != 0xAA for nibble in pairs):
        return None, None, ["an address field holds bytes that are not 4-and-4 code"]
    volume, said_track, number, checksum = (
        decode_4_and_4(pairs[k], pairs[k + 1]) for k in range(0, 8, 2))
    if volume ^ said_track ^ number != checksum:
        return None, None, ["an address field's checksum is wrong"]
    if number >= SECTORS:
        return None, None, ["an address field says sector %d, where a track has 0 to 15" % number]
    name = "sector %d" % number
    faults = []
    if said_track != track:
        faults.append("%s: its address field says track %d" % (name, said_track))
    if ring[at + 11:at + 13] != CHECKED_EPILOGUE:
        faults.append("%s: its address field doesn't end in DE AA" % name)
    start = ring.find(DATA_PROLOGUE, at + 11, at + 11 + DATA_FIELD_REACH)
    if start < 0:
        return number, None, faults + ["%s: no data field follows its address field" % name]
    field = ring[start + 3:start + 3 + DATA_NIBBLES]
    if any(nibble not in SIX_BITS_OF_NIBBLE for nibble in field):
        return number, None, faults + [
            "%s: its data field holds bytes that are not 6-and-2 code" % name]
    data = decode_6_and_2(field)
    if data is None:
        return number, None, faults + ["%s: its data field's checksum is wrong" % name]
    end = start + 3 + DATA_NIBBLES
    if ring[end:end + 2] != CHECKED_EPILOGUE:
        faults.append("%s: its data field doesn't end in DE AA" % name)
    return number, Sector(volume, data, spare_pairs(field)), faults


def read_track(nibbles, track):
    """Find the sectors of physical track `track` in the nibbles of one turn, as DOS does: every
    address field, and the data field that follows each."""
    reading = TrackReading()
    told = set()
    # A sector that runs over the end of the turn continues at its start.
    ring = nibbles + nibbles[:ADDRESS_FIELD_BYTES + DATA_FIELD_REACH + DATA_FIELD_BYTES]
    at = ring.find(ADDRESS_PROLOGUE)
    found = at >= 0
    while 0 <= at < len(nibbles):
        number, sector, faults = read_sector(ring, at, track)
        reading.faults += faults
        if number is not None:
            told.add(number)
        if sector is not None:
            known = reading.sectors.setdefault(number, sector)
            if known[:2] != sector[:2]:
                reading.faults.append("sector %d: found twice, with different contents" % number)
        at = ring.find(ADDRESS_PROLOGUE, at + len(ADDRESS_PROLOGUE))
    if DOS32_ADDRESS_PROLOGUE in ring:
        reading.faults.insert(0, "it has the 13-sector address fields of DOS 3.2 (D5 AA B5),"
            " which this tool can't decode")
    elif not found:
        reading.blank = True
        reading.faults.append("no sectors at all: it is blank, or laid out in a way of its own")
    missing = [n for n in range(SECTORS) if n not in reading.sectors and n not in told]
    if found and missing:
        reading.faults.append("%s missing" % counted("sector", missing))
    return reading


# =============================================================================================
# The track that SDISK// lays out
# =============================================================================================
#
# A track made from sectors needs gaps of sync bytes around its fields. This tool lays every
# such track out as SDISK// does in its .nic images, where every one of 47'040 sectors in 84
# images seen has this layout, and as the .nic writer of the Agat emulator ecat3 does too: for
# each sector in physical order, 22 bytes FF, six 0 bits, nine sync bytes (FF and two 0 bits),
# the address field, 5 bytes FF, the data field and 14 bytes FF. That is 416 bytes a sector,
# so 53'248 bits a track, 213 ms at 4 us a bit, a little more than a turn, which does no harm:
# a computer reads the track as a ring, and an emulator plays it so.

SDISK_LEAD_BITS = "1" * 8 * 22 + "0" * 6 + "1111111100" * 9
SDISK_GAP = b"\xFF" * 5  # between the address field and the data field
SDISK_TAIL = b"\xFF" * 14  # after the data field
SDISK_SECTOR_BYTES = 416


def sdisk_sector_bits(track, number, sector):
    """A Sector, number `number` of a track, as SDISK// lays it out: 416 bytes' worth of
    bits."""
    fields = (address_field(sector.volume, track, number) + SDISK_GAP
        + data_field(sector.data, sector.spare) + SDISK_TAIL)
    bits = SDISK_LEAD_BITS + bits_of_bytes(fields)
    assert len(bits) == 8 * SDISK_SECTOR_BYTES
    return bits


def track_of_sectors(track, sectors):
    """The ring of bits of a standard track of 16 Sectors, by physical number, laid out as
    SDISK// does."""
    return "".join(sdisk_sector_bits(track, number, sector)
        for number, sector in enumerate(sectors))


# =============================================================================================
# The disk, as sectors or as tracks
# =============================================================================================

class SectorDisk:
    """A disk as its sectors: sectors[track][physical sector] is a Sector. label holds an Agat
    label, if any."""

    def __init__(self, sectors, label=b""):
        self.sectors = sectors
        self.label = label


class TrackDisk:
    """A disk as its tracks: tracks[track] is the ring of bits of a track, a string of "0" and
    "1", as long as the image has it."""

    def __init__(self, tracks):
        self.tracks = tracks


def read_tracks(disk):
    """Read every track of a TrackDisk as DOS would: a TrackReading for each."""
    return [read_track(read_latch(bits)[0], track) for track, bits in enumerate(disk.tracks)]


def tracks_of_sector_disk(disk):
    return TrackDisk([track_of_sectors(track, sectors)
        for track, sectors in enumerate(disk.sectors)])


def with_volume(disk, volume):
    """The sectors of a disk from a sector image, each given the volume number."""
    return SectorDisk([[sector._replace(volume=volume) for sector in sectors]
        for sectors in disk.sectors], disk.label)


# =============================================================================================
# .dsk, .do and .po: sector images
# =============================================================================================
#
# A sector image holds the 256 bytes of every sector and nothing else, 4'096 bytes a track:
# 143'360 for 35 tracks, 163'840 for 40. No volume numbers, no gaps: a sector image holds
# standard tracks only.
#
# The sectors of a track come in the order of a DOS's own numbers for them. DOS 3.3 numbers
# them so that each next one it wants is two physical sectors further around the track,
# giving it a sector's time to handle one before the next comes by; a .dsk or .do holds them
# in that order. ProDOS reads 512-byte blocks of two sectors each, numbered in an order of its
# own, and a .po holds them in that order. DOS_ORDER and PRODOS_ORDER give the place in the
# file of each physical sector of a track: physical sector 1 is the eighth sector of a track in
# a .dsk, DOS 3.3's sector 7, and the ninth in a .po. The Agat emulator agatemulator reads a
# .dsk and a .po by these orders, and the 1'120 sectors of two Agat disks found both as .dsk
# and as .nic match DOS_ORDER.
#
# A .dsk is in DOS 3.3 order as a rule, but some tools write ProDOS order under that name. A
# ProDOS disk keeps the header of its volume directory in block 2; when that header is where a
# .po has it and not where a DOS-ordered image has it, the .dsk is read as a .po. A DOS 3.3 disk
# written in ProDOS order would go unnoticed, as no such image has turned up.
#
# A DOS 3.3 disk keeps the root of its catalog, the VTOC, in track 17, sector 0: in bytes 1 and
# 2 the catalog's first track and sector, in byte 3 the DOS release, 3, in byte 6 the volume
# number, in byte 0x35 the sectors per track, 16, in bytes 0x36 and 0x37 the bytes per sector,
# 256. The volume number there is the one DOS gave the address fields, so a disk laid out from
# such an image gets it back.
#
# Agat images may end with 128 bytes more, a label. The two seen, of one author, hold
# "AGAT-DISK", six bytes of unknown use, from byte 15 a description in the CP866 code page,
# from byte 64 the author's name, from byte 80 an e-mail address and from byte 112 a short
# name, each padded with zeros. A sector image made from such an image keeps it as it is.

DOS_ORDER = (0, 7, 14, 6, 13, 5, 12, 4, 11, 3, 10, 2, 9, 1, 8, 15)
PRODOS_ORDER = (0, 8, 1, 9, 2, 10, 3, 11, 4, 12, 5, 13, 6, 14, 7, 15)
ORDERS = {"dsk": DOS_ORDER, "do": DOS_ORDER, "po": PRODOS_ORDER}
AGAT_LABEL_BYTES = 128
AGAT_LABEL_START = b"AGAT-DISK"
VTOC_TRACK = 17
PRODOS_DIRECTORY_BLOCK = 2


def block_of(image, block, order):
    """The 512 bytes of a ProDOS block in a sector image of an order: two physical sectors of
    a track, the first the one at the block's place in a .po."""
    track, first = divmod(2 * block, SECTORS)
    sectors = [PRODOS_ORDER.index(first), PRODOS_ORDER.index(first + 1)]
    offsets = [track * TRACK_BYTES + order[s] * SECTOR_BYTES for s in sectors]
    return b"".join(image[offset:offset + SECTOR_BYTES] for offset in offsets)


def is_prodos_directory(block, blocks):
    """Whether 512 bytes start the volume directory of a ProDOS disk of `blocks` blocks: no
    block before them, storage type F with a name of 1 to 15 characters (Agat disks set their
    top bits at will), entries of 39 bytes, 13 to a block, and a size and a bitmap block that
    fit the disk. The tests are those of dsk_tools, a library of disk tools for the Agat."""
    if len(block) < 0x2B or block[0] or block[1] or block[4] >> 4 != 0xF or not block[4] & 0xF:
        return False
    if any(c & 0x7F < 0x20 for c in block[5:5 + (block[4] & 0xF)]):
        return False
    bitmap, total = struct.unpack_from("<HH", block, 0x27)
    return block[0x23] == 0x27 and block[0x24] == 0x0D and 0 < bitmap < total <= blocks


def dos33_volume(disk):
    """The volume number in the VTOC of a DOS 3.3 disk, or None when it has none."""
    if len(disk.sectors) <= VTOC_TRACK:
        return None
    vtoc = disk.sectors[VTOC_TRACK][0].data
    if (vtoc[3] == 3 and vtoc[0x35] == SECTORS and vtoc[0x36:0x38] == b"\x00\x01"
            and 1 <= vtoc[1] < len(disk.sectors) and vtoc[2] < SECTORS
            and 1 <= vtoc[6] <= 254):
        return vtoc[6]
    return None


def agat_label_text(label):
    """What an Agat label says, its short name and description, or None if it is no such
    label."""
    if not label.startswith(AGAT_LABEL_START):
        return None

    def text(start, end):
        return label[start:end].split(b"\0")[0].decode("cp866").strip()

    return ": ".join(part for part in (text(112, 128), text(15, 64)) if part) or None


def read_sector_image(data, image_format, name):
    """Read a .dsk, .do or .po: return its SectorDisk and lines on what was found."""
    tracks, rest = divmod(len(data), TRACK_BYTES)
    if rest not in (0, AGAT_LABEL_BYTES) or not STANDARD_TRACKS <= tracks <= MAX_TRACKS:
        raise Failure(wrong_size(name, data, "a sector image holds %s bytes a track: %s for %d"
            " tracks, up to %s for %d, and an Agat image 128 more for its label"
            % (grouped(TRACK_BYTES), grouped(STANDARD_TRACKS * TRACK_BYTES), STANDARD_TRACKS,
            grouped(MAX_TRACKS * TRACK_BYTES), MAX_TRACKS)))
    notes = []
    order = ORDERS[image_format]
    blocks = tracks * TRACK_BYTES // 512
    if (image_format == "dsk"
            and is_prodos_directory(block_of(data, PRODOS_DIRECTORY_BLOCK, PRODOS_ORDER), blocks)
            and not is_prodos_directory(block_of(data, PRODOS_DIRECTORY_BLOCK, DOS_ORDER),
                blocks)):
        order = PRODOS_ORDER
        notes.append("its sectors are in ProDOS's order, as in a .po: its ProDOS volume"
            " directory is where a .po keeps it, so it is read as a .po")
    sectors = [[Sector(None, data[t * TRACK_BYTES + order[s] * SECTOR_BYTES:][:SECTOR_BYTES])
        for s in range(SECTORS)] for t in range(tracks)]
    return SectorDisk(sectors, data[tracks * TRACK_BYTES:]), notes


def sector_image_bytes(disk, image_format):
    order = ORDERS[image_format]
    image = bytearray()
    for sectors in disk.sectors:
        in_file = [None] * SECTORS
        for number, sector in enumerate(sectors):
            in_file[order[number]] = sector.data
        image += b"".join(in_file)
    return bytes(image) + disk.label


# =============================================================================================
# .nib: the nibbles of each track
# =============================================================================================
#
# A .nib holds each track as the nibbles the controller reads from it, 6'656 bytes a track, 35
# to 40 tracks. The two 0 bits after each sync byte are gone, so a .nib can't tell where
# sync bytes were. Played back, as FlashFloppy does it, at 8 bits a nibble, 32 us each, the
# latch falls into step on the data instead, and keeps it through the gaps of FF. 6'656
# nibbles take 213 ms, more than a turn; computers and emulators take the track as a ring all
# the same.
#
# Tools lay out the tracks they make in ways of their own, which read all alike, since the
# controller looks for the marks of the fields alone. Two Agat .nib images seen lay a track out
# as 128 bytes FF, then for each sector in physical order its address field, 5 FF, its data
# field and 40 FF; the .nib writer of the Agat emulator ecat3 as 48 FF, then for each sector its
# address field, 6 FF, its data field and 27 FF, then FF up to the end. A .nib from this tool
# holds what the latch reads from the source's tracks: from a sector image, the 6'608 nibbles
# of the track SDISK// would have, with 48 more FF. Whatever the source, the track is brought to
# 6'656 bytes in its longest gap: FF are added at the gap's start, or taken from its end,
# keeping 5 of them.

NIB_TRACK_BYTES = 6656
MIN_GAP = 5


def read_nib(data, name):
    tracks, rest = divmod(len(data), NIB_TRACK_BYTES)
    if rest or not STANDARD_TRACKS <= tracks <= MAX_TRACKS:
        raise Failure(wrong_size(name, data, "a .nib holds %s bytes a track: %s for %d tracks,"
            " up to %s for %d" % (grouped(NIB_TRACK_BYTES),
            grouped(STANDARD_TRACKS * NIB_TRACK_BYTES), STANDARD_TRACKS,
            grouped(MAX_TRACKS * NIB_TRACK_BYTES), MAX_TRACKS)))
    return TrackDisk([bits_of_bytes(data[t * NIB_TRACK_BYTES:(t + 1) * NIB_TRACK_BYTES])
        for t in range(tracks)])


def gaps(ring):
    """The gaps of a ring of nibbles, runs of FF outside the fields, as (start, length), longest
    first and else in ring order; a run that goes over the end of the ring is one, starting
    near the end. Runs of FF within fields, as data may hold, are no gaps; on a track without
    fields, every run of FF is one."""
    size = len(ring)
    inside = bytearray(size)
    for prologue, length in ((ADDRESS_PROLOGUE, ADDRESS_FIELD_BYTES),
            (DATA_PROLOGUE, DATA_FIELD_BYTES)):
        wrapped = ring + ring[:length]
        at = wrapped.find(prologue)
        while 0 <= at < size:
            for i in range(at, at + length):
                inside[i % size] = 1
            at = wrapped.find(prologue, at + 1)
    runs = [(m.start(), m.end() - m.start()) for m in re.finditer(rb"\xFF+", ring)]
    if len(runs) > 1 and runs[0][0] == 0 and sum(runs[-1]) == size:
        start, length = runs.pop()
        runs[0] = (start, length + runs[0][1])
    free = [(start, length) for start, length in runs
        if not any(inside[(start + k) % size] for k in range(length))]
    return sorted(free or runs, key=lambda run: -run[1])


def fit_track(nibbles, length, track):
    """Lengthen or shorten a ring of nibbles to `length` by FF in its longest gaps."""
    if len(nibbles) <= length:
        runs = gaps(nibbles)
        at = runs[0][0] if runs else len(nibbles)
        return nibbles[:at] + b"\xFF" * (length - len(nibbles)) + nibbles[at:]
    excess = len(nibbles) - length
    dropped = set()
    for start, run in gaps(nibbles):
        take = min(excess - len(dropped), run - MIN_GAP)
        if take > 0:
            end = start + run
            dropped.update(i % len(nibbles) for i in range(end - take, end))
    if len(dropped) < excess:
        raise Failure("track %d holds %s nibbles, too many for the %s of a .nib track, even with"
            " its gaps cut short" % (track, grouped(len(nibbles)), grouped(length)))
    return bytes(b for i, b in enumerate(nibbles) if i not in dropped)


def nib_bytes(disk):
    return b"".join(fit_track(read_latch(bits)[0], NIB_TRACK_BYTES, track)
        for track, bits in enumerate(disk.tracks))


# =============================================================================================
# .nic: the image of SDISK//
# =============================================================================================
#
# SDISK// is an Apple2 floppy emulator on an SD card, and FlashFloppy plays its images as
# well. A .nic holds 35 to 40 tracks of 16 records of 512 bytes, 8 KB a track. Record s of a
# track holds physical sector s, as the bits to send, top bit first, in its first 416 bytes,
# laid out as told above for the track that SDISK// lays out, and zeros in the other 96. Sent
# back to back, the 16 records are the track, 53'248 bits. FlashFloppy writes what the
# computer writes into fixed places of the records, those of the address field and of the data
# field, by the sector number it decodes, so a .nic holds standard tracks only, and this tool
# writes every record in that layout.

NIC_RECORD_BYTES = 512
NIC_TRACK_BYTES = SECTORS * NIC_RECORD_BYTES


def read_nic(data, name):
    tracks, rest = divmod(len(data), NIC_TRACK_BYTES)
    if rest or not STANDARD_TRACKS <= tracks <= MAX_TRACKS:
        raise Failure(wrong_size(name, data, "a .nic holds %s bytes a track: %s for %d tracks,"
            " up to %s for %d" % (grouped(NIC_TRACK_BYTES),
            grouped(STANDARD_TRACKS * NIC_TRACK_BYTES), STANDARD_TRACKS,
            grouped(MAX_TRACKS * NIC_TRACK_BYTES), MAX_TRACKS)))
    records = [data[at:at + SDISK_SECTOR_BYTES] for at in range(0, len(data), NIC_RECORD_BYTES)]
    return TrackDisk([bits_of_bytes(b"".join(records[t * SECTORS:(t + 1) * SECTORS]))
        for t in range(tracks)])


def nic_bytes(disk):
    """The .nic of a SectorDisk whose sectors have volume numbers."""
    return b"".join(
        bytes_of_bits(sdisk_sector_bits(track, number, sector)).ljust(NIC_RECORD_BYTES, b"\0")
        for track, sectors in enumerate(disk.sectors)
        for number, sector in enumerate(sectors))


# =============================================================================================
# .hfe: the image of the HxC floppy emulator
# =============================================================================================
#
# An HFE file holds each track as flux cells, short time slots, each 1 if the flux reverses in
# it. HFE was made for the drives of PCs and the like, where FM and MFM put clock pulses
# between the data bits, so a data bit is two cells there: its header gives a bit rate, and
# cells come twice as fast, 2 us each at 250 kbit/s. An Apple2 bit of 4 us is then two cells
# too: a 1 has the flux reverse in one of them, "01" or "10", and a 0 is "00".
#
# The file is laid out in blocks of 512 bytes, and its numbers are little-endian.
#
# Block 0, the header: "HXCPICFE" for version 1 ("HXCHFEV3" for version 3, below), then bytes
# for the format revision, 0, the number of tracks, the number of sides and the track encoding
# (0 MFM, 2 FM, 6 and 7 Apple2 GCR, FF unknown), 16 bits for the bit rate in kbit/s and for the
# rpm (0 for unknown), bytes for the interface mode (7 a Shugart drive, FF none), one to
# ignore, 16 bits for the track list's block, then bytes for whether writes are allowed (FF
# yes) and for the step, FF for single step and 00 for double step, which FlashFloppy's Apple2
# mode needs to play one HFE track for each track. The rest of the block is FF.
#
# The track list: 4 bytes for each track, 16 bits for its first block and 16 for its length
# in bytes, both sides together.
#
# The tracks: each from its first block on, in blocks of 256 bytes of side 0 and 256 of side
# 1; the first cell of each byte is its bit 0.
#
# Version 3 adds opcodes among the cells: bytes whose first four cells are 1s, which no real
# track has. 0F does nothing, 8F marks the index, 4F sets a new bit rate from the byte after
# it, CF skips cells at the start of the byte after the next, as many as the next byte's bits,
# reversed, AND 7, and 2F stands for a byte of random cells, the weak bits of some copy
# protection. FlashFloppy takes any other such byte for an opcode that does nothing.
#
# The Apple2 HFEs seen come from three converters, all of them writing two cells a bit: this
# tool, and nic2hfe.py before it, a 1 as "01", the bits of each track as they come, 250 kbit/s,
# encoding FF; Greaseweazle, a 1 as "01", 256 kbit/s, encoding FF, 51'020 bits a track, about a
# turn at that rate; the HxC software, a 1 as "10", 255 kbit/s, encoding 7, 53'008 bits. Where
# FlashFloppy recorded a write, the reversals keep to pairs too, but the pairing shifts by a
# cell where the write begins and ends, and a capture of a real disk would have each reversal
# wherever it fell. So where reversals don't keep to one cell of their pairs, this tool counts
# the bits between them by a clock that follows them, as the controller's own logic does.

HFE_BLOCK = 512
HFE_SIGNATURES = {b"HXCPICFE": 1, b"HXCHFEV3": 3}
HFE_HEADER = "<BBBBHHBBHBB"  # after the signature
HFE_GCR_ENCODINGS = (6, 7)
HFE_UNKNOWN_ENCODING = 0xFF
DEFAULT_RATE_KBPS = 250  # 2 us cells, so 4 us bits
UNUSED_SIDE_FILL = 0x88  # what Greaseweazle fills the absent second side with
V3_BITRATE, V3_SKIP, V3_RANDOM = 0x4F, 0xCF, 0x2F  # the opcodes that do something here
CELLS_OF_HFE_BYTE = [format(byte, "08b")[::-1] for byte in range(256)]
CELLS_OF_BITS = str.maketrans({"0": "00", "1": "01"})


def cells_of_hfe_bytes(data):
    """The cells of HFE track bytes, as a string of "0" and "1", each byte's bit 0 first."""
    return format(int.from_bytes(data, "little"), "0%db" % (8 * len(data)))[::-1] if data else ""


def hfe_bytes_of_cells(cells):
    """The inverse of cells_of_hfe_bytes(), for a whole number of bytes."""
    return int(cells[::-1], 2).to_bytes(len(cells) // 8, "little") if cells else b""


def cells_of_v3_bytes(data):
    """The cells of HFE version 3 track bytes, its opcodes done; return them and the number of
    bytes of random cells, which become cells without a reversal."""
    cells = []
    skip = weak = 0
    at = 0
    while at < len(data):
        byte = data[at]
        at += 1
        if byte & 0x0F == 0x0F and byte != V3_RANDOM:
            if byte == V3_BITRATE:
                at += 1
            elif byte == V3_SKIP and at < len(data):
                skip = int(CELLS_OF_HFE_BYTE[data[at]], 2) & 7
                at += 1
            continue
        if byte == V3_RANDOM:
            weak += 1
            byte = 0
        cells.append(CELLS_OF_HFE_BYTE[byte][skip:])
        skip = 0
    return "".join(cells), weak


LONG_RUN = 32  # 1 bits in a row, as in the gaps, over which the cells a bit are measured


def cells_per_bit(reversals):
    """The cells a bit takes on a track, measured over its long runs of 1 bits, which the gaps
    of every formatted track have; 2 for a track without them."""
    cells = bits = 0
    run = []
    for before, after in zip(reversals, reversals[1:] + [None]):
        if after is not None and after - before <= 3:
            run.append(after - before)
            continue
        if len(run) >= LONG_RUN:
            cells += sum(run)
            bits += len(run)
        run = []
    return cells / bits if bits else 2.0


def bits_of_cells(cells):
    """The disk bits of a ring of HFE cells, two cells to a bit.

    When every reversal keeps to one cell of its pair, the bits are those cells. Otherwise a
    clock counts them: each reversal ends as many bits as the clock's periods since the last
    one, rounded, and the clock moves an eighth of the way to it. The period is measured first
    by cells_per_bit(), since a disk that turned a little slower than its cells were counted
    puts a reversal a cell late every few dozen bits, a gap of 1.5 periods by a period of 2;
    then it follows the reversals a 256th of the way, through slow changes of speed."""
    if "1" not in cells[0::2]:
        return cells[1::2]
    if "1" not in cells[1::2]:
        return cells[0::2]
    reversals = [m.start() for m in re.finditer("1", cells)]
    period = cells_per_bit(reversals)
    clock = float(reversals[0])
    lead = int(reversals[0] / period)
    bits = ["0" * lead + "1"]
    for at in reversals[1:]:
        count = max(1, int((at - clock) / period + 0.5))
        bits.append("0" * (count - 1) + "1")
        expected = clock + count * period
        clock = expected + (at - expected) / 8
        period = min(2.5, max(1.5, period + (at - expected) / count / 256))
    count = max(1, int((len(cells) - reversals[-1] + reversals[0]) / period + 0.5))
    bits.append("0" * max(0, count - 1 - lead))
    return "".join(bits)


def has_gcr_shape(cells):
    """Whether cells hold four 0s in a row, as GCR of two cells a bit does wherever two 0 bits
    follow each other, in the data of every formatted track, and MFM and FM never do."""
    return "0000" in cells


def read_hfe(data, name):
    """Read an HFE of an Apple2 disk: return its TrackDisk and lines on what was found."""
    version = HFE_SIGNATURES.get(data[:8])
    if version is None or len(data) < 2 * HFE_BLOCK:
        raise Failure(wrong_size(name, data, "an HFE file starts with \"HXCPICFE\" or"
            " \"HXCHFEV3\""))
    (_revision, tracks, sides, encoding, _rate, _rpm, _interface, _, list_block, _write,
        step) = struct.unpack_from(HFE_HEADER, data, 8)
    if encoding not in HFE_GCR_ENCODINGS + (HFE_UNKNOWN_ENCODING,):
        raise Failure("%s says its tracks are in %s, not in the GCR of an Apple2 disk, and has"
            " %d tracks; so it is an image of another kind of disk, or if it was meant to be an"
            " Apple2 one, the tool that made it failed" % (name, encoding_name(encoding), tracks))
    if not STANDARD_TRACKS <= tracks <= MAX_TRACKS or not 1 <= sides <= 2:
        raise Failure("%s has %d tracks on %d sides, where an Apple2 disk has %d to %d tracks on"
            " one side" % (name, tracks, sides, STANDARD_TRACKS, MAX_TRACKS))
    if list_block * HFE_BLOCK + 4 * tracks > len(data):
        raise Failure("%s ends within its list of tracks" % name)
    notes = []
    rings = []
    weak = []
    for track in range(tracks):
        block, length = struct.unpack_from("<HH", data, list_block * HFE_BLOCK + 4 * track)
        start = block * HFE_BLOCK
        side0 = b"".join(data[start + k:start + k + HFE_BLOCK // 2]
            for k in range(0, length, HFE_BLOCK))[:length // 2]
        if len(side0) < length // 2:
            raise Failure("%s ends within track %d" % (name, track))
        if version == 3:
            cells, weak_bytes = cells_of_v3_bytes(side0)
            if weak_bytes:
                weak.append(track)
        else:
            cells = cells_of_hfe_bytes(side0)
        if track == 0 and encoding == HFE_UNKNOWN_ENCODING and not has_gcr_shape(cells):
            raise Failure("%s holds no Apple2 GCR: its header doesn't say which encoding it"
                " has, and its track 0 never goes four cells without a flux reversal, as the 0"
                " bits of Apple2 data do, two cells a bit, so its cells look like the FM or MFM"
                " of other disks" % name)
        rings.append(bits_of_cells(cells))
    if sides == 2:
        notes.append("its second side is left out: an Apple2 disk has one")
    if step == 0xFF:
        notes.append("it is marked single-step, but its tracks are taken as whole tracks")
    if weak:
        notes.append("weak bits on %s, which no other format holds, become cells without a"
            " reversal" % counted("track", weak))
    return TrackDisk(rings), notes


def padded_bits(bits):
    """A ring of bits brought to a multiple of 4 bits, a whole number of HFE bytes, by 0s
    where a nibble starts, which the latch skips."""
    short = -len(bits) % 4
    if not short:
        return bits
    _, starts = read_latch(bits)
    at = starts[-1] if starts and starts[-1] + 8 > len(bits) else len(bits)
    return bits[:at] + "0" * short + bits[at:]


def hfe_header(tracks, rate_kbps, track_list_block):
    header = b"HXCPICFE" + struct.pack(
        "<BBBBHHBBHBBBBBB",
        0,  # format revision
        tracks,
        1,  # sides
        HFE_UNKNOWN_ENCODING,  # as Greaseweazle writes for Apple2
        rate_kbps,
        0,  # rpm, unknown
        0xFF,  # interface mode: none
        1,  # unused, 1 as Greaseweazle writes
        track_list_block,
        0xFF,  # writes allowed
        0x00,  # double step
        0xFF, 0xFF, 0xFF, 0xFF)  # no other encoding for track 0
    return header.ljust(HFE_BLOCK, b"\xFF")


def hfe_bytes(disk, rate_kbps):
    """The HFE version 1 of a TrackDisk: one side, double step, two cells a bit."""
    track_list = bytearray()
    body = bytearray()
    first_block = 2  # after the header and the track list
    for bits in disk.tracks:
        cells = hfe_bytes_of_cells(padded_bits(bits).translate(CELLS_OF_BITS))
        track_list += struct.pack("<HH", first_block + len(body) // HFE_BLOCK, 2 * len(cells))
        for k in range(0, len(cells), HFE_BLOCK // 2):
            body += cells[k:k + HFE_BLOCK // 2].ljust(HFE_BLOCK, bytes([UNUSED_SIDE_FILL]))
    return (hfe_header(len(disk.tracks), rate_kbps, 1) + bytes(track_list).ljust(HFE_BLOCK,
        b"\xFF") + bytes(body))


def encoding_name(encoding):
    names = {
        0: "MFM (track encoding 0), as on the disks of PCs",
        1: "Amiga MFM (track encoding 1)",
        2: "FM (track encoding 2), the single-density encoding of early floppies",
    }
    return names.get(encoding, "track encoding %d" % encoding)


# =============================================================================================
# The formats
# =============================================================================================

SECTOR_FORMATS = ("dsk", "do", "po")
TRACK_FORMATS = ("nib", "nic", "hfe")
FORMATS = SECTOR_FORMATS + TRACK_FORMATS
FORMAT_NAMES = {
    "dsk": "a sector image in DOS 3.3 order",
    "do": "a sector image in DOS 3.3 order",
    "po": "a sector image in ProDOS order",
    "nib": "a nibble image",
    "nic": "an SDISK// image",
    "hfe": "an HFE image",
}


def with_article(image_format):
    """A format's extension with its article: "a .nib", "an .hfe"."""
    return ("an ." if image_format == "hfe" else "a .") + image_format


def format_of(path):
    """The format of a path, by its extension: one of FORMATS, or None."""
    extension = os.path.splitext(path)[1][1:].lower()
    return extension if extension in FORMATS else None


def read_image(data, image_format, name):
    """Read an image: return its disk, a SectorDisk or a TrackDisk, and lines on what was
    found."""
    if image_format in SECTOR_FORMATS:
        return read_sector_image(data, image_format, name)
    if image_format == "nib":
        return read_nib(data, name), []
    if image_format == "nic":
        return read_nic(data, name), []
    return read_hfe(data, name)


def guess(data):
    """What an image's bytes look like, for a hint when they don't fit their extension."""
    size = len(data)
    if data[:8] in HFE_SIGNATURES:
        return "an HFE file"
    for track_bytes, extra, kind in ((NIB_TRACK_BYTES, 0, "a .nib"),
            (NIC_TRACK_BYTES, 0, "a .nic"), (TRACK_BYTES, 0, "a sector image"),
            (TRACK_BYTES, AGAT_LABEL_BYTES, "a sector image with an Agat label")):
        tracks, rest = divmod(size - extra, track_bytes)
        if not rest and STANDARD_TRACKS <= tracks <= MAX_TRACKS:
            return "%s of %d tracks" % (kind, tracks)
    if size == STANDARD_TRACKS * 13 * SECTOR_BYTES:
        return ("a sector image of 13-sector tracks, as DOS 3.2 formats them, which this tool"
            " doesn't handle")
    if size == 2 * 80 * 21 * SECTOR_BYTES:
        return ("an 840 KB Agat disk, two sides of 80 tracks of 21 sectors in MFM, which"
            " FlashFloppy plays in its Shugart mode, not in its Apple2 mode")
    return None


def wrong_size(name, data, rule):
    """The message for an image whose bytes don't fit its format."""
    text = "%s is not what its extension says: it has %s bytes, and %s" % (
        name, grouped(len(data)), rule)
    hint = guess(data)
    return text + (". Its size is that of %s" % hint if hint else "")


# =============================================================================================
# Converting
# =============================================================================================
#
# Sector images hold sectors, the other formats tracks, so a conversion goes one of four ways.
#
# Sectors to sectors: the sectors go into the target's order, and an Agat label goes along.
#
# Sectors to tracks: each track is laid out as SDISK// lays it out, with the volume number of
# --volume, else the one of a DOS 3.3 catalog on the disk, else 254. An Agat label has no place
# in a track image and is left out.
#
# Tracks to sectors, or into a .nic: each track is read as DOS reads it, and must hold its 16
# sectors intact, unless --salvage lets zeros stand for what can't be read. A .nic gets each
# sector anew, with the volume number of its address field. Tracks past the 35th that hold no
# fields at all are left out.
#
# Tracks into a .nib or an .hfe: the bits go across as they are, all of them into an .hfe, and
# as the nibbles the controller reads into a .nib.
#
# Every image is then read back as a source would be: its sectors must be those meant for it,
# and when tracks went across as they are, so must the nibbles of every track, the lengths of
# runs of FF aside, which a .nib may change.

class Conversion:
    """One image to convert: the source, the target and their formats."""

    def __init__(self, source, target):
        self.source = source
        self.target = target
        self.source_format = format_of(source)
        self.target_format = format_of(target)

    def decodes(self):
        """Whether the source's tracks are to be decoded into sectors."""
        return (self.source_format in TRACK_FORMATS
            and self.target_format in SECTOR_FORMATS + ("nic",))


class Product:
    """What a conversion makes: the image, a clause on it, and what reading it back must give.
    expected[track] maps sector numbers to Sectors, whose volume is None where the target keeps
    none; label is the Agat label; signatures holds the ring_signature() of each track when the
    tracks went across as they are, and is None otherwise."""

    def __init__(self, image, description, expected, label=b"", signatures=None):
        self.image = image
        self.description = description
        self.expected = expected
        self.label = label
        self.signatures = signatures


def faults_text(readings, limit=12):
    """Lines on what is wrong with the tracks, at most `limit` of them."""
    lines = []
    for track, reading in enumerate(readings):
        lines += ["track %d: %s" % (track, fault) for fault in reading.faults]
    if len(lines) > limit:
        lines[limit - 1:] = ["and %d more" % (len(lines) - limit + 1)]
    return lines


def volumes_text(volumes):
    counts = collections.Counter(volumes)
    if len(counts) == 1:
        return "volume %d" % next(iter(counts))
    return "volumes %s" % ", ".join("%d (%d sectors)" % item for item in counts.most_common())


def describe_source(disk, readings, image_format):
    """A clause on what a source holds."""
    what = "%s (.%s), " % (FORMAT_NAMES[image_format], image_format)
    if readings is None:
        what += "%d tracks" % len(disk.sectors)
        volume = dos33_volume(disk)
        if volume is not None:
            what += ", a DOS 3.3 disk of volume %d" % volume
        label = agat_label_text(disk.label)
        if label:
            what += ", with the Agat label \"%s\"" % label
        elif disk.label:
            what += ", with 128 bytes more at its end"
        return what
    what += "%d tracks, " % len(readings)
    standard = sum(reading.is_standard() for reading in readings)
    volumes = [sector.volume for reading in readings for sector in reading.sectors.values()]
    if standard == len(readings):
        return what + "every one with its 16 sectors intact, %s" % volumes_text(volumes)
    if not volumes:
        return what + "not one sector of them readable"
    return what + "%d of them with their 16 sectors intact, %s" % (standard,
        volumes_text(volumes))


KEEPS = {
    "dsk": "A .dsk keeps the 256 bytes of each of the 16 sectors of a track and nothing else",
    "do": "A .do keeps the 256 bytes of each of the 16 sectors of a track and nothing else",
    "po": "A .po keeps the 256 bytes of each of the 16 sectors of a track and nothing else",
    "nic": "A .nic keeps each of the 16 sectors of a track in a record of its own, laid out as"
        " SDISK// lays it out",
}
SALVAGE_ADVICE = ("%(keeps)s, so it can take a track only when every one of its sectors reads"
    " intact. Copy-protected disks lay out some tracks in ways of their own, and in a damaged"
    " image some sectors no longer read. To keep the tracks as they are, convert to .nib or"
    " .hfe instead; to write a .%(target)s anyway, with zeros for what can't be read, give"
    " --salvage.")


def decoded(conversion, readings, salvage, lines):
    """The SectorDisk of tracks read, with the volume number of each sector. Unless every track
    is standard, raise a Failure that says why the target can't hold them, or with salvage let
    zeros stand for the sectors that can't be read, and add lines that say so."""
    refusal = ("%s can't become a .%s without losing what these tracks hold:"
        % (conversion.source, conversion.target_format), SALVAGE_ADVICE % {
            "target": conversion.target_format, "keeps": KEEPS[conversion.target_format]})
    return decoded_tracks(readings, salvage, lines, refusal,
        "the .%s" % conversion.target_format)


def decoded_tracks(readings, salvage, lines, refusal, what):
    """decoded() for any purpose: refusal is the first and the last line of the Failure, what
    names what gets the tracks."""
    count = len(readings)
    while count > STANDARD_TRACKS and readings[count - 1].blank:
        count -= 1
    if count < len(readings):
        lines.append("  %s hold no fields at all, so %s gets %d tracks" % (
            counted("track", range(count, len(readings))), what, count))
        readings = readings[:count]
    bad = [track for track, reading in enumerate(readings) if not reading.is_standard()]
    if bad and not salvage:
        raise Failure("\n".join([refusal[0]] + ["  " + line for line in faults_text(readings)]
            + [refusal[1]]))
    lost = Sector(usual_volume(readings), bytes(SECTOR_BYTES))
    sectors = [[reading.sectors.get(s, lost) for s in range(SECTORS)] for reading in readings]
    if bad:
        zeroed = sum(SECTORS - len(reading.sectors) for reading in readings)
        lines.append("  salvaged: %d sectors that can't be read are zeros; on %s:"
            % (zeroed, counted("track", bad)))
        lines += ["    " + line for line in faults_text(readings)]
    return SectorDisk(sectors)


def usual_volume(readings):
    """The volume number most sectors read have, or the default if none can be read."""
    volumes = collections.Counter(
        sector.volume for reading in readings for sector in reading.sectors.values())
    return volumes.most_common(1)[0][0] if volumes else DEFAULT_VOLUME


def disk_volumes(disk):
    return volumes_text([sector.volume for sectors in disk.sectors for sector in sectors])


def expected_of(disk):
    """What reading back must give for a SectorDisk: every sector, by physical number."""
    return [dict(enumerate(sectors)) for sectors in disk.sectors]


def sectors_with_volumes(conversion, disk, readings, options, lines):
    """The sectors for a .nic or a track image made from them, each with its volume number, and
    a clause on that number."""
    if readings is not None:
        sector_disk = decoded(conversion, readings, options.salvage, lines)
        return sector_disk, disk_volumes(sector_disk)
    volume, whence = options.volume, "as --volume says"
    if volume is None:
        volume, whence = dos33_volume(disk), "as the disk's DOS 3.3 catalog says"
    if volume is None:
        volume, whence = DEFAULT_VOLUME, "the usual one"
    if disk.label:
        lines.append("  its Agat label has no place in %s and is left out"
            % with_article(conversion.target_format))
    return with_volume(disk, volume), "volume %d, %s" % (volume, whence)


def make_sector_image(conversion, disk, readings, options, lines):
    if readings is None:
        sector_disk = disk
    else:
        sector_disk = decoded(conversion, readings, options.salvage, lines)
        lines.append("  a .%s has no place for the volume number: the disk had %s" % (
            conversion.target_format, disk_volumes(sector_disk)))
    # Read back, a sector image gives its sectors' bytes and nothing else.
    expected = [{number: Sector(None, sector.data) for number, sector in enumerate(sectors)}
        for sectors in sector_disk.sectors]
    return Product(sector_image_bytes(sector_disk, conversion.target_format),
        "%d tracks of 16 sectors" % len(sector_disk.sectors), expected, sector_disk.label)


def make_nic(conversion, disk, readings, options, lines):
    sector_disk, volumes = sectors_with_volumes(conversion, disk, readings, options, lines)
    return Product(nic_bytes(sector_disk), "%d tracks laid out as SDISK// lays them out, %s"
        % (len(sector_disk.sectors), volumes), expected_of(sector_disk))


def make_track_image(conversion, disk, readings, options, lines):
    """A .nib or an .hfe: from a sector image, its tracks laid out as SDISK// lays them out;
    from a track image, its tracks as they are."""
    nib = conversion.target_format == "nib"
    if readings is None:
        sector_disk, volumes = sectors_with_volumes(conversion, disk, readings, options, lines)
        tracks = tracks_of_sector_disk(sector_disk)
        description = "%d tracks laid out as SDISK// lays them out, %s" % (
            len(tracks.tracks), volumes)
        expected, signatures = expected_of(sector_disk), None
    else:
        tracks = disk
        description = ("%d tracks, as the nibbles the controller reads from the source's" if nib
            else "%d tracks, bit for bit as the source's") % len(tracks.tracks)
        expected = [reading.sectors for reading in readings]
        signatures = [ring_signature(read_latch(bits)[0]) for bits in tracks.tracks]
        if not all(reading.is_standard() for reading in readings):
            lines.append("  these go across as they are:")
            lines += ["    " + line for line in faults_text(readings)]
    if nib:
        image = nib_bytes(tracks)
    else:
        image = hfe_bytes(tracks, options.rate)
        description += ", %d kbit/s" % options.rate
    return Product(image, description, expected, signatures=signatures)


MAKERS = {"dsk": make_sector_image, "do": make_sector_image, "po": make_sector_image,
    "nic": make_nic, "nib": make_track_image, "hfe": make_track_image}


def ring_signature(nibbles):
    """The nibbles of a turn with each run of FF cut to one, for comparing two tracks whose
    gaps may differ in length."""
    signature = re.sub(rb"\xFF+", b"\xFF", nibbles)
    if len(signature) > 1 and signature[0] == signature[-1] == 0xFF:
        signature = signature[1:]
    return signature


def same_ring(one, other):
    return len(one) == len(other) and (one + one).find(other) >= 0


def same_sector(found, wanted):
    """Whether a sector read back is the one wanted, in what the wanted one tells."""
    return (found.data == wanted.data
        and wanted.volume in (None, found.volume) and wanted.spare in (None, found.spare))


def verify(product, target_format):
    """Read a product's image as a source would be read, and compare it with what it should
    hold; return a line for each difference."""
    disk, _ = read_image(product.image, target_format, "the image made")
    if isinstance(disk, SectorDisk):
        found = [dict(enumerate(sectors)) for sectors in disk.sectors]
        problems = [] if disk.label == product.label else ["its Agat label differs"]
    else:
        found = [reading.sectors for reading in read_tracks(disk)]
        problems = []
    if len(found) != len(product.expected):
        return problems + ["it has %d tracks, not %d" % (len(found), len(product.expected))]
    for track, (want, got) in enumerate(zip(product.expected, found)):
        wrong = [number for number, sector in sorted(want.items())
            if number not in got or not same_sector(got[number], sector)]
        if wrong:
            problems.append("track %d: %s differ" % (track, counted("sector", wrong)))
    if product.signatures is not None:
        rings = [ring_signature(read_latch(bits)[0]) for bits in disk.tracks]
        problems += ["track %d: its nibbles differ" % track
            for track, (one, other) in enumerate(zip(product.signatures, rings))
            if not same_ring(one, other)]
    return problems


def convert(conversion, options):
    """Convert one image as the options say; return 0 when it is done, and 1 when what would be
    written differs from the source, and so is not written."""
    check_backup(conversion.target)
    with open(conversion.source, "rb") as handle:
        data = handle.read()
    disk, notes = read_image(data, conversion.source_format, conversion.source)
    readings = read_tracks(disk) if isinstance(disk, TrackDisk) else None
    lines = ["%s: %s" % (conversion.source,
        describe_source(disk, readings, conversion.source_format))]
    lines += ["  " + note for note in notes]
    try:
        product = MAKERS[conversion.target_format](conversion, disk, readings, options, lines)
    finally:
        for line in lines:
            say(line)
    checked = ""
    if options.verify:
        problems = verify(product, conversion.target_format)
        if problems:
            say("%s: not written: read back, it would differ from the source, which is a bug"
                " in this tool:" % conversion.target)
            for problem in problems:
                say("  " + problem)
            return 1
        checked = "; verified: %d sectors%s as in the source" % (
            sum(len(sectors) for sectors in product.expected),
            "" if product.signatures is None else " and the nibbles of every track")
    say("%s: %s: %s%s" % (conversion.target, install(conversion.target, product.image,
        options.dry_run), product.description, checked))
    return 0


def check_backup(target):
    """Refuse to go on when the target exists and its backup name is taken as well."""
    if os.path.lexists(target) and os.path.lexists(target + BACKUP_SUFFIX):
        raise Failure("%s exists, and so does %s; move one of them away"
            % (target, target + BACKUP_SUFFIX))


def install(target, image, dry_run):
    """Write the image to the target, an existing one renamed to its backup first, and say
    what was done."""
    check_backup(target)
    if os.path.isdir(target):
        raise Failure("%s is a folder" % target)
    backup = target + BACKUP_SUFFIX
    renamed = ", the old one renamed to %s" % backup if os.path.lexists(target) else ""
    if dry_run:
        return "would be written" + renamed
    # Written aside first, so that a failed write leaves the old target as it was.
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(target)),
        prefix=".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(image)
        if renamed:
            os.replace(target, backup)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return "written" + renamed


# =============================================================================================
# The Agat's characters
# =============================================================================================
#
# The Agat draws 256 characters. DOS writes A0-FF: A0-DF are the ASCII of 20-5F, with a
# currency sign in place of the dollar, and E0-FF the Cyrillic capitals in the order of KOI-7.
# 40-7F hold the small letters, Latin and Cyrillic, in the same orders: BASIC keeps the
# characters of a string without the top bit and prints them with it. 00-3F and 80-9F are fill
# patterns, block elements, box drawing and symbols; one that has no fair likeness in Unicode,
# or only an emoji, which some fonts draw in color, is an empty square here.

EMPTY_SQUARE = 0x25A1
# The small Cyrillic letters in the order of KOI-7: from 60, and from E0 as capitals.
KOI7_CYRILLIC = (0x44E, 0x430, 0x431, 0x446, 0x434, 0x435, 0x444, 0x433, 0x445, 0x438, 0x439,
    0x43A, 0x43B, 0x43C, 0x43D, 0x43E, 0x43F, 0x44F, 0x440, 0x441, 0x442, 0x443, 0x436, 0x432,
    0x44C, 0x44B, 0x437, 0x448, 0x44D, 0x449, 0x447, 0x44A)
AGAT_GRAPHICS = (
    # 00: fill patterns, zigzags and a thin line as plain lines, a bar, diagonals, triangles.
    0x25A8, 0x25A7, 0x25A7, 0x25A4, 0x2592, 0x25A5, 0x2500, 0x2502,
    0x2500, 0x2500, 0x25AC, 0x2572, 0x25E3, 0x2571, 0x25E2, 0x2573,
    # 10: fill patterns, then the lower eighths of the cell and the left ones.
    0x25A8, 0x25A7, 0x25A7, 0x2581, 0x2582, 0x2583, 0x2584, 0x2585,
    0x2586, 0x2587, 0x258F, 0x258E, 0x258D, 0x258C, 0x258B, 0x258A,
    # 20: light box drawing.
    0x0020, 0x256D, 0x256E, 0x2570, 0x256F, 0x250C, 0x2510, 0x2514,
    0x2518, 0x2500, 0x2502, 0x251C, 0x2524, 0x252C, 0x2534, 0x253C,
    # 30: heavy box drawing, but for the arcs, which Unicode has light only.
    0x0020, 0x256D, 0x256E, 0x2570, 0x256F, 0x250F, 0x2513, 0x2517,
    0x251B, 0x2501, 0x2503, 0x2523, 0x252B, 0x2533, 0x253B, 0x254B,
)
AGAT_SYMBOLS = (
    # 80: a face, a dot, squares, a house, arrows, a ceiling, for-all, there-exists, two men.
    EMPTY_SQUARE, 0x00B7, EMPTY_SQUARE, 0x22A1, 0x25A3, 0x25A1, EMPTY_SQUARE, 0x2302,
    0x2190, 0x2309, 0x2200, 0x2203, 0x2B09, EMPTY_SQUARE, 0x1FBC5, EMPTY_SQUARE,
    # 90: a diameter, the suits, arrows, game creatures, a degree sign, a low ring.
    0x2300, 0x2664, 0x2667, 0x2662, 0x2661, 0x2192, EMPTY_SQUARE, EMPTY_SQUARE,
    0x2B0B, 0x2191, 0x2193, 0x00B0, 0x02F3, 0x2B08, 0x2B0A, EMPTY_SQUARE,
)


def agat_chars():
    """The character of each of the 256 codes."""
    chars = [chr(code) for code in AGAT_GRAPHICS]
    chars += [chr(code) for code in range(0x60, 0x80)]
    # The ASCII bar and DEL, drawn as a broken bar and a full block.
    chars[0x5C], chars[0x5F] = chr(0xA6), chr(0x2588)
    chars += [chr(code) for code in KOI7_CYRILLIC]
    chars += [chr(code) for code in AGAT_SYMBOLS]
    chars += [chr(code) for code in range(0x20, 0x60)]
    chars[0xA4] = chr(0xA4)
    chars += [chr(code - 0x20) for code in KOI7_CYRILLIC]
    return tuple(chars)


AGAT_CHARS = agat_chars()


def agat_text(data):
    return "".join(AGAT_CHARS[byte] for byte in data)


def readable(byte):
    """Whether a code is a letter, a digit or a sign, as opposed to graphics."""
    return byte >= 0xA0 or 0x40 <= byte < 0x80


# The tokens of the Agat's BASIC, 80 to EA, which Applesoft's are but for a few: GR=, TEXT=,
# MGR=, HGR= and RIBBON= for its own graphics, ! for its assembler, and the currency sign.
AGAT_BASIC_TOKENS = ("END", "FOR", "NEXT", "DATA", "INPUT", "DEL", "DIM", "READ", "GR=",
    "TEXT=", "PR#", "IN#", "CALL", "PLOT", "!", "&", "MGR=", "HGR=", "RIBBON=", "&", "DRAW",
    "XDRAW", "HTAB", "HOME", "ROT=", "SCALE=", "SHLOAD", "TRACE", "NOTRACE", "NORMAL",
    "INVERSE", "FLASH", "COLOR=", "POP", "VTAB", "HIMEM:", "LOMEM:", "ONERR", "RESUME",
    "RECALL", "STORE", "SPEED=", "LET", "GOTO", "RUN", "IF", "RESTORE", "&", "GOSUB", "RETURN",
    "REM", "STOP", "ON", "WAIT", "LOAD", "SAVE", "DEF", "POKE", "PRINT", "CONT", "LIST",
    "CLEAR", "GET", "NEW", "TAB(", "TO", "FN", "SPC(", "THEN", "AT", "NOT", "STEP", "+", "-",
    "*", "/", "^", "AND", "OR", ">", "=", "<", "SGN", "INT", "ABS", "USR", "FRE", "SCRN(",
    "PDL", "POS", "SQR", "RND", "LOG", "EXP", "COS", "SIN", "TAN", "ATN", "PEEK", "LEN",
    "STR" + chr(0xA4), "VAL", "ASC", "CHR" + chr(0xA4), "LEFT" + chr(0xA4),
    "RIGHT" + chr(0xA4), "MID" + chr(0xA4))


def basic_names(data):
    """The long variable names of an Agat BASIC program: data is the file, its length first,
    and the two bytes before the end of that length give the size of a table of names before
    them, each name ending with a character whose top bit is set. Applesoft has no such table,
    and ends with two zeros instead."""
    names = []
    if len(data) < 2:
        return names
    declared = data[0] | data[1] << 8
    if not 4 <= declared <= len(data):
        return names
    size = data[declared - 2] | data[declared - 1] << 8
    start = declared - 2 - size - 1
    if not size or start < 2:
        return names
    name = ""
    for byte in data[start:declared - 2]:
        if byte == 0:
            break
        name += AGAT_CHARS[byte | 0x80]
        if byte & 0x80:
            names.append(name)
            name = ""
    return names


def basic_listing(data):
    """A BASIC program as text, a line to a line. data is the file: the program's length in 2
    bytes, then its image in memory from $801 on, each line the address of the next one (0
    after the last), its number, its tokens and characters, and a 0. The Agat's BASIC has
    variables of long names too, given as 01 or 02 and a number in a table after the program.
    The characters of strings, of REM and of DATA are kept as they are."""
    names = basic_names(data)
    program = data[2:]
    lines = []
    at = 0
    while at + 4 <= len(program):
        link, number = struct.unpack_from("<HH", program, at)
        if link == 0:
            break
        at += 4
        text = ""
        quoted = verbatim = False
        while at < len(program) and program[at] != 0:
            byte = program[at]
            at += 1
            if byte in (1, 2) and not quoted and not verbatim and at < len(program):
                index = (byte - 1) << 8 | program[at]
                at += 1
                text += names[index - 1] if 0 < index <= len(names) else "?VAR%d?" % index
            elif byte >= 0x80 and not quoted and not verbatim:
                index = byte - 0x80
                word = AGAT_BASIC_TOKENS[index] if index < len(AGAT_BASIC_TOKENS) else (
                    "<%02X>" % byte)
                if not word[0].isalpha():
                    # An operator stands between its operands as the characters do.
                    text = text.rstrip(" ") + word
                else:
                    text = text.rstrip(" ") + " " + word + ("" if word.endswith("(") else " ")
                verbatim = word in ("REM", "DATA")
            else:
                char = AGAT_CHARS[byte if byte >= 0x80 else byte | 0x80]
                quoted ^= char == "\""
                verbatim = verbatim and not (char == ":" and not quoted and "DATA" in text)
                text += char
        at += 1
        lines.append("%d %s" % (number, text.strip(" ")))
    return "".join(line + "\n" for line in lines)


def text_view(data):
    """A text file as text: its characters to its first 00, return (8D) ending a line."""
    end = data.find(0)
    if end >= 0:
        data = data[:end]
    text = "".join("\n" if byte == 0x8D else AGAT_CHARS[byte] for byte in data)
    return text if text.endswith("\n") or not text else text + "\n"


# =============================================================================================
# Hex dumps
# =============================================================================================
#
# What has no file of its own, as the sectors of DOS itself or the label of an Agat image, is
# unpacked as a hex dump in the form of `hexdump -Cv`: the offset, 16 bytes in hex, then the
# same bytes as characters, the Agat's where it has a letter, a digit or a sign, else a dot.
# Packing reads the offset and the bytes and ignores the rest, and a line starting with "#" is
# a comment: so the bytes, not the characters, are to be edited.

def agat_char(byte):
    return AGAT_CHARS[byte] if readable(byte) else "."


def label_char(byte):
    """A character of an Agat label, which is ASCII and Cyrillic in code page 866."""
    if 0x20 <= byte < 0x7F:
        return chr(byte)
    return bytes([byte]).decode("cp866") if byte >= 0x80 else "."


def hexdump(data, offset=0, char=agat_char):
    """The lines of a hex dump of data, its first byte at offset."""
    lines = []
    for at in range(0, len(data), 16):
        row = data[at:at + 16]
        cells = " ".join("%02x" % byte for byte in row[:8])
        if len(row) > 8:
            cells += "  " + " ".join("%02x" % byte for byte in row[8:])
        lines.append("%08x  %s  |%s|" % (offset + at, cells, "".join(map(char, row))))
    return lines


def parse_hexdump(text, name):
    """The (offset, bytes) of each line of a hex dump."""
    chunks = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("|")[0].split()
        try:
            if len(fields) < 2 or any(len(field) != 2 for field in fields[1:]):
                raise ValueError
            chunks.append((int(fields[0], 16), bytes(int(field, 16) for field in fields[1:])))
        except ValueError:
            raise Failure("%s, line %d, is no line of a hex dump: an offset, then bytes in hex,"
                " two digits each, and what follows a \"|\" is left alone" % (name, number))
    return chunks


# =============================================================================================
# DOS 3.3 and the Agat's DOS
# =============================================================================================
#
# DOS numbers the sectors of a track in its own order, the order of a .dsk, and its catalog
# names sectors so. Track 17, sector 0 is the VTOC: bytes 1 and 2 the catalog's first track and
# sector, 3 the release of DOS, 6 the volume number, 27h the track/sector pairs a list holds,
# 34h to 37h the tracks, the sectors a track and the bytes a sector (35, 16, 256), and from 38h
# four bytes a track: in the first two a bit for each sector, set when it is free, sectors 15
# to 8 and then 7 to 0, top bits first.
#
# The catalog is a chain of sectors, each naming the next in bytes 1 and 2 (0 0 ends it) and
# holding seven entries of 35 bytes from 0Bh. An entry has the track and sector of the file's
# first track/sector list (track 0: no entry from here on; FF: deleted, the track then in the
# entry's last byte), its type, its name in 30 bytes with the top bit set, and its length in
# sectors. The type is a bit: 00 text (T), 01 Integer BASIC (I), 02 Applesoft (A), 04 binary
# (B), 08 S, 10, 20 and 40, which Apple's DOS calls R, a and b and the Agat's shows as Cyrillic
# letters, PE, KA and DE; bit 7 locks the file. The Agat's DOS takes FF for a directory, whose
# own catalog starts with an entry for the directory above it.
#
# A track/sector list names the next one in bytes 1 and 2, and from 0Ch the file's sectors in
# pairs, 0 0 where the file has none. An Applesoft or Integer BASIC program starts with its
# length in 2 bytes, a binary file with its address and its length; a text file runs to its
# first 00.

DOS_TYPES = {0x00: "T", 0x01: "I", 0x02: "A", 0x04: "B", 0x08: "S", 0x10: chr(0x41F),
    0x20: chr(0x41A), 0x40: chr(0x414)}
DOS_HEADERS = {"I": 2, "A": 2, "B": 4}
DOS_ENTRIES_AT = 0x0B
DOS_ENTRY_BYTES = 35
DOS_ENTRIES = 7
DOS_PAIRS_AT = 0x0C
DOS_PAIRS = 122  # what fits a sector, and what DOS 3.3 writes into the VTOC
BOOT_TRACKS = 3  # where DOS keeps itself


def dos_offset(track, sector):
    return (track * SECTORS + sector) * SECTOR_BYTES


def dos_sector(image, track, sector):
    return bytes(image[dos_offset(track, sector):dos_offset(track, sector) + SECTOR_BYTES])


class DosEntry:
    """A catalog entry: where is (track, sector, index) of the catalog sector, folder the names
    of the directories it is in and parent the entry of the last of them, positions the (track,
    sector) of each data sector of a file, None where it has none, and lists those of its
    track/sector lists."""

    def __init__(self, where, raw, folder, parent):
        self.where = where
        self.raw = raw
        self.folder = folder
        self.parent = parent
        self.positions = []
        self.lists = []

    @property
    def deleted(self):
        return self.raw[0] == 0xFF

    @property
    def directory(self):
        return self.raw[2] == 0xFF and not self.deleted

    @property
    def locked(self):
        return bool(self.raw[2] & 0x80) and not self.directory

    @property
    def type_letter(self):
        if self.directory:
            return "directory"
        return DOS_TYPES.get(self.raw[2] & 0x7F, "%02X" % (self.raw[2] & 0x7F))

    @property
    def name(self):
        return agat_text(self.raw[3:33]).rstrip(" ")

    @property
    def start(self):
        """The first track/sector list, or a directory's first catalog sector."""
        return (self.raw[0x20] if self.deleted else self.raw[0]), self.raw[1]


class DosDisk:
    """The catalog of a DOS 3.3 disk: its entries in the order of the catalog, and what each
    sector holds, as role[(track, sector)]."""

    def __init__(self, image, tracks):
        self.image = image
        self.tracks = tracks
        vtoc = dos_sector(image, VTOC_TRACK, 0)
        self.release, self.volume = vtoc[3], vtoc[6]
        self.pairs = vtoc[0x27] if 0 < vtoc[0x27] <= DOS_PAIRS else DOS_PAIRS
        self.entries = []
        self.role = {(VTOC_TRACK, 0): "the VTOC"}
        self.notes = []
        self.walk((vtoc[1], vtoc[2]), (), None, None)

    def inside(self, track, sector):
        return 0 <= track < self.tracks and 0 <= sector < SECTORS

    def claim(self, place, role):
        if place in self.role:
            self.notes.append("track %d, sector %d is both %s and %s" % (place + (
                self.role[place], role)))
        else:
            self.role[place] = role

    def walk(self, start, folder, parent, directory):
        """Read a catalog chain and the directories it holds: parent is the first sector of the
        catalog above, directory the entry of this one."""
        place = start
        seen = set()
        where_name = "/".join(folder) or "the catalog"
        while place[0] != 0 and self.inside(*place) and place not in seen:
            seen.add(place)
            self.claim(place, "a sector of %s" % where_name)
            sector = dos_sector(self.image, *place)
            for index in range(DOS_ENTRIES):
                at = DOS_ENTRIES_AT + index * DOS_ENTRY_BYTES
                raw = sector[at:at + DOS_ENTRY_BYTES]
                if raw[0] == 0:
                    return
                entry = DosEntry(place + (index,), raw, folder, directory)
                if entry.directory and parent is not None and entry.start == parent:
                    continue  # the entry back to the directory above
                self.entries.append(entry)
                if entry.directory:
                    self.walk(entry.start, folder + (entry.name,), start, entry)
                elif not entry.deleted:
                    self.read_lists(entry)
            place = (sector[1], sector[2])

    def read_lists(self, entry):
        """The data sectors of a file, from its chain of track/sector lists."""
        place = entry.start
        while place[0] != 0 and self.inside(*place) and place not in entry.lists:
            entry.lists.append(place)
            self.claim(place, "a track/sector list of %s" % entry.name)
            sector = dos_sector(self.image, *place)
            for pair in range(self.pairs):
                track, number = sector[DOS_PAIRS_AT + 2 * pair:DOS_PAIRS_AT + 2 * pair + 2]
                if track == 0 or not self.inside(track, number):
                    entry.positions.append(None)
                else:
                    entry.positions.append((track, number))
            place = (sector[1], sector[2])
        while entry.positions and entry.positions[-1] is None:
            entry.positions.pop()
        for position in entry.positions:
            if position is not None:
                self.claim(position, "data of %s" % entry.name)

    def free(self, track, sector):
        """Whether the VTOC marks a sector free."""
        vtoc = dos_sector(self.image, VTOC_TRACK, 0)
        at = 0x38 + 4 * track
        if at + 2 > SECTOR_BYTES:
            return False
        bits = vtoc[at] << 8 | vtoc[at + 1]
        return bool(bits >> sector & 1)

    def data(self, entry):
        """A file's bytes: its sectors one after the other, zeros where it has none."""
        return b"".join(bytes(SECTOR_BYTES) if position is None
            else dos_sector(self.image, *position) for position in entry.positions)


def read_dos(image, tracks):
    """The DosDisk of a disk held in DOS order, or None if it has no DOS 3.3 catalog."""
    if tracks <= VTOC_TRACK:
        return None
    vtoc = dos_sector(image, VTOC_TRACK, 0)
    if (vtoc[0x35] != SECTORS or vtoc[0x36:0x38] != b"\x00\x01" or not 0 < vtoc[1] < tracks
            or vtoc[2] >= SECTORS):
        return None
    return DosDisk(image, tracks)


def split_file(entry, data):
    """A file's (header, contents): the header that DOS keeps before the contents, and the
    contents as a file of their own, to the length the header gives, or with no header to
    the last byte that isn't zero."""
    size = DOS_HEADERS.get(entry.type_letter, 0)
    if not size:
        return b"", data.rstrip(b"\0")
    header = data[:size]
    length = struct.unpack_from("<H", header, size - 2)[0] if len(header) == size else 0
    return header, data[size:size + length]


def tail_dump(entry, data, end):
    """The hex dump of what a file's sectors hold after its end, sector by sector at their
    places on the disk, trailing zeros left out."""
    lines = []
    for index, position in enumerate(entry.positions):
        first = max(end - index * SECTOR_BYTES, 0)
        if position is None or first >= SECTOR_BYTES:
            continue
        chunk = data[index * SECTOR_BYTES + first:(index + 1) * SECTOR_BYTES].rstrip(b"\0")
        if chunk:
            lines += hexdump(chunk, dos_offset(*position) + first)
    return lines


# =============================================================================================
# Unpacking and packing
# =============================================================================================
#
# unpack writes an image as a folder, IMAGE.files: the files of its catalog under files/, a
# folder to each directory, and what is in no file as hex dumps: the sectors of DOS itself on
# tracks 0 to 2 (boot.hex), the VTOC, the catalog and the track/sector lists (catalog.hex), and
# any other sector that isn't all zeros (unused.hex: deleted files, leftovers), or, without a
# catalog, every such sector (sectors.hex). disk.json holds the rest: the format, the tracks,
# the volume numbers and spare bits of a track image, and for each file where its entry is, the
# header DOS keeps before it and what follows it in its last sector. Fields named _x are for
# reading and are ignored by pack, and so are the listings of BASIC programs and the text of
# text files written beside them.
#
# pack writes the image back: the hex dumps first, then each file into the sectors its
# track/sector lists name. A file that has changed gets its header's length anew and no tail,
# and a file that no longer fits its sectors stops the pack: the catalog is not rewritten.

FILES_SUFFIX = ".files"
FILES_FOLDER = "files"
DUMPS = ("boot.hex", "catalog.hex", "unused.hex", "sectors.hex", "tails.hex")
LABEL_DUMP = "label.hex"
DISK_JSON = "disk.json"
LISTING_SUFFIX = ".txt"
FORBIDDEN = set("<>:\"/\\|?*")
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % n for n in range(1, 10)} | {
    "LPT%d" % n for n in range(1, 10)}


def place_key(track, sector):
    return "%d:%d" % (track, sector)


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def host_name(name, used):
    """A file name the host takes for a name of the catalog, unlike any in used (case aside),
    and added to it."""
    clean = "".join("_" if char in FORBIDDEN or ord(char) < 32 else char for char in name)
    clean = clean.rstrip(" .") or "_"
    if clean.split(".")[0].upper() in RESERVED:
        clean = "_" + clean
    candidate, number = clean, 1
    while candidate.casefold() in used:
        number += 1
        stem, dot, extension = clean.rpartition(".") if "." in clean else (clean, "", "")
        candidate = "%s~%d%s%s" % (stem, number, dot, extension)
    used.add(candidate.casefold())
    return candidate


def dos_image(disk):
    """The sectors of a SectorDisk in DOS order, one after the other, as a .dsk has them."""
    image = bytearray(len(disk.sectors) * TRACK_BYTES)
    for track, sectors in enumerate(disk.sectors):
        for physical, sector in enumerate(sectors):
            at = dos_offset(track, DOS_ORDER[physical])
            image[at:at + SECTOR_BYTES] = sector.data
    return image


def spare_modes(disk):
    """The spare pairs of a disk's sectors as disk.json tells them: "zero" or "dos" (as DOS
    3.3 fills them, with the pairs of bytes 0 and 1), whichever more sectors have, and the
    sectors that have neither."""
    def dos_spare(data):
        return (swapped_low_bits(data[0]), swapped_low_bits(data[1]))
    counts = collections.Counter()
    for sectors in disk.sectors:
        for sector in sectors:
            spare = sector.spare or (0, 0)
            counts["zero"] += spare == (0, 0)
            counts["dos"] += spare == dos_spare(sector.data)
    mode = "dos" if counts["dos"] > counts["zero"] else "zero"
    exceptions = {}
    for track, sectors in enumerate(disk.sectors):
        for physical, sector in enumerate(sectors):
            spare = tuple(sector.spare or (0, 0))
            wanted = dos_spare(sector.data) if mode == "dos" else (0, 0)
            if spare != wanted:
                exceptions[place_key(track, physical)] = list(spare)
    return mode, exceptions


def describe_files(dos):
    files = [entry for entry in dos.entries if not entry.directory and not entry.deleted]
    directories = [entry for entry in dos.entries if entry.directory]
    deleted = [entry for entry in dos.entries if entry.deleted]
    what = "DOS 3.3, release %d, volume %d: %s" % (dos.release, dos.volume,
        counted_things("file", len(files)))
    if directories:
        what += " in %s" % counted_things("directory", len(directories) + 1, "directories")
    if deleted:
        what += ", %d deleted" % len(deleted)
    return what


def counted_things(noun, number, plural=None):
    return "%d %s" % (number, noun if number == 1 else plural or noun + "s")


def unpacked(disk, image_format, source, salvaged):
    """The folder of a SectorDisk, as {path: bytes}, and lines on what it holds."""
    tracks = len(disk.sectors)
    image = dos_image(disk)
    track_source = image_format in TRACK_FORMATS
    meta = {"format": image_format, "tracks": tracks, "file_system": None,
        "_source": os.path.basename(source), "_tool": PROGRAM}
    if track_source:
        volumes = collections.Counter(sector.volume for sectors in disk.sectors
            for sector in sectors)
        volume = volumes.most_common(1)[0][0]
        meta["volume"] = volume
        meta["volumes"] = {place_key(track, physical): sector.volume
            for track, sectors in enumerate(disk.sectors)
            for physical, sector in enumerate(sectors) if sector.volume != volume}
        meta["spare_pairs"], meta["spare_exceptions"] = spare_modes(disk)
        if salvaged:
            meta["_unreadable"] = salvaged
    contents = {}
    lines = []
    dumps = collections.defaultdict(list)
    dos = read_dos(image, tracks)
    claimed = set()
    if dos is not None:
        meta["file_system"] = "dos33"
        meta["_dos"] = {"release": dos.release, "volume": dos.volume, "catalog":
            list(dos_sector(image, VTOC_TRACK, 0)[1:3])}
        lines.append("  " + describe_files(dos))
        lines += ["  " + note for note in dos.notes]
        records = []
        # The host folder of each directory's entry, and the names taken in each folder.
        folders = {None: FILES_FOLDER}
        used = collections.defaultdict(set)
        listings = 0
        for entry in dos.entries:
            record = {"catalog": list(entry.where), "_name": entry.name,
                "_type": entry.type_letter}
            if entry.deleted:
                record["_deleted"] = True
                records.append(record)
                continue
            folder = folders[entry.parent]
            names = used[folder.casefold()]
            if entry.directory:
                record["path"] = folders[entry] = folder + "/" + host_name(entry.name, names)
                contents[record["path"]] = None
                records.append(record)
                continue
            data = dos.data(entry)
            header, body = split_file(entry, data)
            path = folder + "/" + host_name(entry.name + "." + entry.type_letter, names)
            record.update({"path": path, "sha1": sha1(body), "_locked": entry.locked,
                "_sectors": len(entry.positions)})
            if header:
                record["header"] = header.hex()
            tail = tail_dump(entry, data, len(header) + len(body))
            if tail:
                dumps["tails.hex"] += ["# after the end of " + path] + tail
            if entry.type_letter == "B" and len(header) == 4:
                record["_address"] = "$%04X" % struct.unpack_from("<H", header)[0]
            contents[path] = body
            view = None
            if entry.type_letter == "A":
                view = basic_listing(header + body)
            elif entry.type_letter == "T":
                view = text_view(body)
            if view:
                listing = path + LISTING_SUFFIX
                names.add(listing.rsplit("/", 1)[1].casefold())
                contents[listing] = view.encode("utf-8")
                record["listing"] = {"path": listing, "sha1": sha1(contents[listing])}
                listings += 1
            records.append(record)
            claimed.update(position for position in entry.positions if position is not None)
        meta["files"] = records
        if listings:
            lines.append("  BASIC programs and text files get their text beside them, as"
                " NAME.txt, to read; pack ignores it")
    for track in range(tracks):
        for sector in range(SECTORS):
            place = (track, sector)
            data = dos_sector(image, track, sector)
            if place in claimed:
                continue
            if dos is None:
                dump, role = "sectors.hex", None
            elif place in dos.role:
                dump, role = "catalog.hex", dos.role[place]
            elif track < BOOT_TRACKS:
                dump, role = "boot.hex", None
            else:
                dump = "unused.hex"
                role = "free" if dos.free(track, sector) else "in use, but in no file"
            if not any(data) and dump != "catalog.hex":
                continue
            heading = "# track %d, sector %d%s" % (track, sector, ": " + role if role else "")
            dumps[dump] += [heading] + hexdump(data, dos_offset(track, sector))
    for dump, dump_lines in dumps.items():
        contents[dump] = "".join(line + "\n" for line in dump_lines).encode("utf-8")
    if disk.label:
        label = agat_label_text(disk.label)
        if label:
            meta["_label"] = label
        contents[LABEL_DUMP] = "".join(line + "\n" for line in ["# the label after the"
            " sectors"] + hexdump(disk.label, char=label_char)).encode("utf-8")
    contents[DISK_JSON] = (json.dumps(meta, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return contents, lines


def packed(read, name, options):
    """The SectorDisk, the label and the disk.json of a folder, whose files read(path) gives,
    None for one that is missing; and lines of warnings."""
    raw = read(DISK_JSON)
    if raw is None:
        raise Failure("%s has no %s: it is no folder that unpack wrote" % (name, DISK_JSON))
    try:
        meta = json.loads(raw.decode("utf-8"))
        tracks = int(meta["tracks"])
    except (ValueError, KeyError, TypeError) as error:
        raise Failure("%s/%s can't be read: %s" % (name, DISK_JSON, error))
    if not STANDARD_TRACKS <= tracks <= MAX_TRACKS:
        raise Failure("%s/%s says %d tracks, where an Apple2 disk has %d to %d"
            % (name, DISK_JSON, tracks, STANDARD_TRACKS, MAX_TRACKS))
    image = bytearray(tracks * TRACK_BYTES)
    for dump in DUMPS:
        text = read(dump)
        if text is None:
            continue
        for offset, data in parse_hexdump(text.decode("utf-8"), "%s/%s" % (name, dump)):
            if offset + len(data) > len(image):
                raise Failure("%s/%s writes beyond the %d tracks of the disk, at %Xh"
                    % (name, dump, tracks, offset))
            image[offset:offset + len(data)] = data
    label = bytearray()
    text = read(LABEL_DUMP)
    if text is not None:
        for offset, data in parse_hexdump(text.decode("utf-8"), "%s/%s" % (name, LABEL_DUMP)):
            if len(label) < offset + len(data):
                label.extend(bytes(offset + len(data) - len(label)))
            label[offset:offset + len(data)] = data
    if len(label) not in (0, AGAT_LABEL_BYTES):
        raise Failure("%s/%s holds %d bytes, where an Agat label has %d"
            % (name, LABEL_DUMP, len(label), AGAT_LABEL_BYTES))
    warnings = []
    if meta.get("file_system") == "dos33":
        write_files(image, tracks, meta, read, name, warnings)
    sectors = []
    for track in range(tracks):
        row = []
        for physical in range(SECTORS):
            at = dos_offset(track, DOS_ORDER[physical])
            data = bytes(image[at:at + SECTOR_BYTES])
            row.append(Sector(sector_volume(meta, track, physical, options), data,
                sector_spare(meta, track, physical, data)))
        sectors.append(row)
    return SectorDisk(sectors, bytes(label)), meta, warnings


def sector_volume(meta, track, physical, options):
    """The volume number of a sector: as disk.json has it for a track image's folder, as
    --volume says or None (the DOS 3.3 catalog's, then the usual one) for a sector image's."""
    if meta.get("volume") is None:
        return options.volume
    return meta.get("volumes", {}).get(place_key(track, physical), meta["volume"])


def sector_spare(meta, track, physical, data):
    spare = meta.get("spare_exceptions", {}).get(place_key(track, physical))
    if spare is not None:
        return tuple(spare)
    if meta.get("spare_pairs") == "dos":
        return (swapped_low_bits(data[0]), swapped_low_bits(data[1]))
    return None


def write_files(image, tracks, meta, read, name, warnings):
    """Write each file of disk.json into the sectors its track/sector lists name."""
    dos = read_dos(image, tracks)
    if dos is None:
        raise Failure("%s: its hex dumps hold no DOS 3.3 catalog, which disk.json says the"
            " disk has" % name)
    entries = {entry.where: entry for entry in dos.entries}
    for record in meta.get("files", []):
        if record.get("_deleted") or record.get("_type") == "directory" or "path" not in record:
            continue
        where = tuple(record["catalog"])
        entry = entries.get(where)
        if entry is None or entry.directory:
            raise Failure("%s/%s names a file at track %d, sector %d, entry %d of the catalog,"
                " where the catalog has none" % ((name, DISK_JSON) + where))
        body = read(record["path"])
        if body is None:
            raise Failure("%s/%s is missing; pack writes every file of the catalog, so put it"
                " back" % (name, record["path"]))
        header = bytes.fromhex(record.get("header", ""))
        # A changed file loses what followed its end (tails.hex), and its length is told anew.
        changed = sha1(body) != record.get("sha1")
        if changed and header:
            if len(body) > 0xFFFF:
                raise Failure("%s/%s holds %s bytes, more than DOS can tell in its two bytes of"
                    " length" % (name, record["path"], grouped(len(body))))
            header = header[:-2] + struct.pack("<H", len(body))
        data = header + body
        room = len(entry.positions) * SECTOR_BYTES
        if len(data) > room:
            raise Failure("%s/%s doesn't fit the sectors its file has on the disk: it needs %s"
                " bytes, and its %s hold %s. A file is written back into its own sectors, as"
                " the catalog has them, so it can't grow past them." % (name, record["path"],
                grouped(len(data)), counted_things("sector", len(entry.positions)),
                grouped(room)))
        for index, position in enumerate(entry.positions):
            chunk = data[index * SECTOR_BYTES:(index + 1) * SECTOR_BYTES]
            if position is None:
                if any(chunk):
                    raise Failure("%s/%s has data where its file has no sector, at %Xh: a"
                        " file is written back into its own sectors" % (name, record["path"],
                        index * SECTOR_BYTES))
                continue
            at = dos_offset(*position)
            if changed:
                chunk = chunk.ljust(SECTOR_BYTES, b"\0")
            image[at:at + len(chunk)] = chunk
        listing = record.get("listing")
        if listing:
            view = read(listing["path"])
            if view is not None and sha1(view) != listing.get("sha1"):
                warnings.append("%s/%s has changed, but it is only for reading: pack takes %s"
                    % (name, listing["path"], record["path"]))


UNPACK_SALVAGE_ADVICE = ("A folder keeps the 256 bytes of each sector, the volume number of its"
    " address field and the spare bits of its data field, but not a track laid out a way of its"
    " own, so it can take a track only when every one of its sectors reads intact. To unpack"
    " it anyway, with zeros for what can't be read, give --salvage.")


def pack_product(disk, target_format, options, lines):
    """The image of a SectorDisk in a format, as a Product."""
    tracks = len(disk.sectors)
    if target_format in SECTOR_FORMATS:
        expected = [{number: Sector(None, sector.data) for number, sector in enumerate(sectors)}
            for sectors in disk.sectors]
        return Product(sector_image_bytes(disk, target_format),
            "%d tracks of 16 sectors" % tracks, expected, disk.label)
    if any(sector.volume is None for sectors in disk.sectors for sector in sectors):
        volume, whence = dos33_volume(disk), "as the disk's DOS 3.3 catalog says"
        if volume is None:
            volume, whence = DEFAULT_VOLUME, "the usual one"
        disk = SectorDisk([[sector if sector.volume is not None else sector._replace(
            volume=volume) for sector in sectors] for sectors in disk.sectors], disk.label)
        lines.append("  volume %d, %s" % (volume, whence))
    if disk.label:
        lines.append("  its Agat label has no place in %s and is left out"
            % with_article(target_format))
    description = "%d tracks laid out as SDISK// lays them out, %s" % (tracks,
        disk_volumes(disk))
    if target_format == "nic":
        image = nic_bytes(disk)
    elif target_format == "nib":
        image = nib_bytes(tracks_of_sector_disk(disk))
    else:
        image = hfe_bytes(tracks_of_sector_disk(disk), options.rate)
        description += ", %d kbit/s" % options.rate
    return Product(image, description, expected_of(disk))


def write_folder(folder, contents, dry_run):
    """Write the files of contents ({path: bytes}, None for a folder) as the folder, an
    existing one renamed to its backup first, and say what was done."""
    check_backup(folder)
    if os.path.lexists(folder) and not os.path.isdir(folder):
        raise Failure("%s is a file, not a folder" % folder)
    backup = folder + BACKUP_SUFFIX
    renamed = ", the old one renamed to %s" % backup if os.path.lexists(folder) else ""
    if dry_run:
        return "would be written" + renamed
    # Written aside first, so that a failed write leaves the old folder as it was.
    temporary = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(folder)), prefix=".",
        suffix=".tmp")
    try:
        for path, data in sorted(contents.items()):
            full = os.path.join(temporary, *path.split("/"))
            if data is None:
                os.makedirs(full, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(data)
        if renamed:
            os.replace(folder, backup)
        os.replace(temporary, folder)
    finally:
        if os.path.exists(temporary):
            shutil.rmtree(temporary)
    return "written" + renamed


def packs_back(contents, image_format, data, disk, options):
    """How the folder packs back into its source's format: a clause, or None when its sectors
    would differ, which is a bug; problems then holds the lines on them."""
    read = contents.get
    packed_disk, _, _ = packed(read, "the folder", options)
    product = pack_product(packed_disk, image_format, options, [])
    if product.image == data:
        return "packs back to the same image, byte for byte", []
    if image_format in SECTOR_FORMATS:
        expected = [{number: Sector(None, sector.data) for number, sector in enumerate(sectors)}
            for sectors in disk.sectors]
        label = disk.label
    else:
        expected, label = expected_of(disk), b""
    problems = verify(Product(product.image, "", expected, label), image_format)
    if problems:
        return None, problems
    return "packs back to the same sectors, its tracks laid out as SDISK// lays them out", []


def unpack(source, options):
    """Unpack one image as the options say; return 0 when it is done, and 1 when the folder
    would not pack back to the same sectors, and so is not written."""
    folder = source + FILES_SUFFIX
    check_backup(folder)
    with open(source, "rb") as handle:
        data = handle.read()
    image_format = format_of(source)
    disk, notes = read_image(data, image_format, source)
    readings = read_tracks(disk) if isinstance(disk, TrackDisk) else None
    lines = ["%s: %s" % (source, describe_source(disk, readings, image_format))]
    lines += ["  " + note for note in notes]
    try:
        salvaged = []
        if readings is not None:
            refusal = ("%s can't be unpacked without losing what these tracks hold:" % source,
                UNPACK_SALVAGE_ADVICE)
            sector_disk = decoded_tracks(readings, options.salvage, lines, refusal,
                "the folder")
            salvaged = [place_key(track, sector)
                for track, reading in enumerate(readings[:len(sector_disk.sectors)])
                for sector in range(SECTORS) if sector not in reading.sectors]
        else:
            sector_disk = disk
        contents, more = unpacked(sector_disk, image_format, source, salvaged)
        lines += more
    finally:
        for line in lines:
            say(line)
    checked = ""
    if options.verify:
        clause, problems = packs_back(contents, image_format, data, sector_disk, options)
        if clause is None:
            say("%s: not written: packed back, it would differ from the source, which is a bug"
                " in this tool:" % folder)
            for problem in problems:
                say("  " + problem)
            return 1
        checked = "; " + clause
    files = sum(1 for path, data in contents.items() if data is not None
        and path.startswith(FILES_FOLDER + "/") and not path.endswith(LISTING_SUFFIX))
    say("%s: %s: %s%s" % (folder, write_folder(folder, contents, options.dry_run),
        counted_things("file", files), checked))
    return 0


def pack_target(folder, target, meta_format):
    """The image a folder packs into: as target names it, or beside the folder with the
    extension, or the source's name and format."""
    base = folder[:-len(FILES_SUFFIX)] if folder.endswith(FILES_SUFFIX) else folder
    if target is None:
        return base if format_of(base) else base + "." + meta_format
    extension = extension_alone(target)
    if extension is None:
        return target
    if format_of(base):
        base = os.path.splitext(base)[0]
    return base + "." + extension


def pack(folder, target, options):
    """Pack one folder as the options say; return 0 when it is done, and 1 when the image
    would not read back as meant, and so is not written."""
    name = folder.rstrip("/\\")

    def read(path):
        full = os.path.join(name, *path.split("/"))
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as handle:
            return handle.read()

    if not os.path.isdir(name):
        raise Failure("%s is no folder" % name)
    disk, meta, warnings = packed(read, name, options)
    target_path = pack_target(name, target, meta.get("format", "dsk"))
    target_format = format_of(target_path)
    if target_format is None:
        raise Failure("%s names no format: give a target whose extension is one of %s"
            % (target_path, ", ".join(FORMATS)))
    check_backup(target_path)
    if options.volume is not None and meta.get("volume") is not None:
        raise Failure("--volume can't be used with %s: it keeps the volume number of each"
            " sector, as the address fields of its source had them" % name)
    if options.volume is not None and target_format in SECTOR_FORMATS:
        raise Failure(CONTRADICTIONS["volume-sectors"] % {"target": target_path,
            "target_format": target_format})
    if options.rate is not None and target_format != "hfe":
        raise Failure(CONTRADICTIONS["rate"] % {"target": target_path})
    options.rate = options.rate or DEFAULT_RATE_KBPS
    what = "a folder of %d tracks" % len(disk.sectors)
    if meta.get("file_system") == "dos33":
        what += ", %s" % counted_things("file", sum(1 for record in meta.get("files", [])
            if "sha1" in record))
    lines = ["%s: %s" % (name, what)]
    lines += ["  " + warning for warning in warnings]
    try:
        product = pack_product(disk, target_format, options, lines)
    finally:
        for line in lines:
            say(line)
    checked = ""
    if options.verify:
        problems = verify(product, target_format)
        if problems:
            say("%s: not written: read back, it would differ from the folder, which is a bug"
                " in this tool:" % target_path)
            for problem in problems:
                say("  " + problem)
            return 1
        checked = "; verified: %d sectors as in the folder" % sum(
            len(sectors) for sectors in product.expected)
    say("%s: %s: %s%s" % (target_path, install(target_path, product.image, options.dry_run),
        product.description, checked))
    return 0

def grouped(number):
    """A number with its digits grouped by apostrophes."""
    return "{:,}".format(number).replace(",", "'")


def counted(noun, numbers):
    """A noun with numbers: "track 5", "tracks 1, 3 to 5 and 9"."""
    numbers = sorted(set(numbers))
    if len(numbers) == 1:
        return "%s %d" % (noun, numbers[0])
    runs = []
    for number in numbers:
        if runs and runs[-1][1] == number - 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    parts = ["%d" % a if a == b else "%d and %d" % (a, b) if b == a + 1 else "%d to %d" % (a, b)
        for a, b in runs]
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return "%ss %s" % (noun, text)


def width():
    return min(LINE_WIDTH, shutil.get_terminal_size((LINE_WIDTH + 1, 24)).columns - 1)


def say(text, stream=None):
    """Print a line, wrapped to the terminal, its continuations indented two more spaces."""
    stream = stream or sys.stdout
    for line in text.split("\n"):
        indent = " " * (len(line) - len(line.lstrip()))
        stream.write(textwrap.fill(line.lstrip(), width(), initial_indent=indent,
            subsequent_indent=indent + "  ", break_long_words=False,
            break_on_hyphens=False) + "\n")
    # Messages to the two streams come in the order they are written.
    stream.flush()


def extension_alone(target):
    """The extension that a target of the command line is, as "hfe" or ".hfe", or None when
    it is a path."""
    extension = target[1:] if target.startswith(".") else target
    return extension if extension.lower() in FORMATS else None


def target_of(source, target):
    """The target path for a source: the source's with the extension, if the target is one
    alone, else the target itself."""
    extension = extension_alone(target)
    return target if extension is None else os.path.splitext(source)[0] + "." + extension


CONTRADICTIONS = {
    "volume-tracks": "--volume can't be used with %(source)s: %(a_source_format)s keeps the"
        " address fields of its sectors, and the volume number in each of them, as the disk"
        " has them, and the conversion keeps them too. --volume sets the volume number of a"
        " disk made from a sector image, .dsk, .do or .po, which keeps the 256 bytes of each"
        " sector and nothing else.",
    "volume-sectors": "--volume can't be used for %(target)s: a .%(target_format)s keeps the"
        " 256 bytes of each sector and nothing else, so it has no place for the volume number,"
        " which a disk holds in the address field before each sector. --volume sets the volume"
        " number of a .nib, .nic or .hfe made from a sector image.",
    "rate": "--rate can't be used for %(target)s: it sets the bit rate that an .hfe records,"
        " which tells FlashFloppy how fast to send its cells. A .nib or .nic is always sent at"
        " the Disk II's own rate, 4 microseconds a bit, and a sector image holds no bits at"
        " all.",
    "salvage-sectors": "--salvage can't be used with %(source)s: it lets the tracks of a .nib,"
        " .nic or .hfe be decoded into sectors in spite of the ones that can't be read, and a"
        " .%(source_format)s holds sectors already, every one of them there.",
    "salvage-copy": "--salvage can't be used for %(target)s: %(a_target_format)s gets the"
        " tracks of %(source)s bit for bit, sectors that can't be read included, so nothing is"
        " decoded and nothing needs salvaging. --salvage is for making a .dsk, .do, .po or .nic"
        " from a .nib, .nic or .hfe, as those hold intact sectors only.",
}


def contradiction(conversion, options):
    """The key in CONTRADICTIONS of an option that doesn't fit a conversion, or None."""
    if options.volume is not None:
        if conversion.source_format in TRACK_FORMATS:
            return "volume-tracks"
        if conversion.target_format in SECTOR_FORMATS:
            return "volume-sectors"
    if options.rate is not None and conversion.target_format != "hfe":
        return "rate"
    if options.salvage and not conversion.decodes():
        if conversion.source_format in SECTOR_FORMATS:
            return "salvage-sectors"
        return "salvage-copy"
    return None


def conversions_of(paths, options):
    """The conversions that the paths of the command line ask for, checked against the
    options; raise UsageError for anything wrong."""
    if len(paths) < 2:
        raise UsageError("give a target after the source: a file name, or just the extension of"
            " the format to convert to, one of %s" % ", ".join(FORMATS))
    sources, target = paths[:-1], paths[-1]
    alone = extension_alone(target)
    if alone is None and format_of(target) is None:
        raise UsageError("%s names no format: give a target whose extension is one of %s, or"
            " just the extension" % (target, ", ".join(FORMATS)))
    if len(sources) > 1 and alone is None:
        raise UsageError("with several sources, give the target as an extension alone, as"
            " \"%s\", so that each goes beside its source" % format_of(target))
    conversions = []
    seen = {}
    for source in sources:
        if format_of(source) is None:
            raise UsageError("%s names no format this tool knows: a source's extension must be"
                " one of %s" % (source, ", ".join(FORMATS)))
        conversion = Conversion(source, target_of(source, target))
        key = os.path.normcase(os.path.abspath(conversion.target))
        if key == os.path.normcase(os.path.abspath(source)):
            raise UsageError("%s would be written over itself" % source)
        if key in seen:
            raise UsageError("%s and %s would both be written to %s" % (
                seen[key], source, conversion.target))
        seen[key] = source
        conversions.append(conversion)
    for conversion in conversions:
        key = contradiction(conversion, options)
        if key:
            raise UsageError(CONTRADICTIONS[key] % {"source": conversion.source,
                "target": conversion.target, "source_format": conversion.source_format,
                "target_format": conversion.target_format,
                "a_source_format": with_article(conversion.source_format),
                "a_target_format": with_article(conversion.target_format)})
    if options.dry_run and not options.verify:
        raise UsageError("--dry-run and --no-verify together would do nothing: a dry run"
            " converts and verifies without writing, and --no-verify skips the verification")
    return conversions


def volume_number(text):
    value = int(text, 0)
    if not 0 <= value <= 255:
        raise argparse.ArgumentTypeError("a volume number is a byte, 0 to 255; DOS 3.3 uses 1 to"
            " 254")
    return value


def rate_number(text):
    value = int(text)
    if not 0 < value <= 0xFFFF:
        raise argparse.ArgumentTypeError("an HFE records 1 to 65535 kbit/s")
    return value


VERB_CONTRADICTIONS = {
    "unpack-volume": "--volume can't be used to unpack: a folder keeps the volume number of"
        " each sector of a track image as its address field has it, and a sector image has"
        " none. --volume sets it when a sector image's folder is packed into a .nib, .nic or"
        " .hfe.",
    "unpack-rate": "--rate can't be used to unpack: it sets the bit rate that an .hfe records,"
        " and unpacking writes a folder.",
    "pack-salvage": "--salvage can't be used to pack: a folder holds sectors already, every one"
        " of them there. --salvage is for unpacking a .nib, .nic or .hfe whose sectors don't"
        " all read.",
}


def unpack_jobs(paths, options):
    """The images that the command line asks to unpack, checked against the options."""
    if not paths:
        raise UsageError("give the images to unpack")
    for source in paths:
        if format_of(source) is None:
            raise UsageError("%s names no format this tool knows: an image's extension must be"
                " one of %s" % (source, ", ".join(FORMATS)))
        if options.salvage and format_of(source) in SECTOR_FORMATS:
            raise UsageError(CONTRADICTIONS["salvage-sectors"] % {"source": source,
                "source_format": format_of(source)})
    if options.volume is not None:
        raise UsageError(VERB_CONTRADICTIONS["unpack-volume"])
    if options.rate is not None:
        raise UsageError(VERB_CONTRADICTIONS["unpack-rate"])
    return paths


def pack_jobs(paths, options):
    """The folder and the target, None for the folder's own, that the command line asks to
    pack."""
    if not 1 <= len(paths) <= 2:
        raise UsageError("give one folder to pack, then the target, or just its extension, or"
            " nothing for the image the folder came from")
    if options.salvage:
        raise UsageError(VERB_CONTRADICTIONS["pack-salvage"])
    target = paths[1] if len(paths) == 2 else None
    if target is not None and extension_alone(target) is None and format_of(target) is None:
        raise UsageError("%s names no format: give a target whose extension is one of %s, or"
            " just the extension" % (target, ", ".join(FORMATS)))
    return [(paths[0], target)]


EXIT_STATUS = """\
Exit status: 0 when every image or folder was written, 1 when one would have differed from its
source and was not written, 2 on any other error."""


def main(argv=None):
    parser = argparse.ArgumentParser(prog=PROGRAM, description=__doc__,
        usage="%(prog)s [options] SOURCE... [[TARGET].]EXT\n"
            "       %(prog)s unpack [options] IMAGE...\n"
            "       %(prog)s pack [options] FOLDER [[TARGET].]EXT",
        formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(prog, width=LINE_WIDTH),
        epilog=EXIT_STATUS)
    parser.add_argument("paths", nargs="+", metavar="PATH",
        help="the sources, then the target, or just its extension; or unpack and the images;"
            " or pack, the folder and the target")
    parser.add_argument("--volume", type=volume_number, metavar="N",
        help="the volume number to write into every address field of a disk made from a .dsk,"
            " .do or .po; by default, the one in the disk's DOS 3.3 catalog, else %d"
            % DEFAULT_VOLUME)
    parser.add_argument("--salvage", action="store_true",
        help="when decoding a .nib, .nic or .hfe into a .dsk, .do, .po or .nic, or unpacking"
            " it, write zeros for the sectors that can't be read, and list them, instead of"
            " refusing")
    parser.add_argument("--rate", type=rate_number, metavar="KBPS",
        help="the bit rate to record in an .hfe, in kbit/s, default %d: FlashFloppy sends its"
            " cells at twice that rate, so %d gives the 4 us bits of a Disk II"
            % (DEFAULT_RATE_KBPS, DEFAULT_RATE_KBPS))
    parser.add_argument("-n", "--dry-run", action="store_true",
        help="convert and verify, but write nothing")
    parser.add_argument("--no-verify", dest="verify", action="store_false",
        help="don't read the image back to compare it with the source")
    # Options may stand anywhere, after unpack or pack too.
    options = parser.parse_intermixed_args(argv)
    # A label in Cyrillic must not stop a message on a console that lacks the letters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    verb = options.paths[0] if options.paths[0] in ("unpack", "pack") else None
    try:
        if verb == "unpack":
            jobs = [(unpack, (source, options)) for source in unpack_jobs(options.paths[1:],
                options)]
        elif verb == "pack":
            jobs = [(pack, (folder, target, options))
                for folder, target in pack_jobs(options.paths[1:], options)]
        else:
            jobs = [(convert, (conversion, options))
                for conversion in conversions_of(options.paths, options)]
    except UsageError as error:
        say("%s: %s" % (PROGRAM, error), sys.stderr)
        return 2
    if verb != "pack":
        options.rate = options.rate or DEFAULT_RATE_KBPS
    errors = unverified = 0
    for job, arguments in jobs:
        try:
            unverified += job(*arguments)
        except Failure as failure:
            errors += 1
            say("%s: %s" % (PROGRAM, failure), sys.stderr)
        except OSError as error:
            errors += 1
            where = "%s: " % error.filename if error.filename else ""
            say("%s: %s%s" % (PROGRAM, where, error.strerror or error), sys.stderr)
    return 2 if errors else 1 if unverified else 0


if __name__ == "__main__":
    sys.exit(main())
