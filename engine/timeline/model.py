"""Modèle d'un projet timeline, et validation de ce que l'éditeur envoie.

L'éditeur possède le montage (pistes, clips, format, réglages) et l'envoie en
entier à chaque sauvegarde. Rien n'y est cru sur parole : chaque champ est
typé et borné, les clips orphelins ou incompatibles avec leur piste sont
écartés, et deux clips d'une même piste ne peuvent pas se chevaucher. Les
médias, eux, appartiennent au moteur (import, proxies, transcription) : une
sauvegarde de l'éditeur ne les touche pas.

Repères :
  * `start`/`dur` situent un clip sur la timeline, en secondes ;
  * `in` est son point d'entrée dans le média et `speed` sa vitesse : il lit
    la source de `in` à `in + dur * speed` ;
  * `x`/`y` (0..1) placent le CENTRE du clip dans le cadre, comme pour les
    sous-titres ; `scale` multiplie la taille « remplir » ou « adapter ».
  * l'ordre des pistes est l'ordre d'affichage, de haut en bas : une piste
    vidéo plus haute passe devant les autres.
"""
from __future__ import annotations

import math
import secrets

from engine.pipeline.style_presets import BASE as CAPTION_BASE
from engine.pipeline.style_presets import COLOR_FIELDS, MODES
from engine.pipeline.style_presets import PRESETS as CAPTION_PRESETS

VERSION = 1

# Formats de projet proposés à la création (largeur, hauteur de SORTIE).
CANVAS_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
    "21:9": (2560, 1080),
}
DEFAULT_CANVAS = "9:16"
FPS_CHOICES = (24, 25, 30, 50, 60)
MAX_SIDE = 4096          # au-delà, ni les encodeurs ni les navigateurs ne suivent

TRACK_KINDS = ("video", "audio", "text")
# Genre de clip -> genre de piste qui l'accepte.
CLIP_TRACK = {"video": "video", "image": "video", "audio": "audio", "text": "text"}
MIN_DUR = 0.04           # un clip plus court qu'une image à 25 i/s n'existe pas
MAX_SPEED, MIN_SPEED = 16.0, 0.1
# Tolérance sur la fin d'un média : les durées sondées sont arrondies.
_END_SLACK = 0.05

SETTINGS_DEFAULTS: dict = {
    "max_gap": 0.5,          # silence toléré entre deux mots (s)
    "pad": 0.08,             # marge laissée autour des mots (s)
    "fillers": False,        # couper aussi les tics de langage
    "words_per_line": 4,
    "max_chars": 18,
    "word_by_word": False,   # sous-titres « mot à mot » : un seul mot à l'écran
    "style": "hype",
    "emojis": True,
    "language": None,        # langue parlée, None = détectée
    "model": "large-v3-turbo",
    "device": "auto",
    "loudness": False,       # export : niveau ramené à -14 LUFS (posé par le montage automatique)
    "auto": None,            # options du montage automatique (voir AUTO_DEFAULTS)
}
# Montage automatique : ce qu'on laisse faire à l'IA, et le rythme des zooms.
AUTO_DEFAULTS: dict = {
    "silence": True, "fillers": True, "trim": True, "hook": True, "cold_open": False, "zoom": True,
    "texts": True, "captions": True, "sound": True, "llm": True,
    "rhythm": "normal",      # calm | normal | punchy
    "max_duration": 0,       # 0 = libre
}

# Champs d'apparence d'un texte / sous-titre (mêmes clés que le mode short).
_TEXT_LOOK = dict(CAPTION_BASE)
_TEXT_EXTRA = {"emoji": "", "emoji_size": 0.0, "emoji_dx": 0.0, "emoji_dy": 0.0,
               "emoji_moved": False, "moved": False, "hidden": False, "auto": False,
               "gone": False, "lang": "", "tr_hidden": False,
               "hold": False,                  # mot à mot : reste affiché jusqu'au suivant
               "ai": ""}                       # "hook" / "text" : posé par le montage automatique
