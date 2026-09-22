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

# Groupes affichés par l'interface, dans cet ordre.
GROUPS: dict[str, str] = {
    "tendance": "Tendance", "fond": "Sur fond", "sobre": "Sobre", "couleur": "Couleur", "fun": "Fun",
}

# Polices : présentes sur tout Windows 10/11 ; ailleurs, libass en choisit une
# proche. `featured` : proposés sur l'écran d'import du mode short.
PRESETS: dict[str, dict] = {
    # ---- tendance
    "hype": {
        "label": "Hype", "hint": "vert + boîte (recommandé)", "group": "tendance", "featured": True,
        "hl": "#00FF66", "box": True, "box_alpha": 0.25, "outline": 5.0,
    },
    "impact": {
        "label": "Impact", "hint": "majuscules, jaune", "group": "tendance", "featured": True,
        "font": "Impact", "size": 96, "upper": True, "hl": "#FFE500", "outline": 8.0,
    },
    "box": {
        "label": "Boîte", "hint": "texte sur fond noir", "group": "tendance", "featured": True,
        "font": "Segoe UI Black", "size": 80, "box": True, "box_alpha": 0.2, "hl": "#FFE500",
        "outline": 9.0, "pop": False,
    },
    "pop": {
        "label": "Pop", "hint": "rose, dynamique", "group": "tendance",
        "font": "Arial Black", "size": 84, "upper": True, "hl": "#FF3B81", "outline_col": "#2B0A18",
        "outline": 7.0,
    },
    "duo": {
        "label": "Duo", "hint": "cyan, condensé", "group": "tendance",
        "font": "Bahnschrift", "size": 92, "upper": True, "hl": "#00E5FF", "outline_col": "#0B1B2B",
        "outline": 6.0, "shadow": 2.0,
    },
    "gras": {
        "label": "Gras", "hint": "épais, jaune", "group": "tendance",
        "font": "Segoe UI Black", "size": 88, "hl": "#FFE500", "outline": 8.0,
    },
    # ---- sur fond
    "ruban": {
        "label": "Ruban", "hint": "bandeau rouge", "group": "fond",
        "size": 74, "upper": True, "box": True, "box_alpha": 0.0, "outline_col": "#F23A52",
        "hl": "#FFE500", "outline": 12.0, "pop": False,
    },
    "journal": {
        "label": "Journal", "hint": "fond blanc, texte sombre", "group": "fond",
        "font": "Segoe UI", "size": 68, "box": True, "box_alpha": 0.05, "outline_col": "#FFFFFF",
        "color": "#17181C", "hl": "#D82C43", "outline": 12.0, "pop": False,
    },
    "machine": {
        "label": "Machine", "hint": "terminal, balayage", "group": "fond",
        "font": "Consolas", "size": 70, "box": True, "box_alpha": 0.1, "outline_col": "#0B0F0B",
        "color": "#D8FFE0", "hl": "#00FF66", "outline": 10.0, "mode": "sweep", "pop": False,
    },
    "ardoise": {
        "label": "Ardoise", "hint": "fond bleu nuit", "group": "fond",
        "font": "Corbel", "size": 76, "box": True, "box_alpha": 0.15, "outline_col": "#1E2A3A",
        "color": "#F5F7FA", "hl": "#FFC93C", "outline": 11.0,
    },
    "contraste": {
        "label": "Contraste", "hint": "sombre, contour blanc", "group": "fond",
        "font": "Arial Black", "size": 84, "color": "#17181C", "hl": "#F23A52", "outline_col": "#FFFFFF",
        "outline": 7.0,
    },
    # ---- sobre
    "clean": {
        "label": "Clean", "hint": "sobre / pro", "group": "sobre", "featured": True,
        "size": 62, "hl": "#FFFFFF", "outline": 3.0, "shadow": 1.0, "y": 0.85,
        "mode": "sweep", "pop": False,
    },
    "minimal": {
        "label": "Minimal", "hint": "petit, sans surlignage", "group": "sobre",
        "font": "Segoe UI", "size": 56, "hl": "#FFFFFF", "outline": 3.0, "shadow": 1.0, "y": 0.86,
        "mode": "none", "pop": False,
    },
    "elegant": {
        "label": "Élégant", "hint": "serif, balayage", "group": "sobre",
        "font": "Georgia", "size": 72, "bold": False, "hl": "#F5D0A9", "outline_col": "#1A1410",
        "outline": 2.0, "shadow": 2.0, "mode": "sweep", "pop": False, "y": 0.84,
    },
    "ombre": {
        "label": "Ombre", "hint": "sans contour, ombre portée", "group": "sobre",
        "font": "Trebuchet MS", "size": 84, "hl": "#FFFFFF", "outline": 0.0, "shadow": 6.0,
    },
    "haut": {
        "label": "Haut", "hint": "en haut de l'image", "group": "sobre",
        "size": 78, "box": True, "box_alpha": 0.3, "hl": "#00FF66", "y": 0.14,
    },
    # ---- couleur
    "classic": {
        "label": "Classic", "hint": "blanc + jaune", "group": "couleur", "featured": True,
        "hl": "#FFE500", "outline": 6.0, "shadow": 2.0,
    },
    "punch": {
        "label": "Punch", "hint": "ÉNORME majuscules", "group": "couleur", "featured": True,
        "size": 98, "upper": True, "hl": "#FFE500", "outline": 11.0, "y": 0.5,
    },
    "neon": {
        "label": "Neon", "hint": "cyan / magenta", "group": "couleur", "featured": True,
        "size": 88, "hl": "#00FFFF", "outline_col": "#FF00AA", "shadow": 3.0,
    },
    "karaoke": {
        "label": "Karaoké", "hint": "balayage jaune", "group": "couleur", "featured": True,
        "size": 82, "hl": "#FFE500", "mode": "sweep", "pop": False,
    },
    "fluo": {
        "label": "Fluo", "hint": "vert fluo", "group": "couleur",
        "font": "Arial Black", "size": 86, "upper": True, "color": "#C6FF00", "hl": "#FFFFFF",
        "outline_col": "#1B2600", "outline": 6.0,
    },
    "or": {
        "label": "Or", "hint": "doré", "group": "couleur",
        "font": "Cambria", "size": 84, "color": "#FFD84D", "hl": "#FFFFFF", "outline_col": "#3A2800",
        "outline": 5.0, "shadow": 3.0,
    },
    "glace": {
        "label": "Glace", "hint": "bleu glacé", "group": "couleur",
        "font": "Bahnschrift", "size": 88, "hl": "#7FD8FF", "outline_col": "#0B3D91", "outline": 6.0,
    },
    "feu": {
        "label": "Feu", "hint": "orange et rouge", "group": "couleur",
        "font": "Impact", "size": 92, "upper": True, "color": "#FFE08A", "hl": "#FF4D1A",
        "outline_col": "#3A0A00", "outline": 7.0, "shadow": 2.0,
    },
    # ---- fun
    "retro": {
        "label": "Rétro", "hint": "chaud, années 70", "group": "fun",
        "font": "Georgia", "size": 84, "color": "#FFF3D6", "hl": "#FF9F1C", "outline_col": "#4A1F00",
        "outline": 6.0, "shadow": 4.0,
    },
    "bulle": {
        "label": "Bulle", "hint": "bande dessinée", "group": "fun",
        "font": "Comic Sans MS", "size": 80, "hl": "#FF6BD6", "outline_col": "#3B0F33", "outline": 7.0,
    },
    "manuscrit": {
        "label": "Manuscrit", "hint": "écrit à la main", "group": "fun",
        "font": "Ink Free", "size": 96, "hl": "#FFE500", "outline": 5.0, "shadow": 2.0,
    },
    "script": {
        "label": "Cursive", "hint": "calligraphie", "group": "fun",
        "font": "Segoe Script", "size": 80, "hl": "#F5D0A9", "outline": 4.0, "shadow": 3.0,
        "mode": "sweep", "pop": False,
    },
}

# Champs qui décrivent l'apparence (le reste — label/hint/group — est pour l'UI).
LOOK_FIELDS: tuple[str, ...] = tuple(BASE)


def preset(name: str) -> dict:
    """Preset complet (BASE + surcharges), sans les champs d'UI."""
    p = {**BASE, **PRESETS.get(name, {})}
    return {k: p[k] for k in LOOK_FIELDS}


def catalog() -> list[dict]:
    """Liste envoyée au front : nom, libellé, description + apparence."""
    return [
        {"name": name, "label": p.get("label", name), "hint": p.get("hint", ""),
         "group": p.get("group", "tendance"), "featured": bool(p.get("featured")),
         **preset(name)}
        for name, p in PRESETS.items()
    ]


def groups() -> list[dict]:
    return [{"name": k, "label": v} for k, v in GROUPS.items()]
