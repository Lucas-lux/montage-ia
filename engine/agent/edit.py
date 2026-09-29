"""Opérations de montage du moteur, pour les outils des agents.

Portage des règles de `engine/web/studio/model.js` dont les outils ont
besoin : piste principale magnétique, pistes rangées comme dans CapCut, coupes
des blancs et des tics, recalage des sous-titres liés à la voix, zooms
rythmés cadrés sur le visage. Les fonctions modifient EN PLACE un document
`{canvas, tracks, clips, markers, settings}` ; `service.py` le fait ensuite
valider par `engine.timeline.model` avant de l'enregistrer, comme une
sauvegarde du studio.
"""
from __future__ import annotations

import copy
import math
import re
import unicodedata

from engine.timeline import model

EPS = 1e-4
MIN_DUR = model.MIN_DUR
IMAGE_DUR = 3.0
TEXT_DUR = 3.0
HOLD_GAP = 0.8
CAPTIONS_TRACK = "Sous-titres"

# Rythme des coupes et des zooms (mêmes valeurs que studio/autoedit.js) :
# plan le plus long avant une coupe, zoom un plan sur deux, zoom des moments
# forts, respiration laissée après une phrase avant une coupe.
RHYTHM = {
    "calm": {"max": 9.0, "z": 1.1, "zh": 1.22, "tail": 0.3},
    "normal": {"max": 6.0, "z": 1.15, "zh": 1.28, "tail": 0.2},
    "punchy": {"max": 4.0, "z": 1.2, "zh": 1.32, "tail": 0.12},
    # « dynamique » (vidéos documentaires, créateurs) : un plan toutes les 1 à 3 s,
    # coupé en fin de proposition, cadres qui changent comme plusieurs caméras
    # (large, serré, moyen, très serré), poussée lente sur les plans larges
    "dynamic": {"max": 2.8, "min": 1.1, "cycle": (1.0, 1.3, 1.12, 1.42), "tail": 0.12, "push": True},
}
MAX_UPSCALE = 1.7        # au-delà (pixels de sortie par pixel de source), l'image devient floue

_LABEL = {"video": "Vidéo", "audio": "Audio", "text": "Texte"}


def r4(v: float) -> float:
    return round(float(v), 4)


def clip_end(c: dict) -> float:
    return c["start"] + c["dur"]


def src_end(c: dict) -> float:
    return (c.get("in") or 0.0) + c["dur"] * (c.get("speed") or 1.0)


def has_source(c: dict) -> bool:
    return c.get("kind") in ("video", "audio")


# ------------------------------------------------------------------ pistes

def find_track(doc: dict, tid: str) -> dict | None:
    return next((t for t in doc["tracks"] if t["id"] == tid), None)


def main_track(doc: dict) -> dict | None:
    return (next((t for t in doc["tracks"] if t.get("main")), None)
            or next((t for t in doc["tracks"] if t["kind"] == "video"), None))


def is_main(doc: dict, tid: str) -> bool:
    m = main_track(doc)
    return bool(m) and m["id"] == tid


def track_clips(doc: dict, tid: str) -> list[dict]:
    return sorted((c for c in doc["clips"] if c["track"] == tid), key=lambda c: c["start"])


def main_clips(doc: dict) -> list[dict]:
    m = main_track(doc)
    return track_clips(doc, m["id"]) if m else []


def add_track(doc: dict, kind: str, name: str | None = None, at: int | None = None) -> dict:
    """Nouvelle piste, rangée comme dans CapCut : texte en haut, vidéos de
    superposition au-dessus de la principale, audio en bas."""
    n = 1 + sum(1 for t in doc["tracks"] if t["kind"] == kind)
    t = {"id": model.new_id("t"), "kind": kind, "main": False, "muted": False, "hidden": False,
         "locked": False, "name": (name or f"{_LABEL[kind]} {n}")[:40]}
    pos = at
    if pos is None:
        if kind == "text":
            pos = 0
        elif kind == "video":
            first = next((i for i, x in enumerate(doc["tracks"]) if x["kind"] == "video"), -1)
            pos = first if first >= 0 else sum(1 for x in doc["tracks"] if x["kind"] == "text")
        else:
            pos = len(doc["tracks"])
    doc["tracks"].insert(max(0, min(pos, len(doc["tracks"]))), t)
    return t


def is_free(doc: dict, tid: str, start: float, dur: float, ignore=frozenset()) -> bool:
    a, b = start, start + dur
    return not any(c["track"] == tid and c["id"] not in ignore and c["start"] < b - EPS and clip_end(c) > a + EPS
                   for c in doc["clips"])


def free_track(doc: dict, kind: str, start: float, dur: float, *, ignore=frozenset(), skip_main: bool = False,
               create: bool = True, exclude=frozenset(), name: str | None = None) -> dict | None:
    """Première piste du genre voulu où le créneau est libre (sinon une nouvelle).
    Vidéo de superposition : la plus proche de la principale d'abord."""
    cands = [t for t in doc["tracks"] if t["kind"] == kind and not t.get("locked")
             and not (skip_main and t.get("main")) and t["id"] not in exclude]
    if name:
        cands = [t for t in cands if t["name"] == name] + [t for t in cands if t["name"] != name]
    elif kind == "video":
        cands.reverse()
    found = next((t for t in cands if is_free(doc, t["id"], start, dur, ignore)), None)
    return found or (add_track(doc, kind, name) if create else None)