# Réglages d'image d'un clip vidéo ou image.
FILTERS = ("brightness", "contrast", "saturation", "temperature")
# Traitement de la voix d'un clip (voix off, face caméra) : intensités 0..1 ou
# interrupteurs. Rendu par ffmpeg à l'export (engine/timeline/render.py), en
# partie entendu dans l'aperçu (Web Audio).
VOICE_FX: dict = {
    "denoise": 0.0,      # réduction de bruit (RNNoise)
    "lowcut": False,     # coupe-bas 80 Hz : souffle, ronflement, pop
    "gate": False,       # porte anti-bruit entre les phrases
    "deess": 0.0,        # de-esser : sifflantes
    "compress": 0.0,     # compression : voix régulière et présente
    "clarity": 0.0,      # clarté : moins de boue (200 Hz), plus de présence (3 kHz) et d'air
    "warmth": 0.0,       # chaleur : graves autour de 180 Hz
    "level": False,      # niveau constant (normalisation dynamique)
}
# Transitions d'entrée (noms des transitions `xfade` de ffmpeg).
TRANSITIONS = ("fade", "fadeblack", "fadewhite", "slideleft", "slideright", "wipeleft", "circleopen",
               "zoomin", "dissolve")
MAX_TRANSITION = 3.0


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(4)


# ------------------------------------------------------------ petits outils

def _num(value, default: float, lo: float = -math.inf, hi: float = math.inf) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return lo if v < lo else hi if v > hi else v


def _int(value, default: int, lo: int, hi: int) -> int:
    return int(round(_num(value, default, lo, hi)))


def _bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _str(value, default: str = "", limit: int = 200) -> str:
    if value is None:
        return default
    return str(value)[:limit]


def _color(value, default: str) -> str:
    s = str(value or "").strip()
    if len(s) in (4, 7) and s.startswith("#"):
        try:
            int(s[1:], 16)
            return s.upper() if len(s) == 7 else "#" + "".join(c * 2 for c in s[1:]).upper()
        except ValueError:
            pass
    return default


def _t(v: float) -> float:
    """Temps arrondi à la 0,1 ms : assez fin pour du 60 i/s, JSON compact."""
    return round(v, 4)


# ------------------------------------------------------------------ état neuf

def canvas_for(preset: str | None) -> dict:
    w, h = CANVAS_PRESETS.get(preset or "", CANVAS_PRESETS[DEFAULT_CANVAS])
    return {"w": w, "h": h, "fps": 30, "bg": "#000000", "blur": False}


def default_tracks() -> list[dict]:
    """Une piste vidéo principale et une piste audio, comme un projet CapCut vide."""
    return [
        {"id": "tv1", "kind": "video", "name": "Vidéo", "main": True,
         "muted": False, "hidden": False, "locked": False},
        {"id": "ta1", "kind": "audio", "name": "Audio 1", "main": False,
         "muted": False, "hidden": False, "locked": False},
    ]


def new_state(pid: str, name: str, preset: str | None = None, now: float = 0.0) -> dict:
    return {
        "id": pid,
        "kind": "timeline",
        "version": VERSION,
        "name": (name or "").strip()[:80] or "Nouveau montage",
        "created": now,
        "updated": now,
        "canvas": canvas_for(preset),
        "media": [],
        "tracks": default_tracks(),
        "clips": [],
        "markers": [],
        "settings": dict(SETTINGS_DEFAULTS),
        "export": None,
    }


# ------------------------------------------------------------ normalisation

