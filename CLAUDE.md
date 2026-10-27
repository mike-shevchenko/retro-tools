# retro-tools: notes for Claude Code sessions

Notes for a session working in this repository: the owner's helper programs for retro-computing. This file records what a session needs to know and would otherwise have to find out again. Its first subject is the Apple2 140K disk formats, behind the converter of Apple2 and Agat disk images; the knowledge comes from the FlashFloppy fork's work of 2026-10-08 to 2026-10-14, where much of it was checked against real images, and from the converter's own work of 2026-10-13 and 2026-10-14.

Each fact below is marked as checked against images, as taken from working code, or as from memory and to be checked first.

## Test data

Unit tests hold no contents of real disks, which may be copyrighted: no games, no DOS, no BASIC (the owner's word of 2026-10-14). A test that needs a disk with usable data gets a simple BASIC program of our own on it, as the Applesoft `HELLO` on the DOS 3.3 disk that `dos33_dsk()` in `tests/test_apple2_disk_converter.py` builds, with no DOS on it; any other test gets made-up bytes of a predictable pattern. Real images serve only checks run outside the tests, on the owner's collection. `tests/data`, which held the Agat game disk `KLAD140` in four formats, was removed that day; its files remain in the history, from `ed17c57`.

## The converter

`apple2-disk-convert.py` (`nic2hfe.py` until 2026-10-14, renamed and extended) converts between `.dsk`/`.do`, `.po`, `.nib`, `.nic` and `.hfe`: `apple2-disk-convert.py [options] SOURCE... [[TARGET].]EXT`, the target's extension naming the format, an extension alone taking each source's name and folder. Its module docstring is the `--help`, written for a newcomer to the Apple2 who knows the Gotek, and its comments are meant as a guide to the formats for a programmer who knows little of floppies; keep them so (the owner's wish). Its tests are `tests/test_apple2_disk_converter.py`, run with the others by `python -m unittest discover tests`, on disks the tests make up and on fields worked out by hand from the formats, which every real disk checked matches.

- Sector images are read and written in their orders. Every track made from sectors is laid out as SDISK// lays out a `.nic` record, spare pairs 0, so `.dsk` to `.nic` gives the bits FlashFloppy sends for the `.dsk`, and `.dsk` to `.hfe` equals `.dsk` to `.nic` to `.hfe`; a `.nib` gets the same track without the sync zeros, 48 `FF` added in the gap before sector 0.
- Into a sector image or a `.nic`, tracks are decoded and must hold 16 intact sectors (`--salvage` writes zeros instead); a `.nic` gets each sector's volume and spare pairs as read. `.nib`/`.nic`/`.hfe` into `.nib`/`.hfe` go across bit for bit.
- Every result is read back in memory and compared with the source, sector by sector and, for bit copies, nibble by nibble with runs of `FF` collapsed; it is written only if it matches, through a temporary file. An existing target becomes `TARGET.BAK`; if that exists too, the conversion is refused before any work, as `pxfont.py` does, with no comparison of contents (the owner's choice of 2026-10-14, over a first version that left an identical target alone and replaced one whose backup held the same bytes).
- Options: `--volume N` (sector source to `.nib`/`.nic`/`.hfe` only; default the VTOC's volume, else 254, where the firmware's `.dsk` handler always uses 254), `--salvage` (decoding conversions only), `--rate KBPS` (`.hfe` targets only, default 250), `-n`/`--dry-run`, `--no-verify`. An option that doesn't fit the formats is refused with an explanation, as the owner asked.
- Verified on 2026-10-14 over the whole collection, 44 distinct GCR images: all 28 distinct `.nic` re-encode byte for byte, all 3 `.nib` and the 3 `.hfe` of `nic2hfe.py` rewrite byte for byte, every sector image survives every route, and no bit copy loses a nibble; `SHAMUS.DSK` to `.nic` gives `SHAMUS.NIC` byte for byte, and `KLAD140.dsk` gives `KLAD140.nic` but for the spare pairs DOS wrote there.

## Unpack and pack

`apple2-disk-convert.py unpack IMAGE...` writes `IMAGE.files/`, and `pack FOLDER [[TARGET].]EXT` writes it back, by default into the source's name and format (added on 2026-10-14, the owner's design: pxfont-like verbs, metadata in JSON, hidden data as files, hex dumps in the form of `hexdump -Cv`).

- The folder: `files/` with the catalog's files, a folder to each Agat DOS directory, names in the Agat's letters, the DOS type as extension (T I A B S, and the Agat's PE, KA, DE in Cyrillic); `NAME.txt` beside BASIC programs (a listing) and text files (the text), output only: pack warns when one was changed. Hex dumps: `boot.hex` (tracks 0-2 outside files), `catalog.hex` (VTOC, catalog, track/sector lists), `unused.hex` (other sectors that aren't zero), `tails.hex` (what follows a file's end in its sectors), `label.hex` (the Agat label, its character column ASCII and CP866), or `sectors.hex` alone without a catalog. Offsets are those of a DOS-ordered `.dsk`; pack reads offset and bytes and ignores the character column and lines starting with `#`. `disk.json`: format, tracks, file system, for a track image the usual volume, its exceptions and the spare pairs (`zero` or `dos` and exceptions, keyed `track:physical sector`), and per file the catalog place, the host path, the DOS header and the SHA-1 of the contents; `_x` fields are derived and ignored, as in pxfont.
- Packing an edited file (the owner's rule): it goes back into the sectors its track/sector lists name, the header's length told anew and the tail dropped; a file that no longer fits stops the pack; the catalog, VTOC and T/S lists are never rewritten. A file's contents are A/I/B without the DOS header (2 or 4 bytes), cut at the header's length; other types with trailing zeros cut.
- Every unpack packs the folder back in memory and compares: on 2026-10-14 all 51 distinct 140K images of the collection unpacked, 38 packing back byte for byte and 13 to the same sectors (a `.nib`, `.hfe` of other producers or salvaged images, whose tracks are laid out anew as SDISK// lays them out).
- The Agat's characters come from the owner's font table, `C:\Retro\comps\agat\Agat-7 font 256.png`, glyph by glyph with his answers of 2026-10-14: A0-DF ASCII but A4 a currency sign, E0-FF and 60-7F Cyrillic capitals and small letters in KOI-7 order (7F is the small hard sign), 40-5F small Latin with 5C a broken bar and 5F a block; 00-3F and 80-9F graphics. Anything with no fair Unicode counterpart is an empty square U+25A1, and no character with the Unicode Emoji property is used (the owner hates color glyphs: the suits are the outlined ones, the diagonal arrows U+2B08-2B0B, the face 80 an empty square). Escapes such as a backslash-u in an Edit tool's text arrive as the characters themselves, so the sources build such strings with `chr()`.
- Agat DOS: file types bits 10, 20, 40 shown as PE, KA, DE; type FF a directory, whose catalog starts with an entry back to the parent (skipped). The release byte of the VTOC is 0 on some Agat disks. BASIC listings use the Agat BASIC tokens (as dsk_tools has them) and its long variable names: `01`/`02` and an index into a table of names before the two bytes that end the file's declared length, each name ending with a byte whose top bit is set. Applesoft programs list the same but for a few tokens.
- Not done: Sprite OS (no 140K sample in the collection; dsk_tools' `fs_spriteos.cpp` describes it), ProDOS (two Apple disks in the collection), CP/M; a listing in Applesoft's tokens for Apple disks.

## Sources

- The FlashFloppy fork, `C:\github\forks\flashfloppy\`, branch `apple2-mode`: `src/image/nib.c` (the `.nib`, `.nic` and, since 2026-10-14, `.dsk`/`.do`/`.po` handlers: streaming as 4 us bit cells, a sector image's track made into `.nic` blocks as it is read, and decoding a write back into nibbles, fields and sectors), `src/image/hfe.c` (the header's encoding checked against the mode, and `hfe_bits_look_gcr()`, which tells GCR from MFM/FM by the bits of track 0, for the log only), `src/image/image.c` (`apple2_image_type[]`, and `image_valid()`, which hides HFEs over 1.25 MB in Apple2 mode), `inc/floppy.h` (the handlers' state). The fork's `CLAUDE.md`, tracked on its branch `private` and merged into `next`, has the history of that work.
- Oleg Odintsov's Gotek firmware 307 for the Agat, the gotek-sa390 project of https://svn.code.sf.net/p/agat-hardware/code, cloned at `C:\Retro\comps\agat\agat-hardware\`: its `.nib` handler was the model for FlashFloppy's; it has no sector-image encoder.
- The Agat emulator agatemulator, its SVN cloned at `C:\Retro\comps\agat\agatemulator\` (an older copy under `C:\Retro\comps\agat\emulators\`): `fdd/fdd1.c` reads `.nib` by name, `.po` in ProDOS order, other names in DOS order, volume 254, and its own 256-byte header, "Agathe emulator virtual disk", at the start of an image, which no image of the collection has.
- The Agat emulator ecat3 by Mikhail Revzin, `C:\github\forks\ecat3\`, with its library dsk_tools, also cloned alone at `C:\github\forks\dsk_tools\` (the two copies differed only in `fs_cpm.cpp` on 2026-10-14), and the GUI built on it, Disk Commander, at `C:\github\forks\dsk_commander\`: `src/writers/writer_mfm.cpp` writes `.nic` in the SDISK// layout and `.nib` in a layout of its own (below); its HFE writer has no Apple2 GCR path upstream, 840K and 880K Agat and PC-like disks only (on 2026-10-14 each of the three forks got a branch `apple2-140k`, pushed to the owner's forks and not yet offered upstream, with its pull request notes in `pr-apple2-140k.md` at the fork's root: 140K GCR HFE, `.po` held in DOS 3.3 order and written back in ProDOS order, ProDOS volumes read in either order and from track images, 35 to 40 tracks, and `.nib` output from `fddconv`); `src/dsk_tools.cpp` tells a ProDOS-ordered `.dsk` by the volume directory header at block 2 in ProDOS order, with `fs_prodos.cpp` `volume_header_is_valid()`, the test the converter took over.
- Images: the fork's `test_data\` and the owner's collection at `C:\Retro\comps\agat\Software\`, see the section on test material.

## The physical layer (Disk II, Agat 140K)

- 35 tracks (some disks 40), one side, 300 rpm, 16 sectors of 256 bytes: 143'360 bytes. Bits are 4 us apart (250 kbit/s on the disk). From the code and the images.
- The drive head is moved by four stepper phases; a track is two half-track steps. Copy protection may use half and quarter tracks, which only `.nib`, `.nic`, `.hfe` and `.woz` can carry; `.dsk` holds standard 16-sector disks only.
- The controller reads a nibble as eight bits from a 1 bit on: the read latch shifts bits in and delivers a byte when its top bit is set (`read_latch()` in the converter). Valid data nibbles have the top bit set and no two consecutive 0 bits in their lower bits.
- Self-sync: gap bytes are `FF` followed by two 0 bits, 10 bits each, so the latch falls into step with byte boundaries after a few of them.
- The 64 data nibbles are exactly the bytes with the top bit set, no three 0 bits in a row and at most one pair of them, and two adjacent 1 bits below the top bit, `AA` and `D5` excepted (checked by computation on 2026-10-13).

## Address field (checked)

`D5 AA 96`, then volume, track, sector and checksum, each in 4-and-4 encoding (two nibbles: the odd bits, then the even bits), then `DE AA EB`. The checksum is volume ^ track ^ sector.

```python
def encode_4_and_4(v):
    return bytes([(v >> 1) | 0xAA, v | 0xAA])

def decode_4_and_4(odd, even):
    return ((odd << 1) | 1) & even
```

The decoder ran on the 1'120 sectors of two `.nic` images on 2026-10-13, and both on the whole collection on 2026-10-14.

## Data field (checked)

`D5 AA AD`, then 342 nibbles of 6-and-2 data, a checksum nibble, then `DE AA EB`.

- The 256 bytes become 342 values of 6 bits: first 86 auxiliary values holding the low two bits of each byte, swapped (bit 0 into bit 1 and bit 1 into bit 0), three pairs to a value; then 256 values holding the high six bits of each byte. Byte `j`'s pair is in auxiliary value `j % 86` at bit `2 * (j // 86)`. The auxiliary values come in this forward order in every image checked: reversed, the sectors no longer matched the `.dsk` of the same disk.
- Before translation, each value is XORed with the one before it (the first with 0); the checksum nibble is the last value, so that decoding, which XORs as it goes, ends at 0.
- Each 6-bit value goes through the 64-entry table below.

```python
NIBBLE_OF_SIX_BITS = bytes.fromhex(
    "96979a9b9d9e9fa6a7abacadaeafb2b3b4b5b6b7b9babbbcbdbebfcbcdcecfd3"
    "d6d7d9dadbdcdddedfe5e6e7e9eaebecedeeeff2f3f4f5f6f7f9fafbfcfdfeff")

def encode_6_and_2(data):
    """256 bytes -> 342 data nibbles and the checksum nibble."""
    aux = [0] * 86
    for j, b in enumerate(data):
        aux[j % 86] |= (((b & 1) << 1) | ((b >> 1) & 1)) << (2 * (j // 86))
    out, prev = bytearray(), 0
    for v in aux + [b >> 2 for b in data]:
        out.append(NIBBLE_OF_SIX_BITS[v ^ prev])
        prev = v
    out.append(NIBBLE_OF_SIX_BITS[prev])
    return bytes(out)
```

`decode_6_and_2()` in the converter is the decoder; the same algorithm decoded the 1'120 sectors of `KLAD140.nic` and `SHAMUS.NIC` on 2026-10-13. The encoder above is its inverse; the same algorithm in C, the firmware's `.dsk` handler of 2026-10-14, reproduces `SHAMUS.NIC` byte for byte from `SHAMUS.DSK`, and so does the converter.

Aux values 84 and 85 hold only two pairs each, bytes 84 and 85 and bytes 170 and 171; their top two bits are unused, and the encoder above leaves them 0. DOS 3.3 itself fills them, with the pairs of bytes 0 and 1 once more, its loop running on past byte 255 (checked 2026-10-14): every sector written on the Agat into `blankDOS33_do.hfe` (62) has them so, as do 505 of the 560 sectors of `KLAD140.nic`, all others there having zeros at bytes 0 and 1, the whole of `EVO.nib` and `LODE.NIB`, presumably copied by a DOS-based program, and one sector of the collection's `ya_sd\Games\ALT.NIC`. Converters leave them 0: Greaseweazle, the HxC software, the firmware, and whatever made the other 25 distinct `.nic`. The nibbles 84 to 86 of such data fields differ from a re-encoding while the sector data is the same. Readers ignore those bits, so compare sectors, never raw nibbles, when verifying a conversion; the converter keeps a sector's spare pairs where it re-encodes a decoded one.

## Sector order and volume (checked on two disks; ProDOS order from code)

A `.dsk`/`.do` file holds the sectors in DOS 3.3 order. The physical sector numbers of the address fields map to file sectors so:

```python
DOS_ORDER = [0, 7, 14, 6, 13, 5, 12, 4, 11, 3, 10, 2, 9, 1, 8, 15]  # physical -> file sector
```

On 2026-10-13 every one of the 560 sectors of `KLAD140.nic` and of `SHAMUS.NIC` equaled the same disk's `.dsk` at `track * 4096 + DOS_ORDER[physical] * 256`.

A `.po` file holds them in ProDOS order. Taken from working code, agatemulator's `pren1` (file order to physical, the inverse of this), not checked against an image, since the collection has no `.po`:

```python
PRODOS_ORDER = [0, 8, 1, 9, 2, 10, 3, 11, 4, 12, 5, 13, 6, 14, 7, 15]  # physical -> file sector
```

A `.dsk` is in principle ambiguous: some tools write ProDOS-ordered images under that name. Every `.dsk` met so far is DOS-ordered, with a DOS 3.3 VTOC. The converter, like the firmware and dsk_tools, reads a `.dsk` as a `.po` when a ProDOS volume directory header is at block 2 in ProDOS order: the firmware by its first five bytes alone (no previous block, next block 3, storage type F), dsk_tools and the converter by the header's fields (name, entry size, entries per block, bitmap and size, but not the next block), the converter also wanting it absent in DOS order. On 2026-10-14 the firmware's rule and the converter's agreed on all 18 sector images on this machine, all DOS-ordered, and on the unit tests' made-up ProDOS header; a DOS 3.3 disk in ProDOS order would go unnoticed. Physical sectors 0 and 15 are at the same place in both orders, so the VTOC (track 17, physical 0) can't tell them apart.

The volume number in the address fields is 254 in every image checked; a `.dsk` does not store it. The converter writes the VTOC's volume (byte 6) when there is a DOS 3.3 VTOC, else 254, unless `--volume` says otherwise; the firmware writes 254.

## DOS 3.3 VTOC (checked)

Track 17, DOS sector 0, in a DOS-ordered image at offset `17 * 16 * 256`: byte 1 the catalog's track, byte 2 its sector, byte 3 the DOS version (3), byte 6 the volume (254), byte `0x34` the tracks per disk (35), byte `0x35` the sectors per track (16). Useful to recognize a DOS-ordered image and for sanity checks; `MICRODS7.DSK` has 12 there, a nonstandard DOS, though it has 35 tracks.

## Image formats

- **`.dsk` / `.do`:** 35 tracks x 16 sectors x 256 bytes = 143'360, DOS order; 40 tracks = 163'840. Agat images may carry a 128-byte trailer after the data: `BTK7.DSK` and `MICRODS7.DSK` are 143'488 bytes (checked). It is more than a name: in those two, both of one author, `AGAT-DISK`, six bytes of unknown use (`01 05 00 00 0a f4` and `01 01 00 1d 02 f4`), a description in CP866 from byte 15 (" BEST TOOL KIT" and "for Agat-7" in Russian), the author from byte 64 (`IGOR`), an e-mail address from byte 80, a short name from byte 112 (`BTK7`, `MICRODS7`), each padded with zeros; no tool among the sources writes or reads it. Keep the trailer when rewriting such a file: the converter keeps it between sector images and drops it, with a note, for the others.
- **`.po`:** the same with ProDOS order.
- **`.nib`:** 35 tracks x 6'656 bytes = 232'960: the nibbles as the controller reads them, a track's worth each. Sync bytes have lost their two trailing 0 bits (plain `FF`), so the timing of the original is not kept; the controller re-aligns on the data, and keeps the alignment through the gaps, which are all ones. From `nib.c`; `EVO.nib` and `LODE.NIB` are samples. Layouts seen (checked): `EVO.nib` and `LODE.NIB` 128 `FF`, then per sector the address field, 5 `FF`, the data field, 40 `FF` (`LODE.NIB` with stray nibbles of writes after some data fields); `superdos.nib` 416 nibbles a sector, 6 `FF` between the fields and 47 after; ecat3's writer 48 `FF`, per sector 6 and 27 `FF`, the rest `FF`.
- **`.nic`** (the SDISK// emulator's format): 35 tracks x 16 blocks x 512 bytes = 286'720. The first 416 bytes of each block are one sector as a ready bit stream, MSB first, sync bits included; the remaining 96 bytes are zero. A track streams as its 16 blocks' 416 bytes back to back, 6'656 bytes like a `.nib` track, but with true 10-bit syncs packed in. In every one of the 47'040 records of the 84 `.nic` files found on 2026-10-13 (28 distinct), the layout is:
  - bytes 0 to 33: 22 bytes `FF`, then `03 fc ff 3f cf f3 fc ff 3f cf f3 fc` (six 0 bits and nine 10-bit syncs);
  - bytes 34 to 47: the address field (14 bytes);
  - bytes 48 to 52: 5 bytes `FF`;
  - bytes 53 to 401: the data field (349 bytes: prologue, 342 + 1 nibbles, epilogue);
  - bytes 402 to 415: 14 bytes `FF`;
  - bytes 416 to 511: zero.

  So a `.nic` can be generated from sectors byte for byte, and the result compared with a real `.nic` exactly, the spare pairs aside (see the data field).
- **`.hfe`** (HxC Floppy Emulator, v1, signature `HXCPICFE`; v3 `HXCHFEV3`, which FlashFloppy and the converter read): a 512-byte header, a track list, then the tracks. Header fields, little-endian (checked as far as the collection's files use them): byte 8 format revision (0); byte 9 number of tracks; byte 10 number of sides; byte 11 track encoding (0 ISO/IBM MFM, 2 ISO/IBM FM, 6 and 7 Apple II GCR1 and GCR2, `FF` unknown); bytes 12-13 bit rate in kbit/s; bytes 14-15 rpm; byte 16 interface mode (7 generic Shugart DD, `FF` unset); bytes 18-19 the track list's offset in 512-byte blocks; byte 20 write allowed; byte 21 single step (`FF`) or double step (`00`). The track list has 4 bytes per track: the track's offset in blocks and its length in bytes, both sides together. Track data comes in 512-byte blocks of 256 bytes of side 0 and 256 of side 1; the cells of each byte are taken LSB first. v3 opcodes are bytes whose low nibble is `F` (`0F` nop, `8F` index, `4F` + rate, `CF` + bit-reversed count & 7 skipping cells of the byte after, `2F` a random byte); any other `xF` byte is skipped too, as FlashFloppy does, so a v3 track can't hold four 1 cells in a row.
  - Apple2 GCR in an HFE: two 2 us cells per 4 us disk bit, at a nominal 250 kbit/s, one side, 35 cylinders, double step. A 1 is `01` (in time order) from `nic2hfe.py`, the converter, Greaseweazle and FlashFloppy's writes, and `10` from the HxC software (checked). The bit pattern betrays GCR without the header: runs of four or more 0 cells, which MFM and FM never have (`hfe_bits_look_gcr()`).
  - Three producers in the collection: `nic2hfe.py` and the converter, 932'864 bytes (35 tracks of 6'656 disk bytes, encoding `FF`, 250 kbit/s); the HxC software, 932'864 bytes too, but 53'008 bits a track, encoding 7, 255 kbit/s, interface mode 7; and Greaseweazle (`*_gw.hfe`), 897'024 bytes, 51'020 bits a track, 256 kbit/s, encoding and interface `FF`.
  - Where FlashFloppy recorded a write, the cells keep to pairs, but the pairing shifts by one cell at each end of the write (about 30 shifts on a track of `blankDOS33_do.hfe`). The converter reads cells that leave their pairs with a clock measured first over the long runs of 1 bits in the gaps, then following phase by 1/8 and rate by 1/256; simple rounding, with the clock taken as 2 cells, misreads everything after the first one-cell slip of a clock a few percent off.
  - A broken one: `test_data\SHAMUS.hfe` and the collection's `KLAD140.hfe` and `SHAMUS.hfe` of 1'405'952 bytes claim 56 tracks of FM, interface mode 7, but hold no sector marks of any encoding: never two 1 cells in a row (so no FM address mark), no MFM `4489` sync, no `D5 AA 96` at one or two cells per bit; the tracks are mostly alternating cells. Most likely a failed conversion by a tool that did not report the error; the converter refuses them, naming the FM encoding and the 56 tracks.

## Test material

- The fork's `test_data\` (`C:\github\forks\flashfloppy\test_data\`), the owner's folder for experiments, so its files may change: `KLAD140.dsk` with `KLAD140.nic`, and `SHAMUS.DSK` with `SHAMUS.NIC`, the same disks in two formats (checked equal sector for sector); `ARCANOID.NIC`, `arkanoid.NIC`, `KLAD140.nic`; `EVO.nib`, `LODE.NIB`; `ALV_sideB.DSK` and a side A whose name has a Cyrillic letter; `BTK7.DSK` and `MICRODS7.DSK` with the 128-byte trailer; a 5-byte file `Very Long filename that scrolls.dsk`, which is no image (a display test); the broken `SHAMUS.hfe`, a copy of the collection's; `rain.trd`, a 640K TR-DOS image for a Shugart host; notes on some disks in `ALV.txt`, `LODE.txt` and `SHAMUS.txt`; and `usb-test-data.img` and `flash.img`, drive images, which are no floppy images.
- The collection, `C:\Retro\comps\agat\Software\`, surveyed on 2026-10-13: 79 `.nic`, 3 `.nib`, 7 `.dsk` (5 plain, 2 with the trailer), 12 GCR `.hfe` of 35 tracks (2 with the header saying GCR, 10 saying unknown) and the 2 broken 56-track ones; 44 of the GCR images distinct by content. No 840K images were found there by extension, though the owner remembers a mix; check again before relying on that.
- `From Alexandr agat retro\blankDOS33_do.hfe` (made by the HxC software, then written on the Agat through FlashFloppy) has tracks 0 to 10 damaged, 50 sectors unreadable, address fields among them; two data separators agree, so the damage is in the file, likely from the FlashFloppy write bug fixed on 2026-10-09 (see the fork's notes). A good sample for `--salvage`.

## Not covered yet

- DOS 3.2's 13-sector format (5-and-3 encoding, address prologue `D5 AA B5`): known to exist, nothing checked; the converter names such tracks and refuses to decode them.
- `.woz`: not in the collection; its specification is public.
- Agat 840K disks: a different, MFM format, outside the 140K formats above; none found in the collection. The converter names an 860'160-byte image as one.
- agatemulator's 256-byte header on images: not read by the converter.
- Half and quarter tracks: the converter handles whole tracks only.
