# tools/mod: image modding for the bin_file archive

A private mod experiment, not part of the decomp. `pplmod.py` lists, extracts and
replaces images in the game's `bin_file` archive; `build_charswap.sh` builds a
proof-of-concept ROM where Brock's VS/select portrait (`CBROCK.BIF`) shows
"Moss", an original placeholder character drawn by `art/make_moss.py`.

```sh
gmake                                  # matching build first
tools/mod/build_charswap.sh            # -> build/mod/puzzleleague64.charswap.z64, then verifies it
uv run tools/mod/pplmod.py list build/usa/puzzleleague64.usa.z64
uv run tools/mod/pplmod.py extract build/usa/puzzleleague64.usa.z64 <out_dir> [NAME.BIF ...]
```

Extracted images come from the ROM: keep them out of the repo. Only the tools and
our own art (`art/`) are committed.

## Format

**Archive** (`bin_file` segment, USA ROM 0x9BDB40; the splat config has every
version's start): big-endian `u32 count`, then `count` rows of
`u32 size, u32 offset (from the archive start), char name[16]`, then the files,
each padded to an even length. `fileFind` in `src/main/file.c` looks names up
here; `tools/splat_ext/bin_file.py` and `tools/package_bin_file.py` split and
repack it.

**3FIB** (image file, `imageLoad` in `src/main/image.c`): header words are
little endian: `"3FIB"`, `u32 flags`, `rgba color`, `u32 frame_count`, a second
colour if `flags & 0x10`, a name (`u32 len` + bytes) if `flags & 0x08`. Then per
frame: 12 bytes (x, y, display time; little endian) and one BFF2.

**BFF2** (one bitmap, `bitmapLoad` in `src/main/bitmap.c`), big endian:
`"BFF2"`, `u32 flags`, `u32 width` (`flags & 0x200000`: display width in the
high half, stored width in the low half), `u32 height`, `u32 bytes_per_line`,
a name if `flags & 0x200`, sub-image counts if `flags & 0x400000` (fonts only;
not supported here), a palette (`u32 count` + RGBA8888 entries) for CI formats,
then the pixels. Flags low byte is the format: 0x32 CI4, 0x33 CI8, 0x24 IA8,
0x54 RGBA5551, 0x55 RGBA8888 (0x22/0x23 look like I4/I8). `flags & 0x400`: palette
index `(flags >> 12) & 0xFF` is transparent. `flags & 0x800`: pixels are RLE.

**RLE** (`bitmapLoadRLE`), output size `bytes_per_line * height`:

| byte | meaning |
|---|---|
| `0xxxxxxx` | copy the next `x + 1` bytes |
| `10nnnnnn b` | byte `b`, `n + 2` times |
| `11lllrrr` + `l + 2` bytes | that block, `r + 2` times |

## How the swap is written

`replace` re-encodes the frames in place: same frame count, size, format and
palette size (CI palettes are rebuilt from the new image), so the game allocates
what it always did. The new file must fit the old slot; the rest of the slot is
filled with 0xFF and the table's size field is updated. No other file moves.
Then the header CRC is recomputed (`ipl3checksum`, like `tools/checksummer.py`).
`verify` re-parses the modded ROM: the new frames decode to exactly the source
PNGs, and every other archive entry and every byte outside the slot, its table
row and the CRC are unchanged.

## Credit

The archive, 3FIB/BFF2 and RLE layout was first worked out by
[N64PPLEditorC](https://github.com/Adrien-G/N64PPLEditorC) (Adrien-G). This
tool is an independent Python implementation written from those notes and the
decomp's own `file.c`, `image.c` and `bitmap.c`; no code is copied.
