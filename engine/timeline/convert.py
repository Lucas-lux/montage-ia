"""Ouvrir un projet « short » dans la timeline.

Le projet short garde sa source, ses mots transcrits (temps source), ses
passages gardés et ses sous-titres retouchés (temps de SORTIE). La timeline
reçoit :
  * la source comme média (lue sur place, jamais copiée), avec sa
    transcription déjà faite : pas de nouveau passage de Whisper ;
  * un clip par passage gardé, recollés sur la piste principale ;
  * les sous-titres, repassés en temps source mot par mot : ils restent liés
    à la voix et suivront les retouches suivantes.
Le projet short n'est pas modifié.
"""
from __future__ import annotations

import json
import os

from engine import store
from engine.timeline import model
from engine.timeline.project import TimelineProject

_LOOK = ("font", "size", "bold", "upper", "color", "hl", "outline_col", "outline", "shadow", "box",
         "box_alpha", "mode", "pop", "x", "y", "emoji", "emoji_size", "emoji_dx", "emoji_dy",
         "emoji_moved", "moved", "hidden", "lang")


def _to_source(t: float, keep: list) -> float | None:
    """Instant de la sortie (après coupes) -> instant de la source."""
    acc = 0.0
    for k in keep:
        n = k.end - k.start
        if t < acc + n - 1e-6:
            return k.start + max(0.0, t - acc)
        acc += n
    return None


def from_short(work_dir: str, pid: str) -> TimelineProject:
    from engine.project import Project        # import tardif : évite une boucle
    short = Project.load(work_dir, pid)
    if short is None:
        raise ValueError("Ce projet n'est pas analysé : rien à ouvrir dans la timeline.")
    if not os.path.isfile(short.source):
        raise ValueError("La vidéo de ce projet a disparu : " + short.source)

    tl = TimelineProject.create(work_dir, (short.name or "Short") + " (timeline)")
    fps = min(model.FPS_CHOICES, key=lambda f: abs(f - (short.info.fps or 30)))
    tl.state["canvas"] = model.normalize_canvas({"w": short.out_w, "h": short.out_h, "fps": fps})
    tl.state["settings"]["style"] = short.style
    mid = model.new_id("m")

    # transcription reprise telle quelle (temps source)
    folder = tl.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    words = [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)} for w in short.words]
    with open(os.path.join(folder, "words.json"), "w", encoding="utf-8") as f:
        json.dump({"words": words, "language": short.language}, f, ensure_ascii=False)

    main = next(t for t in tl.state["tracks"] if t["main"])
    text_track = {"id": model.new_id("t"), "kind": "text", "name": "Sous-titres", "main": False,
                  "muted": False, "hidden": False, "locked": False}
    tl.state["tracks"].insert(0, text_track)

    clips: list[dict] = []
    acc = 0.0
    for k in short.keep:
        clips.append({"id": model.new_id("c"), "track": main["id"], "kind": "video", "media": mid,
                      "start": round(acc, 4), "dur": round(k.end - k.start, 4), "in": round(k.start, 4),
                      "speed": 1.0, "fit": "cover", "x": 0.5, "y": 0.5, "scale": 1.0})
        acc += k.end - k.start

    for c in short.captions:
        out_words = []
        for w in c.get("words") or []:
            s = _to_source(float(w["start"]), short.keep)
            e = _to_source(max(float(w["start"]), float(w["end"]) - 1e-4), short.keep)
            if s is None:
                continue
            out_words.append({"text": w["text"], "start": w["start"], "end": w["end"],
                              "m": mid, "s": round(s, 4), "e": round(max(s, e if e is not None else s), 4)})
        if not out_words:
            continue
        cap = {k: c[k] for k in _LOOK if k in c}
        cap.update({"id": model.new_id("k"), "track": text_track["id"], "kind": "text", "auto": True,
                    "start": float(c["start"]), "dur": max(0.05, float(c["end"]) - float(c["start"])),
                    "words": out_words})
        clips.append(cap)

    with tl.lock:
        tl.state["clips"] = clips
        tl.save()
    entry = tl.add_media(short.source, os.path.basename(short.source), copied=False, mid=mid)
    tl.update_media(entry["id"], transcript={"status": "done", "count": len(words),
                                             "language": short.language or ""})
    # la normalisation se fera à la première sauvegarde de l'éditeur (le média
    # n'est pas encore sondé : ses bornes sont inconnues)
    tl.state["updated"] = store.now()
    return tl
