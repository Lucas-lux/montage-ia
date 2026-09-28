"""Animations d'entrée, de sortie et en boucle des clips (textes, vidéos, images).

Les définitions sont des données (`engine/web/studio/animations.json`) : des
images clés sur une progression 0..1 et une courbe d'accélération par segment.
L'aperçu les évalue en JavaScript (`studio/anim.js`), l'export ici, avec les
mêmes formules : un test vérifie que les deux donnent les mêmes valeurs.

Un clip porte au plus trois animations :
    anim_in   {"type": "pop", "dur": 0.4}        au début du clip
    anim_out  {"type": "fade_out", "dur": 0.4}   à la fin
    anim_loop {"type": "pulse", "speed": 1}      pendant tout le clip

Propriétés : o opacité, dx/dy décalage en fraction du cadre, s échelle, sx/sy
étirement, r rotation en degrés (sens horaire), b flou en pixels de sortie.
Combinaison : opacités et échelles se multiplient, le reste s'additionne.
Une animation « lettre à lettre » (`per`) donne en plus, pour chaque lettre ou
mot, une opacité et un flou.
"""
from __future__ import annotations

import functools
import json
import math
import os

DEFS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "web", "studio", "animations.json")

IDENTITY = {"o": 1.0, "dx": 0.0, "dy": 0.0, "s": 1.0, "sx": 1.0, "sy": 1.0, "r": 0.0, "b": 0.0}
_MULT = ("o", "s", "sx", "sy")
UNIT_PROPS = ("o", "b")
MIN_DUR, MAX_DUR = 0.1, 5.0
MIN_SPEED, MAX_SPEED = 0.25, 4.0


@functools.lru_cache(maxsize=1)
def definitions() -> dict:
    with open(DEFS_PATH, encoding="utf-8") as f:
        return json.load(f)["anims"]


