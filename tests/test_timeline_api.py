"""Routes des projets timeline."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    server_module.timeline_api.TIMELINES.clear()
    yield server_module
    server_module.timeline_api.TIMELINES.clear()


@pytest.fixture
def client(server):
    try:
        return TestClient(server.app)
    except TypeError as exc:  # starlette < 0.28 avec httpx >= 0.28
        pytest.skip(f"TestClient inutilisable : {exc}")


def test_pages_et_fichiers_statiques(client):
    assert "Montage IA" in client.get("/studio").text
    r = client.get("/web/studio/main.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_presets(client):
    data = client.get("/api/timeline/presets").json()
    assert data["default"] == "9:16"
    assert {"name": "16:9", "w": 1920, "h": 1080} in data["canvas"]


def test_creer_lire_sauver(client):
    pid = client.post("/api/timeline", json={"name": "Vlog", "canvas": "1:1"}).json()["id"]
    state = client.get(f"/api/timeline/{pid}").json()
    assert state["name"] == "Vlog" and state["canvas"]["w"] == state["canvas"]["h"] == 1080
    assert state["duration"] == 0 and state["task"]["status"] == "idle"

    r = client.post(f"/api/timeline/{pid}/save",
                    json={"name": "Vlog 2", "canvas": {"w": 1920, "h": 1080, "fps": 25}})
    assert r.json()["ok"]
    state = client.get(f"/api/timeline/{pid}").json()
    assert state["name"] == "Vlog 2" and state["canvas"]["fps"] == 25


def test_format_refuse(client):
    assert client.post("/api/timeline", json={"canvas": "2:7"}).status_code == 400


def test_montage_inconnu(client):
    assert client.get("/api/timeline/nope").status_code == 404
    assert client.post("/api/timeline/nope/save", json={}).status_code == 404


def test_liste_commune_et_suppression(client, server):
    pid = client.post("/api/timeline", json={"name": "Film"}).json()["id"]
    projects = client.get("/api/projects").json()["projects"]
    assert [(p["id"], p["kind"], p["name"]) for p in projects] == [(pid, "timeline", "Film")]

    assert client.delete(f"/api/projects/{pid}").json()["ok"]
    assert client.get("/api/projects").json()["projects"] == []
    assert client.get(f"/api/timeline/{pid}").status_code == 404


def test_un_projet_short_n_est_pas_un_montage(client, server):
    server.store.write_state(server.WORK_DIR, "s1", {"id": "s1", "name": "Short",
                                                    "words": [{"text": "a", "start": 0, "end": 1}]})
    assert client.get("/api/timeline/s1").status_code == 404
    [p] = client.get("/api/projects").json()["projects"]
    assert p["kind"] == "short"
