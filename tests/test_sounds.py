"""Effets sonores : synthèse de la bibliothèque, « Mes sons », ajout à un projet."""
from __future__ import annotations

import io
import shutil
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine.tools import sfx


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


def test_chaque_son_de_la_bibliotheque(tmp_path):
    lib = sfx.Sounds(str(tmp_path)).library()
    assert len(lib) >= 60 and {s["category"] for s in lib} == set(sfx.CATEGORIES)
    assert len({s["id"] for s in lib}) == len(lib)
    for s in lib:
        x = sfx.synth(s["engine"], s["params"])
        assert x.shape[1] == 2 and 0.02 < len(x) / sfx.SR <= sfx.MAX_DUR + 1, s["id"]
        assert np.isfinite(x).all() and 0.5 < np.max(np.abs(x)) <= 1.0, s["id"]


def test_meme_reglage_meme_son():
    a = sfx.synth("whoosh", {"dur": 0.4, "pitch": 3})
    assert np.array_equal(a, sfx.synth("whoosh", {"dur": 0.4, "pitch": 3}))
    assert not np.array_equal(a, sfx.synth("whoosh", {"dur": 0.4, "pitch": -3}))
    # réglages bornés, inconnus ignorés
    p = sfx.params_of("pop", {"dur": 99, "nimporte": 1, "reverse": 1})
    assert p["dur"] == 0.8 and "nimporte" not in p and p["reverse"] == 1


def _wav_bytes(seconds=0.5):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        t = np.arange(int(22050 * seconds)) / 22050
        w.writeframes((np.sin(2 * np.pi * 440 * t) * 20000).astype("<i2").tobytes())
    return buf.getvalue()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
def test_api_des_sons(client):
    data = client.get("/api/sounds").json()
    assert data["library"] and data["mine"] == [] and data["engines"] and data["categories"]
    first = data["library"][0]
    r = client.get(first["url"])
    assert r.status_code == 200 and r.content[:4] == b"RIFF"

    # créer un son : l'écouter, puis le garder
    pv = client.post("/api/sounds/preview", json={"engine": "laser", "params": {"dur": 0.3}}).json()
    assert pv["id"].startswith("tmp_") and client.get(pv["url"]).status_code == 200
    assert client.post("/api/sounds/preview", json={"engine": "rien"}).status_code == 400
    mine = client.post("/api/sounds/mine", json={"name": "Mon laser", "engine": "laser",
                                                  "params": {"dur": 0.3}}).json()
    assert mine["label"] == "Mon laser" and mine["source"] == "synth"

    # importer un son (converti en WAV 48 kHz stéréo), le renommer, le supprimer
    up = client.post("/api/sounds/mine/upload?name=Bip.wav", content=_wav_bytes()).json()
    assert up["label"] == "Bip" and up["dur"] == pytest.approx(0.5, abs=0.05)
    assert client.patch(f"/api/sounds/mine/{up['id']}", json={"name": "Bip 2"}).json()["label"] == "Bip 2"
    assert [x["id"] for x in client.get("/api/sounds").json()["mine"]] == [up["id"], mine["id"]]
    assert client.delete(f"/api/sounds/mine/{up['id']}").json()["ok"]
    assert client.post("/api/sounds/mine/upload?name=vide", content=b"").status_code == 400

    # dans un projet : un média comme un autre, copié dans le projet
    pid = client.post("/api/timeline", json={}).json()["id"]
    m = client.post(f"/api/timeline/{pid}/sounds/{first['id']}").json()
    assert m["copied"] and m["name"].endswith(".wav") and m["kind"] == "audio"
    assert client.post(f"/api/timeline/{pid}/sounds/inconnu").status_code == 404
