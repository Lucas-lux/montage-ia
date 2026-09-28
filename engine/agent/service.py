"""Ce que font les outils des agents IA, sur un projet timeline du moteur.

Chaque outil lit le montage, le modifie avec `edit.py`, puis l'enregistre
comme une sauvegarde du studio : validation par `timeline.model`, révision +1.
Le studio ouvert sur ce projet recharge tout seul (studio/sync.js), et l'état
d'avant chaque outil est gardé (`undo`).

Temps : les plages de SOURCE (`start`/`end` d'un segment, phrases de la
transcription) sont en secondes dans le média ; tout le reste (textes, plans
de coupe, sons) est en secondes sur la TIMELINE. `timeline_text` dit où tombe
chaque phrase une fois le montage fait.

Les messages d'erreur s'adressent à l'agent : ils disent quoi faire ensuite.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import threading
import time
import traceback
import unicodedata

from engine.agent import edit as E
from engine.pipeline import style_presets
from engine.timeline import ai, autoedit, jobs, model

DOC_KEYS = ("name", "canvas", "tracks", "clips", "markers", "settings")
MAX_HISTORY = 40
HISTORY: dict[str, list[dict]] = {}
JOBS: dict[str, dict] = {}

TITLES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "web", "studio", "titles.json")

# Traitements de la voix (mêmes presets que studio/voice.js).
VOICE_PRESETS: dict[str, dict] = {
    "brut": {},
    "clair": {"lowcut": True, "clarity": 0.6, "compress": 0.4, "deess": 0.3},
    "voixoff": {"denoise": 0.7, "lowcut": True, "gate": True, "deess": 0.4, "compress": 0.6, "clarity": 0.5,
                "level": True},
    "podcast": {"denoise": 0.5, "lowcut": True, "deess": 0.4, "compress": 0.6, "clarity": 0.4, "warmth": 0.4,
                "level": True},
    "radio": {"lowcut": True, "deess": 0.4, "compress": 0.9, "clarity": 0.7, "warmth": 0.5, "level": True},
}

# Textes du montage automatique (studio/autoedit.js).
HOOK_LOOK = {"font": "Arial Black", "size": 100, "color": "#FFFFFF", "outline_col": "#000000", "outline": 7,
             "shadow": 3, "box": False, "box_alpha": 0.25, "hl": "#FFFFFF", "bold": True, "upper": False}
TEXT_LOOK = {"font": "Arial Black", "size": 64, "color": "#FFE500", "outline_col": "#000000", "outline": 6,
             "shadow": 2, "box": False, "box_alpha": 0.25, "hl": "#FFE500", "bold": True, "upper": True}
TITLE_DUR = 3.2
SENTENCE_LEAD, SENTENCE_TAIL = 0.15, 0.35      # s gardées autour d'une phrase choisie
LOOK_FIELDS = tuple(style_presets.BASE.keys())
MEDIA_FIELDS = ("x", "y", "scale", "rotation", "opacity", "fit", "flip_h", "flip_v", "cutout", "follow",
                "volume", "muted", "fade_in", "fade_out", "speed", "filters", "border", "border_col")
BANDS = {"top_band": 0.05, "center_band": None, "bottom_band": 0.44}   # bord haut (fraction du cadre)
BAND_WIDTH = 0.92
POSITIONS = {           # plan de coupe « incrusté » : centre (x, y) et taille par défaut
    "center": (0.5, 0.5), "top": (0.5, 0.27), "bottom": (0.5, 0.73), "left": (0.3, 0.5), "right": (0.7, 0.5),
    "top_left": (0.28, 0.2), "top_right": (0.72, 0.2), "bottom_left": (0.28, 0.8), "bottom_right": (0.72, 0.8),
}


class AgentError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------- document

def doc_of(proj) -> dict:
    with proj.lock:
        return copy.deepcopy({k: proj.state[k] for k in DOC_KEYS})


def commit(proj, doc: dict, note: str) -> int:
    """Enregistre le montage modifié (une étape d'annulation pour l'agent)."""
    E.clean(doc)
    with proj.lock:
        hist = HISTORY.setdefault(proj.id, [])
        hist.append({"note": note, "doc": doc_of(proj), "at": time.time()})
        del hist[:-MAX_HISTORY]
        proj.apply(doc)
        return proj.rev


def undo(proj) -> dict:
    hist = HISTORY.get(proj.id) or []
    if not hist:
        raise AgentError("Nothing to undo: no change made by the agent since the app started.", 409)
    last = hist.pop()
    with proj.lock:
        proj.apply(last["doc"])
    return {"undone": last["note"], "rev": proj.rev, "remaining": len(hist)}


# --------------------------------------------------------------- médias

def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def media_names(proj) -> str:
    return ", ".join(f"{m['id']} ({m['name']})" for m in proj.state["media"]) or "none"


def resolve_media(proj, ref, kinds: tuple[str, ...] | None = None) -> dict:
    """Média désigné par son id, son nom ou un bout de son nom."""
    media = [m for m in proj.state["media"] if not kinds or m["kind"] in kinds]
    if not ref:
        if len(media) == 1:
            return media[0]
        raise AgentError(f"Say which media (id or name). Media: {media_names(proj)}")
    ref = str(ref).strip()
    for m in media:
        if m["id"] == ref:
            return m
    exact = [m for m in media if m["name"] == ref or os.path.basename(m["path"]) == ref]
    if exact:
        return exact[0]
    part = [m for m in media if _fold(ref) in _fold(m["name"])]
    if len(part) == 1:
        return part[0]
    raise AgentError(f"Media not found: {ref!r}" + (f" (kind {'/'.join(kinds)})" if kinds else "")
                     + f". Media: {media_names(proj)}", 404)


def require_ready(m: dict) -> None:
    if m.get("status") == "error":
        raise AgentError(f"Media {m['name']} could not be prepared: {m.get('error')}", 409)
    if m.get("status") != "ready":
        raise AgentError(f"Media {m['name']} is still being prepared ({m.get('progress', 0)} %): "
                         "call wait(what='media') first.", 409)


def require_transcript(m: dict) -> None:
    require_ready(m)
    tr = m.get("transcript") or {}
    if tr.get("status") == "done":
        return
    if tr.get("status") in ("queued", "running"):
        raise AgentError(f"Media {m['name']} is being transcribed: call wait(what='transcription').", 409)
    if tr.get("status") == "error":
        raise AgentError(f"Transcription of {m['name']} failed: {tr.get('error')}", 409)
    raise AgentError(f"Media {m['name']} is not transcribed: call transcribe first.", 409)


MEDIA_REF = re.compile(r"(?<=[\"'(])media:([\w-]+)")


def media_refs(proj, markup: str) -> str:
    """`src="media:m1234abcd"` dans un visuel → l'adresse file:/// du média du projet
    (l'agent n'a pas à recopier de chemin Windows)."""
    def url(mo: re.Match) -> str:
        m = next((x for x in proj.state["media"] if x["id"] == mo.group(1)), None)
        if not m:
            raise AgentError(f"Unknown media {mo.group(1)} in the visual. Media: {media_names(proj)}")
        return pathlib.Path(m["path"]).resolve().as_uri()
    return MEDIA_REF.sub(url, markup)


def media_brief(m: dict) -> dict:
    tr = m.get("transcript") or {}
    sub = m.get("subject") or {}
    out = {"id": m["id"], "name": m["name"], "kind": m["kind"], "status": m.get("status"),
           "duration": round(float(m.get("duration") or 0), 3), "w": m.get("w"), "h": m.get("h"),
           "has_audio": bool(m.get("has_audio")), "path": m.get("path"),
           "transcript": {k: tr[k] for k in ("status", "count", "language", "error", "by_takes") if tr.get(k)}}
    if m.get("status") in ("pending", "processing"):
        out["progress"] = m.get("progress", 0)
    if m.get("error"):
        out["error"] = m["error"]
    if sub:
        out["subject"] = {k: sub[k] for k in ("status", "progress", "error", "x", "y", "t") if k in sub}
    if m.get("credit"):
        out["credit"] = m["credit"]
    return out


class Words:
    """Mots et phrases des médias, lus une fois par outil."""

    def __init__(self, proj) -> None:
        self.proj = proj
        self._words: dict[str, list] = {}
        self._sents: dict[str, list] = {}
        self._cut: dict[str, list] = {}

    def words(self, mid: str) -> list[dict]:
        if mid not in self._words:
            m = self.proj.media(mid) or {}
            done = (m.get("transcript") or {}).get("status") == "done"
            self._words[mid] = ai.load_words(self.proj, mid) if done else []
        return self._words[mid]

    def sentences(self, mid: str) -> list[dict]:
        if mid not in self._sents:
            self._sents[mid] = autoedit.sentences(self.words(mid))
        return self._sents[mid]

    def sounding(self, mid: str) -> list[dict]:
        """Mots avec leurs bornes SONORES (`cs`, `ce`) : la voix dure souvent
        après la fin datée par Whisper (engine/timeline/sound.py)."""
        if mid not in self._cut:
            from engine.timeline import sound
            self._cut[mid] = sound.cut_words(self.proj, mid, self.words(mid))
        return self._cut[mid]

    def for_cuts(self, mid: str) -> list[dict]:
        """Mots à couper : début et fin sonores à la place des dates de Whisper."""
        return [dict(w, start=w["cs"], end=w["ce"]) for w in self.sounding(mid)]


def face_of(proj, m: dict) -> dict | None:
    from engine.timeline.api import _face
    try:
        return _face(proj, m)
    except Exception:  # noqa: BLE001 - YuNet absent ou proxy illisible : cadrage centré
        return None


def face_track_of(proj, m: dict) -> list:
    """Visage au fil du rush (2 fois par seconde), en cache à côté du média."""
    if m.get("kind") != "video":
        return []
    folder = proj.media_folder(m["id"])
    cache = os.path.join(folder, "face_track.json")
    try:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    proxy = os.path.join(folder, m.get("proxy_file") or "")
    track = autoedit.face_track(proxy if os.path.isfile(proxy) else m["path"])
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(track, f)
    except OSError:
        pass
    return track


def face_finder(proj, media: dict[str, dict]):
    """`face(mid, a, b)` : le visage d'une plage de source (suivi), sinon celui du rush."""
    tracks: dict[str, list] = {}
    anchors: dict[str, dict | None] = {}

    def face(mid: str, a: float, b: float) -> dict | None:
        m = media.get(mid)
        if not m or m.get("kind") != "video":
            return None
        if mid not in tracks:
            tracks[mid] = face_track_of(proj, m)
            anchors[mid] = None if tracks[mid] else face_of(proj, m)
        return autoedit.face_in(tracks[mid], a, b, anchors[mid])
    return face


