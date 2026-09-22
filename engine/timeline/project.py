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
import re
import shutil
import threading
import time
import uuid

from engine import store
from engine.timeline import jobs, media as mediatools, model


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
        proj = cls(work_dir, upgrade(state))
        proj.resume()
        return proj

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

    # ----------------------------------------------------------------- médias

    def add_media(self, path: str, name: str = "", copied: bool = False,
                  mid: str | None = None) -> dict:
        """Ajoute un média au projet et met sa préparation en file."""
        path = os.path.abspath(path)
        entry = {
            "id": mid or model.new_id("m"),
            "name": (name or os.path.basename(path))[:200],
            "path": path,
            "copied": copied,
            "kind": mediatools.kind_of(path) or "video",
            "duration": 0.0, "w": 0, "h": 0, "fps": 0.0, "has_audio": False,
            "status": "pending", "progress": 0, "error": "", "rev": 0,
            "proxy_file": "", "poster": "", "thumbs": None, "waveform": None,
            "transcript": {"status": "none"},
            "added": store.now(),
        }
        with self.lock:
            self.state["media"].append(entry)
            self.save()
        self.queue_media(entry["id"])
        return entry

    def queue_media(self, mid: str) -> None:
        jobs.MEDIA.submit((self.id, mid), process_media, self, mid)

    def update_media(self, mid: str, save: bool = True, **fields) -> dict | None:
        with self.lock:
            m = self.media(mid)
            if m is None:
                return None
            m.update(fields)
            if save:
                self.save()
            return m

    def remove_media(self, mid: str) -> bool:
        """Retire un média, les clips qui l'utilisent et ses fichiers."""
        with self.lock:
            m = self.media(mid)
            if m is None:
                return False
            self.state["media"] = [x for x in self.state["media"] if x["id"] != mid]
            self.state["clips"] = [c for c in self.state["clips"] if c.get("media") != mid]
            self.save()
        shutil.rmtree(self.media_folder(mid), ignore_errors=True)
        return True

    def media_folder(self, mid: str) -> str:
        return os.path.join(self.media_dir, mid)

    def resume(self) -> None:
        """Après un redémarrage : relance ce qui n'avait pas abouti.

        Un média en cours de préparation à l'arrêt repart de zéro ; un média
        prêt dont le proxy a disparu est refait ; un fichier lu sur place qui
        n'est plus là est signalé (on pourra le relier).
        """
        todo = []
        with self.lock:
            for m in self.state["media"]:
                if m.get("status") in ("pending", "processing"):
                    m.update(status="pending", progress=0)
                    todo.append(m["id"])
                elif m.get("status") == "ready":
                    proxy = os.path.join(self.media_folder(m["id"]), m.get("proxy_file") or "-")
                    if not os.path.isfile(m["path"]) and not m.get("copied"):
                        m.update(status="missing", error="Fichier introuvable : " + m["path"])
                    elif not os.path.isfile(proxy):
                        m.update(status="pending", progress=0)
                        todo.append(m["id"])
                elif m.get("status") == "missing" and os.path.isfile(m["path"]):
                    m.update(status="pending", progress=0, error="")
                    todo.append(m["id"])
        for mid in todo:
            self.queue_media(mid)
        # transcriptions interrompues par l'arrêt : on les relance
        from engine.timeline import ai
        for m in self.state["media"]:
            if (m.get("transcript") or {}).get("status") in ("queued", "running"):
                m["transcript"] = {"status": "none"}
                if m.get("status") == "ready":
                    ai.queue_transcription(self, m["id"])

    # ------------------------------------------------------------------ export

    def reserve_export(self) -> bool:
        """Réserve le projet pour un export. Faux si un export tourne déjà."""
        with self.lock:
            if self.is_busy:
                return False
            self.task = {"status": "running", "pct": 0, "message": "Préparation de l'export…"}
            self._cancel = threading.Event()
            return True

    def export(self, opts: dict) -> None:
        """Rend le montage (dans un thread, après `reserve_export`)."""
        from engine.timeline import render
        from engine.tools import audio as audio_tool
        with self.lock:
            snapshot = copy.deepcopy(self.state)
        audio_only = bool(opts.get("audio_only"))
        fmt = str(opts.get("audio_format") or "mp3")
        ext = audio_tool.FORMATS[fmt]["ext"] if audio_only else "mp4"
        try:
            folder = str(opts.get("folder") or "").strip().strip('"') or default_export_dir(self)
            out = unique_path(folder, safe_name(self.state.get("name") or "Montage"), ext)
            audio_args = audio_tool.encode_args(fmt, opts.get("audio_quality")) if audio_only else None
            self.task["message"] = "Rendu…"

            def progress(frac: float) -> None:
                self.task["pct"] = round(100 * frac, 1)

            res = render.export(snapshot, snapshot["media"], out, resolution=opts.get("resolution"),
                                fps=opts.get("fps"), quality=str(opts.get("quality") or "standard"),
                                codec=str(opts.get("codec") or "h264"),
                                encoder=str(opts.get("encoder") or "auto"), audio_only=audio_only,
                                audio_args=audio_args, on_progress=progress, cancel=self._cancel,
                                loudness=bool(opts.get("loudness")))
            res.update(at=store.now(), audio_only=audio_only, signature=str(opts.get("signature") or ""))
            with self.lock:
                self.state["export"] = res
                self.save()
            if not audio_only:
                from engine.pipeline.render import grab_thumbnail
                grab_thumbnail(out, os.path.join(self.dir, "thumb.jpg"), at=min(1.0, res["duration"] * 0.1))
            self.task = {"status": "done", "pct": 100, "message": "Export terminé", "result": res}
        except render.Cancelled:
            self.task = {"status": "cancelled", "pct": 0, "message": "Export annulé."}
        except Exception as exc:  # noqa: BLE001 - affiché dans la fenêtre d'export
            self.task = {"status": "error", "pct": 0, "message": str(exc)[:1500]}

    def cancel_export(self) -> bool:
        ev = getattr(self, "_cancel", None)
        if ev is None or not self.is_busy:
            return False
        ev.set()
        return True

    @property
    def media_busy(self) -> bool:
        return any(m.get("status") in ("pending", "processing") for m in self.state["media"])

    # -------------------------------------------------------- sérialisation

    def to_dict(self) -> dict:
        with self.lock:
            data = copy.deepcopy(self.state)
            data["duration"] = model.duration(data["clips"])
            data["task"] = dict(self.task)
            data["media"] = [self._media_view(m) for m in data["media"]]
            return data

    def media_views(self) -> list[dict]:
        with self.lock:
            return [self._media_view(copy.deepcopy(m)) for m in self.state["media"]]

    def _media_view(self, m: dict) -> dict:
        """Média tel que l'éditeur le voit : chemins disque remplacés par des URL."""
        base = f"/api/timeline/{self.id}/media/{m['id']}"
        v = int(m.get("rev") or 0)
        view = {k: v2 for k, v2 in m.items() if k != "proxy_file"}
        view["urls"] = {
            "proxy": f"{base}/proxy?v={v}" if m.get("proxy_file") else "",
            "thumbs": f"{base}/thumbs?v={v}" if (m.get("thumbs") or {}).get("count") else "",
            "wave": f"{base}/wave?v={v}" if (m.get("waveform") or {}).get("count") else "",
            "poster": f"{base}/poster?v={v}" if m.get("poster") else "",
        }
        return view


