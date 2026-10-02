"""Regenerate assets/coco.{png,ico,icns,svg} (developer tool; the app itself never needs Pillow).

    uv run --with pillow python scripts/make_icons.py      # macOS: also builds the .icns via iconutil

The mark: coco's green, an open "c" around three voice bars – a meeting being listened to.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "assets"
GREEN_TOP, GREEN_BOTTOM = (38, 128, 106), (24, 88, 73)
S = 4  # supersampling factor


def mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float) -> None:
    """The 'c' + voice bars, drawn in white, centred at (cx, cy); scale 1.0 = 1024-px canvas."""
    ring_r, ring_w = 300 * scale, 92 * scale
    box = [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r]
    draw.arc(box, start=48, end=312, fill="white", width=int(ring_w))
    import math
    for ang in (48, 312):  # round caps on both ends of the arc
        a = math.radians(ang)
        mx = cx + (ring_r - ring_w / 2) * math.cos(a)
        my = cy + (ring_r - ring_w / 2) * math.sin(a)
        r = ring_w / 2
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill="white")
    bar_w = 50 * scale
    for dx, h in ((-80, 150), (0, 260), (80, 150)):
        x = cx - 26 * scale + dx * scale
        draw.rounded_rectangle([x - bar_w / 2, cy - h * scale / 2, x + bar_w / 2, cy + h * scale / 2],
                               radius=bar_w / 2, fill="white")


def gradient(size: int) -> Image.Image:
    g = Image.new("RGB", (1, size))
    for y in range(size):
        k = y / (size - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(GREEN_TOP, GREEN_BOTTOM)))
    return g.resize((size, size))


def tile(size: int, inset: float, radius: float, shadow: bool) -> Image.Image:
    """Rounded-square icon: inset = margin fraction (macOS icons leave ~10% for the shadow)."""
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    m = int(big * inset)
    box = [m, m, big - m, big - m]
    if shadow:
        sh = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + big * 0.012, box[2], box[3] + big * 0.012],
                                             radius=radius * big, fill=(0, 0, 0, 90))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(big * 0.018)))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius * big, fill=255)
    face = gradient(big).convert("RGBA")
    face.putalpha(mask)
    img = Image.alpha_composite(img, face)
    inner = (box[2] - box[0]) / 1024
    mark(ImageDraw.Draw(img), big / 2, big / 2, inner)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    full = tile(1024, 0.0, 0.2237, shadow=False)            # Windows / Linux: full bleed
    full.save(ASSETS / "coco.png")
    full.save(ASSETS / "coco.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as d:
            iconset = Path(d) / "coco.iconset"
            iconset.mkdir()
            mac = tile(1024, 0.0977, 0.2237 * 0.805, shadow=True)  # Big Sur grid: 824-px tile
            for base in (16, 32, 128, 256, 512):
                for k in (1, 2):
                    px = base * k
                    name = f"icon_{base}x{base}{'@2x' if k == 2 else ''}.png"
                    mac.resize((px, px), Image.LANCZOS).save(iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "coco.icns")], check=True)
    # favicon: the same mark as vector
    (ASSETS / "coco.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#26806a"/><stop offset="1" stop-color="#185849"/></linearGradient></defs>'
        '<rect width="1024" height="1024" rx="229" fill="url(#g)"/>'
        '<path d="M 681.96 323.25 A 254 254 0 1 0 681.96 700.75" fill="none" stroke="#fff" stroke-width="92" '
        'stroke-linecap="round"/>'
        '<g fill="#fff"><rect x="381" y="437" width="50" height="150" rx="25"/>'
        '<rect x="461" y="382" width="50" height="260" rx="25"/>'
        '<rect x="541" y="437" width="50" height="150" rx="25"/></g></svg>', encoding="utf-8")
    print("✓", ", ".join(sorted(p.name for p in ASSETS.iterdir())))


if __name__ == "__main__":
    main()