def normalize_canvas(c) -> dict:
    c = c if isinstance(c, dict) else {}
    base = canvas_for(DEFAULT_CANVAS)
    w = _int(c.get("w"), base["w"], 16, MAX_SIDE)
    h = _int(c.get("h"), base["h"], 16, MAX_SIDE)
    fps = _int(c.get("fps"), 30, 1, 120)
    if fps not in FPS_CHOICES:
        fps = min(FPS_CHOICES, key=lambda f: abs(f - fps))
    # Les encodeurs H.264/HEVC veulent des dimensions paires.
    return {"w": w - w % 2, "h": h - h % 2, "fps": fps, "bg": _color(c.get("bg"), "#000000"),
            # « arrière-plan flou » : derrière la piste principale, sa propre image floutée
            "blur": _bool(c.get("blur"))}


def normalize_tracks(tracks) -> list[dict]:
    """Pistes valides, identifiants uniques, exactement une piste vidéo principale."""
    out: list[dict] = []
    seen: set[str] = set()
    for t in tracks if isinstance(tracks, list) else []:
        if not isinstance(t, dict) or t.get("kind") not in TRACK_KINDS:
            continue
        tid = _str(t.get("id"), "", 40) or new_id("t")
        while tid in seen:
            tid = new_id("t")
        seen.add(tid)
        out.append({
            "id": tid,
            "kind": t["kind"],
            "name": _str(t.get("name"), "", 40).strip() or _track_label(t["kind"], out),
            "main": _bool(t.get("main")) and t["kind"] == "video",
            "muted": _bool(t.get("muted")),
            "hidden": _bool(t.get("hidden")),
            "locked": _bool(t.get("locked")),
        })

    videos = [t for t in out if t["kind"] == "video"]
    if not videos:
        main = default_tracks()[0]
        while main["id"] in seen:
            main["id"] = new_id("t")
        # Sous les pistes texte et vidéo de superposition, au-dessus de l'audio.
        at = next((i for i, t in enumerate(out) if t["kind"] == "audio"), len(out))
        out.insert(at, main)
        videos = [main]
    mains = [t for t in videos if t["main"]]
    keep = mains[-1] if mains else videos[-1]   # la plus basse, comme dans CapCut
    for t in videos:
        t["main"] = t is keep
    return out


def _track_label(kind: str, existing: list[dict]) -> str:
    n = 1 + sum(1 for t in existing if t["kind"] == kind)
    return {"video": "Vidéo", "audio": "Audio", "text": "Texte"}[kind] + f" {n}"


def _media_bounds(media: dict | None) -> float:
    """Durée utilisable d'un média (0 = sans limite : image fixe)."""
    if not media or media.get("kind") == "image":
        return 0.0
    return max(0.0, _num(media.get("duration"), 0.0))


