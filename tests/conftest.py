"""Fixtures communes : aucun GPU, aucun modèle, aucune vraie vidéo."""
from __future__ import annotations

import importlib
import os
import shutil
import sys

import pytest


def pytest_collection_modifyitems(config, items):
    """Les tests `ffmpeg` sont ignorés si ffmpeg/ffprobe manquent."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    skip = pytest.mark.skip(reason="ffmpeg/ffprobe introuvable dans le PATH")
    for item in items:
        if item.get_closest_marker("ffmpeg"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_translate_model(tmp_path_factory, monkeypatch):
    """Dossier de modèles de traduction vide : jamais le vrai modèle du dépôt."""
    root = tmp_path_factory.mktemp("translate_models")
    monkeypatch.setenv("MONTAGE_IA_TRANSLATE", str(root))
    return root


@pytest.fixture
def fake_model(no_translate_model):
    """Installe un faux modèle fr->en (seule la présence des fichiers compte)."""
    d = no_translate_model / "opus-mt-fr-en"
    d.mkdir(exist_ok=True)
    for name in ("model.bin", "source.spm", "target.spm"):
        (d / name).write_bytes(b"")
    return d


@pytest.fixture(scope="session")
def server_module(tmp_path_factory):
    """Importe engine.server APRÈS avoir posé MONTAGE_IA_WORK : l'import
    calcule WORK_DIR et y fait le ménage, jamais dans le vrai dossier work/."""
    work = tmp_path_factory.mktemp("server_work")
    old = os.environ.get("MONTAGE_IA_WORK")
    os.environ["MONTAGE_IA_WORK"] = str(work)
    try:
        if "engine.server" in sys.modules:
            mod = importlib.reload(sys.modules["engine.server"])
        else:
            mod = importlib.import_module("engine.server")
        yield mod
    finally:
        if old is None:
            os.environ.pop("MONTAGE_IA_WORK", None)
        else:
            os.environ["MONTAGE_IA_WORK"] = old