# ----------------------------------------------------------- lecture

def clip_brief(c: dict, media: dict[str, dict]) -> dict:
    b = {"id": c["id"], "kind": c["kind"], "track": c["track"], "start": E.r4(c["start"]),
         "end": E.r4(E.clip_end(c))}
    if c.get("media"):
        m = media.get(c["media"]) or {}
        b["media"] = c["media"]
        b["name"] = m.get("name", "")
        if c["kind"] != "image":
            b["in"], b["out"] = E.r4(c["in"]), E.r4(E.src_end(c))
            if (c.get("speed") or 1) != 1:
                b["speed"] = c["speed"]
        for k, default in (("scale", 1), ("x", 0.5), ("y", 0.5), ("rotation", 0), ("opacity", 1), ("fit", "cover"),
                           ("volume", 1), ("muted", False), ("cutout", False), ("follow", False),
                           ("fade_in", 0), ("fade_out", 0), ("border", 0)):
            if k in c and c[k] != default:
                b[k] = c[k]
        if c.get("trans"):
            b["transition"] = c["trans"]
        if c.get("audio_fx"):
            b["voice"] = c["audio_fx"].get("preset") or "custom"
    if c["kind"] == "text":
        b["text"] = " ".join(w["text"] for w in c.get("words") or [] if not w.get("cut"))
        if c.get("auto"):
            b["caption"] = True
        if c.get("gone"):
            b["gone"] = True
        for k in ("font", "size", "color", "y"):
            b[k] = c.get(k)
    for k in ("anim_in", "anim_out", "anim_loop"):
        if c.get(k):
            b[k] = c[k]
    return b


def summary(proj, with_captions: bool = False) -> dict:
    with proj.lock:
        st = copy.deepcopy(proj.state)
    media = {m["id"]: m for m in st["media"]}
    tracks = []
    for t in st["tracks"]:
        clips = sorted((c for c in st["clips"] if c["track"] == t["id"]), key=lambda c: c["start"])
        caps = [c for c in clips if c["kind"] == "text" and c.get("auto")]
        entry = {"id": t["id"], "name": t["name"], "kind": t["kind"], "main": bool(t.get("main"))}
        if t.get("muted"):
            entry["muted"] = True
        if t.get("hidden"):
            entry["hidden"] = True
        if caps and not with_captions and len(caps) == len(clips):
            live = [c for c in caps if not c.get("gone")]
            entry["captions"] = {"lines": len(live), "style": st["settings"].get("style"),
                                 "word_by_word": st["settings"].get("word_by_word"),
                                 "first": clip_brief(live[0], media) if live else None}
        else:
            entry["clips"] = [clip_brief(c, media) for c in clips]
        tracks.append(entry)
    return {"id": st["id"], "name": st["name"], "rev": int(st.get("rev") or 0), "canvas": st["canvas"],
            "duration": model.duration(st["clips"]), "media": [media_brief(m) for m in st["media"]],
            "tracks": tracks, "markers": st.get("markers") or [],
            "settings": {k: st["settings"].get(k) for k in ("style", "word_by_word", "words_per_line", "loudness",
                                                           "language")},
            "export": st.get("export"), "task": dict(proj.task)}


def transcript(proj, ref=None, start=None, end=None, with_words: bool = False) -> dict:
    from engine.timeline.api import wave_of
    m = resolve_media(proj, ref, ("video", "audio"))
    require_transcript(m)
    words = ai.load_words(proj, m["id"])
    wave, rate = wave_of(proj, m)
    an = autoedit.analyze(words, wave, rate)
    fluff, retakes = set(an["fluff"]), set(an["retakes"])
    lo = -1e9 if start is None else float(start)
    hi = 1e9 if end is None else float(end)
    out = []
    for s in an["_sents"]:
        if s["end"] <= lo or s["start"] >= hi:
            continue
        flags = [f for f, on in (("fluff", s["i"] in fluff), ("retake", s["i"] in retakes)) if on]
        out.append({"i": s["i"], "start": s["start"], "end": s["end"], "text": s["text"],
                    "score": s.get("score", 0), "flags": flags})
    res = {"media": media_brief(m), "language": (m.get("transcript") or {}).get("language") or "",
           "sentences_total": len(an["_sents"]), "sentences": out}
    if with_words:
        res["words"] = [[w["text"], w["start"], w["end"]] for w in words if lo < w["end"] and w["start"] < hi]
    return res


