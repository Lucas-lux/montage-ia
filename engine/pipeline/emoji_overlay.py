"""Rendu des émojis en images PNG couleur (Pillow + police système).

libass rend les émojis en noir et blanc -> on les incruste en images. Les PNG
sont générés localement depuis la police émoji couleur du système, sans réseau,
et mis en cache sur disque (clé = émoji + taille demandée).

La superposition elle-même est faite par `render.burn_and_overlay`, dans la
même passe ffmpeg que les sous-titres.
"""
from __future__ import annotations

import os
import tempfile

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguiemj.ttf",                          # Windows : Segoe UI Emoji
    "/System/Library/Fonts/Apple Color Emoji.ttc",             # macOS
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",       # Debian/Ubuntu : fonts-noto-color-emoji
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",                # Arch
    "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",   # Fedora
]

# Les polices émoji bitmap (Noto, Apple) n'existent qu'à quelques tailles fixes
# et refusent les autres : on charge une taille disponible puis on redimensionne.
_BITMAP_SIZES = (109, 160, 96, 64, 48)

CACHE_DIR = os.path.join(tempfile.gettempdir(), "montage_ia_emoji")


def _font(px: int):
    """Police émoji couleur, et la taille réellement chargée (peut différer de `px`)."""
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if not os.path.isfile(p):
            continue
        for size in (px, *_BITMAP_SIZES):
            try:
                return ImageFont.truetype(p, size), size
            except OSError:
                continue
    raise RuntimeError("Police émoji couleur introuvable (Segoe UI Emoji, Apple Color Emoji "
                       "ou Noto Color Emoji).")


def render_emoji_png(emoji: str, px: int = 137) -> str:
    """Rend `emoji` en PNG transparent (couleur). Mémoïsé sur disque.

    La taille fait partie de la clé de cache : un émoji agrandi dans l'éditeur
    est re-rendu net au lieu d'être un upscale flou. On arrondit par paliers de
    16 px pour ne pas remplir le cache d'une variante par pixel.
    """
    from PIL import Image, ImageDraw

    px = max(16, min(600, int(round(px / 16)) * 16))
    os.makedirs(CACHE_DIR, exist_ok=True)
    code = "-".join(f"{ord(c):x}" for c in emoji)
    path = os.path.join(CACHE_DIR, f"{code}_{px}.png")
    if os.path.isfile(path):
        return path

    font, native = _font(px)
    probe = ImageDraw.Draw(Image.new("RGBA", (native * 2, native * 2)))
    bbox = probe.textbbox((0, 0), emoji, font=font, embedded_color=True)
    w, h = max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-bbox[0], -bbox[1]), emoji, font=font, embedded_color=True)
    if native != px:
        ratio = px / native
        img = img.resize((max(1, round(w * ratio)), max(1, round(h * ratio))), Image.LANCZOS)
    img.save(path)
    return path
