#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10"]
# ///
"""Draw "MOSS", an original placeholder trainer, as two 104x129 portraits.

Matches the layout of the VS/select portraits (c<name>.bif): picture in the left
99 columns, the last 5 columns transparent, name lettered across the top. Frame 1
faces the other way. Output is quantized so it fits a 256-colour CI8 palette
exactly, which lets the mod tool round-trip it losslessly.
"""

from pathlib import Path

from PIL import Image, ImageDraw

W, H, CONTENT_W, SS = 104, 129, 99, 4  # SS: supersampling factor
OUT = Path(__file__).resolve().parent

# 5x7 pixel glyphs, only the letters we need
GLYPHS = {
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
}


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw_portrait() -> Image.Image:
    w, h = CONTENT_W * SS, H * SS
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)

    # background: teal-to-forest vertical gradient (one colour per row keeps RLE small)
    top, bottom = (34, 150, 140), (16, 70, 52)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(top, bottom, y / h))

    s = SS
    outline = (20, 24, 20)
    # shoulders: mustard raincoat with a dark collar
    d.ellipse([-10 * s, 100 * s, 110 * s, 170 * s], fill=(222, 170, 40), outline=outline, width=s)
    d.polygon([(38 * s, 100 * s), (50 * s, 122 * s), (62 * s, 100 * s)], fill=(60, 40, 30), outline=outline)
    # neck
    d.rectangle([42 * s, 86 * s, 58 * s, 104 * s], fill=(196, 140, 100), outline=outline, width=s)
    # head
    d.ellipse([24 * s, 42 * s, 76 * s, 98 * s], fill=(214, 160, 118), outline=outline, width=s)
    # moss-green hair: a round cap of curls
    for cx, cy, r in [(30, 52, 10), (40, 42, 12), (52, 38, 13), (64, 43, 12), (72, 54, 9), (50, 50, 14)]:
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=(92, 176, 64), outline=outline, width=s)
    d.ellipse([38 * s, 46 * s, 62 * s, 60 * s], fill=(92, 176, 64))
    # big round glasses
    for cx in (40, 60):
        d.ellipse([(cx - 8) * s, 64 * s, (cx + 8) * s, 80 * s], fill=(200, 236, 240), outline=(40, 40, 120), width=2 * s)
        d.ellipse([(cx - 3) * s, 69 * s, (cx + 2) * s, 75 * s], fill=outline)
    d.line([(48 * s, 71 * s), (52 * s, 71 * s)], fill=(40, 40, 120), width=2 * s)
    # cheeks and a wide grin
    for cx in (33, 67):
        d.ellipse([(cx - 4) * s, 81 * s, (cx + 4) * s, 86 * s], fill=(230, 120, 110))
    d.chord([40 * s, 80 * s, 60 * s, 93 * s], 0, 180, fill=(120, 30, 40), outline=outline, width=s)
    d.rectangle([43 * s, 86 * s, 57 * s, 88 * s], fill=(250, 250, 245))
    # a leaf pin on the coat
    d.ellipse([70 * s, 112 * s, 82 * s, 120 * s], fill=(92, 176, 64), outline=outline, width=s)

    small = img.resize((CONTENT_W, H), Image.LANCZOS)

    # name, drawn after downscaling so the letters stay crisp
    sd = ImageDraw.Draw(small)
    name, scale = "MOSS", 2
    text_w = len(name) * 6 * scale - scale
    x0, y0 = (CONTENT_W - text_w) // 2, 6
    for dx, dy, colour in [(o, p, outline) for o in (-1, 0, 1) for p in (-1, 0, 1)] + [(0, 0, (250, 214, 60))]:
        for i, ch in enumerate(name):
            for gy, row in enumerate(GLYPHS[ch]):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        x = x0 + (i * 6 + gx) * scale + dx
                        y = y0 + gy * scale + dy
                        sd.rectangle([x, y, x + scale - 1, y + scale - 1], fill=colour)
    return small


