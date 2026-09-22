"""Routes HTTP des projets timeline (`/api/timeline/...`).

Branchées par `engine.server`, qui leur fournit le dossier de travail via
`configure` : les tests déplacent ce dossier en cours de route.
"""
from __future__ import annotations

import copy
import os
import re
import shutil
from typing import Callable

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from engine.timeline import media as mediatools
from engine.timeline import model
from engine.timeline.project import TimelineProject

router = APIRouter()

# Cache des projets ouverts ; la vérité est sur disque.
TIMELINES: dict[str, TimelineProject] = {}
_work_dir: Callable[[], str] = lambda: ""   # noqa: E731 - remplacé par configure()


def configure(work_dir: Callable[[], str]) -> None:
    global _work_dir
    _work_dir = work_dir


def get(pid: str) -> TimelineProject:
    """Projet en cache, sinon relu depuis le disque."""
    proj = TIMELINES.get(pid)
    if proj is None or proj.work_dir != _work_dir():
        proj = TimelineProject.load(_work_dir(), pid)
        if proj is None:
            raise HTTPException(404, "Montage introuvable.")
        TIMELINES[pid] = proj
    return proj


def forget(pid: str) -> None:
    """Avant suppression du projet : refuse si un export tourne, sinon oublie-le."""
    proj = TIMELINES.get(pid)
    if proj is None:
        return
    if proj.is_busy:
        raise HTTPException(409, "Une opération est en cours sur ce projet.")
    proj.deleted = True
    TIMELINES.pop(pid, None)


@router.get("/api/timeline/presets")
def presets() -> dict:
    return {
        "canvas": [{"name": k, "w": w, "h": h} for k, (w, h) in model.CANVAS_PRESETS.items()],
        "fps": list(model.FPS_CHOICES),
        "default": model.DEFAULT_CANVAS,
    }


@router.post("/api/timeline")
def create(body: dict = Body(default={})) -> dict:
    """Nouveau montage vide, au format demandé (9:16 par défaut)."""
    preset = str(body.get("canvas") or model.DEFAULT_CANVAS)
    if preset not in model.CANVAS_PRESETS:
        raise HTTPException(400, f"Format inconnu : {preset}")
    proj = TimelineProject.create(_work_dir(), str(body.get("name") or ""), preset)
    TIMELINES[proj.id] = proj
    return {"id": proj.id}


@router.get("/api/timeline/{pid}")
def state(pid: str) -> dict:
    return get(pid).to_dict()


@router.post("/api/timeline/{pid}/save")
def save(pid: str, body: dict = Body(...)) -> dict:
    """Sauvegarde automatique de l'éditeur : pistes, clips, format, réglages, nom."""
    proj = get(pid)
    proj.apply(body)
    return {"ok": True, "updated": proj.state["updated"],
            "duration": model.duration(proj.state["clips"])}


# ------------------------------------------------------------------- médias

MAX_IMPORT = 500          # fichiers par import de dossier


