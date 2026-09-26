r"""Traduit les sous-titres et textes ÉDITÉS dans l'interface en fichier .ass.

Contrat WYSIWYG : l'éditeur envoie des textes entièrement résolus (position,
taille, couleurs, effets, surlignage, animations, émoji) ; ce module ne décide
de rien, il traduit fidèlement en tags libass. Ce qui est affiché dans
l'éditeur (engine/web/studio/textfx.js) est ce qui est incrusté à l'export.

Détails de libass qui expliquent la forme du fichier :
  * BorderStyle (contour vs boîte opaque) n'a pas de tag inline -> deux styles
    sont déclarés, `Out` et `Box`, et tout le reste est surchargé ligne à ligne.
  * En BorderStyle=3, la couleur de contour (\3c) devient la couleur de la
    boîte et \bord sa marge — d'où le champ `outline_col` à double usage.
  * Les effets sont des COUCHES : sous le texte, des copies de la même ligne
    (même position, même mise en page) qui ne diffèrent que par la couleur,
    l'épaisseur du contour, le flou ou un décalage : relief 3D, ombre douce,
    lueur, second contour.
  * Une animation est rendue image par image : pendant qu'elle joue, une ligne
    par image de la vidéo, avec l'état déjà calculé (engine/timeline/
    animations.py, le même calcul que l'aperçu). Les bornes de ces lignes
    tombent à mi-chemin entre deux images : l'arrondi au centième d'ASS ne
    fait jamais tomber une image dans la mauvaise ligne.

Couleurs : \1c/\3c/\4c prennent &HBBGGRR&, l'opacité passe par \1a/\3a/\4a
(&HAA&, 00 = opaque).
"""
from __future__ import annotations

import math
import os
import re

from engine.pipeline.fonts import system_font

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

