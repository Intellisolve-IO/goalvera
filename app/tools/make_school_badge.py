#!/usr/bin/env python3
"""Draw the Everyday Heroes Academy badge (Oct 10 2026): a plain shield with a star and "EH".
Original artwork made here from basic shapes; no character or studio imagery.

    python3 app/tools/make_school_badge.py   # writes app/backend-pipeline/review_api/assets/everyday-heroes-badge.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "backend-pipeline/review_api/assets/everyday-heroes-badge.png"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
NAVY, GOLD, WHITE = (27, 58, 107, 255), (242, 182, 50, 255), (255, 255, 255, 255)


def main():
    s = 4                                    # draw large, scale down for smooth edges
    W = H = 256 * s
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def shield(inset):
        x0, x1, y0 = 28 * s + inset, W - 28 * s - inset, 16 * s + inset
        mid, yb, ytip = W // 2, 150 * s, H - 14 * s - inset
        pts = [(x0, y0 + 18 * s), (mid, y0), (x1, y0 + 18 * s), (x1, yb)]
        for i in range(1, 21):                # curved lower edges to the tip
            t = i / 20
            pts.append((x1 + (mid - x1) * t, yb + (ytip - yb) * (t ** 1.6)))
        for i in range(1, 20):
            t = i / 20
            pts.append((mid + (x0 - mid) * t, ytip + (yb - ytip) * (1 - (1 - t) ** 1.6)))
        pts.append((x0, yb))
        return pts

    d.polygon(shield(0), fill=GOLD)
    d.polygon(shield(10 * s), fill=NAVY)
    # five-point star
    import math
    cx, cy, ro, ri = W // 2, 74 * s, 34 * s, 14 * s
    star = [(cx + (ro if i % 2 == 0 else ri) * math.sin(i * math.pi / 5),
             cy - (ro if i % 2 == 0 else ri) * math.cos(i * math.pi / 5)) for i in range(10)]
    d.polygon(star, fill=GOLD)
    f = ImageFont.truetype(FONT, 70 * s)
    box = d.textbbox((0, 0), "EH", font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((W - tw) / 2 - box[0], 128 * s - box[1]), "EH", font=f, fill=WHITE)
    im = im.resize((256, 256), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT, format="PNG", optimize=True)
    print(OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