def plan(proj, ref=None, opts: dict | None = None) -> dict:
    """Analyse du montage automatique : accroche, passages faibles, moments forts."""
    from engine.timeline.api import media_plan
    o = opts or {}
    m = resolve_media(proj, ref, ("video", "audio"))
    require_transcript(m)
    p = media_plan(proj, m["id"], {"llm": bool(o.get("llm", False)), "trim": bool(o.get("trim", True)),
                                   "cold_open": bool(o.get("cold_open", False)),
                                   "max_duration": float(o.get("max_duration") or 0)})
    keep = [s for s in p.get("sentences") or [] if s["i"] not in set(p.get("drop_sentences") or [])]
    return {"media": m["id"], "name": m["name"], "llm": bool(p.get("llm")), "hook": p.get("hook"),
            "title": p.get("title"), "drop_sentences": p.get("drop_sentences") or [],
            "fluff": p.get("fluff") or [], "retakes": p.get("retakes") or [],
            "highlights": p.get("highlights") or [], "texts": p.get("texts") or [], "face": p.get("face"),
            "kept_duration": round(sum(s["end"] - s["start"] for s in keep), 2)}


def timeline_text(proj) -> list[dict]:
    doc = doc_of(proj)
    W = Words(proj)
    return E.timeline_sentences(doc, W.sentences)


def timeline_words(proj) -> list[dict]:
    return E.timeline_words(doc_of(proj), Words(proj).words)


# ---------------------------------------------------------------- styles

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(s)).strip("_")


def titles() -> list[dict]:
    with open(TITLES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for s in data["styles"]:
        look = {"mode": "none", "pop": False, "bold": True, "upper": False, "shadow": 0, "box": False,
                "box_alpha": 0.25, "hl": s["look"].get("color") or "#FFFFFF", **s["look"]}
        out.append({"name": _slug(s["label"]), "label": s["label"], "hint": s.get("hint", ""),
                    "group": s.get("group", "base"), "look": look})
    return out


def style_look(name: str | None, default: dict | None = None) -> dict:
    """Apparence d'un style de titre (titles.json) ou de sous-titre (style_presets)."""
    if not name:
        return dict(default or titles()[0]["look"])
    key = _slug(name)
    for t in titles():
        if t["name"] == key:
            return dict(t["look"])
    if name in style_presets.PRESETS or key in style_presets.PRESETS:
        n = name if name in style_presets.PRESETS else key
        return {**style_presets.preset(n), **style_presets.preset_anims(n)}
    raise AgentError(f"Unknown style {name!r}: see catalog(what='title_styles') or catalog(what='caption_styles').")


def _anim(value, kind: str, target: str):
    """Animation donnée par son nom (« pop ») ou `{type, dur|speed}`, vérifiée."""
    from engine.timeline import animations
    if value in (None, "", False):
        return None
    v = {"type": value} if isinstance(value, str) else dict(value)
    if v.get("type") == "custom":
        a = animations.normalize(v, kind, target)
        if not a:
            raise AgentError("Custom animation: give kf = [[0, {props}], …, [1, {props}]] with props among "
                             "o (opacity 0..1), dx/dy (fraction of the frame), s/sx/sy (scale), r (degrees), "
                             "b (blur px), an optional curve per keyframe, and dur (in/out) or period/span (loop).")
        return a
    d = animations.definitions().get(str(v.get("type") or ""))
    if not d or d["kind"] != kind or target not in d.get("for", ["text", "media"]):
        names = sorted(k for k, x in animations.definitions().items()
                       if x["kind"] == kind and target in x.get("for", ["text", "media"]))
        raise AgentError(f"Unknown {kind} animation {v.get('type')!r} for {target}. Choices: {', '.join(names)}")
    return animations.normalize(v, kind, target)


def _apply_anims(c: dict, body: dict, target: str) -> None:
    for key, kind in (("anim_in", "in"), ("anim_out", "out"), ("anim_loop", "loop")):
        if key in body:
            a = _anim(body[key], kind, target)
            if a:
                c[key] = a
            else:
                c.pop(key, None)


def _font_warning(font: str | None) -> str | None:
    if not font:
        return None
    from engine.pipeline import fonts
    names = {f["name"] for f in fonts.bundled()} | set(fonts.SYSTEM_FONTS)
    if font in names:
        return None
    return f"Font {font!r} is not bundled with Montage IA: it may be replaced at export. See catalog(what='fonts')."


# ------------------------------------------------------------- montage

def _indices(value) -> list[int]:
    """[3, 4, 9], "3-8", "3,4,9", "3-5,9" → liste d'indices, dans l'ordre donné."""
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        out = []
        for v in value:
            out += _indices(v)
        return out
    out = []
    for part in str(value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out += list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
        elif part.isdigit():
            out.append(int(part))
        else:
            raise AgentError(f"Bad sentence list {value!r}: use [3, 4, 9] or \"3-8\".")
    return out


_SEG_KEYS = ("zoom", "x", "y", "fit", "speed", "transition", "transition_duration")


def expand_segment(proj, W: Words, seg: dict) -> list[dict]:
    """Un segment de l'agent → plages de source (une par suite de phrases)."""
    if not isinstance(seg, dict):
        raise AgentError("Each segment is an object: {media, start, end} or {media, sentences}.")
    m = resolve_media(proj, seg.get("media"), ("video", "image"))
    require_ready(m)
    extra = {k: seg[k] for k in _SEG_KEYS if seg.get(k) is not None}
    if extra.get("transition") and extra["transition"] not in model.TRANSITIONS:
        raise AgentError(f"Unknown transition {extra['transition']!r}. Choices: {', '.join(model.TRANSITIONS)}")
    if m["kind"] == "image":
        return [{"media": m["id"], "duration": float(seg.get("duration") or E.IMAGE_DUR), **extra}]
    dur = float(m.get("duration") or 0)
    if seg.get("takes") is not None:
        tk = load_takes(proj, m)
        if not tk:
            raise AgentError(f"No take list for {m['name']}: call derush(media) first.", 409)
        numbers = _indices(seg["takes"])
        known = {t["n"] for t in tk["takes"]}
        bad = [n for n in numbers if n not in known]
        if bad:
            raise AgentError(f"Takes {bad} do not exist in {m['name']} (1..{len(tk['takes'])}).")
        from engine.timeline import takes as T
        return [{"media": m["id"], "start": round(a, 3), "end": round(min(b, dur), 3), **extra}
                for a, b in T.kept_ranges(tk["takes"], numbers)]
    if seg.get("sentences") is not None:
        require_transcript(m)
        sents = W.sentences(m["id"])
        by = {s["i"]: s for s in sents}
        idx = _indices(seg["sentences"])
        bad = [i for i in idx if i not in by]
        if bad:
            raise AgentError(f"Sentences {bad} do not exist in {m['name']} (0..{len(sents) - 1}).")
        runs: list[list[int]] = []
        for i in idx:
            if runs and i == runs[-1][-1] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        sounding = {(w["start"], w["end"]): w for w in W.sounding(m["id"])}
        edge = lambda w, k, d: sounding.get((w["start"], w["end"]), w).get(k, d)  # noqa: E731
        out = []
        for run in runs:
            first, last = by[run[0]], by[run[-1]]
            prev_end = edge(by[run[0] - 1]["words"][-1], "ce", by[run[0] - 1]["end"]) if run[0] - 1 in by else 0.0
            next_start = edge(by[run[-1] + 1]["words"][0], "cs", by[run[-1] + 1]["start"]) \
                if run[-1] + 1 in by else dur
            real_start = edge(first["words"][0], "cs", first["start"])
            real_end = edge(last["words"][-1], "ce", last["end"])
            a = max(prev_end, real_start - SENTENCE_LEAD, 0.0)
            b = min(next_start, max(last["end"] + SENTENCE_TAIL, real_end + END_TAIL), dur)
            out.append({"media": m["id"], "start": round(a, 3), "end": round(b, 3), **extra})
        return out
    a = float(seg.get("start") or 0.0)
    b = float(seg["end"]) if seg.get("end") is not None else dur
    if b <= a:
        raise AgentError(f"Segment of {m['name']}: end ({b}) must be after start ({a}).")
    a, b = snap_to_words(W.sounding(m["id"]), a, b)
    return [{"media": m["id"], "start": a, "end": min(b, dur), **extra}]


END_TAIL = 0.12          # s gardées après la fin SONORE du dernier mot d'une plage
HEAD_PAD = 0.04          # s gardées avant le début sonore du premier mot


# ------------------------------------------------------------- dérush

def takes_path(proj, mid: str) -> str:
    return os.path.join(proj.media_folder(mid), "takes.json")


def load_takes(proj, m: dict) -> dict | None:
    try:
        with open(takes_path(proj, m["id"]), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_takes(proj, mid: str, res: dict) -> None:
    path = takes_path(proj, mid)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def derush(proj, body: dict) -> dict:
    """Prises du rush et décisions proposées (engine/timeline/takes.py), sur la
    transcription actuelle. Rapide : la transcription prise par prise
    (`precise`) passe par `derush_job`."""
    from engine.timeline import takes as T
    m = resolve_media(proj, body.get("media"), ("video", "audio"))
    require_transcript(m)
    if not m.get("has_audio"):
        raise AgentError(f"{m['name']} has no sound: nothing to derush.")
    levels = T.media_levels(proj, m)
    res = T.derush(levels, ai.load_words(proj, m["id"]))
    res.update(media=m["id"], precise=bool((m.get("transcript") or {}).get("by_takes")))
    _save_takes(proj, m["id"], res)
    return res


def derush_job(job: dict, proj, body: dict) -> dict:
    """Dérush précis : prises mesurées, puis chaque prise transcrite seule (une
    phrase redite n'est plus avalée par Whisper), et ces mots deviennent la
    transcription du rush (l'ancienne est gardée : words_whole.json)."""
    from engine.pipeline.captions import clean_words
    from engine.pipeline.transcribe import transcribe
    from engine.timeline import takes as T
    m = resolve_media(proj, body.get("media"), ("video", "audio"))
    require_transcript(m)
    job["message"] = "Measuring the takes…"
    levels = T.media_levels(proj, m)
    words = ai.load_words(proj, m["id"])
    first = T.derush(levels, words)
    clips = [(t["in"], t["out"]) for t in first["takes"]]
    if not clips:
        raise AgentError(f"No speech found in {m['name']}.")
    job["message"] = f"Transcribing {len(clips)} takes one by one…"
    job["pct"] = 10
    settings = proj.state.get("settings") or {}
    meta: dict = {}
    raw = transcribe(m["path"], settings.get("model") or "large-v3-turbo", settings.get("device") or "auto",
                     "auto", settings.get("language") or (m.get("transcript") or {}).get("language") or None,
                     info=meta, clips=clips)
    new = [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)} for w in clean_words(raw)]
    if not new:
        raise AgentError(f"The take-by-take transcription of {m['name']} found no words.")
    folder = proj.media_folder(m["id"])
    whole = os.path.join(folder, "words_whole.json")
    if not os.path.isfile(whole) and os.path.isfile(ai.words_path(proj, m["id"])):
        import shutil
        shutil.copyfile(ai.words_path(proj, m["id"]), whole)
    lang = meta.get("language") or (m.get("transcript") or {}).get("language") or ""
    path = ai.words_path(proj, m["id"])
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"words": new, "language": lang, "by_takes": True}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    tr = dict(m.get("transcript") or {})
    tr.update(status="done", count=len(new), language=lang, by_takes=True)
    proj.update_media(m["id"], transcript=tr)
    res = T.derush(levels, new, takes=first["takes"])
    res.update(media=m["id"], precise=True, words_before=len(words), words_after=len(new))
    _save_takes(proj, m["id"], res)
    return res


