"""Build Morph's app icon from packaging/icon-art.webp.

Takes a square crop of the artwork, shapes it like a macOS app icon (rounded
square on the standard 824-of-1024 grid with a soft shadow) and packs it into
packaging/Morph.icns (needs macOS iconutil). Also writes static/favicon.png.
"""

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
ART = HERE / "icon-art.webp"
CROP = (448, 95, 520)  # left, top, size of the square taken from the artwork
S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    box = [100, 100, 924, 924]          # macOS icon grid
    radius = 185

    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([box[0], box[1] + 12, box[2], box[3] + 12],
                                             radius, fill=(0, 0, 0, 100))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))

    x, y, size = CROP
    art = Image.open(ART).convert("RGB").crop((x, y, x + size, y + size))
    art = art.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.paste(art, (box[0], box[1]))
    img.paste(layer, (0, 0), mask)

    # faint edge so the icon holds its shape on light backgrounds
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(box, radius, outline=(0, 0, 0, 38), width=2)
    img.alpha_composite(edge)
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
    master.crop((100, 100, 924, 924)).resize((64, 64), Image.LANCZOS).save(HERE.parent / "static" / "favicon.png")
    print("wrote", HERE / "Morph.icns", "and static/favicon.png")


if __name__ == "__main__":
    main()
