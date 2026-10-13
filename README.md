# retro-tools

Various helper programs for retro-computing.

(C) 2026 Mikhail Shevchenko, mike.shevchenko@gmail.com

## License

Unless specified otherwise inside a subdirectory or in a file itself, the contents of this repository are licensed under the Mozilla Public License 2.0, see [license_mpl2.md](license_mpl2.md). Third-party code keeps the license stated in its own files.

## Tests

The tests need Python 3; those of `pxfont.py` need Pillow as well, and one of them fontTools, and are skipped without them. The tests make up the disks and fonts they work on, so that no copyrighted software is needed. Run them from the repository root:

```
python -m unittest discover tests
```