def _safe_name(name: str) -> str:
    """Nom de fichier utilisable sous Windows (le chemin envoyé est ignoré)."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")[:180] or "media"


def _view(proj: TimelineProject, entry: dict) -> dict:
    return proj._media_view(copy.deepcopy(entry))


@router.get("/api/timeline/{pid}/media")
def media_list(pid: str) -> dict:
    """État des médias (interrogé par l'éditeur tant qu'une préparation tourne)."""
    proj = get(pid)
    return {"media": proj.media_views(), "busy": proj.media_busy}


@router.post("/api/timeline/{pid}/media/upload")
async def media_upload(pid: str, request: Request, name: str = Query(...)) -> dict:
    """Téléverse UN fichier, envoyé tel quel dans le corps de la requête.

    Pas de formulaire multipart : le fichier est écrit une seule fois, au fil
    de l'envoi, directement à sa place (un rush de plusieurs Go ne transite
    pas par un fichier temporaire).
    """
    proj = get(pid)
    clean = _safe_name(name)
    if not mediatools.is_media(clean):
        raise HTTPException(400, f"Type de fichier non pris en charge : {clean}")
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "source" + os.path.splitext(clean)[1].lower())
    part = dest + ".part"
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if os.path.getsize(part) == 0:
            raise ValueError("fichier vide")
        os.replace(part, dest)
    except Exception as exc:  # noqa: BLE001 - envoi coupé, disque plein…
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"Envoi de {clean} interrompu ({exc}).") from exc
    return _view(proj, proj.add_media(dest, name=clean, copied=True, mid=mid))


@router.post("/api/timeline/{pid}/media/paths")
def media_paths(pid: str, body: dict = Body(...)) -> dict:
    """Ajoute des fichiers ou des dossiers LOCAUX, lus sur place (aucune copie)."""
    proj = get(pid)
    raw = body.get("paths")
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise HTTPException(400, "`paths` doit être une liste de chemins.")
    recursive = bool(body.get("recursive"))
    known = {os.path.normcase(os.path.abspath(m["path"])) for m in proj.state["media"]}
    added: list[dict] = []
    skipped: list[dict] = []
    for item in raw:
        path = str(item or "").strip().strip('"').strip()
        if not path:
            continue
        if os.path.isdir(path):
            files = mediatools.scan_folder(path, recursive)
            if not files:
                skipped.append({"path": path, "reason": "aucun média dans ce dossier"})
        elif os.path.isfile(path):
            files = [path] if mediatools.is_media(path) else []
            if not files:
                skipped.append({"path": path, "reason": "type de fichier non pris en charge"})
        else:
            skipped.append({"path": path, "reason": "introuvable"})
            continue
        for f in files:
            key = os.path.normcase(os.path.abspath(f))
            if key in known:
                skipped.append({"path": f, "reason": "déjà dans le projet"})
                continue
            if len(added) >= MAX_IMPORT:
                skipped.append({"path": f, "reason": f"plus de {MAX_IMPORT} fichiers"})
                continue
            known.add(key)
            added.append(_view(proj, proj.add_media(f)))
    return {"added": added, "skipped": skipped}


@router.delete("/api/timeline/{pid}/media/{mid}")
def media_delete(pid: str, mid: str) -> dict:
    """Retire un média du projet (et les clips qui l'utilisent)."""
    if not get(pid).remove_media(mid):
        raise HTTPException(404, "Média introuvable.")
    return {"ok": True}


@router.post("/api/timeline/{pid}/media/{mid}/retry")
def media_retry(pid: str, mid: str) -> dict:
    proj = get(pid)
    if not proj.update_media(mid, status="pending", progress=0, error=""):
        raise HTTPException(404, "Média introuvable.")
    proj.queue_media(mid)
    return {"ok": True}


@router.post("/api/timeline/{pid}/media/{mid}/relink")
def media_relink(pid: str, mid: str, body: dict = Body(...)) -> dict:
    """Retrouve un fichier déplacé : même média, nouveau chemin."""
    proj = get(pid)
    path = str(body.get("path") or "").strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(400, f"Fichier introuvable : {path}")
    if not proj.update_media(mid, path=os.path.abspath(path), copied=False,
                             status="pending", progress=0, error=""):
        raise HTTPException(404, "Média introuvable.")
    proj.queue_media(mid)
    return {"ok": True}


_FILES = {
    "thumbs": ("thumbs.jpg", "image/jpeg"),
    "poster": ("poster.jpg", "image/jpeg"),
    "wave": ("wave.bin", "application/octet-stream"),
}
_PROXY_TYPES = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".jpg": "image/jpeg"}


@router.get("/api/timeline/{pid}/media/{mid}/{what}")
def media_file(pid: str, mid: str, what: str):
    """Fichiers dérivés d'un média. L'URL porte sa révision : cache permanent."""
    proj = get(pid)
    m = proj.media(mid)
    if m is None:
        raise HTTPException(404, "Média introuvable.")
    if what == "proxy":
        name = m.get("proxy_file") or ""
        mime = _PROXY_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
    elif what in _FILES:
        name, mime = _FILES[what]
    else:
        raise HTTPException(404, "Fichier inconnu.")
    path = os.path.join(proj.media_folder(mid), name)
    if not name or not os.path.isfile(path):
        raise HTTPException(404, "Pas encore prêt.")
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})