def snap_to_words(words: list[dict], a: float, b: float) -> tuple[float, float]:
    """Une coupe tombée au milieu d'un mot glisse à son bord : le mot est gardé
    s'il est surtout dans la plage, sinon laissé dehors (pas de syllabe coupée,
    pas de sous-titre d'un dixième de seconde). Les bords sont ceux du SON du
    mot (`cs`/`ce` si connus) ; une fin gardée laisse END_TAIL de souffle."""
    for i, w in enumerate(words):
        s0, e0 = w.get("cs", w["start"]), w.get("ce", w["end"])
        half = (e0 - s0) / 2
        prev_e = words[i - 1].get("ce", words[i - 1]["end"]) if i else 0.0
        next_s = words[i + 1].get("cs", words[i + 1]["start"]) if i + 1 < len(words) else e0 + 1.0
        if s0 < a < e0:
            a = e0 if e0 - a < half else max(prev_e, s0 - HEAD_PAD)
        if s0 < b < e0:
            b = max(prev_e, s0 - 0.02) if b - s0 < half else min(next_s, e0 + END_TAIL)
        elif e0 <= b < e0 + END_TAIL and b < next_s:
            b = min(next_s, e0 + END_TAIL)            # fin posée pile sur le mot : un peu d'air
    return round(a, 3), round(max(b, a), 3)


def _voice(proj, doc: dict, preset: str | None) -> dict | None:
    """Traitement de la voix de la piste principale. « auto » : mesuré sur chaque
    rush (engine/timeline/sound.py), musique de fond baissée sous la voix."""
    if not preset or preset == "none":
        return None
    if preset == "auto":
        from engine.timeline import sound
        plan = sound.optimize(proj, doc["clips"])
        sound.apply(doc, plan)
        return plan
    if preset not in VOICE_PRESETS:
        raise AgentError(f"Unknown voice preset {preset!r}. Choices: auto, {', '.join(VOICE_PRESETS)}")
    fx = dict(VOICE_PRESETS[preset])
    for c in E.main_clips(doc):
        if c["kind"] == "video":
            c["audio_fx"] = {**fx, "preset": preset} if fx else {}
            for p in E.partners(doc, c):
                if p["kind"] == "audio":
                    p["audio_fx"] = dict(c["audio_fx"])
    return None


