# tools/mod: image modding for the bin_file archive

A private mod experiment, not part of the decomp. `pplmod.py` lists, extracts and
replaces images and scene text in the game's `bin_file` archive;
`build_charswap.sh` builds a proof-of-concept ROM where Brock becomes "Moss", an
original placeholder character (`art/make_moss.py`, `art/paint_group.py`).

```sh
gmake                                  # matching build first
tools/mod/build_charswap.sh            # -> build/mod/puzzleleague64.charswap.z64, then verifies it
uv run tools/mod/pplmod.py list build/usa/puzzleleague64.usa.z64
uv run tools/mod/pplmod.py extract build/usa/puzzleleague64.usa.z64 <out_dir> [NAME.BIF ...]
uv run tools/mod/pplmod.py scenes build/usa/puzzleleague64.usa.z64 CBROCK.BIF ... --word brock
```

Extracted images come from the ROM: keep them out of the repo. Only the tools and
our own art (`art/*.png`) are committed. Edits of composite pictures (the 2P
group picture and its silhouette) are generated at build time from the base ROM
into `build/mod/work/`.

## What the swap changes, and where it shows

| File | Change | Scene (file: scene) | Reach it with one player |
|---|---|---|---|
| `CBROCK.BIF` | portrait, both frames | `OPPONENT.SBF: TRANSITION2`, `POKEMON.SBF: 1PPK-PICK`, `MENU.SBF: 2PPK-PICK`, `RESULT.SBF: 2P-WINNER` | 1-Player Game (stadium), beat stage 1 (Gary): the stage 2 intro and the Pokémon pick screen show Moss |
| `RECHEADS.BIF` frame 1 | head (other 15 frames byte-identical) | `MENU.SBF: RECORD-1` | Options > Records, the page with trainer heads |
| `PBACKBROCK.BIF` | puzzle-editor background | `MENU.SBF: PUZ-CUSTCHAR` ("Select Background") | custom-puzzle background picker: the scene after `PUZ-CUSTPICK` (custom puzzle slots) offers three backgrounds (Ritchie's, Lorelei's, Brock's); from the scene data, not traced in code. Likely Puzzle Games > Puzzle University > custom puzzle |
| `TRSELECT.BIF` | Brock's pixels repainted as Moss | `MENU.SBF: 2PTR-PICK` | 2-Player Games trainer select only |
| `BROCK.BIF` | highlight silhouette of the new shape | `MENU.SBF: 2PTR-PICK` | same |
| `MENU.SBF` text | "BROCK" -> "MOSS" (profile), "Brock" -> "Moss" (2P select) | `PROFILE`, `2PTR-PICK` | Options > Profiles, page 3 |
| `TITLE.SBF` text | "BROCK" -> "MOSS" | `PROFILE` | title screen attract loop (leave it idle) |
| `RESULT.SBF` text | "BROCK" -> "MOSS" | `2P-WINNER` | 2P results only |

Scene IDs: in these scenes 200 + n is trainer n (0 Ash, 1 Gary, 2 Brock, ...);
the 1P stage number picks `TRANSITION<stage>` and image `200 + stage`
(`src/main/043000.c`, `043D10.c`). Not in the archive and not changed: the 3D
model on the Profiles page, in-match graphics, "PEWTER CITY" on his profile.

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

**SBF1** (scene file, `screenLoad` in `src/main/screen.c`), big endian:
`"SBF1"`, image count, 16-byte image names, scene count, then per scene an id, a
name (`u32 len` + bytes) and four lists, each a `u32` count plus records:
areas (image index when `type & 0x80`), texts, sprites (`flags, x, y, ?, id, ?,
image index`, then optional words for flags 0x400 / 0x40) and extras. A text
record is 5 words (`flags, x, y, id, ?`), optional pairs for flags 0x40000800 and
0x4000, `u32 n` + n pairs, 2 words, `u32 n` + n words, then `u32 length` + u16
characters: `(kind << 10) | value`, kind 6 = A-Z (1-26), 7 = a-z, 5 = digits,
0 = space (1) / line break (2), 4 = punctuation. Nothing in the file stores
offsets, so a text can change length.

## How the swap is written

`replace` re-encodes the frames in place: same frame count, size, format and
palette size (CI palettes are rebuilt from the new image), so the game allocates
what it always did. `--rename FILE.SBF:Old=New` rewrites every text that reads
exactly `Old`. The new file must fit the old slot; the rest of the slot is
filled with 0xFF and the table's size field is updated. No other file moves.
Then the header CRC is recomputed (`ipl3checksum`, like `tools/checksummer.py`).
`verify` re-parses the modded ROM: the new frames decode to exactly the source
PNGs, kept frames are byte-identical, renamed scene files differ only in those
texts, and every other archive entry and every byte outside the edited slots,
their table rows and the CRC are unchanged. (The boot CRC only covers the first
1 MB, so these edits leave it as it was.)

Growing a file past its slot would mean repacking the archive. Code finds files
through the table (`fileFind`), but the archive is packed tight against the next
ROM segment, so repacking only works while the total shrinks; not implemented.

## Credit

The archive, 3FIB/BFF2 and RLE layout was first worked out by
[N64PPLEditorC](https://github.com/Adrien-G/N64PPLEditorC) (Adrien-G). This
tool is an independent Python implementation written from those notes and the
decomp's own `file.c`, `image.c`, `bitmap.c` and `screen.c`; no code is copied.
