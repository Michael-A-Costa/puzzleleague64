#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10"]
# ///
"""Paint Moss over Brock in the 2P trainer-select group picture.

TRSELECT.BIF (320x240) shows every trainer at once; BROCK.BIF is Brock's
highlight silhouette, drawn at (97, 73) on top of it. The silhouette marks exactly
which pixels of the group picture are Brock, so Moss is painted inside that shape
(with the spiky hair rounded off into curls) and nothing else in the picture is
touched. A matching silhouette is made from Moss's shape.

Both outputs contain the rest of the original picture, so they are generated at
build time from the base ROM into a gitignored folder, never committed.

usage: paint_group.py BASE_ROM OUT_DIR
"""

import sys
from pathlib import Path

from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pplmod  # noqa: E402

ORIGIN = (97, 73)  # where 2PTR-PICK draws BROCK.BIF over TRSELECT.BIF
HAIR_ROWS = 17  # silhouette rows above the face

SKIN, OUTLINE = (214, 160, 118), (20, 24, 20)
HAIR, HAIR_DARK = (92, 176, 64), (56, 120, 44)
COAT, COAT_DARK = (222, 170, 40), (176, 128, 26)
PANTS, PANTS_DARK = (40, 80, 90), (26, 54, 62)
SHOES = (90, 60, 40)


def frame0(rom: bytes, start: int, name: str) -> pplmod.Frame:
    e = pplmod.find(pplmod.read_table(rom, start), name)
    return pplmod.parse_fib(rom[start + e.offset : start + e.offset + e.size]).frames[0]


def moss_mask(silhouette: Image.Image) -> Image.Image:
    """Brock's shape with the hair spikes merged into one round, bumpy cap."""
    w, h = silhouette.size
    body = silhouette.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    hair = body.crop((0, 0, w, HAIR_ROWS + 2))
    cap = hair.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))
    mask = body.copy()
    mask.paste(cap.crop((0, 0, w, HAIR_ROWS)), (0, 0), cap.crop((0, 0, w, HAIR_ROWS)))
    return mask


def colour(x: int, y: int, cx: int) -> tuple[int, int, int]:
    """Moss, standing: curly hair, glasses, mustard coat, teal trousers."""
    if y < HAIR_ROWS:
        return HAIR_DARK if (x % 4, y % 4) in ((0, 1), (1, 2), (2, 3), (3, 0)) and (x // 4 + y // 4) % 2 else HAIR
    if y < 29:
        for gx in (cx - 3, cx + 3):  # glasses
            d = (x - gx) ** 2 + (y - 21) ** 2
            if d <= 1:
                return OUTLINE
            if d <= 4:
                return (200, 236, 240)
            if d <= 7:
                return (40, 40, 120)
        if y == 25 and abs(x - cx) <= 2:
            return (120, 30, 40)
        return SKIN
    if y < 64:
        if y < 37 and abs(x - cx) <= (37 - y) // 2:  # collar
            return (60, 40, 30)
        if y in (43, 51):  # folded arms
            return COAT_DARK
        if (x - cx - 7) ** 2 + (y - 36) ** 2 <= 2:  # leaf pin
            return HAIR
        return COAT
    if y < 111:
        return PANTS_DARK if abs(x - cx - 1) == 0 and y > 70 else PANTS
    return SHOES


def main():
    base, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rom = bytes(pplmod.load_rom(base))
    start = pplmod.archive_start()
    group = pplmod.frame_to_image(frame0(rom, start, "TRSELECT.BIF"))
    silhouette = pplmod.frame_to_image(frame0(rom, start, "BROCK.BIF"))
    mask = moss_mask(silhouette)
    w, h = mask.size
    inside = lambda x, y: 0 <= x < w and 0 <= y < h and mask.getpixel((x, y))

    ox, oy = ORIGIN
    for y in range(h):
        xs = [x for x in range(w) if inside(x, y)]
        if not xs:
            continue
        cx = (min(xs) + max(xs)) // 2 if y >= HAIR_ROWS else 18
        for x in xs:
            edge = not all(inside(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            group.putpixel((ox + x, oy + y), (*(OUTLINE if edge else colour(x, y, cx)), 255))

    # silhouette: solid grey inside, a fading white rim outside, like the original's
    ring1 = mask.filter(ImageFilter.MaxFilter(3))
    ring2 = mask.filter(ImageFilter.MaxFilter(5))
    sil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w):
            if mask.getpixel((x, y)):
                sil.putpixel((x, y), (128, 128, 128, 255))
            elif ring1.getpixel((x, y)):
                sil.putpixel((x, y), (255, 255, 255, 96))
            elif ring2.getpixel((x, y)):
                sil.putpixel((x, y), (255, 255, 255, 32))

    group.save(out / "trselect.png")
    sil.save(out / "silhouette.png")
    print(f"wrote {out / 'trselect.png'} and {out / 'silhouette.png'}")


if __name__ == "__main__":
    main()
