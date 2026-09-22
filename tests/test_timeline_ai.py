"""Outils automatiques du studio : transcription, sous-titres, silences."""
from __future__ import annotations

import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from engine.edl import Word
from engine.timeline import ai, jobs
from engine.timeline.project import TimelineProject

WORDS = [Word(" Bonjour", 1.0, 1.4), Word(" tout", 1.5, 1.7), Word(" le", 1.8, 1.9),
         Word(" monde", 2.0, 2.4), Word(" l'argent", 5.0, 5.6), Word(" arrive", 5.7, 6.2)]


@pytest.fixture
def fake_whisper(monkeypatch):
    calls = []

    def fake(path, model, device, compute, language, info=None):
        calls.append(path)
        if info is not None:
            info.update(language="fr", device="cpu")
        return list(WORDS)
    monkeypatch.setattr(ai, "transcribe", fake)
    return calls


@pytest.fixture
def proj(tmp_path):
    p = TimelineProject.create(str(tmp_path / "work"), "IA")
    mid = "m1"
    p.state["media"].append({"id": mid, "name": "rush.mp4", "path": str(tmp_path / "rush.mp4"),
                             "kind": "video", "duration": 10.0, "has_audio": True, "status": "ready",
                             "transcript": {"status": "none"}, "proxy_file": ""})
    os.makedirs(p.media_folder(mid), exist_ok=True)
    return p


def video(**kw):
    c = {"id": "c1", "track": "tv1", "kind": "video", "media": "m1", "start": 0.0, "dur": 10.0,
         "in": 0.0, "speed": 1.0}
    c.update(kw)
    return c


def test_transcription_rangee_une_fois(proj, fake_whisper):
    assert ai.queue_transcription(proj, "m1")
    jobs.TRANSCRIBE.join()
    tr = proj.media("m1")["transcript"]
    assert tr["status"] == "done" and tr["count"] == 6 and tr["language"] == "fr"
    words = ai.load_words(proj, "m1")
    assert words[0] == {"text": "Bonjour", "start": 1.0, "end": 1.4}
    assert ai.queue_transcription(proj, "m1")           # déjà fait : rien relancé
    jobs.TRANSCRIBE.join()
    assert len(fake_whisper) == 1
    ai.queue_transcription(proj, "m1", force=True)
    jobs.TRANSCRIBE.join()
    assert len(fake_whisper) == 2


