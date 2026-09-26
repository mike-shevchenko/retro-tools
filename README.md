# retro-tools

Various helper programs for retro-computing.

(C) 2026 Mikhail Shevchenko, mike.shevchenko@gmail.com

## nic2hfe.py

Converts SDISK II `.nic` disk images, Apple II or Agat 140 KB, to HFE files for FlashFloppy's Apple II firmware. Each track's bit stream is copied verbatim, so gaps, volume number and sector order stay as SDISK II plays them, and every HFE written is read back and its sectors compared with the source:

```
python nic2hfe.py GAME.nic ...
```

Each `GAME.hfe` is written next to its `GAME.nic`; `python nic2hfe.py --help` describes the options.

## Tests

The tests need only Python 3 and use the disk images in `tests/data`; run them from the repository root:

```
python -m unittest discover tests
```