def captions_track(doc: dict) -> dict | None:
    return next((t for t in doc["tracks"] if t["kind"] == "text" and t["name"] == CAPTIONS_TRACK), None)


# ------------------------------------------------------------------- clips

def partners(doc: dict, c: dict) -> list[dict]:
    link = c.get("link")
    return [o for o in doc["clips"] if o is not c and link and o.get("link") == link]


def _shift_with_partners(doc: dict, c: dict, delta: float) -> None:
    if abs(delta) < EPS:
        return
    c["start"] = r4(max(0.0, c["start"] + delta))
    for o in partners(doc, c):
        if not is_main(doc, o["track"]):
            o["start"] = r4(max(0.0, o["start"] + delta))


def pack_main(doc: dict) -> None:
    """Recolle les clips de la piste principale à partir de 0, sans trous."""
    main = main_track(doc)
    if not main:
        return
    at = 0.0
    for c in track_clips(doc, main["id"]):
        _shift_with_partners(doc, c, at - c["start"])
        at = r4(at + c["dur"])
    fix_overlaps(doc)


def fix_overlaps(doc: dict) -> None:
    """Chevauchements hors principale : le clip fautif part sur une piste libre."""
    for t in list(doc["tracks"]):
        if t.get("main"):
            continue
        last_end = -math.inf
        for c in track_clips(doc, t["id"]):
            if c["start"] < last_end - EPS:
                c["track"] = free_track(doc, t["kind"], c["start"], c["dur"], ignore={c["id"]},
                                        skip_main=True, name=t["name"])["id"]
            else:
                last_end = clip_end(c)


def new_media_clip(media: dict, track: str, start: float = 0.0, kind: str | None = None,
                   dur: float | None = None, src: float = 0.0) -> dict:
    k = kind or ("image" if media["kind"] == "image" else "audio" if media["kind"] == "audio" else "video")
    avail = math.inf if media["kind"] == "image" else max(MIN_DUR, float(media.get("duration") or 0) - src)
    d = min(dur or (IMAGE_DUR if media["kind"] == "image" else avail), avail)
    c = {"id": model.new_id("c"), "track": track, "kind": k, "media": media["id"], "start": r4(max(0.0, start)),
         "dur": r4(d), "in": r4(src), "speed": 1, "link": ""}
    if k in ("video", "audio"):
        c.update(volume=1, muted=False, fade_in=0, fade_out=0)
    if k == "video":
        c["detached"] = False
    if k in ("video", "image"):
        c.update(x=0.5, y=0.5, scale=1, rotation=0, opacity=1, fit="cover", flip_h=False, flip_v=False)
    return c


def linked_ids(doc: dict, ids) -> set[str]:
    ids = set(ids)
    links = {c["link"] for c in doc["clips"] if c["id"] in ids and c.get("link")}
    return ids | {c["id"] for c in doc["clips"] if c.get("link") and c["link"] in links}


def split_clip(doc: dict, clip: dict, t: float) -> dict | None:
    """Coupe un clip à l'instant `t` : il garde la gauche, la droite est renvoyée."""
    if t <= clip["start"] + MIN_DUR or t >= clip_end(clip) - MIN_DUR:
        return None
    left = t - clip["start"]
    right = copy.deepcopy(clip)
    right["id"] = model.new_id("c")
    right["start"] = r4(t)
    right["dur"] = r4(clip["dur"] - left)
    clip["dur"] = r4(left)
    if has_source(clip):
        right["in"] = r4(clip["in"] + left * (clip.get("speed") or 1))
    if "fade_out" in clip:
        right["fade_in"] = 0
        clip["fade_out"] = 0
    right.pop("trans", None)
    right.pop("gap", None)
    clip.pop("tail", None)
    if clip["kind"] == "text":
        words = clip.get("words") or []
        clip["words"] = [w for w in words if w["start"] < t]
        right["words"] = [w for w in words if w["start"] >= t]
        right.pop("src_words", None)
    if clip.get("link"):
        right["link"] = clip["link"] + "@" + str(int(round(t * 1000)))
    doc["clips"].append(right)
    return right


def split_at(doc: dict, t: float, ids=None) -> list[dict]:
    base = ids if ids else [c["id"] for c in doc["clips"]]
    everyone = linked_ids(doc, base)
    return [r for r in (split_clip(doc, c, t) for c in list(doc["clips"])
                        if c["id"] in everyone and c["start"] < t - MIN_DUR and clip_end(c) > t + MIN_DUR) if r]