def normalize_clip(c: dict, track: dict, media: dict | None) -> dict | None:
    """Un clip propre, ou None s'il ne tient pas debout."""
    kind = c.get("kind")
    if kind not in CLIP_TRACK or CLIP_TRACK[kind] != track["kind"]:
        return None
    start = _num(c.get("start"), 0.0, 0.0, 24 * 3600)
    dur = _num(c.get("dur"), 0.0, 0.0, 24 * 3600)
    out: dict = {"id": _str(c.get("id"), "", 40), "track": track["id"], "kind": kind,
                 "start": start, "dur": dur}

    if kind in ("video", "audio", "image"):
        if not media:
            return None
        if kind == "image" and media.get("kind") != "image":
            return None
        if kind == "video" and media.get("kind") != "video":
            return None
        if kind == "audio" and not (media.get("kind") == "audio" or media.get("has_audio")):
            return None
        out["media"] = media["id"]
        speed = 1.0 if kind == "image" else _num(c.get("speed"), 1.0, MIN_SPEED, MAX_SPEED)
        src_in = 0.0 if kind == "image" else _num(c.get("in"), 0.0, 0.0)
        limit = _media_bounds(media)
        if limit:
            if src_in >= limit - MIN_DUR:
                return None
            # La fin lue dans la source ne dépasse pas le média.
            dur = min(dur, (limit + _END_SLACK - src_in) / speed)
        out.update({"in": _t(src_in), "speed": round(speed, 4)})
        if kind in ("video", "audio"):
            out.update({
                "volume": round(_num(c.get("volume"), 1.0, 0.0, 2.0), 3),
                "muted": _bool(c.get("muted")),
                "fade_in": round(_num(c.get("fade_in"), 0.0, 0.0, 30.0), 3),
                "fade_out": round(_num(c.get("fade_out"), 0.0, 0.0, 30.0), 3),
            })
        if kind == "video":
            # Son parti sur un clip audio lié : la vidéo se tait.
            out["detached"] = _bool(c.get("detached"))
        if kind in ("video", "image"):
            out.update(_transform(c))
            tr = c.get("trans")
            if isinstance(tr, dict) and tr.get("type") in TRANSITIONS:
                out["trans"] = {"type": tr["type"],
                                "dur": round(_num(tr.get("dur"), 0.5, 0.1, MAX_TRANSITION), 3)}
        if kind in ("video", "audio"):
            # passages retirés par la suppression des blancs (temps source) :
            # l'éditeur les affiche et peut les restaurer
            for key in ("gap", "tail"):
                r = c.get(key)
                if isinstance(r, dict):
                    a, b = _num(r.get("s"), -1.0, -1.0), _num(r.get("e"), -1.0, -1.0)
                    if b > a >= 0:
                        out[key] = {"s": _t(a), "e": _t(b)}
            fx = normalize_voice_fx(c.get("audio_fx"))
            if fx:
                out["audio_fx"] = fx
        out["link"] = _str(c.get("link"), "", 40)
    else:
        out.update(_text_fields(c))

    if kind in ("video", "image", "text"):
        out.update(_anims(c, "text" if kind == "text" else "media"))

    if dur < MIN_DUR:
        return None
    out["dur"] = _t(dur)
    out["start"] = _t(start)
    for key in ("fade_in", "fade_out"):
        if key in out:
            out[key] = round(min(out[key], dur), 3)
    return out


def _anims(c: dict, target: str) -> dict:
    """Animations d'entrée, de sortie et en boucle (engine/timeline/animations.py)."""
    from engine.timeline import animations
    out = {}
    for key, kind in (("anim_in", "in"), ("anim_out", "out"), ("anim_loop", "loop")):
        a = animations.normalize(c.get(key), kind, target)
        if a:
            out[key] = a
    return out


def _transform(c: dict) -> dict:
    out = {
        "x": round(_num(c.get("x"), 0.5, -2.0, 3.0), 4),
        "y": round(_num(c.get("y"), 0.5, -2.0, 3.0), 4),
        "scale": round(_num(c.get("scale"), 1.0, 0.05, 20.0), 4),
        "rotation": round(_num(c.get("rotation"), 0.0, -3600.0, 3600.0), 2),
        "opacity": round(_num(c.get("opacity"), 1.0, 0.0, 1.0), 3),
        "fit": c.get("fit") if c.get("fit") in ("cover", "contain") else "cover",
        "flip_h": _bool(c.get("flip_h")),
        "flip_v": _bool(c.get("flip_v")),
    }
    # sujet détouré (engine/pipeline/matting.py) : arrière-plan retiré, cadre qui suit le sujet
    for key in ("cutout", "follow"):
        if _bool(c.get(key)):
            out[key] = True
    # Réglages d'image (-1..1, 0 = neutre) : seuls les réglages actifs sont gardés.
    f = c.get("filters")
    if isinstance(f, dict):
        flt = {k: round(_num(f.get(k), 0.0, -1.0, 1.0), 3) for k in FILTERS}
        flt = {k: v for k, v in flt.items() if v}
        if flt:
            out["filters"] = flt
    return out


