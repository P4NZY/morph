"""Draw Morph's app icon and pack it into packaging/Morph.icns (needs macOS iconutil)."""

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 1024


def draw(size: int = S) -> Image.Image:
    k = size / S
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # macOS icon grid: 824px rounded square centred on a 1024 canvas
    box = [int(100 * k), int(100 * k), int(924 * k), int(924 * k)]
    radius = int(185 * k)

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([box[0], box[1] + int(12 * k), box[2], box[3] + int(12 * k)],
                                             radius, fill=(20, 16, 60, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * k)))

    # vertical indigo gradient
    grad = Image.new("RGBA", (size, size))
    top, bottom = (129, 131, 249), (67, 56, 202)
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = min(max((y - box[1]) / (box[3] - box[1]), 0), 1)
        gd.line([(0, y), (size, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # the in-app logo's mountain mark (32-unit grid: 9,21 14,11 18,18 20,15 23,21)
    x0, y0, unit = box[0] + int(70 * k), box[1] + int(30 * k), (box[2] - box[0] - int(140 * k)) / 16
    pts = [(9, 21), (14, 11), (18, 18), (20, 15), (23, 21)]
    poly = [(x0 + (px - 8) * unit, y0 + (py - 8) * unit) for px, py in pts]
    ImageDraw.Draw(img).polygon(poly, fill=(255, 255, 255, 255))

    # little "upscale" sparkle, top-right
    cx, cy, r = box[0] + int(640 * k), box[1] + int(215 * k), int(70 * k)
    star = [(cx, cy - r), (cx + r * .28, cy - r * .28), (cx + r, cy), (cx + r * .28, cy + r * .28),
            (cx, cy + r), (cx - r * .28, cy + r * .28), (cx - r, cy), (cx - r * .28, cy - r * .28)]
    ImageDraw.Draw(img).polygon(star, fill=(255, 255, 255, 235))
    return img


def main() -> None:
    master = draw()
    master.save(HERE / "icon.png")
    iconset = HERE / "Morph.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            master.resize((px, px), Image.LANCZOS).save(iconset / name)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "Morph.icns")], check=True)
    shutil.rmtree(iconset)
    print("wrote", HERE / "Morph.icns")


if __name__ == "__main__":
    main()