def delete_clips(doc: dict, ids) -> int:
    """Supprime des clips (et leurs partenaires liés) ; la principale se recolle."""
    ids = linked_ids(doc, ids)
    main = main_track(doc)
    touches_main = any(c["id"] in ids and main and c["track"] == main["id"] for c in doc["clips"])
    before = len(doc["clips"])
    doc["clips"] = [c for c in doc["clips"] if c["id"] not in ids]
    if touches_main:
        pack_main(doc)
    return before - len(doc["clips"])


def cut_range(doc: dict, a: float, b: float) -> float:
    """Retire l'intervalle [a, b] de la timeline, sur toutes les pistes : ce qui
    suit recule d'autant (comme « Supprimer » dans CapCut, en décalant tout)."""
    a, b = max(0.0, a), max(0.0, b)
    if b - a < MIN_DUR:
        return 0.0
    for t in (b, a):
        split_at(doc, t, [c["id"] for c in doc["clips"] if not (c["kind"] == "text" and c.get("auto"))])
    inside = {c["id"] for c in doc["clips"] if not (c["kind"] == "text" and c.get("auto"))
              and c["start"] >= a - EPS and clip_end(c) <= b + EPS}
    doc["clips"] = [c for c in doc["clips"] if c["id"] not in inside]
    main = main_track(doc)
    for c in doc["clips"]:
        if c["track"] != (main or {}).get("id") and not (c["kind"] == "text" and c.get("auto")) \
                and c["start"] >= b - EPS:
            c["start"] = r4(c["start"] - (b - a))
    for m in doc.get("markers") or []:
        if m["t"] >= b:
            m["t"] = r4(m["t"] - (b - a))
    doc["markers"] = [m for m in doc.get("markers") or [] if not (a <= m["t"] < b)]
    pack_main(doc)
    reflow_captions(doc)
    return r4(b - a)


# ------------------------------------------------------------------ coupes

FILLERS = {"euh", "heu", "heuh", "euhm", "hum", "hmm", "mmh", "mh", "bah", "ben", "hein", "bof", "pff", "genre",
           "uh", "um", "erm", "uhm"}
MULTI_FILLERS = [["en", "fait"], ["du", "coup"], ["tu", "vois"], ["tu", "sais"], ["en", "gros"], ["et", "tout"],
                 ["je", "veux", "dire"], ["you", "know"], ["i", "mean"]]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[\W_]+", "", s)


def silence_cuts(words: list[dict], dur: float, max_gap: float = 0.5, pad: float = 0.08,
                 tail: float = 0.0) -> list[list[float]]:
    """Blancs avant, entre (au-delà de `max_gap`) et après les mots, en gardant
    `pad` autour d'eux et `tail` après le dernier mot avant une coupe."""
    if not words:
        return [[0.0, dur]]
    cuts = []
    if words[0]["start"] - pad > 0:
        cuts.append([0.0, words[0]["start"] - pad])
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] > max_gap:
            cuts.append([a["end"] + pad + tail, b["start"] - pad])
    last = words[-1]
    if last["end"] + pad + tail < dur:
        cuts.append([last["end"] + pad + tail, dur])
    return [c for c in cuts if c[1] > c[0]]


def filler_cuts(words: list[dict], pad: float = 0.05) -> list[list[float]]:
    """Tics (« euh », « du coup »…) à retirer. La marge `pad` ne déborde jamais
    sur les mots voisins : couper « du coup » ne doit pas entamer « je » collé derrière."""
    n = [norm(w["text"]) for w in words]
    cuts = []

    def cut(i: int, j: int) -> None:
        lo = words[i - 1]["end"] if i > 0 else -1e9
        hi = words[j + 1]["start"] if j + 1 < len(words) else 1e9
        cuts.append([max(lo, words[i]["start"] - pad), min(hi, words[j]["end"] + pad)])
    i = 0
    while i < len(words):
        phrase = next((p for p in MULTI_FILLERS if n[i:i + len(p)] == p), None)
        if phrase:
            cut(i, i + len(phrase) - 1)
            i += len(phrase)
            continue
        if n[i] in FILLERS:
            cut(i, i)
        i += 1
    return cuts


def merge_ranges(ranges) -> list[list[float]]:
    iv = sorted([max(0.0, a), b] for a, b in ranges if b > a)
    out: list[list[float]] = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def clip_cuts(clip: dict, *, words=None, extra=None, max_gap: float = 0.5, pad: float = 0.08, tail: float = 0.0,
              fillers: bool = False, min_keep: float = 0.1, min_cut: float = 0.12) -> list[list[float]]:
    """Plages à retirer d'un clip, EN TEMPS SOURCE (règles de studio/model.js)."""
    a, b = clip["in"], src_end(clip)
    cuts = [list(x) for x in (extra or [])]
    spoken: list[float] = []
    if words is not None:
        inside = [{"text": w["text"], "start": max(0.0, w["start"] - a), "end": min(b - a, w["end"] - a)}
                  for w in words if w["end"] > a and w["start"] < b]
        found = silence_cuts(inside, b - a, max_gap, pad, tail)
        if fillers:
            found += filler_cuts(inside)
        cuts += [[x + a, y + a] for x, y in found]
        spoken = [a + (w["start"] + w["end"]) / 2 for w in inside]
    # un bout gardé trop court part aussi — sauf s'il contient un mot (« je l'ai fait. »)
    has_word = lambda x, y: any(x < t < y for t in spoken)  # noqa: E731
    cuts = merge_ranges([[max(a, x), min(b, y)] for x, y in cuts])
    merged: list[list[float]] = []
    for c in cuts:
        if merged and c[0] - merged[-1][1] < min_keep and not has_word(merged[-1][1], c[0]):
            merged[-1][1] = c[1]
        else:
            merged.append(list(c))
    if merged and merged[0][0] - a < min_keep and not has_word(a, merged[0][0]):
        merged[0][0] = a
    if merged and b - merged[-1][1] < min_keep and not has_word(merged[-1][1], b):
        merged[-1][1] = b
    return [[r4(x), r4(y)] for x, y in merged if y - x >= min_cut]