def assemble_doc(proj, doc: dict, W: Words, segments: list[dict], opts: dict,
                 highlights: dict[str, list] | None = None) -> dict:
    """Pose les segments sur la piste principale, rythme, voix, sous-titres."""
    media = {m["id"]: m for m in proj.state["media"]}
    rs = bool(opts.get("remove_silences", True))
    rf = bool(opts.get("remove_fillers", True))
    rhythm = str(opts.get("rhythm") or "normal")
    if rhythm not in ("none", *E.RHYTHM):
        raise AgentError(f"Unknown rhythm {rhythm!r}: none, calm, normal, punchy or dynamic.")
    for s in segments:
        m = media[s["media"]]
        if m["kind"] == "video" and (rs or rf) and m.get("has_audio"):
            require_transcript(m)
    R = E.RHYTHM.get(rhythm, E.RHYTHM["normal"])
    res = E.assemble(doc, media, W.for_cuts, segments, remove_silences=rs, remove_fillers=rf,
                     max_gap=float(opts.get("max_gap") or 0.5), pad=float(opts.get("pad") or 0.08),
                     tail=R["tail"], replace=opts.get("replace", True) is not False)
    used = sorted({c["media"] for c in res["pieces"] if media[c["media"]]["kind"] == "video"})
    faces = face_finder(proj, media)
    fixed = {c["id"] for c in res["pieces"] if segments[c["_seg"]].get("zoom") is not None
             or segments[c["_seg"]].get("x") is not None}
    zooms = 0
    if rhythm != "none":
        if rhythm == "dynamic":
            cut = {mid: E.clause_points(W.sounding(mid)) for mid in used}
        else:
            cut = {mid: autoedit._cutpoints(W.sentences(mid)) for mid in used}
        zooms = E.apply_rhythm(doc, media, cut, faces, rhythm, highlights, skip=fixed)
    else:
        for c in E.main_clips(doc):
            if c["kind"] == "video" and c["id"] not in fixed and c.get("fit") != "contain":
                face = faces(c["media"], c["in"], E.src_end(c))
                c["x"], c["y"] = E.position(face, media[c["media"]], doc["canvas"], c["scale"])
    sound_plan = _voice(proj, doc, opts.get("voice", "auto"))
    if opts.get("loudness", True):
        doc["settings"]["loudness"] = True
    caps = opts.get("captions")
    n_caps = None
    if caps:
        n_caps = make_captions(proj, doc, W, caps if isinstance(caps, dict) else {})
    else:
        E.reflow_captions(doc)
    return {"pieces": len(E.main_clips(doc)), "removed_seconds": res["removed"], "zooms": zooms,
            "captions": n_caps, "sound": (sound_plan or {}).get("report")}


def build_edit(proj, body: dict) -> dict:
    segs = body.get("segments")
    if not isinstance(segs, list) or not segs:
        raise AgentError("`segments` must be a non-empty list of {media, start, end} or {media, sentences}.")
    W = Words(proj)
    segments = [x for s in segs for x in expand_segment(proj, W, s)]
    if not segments:
        raise AgentError("The segments select nothing.")
    with proj.lock:
        doc = doc_of(proj)
        rep = assemble_doc(proj, doc, W, segments, body)
        rev = commit(proj, doc, "build_edit")
    return {"rev": rev, "duration": model.duration(proj.state["clips"]), **rep,
            "timeline_text": E.timeline_sentences(doc_of(proj), W.sentences)}


def make_captions(proj, doc: dict, W: Words, opts: dict) -> int:
    """Sous-titres de la voix du montage (remplace les précédents)."""
    keys = ("style", "words_per_line", "max_chars", "emojis", "word_by_word", "language")
    settings = {**doc["settings"], **{k: opts[k] for k in keys if opts.get(k) is not None}}
    if settings.get("style") and settings["style"] not in style_presets.PRESETS:
        raise AgentError(f"Unknown caption style {settings['style']!r}: see catalog(what='caption_styles').")
    doc["settings"] = model.normalize_settings(settings)
    media = {m["id"]: m for m in proj.state["media"]}
    clips = [c for c in doc["clips"] if c.get("kind") in ("video", "audio") and not c.get("muted")]
    voice = ai._voice_clips(clips, media)
    for c in voice:
        require_transcript(media[c["media"]])
    caps = ai.build_captions(clips, media, W.words, doc["settings"])
    look = {k: v for k, v in (opts.get("look") or {}).items() if k in LOOK_FIELDS}
    for k in ("y", "size", "font", "color", "hl", "upper", "kw", "kw_scale", "kw_pop"):
        if opts.get(k) is not None:
            look[k] = opts[k]
    for c in caps:
        c.update(look)
        if "y" in look:
            c["moved"] = True
    mark_captions(caps, opts.get("keywords"), opts.get("lexicon"))
    return E.put_captions(doc, caps)


_ELISION = re.compile(r"^(?:d|l|qu|n|s|j|c|m|t|jusqu|lorsqu|puisqu)['’]", re.I)


def kw_norm(text: str) -> str:
    """Forme de comparaison d'un mot-clé : sans casse, accents, ponctuation ni
    élision (« d'Ingénieur, » → « ingenieur », « 40 825 » → « 40825 »)."""
    t = _ELISION.sub("", _fold(str(text)).replace("’", "'"))
    return re.sub(r"[^\w%€$]", "", t)


def mark_captions(caps: list[dict], keywords, lexicon) -> int:
    """Mots-clés (`k` : agrandis, couleur d'accent selon le style) et orthographe
    des noms propres (`lexicon` : un mot → un mot, ponctuation gardée). Un
    mot-clé de plusieurs mots marque toute la suite. Renvoie le nombre marqué."""
    lex = {kw_norm(k): str(v) for k, v in (lexicon or {}).items() if kw_norm(k)}
    if any(len(str(k).split()) > 1 for k in (lexicon or {})):
        raise AgentError("lexicon: one word → one word (the captions follow the spoken words one by one).")
    wanted = [kw_norm(k) for k in (keywords or []) if kw_norm(k)]
    n = 0
    for c in caps:
        words = c.get("words") or []
        if lex:
            for w in words:
                key = kw_norm(w["text"])
                if key in lex:
                    tail = re.search(r"[^\w]*$", w["text"]).group(0)
                    w["text"] = lex[key] + tail
        norms = [kw_norm(w["text"]) for w in words]
        for i in range(len(words)):
            for kw in wanted:
                acc, j = "", i
                while j < len(words) and len(acc) < len(kw) and kw.startswith(acc + norms[j]):
                    acc += norms[j]
                    j += 1
                if acc == kw and j > i:
                    for w in words[i:j]:
                        if not w.get("k"):
                            w["k"] = True
                            n += 1
                    break
    return n


def captions(proj, body: dict) -> dict:
    W = Words(proj)
    with proj.lock:
        doc = doc_of(proj)
        n = make_captions(proj, doc, W, body)
        rev = commit(proj, doc, "captions")
    return {"rev": rev, "lines": n, "style": doc["settings"]["style"]}


def optimize_sound(proj, body: dict) -> dict:
    """« Optimiser le son » sur tout le montage (ou les clips `clips`)."""
    from engine.timeline import sound
    with proj.lock:
        doc = doc_of(proj)
        ids = set(body.get("clips") or []) or None
        clips = [c for c in doc["clips"] if ids is None or c["id"] in ids]
        plan = sound.optimize(proj, clips)
        n = sound.apply(doc, plan, ids)
        if not n:
            raise AgentError("No clip with sound to optimize on the timeline.")
        rev = commit(proj, doc, "optimize_sound")
    return {"rev": rev, "clips": n, "report": plan["report"]}


