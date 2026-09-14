"""Modèles de données partagés du pipeline.

Principe : on analyse la vidéo UNE fois (mots horodatés), puis éditer =
modifier ces données en mémoire (instantané) ; seul l'export final repasse
dans ffmpeg.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class Word:
    """Un mot horodaté (sortie de Whisper, puis remappé sur la timeline coupée)."""
    text: str
    start: float
    end: float


class KeepSegment(BaseModel):
    """Un intervalle de la source à GARDER (le reste = blanc supprimé)."""
    start: float
    end: float
