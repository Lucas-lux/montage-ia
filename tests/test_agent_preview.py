"""Aperçus et visuels des agents : un aperçu périmé est refait, un profil de
navigateur qui refuse de servir est remplacé par un profil neuf."""
from __future__ import annotations

import os
import subprocess

import pytest

from engine.agent import preview, visuals
from engine.timeline.project import TimelineProject


def test_un_apercu_d_une_revision_passee_est_refait(tmp_path, monkeypatch):
    from engine.timeline import render
    proj = TimelineProject.create(str(tmp_path / "work"), "Aperçu")
    proj.state["clips"] = [{"id": "x", "kind": "text", "track": "t", "start": 0, "dur": 1}]
    calls = []

    def fake_export(state, media, out, **kw):
        calls.append(out)
        open(out, "wb").close()
        return {"duration": 1.0, "width": 270, "height": 480}
    monkeypatch.setattr(render, "export", fake_export)
    preview.PREVIEWS[proj.id] = {"status": "done", "rev": proj.rev - 1, "path": "ancien.mp4", "height": 480}
    job = preview.start(proj, 480, 15)
    assert job["rev"] == proj.rev and not job.get("stale")
    for _ in range(100):
        if preview.status(proj)["status"] == "done":
            break
        import time
        time.sleep(0.02)
    assert preview.status(proj)["status"] == "done" and len(calls) == 1
    assert preview.start(proj, 480, 15)["status"] == "done" and len(calls) == 1   # à jour : rien de refait


def test_navigateur_qui_se_relance_dans_un_autre_processus(tmp_path, monkeypatch):
    """Edge lancé depuis l'application installée se relance ailleurs et le processus
    lancé s'arrête aussitôt : un lanceur qui démarre le navigateur à part fait pareil."""
    edge = visuals.find_browser()
    if os.name != "nt" or not edge:
        pytest.skip("Windows avec Edge ou Chrome")
    bat = tmp_path / "relance.bat"
    bat.write_text(f'@start "" "{edge}" %*\r\n', encoding="utf-8")
    monkeypatch.setattr(visuals, "find_browser", lambda: str(bat))
    prof = str(tmp_path / "profil")
    proc, cdp = visuals._devtools(str(bat), 320, 240, prof)
    try:
        assert cdp.eval("1 + 1") == 2
        assert proc.poll() is not None                  # le processus lancé est déjà parti
    finally:
        visuals._stop(proc, cdp, prof)
    assert not os.path.exists(os.path.join(prof, "lockfile"))     # navigateur fermé, profil libéré
    out = str(tmp_path / "v.png")
    visuals.render("<b style='font-size:30px'>x</b>", 64, 32, out, str(tmp_path / "profil2"))
    assert os.path.getsize(out) > 0


def test_profil_de_navigateur_remplace_s_il_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(visuals, "find_browser", lambda: "edge.exe")
    profiles = []

    def fake_run(args, **kw):
        prof = next(a for a in args if a.startswith("--user-data-dir="))
        profiles.append(prof)
        shot = next(a for a in args if a.startswith("--screenshot=")).split("=", 1)[1]
        if len(profiles) == 2:                       # le profil gardé échoue, le profil neuf réussit
            from PIL import Image
            Image.new("RGBA", (64, 32), (0, 0, 0, 0)).save(shot)
        return subprocess.CompletedProcess(args, 0 if len(profiles) == 2 else 21, "", "")
    monkeypatch.setattr(visuals.subprocess, "run", fake_run)
    out = str(tmp_path / "v.png")
    visuals.render("<b>x</b>", 64, 32, out, str(tmp_path / "profil"))
    assert os.path.isfile(out) and len(profiles) == 2 and str(tmp_path / "profil") in profiles[0]
