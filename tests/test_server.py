"""API du serveur local. Jamais /api/analyze ni /api/export (travail lourd)."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from engine.core import Options
from engine.edl import Word
from engine.pipeline.probe import MediaInfo
from engine.project import Project


@pytest.fixture
def server(server_module, tmp_path, monkeypatch):
    """Dossier de travail neuf et cache vidé pour chaque test."""
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    server_module.PROJECTS.clear()
    yield server_module
    server_module.PROJECTS.clear()


@pytest.fixture
def client(server):
    try:
        return TestClient(server.app)
    except TypeError as exc:  # starlette < 0.28 avec httpx >= 0.28
        pytest.skip(f"TestClient inutilisable (starlette trop ancien pour cet httpx) : {exc}")


def stored_project(server, pid="p1", language="fr") -> Project:
    proj = Project(pid, os.path.join(server.WORK_DIR, "clip.mp4"), server.WORK_DIR,
                   Options(language=language), os.path.join(server.WORK_DIR, "out.mp4"))
    proj.info = MediaInfo(duration=4.0, width=1920, height=1080, fps=30.0)
    proj.words = [Word("Bonjour", 0.5, 0.9), Word("tout", 1.0, 1.3), Word("le", 1.4, 1.5),
                  Word("monde.", 1.6, 2.0)]
    proj._apply_cuts()
    proj.captions = proj._build_captions()
    proj.save()
    return proj


def test_import_utilise_le_dossier_de_travail_de_l_environnement(server_module):
    assert server_module.WORK_DIR == os.path.abspath(os.environ["MONTAGE_IA_WORK"])


def test_clean_workdir_ne_retire_que_les_fichiers_temporaires(server):
    proj = stored_project(server)
    for name in ("_cut_1.mp4", "_caps_1.ass", "preview_1.mp4", "thumb.jpg"):
        open(os.path.join(proj.dir, name), "wb").close()
    server._clean_workdir()
    assert sorted(os.listdir(proj.dir)) == ["preview_1.mp4", "project.json", "thumb.jpg"]


def test_styles(client):
    styles = client.get("/api/styles").json()["styles"]
    assert {s["name"] for s in styles} >= {"hype", "classic", "clean"}
    assert all({"label", "mode", "size", "hl"} <= s.keys() for s in styles)


def test_projects_vide_puis_liste(client, server):
    assert client.get("/api/projects").json() == {"projects": []}
    stored_project(server, "p1")
    listed = client.get("/api/projects").json()["projects"]
    assert [p["id"] for p in listed] == ["p1"]
    assert listed[0]["name"] == "clip" and listed[0]["captions"] >= 1


def test_project_introuvable(client):
    assert client.get("/api/project/nope").status_code == 404
    assert client.post("/api/recut", json={"job_id": "nope"}).status_code == 404


def test_project_relu_depuis_le_disque(client, server):
    stored_project(server, "p1")
    r = client.get("/api/project/p1")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "p1" and body["language"] == "fr"
    assert {"kept", "stitches", "captions", "translate"} <= body.keys()


def test_save_renomme(client, server):
    stored_project(server, "p1")
    r = client.post("/api/project/p1/save", json={"name": "  Nouveau nom  "})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/projects").json()["projects"][0]["name"] == "Nouveau nom"


def test_recut_keep_ranges_pas_une_liste(client, server):
    stored_project(server, "p1")
    r = client.post("/api/recut", json={"job_id": "p1", "keep_ranges": "1,2"})
    assert r.status_code == 400


# ---------------------------------------------------------------- traduction


def test_translate_captions_pas_une_liste(client, server, fake_model):
    stored_project(server, "p1")
    r = client.post("/api/project/p1/translate", json={"captions": "oops"})
    assert r.status_code == 400


def test_translate_meme_langue(client, server, fake_model):
    stored_project(server, "p1", language="en")
    r = client.post("/api/project/p1/translate", json={"captions": [], "target": "en"})
    assert r.status_code == 400


def test_translate_sans_modele(client, server):
    stored_project(server, "p1")
    r = client.post("/api/project/p1/translate", json={"captions": []})
    assert r.status_code == 400
    assert "fr → en" in r.json()["detail"]


def test_translate_succes(client, server, fake_model, monkeypatch):
    proj = stored_project(server, "p1", language=None)  # langue inconnue : français
    calls = []

    def fake(captions, source, target):
        calls.append((captions, source, target))
        return [{**c, "lang": target} for c in captions]

    monkeypatch.setattr(server, "translate_captions", fake)
    r = client.post("/api/project/p1/translate", json={"captions": proj.captions})
    assert r.status_code == 200
    assert calls == [(proj.captions, "fr", "en")]
    assert [c["id"] for c in r.json()["captions"]] == [c["id"] for c in proj.captions]
    assert all(c["lang"] == "en" for c in r.json()["captions"])


def test_translate_erreur_du_modele(client, server, fake_model, monkeypatch):
    stored_project(server, "p1")

    def boom(*args, **kwargs):
        raise RuntimeError("modèle corrompu")

    monkeypatch.setattr(server, "translate_captions", boom)
    r = client.post("/api/project/p1/translate", json={"captions": []})
    assert r.status_code == 500
    assert "modèle corrompu" in r.json()["detail"]
