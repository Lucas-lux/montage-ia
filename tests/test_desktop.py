"""Application de bureau : choix de la fenêtre, instance unique, processus enfants."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

import app


def test_fenetre_native_sauf_demande_contraire(monkeypatch):
    monkeypatch.delenv("MONTAGE_IA_BROWSER", raising=False)
    assert app.native_window() == (os.name == "nt")
    monkeypatch.setenv("MONTAGE_IA_BROWSER", "1")
    assert not app.native_window()


def test_instance_propre_a_son_dossier_de_projets(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "user_dir", lambda: str(tmp_path))
    monkeypatch.setenv("MONTAGE_IA_WORK", str(tmp_path / "vrai"))
    app.remember_instance("http://127.0.0.1:1")
    assert (tmp_path / "instance.json").is_file()
    # une instance d'essai (autre dossier) ignore celle de l'utilisateur
    monkeypatch.setenv("MONTAGE_IA_WORK", str(tmp_path / "essai"))
    assert app.running_instance() is None
    # même dossier, mais plus personne ne répond à cette adresse
    monkeypatch.setenv("MONTAGE_IA_WORK", str(tmp_path / "vrai"))
    assert app.running_instance() is None
    app.forget_instance()
    assert not (tmp_path / "instance.json").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="job Windows")
def test_ffmpeg_lie_a_l_application(monkeypatch):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe absent")
    import ctypes

    import desktop
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)      # remis en place après l'essai
    desktop.tie_child_processes(no_window=True)
    assert subprocess.Popen._montage
    proc = subprocess.Popen(["ffprobe", "-version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    inside = ctypes.c_int(0)
    ctypes.windll.kernel32.IsProcessInJob(ctypes.c_void_p(int(proc._handle)), None, ctypes.byref(inside))
    out = proc.communicate()[0]
    assert inside.value, "ffprobe n'est pas rangé dans le job de l'application"
    assert b"ffprobe version" in out
    # subprocess.run et ses options habituelles marchent toujours
    res = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0 and "ffprobe" in res.stdout
