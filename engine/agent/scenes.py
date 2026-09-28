"""Scènes d'un montage court : ce qui est à l'écran pendant chaque passage.

Après la coupe (build_edit), l'agent découpe la timeline en scènes, chacune
calée sur un mot, avec sa mise en page et ce qui y apparaît, mot par mot :

  * face     — le visage plein cadre, habillé de textes (barrés, entourés,
               soulignés), d'une étiquette ou d'une carte d'appel à l'action ;
  * split    — écran partagé : une zone graphique en haut (0→880 px d'un
               cadre 1080×1920), le visage dessous ;
  * face_top — le visage en haut, une page de papier dessous (look paper) ;
  * face_box — une page pleine, le visage dans une fenêtre arrondie ;
  * full     — une page pleine, sans visage (la voix continue) ;
  * world    — 16:9 : un décor plein cadre où le visage est une carte qui
               change de place sur les mots.

Pour chaque scène : les plans de la piste principale sont coupés à ses
bornes et le visage est cadré dans sa zone (`box`) ; la zone graphique est une
page HTML animée (scene_html.py) rendue en vidéo et posée sur la piste
« Scènes » ; les plans de coupe vidéo d'une zone deviennent de vrais clips
(retouchables dans le studio) ; les sous-titres vont là où la mise en page
les veut, en encre sur une zone claire.

Avant tout rendu, le plan est contrôlé comme le ferait un monteur : chaque
scène commence sur un mot, les scènes couvrent toute la timeline, et l'image
ne reste jamais plus de MAX_GAP s sans que rien ne s'y passe — sauf pause
voulue (`hold` : pourquoi on laisse respirer). Après rendu, chaque zone est
vérifiée dans le navigateur : textes qui se chevauchent ou sortent du cadre,
contraste trop faible, texte posé sur le visage.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from engine.agent import edit as E
from engine.agent import scene_html as SH
from engine.agent import visuals
from engine.timeline import model

MAX_GAP = 2.2              # s sans événement visuel au-delà desquelles il faut une raison
ANCHOR_BEFORE, ANCHOR_AFTER = 0.15, 0.2   # un début de scène à ±… d'un début de mot
SNAP = 0.35                # s : un début de scène plus loin d'un mot est une erreur
MAX_SCENE = 60.0           # s : une scène plus longue se découpe
SCENES_TRACK = "Scènes"
CUTS_TRACK = "Plans (scènes)"
TAG = "scene"
VERSION = 1
LAYOUTS = ("face", "split", "face_top", "face_box", "full", "world")
FACE_SPOTS = ("full", "wide", "left", "right", "tl", "tr", "bl", "br", "center", "hidden")
ITEM_TYPES = ("card", "badge", "stamp", "line", "big", "check", "image", "capture", "logo", "photo", "chevrons",
              "glass", "statement", "pill", "tag", "logocard", "cascade", "kicker", "headline", "sub", "note",
              "rows", "mega", "keyword", "text", "video")


HELP = {
    "how": "build_scenes(scenes=[…], look, brand, captions) after build_edit. Scenes are in TIMELINE time, in "
           "order, each starting on the word that opens it (start: seconds or \"w:word\"; \"w:word#2\" = its 2nd "
           "time). A scene ends where the next starts; the last ends with the video. Every `at` of an item is a "
           "timeline time or \"w:word\" said inside the scene. Item x/y/w/h are pixels of the scene's ZONE "
           "(top-left origin; x:\"center\" centres). The plan is checked before anything is drawn: scenes on "
           "words, full coverage, and never more than 2.2 s without something happening on screen unless the "
           "scene has `hold` (why you let it breathe). check_only=true checks without drawing.",
    "layouts": {
        "face": "speaker full frame (zone = whole frame 1080x1920). Items: `text` overlays (keep them off the "
                "face: y ≥ 1000 in a vertical selfie), `image`, `chevrons`; scene `tag` (label top-left); scene "
                "`cta` {kicker, keyword} = call-to-action card (captions move above it).",
        "split": "screen zone on top (0→880 px, zone 1080x880, brand background) + face below (framed on the "
                 "face in its window). Captions sit at the bottom of the zone in ink. zone_height changes 880.",
        "face_top": "face on top (0→1000 px) + paper/zone below (zone starts at y 940: 1080x980).",
        "face_box": "full page (1080x1920) + the face in a rounded window at x90 y1030 900x780 — put items in "
                    "the top 0→1000 px.",
        "full": "full page, no face (the voice goes on): one idea, one number, one proof, or the end screen.",
        "world": "16:9 only: full-frame décor (mesh gradient of the accent, or background=\"media:<id>\") where "
                 "the speaker is a card: face_start + face=[{pos, at}] with pos full|wide|left|right|tl|tr|bl|br|"
                 "center|hidden; items (glass, statement, pill, tag, logocard, cascade…) around it.",
    },
    "scene_fields": {
        "layout": "see layouts", "start": "seconds or \"w:word\"", "hold": "reason for a long still moment",
        "items": "list of items (below)", "look": "override the global look for this scene (clean|paper)",
        "background": "plain | gradient | paper | mesh | media:<image id>", "enter": "drop (default) | curtain | none",
        "tag": "face layout: small label", "tag_pos": "[x, y]", "cta": "face layout: {kicker, keyword with <em>}",
        "face_focus": "{x, y} in the source (0..1) to frame on, instead of the detected face",
        "face_zoom": "zoom of the face in its window (1 = fill)", "captions": "caption look for this scene "
        "(y, color, kw, size…)", "zone_height": "split: height of the top zone (px)", "brand": "per-scene colours",
    },
    "items": {
        "card": "white card: title (<em>accent</em>, <br>), label, value+suffix+count (count-up duration s, "
                "count_at), number (static), logos [media:…], cols; from left|right|up|down|none; strike_at "
                "(+strike_y) crosses it out; shrink_at (+shrink_scale, shrink_x, shrink_y)",
        "big": "huge number or word: value+suffix (counts up) or text, label, size, y, circle_at (a ring around it)",
        "stamp": "tilted stamp word (WHO EARNS MORE?)", "badge": "round accent badge (VS)",
        "check": "row with n, text and a check mark at check_at; underline_at",
        "line": "one line of text (size, heavy, color, tracking; x:\"center\")",
        "image": "picture card (src media:<id>): w, h, highlights [[x,y,w,h]] + highlight_at, zoom_at + zoom + "
                 "origin \"x% y%\" — real screenshots/captures first",
        "capture": "browser window around a page capture: src, url, w, h, pan (px scrolled), highlight [x,y,w,h]",
        "video": "b-roll playing inside the zone — a real clip in its window: src media:<id>, x, y, w, h, "
                 "media_start, out_at, radius, focus [x, y], keep_audio",
        "photo": "photo card: src, w, h, rotate, drift (slow zoom)", "logo": "logo image: src, w",
        "chevrons": "animated ⌄⌄⌄ pointing down (y)",
        "glass": "frosted card (world): icon, title, value+count+suffix, number, sub, bars [%…], shot, dark",
        "statement": "kicker + big text (<em>) + sub", "pill": "glass button with arrow", "tag": "rounded label "
        "(text, logo)", "logocard": "product card: logo, title, chip, badge", "cascade": "3D fan of images (src list)",
        "kicker": "paper look: small label row (left, right)", "headline": "paper look: big uppercase title, "
        "<em> in serif italic accent", "sub": "serif italic line", "note": "handwritten annotation (red, tilted)",
        "rows": "paper card with numbered rows [[num, label, meta, done(true/false/null)]]",
        "mega": "giant words (lines [..]), circle", "keyword": "call-to-action card: kicker + text with <em>",
        "text": "face layout overlay: text (<em>, <br>), y (top px), size, color white|accent|grey|#hex, italic, "
                "serif, label, check, strike_at, circle_at, underline_at, dim_at (+dim_to, dim_scale), out_at, "
                "pop scale|up",
    },
    "common": "every item: at (appearance), out_at (leaves). Decorations land on their own words: strike_at, "
              "circle_at, underline_at, check_at, highlight_at, zoom_at, count_at.",
    "look": "clean (default: light brand background, white cards, one accent, Inter) or paper (warm paper, grain, "
            "grid, tilted cards, Archivo + Fraunces italic accent + Caveat notes)",
    "brand": "{bg, ink, accent, accent_ink (accent dark enough to read as text), muted, card, font overrides "
             "text/heavy/display/serif/hand (bundled font names)}",
    "captions": "build_scenes(captions={style, keywords:[…], lexicon:{wrong:right}, words_per_line}) rebuilds the "
                "captions, marks the keywords (enlarged in the short-form styles net/net_accent) and places them "
                "per layout. Without it, the current captions are placed.",
}


class SceneError(ValueError):
    """Plan de scènes refusé : `problems` dit quoi corriger, scène par scène."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