def keep_ranges(a: float, b: float, cuts) -> list[list[float]]:
    out = []
    cur = a
    for x, y in merge_ranges(cuts):
        if x > cur + EPS:
            out.append([cur, min(x, b)])
        cur = max(cur, y)
    if b > cur + EPS:
        out.append([cur, b])
    return [r for r in out if r[1] - r[0] >= MIN_DUR]


# ------------------------------------------------------------- assemblage

def assemble(doc: dict, media: dict[str, dict], words_of, segments: list[dict], *, remove_silences: bool = True,
             remove_fillers: bool = True, max_gap: float = 0.5, pad: float = 0.08, tail: float = 0.2,
             min_keep: float = 0.35, replace: bool = True) -> dict:
    """Monte la piste principale à partir de plages de sources, dans l'ordre.

    `segments` : `{media, start, end, speed?, zoom?, x?, y?, fit?, transition?}`
    en temps SOURCE (une image : `duration`). Dans chaque plage, les blancs et
    les tics sont retirés d'après la transcription. Renvoie les morceaux posés
    et le temps retiré.
    """
    main = main_track(doc)
    if replace:
        old = linked_ids(doc, [c["id"] for c in doc["clips"] if c["track"] == main["id"]])
        doc["clips"] = [c for c in doc["clips"] if c["id"] not in old]
        t = 0.0
    else:
        t = max((clip_end(c) for c in track_clips(doc, main["id"])), default=0.0)
    placed: list[dict] = []
    removed = 0.0
    for n, seg in enumerate(segments):
        m = media[seg["media"]]
        speed = min(model.MAX_SPEED, max(model.MIN_SPEED, float(seg.get("speed") or 1.0)))
        if m["kind"] == "image":
            dur = float(seg.get("duration") or (seg.get("end", 0) - seg.get("start", 0)) or IMAGE_DUR)
            keep, a, b = [[0.0, dur]], 0.0, dur
        else:
            a = max(0.0, float(seg.get("start") or 0.0))
            b = min(float(m.get("duration") or 0.0), float(seg.get("end") if seg.get("end") is not None
                                                           else m.get("duration") or 0.0))
            if b - a < MIN_DUR:
                continue
            words = words_of(m["id"]) if (remove_silences or remove_fillers) else None
            cuts = []
            if words:
                probe = {"in": a, "dur": (b - a) / speed, "speed": speed}
                if remove_silences:
                    cuts = clip_cuts(probe, words=words, max_gap=max_gap, pad=pad, tail=tail,
                                     fillers=remove_fillers, min_keep=min_keep)
                else:
                    inside = [w for w in words if w["end"] > a and w["start"] < b]
                    cuts = clip_cuts(probe, extra=filler_cuts(inside), min_keep=min_keep)
            keep = keep_ranges(a, b, cuts)
        for i, (x, y) in enumerate(keep):
            if m["kind"] == "image":
                c = new_media_clip(m, main["id"], t, "image", y - x)
            else:
                c = new_media_clip(m, main["id"], t, "video", (y - x) / speed, x)
                c["speed"] = speed
                before = a if i == 0 else keep[i - 1][1]
                if x - before > EPS:
                    c["gap"] = {"s": r4(before), "e": r4(x)}
                if i == len(keep) - 1 and b - y > EPS:
                    c["tail"] = {"s": r4(y), "e": r4(b)}
            if seg.get("zoom") is not None:
                c["scale"] = r4(float(seg["zoom"]))
            for key in ("x", "y"):
                if seg.get(key) is not None:
                    c[key] = r4(float(seg[key]))
            if seg.get("fit") in ("cover", "contain"):
                c["fit"] = seg["fit"]
            if i == 0 and seg.get("transition"):
                c["trans"] = {"type": seg["transition"], "dur": float(seg.get("transition_duration") or 0.4)}
            c["_seg"] = n
            doc["clips"].append(c)
            placed.append(c)
            t = r4(t + c["dur"])
        removed += (b - a) / speed - sum((y - x) / speed for x, y in keep) if m["kind"] != "image" else 0.0
    pack_main(doc)
    return {"pieces": placed, "removed": r4(removed)}