def process_media(proj: TimelineProject, mid: str) -> None:
    """Sonde puis fabrique proxy, affiche, vignettes et forme d'onde d'un média.

    Tourne dans la file `jobs.MEDIA`. Chaque étape vérifie que le média existe
    encore : il a pu être retiré (ou le projet supprimé) entre-temps.
    """
    m = proj.media(mid)
    if m is None or proj.deleted:
        return
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    src = m["path"]
    last = [0.0]

    def progress(frac: float, lo: float, hi: float) -> None:
        # En mémoire seulement : l'éditeur la lit en interrogeant le moteur.
        now = time.monotonic()
        if now - last[0] > 0.25:
            last[0] = now
            proj.update_media(mid, save=False, progress=round(lo + (hi - lo) * frac, 1))

    try:
        proj.update_media(mid, status="processing", progress=1, error="")
        if not os.path.isfile(src):
            raise ValueError("Fichier introuvable : " + src)
        info = mediatools.probe_media(src)
        if proj.update_media(mid, progress=5, **info) is None:
            return

        name = mediatools.proxy_name(info["kind"])
        proxy = os.path.join(folder, name)
        tmp = os.path.join(folder, "tmp_" + name)
        mediatools.make_proxy(src, tmp, info, on_progress=lambda f: progress(f, 5, 85))
        os.replace(tmp, proxy)
        if proj.update_media(mid, progress=85, proxy_file=name) is None:
            return

        extra: dict = {}
        if info["kind"] in ("video", "image"):
            mediatools.make_poster(proxy, os.path.join(folder, "poster.jpg"), info)
            extra["poster"] = "poster.jpg"
            proj.update_media(mid, save=False, progress=90)
            extra["thumbs"] = mediatools.make_thumbs(proxy, os.path.join(folder, "thumbs.jpg"),
                                                     info)
            proj.update_media(mid, save=False, progress=95)
        if info["kind"] != "image" and info["has_audio"]:
            extra["waveform"] = mediatools.make_waveform(proxy, os.path.join(folder, "wave.bin"))
        with proj.lock:
            m = proj.media(mid)
            if m is None:
                return
            m.update(status="ready", progress=100, rev=int(m.get("rev") or 0) + 1, **extra)
            proj.save()
        if extra.get("poster"):
            _project_thumb(proj, os.path.join(folder, "poster.jpg"))
    except Exception as exc:  # noqa: BLE001 - rangé dans le média, affiché par l'éditeur
        proj.update_media(mid, status="error", error=str(exc)[:400])


def _project_thumb(proj: TimelineProject, poster: str) -> None:
    """Première affiche venue = vignette du projet sur l'écran d'accueil."""
    thumb = os.path.join(proj.dir, "thumb.jpg")
    if not os.path.isfile(thumb):
        try:
            shutil.copyfile(poster, thumb)
        except OSError:
            pass


def default_export_dir(proj: TimelineProject, create: bool = True) -> str:
    """Dossier des exports : `MONTAGE_IA_EXPORTS`, sinon « Vidéos/Montage IA »
    de l'utilisateur (« Movies » sur Mac), sinon le dossier du projet. Créé
    seulement pour écrire."""
    home = os.path.expanduser("~")
    videos = next((os.path.join(home, d) for d in ("Videos", "Movies") if os.path.isdir(os.path.join(home, d))),
                  os.path.join(home, "Videos"))
    target = os.environ.get("MONTAGE_IA_EXPORTS") or (
        os.path.join(videos, "Montage IA") if os.path.isdir(videos) else os.path.join(proj.dir, "exports"))
    if not create:
        return target
    try:
        os.makedirs(target, exist_ok=True)
        return target
    except OSError:
        fallback = os.path.join(proj.dir, "exports")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:120] or "Montage"


def unique_path(folder: str, base: str, ext: str) -> str:
    """`dossier/nom.ext`, ou `nom (2).ext`… : un export n'en écrase jamais un autre."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{base}.{ext}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).{ext}")
        n += 1
    return path


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
