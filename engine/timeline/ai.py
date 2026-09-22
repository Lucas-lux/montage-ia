"""Outils automatiques du studio : transcription, silences, sous-titres.

  * Transcription : un média à la fois (file `jobs.TRANSCRIBE`), avec le
    même moteur que le mode short (Whisper sur GPU, repli processeur). Les
    mots nettoyés sont rangés dans `media/<id>/words.json` : on ne transcrit
    jamais deux fois le même rush.
  * Silences au volume : `silencedetect` sur le proxy, gardé en cache par
    réglage — pour les passages sans parole (musique, ambiance).
  * Sous-titres : à partir des clips qui portent la voix, dans l'ordre de la
    timeline. Chaque mot garde sa place dans le média source (`m`, `s`, `e`) :
    l'éditeur recale ensuite les lignes tout seul après chaque coupe. Une ligne
    ne chevauche jamais une coupe : un changement de clip la termine.
"""
from __future__ import annotations

import json
import os

from engine.pipeline.ass_edit import emoji_geometry
from engine.pipeline.captions import clean_words, group_indices
from engine.pipeline.emoji import emoji_for
from engine.pipeline.style_presets import preset
from engine.pipeline.transcribe import transcribe
from engine.timeline import jobs, media as mediatools
from engine.timeline import model

EMOJI_MIN_GAP = 3.0       # s entre deux émojis
LINE_GAP = 1.0            # un blanc plus long termine la ligne


# --------------------------------------------------------------- transcription

def words_path(proj, mid: str) -> str:
    return os.path.join(proj.media_folder(mid), "words.json")


def load_words(proj, mid: str) -> list[dict]:
    try:
        with open(words_path(proj, mid), encoding="utf-8") as f:
            return json.load(f).get("words", [])
    except (OSError, ValueError):
        return []


def queue_transcription(proj, mid: str, force: bool = False) -> bool:
    """Met un média en file de transcription. Faux s'il n'a pas de son."""
    m = proj.media(mid)
    if not m or m.get("status") != "ready" or not m.get("has_audio"):
        return False
    tr = m.get("transcript") or {}
    if tr.get("status") in ("queued", "running"):
        return True
    if tr.get("status") == "done" and not force and os.path.isfile(words_path(proj, mid)):
        return True
    proj.update_media(mid, transcript={"status": "queued"})
    jobs.TRANSCRIBE.submit(("tr", proj.id, mid), run_transcription, proj, mid)
    return True


def run_transcription(proj, mid: str) -> None:
    m = proj.media(mid)
    if m is None or proj.deleted:
        return
    settings = proj.state.get("settings") or {}
    proj.update_media(mid, transcript={"status": "running"})
    try:
        meta: dict = {}
        raw = transcribe(m["path"], settings.get("model") or "large-v3-turbo",
                         settings.get("device") or "auto", "auto",
                         settings.get("language") or None, info=meta)
        words = [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)}
                 for w in clean_words(raw)]
        path = words_path(proj, mid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"words": words, "language": meta.get("language") or ""}, f, ensure_ascii=False)
        os.replace(tmp, path)
        proj.update_media(mid, transcript={
            "status": "done", "count": len(words),
            "language": settings.get("language") or meta.get("language") or "",
            "device": meta.get("device", ""),
        })
    except Exception as exc:  # noqa: BLE001 - affiché dans le panneau Médias
        proj.update_media(mid, transcript={"status": "error", "error": str(exc)[:400]})


# ------------------------------------------------------------------ silences

def silences(proj, mid: str, noise_db: float = -35.0, min_dur: float = 0.4) -> list[list[float]]:
    """Silences mesurés au volume sur le proxy (en cache par réglage)."""
    m = proj.media(mid)
    if not m or m.get("status") != "ready":
        raise ValueError("Média pas encore prêt.")
    if not m.get("has_audio"):
        return [[0.0, m.get("duration", 0.0)]]
    key = f"silences_{int(round(noise_db))}_{int(round(min_dur * 100))}.json"
    cache = os.path.join(proj.media_folder(mid), key)
    try:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    src = os.path.join(proj.media_folder(mid), m.get("proxy_file") or "")
    if not os.path.isfile(src):
        src = m["path"]
    out = mediatools.detect_silences(src, noise_db, min_dur, m.get("duration", 0.0))
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


