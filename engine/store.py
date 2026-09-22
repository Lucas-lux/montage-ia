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
import tempfile
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


def replace(src: str, dst: str, tries: int = 20) -> None:
    """`os.replace` qui patiente quand Windows verrouille un instant la cible
    (antivirus, indexation, lecture en cours) au lieu d'échouer."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))


def write_state(work_dir: str, pid: str, data: dict) -> None:
    """Écrit l'état de façon atomique : un crash en cours d'écriture ne doit
    pas laisser un project.json tronqué (donc un projet perdu). Chaque écriture
    a son propre fichier temporaire : deux sauvegardes simultanées (préparation
    d'un média, éditeur) ne se marchent pas dessus."""
    path = state_path(work_dir, pid)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="project.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_state(work_dir: str, pid: str) -> dict | None:
    try:
        with open(state_path(work_dir, pid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def summary(state: dict) -> dict:
    """Fiche courte affichée dans la grille de projets."""
    exp = state.get("export") or None
    if state.get("kind") == "timeline":
        clips = state.get("clips") or []
        canvas = state.get("canvas") or {}
        return {
            "id": state.get("id"),
            "kind": "timeline",
            "name": state.get("name") or "Sans titre",
            "created": state.get("created", 0),
            "updated": state.get("updated", 0),
            "duration": round(max((c.get("start", 0) + c.get("dur", 0) for c in clips),
                                  default=0.0), 3),
            "media": len(state.get("media") or []),
            "clips": sum(1 for c in clips if c.get("kind") != "text"),
            "captions": sum(1 for c in clips if c.get("kind") == "text"),
            "canvas": [canvas.get("w", 1080), canvas.get("h", 1920)],
            "vertical": canvas.get("h", 1920) > canvas.get("w", 1080),
            "exported_at": (exp or {}).get("at"),
            "ready": True,
        }
    return {
        "id": state.get("id"),
        "kind": "short",
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
        # Faux si l'analyse n'a jamais abouti (plantage, fermeture en cours de route).
        "ready": bool(state.get("words")) and bool(state.get("info")),
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