def _text_fields(c: dict) -> dict:
    """Texte libre ou sous-titre : apparence (mêmes champs que le mode short) + mots."""
    out: dict = {}
    for key, default in _TEXT_LOOK.items():
        v = c.get(key, default)
        if isinstance(default, bool):
            out[key] = _bool(v, default)
        elif isinstance(default, (int, float)):
            out[key] = round(_num(v, float(default), -10000, 10000), 4)
        elif key in COLOR_FIELDS:
            # `color2` vide = pas de dégradé
            out[key] = "" if (key == "color2" and not v) else _color(v, default or "#FFFFFF")
        else:
            out[key] = _str(v, default, 60)
    if out["mode"] not in MODES:
        out["mode"] = "word"
    out["opacity"] = min(1.0, max(0.0, out["opacity"]))
    for key, default in _TEXT_EXTRA.items():
        v = c.get(key, default)
        if isinstance(default, bool):
            out[key] = _bool(v, default)
        elif isinstance(default, float):
            out[key] = round(_num(v, default, -10000, 10000), 3)
        else:
            out[key] = _str(v, default, 16)
    out["words"] = _words(c.get("words"))
    if c.get("src_words") is not None:
        out["src_words"] = _words(c.get("src_words"))
    return out


def _words(words) -> list[dict]:
    out: list[dict] = []
    for w in words if isinstance(words, list) else []:
        if not isinstance(w, dict):
            continue
        text = _str(w.get("text"), "", 400)
        if not text.strip():
            continue
        start = _num(w.get("start"), 0.0, 0.0)
        end = max(start, _num(w.get("end"), start, 0.0))
        word = {"text": text, "start": _t(start), "end": _t(end)}
        if _bool(w.get("cut")):
            word["cut"] = True       # passage coupé : ni affiché ni exporté
        # Mot lié à la voix : sa place dans le média source.
        if w.get("m"):
            word["m"] = _str(w.get("m"), "", 40)
            word["s"] = _t(_num(w.get("s"), 0.0, 0.0))
            word["e"] = _t(max(word["s"], _num(w.get("e"), word["s"], 0.0)))
        out.append(word)
    return out


def normalize_clips(clips, tracks: list[dict], media: list[dict]) -> list[dict]:
    """Clips valides, identifiants uniques, aucun chevauchement dans une piste.

    Quand deux clips d'une piste se chevauchent, le premier est raccourci
    jusqu'au début du suivant (et disparaît s'il n'en reste rien) : c'est le
    clip posé le plus tard qui gagne, comme quand on dépose un clip par-dessus
    un autre.
    """
    by_track = {t["id"]: t for t in tracks}
    by_media = {m["id"]: m for m in media}
    out: list[dict] = []
    seen: set[str] = set()
    for c in clips if isinstance(clips, list) else []:
        if not isinstance(c, dict):
            continue
        track = by_track.get(c.get("track"))
        if not track:
            continue
        clean = normalize_clip(c, track, by_media.get(c.get("media")))
        if not clean:
            continue
        cid = clean["id"] or new_id("c")
        while cid in seen:
            cid = new_id("c")
        seen.add(cid)
        clean["id"] = cid
        out.append(clean)

    result: list[dict] = []
    for t in tracks:
        row = sorted((c for c in out if c["track"] == t["id"]), key=lambda c: (c["start"], c["id"]))
        kept: list[dict] = []
        for c in row:
            if kept:
                prev = kept[-1]
                if c["start"] < prev["start"] + prev["dur"] - 1e-4:
                    prev["dur"] = _t(c["start"] - prev["start"])
                    if prev["dur"] < MIN_DUR:
                        kept.pop()
            kept.append(c)
        result += kept
    return result