# ------------------------------------------------------------ géométrie

def geometry(layout: str, W: int, H: int, scene: dict) -> dict:
    """Zone graphique (px du cadre), fenêtre du visage, place des sous-titres.

    `area` : (x, y, w, h) de la page HTML ; `opaque` : la zone cache ce qui est
    dessous ; `face` : (x, y, w, h, r) du visage, None = plein cadre ;
    `holes` : fenêtres du visage dans la page (repère de la page) ; `cap_y` :
    hauteur des sous-titres (fraction), `cap` : « ink » (encre sur fond clair),
    « pill » (pastille sombre) ou None (le style tel quel)."""
    kx, ky = W / 1080, H / 1920
    if layout == "split":
        zh = round(float(scene.get("zone_height") or 880) * ky)
        return {"area": (0, 0, W, zh), "opaque": True, "face": (0, zh, W, H - zh, 0), "holes": [],
                "cap_y": (zh - 100 * ky) / H, "cap": "ink"}
    if layout == "face_top":
        top = round(940 * ky)
        return {"area": (0, top, W, H - top), "opaque": True, "face": (0, 0, W, round(1000 * ky), 0), "holes": [],
                "cap_y": 850 / 1920, "cap": None}
    if layout == "face_box":
        box = (round(90 * kx), round(1030 * ky), round(900 * kx), round(780 * ky), 30)
        return {"area": (0, 0, W, H), "opaque": True, "face": box,
                "holes": [{"x": box[0], "y": box[1], "w": box[2], "h": box[3], "r": box[4] * kx}],
                "cap_y": 940 / 1920, "cap": "ink"}
    if layout == "full":
        return {"area": (0, 0, W, H), "opaque": True, "face": None, "holes": [], "cap_y": 1690 / 1920, "cap": "ink"}
    if layout == "world":
        return {"area": (0, 0, W, H), "opaque": True, "face": None, "holes": [], "cap_y": 930 / 1080, "cap": "pill"}
    cta = bool(scene.get("cta"))
    return {"area": (0, 0, W, H), "opaque": False, "face": None, "holes": [],
            "cap_y": (1140 / 1920) if cta else None, "cap": None}