def remove_captions(proj) -> dict:
    with proj.lock:
        doc = doc_of(proj)
        before = len(doc["clips"])
        doc["clips"] = [c for c in doc["clips"] if not (c["kind"] == "text" and c.get("auto"))]
        rev = commit(proj, doc, "remove_captions")
    return {"rev": rev, "removed": before - len(doc["clips"])}


def add_text(proj, body: dict) -> dict:
    text = str(body.get("text") or "").strip()
    if not text:
        raise AgentError("`text` is empty.")
    start = max(0.0, float(body.get("start") or 0.0))
    dur = max(E.MIN_DUR, float(body.get("duration") or E.TEXT_DUR))
    look = style_look(body.get("style"))
    look.update({k: body[k] for k in LOOK_FIELDS if body.get(k) is not None and k not in ("x", "y")})
    look.update({k: v for k, v in (body.get("effects") or {}).items() if k in LOOK_FIELDS})
    x = float(body["x"]) if body.get("x") is not None else float(look.get("x", 0.5))
    y = float(body["y"]) if body.get("y") is not None else float(look.get("y", 0.3))
    warnings = [w for w in [_font_warning(look.get("font"))] if w]
    with proj.lock:
        doc = doc_of(proj)
        c = E.add_text(doc, text, start, dur, look, x=x, y=y, track_name=str(body.get("track") or "Titres"),
                       tag="agent")
        _apply_anims(c, body, "text")
        rev = commit(proj, doc, "add_text")
    return {"rev": rev, "clip": c["id"], "track": c["track"], "warnings": warnings}


def _place(c: dict, body: dict, m: dict | None = None, canvas: dict | None = None) -> None:
    """Position d'un plan visuel : `position` (plein cadre, incrustation, bandeau…) puis réglages fins."""
    pos = body.get("position")
    if pos == "full":
        c.update(fit="cover", scale=1.0, x=0.5, y=0.5)
    elif pos in BANDS and m and canvas:
        # bandeau pleine largeur (un écran filmé en haut, l'orateur dessous), image entière
        W, H = canvas["w"], canvas["h"]
        w, h = m.get("w") or W, m.get("h") or H
        base = min(W / w, H / h)
        scale = BAND_WIDTH * W / (w * base)
        disp_h = h * base * scale
        top = BANDS[pos]
        y = 0.5 if top is None else (top * H + disp_h / 2) / H
        c.update(fit="contain", scale=round(scale, 4), x=0.5, y=round(y, 4))
    elif pos:
        if pos not in POSITIONS:
            raise AgentError(f"Unknown position {pos!r}: full, {', '.join(BANDS)}, {', '.join(POSITIONS)}.")
        c["fit"] = "contain"
        c["scale"] = 0.5
        c["x"], c["y"] = POSITIONS[pos]
    for k in MEDIA_FIELDS:
        if body.get(k) is not None:
            c[k] = body[k]
    if body.get("transition"):
        if body["transition"] not in model.TRANSITIONS:
            raise AgentError(f"Unknown transition {body['transition']!r}. Choices: {', '.join(model.TRANSITIONS)}")
        c["trans"] = {"type": body["transition"], "dur": float(body.get("transition_duration") or 0.4)}


def add_clip(proj, body: dict) -> dict:
    """Image ou vidéo au-dessus de la piste principale (plan de coupe, illustration)."""
    m = resolve_media(proj, body.get("media"), ("video", "image"))
    require_ready(m)
    start = max(0.0, float(body.get("start") or 0.0))
    dur = float(body["duration"]) if body.get("duration") else None
    src = float(body.get("in") or 0.0)
    with proj.lock:
        doc = doc_of(proj)
        c = E.add_overlay(doc, m, start, dur, src, track_name=body.get("track"),
                          keep_audio=bool(body.get("keep_audio")))
        if body.get("position") is None and body.get("fit") is None:
            body = {**body, "position": "full"}
        _place(c, body, m, doc["canvas"])
        _apply_anims(c, body, "media")
        rev = commit(proj, doc, "add_clip")
    return {"rev": rev, "clip": c["id"], "track": c["track"], "start": c["start"], "end": E.r4(E.clip_end(c))}


