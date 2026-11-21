from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def render_icon(size: int = 512) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2

    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    pad_bg = int(size * 0.08)
    bd.ellipse((pad_bg, pad_bg, size - pad_bg, size - pad_bg), fill=(10, 18, 30, 235))
    canvas = Image.alpha_composite(canvas, bg)

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    r_outer = size * 0.34
    box = (cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer)
    gd.ellipse(box, outline=(56, 189, 248, 220), width=max(4, int(size * 0.065)))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.045))
    canvas = Image.alpha_composite(canvas, glow)

    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    points = []
    steps = 128
    for i in range(steps + 1):
        theta = 2 * math.pi * i / steps
        wave = 0.55 * math.sin(2 * theta) + 0.30 * math.sin(3 * theta + 1.4) + 0.15 * math.sin(5 * theta + 2.8)
        r = r_outer + size * 0.018 * wave
        points.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    rd.line(points, fill=(56, 189, 248, 255), width=max(3, int(size * 0.048)), joint="curve")
    rd.line(points, fill=(186, 230, 253, 220), width=max(1, int(size * 0.018)), joint="curve")
    canvas = Image.alpha_composite(canvas, ring)
    return canvas


def main() -> None:
    out = Path(__file__).resolve().parent / "jarvis.ico"
    img = render_icon(512)
    img.save(out, format="ICO", sizes=SIZES)
    print(f"[OK] Icono generado: {out}")


if __name__ == "__main__":
    main()