def test_transcription_en_erreur(proj, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("GPU en feu")
    monkeypatch.setattr(ai, "transcribe", boom)
    ai.queue_transcription(proj, "m1")
    jobs.TRANSCRIBE.join()
    tr = proj.media("m1")["transcript"]
    assert tr["status"] == "error" and "GPU en feu" in tr["error"]


def test_pas_de_transcription_sans_son(proj):
    proj.media("m1")["has_audio"] = False
    assert not ai.queue_transcription(proj, "m1")


MEDIA = {"m1": {"id": "m1", "has_audio": True}, "m2": {"id": "m2", "has_audio": False}}
words_of = lambda mid: [{"text": w.text.strip(), "start": w.start, "end": w.end} for w in WORDS]  # noqa: E731


def test_sous_titres_suivent_les_clips():
    caps = ai.build_captions([video(start=3.0, **{"in": 0.5})], MEDIA, words_of,
                             {"words_per_line": 2, "max_chars": 20, "emojis": True, "style": "hype"})
    texts = [" ".join(w["text"] for w in c["words"]) for c in caps]
    assert texts == ["Bonjour tout", "le monde", "l'argent arrive"]   # ligne coupée au long blanc
    first = caps[0]
    assert first["auto"] and first["start"] == pytest.approx(3.5)      # 1,0 source → 3,5 timeline
    assert first["words"][0]["m"] == "m1" and first["words"][0]["s"] == 1.0
    assert first["box"] and first["hl"] == "#00FF66"                    # style « hype »
    assert caps[2]["emoji"] == "💰"


def test_sous_titres_vitesse_son_separe_et_muet():
    fast = ai.build_captions([video(speed=2.0, dur=5.0)], MEDIA, words_of, {})
    assert fast[0]["start"] == pytest.approx(0.5)
    # la vidéo muette de son séparé ne compte pas, son clip audio si
    detached = [video(detached=True), {**video(id="a1", kind="audio", track="ta1")}]
    caps = ai.build_captions(detached, MEDIA, words_of, {})
    assert len({w["text"] for c in caps for w in c["words"]}) == 6
    assert ai.build_captions([video(muted=True)], MEDIA, words_of, {}) == []
    assert ai.build_captions([video(media="m2")], MEDIA, words_of, {}) == []


def test_sous_titres_pas_en_double_pour_deux_clips_superposes():
    caps = ai.build_captions([video(), video(id="c2", track="tv2")], MEDIA, words_of, {})
    starts = [c["start"] for c in caps]
    assert starts == sorted(starts) and len(starts) == len(set(starts))


def test_clip_hors_des_mots():
    assert ai.build_captions([video(**{"in": 7.0, "dur": 3.0})], MEDIA, words_of, {}) == []


# ---------------------------------------------------------------------- API

@pytest.fixture
def client(server_module, tmp_path, monkeypatch, fake_whisper):
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    server_module.timeline_api.TIMELINES.clear()
    try:
        c = TestClient(server_module.app)
    except TypeError as exc:
        pytest.skip(f"TestClient inutilisable : {exc}")
    c.pid = c.post("/api/timeline", json={}).json()["id"]
    proj = server_module.timeline_api.get(c.pid)
    proj.state["media"].append({"id": "m1", "name": "rush.mp4", "path": str(tmp_path / "x.mp4"),
                                "kind": "video", "duration": 10.0, "has_audio": True,
                                "status": "ready", "transcript": {"status": "none"}})
    os.makedirs(proj.media_folder("m1"), exist_ok=True)
    c.proj = proj
    return c


def test_api_sous_titres_apres_transcription(client):
    base = f"/api/timeline/{client.pid}"
    assert client.get(base + "/media/m1/words").status_code == 409
    r = client.post(base + "/captions", json={"clips": [video()]})
    assert r.status_code == 409 and r.json()["detail"]["missing"] == ["m1"]
    assert client.post(base + "/transcribe", json={"media": ["m1", "inconnu"]}).json()["skipped"] == ["inconnu"]
    jobs.TRANSCRIBE.join()
    assert len(client.get(base + "/media/m1/words").json()["words"]) == 6
    r = client.post(base + "/captions", json={"clips": [video(), {"media": "fantome"}, "n'importe quoi"],
                                              "settings": {"words_per_line": 3}}).json()
    assert r["language"] == "fr" and len(r["captions"]) == 3      # 3 + 1 mots, puis 2
    assert client.post(base + "/captions", json={"clips": "x"}).status_code == 400


def test_api_traduction_sans_modele(client):
    r = client.post(f"/api/timeline/{client.pid}/translate", json={"captions": [], "target": "en"})
    assert r.status_code == 400
    assert client.get(f"/api/timeline/{client.pid}/translate/available").json()["available"] is False


@pytest.mark.ffmpeg
def test_api_silences_au_volume(client, tmp_path):
    wav = tmp_path / "pause.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.5", "-f", "lavfi",
                    "-i", "sine=frequency=500:duration=1", "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1",
                    str(wav)], check=True)
    client.proj.media("m1")["path"] = str(wav)
    r = client.get(f"/api/timeline/{client.pid}/media/m1/silences", params={"noise": -40, "min": 0.5}).json()
    assert len(r["silences"]) == 1
    assert os.path.isfile(os.path.join(client.proj.media_folder("m1"), "silences_-40_50.json"))


def test_api_montage_automatique(client, monkeypatch):
    from engine.pipeline import llm
    from engine.timeline import api as tl_api
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(tl_api.autoedit, "face_anchor", lambda path: {"x": 0.4, "y": 0.5, "w": 0.2})
    base = f"/api/timeline/{client.pid}"
    r = client.post(base + "/autoedit", json={"media": ["m1"]})
    assert r.status_code == 409 and r.json()["detail"]["missing"] == ["m1"]
    assert client.post(base + "/autoedit", json={"media": ["inconnu"]}).status_code == 400
    client.post(base + "/transcribe", json={"media": ["m1"]})
    jobs.TRANSCRIBE.join()
    jid = client.post(base + "/autoedit", json={"media": ["m1"], "options": {"llm": True, "trim": True}}).json()["job_id"]
    jobs.TRANSCRIBE.join()
    job = client.get(base + f"/autoedit/{jid}").json()
    assert job["status"] == "done", job
    plan = job["plans"]["m1"]
    assert plan["llm"] is False and plan["sentences"] and plan["cutpoints"]
    assert plan["face"] is None            # pas de proxy vidéo : pas de visage
    # le plan est mis en cache à côté des mots
    folder = client.proj.media_folder("m1")
    assert any(f.startswith("autoedit_") for f in os.listdir(folder))
    assert client.get(base + "/autoedit/nope").status_code == 404
    st = client.get("/api/llm").json()
    assert st["available"] is False and st["download"]["status"] == "idle"