def rhythm_splits(clip: dict, cutpoints, min_piece: float = 2.0, max_piece: float = 6.0,
                  fallback=None) -> list[float]:
    """Instants (timeline) où couper un long plan : de préférence aux fins de
    phrases (`cutpoints`, temps source), sinon entre deux mots (`fallback`,
    temps source : jamais au milieu d'un mot, qui se retrouverait coupé en deux
    sous-titres), sinon tous les `max_piece` s."""
    sp = clip.get("speed") or 1.0
    end = clip_end(clip)

    def timeline(points) -> list[float]:
        return sorted(t for t in (clip["start"] + (s - clip["in"]) / sp for s in points or [])
                      if clip["start"] + min_piece < t < end - min_piece)
    pts, alt = timeline(cutpoints), timeline(fallback)
    out = []
    cur = clip["start"]
    while end - cur > max_piece + min_piece:
        window = [t for t in pts if cur + min_piece <= t <= cur + max_piece] or             [t for t in alt if cur + min_piece <= t <= cur + max_piece]
        t = window[-1] if window else r4(cur + max_piece)
        out.append(r4(t))
        cur = t
    return out


def word_gaps(words: list[dict]) -> list[float]:
    """Entre-deux de mots (temps source, bornes SONORES si connues) : là où une
    coupe ne tranche aucun mot."""
    out = []
    for w, nxt in zip(words, words[1:]):
        a = max(w["end"], w.get("ce", w["end"]))
        b = min(nxt["start"], nxt.get("cs", nxt["start"]))
        out.append(round((a + b) / 2 if b > a else b, 3))
    return out


def zoom_center(face: dict | None, media: dict | None, canvas: dict, scale: float) -> tuple[float, float]:
    """Centre à donner à un clip « remplir » zoomé de `scale` pour que le visage
    reste où il était à l'échelle 1 (sans visage : léger recentrage vers le haut)."""
    W, H = canvas.get("w") or 1080, canvas.get("h") or 1920
    w, h = (media or {}).get("w") or W, (media or {}).get("h") or H
    base = max(W / w, H / h)
    X, Y = 0.5, 0.42
    if face:
        X = 0.5 + (face["x"] - 0.5) * (w * base / W)
        Y = 0.5 + (face["y"] - 0.5) * (h * base / H)
    X = min(0.9, max(0.1, X))
    Y = min(0.9, max(0.1, Y))
    return r4(0.5 + (X - 0.5) * (1 - scale)), r4(0.5 + (Y - 0.5) * (1 - scale))


def reframe_center(face: dict | None, media: dict | None, canvas: dict, scale: float = 1.0) -> tuple[float, float]:
    """Centre d'un clip « remplir » qui met le visage au milieu du cadre (sans
    sortir de l'image) : recadrage 16:9 -> 9:16 sur la personne."""
    W, H = canvas.get("w") or 1080, canvas.get("h") or 1920
    w, h = (media or {}).get("w") or W, (media or {}).get("h") or H
    base = max(W / w, H / h) * scale
    gw, gh = w * base, h * base
    fx, fy = (face["x"], face["y"]) if face else (0.5, 0.42)
    cx = W / 2 - (fx - 0.5) * gw
    cy = H * 0.45 - (fy - 0.5) * gh
    cx = min(gw / 2, max(W - gw / 2, cx))
    cy = min(gh / 2, max(H - gh / 2, cy))
    return r4(cx / W), r4(cy / H)


def position(face: dict | None, media: dict | None, canvas: dict, scale: float = 1.0,
             force: bool = False) -> tuple[float, float]:
    """Centre d'un plan « remplir ». Quand le format rogne beaucoup la source
    (paysage dans un cadre vertical) ou sur demande (`force`), le plan est
    recadré sur le visage ; sinon le zoom garde le visage où il était."""
    W, H = canvas.get("w") or 1080, canvas.get("h") or 1920
    w, h = (media or {}).get("w") or W, (media or {}).get("h") or H
    base = max(W / w, H / h)
    if force or w * base > W * 1.15 or h * base > H * 1.15:
        return reframe_center(face, media, canvas, scale)
    return zoom_center(face, media, canvas, scale)


def zoom_cap(media: dict | None, canvas: dict) -> float:
    """Zoom le plus fort qui reste net : la source n'est pas agrandie plus de
    MAX_UPSCALE fois (un rush 4K encaisse 1,5×, un 720p presque rien)."""
    W, H = canvas.get("w") or 1080, canvas.get("h") or 1920
    w, h = (media or {}).get("w") or W, (media or {}).get("h") or H
    base = max(W / w, H / h)
    return max(1.0, MAX_UPSCALE / base)