def catalog() -> dict:
    with open(DEFS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ courbes

_C1 = 1.70158
_C3 = _C1 + 1
_C4 = 2 * math.pi / 3


def _out_bounce(u: float) -> float:
    n, d = 7.5625, 2.75
    if u < 1 / d:
        return n * u * u
    if u < 2 / d:
        u -= 1.5 / d
        return n * u * u + 0.75
    if u < 2.5 / d:
        u -= 2.25 / d
        return n * u * u + 0.9375
    u -= 2.625 / d
    return n * u * u + 0.984375


EASE = {
    "linear": lambda u: u,
    "inQuad": lambda u: u * u,
    "outQuad": lambda u: 1 - (1 - u) * (1 - u),
    "inOutQuad": lambda u: 2 * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 2 / 2,
    "inCubic": lambda u: u ** 3,
    "outCubic": lambda u: 1 - (1 - u) ** 3,
    "inOutCubic": lambda u: 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2,
    "inBack": lambda u: _C3 * u ** 3 - _C1 * u * u,
    "outBack": lambda u: 1 + _C3 * (u - 1) ** 3 + _C1 * (u - 1) ** 2,
    "outElastic": lambda u: u if u in (0, 1) else 2 ** (-10 * u) * math.sin((u * 10 - 0.75) * _C4) + 1,
    "outBounce": _out_bounce,
    "inOutSine": lambda u: -(math.cos(math.pi * u) - 1) / 2,
}


def ease(name: str, u: float) -> float:
    return EASE.get(name, EASE["linear"])(min(1.0, max(0.0, u)))


# ---------------------------------------------------------- images clés

def sample(kf: list, p: float, default_ease: str = "linear") -> dict:
    """Valeurs des propriétés à la progression `p` (0..1)."""
    if not kf:
        return dict(IDENTITY)
    p = min(1.0, max(0.0, p))
    if p <= kf[0][0]:
        return {**IDENTITY, **kf[0][1]}
    if p >= kf[-1][0]:
        return {**IDENTITY, **kf[-1][1]}
    i = next(i for i in range(len(kf) - 1) if kf[i][0] <= p < kf[i + 1][0])
    a, b = kf[i], kf[i + 1]
    span = b[0] - a[0]
    u = (p - a[0]) / span if span > 0 else 1.0
    e = ease(a[2] if len(a) > 2 else default_ease, u)
    va, vb = {**IDENTITY, **a[1]}, {**IDENTITY, **b[1]}
    return {k: va[k] + (vb[k] - va[k]) * e for k in IDENTITY}


def combine(acc: dict, v: dict) -> dict:
    for k in IDENTITY:
        if k in _MULT:
            acc[k] *= v[k]
        else:
            acc[k] += v[k]
    return acc


def unit_values(d: dict, p: float, n: int) -> list[dict]:
    """Opacité et flou de chaque lettre (ou mot) d'une animation `per`."""
    stagger = min(1.0, max(0.0, float(d.get("stagger", 0.5))))
    out = []
    loop = d["kind"] == "loop"
    w = 1.0 - stagger
    for i in range(n):
        j = n - 1 - i if d.get("reverse") else i
        if loop:
            u = (p + stagger * j / max(1, n)) % 1.0
        elif w > 1e-9:
            u = (p - stagger * j / max(1, n)) / w
        else:
            u = 1.0 if p >= stagger * j / max(1, n) - 1e-9 else 0.0
        v = sample(d["ukf"], u, d.get("ease", "linear"))
        out.append({k: v[k] for k in UNIT_PROPS})
    return out


# ------------------------------------------------------------ d'un clip

KIND_OF = {"anim_in": "in", "anim_out": "out", "anim_loop": "loop"}
PROPS = {"o": (0.0, 1.0), "dx": (-3.0, 3.0), "dy": (-3.0, 3.0), "s": (0.0, 20.0), "sx": (0.0, 20.0),
         "sy": (0.0, 20.0), "r": (-3600.0, 3600.0), "b": (0.0, 80.0)}


def custom_def(a: dict, key: str) -> dict:
    """Définition d'une animation « sur mesure » : ses images clés sont dans le clip."""
    d = {"kind": KIND_OF[key], "label": "Sur mesure", "dur": a.get("dur", 0.5), "ease": a.get("ease", "linear"),
         "kf": a["kf"]}
    if key == "anim_loop":
        if a.get("span"):
            d["span"] = True
        else:
            d["period"] = a.get("period", 1.0)
    return d


def _get(c: dict, key: str) -> tuple[dict, dict] | None:
    a = c.get(key)
    if not isinstance(a, dict):
        return None
    if a.get("type") == "custom" and isinstance(a.get("kf"), list):
        return custom_def(a, key), a
    d = definitions().get(a.get("type") or "")
    return (d, a) if d else None


def timing(c: dict) -> tuple[float, float]:
    """Durées d'entrée et de sortie, réduites ensemble si le clip est trop court."""
    L = float(c.get("dur") or 0.0)
    ai, ao = _get(c, "anim_in"), _get(c, "anim_out")
    di = float(ai[1].get("dur") or ai[0].get("dur", 0.5)) if ai else 0.0
    do = float(ao[1].get("dur") or ao[0].get("dur", 0.5)) if ao else 0.0
    if di + do > L > 0:
        k = L / (di + do)
        di, do = di * k, do * k
    return di, do


def has_anim(c: dict) -> bool:
    return any(_get(c, k) for k in ("anim_in", "anim_out", "anim_loop"))


def state(c: dict, t: float) -> dict:
    """État animé du clip à l'instant `t` (timeline) : propriétés combinées et,
    s'il y en a, `units` = [(définition, progression)] des animations lettre à lettre."""
    st = dict(IDENTITY)
    units = []
    start, L = float(c["start"]), float(c.get("dur") or 0.0)
    di, do = timing(c)
    for key, active, p in (
        ("anim_in", di > 0 and t < start + di, (t - start) / di if di > 0 else 1.0),
        ("anim_out", do > 0 and t > start + L - do, (t - (start + L - do)) / do if do > 0 else 0.0),
    ):
        got = _get(c, key)
        if not got:
            continue
        d = got[0]
        p = min(1.0, max(0.0, p))
        if "per" in d:
            # lettre à lettre : l'état de départ (ou d'arrivée) dure tant que l'animation n'a pas commencé
            units.append((d, p))
        elif active:
            combine(st, sample(d["kf"], p, d.get("ease", "linear")))
    got = _get(c, "anim_loop")
    if got:
        d, a = got
        if d.get("span"):
            p = (t - start) / L if L > 0 else 0.0
        else:
            speed = min(MAX_SPEED, max(MIN_SPEED, float(a.get("speed") or 1.0)))
            p = ((t - start) * speed / float(d.get("period") or 1.0)) % 1.0
        if "per" in d:
            units.append((d, p))
        else:
            combine(st, sample(d["kf"], p, d.get("ease", "linear")))
    if units:
        st["units"] = units
    return st


def windows(c: dict) -> list[tuple[float, float]]:
    """Plages (timeline) où l'animation change l'image : entrée, sortie, ou
    tout le clip pour une boucle."""
    start, L = float(c["start"]), float(c.get("dur") or 0.0)
    if _get(c, "anim_loop"):
        return [(start, start + L)]
    di, do = timing(c)
    out = []
    if di > 0:
        out.append((start, start + di))
    if do > 0:
        out.append((start + L - do, start + L))
    return out


def normalize(value, kind: str, target: str) -> dict | None:
    """Animation valide d'un clip (`target` : "text" ou "media"), sinon None."""
    if not isinstance(value, dict):
        return None
    if value.get("type") == "custom":
        return normalize_custom(value, kind)
    d = definitions().get(str(value.get("type") or ""))
    if not d or d["kind"] != kind or target not in d.get("for", ["text", "media"]):
        return None
    if kind == "loop":
        try:
            speed = float(value.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        return {"type": value["type"], "speed": round(min(MAX_SPEED, max(MIN_SPEED, speed)), 3)}
    try:
        dur = float(value.get("dur") or d.get("dur", 0.5))
    except (TypeError, ValueError):
        dur = float(d.get("dur", 0.5))
    return {"type": value["type"], "dur": round(min(MAX_DUR, max(MIN_DUR, dur)), 3)}


def normalize_custom(value: dict, kind: str) -> dict | None:
    """Animation « sur mesure » : images clés `[[u 0..1, {o, dx, dy, s, sx, sy, r, b}, courbe?], …]`,
    progression 0 -> 1 sur `dur` (entrée, sortie) ou sur `period` / tout le clip (`span`) pour
    une boucle. Valeurs bornées ; courbes inconnues ramenées à linéaire."""
    raw = value.get("kf")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    kf = []
    for item in raw[:32]:
        if not isinstance(item, (list, tuple)) or len(item) < 2 or not isinstance(item[1], dict):
            return None
        try:
            u = round(min(1.0, max(0.0, float(item[0]))), 4)
            props = {k: round(min(PROPS[k][1], max(PROPS[k][0], float(v))), 4)
                     for k, v in item[1].items() if k in PROPS}
        except (TypeError, ValueError):
            return None
        row = [u, props]
        if len(item) > 2 and item[2] in EASE:
            row.append(item[2])
        kf.append(row)
    kf.sort(key=lambda r: r[0])
    kf[0][0], kf[-1][0] = 0.0, 1.0
    # une entrée finit sur l'état normal du clip, une sortie en part : pas de saut
    if kind == "in":
        kf[-1][1] = {}
    elif kind == "out":
        kf[0][1] = {}
    out = {"type": "custom", "ease": value.get("ease") if value.get("ease") in EASE else "linear", "kf": kf}
    try:
        if kind == "loop":
            if value.get("span"):
                out["span"] = True
            else:
                out["period"] = round(min(20.0, max(0.1, float(value.get("period") or 1.0))), 3)
            out["speed"] = round(min(MAX_SPEED, max(MIN_SPEED, float(value.get("speed") or 1.0))), 3)
        else:
            out["dur"] = round(min(MAX_DUR, max(MIN_DUR, float(value.get("dur") or 0.5))), 3)
    except (TypeError, ValueError):
        return None
    return out


def unit_state(st: dict, per: str, n: int) -> list[dict] | None:
    """Opacité et flou combinés de chaque lettre (ou mot) : None sans animation `per`."""
    lst = [(d, p) for d, p in st.get("units", []) if d.get("per") == per]
    if not lst:
        return None
    out = [{"o": 1.0, "b": 0.0} for _ in range(n)]
    for d, p in lst:
        for i, v in enumerate(unit_values(d, p, n)):
            out[i]["o"] *= v["o"]
            out[i]["b"] += v["b"]
    return out