# ---------------------------------------------------------------- sous-titres

def _voice_clips(clips: list[dict], media: dict[str, dict]) -> list[dict]:
    """Clips qui portent la voix d'un média transcrit, dans l'ordre de la timeline.

    Le son séparé d'une vidéo compte une seule fois (le clip audio lié, pas la
    vidéo muette) ; un clip coupé (muet) ne parle pas.
    """
    out = []
    for c in clips:
        m = media.get(c.get("media"))
        if not m or not m.get("has_audio") or c.get("muted"):
            continue
        if c.get("kind") == "audio" or (c.get("kind") == "video" and not c.get("detached")):
            out.append(c)
    return sorted(out, key=lambda c: (c["start"], c.get("track", "")))


def build_captions(clips: list[dict], media: dict[str, dict], words_of, settings: dict) -> list[dict]:
    """Sous-titres (clips texte « auto ») pour les clips qui portent la voix.

    `words_of(mid)` renvoie les mots transcrits d'un média (temps source).
    """
    s = model.normalize_settings(settings)
    look = preset(s["style"])
    esz, edy = emoji_geometry(look["size"])
    out: list[dict] = []
    last_emoji = -1e9
    covered: list[tuple[float, float]] = []    # plages déjà sous-titrées (voix superposées)
    for c in _voice_clips(clips, media):
        speed = float(c.get("speed") or 1.0)
        src_a = float(c.get("in") or 0.0)
        src_b = src_a + float(c["dur"]) * speed
        start = float(c["start"])
        words = []
        for w in words_of(c["media"]):
            if w["end"] <= src_a or w["start"] >= src_b:
                continue
            s0, e0 = max(src_a, w["start"]), min(src_b, w["end"])
            t0 = start + (s0 - src_a) / speed
            t1 = start + (e0 - src_a) / speed
            if any(a <= t0 < b for a, b in covered):
                continue
            words.append({"text": w["text"], "start": round(t0, 4), "end": round(max(t0, t1), 4),
                          "m": c["media"], "s": round(w["start"], 4), "e": round(w["end"], 4)})
        if not words:
            continue
        covered.append((start, start + float(c["dur"])))
        # des lignes qui tiennent dans le cadre, coupées aussi aux longs blancs
        runs: list[list[dict]] = [[]]
        for w in words:
            if runs[-1] and w["start"] - runs[-1][-1]["end"] > LINE_GAP:
                runs.append([])
            runs[-1].append(w)
        for run in runs:
            for idx in group_indices([w["text"] for w in run], s["words_per_line"], s["max_chars"]):
                line = [run[i] for i in idx]
                t0, t1 = line[0]["start"], max(line[-1]["end"], line[0]["start"] + 0.2)
                emoji = ""
                if s["emojis"] and t0 - last_emoji >= EMOJI_MIN_GAP:
                    for w in line:
                        emoji = emoji_for(w["text"]) or ""
                        if emoji:
                            last_emoji = t0
                            break
                out.append({
                    "id": model.new_id("k"), "kind": "text", "auto": True,
                    "start": round(t0, 4), "dur": round(t1 - t0, 4),
                    "words": line, "emoji": emoji, "emoji_size": esz, "emoji_dx": 0,
                    "emoji_dy": round(edy, 1), **look,
                })
    # deux lignes consécutives ne se chevauchent pas
    out.sort(key=lambda c: c["start"])
    for a, b in zip(out, out[1:]):
        if a["start"] + a["dur"] > b["start"]:
            a["dur"] = round(max(0.05, b["start"] - a["start"]), 4)
    return out


def language_of(proj, mids: list[str]) -> str:
    """Langue parlée majoritaire des médias transcrits (« fr » par défaut)."""
    langs: dict[str, int] = {}
    for mid in mids:
        m = proj.media(mid) or {}
        tr = m.get("transcript") or {}
        if tr.get("language"):
            langs[tr["language"]] = langs.get(tr["language"], 0) + int(tr.get("count") or 1)
    return max(langs, key=langs.get) if langs else (proj.state.get("settings") or {}).get("language") or "fr"