# Opacité des couches (0 = opaque .. 255 = invisible), comme dans l'aperçu.
SHADOW_ALPHA = 0x60           # ombre : 62 % d'opacité
GLOW_ALPHA = 0x40             # lueur : 75 %
DIM = 0.4                     # opacité des mots estompés (mode "dim")
# Géométrie des effets, partagée avec textfx.js.
GLOW_BORD = 0.25              # la lueur élargit le contour de glow × 0,25…
GLOW_BLUR = 0.5               # …et le floute de glow × 0,5
EXTRUDE_STEP = 2.0            # une copie du relief tous les 2 px
POP = 1.12                    # zoom du mot actif


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _hex(hex_color) -> tuple[int, int, int] | None:
    h = str(hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return None


def _rgb(hex_color) -> str:
    """#RRGGBB -> &HBBGGRR& (ordre libass)."""
    r, g, b = _hex(hex_color) or (255, 255, 255)
    return "&H{:02X}{:02X}{:02X}&".format(b, g, r)


def _alpha(a) -> str:
    """0.0 (opaque) .. 1.0 (invisible) -> &HAA&."""
    return "&H{:02X}&".format(int(round(_clamp(_num(a, 0.0), 0.0, 1.0) * 255)))


def _aa(base: int, opacity: float) -> str:
    """Transparence `base` (0..255) atténuée par une opacité 0..1 -> &HAA&."""
    v = 255 - (255 - base) * _clamp(opacity, 0.0, 1.0)
    return "&H{:02X}&".format(int(math.floor(_clamp(v, 0, 255) + 0.5)))


def _mix(c1, c2, k: float) -> str:
    """Couleur à la fraction `k` entre deux couleurs (dégradé) — même calcul que textfx.js."""
    a, b = _hex(c1) or (255, 255, 255), _hex(c2) or (255, 255, 255)
    r, g, bl = (int(math.floor(x + (y - x) * k + 0.5)) for x, y in zip(a, b))
    return "&H{:02X}{:02X}{:02X}&".format(bl, g, r)


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
    return "{" + tags + "}" if tags else ""


def _g(v: float) -> str:
    return f"{round(v, 2):g}"


def _dialogue(start: float, end: float, style: str, text: str, layer: int = 0) -> str:
    return "Dialogue: {},{},{},{},,0,0,0,,{}".format(layer, _ts(start), _ts(end), style, text)


def _tokens(c: dict) -> list[str]:
    upper = bool(c.get("upper"))
    out = []
    for wd in c.get("words") or []:
        txt = str(wd.get("text", ""))
        out.append(_esc(txt.upper() if upper else txt))
    return out


_UNIT = re.compile(r"\\N|.", re.S)


# ------------------------------------------------------------------ couches

def _layers(c: dict) -> list[dict]:
    """Couches d'un texte, de la plus basse à la plus haute."""
    bord = _clamp(_num(c.get("outline"), 5), 0, 60)
    out: list[dict] = []
    depth = _clamp(_num(c.get("extrude"), 0), 0, 80)
    if depth > 0:
        n = max(1, int(math.ceil(depth / EXTRUDE_STEP)))
        for i in range(n, 0, -1):
            off = depth * i / n
            out.append({"kind": "extrude", "dx": off, "dy": off, "bord": bord, "blur": 0.0,
                        "c1": c.get("extrude_col"), "c3": c.get("extrude_col"), "a1": 0, "a3": 0})
    shad, sblur = _clamp(_num(c.get("shadow"), 0), 0, 60), _clamp(_num(c.get("shadow_blur"), 0), 0, 60)
    if shad > 0 and sblur > 0:
        out.append({"kind": "shadow", "dx": shad, "dy": shad, "bord": bord, "blur": sblur,
                    "c1": c.get("shadow_col"), "c3": c.get("shadow_col"), "a1": SHADOW_ALPHA, "a3": SHADOW_ALPHA})
    glow = _clamp(_num(c.get("glow"), 0), 0, 80)
    if glow > 0:
        out.append({"kind": "glow", "dx": 0.0, "dy": 0.0, "bord": bord + glow * GLOW_BORD, "blur": glow * GLOW_BLUR,
                    "c1": c.get("glow_col"), "c3": c.get("glow_col"), "a1": GLOW_ALPHA, "a3": GLOW_ALPHA})
    o2 = _clamp(_num(c.get("outline2"), 0), 0, 60)
    if o2 > 0:
        out.append({"kind": "outline2", "dx": 0.0, "dy": 0.0, "bord": bord + o2, "blur": 0.0,
                    "c1": c.get("outline2_col"), "c3": c.get("outline2_col"), "a1": 0, "a3": 0})
    out.append({"kind": "main", "dx": 0.0, "dy": 0.0, "bord": bord, "blur": 0.0})
    return out


def _split_for_blur(layers: list[dict], c: dict) -> list[dict]:
    """libass ne floute que le contour d'un texte qui en a un (le remplissage
    reste net) ; le navigateur floute tout. Pendant un flou, le texte principal
    devient donc deux lignes : le contour seul, puis le remplissage sans contour
    (que libass floute alors)."""
    if c.get("box"):
        return layers
    out = []
    for lay in layers:
        if lay["kind"] == "main" and lay["bord"] > 0:
            out.append(dict(lay, part="outline"))
            if not c.get("hollow"):
                out.append(dict(lay, part="fill", bord=0.0))
        else:
            out.append(lay)
    return out


def _layer_tags(c: dict, lay: dict, w: int, h: int, st: dict) -> tuple[str, dict]:
    """Bloc d'override d'une couche, et ses transparences de base (pour les mots)."""
    s, sx, sy = st["s"], st["sx"], st["sy"]
    x = _clamp(_num(c.get("x"), 0.5), -0.5, 1.5) * w + st["dx"] * w + lay["dx"] * s * sx
    y = _clamp(_num(c.get("y"), 0.82), -0.5, 1.5) * h + st["dy"] * h + lay["dy"] * s * sy
    size = int(round(_clamp(_num(c.get("size"), 86), 8, 400)))
    box = bool(c.get("box")) and lay["kind"] == "main"
    main = lay["kind"] == "main"
    font = system_font(str(c.get("font") or "Arial")).replace(_BS, "").replace("}", "")
    animated = (s, sx, sy) != (1.0, 1.0, 1.0)
    tags = [r"\an5", rf"\pos({_g(x)},{_g(y)})", rf"\fn{font}", rf"\fs{size}",
            rf"\b{1 if c.get('bold', True) else 0}"]
    if c.get("italic"):
        tags.append(r"\i1")
    bord = lay["bord"]
    shad = _clamp(_num(c.get("shadow"), 0), 0, 60) if main and not _num(c.get("shadow_blur"), 0) else 0.0
    if lay.get("part") == "fill":
        shad = 0.0
    if animated:
        tags += [rf"\xbord{_g(bord * s * sx)}", rf"\ybord{_g(bord * s * sy)}",
                 rf"\xshad{_g(shad * s * sx)}", rf"\yshad{_g(shad * s * sy)}"]
    else:
        tags += [rf"\bord{bord:g}", rf"\shad{shad:g}"]
    blur = lay["blur"] * (s * math.sqrt(max(0.0, sx * sy)) if animated else 1.0) + st["b"]
    if blur > 0.01:
        tags.append(rf"\blur{_g(blur)}")
    sp = _num(c.get("spacing"), 0)
    if sp:
        tags.append(rf"\fsp{_g(sp * s * sx)}")
    if animated:
        tags += [rf"\fscx{_g(100 * s * sx)}", rf"\fscy{_g(100 * s * sy)}"]
    rot = _num(c.get("rotation"), 0) + st["r"]
    if abs(rot) > 0.001:
        tags.append(rf"\frz{_g(-rot)}")
    op = _clamp(_num(c.get("opacity"), 1.0), 0, 1) * st["o"]
    if main:
        a1 = 255 if c.get("hollow") or lay.get("part") == "outline" else 0
        a3 = int(round(_clamp(_num(c.get("box_alpha"), 0.25), 0, 1) * 255)) if box else 0
        base = {"a1": a1, "a3": a3, "a4": SHADOW_ALPHA, "op": op}
        tags += [rf"\1c{_rgb(c.get('color'))}", rf"\1a{_aa(a1, op)}",
                 rf"\3c{_rgb(c.get('outline_col'))}", rf"\3a{_aa(a3, op)}",
                 rf"\4c{_rgb(c.get('shadow_col') or '#000000')}", rf"\4a{_aa(SHADOW_ALPHA, op)}"]
    else:
        base = {"a1": lay["a1"], "a3": lay["a3"], "a4": 255, "op": op}
        tags += [rf"\1c{_rgb(lay['c1'])}", rf"\1a{_aa(lay['a1'], op)}",
                 rf"\3c{_rgb(lay['c3'])}", rf"\3a{_aa(lay['a3'], op)}", r"\4a&HFF&"]
    return "".join(tags), base


def _alphas(base: dict, f: float) -> str:
    """Transparences d'une couche pour un mot ou une lettre d'opacité relative `f`."""
    op = base["op"] * f
    return rf"\1a{_aa(base['a1'], op)}\3a{_aa(base['a3'], op)}\4a{_aa(base['a4'], op)}"


# ------------------------------------------------------------------- texte

def _word_states(c: dict, n: int, k: int, mode: str) -> list[dict]:
    """Pour chaque mot : surligné, masqué, estompé, agrandi."""
    out = []
    for j in range(n):
        lit = (j == k) if mode in ("word", "reveal", "dim") else (j <= k) if mode == "sweep" else False
        out.append({
            "lit": lit,
            "f": 0.0 if (mode == "reveal" and j > k) else DIM if (mode == "dim" and j != k) else 1.0,
            "grow": bool(c.get("pop")) and j == k and mode in ("word", "reveal", "dim"),
        })
    return out


def _body(c: dict, toks: list[str], lay: dict, base: dict, words: list[dict] | None,
          chars: list[dict] | None, wunits: list[dict] | None) -> str:
    """Texte d'une couche, avec les surcharges par mot et par lettre."""
    main = lay["kind"] == "main"
    mode = str(c.get("mode") or "word")
    fades = mode in ("reveal", "dim") or wunits is not None     # opacité par mot
    col, hl, c2 = c.get("color"), c.get("hl"), c.get("color2") or ""
    gradient = main and bool(c2) and not c.get("hollow")
    per_word = words is not None or wunits is not None
    per_char = chars is not None or gradient
    if not per_word and not per_char:
        return " ".join(toks)
    total = sum(sum(1 for u in _UNIT.findall(t) if u != r"\N") for t in toks)
    parts: list[str] = []
    ci = 0
    for j, tok in enumerate(toks):
        ws = words[j] if words else {"lit": False, "f": 1.0, "grow": False}
        f = ws["f"] * (wunits[j]["o"] if wunits else 1.0)
        wblur = wunits[j]["b"] if wunits else 0.0
        head = []
        if main and words is not None:
            head.append(rf"\1c{_rgb(hl if ws['lit'] else col)}")
        if fades:
            head.append(_alphas(base, f))
        if words is not None and c.get("pop"):
            head.append(rf"\fscx{_g(100 * POP)}\fscy{_g(100 * POP)}" if ws["grow"] else r"\fscx100\fscy100")
        if wunits is not None:
            head.append(rf"\blur{_g(lay['blur'] + wblur)}")
        if not per_char:
            parts.append(_ov("".join(head)) + tok)
            continue
        units = _UNIT.findall(tok)
        pieces = [_ov("".join(head))]
        for u in units:
            if u == r"\N":
                pieces.append(u)
                continue
            t = []
            if gradient and not ws["lit"]:
                t.append(rf"\1c{_mix(col, c2, ci / max(1, total - 1))}")
            if chars is not None:
                t.append(_alphas(base, f * chars[ci]["o"]))
                t.append(rf"\blur{_g(lay['blur'] + wblur + chars[ci]['b'])}")
            pieces.append(_ov("".join(t)) + u)
            ci += 1
        parts.append("".join(pieces))
    return " ".join(parts)


# ----------------------------------------------------------------- lignes

def _frames(a: float, b: float, fps: float) -> list[float]:
    """Bornes des lignes image par image sur [a, b] : à mi-chemin entre deux images."""
    out = [a, b]
    k = math.ceil(a * fps - 0.5 + 1e-9)
    while (k + 0.5) / fps < b:
        out.append((k + 0.5) / fps)
        k += 1
    return out


def _events(c: dict, w: int, h: int, fps: float = 30.0, z: int = 0) -> list[str]:
    from engine.timeline import animations

    words = list(c.get("words") or [])
    toks = _tokens(c)
    if not toks or not any(t.strip() for t in toks):
        return []
    start = _num(c.get("start"), _num(words[0].get("start"), 0.0))
    end = _num(c.get("end"), _num(words[-1].get("end"), start + 1.0))
    if end <= start:
        return []

    mode = str(c.get("mode") or "word")
    clip = dict(c, start=start, dur=end - start)
    animated = animations.has_anim(clip)
    layers = _layers(c)
    base_layer = z * 100

    if mode == "sweep" and not animated:
        return _sweep(c, toks, words, layers, start, end, w, h, base_layer)

    # découpage : changements de mot surligné, et image par image pendant les animations
    bounds = {start, end}
    starts = [start] + [_clamp(_num(words[k].get("start"), start), start, end) for k in range(1, len(words))]
    if mode in ("word", "reveal", "dim", "sweep"):
        bounds.update(starts[1:])
    wins = animations.windows(clip) if animated else []
    for a, b in wins:
        bounds.update(_frames(max(a, start), min(b, end), fps))
    cuts = sorted(t for t in bounds if start - 1e-9 <= t <= end + 1e-9)

    events: list[str] = []
    style_main = "Box" if c.get("box") else "Out"
    n_chars = sum(sum(1 for u in _UNIT.findall(t) if u != r"\N") for t in toks)
    for s, e in zip(cuts, cuts[1:]):
        if e - s < 1e-6:
            continue
        mid = (s + e) / 2
        in_win = any(a - 1e-9 <= mid <= b + 1e-9 for a, b in wins)
        at = math.floor(mid * fps + 0.5) / fps if in_win else mid
        st = animations.state(clip, at) if animated else dict(animations.IDENTITY)
        k = max(0, sum(1 for t0 in starts if t0 <= mid) - 1)
        wstates = _word_states(c, len(toks), k, mode) if mode in ("word", "reveal", "dim", "sweep") else None
        chars = animations.unit_state(st, "char", n_chars) if animated else None
        wunits = animations.unit_state(st, "word", len(toks)) if animated else None
        blurry = st["b"] > 0.01 or any(u["b"] > 0.01 for u in (chars or []) + (wunits or []))
        for i, lay in enumerate(_split_for_blur(layers, c) if blurry else layers):
            tags, base = _layer_tags(c, lay, w, h, st)
            text = _body(c, toks, lay, base, wstates, chars, wunits)
            style = style_main if lay["kind"] == "main" else "Out"
            events.append(_dialogue(s, e, style, _ov(tags) + text, base_layer + i))
    return events


def _sweep(c, toks, words, layers, start, end, w, h, base_layer) -> list[str]:
    """Karaoké \\kf (sans animation) : une seule ligne, le balayage va de \\2c
    (à venir) vers \\1c (déjà dit). Les couches du dessous restent unies."""
    ident = {"o": 1.0, "dx": 0.0, "dy": 0.0, "s": 1.0, "sx": 1.0, "sy": 1.0, "r": 0.0, "b": 0.0}
    out = []
    for i, lay in enumerate(layers):
        tags, base = _layer_tags(c, lay, w, h, ident)
        if lay["kind"] != "main":
            out.append(_dialogue(start, end, "Out", _ov(tags) + " ".join(toks), base_layer + i))
            continue
        pre = tags + rf"\1c{_rgb(c.get('hl'))}" + rf"\2c{_rgb(c.get('color'))}" + r"\2a&H00&"
        parts = []
        for k, tok in enumerate(toks):
            s = _num(words[k].get("start"), start)
            e = (_num(words[k + 1].get("start"), end) if k + 1 < len(words)
                 else _num(words[k].get("end"), end))
            cs = max(1, int(round((e - s) * 100)))
            parts.append(("" if k == 0 else " ") + _ov(rf"\kf{cs}") + tok)
        out.append(_dialogue(start, end, "Box" if c.get("box") else "Out", _ov(pre) + "".join(parts), base_layer + i))
    return out


def build_ass_edited(
    captions: list[dict], out_path: str, width: int = 1080, height: int = 1920, fps: float = 30.0
) -> list[dict]:
    """Écrit le .ass et retourne les émojis à superposer (en pixels de sortie).

    Les émojis ne passent pas par libass (qui les rendrait en noir et blanc) :
    ils sont incrustés en PNG couleur par `emoji_overlay`, à la position
    calculée dans l'éditeur. `fps` : cadence de la vidéo, pour les animations.
    Les textes arrivent dans l'ordre d'empilement (le dernier passe devant).
    """
    events: list[str] = []
    emojis: list[dict] = []

    for z, c in enumerate(captions):
        if c.get("hidden"):
            continue
        events += _events(c, width, height, fps, z)

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
