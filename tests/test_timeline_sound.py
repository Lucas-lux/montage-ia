"""« Optimiser le son » (engine/timeline/sound.py) : mesures sur des sons
fabriqués dont on connaît les niveaux, réglages qui en découlent, chaîne ffmpeg."""
from __future__ import annotations

import os
import wave

import numpy as np
import pytest

from engine.timeline import model, render, sound
from engine.timeline.project import TimelineProject

SR = 48000
pytestmark = pytest.mark.ffmpeg


def voice(seconds=8.0, level_db=-18.0, noise_db=-60.0, clip=False, seed=1):
    """Une « voix » : des syllabes (bruit filtré 150-4000 Hz, enveloppes de
    120-300 ms) en phrases de 1,5 s séparées de 0,6 s, sur un bruit de fond."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = np.zeros(n)
    words, cur = [], 0.3
    while cur < seconds - 1.8:
        end = cur + 1.5
        s = cur
        while s < end:
            d = rng.uniform(0.12, 0.3)
            a, b = int(s * SR), int(min(s + d, end) * SR)
            env = np.hanning(b - a)
            f0 = rng.uniform(110, 180)
            seg = sum(np.sin(2 * np.pi * f0 * k * t[a:b]) / k for k in range(1, 25))
            x[a:b] += env * seg * rng.uniform(0.6, 1.0)
            words.append({"text": "mot", "start": round(s, 3), "end": round(s + d, 3)})
            s += d + 0.04
        cur = end + 0.6
    x /= np.sqrt(np.mean(x[np.abs(x) > 1e-4] ** 2)) + 1e-9
    x *= 10 ** (level_db / 20)
    x += rng.normal(0, 10 ** (noise_db / 20), n)
    if clip:
        x = np.clip(x * 4, -1, 1)
    return x, words


def write(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return str(path)


def test_une_voix_propre_mesuree_et_reglee(tmp_path):
    x, words = voice(level_db=-30, noise_db=-75)
    a = sound.analyze(write(tmp_path / "v.wav", x), words)
    assert a["speech"] and -34 < a["speech_db"] < -26
    assert a["noise_db"] < -65 and a["snr"] > 35
    fx = sound.recommend(a)
    assert fx["preset"] == "auto" and fx["lowcut"]
    assert 6 <= fx["gain"] <= 14                       # voix faible : remontée au niveau de travail
    assert fx.get("denoise", 0) <= 0.25 and not fx.get("declip")


def test_une_voix_bruitee_est_debruitee_et_la_porte_calee_sur_le_bruit(tmp_path):
    x, words = voice(level_db=-16, noise_db=-36)
    a = sound.analyze(write(tmp_path / "v.wav", x), words)
    assert 14 < a["snr"] < 26
    fx = sound.recommend(a)
    assert fx["denoise"] >= 0.5 and fx["gate"]
    chain = ",".join(render.voice_chain(fx, rnnoise=True))
    thr = float(chain.split("agate=threshold=")[1].split(":")[0])
    assert 10 ** (-60 / 20) <= thr <= 10 ** (-30 / 20)
    assert "volume=" in chain and chain.index("volume=") < chain.index("highpass")


def test_une_voix_saturee_est_reparee(tmp_path):
    x, words = voice(level_db=-10, noise_db=-70, clip=True)
    a = sound.analyze(write(tmp_path / "v.wav", x), words)
    assert a["clipped"] > 1e-4
    fx = sound.recommend(a)
    assert fx["declip"] and fx["gain"] < 0
    assert render.voice_chain(fx)[0] == "adeclip"


def test_sans_transcription_la_voix_se_trouve_a_l_energie(tmp_path):
    x, _ = voice(level_db=-20, noise_db=-65)
    a = sound.analyze(write(tmp_path / "v.wav", x), None)
    assert a["speech"] and abs(a["speech_db"] + 20) < 5 and a["snr"] > 30


def test_une_musique_passe_sous_la_voix(tmp_path):
    t = np.arange(int(10 * SR)) / SR
    music = 0.3 * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 330 * t))
    a = sound.analyze(write(tmp_path / "m.wav", music), None)
    assert a["speech"] is False and a["level_db"] > -15
    vol = sound.music_volume(a)
    assert 0.03 <= vol < 0.3


def test_optimiser_un_montage(tmp_path):
    proj = TimelineProject.create(str(tmp_path / "work"), "Son")
    x, words = voice(seconds=12, level_db=-28, noise_db=-45)
    vpath = write(tmp_path / "voix.wav", x)
    t = np.arange(int(12 * SR)) / SR
    mpath = write(tmp_path / "musique.wav", 0.4 * np.sin(2 * np.pi * 196 * t))
    for mid, path in (("mv", vpath), ("mm", mpath)):
        proj.state["media"].append({"id": mid, "name": os.path.basename(path), "path": path, "kind": "audio",
                                    "duration": 12.0, "has_audio": True, "status": "ready",
                                    "transcript": {"status": "none"}})
    clips = [{"id": "c1", "kind": "audio", "media": "mv", "start": 0, "dur": 12, "track": "ta1"},
             {"id": "c2", "kind": "audio", "media": "mm", "start": 0, "dur": 12, "track": "ta2"},
             {"id": "c3", "kind": "audio", "media": "mm", "start": 20, "dur": 1.0, "track": "ta2"}]
    plan = sound.optimize(proj, clips)
    assert plan["loudness"] and set(plan["voice"]) == {"c1"} and plan["voice_media"]["mv"]["gain"] > 0
    assert set(plan["volume"]) == {"c2"}             # c3 : trop court et hors de la voix (un effet sonore)
    assert {e["kind"] for e in plan["report"]} == {"voice", "music"}
    assert os.path.isfile(os.path.join(proj.media_folder("mv"), "sound.json"))       # mesure gardée
    doc = {"clips": [dict(c) for c in clips], "settings": {}}
    assert sound.apply(doc, plan) == 2
    assert doc["clips"][0]["audio_fx"]["preset"] == "auto" and doc["clips"][1]["volume"] == plan["volume"]["c2"]
    assert doc["settings"]["loudness"] is True
    # le modèle garde les réglages mesurés (gain, bruit, réparation des crêtes)
    fx = model.normalize_voice_fx({**plan["voice_media"]["mv"], "declip": True})
    assert fx["gain"] == plan["voice_media"]["mv"]["gain"] and "noise" in fx and fx["declip"]


def test_route_du_studio(server_module, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from engine.timeline import api as timeline_api
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    timeline_api.TIMELINES.clear()
    client = TestClient(server_module.app)
    pid = client.post("/api/timeline", json={"name": "Son"}).json()["id"]
    proj = timeline_api.get(pid)
    x, _ = voice(level_db=-22)
    path = write(tmp_path / "v.wav", x)
    proj.state["media"].append({"id": "m1", "name": "v.wav", "path": path, "kind": "audio", "duration": 8.0,
                                "has_audio": True, "status": "ready", "transcript": {"status": "none"}})
    r = client.post(f"/api/timeline/{pid}/sound/optimize", json={"clips": [
        {"id": "a1", "kind": "audio", "media": "m1", "start": 0, "dur": 8}, {"id": "t1", "kind": "text"}]}).json()
    assert r["voice"]["a1"]["preset"] == "auto" and r["loudness"]
    assert client.post(f"/api/timeline/{pid}/sound/optimize", json={}).status_code == 400
    timeline_api.TIMELINES.clear()


def test_outil_des_agents(server_module, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from engine.timeline import api as timeline_api
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    timeline_api.TIMELINES.clear()
    client = TestClient(server_module.app)
    pid = client.post("/api/agent/projects", json={"name": "Son"}).json()["id"]
    proj = timeline_api.get(pid)
    x, _ = voice(level_db=-30, noise_db=-50)
    path = write(tmp_path / "v.wav", x)
    proj.state["media"].append({"id": "m1", "name": "v.wav", "path": path, "kind": "audio", "duration": 8.0,
                                "has_audio": True, "status": "ready", "transcript": {"status": "none"}})
    proj.apply({"clips": [{"id": "a1", "kind": "audio", "track": "ta1", "media": "m1", "start": 0, "dur": 8}]})
    r = client.post(f"/api/agent/{pid}/sound", json={}).json()
    assert r["clips"] == 1 and r["report"][0]["kind"] == "voice"
    state = client.get(f"/api/timeline/{pid}").json()
    fx = state["clips"][0]["audio_fx"]
    assert fx["preset"] == "auto" and fx["gain"] > 5 and state["settings"]["loudness"] is True
    assert client.post(f"/api/agent/{pid}/undo").json()["undone"] == "optimize_sound"
    timeline_api.TIMELINES.clear()
