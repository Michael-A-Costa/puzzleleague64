#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10", "ipl3checksum>=1.3.1,<2.0.0"]
# ///
"""Read and replace images in Pokémon Puzzle League's bin_file archive.

Format notes live in tools/mod/README.md. Subcommands:
  list     ROM                       table of archive entries
  extract  ROM OUT_DIR [NAME ...]    decode 3FIB images to PNG
  replace  ROM OUT_ROM NAME=PNG ...  re-encode frames in place and fix the CRC
  scenes   ROM [IMAGE.BIF ...] [--word W]  which scenes show an image / mention a word
  verify   ORIG_ROM MOD_ROM NAME=PNG ...  round-trip and isolation check
(replace and verify also take --rename FILE.SBF:Old=New for scene text)
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]

# BFF2 pixel formats (low byte of the BFF2 flags)
FMT_I4, FMT_I8, FMT_IA8 = 0x22, 0x23, 0x24
FMT_CI4, FMT_CI8 = 0x32, 0x33
FMT_RGBA16, FMT_RGBA32 = 0x54, 0x55

BFF2_HAS_NAME = 0x200
BFF2_HAS_TRANSPARENT_INDEX = 0x400
BFF2_RLE = 0x800
BFF2_PACKED_WIDTH = 0x200000
BFF2_SUBIMAGES = 0x400000

FIB_SECOND_COLOR = 0x10
FIB_NAME = 0x08


def archive_start(version: str = "usa") -> int:
    """ROM offset of the bin_file archive, from the splat config."""
    yaml = (REPO / "config" / version / f"puzzleleague64.{version}.yaml").read_text()
    m = re.search(r"- name: bin_file\s*\n\s*type: \.bin_file\s*\n\s*dir: bin_file\s*\n\s*start: (0x[0-9A-Fa-f]+)", yaml)
    if not m:
        sys.exit("bin_file segment not found in the splat config")
    return int(m.group(1), 16)


@dataclass
class Entry:
    index: int
    name: str
    size: int
    offset: int  # relative to the archive start
    table_pos: int  # ROM offset of this entry's table row


def read_table(rom: bytes, start: int) -> list[Entry]:
    (count,) = struct.unpack_from(">I", rom, start)
    entries = []
    for i in range(count):
        row = start + 4 + 0x18 * i
        size, offset = struct.unpack_from(">II", rom, row)
        name = rom[row + 8 : row + 0x18].split(b"\0", 1)[0].decode("ascii")
        entries.append(Entry(i, name, size, offset, row))
    return entries


# --- RLE (bitmapLoadRLE) -------------------------------------------------------
#   0xxxxxxx           literal: copy the next (x + 1) bytes
#   10nnnnnn b         one byte b repeated (n + 2) times
#   11lllrrr <l+2 B>   a block of (l + 2) bytes repeated (r + 2) times


def rle_decode(data: bytes, pos: int, out_size: int) -> tuple[bytes, int]:
    out = bytearray()
    while len(out) < out_size:
        b = data[pos]
        pos += 1
        if b & 0x80 == 0:
            n = b + 1
            out += data[pos : pos + n]
            pos += n
        elif b & 0x40 == 0:
            out += data[pos : pos + 1] * ((b & 0x3F) + 2)
            pos += 1
        else:
            n = ((b >> 3) & 7) + 2
            out += data[pos : pos + n] * ((b & 7) + 2)
            pos += n
    return bytes(out[:out_size]), pos


def rle_encode(raw: bytes) -> bytes:
    """Greedy encoder: at each position take the repeat that covers the most bytes."""
    out = bytearray()
    literal = bytearray()

    def flush():
        for i in range(0, len(literal), 128):
            chunk = literal[i : i + 128]
            out.append(len(chunk) - 1)
            out.extend(chunk)
        literal.clear()

    i, n = 0, len(raw)
    while i < n:
        best_cover, best = 0, None
        # single-byte run: up to 65 copies
        run = 1
        while i + run < n and run < 65 and raw[i + run] == raw[i]:
            run += 1
        if run >= 2:
            best_cover, best = run, ("run", run)
        # block repeats: block of 2..9 bytes, 2..9 times
        for blen in range(2, 10):
            if i + 2 * blen > n:
                break
            block = raw[i : i + blen]
            reps = 1
            while reps < 9 and raw[i + reps * blen : i + (reps + 1) * blen] == block:
                reps += 1
            # a block costs 1 + blen bytes; only worth it if it beats emitting literals
            if reps >= 2 and reps * blen > best_cover and reps * blen > 1 + blen:
                best_cover, best = reps * blen, ("block", blen, reps)
        if best is None or (best[0] == "run" and best_cover == 2 and literal):
            # a 2-byte run inside a literal stretch saves nothing
            literal.append(raw[i])
            i += 1
            continue
        flush()
        if best[0] == "run":
            out += bytes((0x80 | (best[1] - 2), raw[i]))
        else:
            _, blen, reps = best
            out.append(0xC0 | ((blen - 2) << 3) | (reps - 2))
            out += raw[i : i + blen]
        i += best_cover
    flush()
    return bytes(out)


# --- 3FIB / BFF2 ---------------------------------------------------------------


@dataclass
class Frame:
    hdr_pos: int  # offset of the 12-byte frame header (x, y, display time; little endian)
    bff2_pos: int
    flags: int
    width: int  # displayed width
    pixel_width: int  # stored width (row stride in pixels)
    height: int
    stride: int  # bytes per line
    name: str
    palette: list[tuple[int, int, int, int]]
    data_pos: int  # start of the (compressed) pixel data
    data_end: int
    pixels: bytes = b""

    @property
    def fmt(self) -> int:
        return self.flags & 0xFF

    @property
    def transparent_index(self) -> int | None:
        return (self.flags >> 12) & 0xFF if self.flags & BFF2_HAS_TRANSPARENT_INDEX else None


@dataclass
class Fib:
    flags: int
    frames: list[Frame] = field(default_factory=list)
    end: int = 0


def parse_fib(blob: bytes) -> Fib:
    if blob[:4] != b"3FIB":
        raise ValueError("not a 3FIB")
    flags, _color, count = struct.unpack_from("<I4sI", blob, 4)
    pos = 16
    if flags & FIB_SECOND_COLOR:
        pos += 4
    if flags & FIB_NAME:
        (nlen,) = struct.unpack_from("<I", blob, pos)
        pos += 4 + nlen
    fib = Fib(flags)
    for _ in range(count):
        hdr_pos = pos
        pos += 12
        fr = parse_bff2(blob, pos, hdr_pos)
        fib.frames.append(fr)
        pos = fr.data_end
    fib.end = pos
    return fib


def parse_bff2(blob: bytes, pos: int, hdr_pos: int) -> Frame:
    if blob[pos : pos + 4] != b"BFF2":
        raise ValueError(f"no BFF2 at 0x{pos:X}")
    start = pos
    flags, width, height, stride = struct.unpack_from(">IIII", blob, pos + 4)
    pos += 20
    pixel_width = width
    if flags & BFF2_PACKED_WIDTH:
        width, pixel_width = width >> 16, width & 0xFFFF
    name = ""
    if flags & BFF2_HAS_NAME:
        (nlen,) = struct.unpack_from(">I", blob, pos)
        name = blob[pos + 4 : pos + 4 + nlen].split(b"\0", 1)[0].decode("latin1")
        pos += 4 + nlen
    if flags & BFF2_SUBIMAGES:
        raise NotImplementedError("BFF2 sub-images are not supported")
    palette = []
    if flags & 0xF0 == 0x30:
        (ncolors,) = struct.unpack_from(">I", blob, pos)
        pos += 4
        palette = [tuple(blob[pos + 4 * i : pos + 4 * i + 4]) for i in range(ncolors)]
        pos += 4 * ncolors
    data_pos = pos
    size = stride * height
    if flags & BFF2_RLE:
        pixels, pos = rle_decode(blob, pos, size)
    else:
        pixels, pos = blob[pos : pos + size], pos + size
    fr = Frame(hdr_pos, start, flags, width, pixel_width, height, stride, name, palette, data_pos, pos)
    fr.pixels = pixels
    return fr


def frame_to_image(fr: Frame) -> Image.Image:
    w, h, px = fr.pixel_width, fr.height, fr.pixels
    rgba = bytearray()
    for y in range(h):
        row = px[y * fr.stride : (y + 1) * fr.stride]
        for x in range(w):
            f = fr.fmt
            if f in (FMT_CI4, FMT_CI8):
                idx = (row[x // 2] >> (4 * (1 - x % 2))) & 0xF if f == FMT_CI4 else row[x]
                r, g, b, a = fr.palette[idx] if idx < len(fr.palette) else (255, 0, 255, 255)
                if idx == fr.transparent_index:
                    a = 0
            elif f == FMT_RGBA16:
                v = (row[2 * x] << 8) | row[2 * x + 1]
                r, g, b = ((v >> 11) & 31) * 255 // 31, ((v >> 6) & 31) * 255 // 31, ((v >> 1) & 31) * 255 // 31
                a = 255 if v & 1 else 0
            elif f == FMT_RGBA32:
                r, g, b, a = row[4 * x : 4 * x + 4]
            elif f == FMT_IA8:
                r = g = b = row[2 * x]
                a = row[2 * x + 1]
            elif f == FMT_I4:
                r = g = b = ((row[x // 2] >> (4 * (1 - x % 2))) & 0xF) * 17
                a = 255
            elif f == FMT_I8:
                r = g = b = row[x]
                a = 255
            else:
                raise NotImplementedError(f"pixel format 0x{f:02X}")
            rgba += bytes((r, g, b, a))
    img = Image.frombytes("RGBA", (w, h), bytes(rgba))
    return img.crop((0, 0, fr.width, h)) if fr.width < w else img


def encode_pixels(fr: Frame, img: Image.Image) -> bytes:
    """Map an RGBA image onto the frame's existing format (and palette, for CI)."""
    if img.size != (fr.width, fr.height):
        raise ValueError(f"{fr.name}: image is {img.size}, frame is {(fr.width, fr.height)}")
    img = img.convert("RGBA")
    out = bytearray()
    pal = fr.palette
    nearest: dict[tuple[int, int, int], int] = {}
    for y in range(fr.height):
        row = bytearray(fr.stride)
        for x in range(fr.pixel_width):
            r, g, b, a = img.getpixel((min(x, fr.width - 1), y))
            f = fr.fmt
            if f in (FMT_CI4, FMT_CI8):
                if a < 128 and fr.transparent_index is not None:
                    idx = fr.transparent_index
                else:
                    idx = nearest.get((r, g, b))
                    if idx is None:
                        idx = nearest[(r, g, b)] = min(
                            (i for i in range(len(pal)) if i != fr.transparent_index),
                            key=lambda i: (pal[i][0] - r) ** 2 + (pal[i][1] - g) ** 2 + (pal[i][2] - b) ** 2,
                        )
                if f == FMT_CI4:
                    row[x // 2] |= idx << (4 * (1 - x % 2))
                else:
                    row[x] = idx
            elif f == FMT_RGBA16:
                v = (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | (1 if a >= 128 else 0)
                row[2 * x : 2 * x + 2] = v.to_bytes(2, "big")
            elif f == FMT_RGBA32:
                row[4 * x : 4 * x + 4] = bytes((r, g, b, a))
            elif f == FMT_IA8:
                row[2 * x : 2 * x + 2] = bytes((r, a))
            else:
                raise NotImplementedError(f"encoding format 0x{f:02X}")
        out += row
    return bytes(out)


def fit_palette(fr: Frame, img: Image.Image) -> list[tuple[int, int, int, int]]:
    """New palette for a CI frame: the image's opaque colours, same entry count,
    with the transparent slot left where the game expects it."""
    img = img.convert("RGBA")
    colours = sorted({(r, g, b) for r, g, b, a in rgba_pixels(img) if a >= 128})
    slots = [i for i in range(len(fr.palette)) if i != fr.transparent_index]
    if len(colours) > len(slots):
        raise ValueError(f"{fr.name}: {len(colours)} colours, palette has room for {len(slots)}")
    pal = list(fr.palette)
    for i, slot in enumerate(slots):
        r, g, b = colours[i] if i < len(colours) else colours[0]
        pal[slot] = (r, g, b, 255)
    return pal


def replace_frames(blob: bytes, images: list[Image.Image | None]) -> bytes:
    """Rebuild a 3FIB with new pixels for the frames given (None keeps a frame).

    Frame count, dimensions, format and palette size stay as they were, so the game
    allocates the same memory for it. CI frames get a palette built from the image."""
    fib = parse_fib(blob)
    if len(images) > len(fib.frames):
        raise ValueError(f"{len(images)} images for {len(fib.frames)} frames")
    out = bytearray()
    cursor = 0
    for fr, img in zip(fib.frames, images):
        if img is None:
            continue
        if not fr.flags & BFF2_RLE:
            raise NotImplementedError("only RLE frames are supported")
        if fr.palette:
            pal_pos = fr.data_pos - 4 * len(fr.palette)
            out += blob[cursor:pal_pos]
            fr.palette = fit_palette(fr, img)
            out += b"".join(bytes(c) for c in fr.palette)
        else:
            out += blob[cursor : fr.data_pos]
        out += rle_encode(encode_pixels(fr, img))
        cursor = fr.data_end
    out += blob[cursor:]
    return bytes(out)


def rgba_pixels(img: Image.Image) -> list[tuple[int, int, int, int]]:
    raw = img.convert("RGBA").tobytes()
    return [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)]


def same_image(a: Image.Image, b: Image.Image) -> bool:
    """Pixel-equal, treating every fully transparent pixel as the same."""
    a, b = a.convert("RGBA"), b.convert("RGBA")
    if a.size != b.size:
        return False
    norm = lambda p: (0, 0, 0, 0) if p[3] == 0 else p
    return all(norm(p) == norm(q) for p, q in zip(rgba_pixels(a), rgba_pixels(b)))


# --- SBF1 scenes (screenLoad in src/main/screen.c) ---------------------------------
#
# Big endian: "SBF1", image count, 16-byte image names, scene count, then per scene:
# id, name (u32 len + bytes), and four object lists, each a u32 count followed by
# variable-size records (areas, texts, sprites, extras). Optional fields depend on
# flag bits, as below. Text is u16 codes: (kind << 10) | value, kind 6 = A-Z,
# 7 = a-z, 5 = digits, 0 = spaces/line breaks, 4 = punctuation.


@dataclass
class Text:
    flags: int
    x: int
    y: int
    ident: int
    count_pos: int  # offset of the u32 character count
    chars: list[int]


@dataclass
class Sprite:
    flags: int
    x: int
    y: int
    ident: int
    image: int  # index into the scene file's image list, -1 for none


@dataclass
class Scene:
    ident: int
    name: str
    area_images: list[int] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    sprites: list[Sprite] = field(default_factory=list)
    extra_images: list[int] = field(default_factory=list)


@dataclass
class Sbf:
    images: list[str]
    scenes: list[Scene]
    end: int


def parse_sbf(blob: bytes) -> Sbf:
    pos = 0

    def word(signed=False):
        nonlocal pos
        (v,) = struct.unpack_from(">i" if signed else ">I", blob, pos)
        pos += 4
        return v

    def skip(n):
        nonlocal pos
        pos += n

    if blob[:4] != b"SBF1":
        raise ValueError("not an SBF1")
    skip(4)
    images = []
    for _ in range(word()):
        images.append(blob[pos : pos + 16].split(b"\0", 1)[0].decode("ascii"))
        skip(16)
    scenes = []
    for _ in range(word()):
        ident = word(True)
        nlen = word()
        sc = Scene(ident, blob[pos : pos + nlen].split(b"\0", 1)[0].decode("latin1"))
        skip(nlen)
        for _ in range(word()):  # areas
            kind = word()
            skip(4 * 5)
            points = word(True)
            skip(8 * max(points, 0))
            skip(4 * sum(bool(kind & bit) for bit in (0x2000, 0x10, 0x20)))
            if kind & 0x80:
                sc.area_images.append(word(True))
            if kind & 0x40:
                skip(16)
        for _ in range(word()):  # texts
            flags, x, y, ident_t, _ = word(), word(True), word(True), word(True), word(True)
            if flags & 0x40000800:
                skip(8)
            if flags & 0x4000:
                skip(8)
            skip(8 * word())
            skip(8)
            skip(4 * max(word(True), 0))
            count_pos = pos
            n = word()
            chars = list(struct.unpack_from(f">{n}H", blob, pos))
            skip(2 * n)
            sc.texts.append(Text(flags, x, y, ident_t, count_pos, chars))
        for _ in range(word()):  # sprites
            flags, x, y, _, ident_s, _, image = word(), word(True), word(True), word(), word(True), word(), word(True)
            if flags & 0x400:
                skip(4)
            if flags & 0x40:
                skip(8)
            sc.sprites.append(Sprite(flags, x, y, ident_s, image))
        for _ in range(word()):  # extras
            flags = word()
            skip(12)
            if flags & 1:
                sc.extra_images.append(word(True))
            skip(4 * sum(bool(flags & bit) for bit in (0x10, 0x20)) + (8 if flags & 4 else 0) + 4)
        scenes.append(sc)
    return Sbf(images, scenes, pos)


def decode_text(chars: list[int]) -> str:
    out = ""
    for c in chars:
        kind, v = c >> 10, c & 0x3FF
        if kind == 6 and 1 <= v <= 26:
            out += chr(64 + v)
        elif kind == 7 and 1 <= v <= 26:
            out += chr(96 + v)
        elif kind == 5 and v <= 10:
            out += str(v % 10)
        elif kind == 0 and v == 1:
            out += " "
        else:
            out += f"{{{kind}:{v}}}"
    return out


def encode_text(s: str) -> list[int]:
    out = []
    for ch in s:
        if "A" <= ch <= "Z":
            out.append(6 << 10 | (ord(ch) - 64))
        elif "a" <= ch <= "z":
            out.append(7 << 10 | (ord(ch) - 96))
        elif ch == " ":
            out.append(1)
        else:
            raise ValueError(f"no text code for {ch!r}")
    return out


def rename_texts(blob: bytes, old: str, new: str) -> tuple[bytes, int]:
    """Replace every text object that reads exactly `old` with `new`. Only the
    character count and the characters change; the file shrinks or grows by
    2 bytes per character."""
    sbf = parse_sbf(blob)
    hits = [t for sc in sbf.scenes for t in sc.texts if decode_text(t.chars) == old]
    out = bytearray(blob)
    for t in sorted(hits, key=lambda t: t.count_pos, reverse=True):
        codes = encode_text(new)
        start, end = t.count_pos, t.count_pos + 4 + 2 * len(t.chars)
        out[start:end] = struct.pack(f">I{len(codes)}H", len(codes), *codes)
    return bytes(out), len(hits)


# --- ROM helpers -----------------------------------------------------------------


def load_rom(path: Path) -> bytearray:
    rom = bytearray(path.read_bytes())
    if rom[:4] != b"\x80\x37\x12\x40":
        sys.exit(f"{path}: not a big-endian .z64 ROM")
    return rom


def fix_crc(rom: bytearray) -> None:
    import ipl3checksum

    cic = ipl3checksum.detectCIC(rom) or ipl3checksum.CICKind.CIC_6102_7101
    c1, c2 = ipl3checksum.calculateChecksum(rom, cic)
    struct.pack_into(">II", rom, 0x10, c1, c2)


def find(entries: list[Entry], name: str) -> Entry:
    for e in entries:
        if e.name.upper() == name.upper():
            return e
    sys.exit(f"{name}: not in the archive")


def parse_pairs(pairs: list[str]) -> dict[str, list[Path | None]]:
    """NAME=a.png[,b.png...] -> {NAME: [paths]}: one PNG per frame, in order; an
    empty item keeps that frame."""
    out: dict[str, list[Path | None]] = {}
    for p in pairs:
        name, _, files = p.partition("=")
        out[name.upper()] = [Path(f) if f else None for f in files.split(",")]
    return out


def parse_renames(renames: list[str]) -> dict[str, list[tuple[str, str]]]:
    """FILE.SBF:Old=New -> {FILE.SBF: [(old, new)]}"""
    out: dict[str, list[tuple[str, str]]] = {}
    for r in renames:
        name, _, rest = r.partition(":")
        old, _, new = rest.partition("=")
        out.setdefault(name.upper(), []).append((old, new))
    return out


def edited_file(old: bytes, pngs: list[Path | None] | None, renames: list[tuple[str, str]] | None) -> tuple[bytes, str]:
    if pngs:
        return replace_frames(old, [Image.open(p) if p else None for p in pngs]), f"{sum(1 for p in pngs if p)} frame(s)"
    notes = []
    for o, n in renames or []:
        old, hits = rename_texts(old, o, n)
        if not hits:
            sys.exit(f"text {o!r} not found")
        notes.append(f"{o!r} -> {n!r} x{hits}")
    return old, ", ".join(notes)


def apply_edits(rom: bytearray, start: int, repl, renames) -> list[str]:
    entries = read_table(rom, start)
    log = []
    for name in sorted(set(repl) | set(renames)):
        e = find(entries, name)
        rom_pos = start + e.offset
        old = bytes(rom[rom_pos : rom_pos + e.size])
        new, note = edited_file(old, repl.get(name), renames.get(name))
        if len(new) > e.size:
            # Growing a file would move every later file; not needed so far.
            sys.exit(f"{name}: new data is {len(new)} bytes, slot holds {e.size}")
        rom[rom_pos : rom_pos + len(new)] = new
        rom[rom_pos + len(new) : rom_pos + e.size] = b"\xff" * (e.size - len(new))
        struct.pack_into(">I", rom, e.table_pos, len(new))
        log.append(f"{name}: ROM 0x{rom_pos:X}, {e.size} -> {len(new)} bytes, {note}")
    return log


# --- commands --------------------------------------------------------------------


def cmd_list(a):
    rom = load_rom(a.rom)
    start = archive_start(a.version)
    for e in read_table(rom, start):
        kind = rom[start + e.offset : start + e.offset + 4].decode("latin1")
        extra = ""
        if kind == "3FIB":
            try:
                fib = parse_fib(bytes(rom[start + e.offset : start + e.offset + e.size]))
                f0 = fib.frames[0]
                extra = f" {len(fib.frames)}fr {f0.width}x{f0.height} fmt=0x{f0.fmt:02X}"
            except (ValueError, NotImplementedError, IndexError) as ex:
                extra = f" ({ex})"
        print(f"{e.index:3} 0x{start + e.offset:07X} {e.size:8} {e.name:16} {kind}{extra}")


def cmd_extract(a):
    rom = load_rom(a.rom)
    start = archive_start(a.version)
    a.out.mkdir(parents=True, exist_ok=True)
    wanted = {n.upper() for n in a.names}
    for e in read_table(rom, start):
        if wanted and e.name.upper() not in wanted:
            continue
        blob = bytes(rom[start + e.offset : start + e.offset + e.size])
        if blob[:4] != b"3FIB":
            continue
        try:
            fib = parse_fib(blob)
            for i, fr in enumerate(fib.frames):
                frame_to_image(fr).save(a.out / f"{Path(e.name).stem}_{i:02}.png")
        except (ValueError, NotImplementedError, IndexError) as ex:
            print(f"skip {e.name}: {ex}", file=sys.stderr)


def cmd_scenes(a):
    """Which scene shows which image, and every text that mentions a word."""
    rom = load_rom(a.rom)
    start = archive_start(a.version)
    images = {n.upper() for n in a.images}
    for e in read_table(rom, start):
        blob = bytes(rom[start + e.offset : start + e.offset + e.size])
        if blob[:4] != b"SBF1":
            continue
        sbf = parse_sbf(blob)
        for sc in sbf.scenes:
            for sp in sc.sprites:
                if sp.image >= 0 and sbf.images[sp.image].upper() in images:
                    print(f"{e.name:13} {sc.name:16} sprite id={sp.ident:<5} at ({sp.x},{sp.y}) {sbf.images[sp.image]}")
            for kind, idxs in (("area", sc.area_images), ("extra", sc.extra_images)):
                for i in idxs:
                    if i >= 0 and sbf.images[i].upper() in images:
                        print(f"{e.name:13} {sc.name:16} {kind} {sbf.images[i]}")
            for t in sc.texts:
                s = decode_text(t.chars)
                if a.word and a.word.lower() in s.lower():
                    print(f"{e.name:13} {sc.name:16} text id={t.ident:<5} at ({t.x},{t.y}) {s[:70]!r}")


def cmd_replace(a):
    rom = load_rom(a.rom)
    for line in apply_edits(rom, archive_start(a.version), parse_pairs(a.pairs), parse_renames(a.rename)):
        print(line)
    fix_crc(rom)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(rom)
    print(f"wrote {a.out}")


def cmd_verify(a):
    orig, mod = load_rom(a.orig), load_rom(a.mod)
    start = archive_start(a.version)
    repl, renames = parse_pairs(a.pairs), parse_renames(a.rename)
    edited = set(repl) | set(renames)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok &= cond

    check(len(orig) == len(mod), f"ROM size {len(mod):#x}")
    t_orig, t_mod = read_table(orig, start), read_table(mod, start)
    check(len(t_orig) == len(t_mod), f"{len(t_mod)} archive entries")
    same = 0
    for eo, em in zip(t_orig, t_mod):
        a_o = bytes(orig[start + eo.offset : start + eo.offset + eo.size])
        a_m = bytes(mod[start + em.offset : start + em.offset + em.size])
        name = eo.name.upper()
        if name in repl:
            fib = parse_fib(a_m)
            check(fib.end == em.size, f"{em.name}: re-parses, {len(fib.frames)} frames, ends at its size")
            fo = parse_fib(a_o)
            check(len(fo.frames) == len(fib.frames), f"{em.name}: frame count unchanged")
            pngs = repl[name]
            for i, (x, y) in enumerate(zip(fo.frames, fib.frames)):
                png = pngs[i] if i < len(pngs) else None
                if png:
                    check(same_image(frame_to_image(y), Image.open(png)), f"{em.name} frame {i}: decodes to exactly {png.name}")
                else:
                    check(
                        a_o[x.hdr_pos : x.data_end] == a_m[y.hdr_pos : y.data_end],
                        f"{em.name} frame {i}: bytes unchanged",
                    )
                check(
                    (x.flags, x.width, x.pixel_width, x.height, x.stride, len(x.palette))
                    == (y.flags, y.width, y.pixel_width, y.height, y.stride, len(y.palette)),
                    f"{em.name} frame {i}: flags/size/stride/palette size unchanged",
                )
        if name in renames:
            so, sm = parse_sbf(a_o), parse_sbf(a_m)
            check(sm.end == em.size, f"{em.name}: re-parses, {len(sm.scenes)} scenes, ends at its size")
            texts_o = [decode_text(t.chars) for sc in so.scenes for t in sc.texts]
            texts_m = [decode_text(t.chars) for sc in sm.scenes for t in sc.texts]
            mapping = dict(renames[name])
            check(
                [mapping.get(t, t) for t in texts_o] == texts_m,
                f"{em.name}: {sum(t in mapping for t in texts_o)} text(s) renamed, other {len(texts_o) - sum(t in mapping for t in texts_o)} unchanged",
            )
            strip = lambda s: [(sc.ident, sc.name, sc.area_images, sc.sprites, sc.extra_images,
                                [(t.flags, t.x, t.y, t.ident) for t in sc.texts]) for sc in s.scenes]
            check(so.images == sm.images and strip(so) == strip(sm), f"{em.name}: images, sprites and text layout unchanged")
        if name in edited:
            check(eo.offset == em.offset, f"{em.name}: offset unchanged")
        elif (eo.name, eo.offset, eo.size) == (em.name, em.offset, em.size) and a_o == a_m:
            same += 1
        else:
            check(False, f"{eo.name}: differs")
    check(same == len(t_orig) - len(edited), f"{same} other entries byte-identical (name, offset, size, data)")

    # Everything outside the edited slots, their table rows and the CRC words must match.
    allowed = [(0x10, 0x18)]
    for name in edited:
        e = find(t_orig, name)
        allowed += [(start + e.offset, start + e.offset + e.size), (e.table_pos, e.table_pos + 4)]
    diffs = [i for i in range(len(orig)) if orig[i] != mod[i]] if len(orig) == len(mod) else []
    stray = [i for i in diffs if not any(lo <= i < hi for lo, hi in allowed)]
    check(not stray, f"{len(diffs)} differing bytes, {len(stray)} outside the edited slots/table rows/CRC")

    fixed = bytearray(mod)
    fix_crc(fixed)
    check(fixed[0x10:0x18] == mod[0x10:0x18], f"header CRC valid ({mod[0x10:0x14].hex()} {mod[0x14:0x18].hex()})")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", default="usa")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("list")
    s.add_argument("rom", type=Path)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("extract")
    s.add_argument("rom", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("names", nargs="*")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("scenes")
    s.add_argument("rom", type=Path)
    s.add_argument("images", nargs="*", metavar="IMAGE.BIF")
    s.add_argument("--word", help="also list texts containing this (case-insensitive)")
    s.set_defaults(func=cmd_scenes)

    for cmd, func, roms in (("replace", cmd_replace, ("rom", "out")), ("verify", cmd_verify, ("orig", "mod"))):
        s = sub.add_parser(cmd)
        for r in roms:
            s.add_argument(r, type=Path)
        s.add_argument("pairs", nargs="*", metavar="NAME=PNG[,PNG...]")
        s.add_argument("--rename", action="append", default=[], metavar="FILE.SBF:Old=New")
        s.set_defaults(func=func)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
