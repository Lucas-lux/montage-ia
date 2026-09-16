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


# ------------------------------------------------ analyse qui n'a pas abouti


def broken_project(server, pid="b1", with_source=True) -> Project:
    """Projet enregistré à l'import mais dont la transcription a planté."""
    source = os.path.join(server.WORK_DIR, "projects", pid, "source.mp4")
    proj = Project(pid, source, server.WORK_DIR, Options(device="cuda", compute_type="float16"),
                   os.path.join(server.WORK_DIR, "out.mp4"), "rush 60 ips")
    proj.save()
    if with_source:
        open(source, "wb").close()
    return proj


def test_projet_non_analyse_marque_non_pret(client, server):
    stored_project(server, "p1")
    broken_project(server, "b1")
    ready = {p["id"]: p["ready"] for p in client.get("/api/projects").json()["projects"]}
    assert ready == {"p1": True, "b1": False}


def test_reanalyze_relance_sur_le_disque(client, server, monkeypatch):
    broken_project(server, "b1")
    spawned = []
    monkeypatch.setattr(server, "_spawn", lambda fn, *a, **k: spawned.append(fn))
    r = client.post("/api/project/b1/reanalyze")
    assert r.status_code == 200
    proj = server.PROJECTS["b1"]
    assert spawned == [proj.analyze]
    assert proj.name == "rush 60 ips"
    assert proj.opts.device == "auto"  # l'ancien « cuda » imposé peut se replier sur le CPU


def test_reanalyze_refus(client, server, monkeypatch):
    monkeypatch.setattr(server, "_spawn", lambda *a, **k: None)
    stored_project(server, "p1")
    broken_project(server, "sans_video", with_source=False)
    assert client.post("/api/project/nope/reanalyze").status_code == 404
    assert client.post("/api/project/p1/reanalyze").status_code == 400
    r = client.post("/api/project/sans_video/reanalyze")
    assert r.status_code == 400 and "disparu" in r.json()["detail"]


def test_reanalyze_pendant_une_analyse(client, server):
    proj = broken_project(server, "b1")
    proj.task["status"] = "running"
    server.PROJECTS["b1"] = proj
    assert client.post("/api/project/b1/reanalyze").status_code == 409


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