def finish(content: Image.Image) -> Image.Image:
    # 200 colours leaves room in the palette for the transparent slot
    q = content.quantize(colors=200, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGBA")
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    frame.paste(q, (0, 0))
    return frame


def draw_head() -> Image.Image:
    """30x33 head for the records screen (RECHEADS.BIF frame 1), RGBA with soft edges."""
    s = 8
    img = Image.new("RGBA", (30 * s, 33 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    outline = (20, 24, 20, 255)
    d.ellipse([5 * s, 9 * s, 25 * s, 31 * s], fill=(214, 160, 118, 255), outline=outline, width=s)
    for cx, cy, r in [(7, 11, 5), (11, 6, 6), (16, 4, 6), (21, 6, 6), (24, 11, 5), (15, 9, 7)]:
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=(92, 176, 64, 255), outline=outline, width=s)
    d.ellipse([10 * s, 7 * s, 20 * s, 13 * s], fill=(92, 176, 64, 255))
    for cx in (11.5, 18.5):
        d.ellipse([(cx - 3.5) * s, 16 * s, (cx + 3.5) * s, 23 * s], fill=(200, 236, 240, 255), outline=(40, 40, 120, 255), width=s)
        d.ellipse([(cx - 1) * s, 18.5 * s, (cx + 1) * s, 20.5 * s], fill=outline)
    d.chord([11 * s, 23 * s, 19 * s, 29 * s], 0, 180, fill=(120, 30, 40, 255), outline=outline, width=s)
    # hard alpha edge and few colours, like the game's own heads: it must compress
    # into the old frame's space (RLE only pays off on repeated pixels)
    small = img.resize((30, 33), Image.LANCZOS)
    alpha = small.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    rgb = small.convert("RGB").quantize(colors=20, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    rgb.putalpha(alpha)
    return Image.composite(rgb, Image.new("RGBA", rgb.size, (0, 0, 0, 0)), alpha)


def draw_background() -> Image.Image:
    """128x96 puzzle-editor background (PBACKBROCK.BIF): Moss's greenhouse."""
    w, h = 128, 96
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp((170, 220, 230), (120, 190, 170), y / h))
    # glass panes: white frame lines
    for x in range(0, w, 32):
        d.line([(x, 0), (x, 60)], fill=(240, 250, 250), width=2)
    for y in (20, 40):
        d.line([(0, y), (w, y)], fill=(240, 250, 250), width=2)
    d.rectangle([0, 60, w, h], fill=(120, 84, 56))  # soil bed
    d.rectangle([0, 58, w, 61], fill=(90, 60, 40))
    # rows of round shrubs and pots
    for i, cx in enumerate(range(12, w, 26)):
        green = (70, 150, 60) if i % 2 else (92, 176, 64)
        d.ellipse([cx - 11, 38, cx + 11, 62], fill=green, outline=(30, 70, 30))
        d.polygon([(cx - 8, 66), (cx + 8, 66), (cx + 6, 82), (cx - 6, 82)], fill=(200, 110, 70), outline=(110, 60, 40))
        d.ellipse([cx - 3, 44, cx + 3, 50], fill=(240, 200, 70))
    return img.convert("RGBA")


def main():
    content = draw_portrait()
    finish(content).save(OUT / "moss_00.png")
    # frame 1: face the other way, but keep the name readable and the picture in the left 99 columns
    flipped = content.transpose(Image.FLIP_LEFT_RIGHT)
    flipped.paste(content.crop((0, 0, CONTENT_W, 24)), (0, 0))
    finish(flipped).save(OUT / "moss_01.png")
    draw_head().save(OUT / "moss_head.png")
    draw_background().save(OUT / "moss_back.png")
    print(f"wrote moss_00.png, moss_01.png, moss_head.png, moss_back.png in {OUT}")


if __name__ == "__main__":
    main()