def frame_on(face: dict | None, media: dict | None, canvas: dict, scale: float,
             target: tuple[float, float] = (0.5, 0.4)) -> tuple[float, float]:
    """Centre d'un plan « remplir » zoomé de `scale` qui met le visage au point
    `target` du cadre (au tiers haut : l'œil regarde là), sans découvrir le bord."""
    W, H = canvas.get("w") or 1080, canvas.get("h") or 1920
    w, h = (media or {}).get("w") or W, (media or {}).get("h") or H
    base = max(W / w, H / h) * scale
    gw, gh = w * base, h * base
    fx, fy = (face["x"], face["y"]) if face else (0.5, 0.42)
    cx = target[0] * W - (fx - 0.5) * gw
    cy = target[1] * H - (fy - 0.5) * gh
    cx = min(gw / 2, max(W - gw / 2, cx))
    cy = min(gh / 2, max(H - gh / 2, cy))
    return r4(cx / W), r4(cy / H)


def apply_rhythm(doc: dict, media: dict[str, dict], cutpoints: dict[str, list], faces,
                 rhythm: str, highlights: dict[str, list] | None = None, skip=frozenset(),
                 gaps: dict[str, list] | None = None) -> int:
    """Coupes (fins de phrases ; fins de propositions en « dynamique ») et
    cadres variés, cadrés sur le visage du moment. `faces(mid, a, b)` : visage
    d'une plage de source (ou un dict média -> visage). `skip` : clips à laisser
    tels quels (zoom choisi par l'agent). `gaps` : entre-deux de mots par média
    (word_gaps), où couper quand aucune fin de phrase ne tombe. Renvoie le
    nombre de plans zoomés."""
    R = RHYTHM.get(rhythm) or RHYTHM["normal"]
    face_at = faces if callable(faces) else (lambda mid, a, b: (faces or {}).get(mid))
    for c in [c for c in main_clips(doc) if c["kind"] == "video" and c["id"] not in skip]:
        cur = c
        for t in rhythm_splits(c, cutpoints.get(c["media"]) or [], R.get("min", 2.0), R["max"],
                               (gaps or {}).get(c["media"])):
            right = split_clip(doc, cur, t)
            if right:
                cur = right
    zooms = 0
    odd = False
    k = 0
    canvas = doc["canvas"]
    for p in main_clips(doc):
        if p["kind"] != "video" or p.get("fit") == "contain" or p["id"] in skip:
            continue
        m = media.get(p["media"])
        face = face_at(p["media"], p["in"], src_end(p))
        if "cycle" in R:
            scale = min(R["cycle"][k % len(R["cycle"])], zoom_cap(m, canvas))
            if face is None:
                scale = min(scale, 1.12)          # pas de visage (écran filmé, paysage) : zooms discrets
            k += 1
            if scale > 1.01:
                x, y = frame_on(face, m, canvas, scale)
            else:
                scale = 1.0
                x, y = position(face, m, canvas, 1.0)
                if R.get("push") and p["dur"] >= 1.6 and not p.get("anim_loop"):
                    p["anim_loop"] = {"type": "zoom_slow", "speed": 1.0}      # poussée lente
        else:
            hl = (highlights or {}).get(p["media"]) or []
            strong = any(x["s"] < src_end(p) - 0.3 and x["e"] > p["in"] + 0.3 for x in hl)
            scale = min(R["zh"] if strong else R["z"] if odd else 1.0, max(1.0, zoom_cap(m, canvas)))
            odd = not odd
            x, y = position(face, m, canvas, scale)
        p.update(scale=r4(scale), x=x, y=y)
        zooms += scale > 1
    return zooms


def clause_points(words: list[dict], gap: float = 0.25) -> list[float]:
    """Fins de propositions (virgule, point, point-virgule…) et pauses : là où
    un monteur change de plan sans casser la phrase. Temps source, fin SONORE."""
    out = []
    for w, nxt in zip(words, words[1:] + [None]):
        end = w.get("ce", w["end"])
        punct = str(w["text"]).rstrip("»\")\"'").endswith((",", ".", ";", ":", "!", "?", "…"))
        pause = nxt is not None and nxt.get("cs", nxt["start"]) - end >= gap
        if punct or pause:
            t = end + 0.02
            if nxt is not None:              # jamais dans le mot suivant (il serait coupé en deux sous-titres)
                t = min(t, nxt["start"], nxt.get("cs", nxt["start"]))
            out.append(round(max(t, w["start"]), 3))
    return out


def zone_size(c: dict, canvas: dict) -> tuple[float, float]:
    """Taille (px du cadre) de la zone où vit un clip : sa `box`, sinon le cadre.
    Les cadrages (frame_on, position, zoom_cap…) se calculent dans cette zone :
    il suffit de leur passer {"w": …, "h": …} comme cadre."""
    W, H = canvas.get("w") or 1080, canvas.get("h") or 1920
    b = c.get("box")
    return (b["w"] * W, b["h"] * H) if b else (W, H)


def geometry(c: dict, m: dict, W: float, H: float) -> dict:
    """Taille affichée d'un clip visuel (même calcul que studio/player.js) ; W, H :
    la zone du clip (voir `zone_size`)."""
    w, h = (m or {}).get("w") or W, (m or {}).get("h") or H
    base = min(W / w, H / h) if c.get("fit") == "contain" else max(W / w, H / h)
    s = base * (c.get("scale") or 1.0)
    return {"w": w * s, "h": h * s}


