r"""Presets de sous-titres — source de vérité partagée éditeur <-> rendu.

L'éditeur web affiche ces valeurs en HTML/CSS, `ass_edit` les traduit en tags
libass. Un seul dictionnaire pour les deux : ce qu'on voit dans l'éditeur est
ce qui est incrusté à l'export.

Conventions :
  * `x`/`y` sont normalisés 0..1 et désignent le CENTRE du bloc de texte
    (équivalent ASS `\an5` + `\pos`). Indépendant de la résolution.
  * `size`, `outline`, `shadow` sont en pixels de la frame de SORTIE
    (1080x1920 en vertical).
  * `outline_col` sert de couleur de contour ET de couleur de boîte quand
    `box` est vrai (c'est le comportement de libass avec BorderStyle=3).
  * `mode` : "word" = seul le mot prononcé est surligné (style shorts),
    "sweep" = surlignage progressif type karaoké, "none" = texte statique.
"""
from __future__ import annotations

# Valeurs communes : tout preset part de là et surcharge ce qu'il veut.
BASE: dict = {
    "font": "Arial",
    "size": 86,
    "bold": True,
    "upper": False,
    "color": "#FFFFFF",       # texte au repos
    "hl": "#00FF66",          # mot surligné
    "outline_col": "#000000",
    "outline": 5.0,
    "shadow": 0.0,
    "box": False,
    "box_alpha": 0.25,        # 0 = opaque .. 1 = invisible (utilisé si box)
    "mode": "word",
    "pop": True,              # léger zoom sur le mot actif
    "x": 0.5,
    "y": 0.82,
}

PRESETS: dict[str, dict] = {
    "hype": {
        "label": "Hype", "hint": "vert + boîte (recommandé)",
        "hl": "#00FF66", "box": True, "box_alpha": 0.25, "outline": 5.0,
    },
    "classic": {
        "label": "Classic", "hint": "blanc + jaune",
        "hl": "#FFE500", "outline": 6.0, "shadow": 2.0,
    },
    "punch": {
        "label": "Punch", "hint": "ÉNORME majuscules",
        "size": 98, "upper": True, "hl": "#FFE500", "outline": 11.0, "y": 0.5,
    },
    "neon": {
        "label": "Neon", "hint": "cyan / magenta",
        "size": 88, "hl": "#00FFFF", "outline_col": "#FF00AA", "shadow": 3.0,
    },
    "clean": {
        "label": "Clean", "hint": "sobre / pro",
        "size": 62, "hl": "#FFFFFF", "outline": 3.0, "shadow": 1.0, "y": 0.85,
        "mode": "sweep", "pop": False,
    },
}

# Champs qui décrivent l'apparence (le reste — label/hint — est pour l'UI).
LOOK_FIELDS: tuple[str, ...] = tuple(BASE)


def preset(name: str) -> dict:
    """Preset complet (BASE + surcharges), sans les champs d'UI."""
    p = {**BASE, **PRESETS.get(name, {})}
    return {k: p[k] for k in LOOK_FIELDS}


def catalog() -> list[dict]:
    """Liste envoyée au front : nom, libellé, description + apparence."""
    return [
        {"name": name, "label": p.get("label", name), "hint": p.get("hint", ""),
         **preset(name)}
        for name, p in PRESETS.items()
    ]
