"""Routes HTTP des projets timeline (`/api/timeline/...`).

Branchées par `engine.server`, qui leur fournit le dossier de travail via
`configure` : les tests déplacent ce dossier en cours de route.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Body, HTTPException

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
