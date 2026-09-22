"""Icône de l'application : build/icon.png, icon.icns (macOS), icon.ico (Windows).

    python build/make_icon.py

Un carré arrondi rouge (la couleur d'accent de l'interface), un triangle de
lecture blanc et deux traits de coupe : une timeline qu'on monte. Dessiné en
grand puis réduit, pour des bords nets à toutes les tailles.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
RED = (242, 58, 82, 255)
RED_DARK = (201, 32, 60, 255)


def draw(size: int = 1024) -> Image.Image:
    k = 4                                   # suréchantillonnage
    S = size * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # gabarit macOS : 824 px de carré dans 1024, ombre portée douce
    pad = int(S * 100 / 1024)
    box = (pad, pad, S - pad, S - pad)
    radius = int((box[2] - box[0]) * 0.225)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + S // 64, box[2], box[3] + S // 64), radius,
                                             fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S / 60)))
    # dégradé vertical rouge
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=tuple(int(RED[i] + (RED_DARK[i] - RED[i]) * t) for i in range(4)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    w = box[2] - box[0]
    cx, cy = S / 2, S / 2 - w * 0.04
    # triangle de lecture
    r = w * 0.24
    d.polygon([(cx - r * 0.72, cy - r), (cx - r * 0.72, cy + r), (cx + r * 1.05, cy)], fill=(255, 255, 255, 255))
    # timeline : deux clips séparés par une coupe
    y0, h = box[1] + w * 0.78, w * 0.075
    left, right = box[0] + w * 0.16, box[2] - w * 0.16
    cut = left + (right - left) * 0.58
    gap = w * 0.035
    d.rounded_rectangle((left, y0, cut - gap, y0 + h), h / 2, fill=(255, 255, 255, 235))
    d.rounded_rectangle((cut + gap, y0, right, y0 + h), h / 2, fill=(255, 255, 255, 150))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    big = draw(1024)
    big.save(os.path.join(HERE, "icon.png"))
    big.save(os.path.join(HERE, "icon.icns"))
    big.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                     (128, 128), (256, 256)])
    print("icône : build/icon.png, icon.icns, icon.ico")


if __name__ == "__main__":
    main()