def frame_subject(c: dict, m: dict, canvas: dict) -> bool:
    """Cadre un clip sur son sujet détouré (studio/actions.js `frameSubject`)."""
    track = ((m or {}).get("subject") or {}).get("track") or []
    if not track:
        return False
    W, H = zone_size(c, canvas)
    a, b = c.get("in") or 0.0, src_end(c)
    ref = [p for p in track if a - 0.05 <= p[0] <= b + 0.05] or track
    rx = sum(p[1] for p in ref) / len(ref)
    ry = sum(p[2] for p in ref) / len(ref)
    bh = sum(p[4] for p in ref) / len(ref)
    g = geometry(c, m, W, H)
    zoom = min(2.5, max(1.0, (0.72 * H) / max(1.0, bh * g["h"])))
    if zoom > 1.05 and c.get("fit") != "contain":
        c["scale"] = r4((c.get("scale") or 1.0) * zoom)
    g = geometry(c, m, W, H)
    cx, cy = W / 2 - (rx - 0.5) * g["w"], H * 0.5 - (ry - 0.5) * g["h"]
    if g["w"] >= W:
        cx = min(g["w"] / 2, max(W - g["w"] / 2, cx))
    if g["h"] >= H:
        cy = min(g["h"] / 2, max(H - g["h"] / 2, cy))
    c["x"], c["y"] = r4(cx / W), r4(cy / H)
    return True


# ------------------------------------------------------------- sous-titres

def voice_clips(doc: dict) -> list[dict]:
    audible = [c for c in doc["clips"] if c["kind"] == "audio" or (c["kind"] == "video" and not c.get("detached"))]
    return audible + [c for c in doc["clips"] if c["kind"] == "video" and c.get("detached")]


def map_source(clips: list[dict], mid: str, s: float) -> tuple[dict, float] | None:
    """Où tombe l'instant source `s` du média `mid` sur la timeline."""
    for c in clips:
        if c.get("media") != mid:
            continue
        if c["in"] - EPS <= s < src_end(c) - EPS:
            return c, c["start"] + (s - c["in"]) / (c.get("speed") or 1.0)
    return None


def reflow_captions(doc: dict) -> None:
    """Recale les sous-titres automatiques sur la timeline actuelle (mots dont
    le passage est coupé : `cut` ; ligne sans mot : `gone`)."""
    clips = voice_clips(doc)
    touched = set()
    for c in doc["clips"]:
        if c["kind"] != "text" or not c.get("auto") or not c.get("words"):
            continue
        lo, hi = math.inf, -math.inf
        for w in c["words"]:
            if not w.get("m"):
                lo, hi = min(lo, w["start"]), max(hi, w["end"])
                continue
            hit = map_source(clips, w["m"], w["s"])
            if not hit:
                w["cut"] = True
                continue
            clip, t = hit
            sp = clip.get("speed") or 1.0
            w["start"] = r4(t)
            w["end"] = r4(max(t, clip["start"] + (min(w["e"], src_end(clip)) - clip["in"]) / sp))
            w.pop("cut", None)
            lo, hi = min(lo, w["start"]), max(hi, w["end"])
        if lo == math.inf:
            c["gone"] = True
            continue
        c.pop("gone", None)
        start, dur = r4(lo), r4(max(0.2, hi - lo))
        if start != c["start"] or dur != c["dur"]:
            touched.add(c["track"])
        c["start"], c["dur"] = start, dur
    for tid in touched:
        row = [c for c in track_clips(doc, tid) if not c.get("gone")]
        for a, b in zip(row, row[1:]):
            if clip_end(a) > b["start"] + EPS:
                a["dur"] = r4(max(MIN_DUR, b["start"] - a["start"]))
    for tid in {c["track"] for c in doc["clips"] if c["kind"] == "text" and c.get("hold") and not c.get("gone")}:
        row = [c for c in track_clips(doc, tid) if not c.get("gone")]
        for a, b in zip(row, row[1:]):
            gap = b["start"] - clip_end(a)
            if a.get("hold") and EPS < gap <= HOLD_GAP:
                a["dur"] = r4(b["start"] - a["start"])


def put_captions(doc: dict, captions: list[dict]) -> int:
    """Remplace les sous-titres automatiques par `captions` (piste « Sous-titres »).
    Tous partent, où qu'ils soient : des lignes qui se chevauchaient ont pu
    être rangées sur d'autres pistes texte, et reviendraient au recalage."""
    tr = captions_track(doc) or add_track(doc, "text", CAPTIONS_TRACK)
    doc["clips"] = [c for c in doc["clips"] if not (c["kind"] == "text" and c.get("auto"))]
    for c in captions:
        c["track"] = tr["id"]
        doc["clips"].append(c)
    reflow_captions(doc)
    fix_overlaps(doc)
    return len(captions)