def world_spot(pos: str, W: int, H: int, margin: int = 70) -> tuple | None:
    """Place de la carte du visage dans un décor 16:9 : (x, y, w, h, r) ou None
    (plein cadre) ; « hidden » = caché derrière le décor."""
    m = margin
    spots = {"full": None, "wide": (m + 90, m + 50, W - 2 * (m + 90), H - 2 * (m + 50), 34),
             "left": (m, 90, 620, 900, 34), "right": (W - 620 - m, 90, 620, 900, 34),
             "tl": (m, m, 430, 600, 30), "tr": (W - 430 - m, m, 430, 600, 30),
             "bl": (m, H - 600 - m, 430, 600, 30), "br": (W - 430 - m, H - 600 - m, 430, 600, 30),
             "center": ((W - 680) // 2, 100, 680, 880, 34), "hidden": "hidden"}
    if pos not in spots:
        raise SceneError(f"Unknown face position {pos!r}: {', '.join(FACE_SPOTS)}.")
    return spots[pos]


# ---------------------------------------------------------------- mots

def _norm(s: str) -> str:
    from engine.agent.service import kw_norm
    return kw_norm(s)


class Clock:
    """Temps de la timeline : mots (pour « w:mot ») et cadence."""

    def __init__(self, words: list[dict], fps: int, duration: float, lexicon: dict | None = None) -> None:
        self.words = words                 # [{t0, t1, text}] (edit.timeline_words)
        self.fps = fps
        self.duration = duration
        lex = {_norm(k): _norm(v) for k, v in (lexicon or {}).items()}
        self.norms = [_norm(w["text"]) for w in words]
        self.alias = [lex.get(n, n) for n in self.norms]

    def snap(self, t: float) -> float:
        return round(round(t * self.fps) / self.fps, 4)

    def resolve(self, at, lo: float = 0.0, hi: float | None = None, what: str = "") -> float:
        """`at` : secondes, ou « w:mot » (début du premier « mot » dit entre lo et hi),
        « w:mot#2 » pour la 2e fois, « w:le vrai secret » pour une suite de mots
        (début du premier d'entre eux) : sans ambiguïté sur les petits mots."""
        hi = self.duration if hi is None else hi
        if isinstance(at, str) and at.startswith("w:"):
            target, _, nth = at[2:].partition("#")
            keys = [k for k in (_norm(x) for x in target.split()) if k]
            n = int(nth) if nth.isdigit() else 1
            seen = 0
            for i, w in enumerate(self.words):
                if not (lo - 0.05 <= w["t0"] <= hi + 0.05) or not keys:
                    continue
                if all(i + j < len(self.words) and keys[j] in (self.norms[i + j], self.alias[i + j])
                       for j in range(len(keys))):
                    seen += 1
                    if seen == n:
                        return round(w["t0"], 3)
            near = " ".join(w["text"] for w in self.words if lo - 0.05 <= w["t0"] <= hi + 0.05)[:160]
            raise SceneError(f"{what}: word {target!r} is not said between {lo:.2f} and {hi:.2f} s. "
                             f"Said there: « {near} »")
        try:
            return round(float(at), 3)
        except (TypeError, ValueError):
            raise SceneError(f"{what}: bad time {at!r} (seconds, or \"w:word\").") from None

    def word_near(self, t: float) -> dict | None:
        return min(self.words, key=lambda w: abs(w["t0"] - t)) if self.words else None

    def said(self, a: float, b: float) -> str:
        return " ".join(w["text"] for w in self.words if a - 0.01 <= w["t0"] < b)


# ------------------------------------------------------------- le plan

def plan(body: dict, clock: Clock, W: int, H: int) -> dict:
    """Scènes normalisées (temps de timeline, calés sur les images et les mots),
    événements visuels et problèmes. Lève SceneError si le plan ne tient pas."""
    raw = body.get("scenes")
    if not isinstance(raw, list) or not raw:
        raise SceneError("`scenes` must be a non-empty list of {layout, start, items…}.")
    problems: list[str] = []
    notes: list[str] = []
    portrait = H > W
    scenes = []
    prev = 0.0
    for n, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            raise SceneError(f"Scene {n} must be an object.")
        layout = str(s.get("layout") or "face")
        if layout not in LAYOUTS:
            raise SceneError(f"Scene {n}: unknown layout {layout!r}. Layouts: {', '.join(LAYOUTS)}.")
        if layout in ("split", "face_top", "face_box") and not portrait:
            raise SceneError(f"Scene {n}: layout {layout!r} is for a vertical frame; in 16:9 use \"world\" or \"face\".")
        if layout == "world" and portrait:
            raise SceneError(f"Scene {n}: layout \"world\" is for a 16:9 frame; in 9:16 use \"split\".")
        start = 0.0 if n == 1 and s.get("start") in (None, 0, 0.0) else \
            clock.resolve(s.get("start", prev), prev, None, f"scene {n} start")
        start = clock.snap(start)
        scenes.append({**s, "n": n, "layout": layout, "start": start})
        prev = start
    # bornes : la scène suivante, la fin de la timeline
    for a, b in zip(scenes, scenes[1:] + [None]):
        a["end"] = clock.snap(b["start"]) if b else clock.snap(clock.duration)
    if scenes[0]["start"] > 0.001:
        problems.append(f"Scene 1 starts at {scenes[0]['start']} s: the scenes must cover the timeline from 0.")
    for s in scenes:
        if s["end"] - s["start"] < 1.0 / clock.fps:
            problems.append(f"Scene {s['n']} ({s['start']}–{s['end']} s) has no duration: check the starts.")
        if s["end"] - s["start"] > MAX_SCENE:
            problems.append(f"Scene {s['n']} lasts {s['end'] - s['start']:.1f} s: split it (max {MAX_SCENE:.0f} s).")
        # un début de scène tombe sur un mot (sauf la toute première image)
        if s["n"] > 1 and clock.words:
            ok = any(-ANCHOR_BEFORE <= w["t0"] - s["start"] <= ANCHOR_AFTER for w in clock.words)
            if not ok:
                w = clock.word_near(s["start"])
                if w and abs(w["t0"] - s["start"]) <= SNAP:
                    s["start"] = clock.snap(w["t0"])
                    notes.append(f"scene {s['n']} start snapped to « {w['text']} » at {s['start']} s")
                else:
                    problems.append(f"Scene {s['n']} starts at {s['start']} s, not on a word"
                                    + (f" (nearest: « {w['text']} » at {w['t0']} s)" if w else "")
                                    + ". Start scenes on the word that opens them (\"w:word\").")
    for a, b in zip(scenes, scenes[1:]):
        a["end"] = b["start"]
    return {"scenes": scenes, "problems": problems, "notes": notes}


def build_pages(pl: dict, clock: Clock, W: int, H: int, body: dict) -> list[dict]:
    """Une page HTML par scène qui montre quelque chose, et les événements."""
    look = str(body.get("look") or "clean")
    if look not in ("clean", "paper"):
        raise SceneError("look: \"clean\" or \"paper\".")
    brand = body.get("brand") or {}
    locale = str(body.get("locale") or "fr-FR")
    out = []
    for s in pl["scenes"]:
        g = geometry(s["layout"], W, H, s)
        ax, ay, aw, ah = g["area"]
        t0, t1 = s["start"], s["end"]
        what = f"scene {s['n']}"

        def rel(at, _t0=t0, _t1=t1, _what=what) -> float:
            return max(0.0, clock.resolve(at, _t0, _t1, _what) - _t0)

        p = SH.Page(aw, ah, s.get("look") or look, {**brand, **(s.get("brand") or {})}, locale)
        p.opaque = g["opaque"]
        items = list(s.get("items") or [])
        natives = []
        faces = []                        # world : [(t0, t1, spot)]
        if s["layout"] == "world":
            moves = [{"pos": s.get("face_start", "full"), "at": 0.0}]
            for mv in s.get("face") or []:
                moves.append({"pos": mv.get("pos", "full"), "at": rel(mv.get("at", t0))})
                p.event(moves[-1]["at"], "face-move", mv.get("pos", ""))
            moves.sort(key=lambda m: m["at"])
            for a, b in zip(moves, moves[1:] + [None]):
                faces.append((a["at"], b["at"] if b else t1 - t0, world_spot(a["pos"], W, H)))
        holes = []
        if s["layout"] == "face_box":
            holes = [dict(h, t0=0.0, t1=t1 - t0) for h in g["holes"]]
        elif s["layout"] == "world":
            for a, b, spot in faces:
                if spot is None:
                    holes.append({"x": 0, "y": 0, "w": W, "h": H, "r": 0, "t0": a, "t1": b, "shadow": False})
                elif spot == "hidden":
                    holes.append({"x": 0, "y": 0, "w": 0, "h": 0, "r": 0, "t0": a, "t1": b, "shadow": False})
                else:
                    holes.append({"x": spot[0], "y": spot[1], "w": spot[2], "h": spot[3], "r": spot[4], "t0": a, "t1": b})
        # le fond de la zone
        bg_html = ""
        if s["layout"] != "face":
            kind = s.get("background") or ("paper" if (s.get("look") or look) == "paper" else
                                            "mesh" if s["layout"] == "world" else "gradient" if s.get("gradient") else "plain")
            image = ""
            if isinstance(kind, str) and kind.startswith("media:"):
                image, kind = kind, "image"
            bg_html = SH.background(p, kind, t1 - t0, holes or None, image)
            if s["layout"] != "world" and not s.get("enter") == "none":
                if s.get("enter") == "curtain":
                    p.anim("body", [{"clipPath": "inset(0 0 100% 0)"}, {"clipPath": "inset(0 0 0 0)"}], 0, 0.55, "inout")
                elif s["layout"] in ("split", "face_top"):
                    p.anim("body", [{"opacity": 0, "translate": "0 -40px"}, {"opacity": 1, "translate": "0 0"}], 0, 0.35, "out")
                p.event(0.1, "scene-enter", s["layout"])
        # étiquette et carte d'appel à l'action (visage plein cadre)
        if s.get("tag"):
            tx, ty = (s.get("tag_pos") or [70, 140])[:2]
            items.insert(0, {"type": "tag", "text": s["tag"], "x": tx, "y": ty, "at": t0 + 0.2})
        if s.get("cta"):
            cta = s["cta"] if isinstance(s["cta"], dict) else {"text": str(s["cta"])}
            items.append({"type": "keyword", "kicker": cta.get("kicker", ""), "text": cta.get("keyword") or cta.get("text"),
                          "x": 70, "y": round(1290 * H / 1920) - ay, "w": aw - 140, "at": cta.get("at", t0 + 0.3)})
        for j, it in enumerate(items, 1):
            if not isinstance(it, dict):
                raise SceneError(f"{what}: each item is an object {{type, at, …}}.")
            typ = str(it.get("type") or "line")
            if typ not in ITEM_TYPES:
                raise SceneError(f"{what}: unknown item type {typ!r}. Types: {', '.join(ITEM_TYPES)}.")
            if typ == "video":
                natives.append({**it, "_at": rel(it.get("at", t0)),
                                "_out": rel(it["out_at"]) if it.get("out_at") is not None else t1 - t0})
                p.event(natives[-1]["_at"], "video", str(it.get("src", "")))
                continue
            try:
                SH.add_item(p, it, j, rel)
            except SceneError:
                raise
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                raise SceneError(f"{what}, item {j} ({typ}): {exc}") from None
        has_page = s["layout"] != "face" or len(p.body) > 0
        out.append({"scene": s, "geom": g, "page": p, "has_page": has_page, "bg": bg_html, "natives": natives,
                    "faces": faces, "events": [{"t": round(t0 + e["t"], 3), **{k: v for k, v in e.items() if k != "t"}}
                                               for e in p.events]})
    return out


def rhythm_check(pages: list[dict], extra_events: list[float], duration: float) -> tuple[list[str], dict]:
    """L'image change-t-elle assez souvent ? Un changement de mise en page
    compte (l'image change) ; un creux de plus de MAX_GAP s passé dans des scènes
    qui ne disent pas pourquoi (`hold`) est un problème."""
    changes = [pg["scene"]["start"] for prev, pg in zip(pages, pages[1:])
               if pg["scene"]["layout"] != prev["scene"]["layout"]]
    times = sorted({0.0, round(duration, 3), *[e["t"] for pg in pages for e in pg["events"]],
                    *[round(t, 3) for t in extra_events], *changes})
    problems, worst = [], 0.0
    for a, b in zip(times, times[1:]):
        gap = b - a
        worst = max(worst, gap)
        if gap <= MAX_GAP:
            continue
        # temps du creux passé dans des scènes sans raison de tenir le plan
        bare = [(pg, min(b, pg["scene"]["end"]) - max(a, pg["scene"]["start"])) for pg in pages
                if not pg["scene"].get("hold") and pg["scene"]["start"] < b and pg["scene"]["end"] > a]
        if sum(d for _, d in bare) > MAX_GAP:
            pg = max(bare, key=lambda x: x[1])[0]
            problems.append(f"Nothing happens on screen for {gap:.1f} s from {a:.2f} s (scene {pg['scene']['n']}): "
                            f"add an item on a word there, or give the scene a `hold` reason.")
    return problems, {"events": len(times) - 2, "max_gap": round(worst, 2)}


# ------------------------------------------------------------- rendu

def scenes_dir(proj) -> str:
    d = os.path.join(proj.dir, "agent", "scenes")
    os.makedirs(d, exist_ok=True)
    return d


def _index(proj) -> dict:
    try:
        with open(os.path.join(scenes_dir(proj), "index.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_index(proj, idx: dict) -> None:
    path = os.path.join(scenes_dir(proj), "index.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(idx, f)
    os.replace(path + ".tmp", path)


def render_page(proj, pg: dict, fps: int, media_refs) -> dict:
    """Rend la page d'une scène (ou reprend le rendu identique déjà fait) et
    l'importe comme média. Renvoie {media, check, cached, seconds}."""
    s, p = pg["scene"], pg["page"]
    transparent = not pg["geom"]["opaque"] or bool(pg["geom"]["holes"]) or s["layout"] == "world"
    markup = media_refs(p.html(pg["bg"]))
    dur = round(s["end"] - s["start"], 3)
    key = hashlib.sha1(json.dumps([VERSION, markup, p.w, p.h, dur, fps, transparent], ensure_ascii=False)
                       .encode("utf-8")).hexdigest()[:20]
    idx = _index(proj)
    hit = idx.get(key)
    if hit:
        m = proj.media(hit["media"])
        if m and m.get("status") in ("ready", "pending", "processing") and os.path.isfile(m.get("path", "")):
            return {"media": hit["media"], "check": hit.get("check") or [], "cached": True, "seconds": 0.0}
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "source.mov" if transparent else "source.mp4")
    t0 = time.time()
    info = visuals.render_animation(markup, p.w, p.h, dur, out, fps, transparent, max_duration=MAX_SCENE,
                                    probe="window.__check ? window.__check() : []")
    try:
        os.replace(info["sheet"], os.path.join(folder, "visual_preview.jpg"))
    except OSError:
        pass
    name = f"Scène {s['n']} ({s['layout']})" + (".mov" if transparent else ".mp4")
    proj.add_media(out, name=name, copied=True, mid=mid)
    check = info.get("probe") if isinstance(info.get("probe"), list) else []
    idx[key] = {"media": mid, "check": check, "at": time.time()}
    _save_index(proj, idx)
    return {"media": mid, "check": check, "cached": False, "seconds": round(time.time() - t0, 1),
            "reused_frames": info.get("reused", 0), "frames": info.get("frames", 0)}


# ------------------------------------------------------------ contrôles

def _rgb(css: str) -> tuple[float, float, float] | None:
    m = re.match(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)", str(css or ""))
    if m:
        return tuple(float(x) for x in m.groups())          # type: ignore[return-value]
    h = str(css or "").lstrip("#")
    if len(h) == 6:
        return tuple(float(int(h[i:i + 2], 16)) for i in (0, 2, 4))  # type: ignore[return-value]
    return None


def contrast(fg: str, bg: str) -> float | None:
    """Rapport de contraste WCAG entre deux couleurs CSS."""
    a, b = _rgb(fg), _rgb(bg)
    if not a or not b:
        return None

    def lum(c):
        v = [x / 255 for x in c]
        v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    la, lb = lum(a), lum(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 2)


def page_problems(pg: dict, check: list, face_rect=None) -> list[str]:
    """Ce que le navigateur a vu sur la dernière image d'une zone."""
    s = pg["scene"]
    what = f"scene {s['n']} ({s['layout']})"
    out = []
    ax, ay = pg["geom"]["area"][:2]
    for c in check or []:
        kind = c.get("type")
        if kind == "outside":
            out.append(f"{what}: text « {c.get('text')} » goes outside the frame — move it or make it smaller.")
        elif kind == "overlap":
            out.append(f"{what}: texts overlap (« {c.get('text')} ») — give each its own place.")
        elif kind == "contrast":
            r = contrast(c.get("fg"), c.get("bg"))
            if r is not None and r < 3.0:
                out.append(f"{what}: « {c.get('text')} » is hard to read (contrast {r}:1, needs 3:1) — "
                           f"use a darker accent (brand.accent_ink) or another color.")
        elif kind == "rects" and face_rect:
            fx0, fy0, fx1, fy1 = face_rect
            for _, x, y, w, h in c.get("rects") or []:
                x, y = x + ax, y + ay
                ix, iy = min(x + w, fx1) - max(x, fx0), min(y + h, fy1) - max(y, fy0)
                if ix > 20 and iy > 20:
                    out.append(f"{what}: a text sits on the speaker's face — move it lower (y) or to the side.")
                    break
    return list(dict.fromkeys(out))


# ------------------------------------------------------------ montage

CAPTION_FIELDS = ("y", "color", "hl", "kw", "shadow", "shadow_blur", "outline", "outline_col", "box", "box_alpha")


def _track_above(doc: dict, name: str, ref: dict) -> dict:
    """La piste vidéo `name`, créée si besoin, rangée juste au-dessus de `ref`."""
    tr = next((t for t in doc["tracks"] if t["kind"] == "video" and t["name"] == name), None) \
        or E.add_track(doc, "video", name)
    doc["tracks"].remove(tr)
    doc["tracks"].insert(doc["tracks"].index(ref), tr)
    return tr


def _media_id(ref) -> str:
    return str(ref or "").removeprefix("media:")


def _frame_in(c: dict, m: dict, face, rect: tuple, W: int, H: int, zoom: float | None = None) -> None:
    """Met le plan dans la zone `rect` (px du cadre), visage cadré au tiers haut."""
    x, y, w, h, r = rect
    zone = {"w": w, "h": h}
    scale = min(float(zoom or 1.0), E.zoom_cap(m, zone))
    fx, fy = E.frame_on(face, m, zone, scale, target=(0.5, 0.42))
    c.update(box={"x": round(x / W, 4), "y": round(y / H, 4), "w": round(w / W, 4), "h": round(h / H, 4),
                  **({"r": r} if r else {})},
             scale=round(scale, 4), x=fx, y=fy, fit="cover")
    if c["dur"] >= 1.6 and not c.get("anim_loop"):
        c["anim_loop"] = {"type": "zoom_slow", "speed": 1.0}


def place(proj, doc: dict, pages: list[dict], rendered: dict, body: dict) -> dict:
    """Pose tout sur le montage `doc` (voir la docstring du module)."""
    from engine.agent import service as S
    from engine.pipeline import style_presets
    W, H = doc["canvas"]["w"], doc["canvas"]["h"]
    media = {m["id"]: m for m in proj.state["media"]}
    # 1. ce que la version précédente avait posé s'en va
    doc["clips"] = [c for c in doc["clips"] if c.get("tag") != TAG]
    main = E.main_track(doc)
    # 2. les plans de la principale se coupent aux bornes des scènes (et aux déplacements du visage)
    cuts = sorted({pg["scene"]["start"] for pg in pages} | {round(pg["scene"]["start"] + a, 4)
                                                            for pg in pages for a, _, _ in pg["faces"][1:]})
    for t in cuts:
        if t > 0:
            E.split_at(doc, t, ids=[c["id"] for c in E.main_clips(doc)])
    faces = S.face_finder(proj, media)

    def scene_at(t: float) -> dict:
        return next((pg for pg in pages if pg["scene"]["start"] - 1e-3 <= t < pg["scene"]["end"] - 1e-3), pages[-1])

    face_rects: dict[int, tuple] = {}
    for c in E.main_clips(doc):
        if c["kind"] != "video":
            continue
        pg = scene_at(c["start"] + c["dur"] / 2)
        s, g = pg["scene"], pg["geom"]
        m = media.get(c["media"])
        wanted = s.get("face_focus")           # {x, y} dans la source : où est la personne
        face = wanted if isinstance(wanted, dict) else faces(c["media"], c["in"], E.src_end(c))
        rect = g["face"]
        if s["layout"] == "world":
            rel = c["start"] + c["dur"] / 2 - s["start"]
            spot = next((sp for a, b, sp in pg["faces"] if a - 1e-3 <= rel < b), None)
            rect = spot if spot not in (None, "hidden") else None
        if rect:
            _frame_in(c, m, face, rect, W, H, s.get("face_zoom"))
        elif c.get("box"):
            c.pop("box", None)
            c["scale"] = 1.0
            c["x"], c["y"] = E.position(face, m, doc["canvas"], 1.0)
        if not rect:
            fr = face_on_screen(face, c, m, doc["canvas"])
            if fr:
                face_rects.setdefault(s["n"], fr)
    # 3. les zones graphiques, sur leur piste juste au-dessus de la principale
    scenes_tr = _track_above(doc, SCENES_TRACK, main)
    placed = 0
    for pg in pages:
        r = rendered.get(pg["scene"]["n"])
        if not r:
            continue
        s, (ax, ay, aw, ah) = pg["scene"], pg["geom"]["area"]
        m = proj.media(r["media"])
        c = E.new_media_clip(m, scenes_tr["id"], s["start"], "video", s["end"] - s["start"])
        c.update(tag=TAG, muted=True)
        if (ax, ay, aw, ah) != (0, 0, W, H):
            c["box"] = {"x": round(ax / W, 4), "y": round(ay / H, 4), "w": round(aw / W, 4), "h": round(ah / H, 4)}
        doc["clips"].append(c)
        placed += 1
    # 4. les plans de coupe vidéo des zones : de vrais clips, dans leur fenêtre
    natives = 0
    if any(pg["natives"] for pg in pages):
        cuts_tr = _track_above(doc, CUTS_TRACK, scenes_tr)
        for pg in pages:
            s, (ax, ay, _, _) = pg["scene"], pg["geom"]["area"]
            for it in pg["natives"]:
                m = media.get(_media_id(it.get("src")))
                if not m or m["kind"] != "video":
                    raise SceneError(f"scene {s['n']}: video item needs src=\"media:<id>\" of an imported video "
                                     f"(import_media first). Media: {S.media_names(proj)}")
                x, y = float(it.get("x", 40)) + ax, float(it.get("y", 120)) + ay
                w, h = float(it.get("w", 960)), float(it.get("h", 540))
                dur = max(0.2, it["_out"] - it["_at"])
                c = E.new_media_clip(m, cuts_tr["id"], s["start"] + it["_at"], "video", dur,
                                     float(it.get("media_start") or 0.0))
                c.update(tag=TAG, muted=not it.get("keep_audio"), fit="cover",
                         anim_in={"type": "fade", "dur": 0.3})
                focus = it.get("focus")
                _frame_in(c, m, {"x": focus[0], "y": focus[1]} if focus else None,
                          (x, y, w, h, float(it.get("radius", 18))), W, H, 1.0)
                c.pop("anim_loop", None)
                doc["clips"].append(c)
                natives += 1
    # 5. les sous-titres : là où la mise en page les attend
    look = str(body.get("look") or "clean")
    brand = {**SH.DEFAULT_BRAND.get(look, SH.DEFAULT_BRAND["clean"]), **(body.get("brand") or {})}
    style = style_presets.preset(doc["settings"].get("style") or "hype")
    caps = body.get("captions") if isinstance(body.get("captions"), dict) else {}
    style.update({k: caps[k] for k in CAPTION_FIELDS if caps.get(k) is not None})
    moved = 0
    for c in doc["clips"]:
        if c["kind"] != "text" or not c.get("auto") or c.get("gone"):
            continue
        pg = scene_at(c["start"])
        s, g = pg["scene"], pg["geom"]
        for k in CAPTION_FIELDS:              # d'abord le style tel quel (une version précédente a pu y toucher)
            if k in style:
                c[k] = style[k]
        if g["cap_y"] is not None:
            c["y"] = round(g["cap_y"], 4)
        if g["cap"] == "ink" and not c.get("box"):
            c.update(color=brand["ink"], hl=brand["ink"], kw=brand.get("accent_ink", brand["accent"]),
                     shadow=0, shadow_blur=0, outline=0)
        elif g["cap"] == "pill":
            c.update(box=True, outline_col="#0A0A0A", box_alpha=0.38, outline=max(12.0, float(c.get("outline") or 0)))
        over = {k: v for k, v in (s.get("captions") or {}).items() if k in S.LOOK_FIELDS}
        c.update(over)
        if not caps.get("emojis"):
            c["emoji"] = ""
        c["moved"] = True
        moved += 1
    E.fix_overlaps(doc)
    return {"visuals": placed, "videos": natives, "captions_placed": moved, "face_rects": face_rects}


def build(proj, body: dict, job: dict | None = None) -> dict:
    """L'outil build_scenes (voir la docstring du module). Avec `check_only`,
    rien n'est rendu : le plan contrôlé revient tout de suite."""
    from engine.agent import service as S
    t_start = time.time()

    def progress(pct: float, msg: str) -> None:
        if job is not None:
            job.update(pct=int(pct), message=msg)

    W_ = S.Words(proj)
    with proj.lock:
        doc = S.doc_of(proj)
        rev0 = proj.rev
    if not E.main_clips(doc):
        raise SceneError("The timeline is empty: cut the video first (build_edit), then build the scenes.")
    caps = body.get("captions")
    if caps:
        # pas d'émojis automatiques : ils se poseraient sur les zones graphiques (sauf demande)
        caps = {"emojis": False, **(caps if isinstance(caps, dict) else {})}
        body = {**body, "captions": caps}
        S.make_captions(proj, doc, W_, caps)
    lexicon = (caps or {}).get("lexicon") if isinstance(caps, dict) else None
    fps = int(doc["canvas"].get("fps") or 30)
    Wc, Hc = doc["canvas"]["w"], doc["canvas"]["h"]
    clock = Clock(E.timeline_words(doc, W_.words), fps, model.duration(doc["clips"]), lexicon)
    pl = plan(body, clock, Wc, Hc)
    pages = build_pages(pl, clock, Wc, Hc, body)
    kw_times = [w["start"] for c in doc["clips"] if c["kind"] == "text" and c.get("auto") and not c.get("gone")
                for w in c.get("words") or [] if w.get("k") and not w.get("cut")]
    gaps, metrics = rhythm_check(pages, kw_times, clock.duration)
    problems = pl["problems"] + gaps
    summary = [{"scene": pg["scene"]["n"], "layout": pg["scene"]["layout"], "start": pg["scene"]["start"],
                "end": pg["scene"]["end"], "said": clock.said(pg["scene"]["start"], pg["scene"]["end"])[:140],
                "events": [round(e["t"], 2) for e in pg["events"]],
                **({"hold": pg["scene"]["hold"]} if pg["scene"].get("hold") else {})} for pg in pages]
    report = {"scenes": summary, "metrics": {**metrics, "duration": clock.duration, "scenes": len(pages),
                                             "average_scene": round(clock.duration / max(1, len(pages)), 2)},
              "notes": pl["notes"]}
    if problems and not body.get("force"):
        raise SceneError(f"The scene plan needs fixing ({len(problems)} problem(s)); nothing was rendered.", problems)
    if body.get("check_only"):
        return {**report, "ok": not problems, "problems": problems, "rendered": False}
    # rendu des zones (en cache : une scène inchangée n'est pas refaite)
    rendered: dict[int, dict] = {}
    todo = [pg for pg in pages if pg["has_page"]]
    for i, pg in enumerate(todo):
        progress(5 + 80 * i / max(1, len(todo)), f"Drawing scene {pg['scene']['n']} ({i + 1}/{len(todo)})…")
        rendered[pg["scene"]["n"]] = render_page(proj, pg, fps, lambda mk: S.media_refs(proj, mk))
    progress(88, "Waiting for the drawn scenes to be ready…")
    for r in rendered.values():
        S._wait_ready(proj, r["media"], 240)
    with proj.lock:
        if proj.rev != rev0:
            raise SceneError("The timeline changed while the scenes were being drawn: call build_scenes again "
                             "(the drawings are kept, it will be quick).", [])
        doc2 = S.doc_of(proj)
        if caps:
            S.make_captions(proj, doc2, W_, caps if isinstance(caps, dict) else {})
        rep = place(proj, doc2, pages, rendered, body)
        rev = S.commit(proj, doc2, "build_scenes")
    checks = []
    for pg in pages:
        r = rendered.get(pg["scene"]["n"])
        if r:
            checks += page_problems(pg, r.get("check"), rep["face_rects"].get(pg["scene"]["n"]))
    try:
        with open(os.path.join(scenes_dir(proj), "last.json"), "w", encoding="utf-8") as f:
            json.dump({"body": body, "at": time.time(), "rev": rev,
                       "starts": [pg["scene"]["start"] for pg in pages],
                       "layouts": [pg["scene"]["layout"] for pg in pages]}, f, ensure_ascii=False)
    except OSError:
        pass
    progress(100, "Done.")
    return {**report, "ok": not (problems or checks), "rev": rev, "problems": problems, "layout_checks": checks,
            "rendered": sum(1 for r in rendered.values() if not r["cached"]),
            "reused": sum(1 for r in rendered.values() if r["cached"]),
            "visuals": rep["visuals"], "videos": rep["videos"], "captions_placed": rep["captions_placed"],
            "seconds": round(time.time() - t_start, 1)}


def face_on_screen(face: dict | None, clip: dict, media: dict, canvas: dict) -> tuple | None:
    """Rectangle (px du cadre) du visage d'un plan plein cadre, pour le contrôle
    « rien sur le visage »."""
    if not face or not media or clip.get("box"):
        return None
    W, H = canvas["w"], canvas["h"]
    w, h = media.get("w") or W, media.get("h") or H
    k = max(W / w, H / h) * float(clip.get("scale") or 1.0)
    gw, gh = w * k, h * k
    cx, cy = float(clip.get("x", 0.5)) * W, float(clip.get("y", 0.5)) * H
    fx, fy = cx + (face["x"] - 0.5) * gw, cy + (face["y"] - 0.5) * gh
    fw, fh = (face.get("w") or 0.22) * gw, (face.get("h") or 0.14) * gh
    return fx - fw / 2, fy - fh / 2, fx + fw / 2, fy + fh / 2
