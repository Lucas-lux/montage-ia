"""Polices des sous-titres d'un système à l'autre.

Les styles sont pensés avec les polices livrées par Windows (Segoe UI,
Bahnschrift, Consolas…). Sur un Mac, une partie n'existe pas : sans rien
faire, libass prendrait une police quelconque à l'export et le navigateur une
police à empattements dans l'aperçu. On les remplace par l'équivalent le plus
proche, préinstallé sur macOS — le même tableau sert à l'aperçu
(`engine/web/studio/fonts.js`).

`fontconfig_file()` écrit la configuration dont a besoin le ffmpeg statique
embarqué dans l'application Mac : sans elle, fontconfig ne sait pas où sont
les polices du système et les sous-titres sortent vides.
"""
from __future__ import annotations

import os
import sys

# Police Windows -> police préinstallée sur macOS (les autres existent des deux côtés).
MAC_FONTS = {
    "Segoe UI": "Helvetica Neue",
    "Segoe UI Black": "Helvetica Neue",
    "Segoe UI Emoji": "Apple Color Emoji",
    "Bahnschrift": "DIN Condensed",
    "Calibri": "Helvetica Neue",
    "Candara": "Optima",
    "Corbel": "Gill Sans",
    "Cambria": "Georgia",
    "Franklin Gothic Medium": "Avenir Next Condensed",
    "Consolas": "Menlo",
    "Segoe Script": "Snell Roundhand",
    "Ink Free": "Chalkboard SE",
}

MAC_FONT_DIRS = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
                 "~/Library/Fonts"]


def system_font(name: str, platform: str | None = None) -> str:
    """Nom de police à donner au moteur de rendu sur ce système."""
    if (platform or sys.platform) == "darwin":
        return MAC_FONTS.get(name, name)
    return name


def fontconfig_file(folder: str) -> str | None:
    """Écrit `fonts.conf` (polices du système, cache dans `folder`) et renvoie
    son chemin ; None hors macOS."""
    if sys.platform != "darwin":
        return None
    os.makedirs(folder, exist_ok=True)
    cache = os.path.join(folder, "fontconfig-cache")
    dirs = "\n".join(f"  <dir>{os.path.expanduser(d)}</dir>" for d in MAC_FONT_DIRS)
    conf = f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<!-- Écrit par Montage IA : polices de macOS pour le ffmpeg embarqué. -->
<fontconfig>
{dirs}
  <cachedir>{cache}</cachedir>
  <alias><family>sans-serif</family><prefer><family>Helvetica Neue</family></prefer></alias>
  <alias><family>serif</family><prefer><family>Georgia</family></prefer></alias>
  <alias><family>monospace</family><prefer><family>Menlo</family></prefer></alias>
</fontconfig>
"""
    path = os.path.join(folder, "fonts.conf")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == conf:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(conf)
    return path
