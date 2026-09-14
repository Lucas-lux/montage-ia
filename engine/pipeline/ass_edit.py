r"""Traduit les sous-titres ÉDITÉS dans l'interface en fichier .ass.

Contrat WYSIWYG : l'éditeur envoie des sous-titres entièrement résolus
(position, taille, couleurs, mode de surlignage, émoji) ; ce module ne décide
de rien, il traduit fidèlement en tags libass. Ce qui est affiché dans
l'éditeur est donc ce qui est incrusté à l'export.

Deux détails de libass expliquent la forme du fichier :
  * BorderStyle (contour vs boîte opaque) n'a pas de tag inline -> deux styles
    sont déclarés, `Out` et `Box`, et tout le reste est surchargé ligne à ligne.
  * En BorderStyle=3, la couleur de contour (\3c) devient la couleur de la
    boîte et \bord sa marge — d'où le champ `outline_col` à double usage.

Couleurs : \1c/\3c prennent &HBBGGRR&, l'opacité passe par \1a/\3a (&HAA&,
00 = opaque).
"""
from __future__ import annotations

import os

# Antislash : caractère réservé d'ASS. On le neutralise dans les textes saisis.
_BS = chr(92)

# WrapStyle 1 (repli en fin de ligne) et pas 0 : le mode 0 rééquilibre les
# lignes, ce que le navigateur ne sait pas reproduire — l'aperçu de l'éditeur
# et l'incrustation finale se replieraient alors à des endroits différents.
_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Out,Arial,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,5,0,0,0,1
Style: Box,Arial,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,4,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rgb(hex_color) -> str:
    """#RRGGBB -> &HBBGGRR& (ordre libass)."""
    h = str(hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        r = g = b = 255
    return "&H{:02X}{:02X}{:02X}&".format(b, g, r)


def _alpha(a) -> str:
    """0.0 (opaque) .. 1.0 (invisible) -> &HAA&."""
    return "&H{:02X}&".format(int(round(_clamp(_num(a, 0.0), 0.0, 1.0) * 255)))


def _ts(t: float) -> str:
    # Arrondi AVANT de découper : 59,999 s doit donner 0:01:00.00, pas 0:00:60.00.
    cs = int(round(max(0.0, float(t)) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return "{}:{:02d}:{:02d}.{:02d}".format(h, m, s, cs)


def _esc(text) -> str:
    """Neutralise les caractères réservés et convertit les retours à la ligne."""
    s = str(text).replace(_BS, "/").replace("{", "(").replace("}", ")")
    lines = s.splitlines()
    return r"\N".join(lines) if lines else ""


def _ov(tags: str) -> str:
    """Emballe des tags dans un bloc d'override ASS."""
    return "{" + tags + "}"


def _tags(c: dict, w: int, h: int) -> str:
    """Bloc d'override commun à toutes les lignes d'un sous-titre."""
    x = int(round(_clamp(_num(c.get("x"), 0.5), -0.5, 1.5) * w))
    y = int(round(_clamp(_num(c.get("y"), 0.82), -0.5, 1.5) * h))
    size = int(round(_clamp(_num(c.get("size"), 86), 8, 400)))
    bord = _clamp(_num(c.get("outline"), 5), 0, 60)
    shad = _clamp(_num(c.get("shadow"), 0), 0, 60)
    box = bool(c.get("box"))
    font = str(c.get("font") or "Arial").replace(_BS, "").replace("}", "")
    return "".join([
        r"\an5",
        rf"\pos({x},{y})",
        rf"\fn{font}",
        rf"\fs{size}",
        rf"\b{1 if c.get('bold', True) else 0}",
        rf"\bord{bord:g}",
        rf"\shad{shad:g}",
        rf"\1c{_rgb(c.get('color'))}", r"\1a&H00&",
        rf"\3c{_rgb(c.get('outline_col'))}",
        rf"\3a{_alpha(c.get('box_alpha') if box else 0.0)}",
        r"\4c&H000000&", r"\4a&H60&",
    ])


def _dialogue(start: float, end: float, style: str, text: str) -> str:
    return "Dialogue: 0,{},{},{},,0,0,0,,{}".format(_ts(start), _ts(end), style, text)


def _tokens(c: dict) -> list[str]:
    upper = bool(c.get("upper"))
    out = []
    for wd in c.get("words") or []:
        txt = str(wd.get("text", ""))
        out.append(_esc(txt.upper() if upper else txt))
    return out


def _events(c: dict, w: int, h: int) -> list[str]:
    words = list(c.get("words") or [])
    toks = _tokens(c)
    if not toks or not any(t.strip() for t in toks):
        return []

    start = _num(c.get("start"), _num(words[0].get("start"), 0.0))
    end = _num(c.get("end"), _num(words[-1].get("end"), start + 1.0))
    if end <= start:
        return []

    style = "Box" if c.get("box") else "Out"
    base = _tags(c, w, h)
    hl, col = _rgb(c.get("hl")), _rgb(c.get("color"))
    mode = str(c.get("mode") or "word")

    if mode == "word":
        # Une ligne par mot : seul le mot prononcé porte la couleur de surlignage.
        pop_on = r"\fscx112\fscy112" if c.get("pop") else ""
        pop_off = r"\fscx100\fscy100" if c.get("pop") else ""
        events: list[str] = []
        for k in range(len(toks)):
            s = start if k == 0 else _num(words[k].get("start"), start)
            e = _num(words[k + 1].get("start"), end) if k + 1 < len(words) else end
            if e <= s:
                continue
            parts = [
                (_ov(rf"\1c{hl}" + pop_on) + tok + _ov(rf"\1c{col}" + pop_off))
                if j == k else tok
                for j, tok in enumerate(toks)
            ]
            events.append(_dialogue(s, e, style, _ov(base) + " ".join(parts)))
        return events

    if mode == "sweep":
        # Karaoké \kf : le balayage va de \2c (à venir) vers \1c (déjà dit).
        pre = base + rf"\1c{hl}" + rf"\2c{col}" + r"\2a&H00&"
        parts = []
        for k, tok in enumerate(toks):
            s = _num(words[k].get("start"), start)
            e = (_num(words[k + 1].get("start"), end) if k + 1 < len(words)
                 else _num(words[k].get("end"), end))
            cs = max(1, int(round((e - s) * 100)))
            parts.append(("" if k == 0 else " ") + _ov(rf"\kf{cs}") + tok)
        return [_dialogue(start, end, style, _ov(pre) + "".join(parts))]

    return [_dialogue(start, end, style, _ov(base) + " ".join(toks))]


def build_ass_edited(
    captions: list[dict], out_path: str, width: int = 1080, height: int = 1920
) -> list[dict]:
    """Écrit le .ass et retourne les émojis à superposer (en pixels de sortie).

    Les émojis ne passent pas par libass (qui les rendrait en noir et blanc) :
    ils sont incrustés en PNG couleur par `emoji_overlay`, à la position
    calculée dans l'éditeur.
    """
    events: list[str] = []
    emojis: list[dict] = []

    for c in captions:
        if c.get("hidden"):
            continue
        events += _events(c, width, height)

        char = str(c.get("emoji") or "").strip()
        if not char:
            continue
        size = _num(c.get("size"), 86)
        _, default_dy = emoji_geometry(size)
        emojis.append({
            "char": char,
            "x": int(round(_num(c.get("x"), 0.5) * width + _num(c.get("emoji_dx"), 0.0))),
            "y": int(round(_num(c.get("y"), 0.82) * height
                           + _num(c.get("emoji_dy"), default_dy))),
            "size": int(round(_clamp(_num(c.get("emoji_size"), size * 1.55), 16, 600))),
            "start": _num(c.get("start"), 0.0),
            "end": _num(c.get("end"), 0.0),
        })

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(_HEADER.format(w=int(width), h=int(height)))
        f.write("\n".join(events) + "\n")
    return [e for e in emojis if e["end"] > e["start"]]


def emoji_geometry(size: float) -> tuple[int, float]:
    """Taille et décalage vertical par défaut de l'émoji, au-dessus du texte.

    Le front reçoit ces valeurs dans chaque sous-titre et applique la même
    géométrie : l'aperçu et l'export coïncident.
    """
    esz = int(round(size * 1.55))
    return esz, -(size * 0.75 + esz * 0.5 + 14)