def normalize_settings(s) -> dict:
    s = s if isinstance(s, dict) else {}
    d = SETTINGS_DEFAULTS
    style = _str(s.get("style"), d["style"], 30)
    lang = s.get("language")
    return {
        "max_gap": round(_num(s.get("max_gap"), d["max_gap"], 0.1, 5.0), 3),
        "pad": round(_num(s.get("pad"), d["pad"], 0.0, 0.5), 3),
        "fillers": _bool(s.get("fillers"), d["fillers"]),
        "words_per_line": _int(s.get("words_per_line"), d["words_per_line"], 1, 12),
        "word_by_word": _bool(s.get("word_by_word"), d["word_by_word"]),
        "max_chars": _int(s.get("max_chars"), d["max_chars"], 6, 60),
        "style": style if style in CAPTION_PRESETS else d["style"],
        "emojis": _bool(s.get("emojis"), d["emojis"]),
        "language": _str(lang, "", 8).strip().lower() or None if lang else None,
        "model": _str(s.get("model"), d["model"], 60) or d["model"],
        "device": s.get("device") if s.get("device") in ("auto", "cuda", "cpu") else "auto",
        "loudness": _bool(s.get("loudness"), d["loudness"]),
        "auto": normalize_auto(s.get("auto")),
    }


def normalize_auto(a) -> dict:
    a = a if isinstance(a, dict) else {}
    d = AUTO_DEFAULTS
    out = {k: _bool(a.get(k), d[k]) for k, v in d.items() if isinstance(v, bool)}
    out["rhythm"] = a.get("rhythm") if a.get("rhythm") in ("calm", "normal", "punchy") else d["rhythm"]
    out["max_duration"] = _int(a.get("max_duration"), d["max_duration"], 0, 600)
    return out


def normalize_voice_fx(fx) -> dict:
    """Traitement de la voix d'un clip : seules les valeurs actives restent.
    Les anciens interrupteurs (`denoise`, `voice` booléens) sont convertis."""
    if not isinstance(fx, dict):
        return {}
    src = dict(fx)
    if src.get("denoise") is True:
        src["denoise"] = 0.7
    if src.pop("voice", None) is True:
        src.setdefault("lowcut", True)
        src.setdefault("compress", 0.5)
        src.setdefault("clarity", 0.6)
    out: dict = {}
    for key, default in VOICE_FX.items():
        v = src.get(key, default)
        if isinstance(default, bool):
            if _bool(v):
                out[key] = True
        else:
            x = round(_num(v, 0.0, 0.0, 1.0), 2)
            if x > 0:
                out[key] = x
    preset = _str(src.get("preset"), "", 24).strip()
    if out and preset:
        out["preset"] = preset
    return out


def normalize_markers(markers) -> list[dict]:
    out = []
    for m in markers if isinstance(markers, list) else []:
        if not isinstance(m, dict):
            continue
        out.append({"id": _str(m.get("id"), "", 40) or new_id("k"),
                    "t": _t(_num(m.get("t"), 0.0, 0.0, 24 * 3600)),
                    "label": _str(m.get("label"), "", 80),
                    "color": _color(m.get("color"), "#F23A52")})
    return sorted(out, key=lambda m: m["t"])


def apply_client_state(state: dict, body: dict) -> dict:
    """Intègre la sauvegarde de l'éditeur à l'état du projet, en la validant.

    Seules les parties que l'éditeur possède sont reprises ; un champ absent du
    corps laisse la valeur actuelle.
    """
    if not isinstance(body, dict):
        return state
    if isinstance(body.get("name"), str):
        state["name"] = body["name"].strip()[:80] or state.get("name") or "Nouveau montage"
    if "canvas" in body:
        state["canvas"] = normalize_canvas(body["canvas"])
    if "settings" in body:
        state["settings"] = normalize_settings({**state.get("settings", {}), **(body["settings"] or {})})
    if "tracks" in body:
        state["tracks"] = normalize_tracks(body["tracks"])
    if "markers" in body:
        state["markers"] = normalize_markers(body["markers"])
    if "clips" in body or "tracks" in body:
        clips = body["clips"] if "clips" in body else state.get("clips", [])
        state["clips"] = normalize_clips(clips, state["tracks"], state.get("media", []))
    return state


def duration(clips: list[dict]) -> float:
    """Durée du montage : fin du dernier clip, toutes pistes confondues."""
    return round(max((c["start"] + c["dur"] for c in clips), default=0.0), 4)