def timeline_sentences(doc: dict, sentences_of) -> list[dict]:
    """Ce qui est dit sur la timeline : chaque phrase gardée, avec ses temps
    TIMELINE (`t0`, `t1`), dans l'ordre. `sentences_of(mid)` : phrases du média
    (temps source, avec leurs mots)."""
    clips = [c for c in voice_clips(doc) if not c.get("muted")]
    out = []
    for mid in sorted({c["media"] for c in clips}):
        for s in sentences_of(mid) or []:
            run: list[tuple[float, float, str]] = []
            for w in s["words"]:
                hit = map_source(clips, mid, (w["start"] + w["end"]) / 2)
                if not hit:
                    if run:
                        out.append(_sentence_run(run, mid, s["i"]))
                        run = []
                    continue
                clip, t = hit
                sp = clip.get("speed") or 1.0
                t0 = clip["start"] + (max(w["start"], clip["in"]) - clip["in"]) / sp
                t1 = clip["start"] + (min(w["end"], src_end(clip)) - clip["in"]) / sp
                if run and t0 - run[-1][1] > 1.0:
                    out.append(_sentence_run(run, mid, s["i"]))
                    run = []
                run.append((t0, t1, w["text"]))
            if run:
                out.append(_sentence_run(run, mid, s["i"]))
    return sorted(out, key=lambda x: x["t0"])


def timeline_words(doc: dict, words_of) -> list[dict]:
    """Chaque mot gardé, à son instant TIMELINE (pour caler un visuel, un son,
    un texte sur le mot exact qu'il illustre)."""
    clips = [c for c in voice_clips(doc) if not c.get("muted")]
    out = []
    for mid in sorted({c["media"] for c in clips}):
        for w in words_of(mid) or []:
            hit = map_source(clips, mid, (w["start"] + w["end"]) / 2)
            if not hit:
                continue
            clip, _ = hit
            sp = clip.get("speed") or 1.0
            t0 = clip["start"] + (max(w["start"], clip["in"]) - clip["in"]) / sp
            t1 = clip["start"] + (min(w["end"], src_end(clip)) - clip["in"]) / sp
            out.append({"t0": r4(t0), "t1": r4(t1), "text": w["text"]})
    return sorted(out, key=lambda x: x["t0"])


def _sentence_run(run, mid: str, i: int) -> dict:
    return {"t0": r4(run[0][0]), "t1": r4(run[-1][1]), "text": " ".join(x[2] for x in run), "media": mid,
            "sentence": i}


# ------------------------------------------------------------ superpositions

def add_text(doc: dict, text: str, start: float, dur: float, look: dict, *, x: float = 0.5, y: float = 0.3,
             track_name: str = "Titres", tag: str = "") -> dict:
    """Texte libre (titre, mot-clé) sur une piste texte libre à ce moment-là."""
    from engine.pipeline.ass_edit import emoji_geometry
    cap = captions_track(doc)
    exclude = {cap["id"]} if cap else set()
    tr = free_track(doc, "text", start, dur, exclude=exclude, name=track_name)
    size = float(look.get("size") or 90)
    esz, edy = emoji_geometry(size)
    clip = {
        "id": model.new_id("x"), "track": tr["id"], "kind": "text", "start": r4(start), "dur": r4(dur),
        "auto": False, "ai": tag, "words": [{"text": text, "start": r4(start), "end": r4(start + dur)}],
        "font": "Arial", "size": 90, "bold": True, "upper": False, "color": "#FFFFFF", "hl": "#FFFFFF",
        "outline_col": "#000000", "outline": 5, "shadow": 0, "box": False, "box_alpha": 0.25,
        "emoji": "", "emoji_size": esz, "emoji_dx": 0, "emoji_dy": edy, "moved": True,
    }
    clip.update({k: v for k, v in look.items() if k not in ("label", "hint", "group", "name")})
    clip.update(mode="none", pop=False, x=r4(x), y=r4(y))
    doc["clips"].append(clip)
    return clip


def add_overlay(doc: dict, m: dict, start: float, dur: float | None = None, src: float = 0.0, *,
                track_name: str | None = None, keep_audio: bool = False) -> dict:
    """Image ou vidéo posée au-dessus de la piste principale (plan de coupe,
    illustration, incrustation)."""
    kind = "image" if m["kind"] == "image" else "video"
    c = new_media_clip(m, "", start, kind, dur, src)
    tr = free_track(doc, "video", c["start"], c["dur"], skip_main=True, name=track_name)
    c["track"] = tr["id"]
    if kind == "video" and not keep_audio:
        c["muted"] = True
    doc["clips"].append(c)
    return c


def add_audio(doc: dict, m: dict, start: float, dur: float | None = None, src: float = 0.0, *,
              track_name: str | None = None) -> dict:
    c = new_media_clip(m, "", start, "audio", dur, src)
    c["track"] = free_track(doc, "audio", c["start"], c["dur"], name=track_name)["id"]
    doc["clips"].append(c)
    return c


def clean(doc: dict) -> dict:
    """Retire les marques internes posées pendant un outil."""
    for c in doc["clips"]:
        for k in [k for k in c if k.startswith("_")]:
            del c[k]
    return doc
