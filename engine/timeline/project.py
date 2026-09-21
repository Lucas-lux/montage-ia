"""Un projet timeline : son état sur disque et les tâches qui le modifient.

    work/projects/<id>/
        project.json          état complet (pistes, clips, médias, réglages)
        thumb.jpg             vignette de l'écran d'accueil
        media/<mid>/          un dossier par média importé :
            source.<ext>        l'original, s'il a été téléversé (sinon on
                                lit le fichier là où il est)
            proxy.mp4|m4a|jpg   copie légère lue par l'éditeur
            thumbs.jpg          planche de vignettes pour la timeline
            wave.bin            forme d'onde (un octet par centième de seconde)
            words.json          transcription, mot par mot
        exports/              rendus finaux

L'éditeur possède le montage (il l'envoie en entier à chaque sauvegarde) ; le
moteur possède les médias (import, proxies, transcription). Les deux écrivent
dans le même `project.json`, d'où le verrou : une sauvegarde de l'éditeur et
la fin d'un proxy peuvent arriver en même temps.
"""
from __future__ import annotations

import copy
import os
import threading
import uuid

from engine import store
from engine.timeline import model


class TimelineProject:
    def __init__(self, work_dir: str, state: dict) -> None:
        self.work_dir = work_dir
        self.state = state
        self.id: str = state["id"]
        self.dir = store.project_dir(work_dir, self.id, create=True)
        self.lock = threading.RLock()
        self.deleted = False
        # Tâche longue du projet (export) ; les médias ont chacun leur statut.
        self.task: dict = {"status": "idle", "pct": 0, "message": ""}

    # ------------------------------------------------------------- création

    @classmethod
    def create(cls, work_dir: str, name: str = "", preset: str | None = None) -> "TimelineProject":
        pid = uuid.uuid4().hex
        proj = cls(work_dir, model.new_state(pid, name, preset, store.now()))
        proj.save()
        return proj

    @classmethod
    def load(cls, work_dir: str, pid: str) -> "TimelineProject | None":
        state = store.read_state(work_dir, pid)
        if not state or state.get("kind") != "timeline":
            return None
        return cls(work_dir, upgrade(state))

    # ---------------------------------------------------------- persistance

    def save(self) -> None:
        with self.lock:
            if self.deleted:
                return
            self.state["updated"] = store.now()
            store.write_state(self.work_dir, self.id, self.state)

    def apply(self, body: dict) -> None:
        """Sauvegarde envoyée par l'éditeur (validée par `model`)."""
        with self.lock:
            model.apply_client_state(self.state, body)
            self.save()

    @property
    def media_dir(self) -> str:
        return os.path.join(self.dir, "media")

    def media(self, mid: str) -> dict | None:
        return next((m for m in self.state["media"] if m["id"] == mid), None)

    @property
    def is_busy(self) -> bool:
        return self.task.get("status") == "running"

    # -------------------------------------------------------- sérialisation

    def to_dict(self) -> dict:
        with self.lock:
            data = copy.deepcopy(self.state)
            data["duration"] = model.duration(data["clips"])
            data["task"] = dict(self.task)
            data["media"] = [self._media_view(m) for m in data["media"]]
            return data

    def _media_view(self, m: dict) -> dict:
        """Média tel que l'éditeur le voit : chemins disque remplacés par des URL."""
        base = f"/api/timeline/{self.id}/media/{m['id']}"
        v = int(m.get("rev") or 0)
        view = {k: v2 for k, v2 in m.items() if k not in ("proxy_file", "source_file")}
        view["urls"] = {
            "proxy": f"{base}/proxy?v={v}" if m.get("proxy_file") else "",
            "thumbs": f"{base}/thumbs?v={v}" if (m.get("thumbs") or {}).get("count") else "",
            "wave": f"{base}/wave?v={v}" if (m.get("waveform") or {}).get("count") else "",
            "poster": f"{base}/poster?v={v}" if m.get("poster") else "",
        }
        return view


def upgrade(state: dict) -> dict:
    """Complète un état lu sur disque (champs ajoutés au fil des versions)."""
    fresh = model.new_state(state["id"], state.get("name", ""), None, state.get("created", 0))
    for key, value in fresh.items():
        state.setdefault(key, value)
    state["canvas"] = model.normalize_canvas(state.get("canvas"))
    state["settings"] = model.normalize_settings(state.get("settings"))
    state["tracks"] = model.normalize_tracks(state.get("tracks"))
    state["clips"] = model.normalize_clips(state.get("clips"), state["tracks"], state["media"])
    state["markers"] = model.normalize_markers(state.get("markers"))
    return state
