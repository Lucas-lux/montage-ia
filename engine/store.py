r"""Persistance des projets sur disque.

Un montage ne doit pas disparaître parce qu'on a fermé le serveur : chaque
projet vit dans son dossier, avec son état complet (mots transcrits, réglages
de coupe, sous-titres édités, dernier export), son proxy de prévisualisation et
sa vignette. C'est ce qui rend possible l'écran de sélection de projet.

    work/projects/<id>/
        project.json      état du montage (tout sauf les vidéos)
        preview_<n>.mp4   proxy lu par l'éditeur
        thumb.jpg         vignette de l'écran d'accueil
        source.<ext>      la vidéo importée (si elle a été téléversée)

Ce qui est stocké : les MOTS et les réglages, pas les coupes. Les segments
gardés se redéduisent des deux à l'ouverture — moins de données à garder
cohérentes, et aucun risque qu'un fichier décrive un montage impossible.
"""
from __future__ import annotations

import json
import os
import shutil
import time

PROJECTS = "projects"


def root(work_dir: str) -> str:
    path = os.path.join(work_dir, PROJECTS)
    os.makedirs(path, exist_ok=True)
    return path


def project_dir(work_dir: str, pid: str, create: bool = False) -> str:
    path = os.path.join(root(work_dir), pid)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def state_path(work_dir: str, pid: str) -> str:
    return os.path.join(project_dir(work_dir, pid), "project.json")


def write_state(work_dir: str, pid: str, data: dict) -> None:
    """Écrit l'état de façon atomique : un crash en cours d'écriture ne doit
    pas laisser un project.json tronqué (donc un projet perdu)."""
    path = state_path(work_dir, pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_state(work_dir: str, pid: str) -> dict | None:
    try:
        with open(state_path(work_dir, pid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def summary(state: dict) -> dict:
    """Fiche courte affichée dans la grille de projets."""
    exp = state.get("export") or None
    return {
        "id": state.get("id"),
        "name": state.get("name") or "Sans titre",
        "source_name": os.path.basename(state.get("source") or ""),
        "created": state.get("created", 0),
        "updated": state.get("updated", 0),
        "duration": state.get("duration", 0),
        "source_duration": state.get("source_duration", 0),
        "removed": state.get("removed", 0),
        "captions": len(state.get("captions") or []),
        "vertical": bool((state.get("opts") or {}).get("vertical", True)),
        "exported_at": (exp or {}).get("at"),
    }


def list_projects(work_dir: str) -> list[dict]:
    """Tous les projets, du plus récemment modifié au plus ancien."""
    out: list[dict] = []
    for pid in os.listdir(root(work_dir)):
        if not os.path.isdir(os.path.join(root(work_dir), pid)):
            continue
        state = read_state(work_dir, pid)
        if state and state.get("id"):
            out.append(summary(state))
    out.sort(key=lambda p: p.get("updated") or 0, reverse=True)
    return out


def delete_project(work_dir: str, pid: str) -> bool:
    path = project_dir(work_dir, pid)
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.isdir(path)


def now() -> float:
    return round(time.time(), 3)