def _wait_ready(proj, mid: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = proj.media(mid)
        if m and m.get("status") == "ready":
            return m
        if not m or m.get("status") == "error":
            raise AgentError(f"Media could not be prepared: {(m or {}).get('error')}", 409)
        time.sleep(0.2)
    raise AgentError("Media still being prepared: call wait(what='media') then retry.", 409)


def add_audio(proj, body: dict) -> dict:
    """Musique, son de la bibliothèque (`sound`) ou son d'un média, sur une piste audio."""
    from engine.timeline.api import sounds
    if body.get("sound"):
        mid = model.new_id("m")
        try:
            dest, name = sounds().copy_to(str(body["sound"]), proj.media_folder(mid))
        except KeyError:
            raise AgentError(f"Unknown sound {body['sound']!r}: see catalog(what='sounds').", 404) from None
        m = proj.add_media(dest, name=name, copied=True, mid=mid)
        m = _wait_ready(proj, m["id"])
    else:
        m = resolve_media(proj, body.get("media"), ("audio", "video"))
        require_ready(m)
        if not m.get("has_audio") and m["kind"] != "audio":
            raise AgentError(f"{m['name']} has no sound.")
    start = max(0.0, float(body.get("start") or 0.0))
    dur = float(body["duration"]) if body.get("duration") else None
    src = float(body.get("in") or 0.0)
    track = body.get("track") or ("Effets sonores" if body.get("sound") else "Musique")
    with proj.lock:
        doc = doc_of(proj)
        c = E.add_audio(doc, m, start, dur, src, track_name=track)
        for k in ("volume", "fade_in", "fade_out", "speed"):
            if body.get(k) is not None:
                c[k] = float(body[k])
        if body.get("loop_to_end"):
            # une musique trop courte est reposée à la suite jusqu'à la fin du montage
            end = model.duration([x for x in doc["clips"] if x is not c]) or E.clip_end(c)
            cur = c
            while E.clip_end(cur) < end - 0.2 and len(doc["clips"]) < 5000:
                nxt = E.new_media_clip(m, c["track"], E.clip_end(cur), "audio", end - E.clip_end(cur), 0.0)
                nxt.update({k: c[k] for k in ("volume",) if k in c})
                doc["clips"].append(nxt)
                cur = nxt
        rev = commit(proj, doc, "add_audio")
    return {"rev": rev, "clip": c["id"], "media": m["id"], "track": c["track"], "start": c["start"],
            "end": E.r4(E.clip_end(c))}


def update_clips(proj, body: dict) -> dict:
    changes = body.get("clips")
    if not isinstance(changes, list) or not changes:
        raise AgentError("`clips` must be a list of {id, …fields to change}.")
    notes = []
    with proj.lock:
        doc = doc_of(proj)
        by_id = {c["id"]: c for c in doc["clips"]}
        for ch in changes:
            c = by_id.get(str(ch.get("id") or ""))
            if not c:
                raise AgentError(f"Clip {ch.get('id')!r} not found: call get_timeline.", 404)
            main = E.is_main(doc, c["track"])
            if "start" in ch and ch["start"] is not None:
                if main:
                    notes.append(f"{c['id']}: the main track is magnetic, start ignored (use build_edit to reorder)")
                else:
                    c["start"] = E.r4(max(0.0, float(ch["start"])))
            if ch.get("duration") is not None:
                c["dur"] = E.r4(max(E.MIN_DUR, float(ch["duration"])))
            if ch.get("in") is not None and E.has_source(c):
                c["in"] = E.r4(max(0.0, float(ch["in"])))
            if ch.get("track"):
                tr = E.find_track(doc, ch["track"]) or next(
                    (t for t in doc["tracks"] if t["name"] == ch["track"]), None)
                if not tr or tr["kind"] != model.CLIP_TRACK[c["kind"]]:
                    raise AgentError(f"Track {ch['track']!r} missing or of the wrong kind for a {c['kind']} clip.")
                c["track"] = tr["id"]
            if c["kind"] == "text":
                if ch.get("style"):
                    c.update({k: v for k, v in style_look(ch["style"]).items() if k not in ("x", "y")})
                c.update({k: ch[k] for k in LOOK_FIELDS if ch.get(k) is not None})
                c.update({k: v for k, v in (ch.get("effects") or {}).items() if k in LOOK_FIELDS})
                if ch.get("text") is not None:
                    _retext(c, str(ch["text"]))
                if ch.get("hidden") is not None:
                    c["hidden"] = bool(ch["hidden"])
                _apply_anims(c, ch, "text")
            else:
                _place(c, {k: v for k, v in ch.items() if k != "start"}, proj.media(c.get("media") or ""),
                       doc["canvas"])
                if c["kind"] in ("video", "image"):
                    _apply_anims(c, ch, "media")
            if c["kind"] == "text" and not c.get("auto") and c.get("words") and len(c["words"]) == 1:
                c["words"][0].update(start=c["start"], end=E.r4(E.clip_end(c)))
        E.pack_main(doc)
        E.reflow_captions(doc)
        rev = commit(proj, doc, "update_clips")
    return {"rev": rev, "updated": len(changes), "notes": notes}


def _retext(c: dict, text: str) -> None:
    """Nouveau texte d'un clip texte. Sous-titre : les mots gardent leurs temps
    s'ils sont aussi nombreux, sinon le texte est réparti sur la ligne."""
    parts = text.split()
    words = [w for w in c.get("words") or [] if not w.get("cut")]
    if c.get("auto") and words and len(parts) == len(words):
        for w, p in zip(words, parts):
            w["text"] = p
        return
    if c.get("auto") and words:
        keep = {k: words[0][k] for k in ("m", "s") if k in words[0]}
        last = words[-1]
        c["words"] = [{"text": text, "start": words[0]["start"], "end": last["end"], **keep,
                       **({"e": last["e"]} if "e" in last else {})}]
        return
    c["words"] = [{"text": text, "start": c["start"], "end": E.r4(E.clip_end(c))}]


def delete(proj, body: dict) -> dict:
    ids = [str(x) for x in body.get("ids") or []]
    if not ids:
        raise AgentError("`ids` is empty.")
    with proj.lock:
        doc = doc_of(proj)
        known = {c["id"] for c in doc["clips"]}
        missing = [i for i in ids if i not in known]
        if missing:
            raise AgentError(f"Clips not found: {missing}", 404)
        n = E.delete_clips(doc, ids)
        E.reflow_captions(doc)
        rev = commit(proj, doc, "delete_clips")
    return {"rev": rev, "deleted": n}


def cut(proj, body: dict) -> dict:
    a, b = float(body.get("start") or 0), float(body.get("end") or 0)
    if b <= a:
        raise AgentError("`end` must be after `start` (timeline seconds).")
    with proj.lock:
        doc = doc_of(proj)
        removed = E.cut_range(doc, a, b)
        rev = commit(proj, doc, "cut_range")
    return {"rev": rev, "removed": removed, "duration": model.duration(proj.state["clips"])}


def set_format(proj, body: dict) -> dict:
    """Format, cadence, fond ; recadre les plans sur le visage (16:9 -> 9:16…)."""
    with proj.lock:
        doc = doc_of(proj)
        cv = dict(doc["canvas"])
        fmt = body.get("format")
        if fmt:
            if fmt not in model.CANVAS_PRESETS:
                raise AgentError(f"Unknown format {fmt!r}: {', '.join(model.CANVAS_PRESETS)}.")
            cv["w"], cv["h"] = model.CANVAS_PRESETS[fmt]
        for k in ("w", "h", "fps"):
            if body.get(k):
                cv[k] = int(body[k])
        if body.get("background"):
            cv["bg"] = body["background"]
        if body.get("blur_background") is not None:
            cv["blur"] = bool(body["blur_background"])
        doc["canvas"] = model.normalize_canvas(cv)
        n = 0
        mode = body.get("reframe", "face" if fmt else None)
        if mode:
            n = _reframe(proj, doc, mode)
        rev = commit(proj, doc, "set_format")
    return {"rev": rev, "canvas": proj.state["canvas"], "reframed": n}


def _reframe(proj, doc: dict, mode: str, ids=None) -> int:
    if mode not in ("face", "center", "none"):
        raise AgentError(f"Unknown reframe mode {mode!r}: face, center or none.")
    if mode == "none":
        return 0
    media = {m["id"]: m for m in proj.state["media"]}
    faces = face_finder(proj, media)
    n = 0
    for c in doc["clips"]:
        if c["kind"] != "video" or (ids and c["id"] not in ids) or c.get("fit") == "contain":
            continue
        if not ids and not E.is_main(doc, c["track"]):
            continue
        m = media.get(c["media"])
        face = faces(c["media"], c["in"], E.src_end(c)) if mode == "face" and m else None
        c["x"], c["y"] = E.position(face, m, doc["canvas"], c.get("scale") or 1.0, force=True)
        n += 1
    return n


def reframe(proj, body: dict) -> dict:
    with proj.lock:
        doc = doc_of(proj)
        n = _reframe(proj, doc, str(body.get("mode") or "face"), set(body.get("clips") or []) or None)
        rev = commit(proj, doc, "reframe")
    return {"rev": rev, "reframed": n}


def subject_apply(proj, body: dict) -> dict:
    """Après la détection du sujet : arrière-plan retiré, cadre qui suit, cadrage."""
    m = resolve_media(proj, body.get("media"), ("video", "image"))
    sub = m.get("subject") or {}
    if sub.get("status") != "done":
        raise AgentError(f"No subject detected on {m['name']} yet (status {sub.get('status') or 'none'}): "
                         "call subject(detect) and wait(what='subject').", 409)
    with proj.lock:
        doc = doc_of(proj)
        ids = set(body.get("clips") or [])
        targets = [c for c in doc["clips"] if c.get("media") == m["id"] and c["kind"] in ("video", "image")
                   and (not ids or c["id"] in ids)]
        if not targets:
            raise AgentError(f"No clip of {m['name']} on the timeline.")
        framed = 0
        for c in targets:
            if body.get("remove_background") is not None:
                c["cutout"] = bool(body["remove_background"])
            if body.get("follow") is not None:
                c["follow"] = bool(body["follow"])
            if body.get("frame"):
                framed += E.frame_subject(c, m, doc["canvas"])
        if body.get("background"):
            doc["canvas"]["bg"] = body["background"]
        if body.get("blur_background") is not None:
            doc["canvas"]["blur"] = bool(body["blur_background"])
        rev = commit(proj, doc, "subject")
    return {"rev": rev, "clips": [c["id"] for c in targets], "framed": framed}


def markers(proj, body: dict) -> dict:
    with proj.lock:
        doc = doc_of(proj)
        if body.get("replace"):
            doc["markers"] = []
        for mk in body.get("markers") or []:
            doc["markers"].append({"id": model.new_id("k"), "t": float(mk.get("t") or 0),
                                   "label": str(mk.get("label") or "")[:80], "color": mk.get("color") or "#F5B000"})
        rev = commit(proj, doc, "markers")
    return {"rev": rev, "markers": len(proj.state["markers"])}


def rename(proj, name: str) -> dict:
    with proj.lock:
        doc = doc_of(proj)
        doc["name"] = str(name)[:80]
        return {"rev": commit(proj, doc, "rename")}


# ----------------------------------------------------- montage complet

def auto_edit(job: dict, proj, body: dict) -> dict:
    """Le montage automatique du studio, en une fois : transcription si besoin,
    analyse, coupes, accroche, zooms, textes, sous-titres, son, repères."""
    from engine.timeline.api import media_plan
    media = [m for m in proj.state["media"] if m["kind"] == "video" and m.get("has_audio")]
    if body.get("media"):
        refs = body["media"] if isinstance(body["media"], list) else [body["media"]]
        media = [resolve_media(proj, r, ("video",)) for r in refs]
    if not media:
        raise AgentError("No video with sound in the project: import one first.")
    for m in media:
        require_ready(m)
    job["message"] = "Transcription…"
    for m in media:
        if (m.get("transcript") or {}).get("status") != "done":
            ai.queue_transcription(proj, m["id"])
    t0 = time.time()
    while any((proj.media(m["id"]).get("transcript") or {}).get("status") in ("queued", "running")
              for m in media):
        if time.time() - t0 > 3600:
            raise AgentError("Transcription is taking too long.")
        time.sleep(0.5)
    for m in media:
        require_transcript(proj.media(m["id"]))
    opts = {"llm": bool(body.get("llm", True)), "trim": bool(body.get("trim", True)),
            "cold_open": bool(body.get("cold_open", False)), "max_duration": float(body.get("max_duration") or 0)}
    plans = {}
    for n, m in enumerate(media):
        job.update(message=f"Analysis of {m['name']}…", pct=30 + 40 * n // len(media))
        plans[m["id"]] = media_plan(proj, m["id"], opts, lambda t: job.update(message=t))
    job.update(message="Editing…", pct=75)
    W = Words(proj)
    segments = []
    hook_seg = None
    for m in media:
        p = plans[m["id"]]
        drop = set(p.get("drop_sentences") or [])
        kept = [s["i"] for s in W.sentences(m["id"]) if s["i"] not in drop]
        hk = p.get("hook") or {}
        if hk.get("cold_open") and body.get("hook", True) and hk.get("sentence") in kept and hook_seg is None:
            hook_seg = expand_segment(proj, W, {"media": m["id"], "sentences": [hk["sentence"]]})
            kept.remove(hk["sentence"])
        if kept:
            segments += expand_segment(proj, W, {"media": m["id"], "sentences": kept})
    segments = (hook_seg or []) + segments
    if not segments:
        raise AgentError("Nothing left to keep after the analysis (no speech?).")
    highlights = {mid: p.get("highlights") or [] for mid, p in plans.items()}
    caps = body.get("captions", True)
    with proj.lock:
        doc = doc_of(proj)
        doc["clips"] = [c for c in doc["clips"] if not (c["kind"] == "text" and c.get("ai"))]
        rep = assemble_doc(proj, doc, W, segments, {**body, "captions": caps}, highlights)
        rep["hook"] = ""
        first = plans[media[0]["id"]]
        text = ((first.get("hook") or {}).get("text") or first.get("title") or "").strip()
        if body.get("hook", True) and text:
            size = 68 if len(text) > 40 else 84 if len(text) > 22 else 100
            E.add_text(doc, text, 0.0, TITLE_DUR, {**HOOK_LOOK, "size": size}, y=0.26, track_name="Titres", tag="hook")
            rep["hook"] = text
        rep["texts"] = 0
        if body.get("texts", True):
            clips = E.voice_clips(doc)
            for mid, p in plans.items():
                for tx in p.get("texts") or []:
                    hit = E.map_source(clips, mid, tx["s"] + 0.02)
                    if not hit or (rep["hook"] and hit[1] < TITLE_DUR + 0.2):
                        continue
                    end = E.map_source(clips, mid, tx["e"] - 0.05)
                    dur = min(2.6, max(1.6, (end[1] - hit[1]) if end else 2.0))
                    t = (tx.get("text") or "").strip()
                    row = next((x for x in doc["tracks"] if x["kind"] == "text" and x["name"] == "Textes"), None)
                    if not t or (row and not E.is_free(doc, row["id"], hit[1], dur)):
                        continue
                    E.add_text(doc, t, hit[1], dur, {**TEXT_LOOK, "size": 54 if len(t) > 18 else 64}, y=0.2,
                               track_name="Textes", tag="text")
                    rep["texts"] += 1
        doc["markers"] = [mk for mk in doc.get("markers") or [] if not mk["label"].startswith("★ ")]
        clips = E.voice_clips(doc)
        for mid, p in plans.items():
            for x in p.get("highlights") or []:
                hit = E.map_source(clips, mid, x["s"] + 0.02)
                if hit:
                    doc["markers"].append({"id": model.new_id("k"), "t": E.r4(hit[1]),
                                           "label": "★ " + (x.get("label") or "Moment fort"), "color": "#F5B000"})
        E.reflow_captions(doc)
        E.fix_overlaps(doc)
        rev = commit(proj, doc, "auto_edit")
    return {"rev": rev, "duration": model.duration(proj.state["clips"]), **rep,
            "llm": any(p.get("llm") for p in plans.values()),
            "timeline_text": E.timeline_sentences(doc_of(proj), W.sentences)}


# ------------------------------------------------------------ tâches

def start_job(kind: str, fn, *args, queue=None) -> str:
    """Tâche longue (montage complet, analyse IA) : son état se lit par `JOBS`."""
    jid = model.new_id("j")
    job = {"id": jid, "kind": kind, "status": "running", "pct": 0, "message": "", "result": None,
           "started": time.time()}
    JOBS[jid] = job

    def run() -> None:
        try:
            job["result"] = fn(job, *args)
            job.update(status="done", pct=100, message="Done.")
        except AgentError as exc:
            job.update(status="error", message=str(exc))
        except Exception as exc:  # noqa: BLE001 - remonté à l'agent
            traceback.print_exc()
            job.update(status="error", message=f"{type(exc).__name__}: {exc}"[:1200])
    if queue is not None:
        queue.submit(("agent", jid), run)
    else:
        threading.Thread(target=run, name=f"agent-{kind}", daemon=True).start()
    return jid


def job_view(jid: str) -> dict:
    job = JOBS.get(jid)
    if not job:
        raise AgentError(f"Unknown job {jid!r}.", 404)
    return dict(job)


def analyze_job(job: dict, proj, ref, opts: dict) -> dict:
    job["message"] = "Analysis…"
    return plan(proj, ref, opts)


def queue_for(kind: str):
    return jobs.TRANSCRIBE if kind in ("auto_edit", "analyze", "derush") else None
