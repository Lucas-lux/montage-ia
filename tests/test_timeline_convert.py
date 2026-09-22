"""Ouvrir un projet short dans la timeline."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from engine.core import Options
from engine.edl import Word
from engine.pipeline.probe import MediaInfo
from engine.project import Project
from engine.timeline import ai, jobs


@pytest.fixture
def client(server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    server_module.timeline_api.TIMELINES.clear()
    server_module.PROJECTS.clear()
    try:
        return TestClient(server_module.app)
    except TypeError as exc:
        pytest.skip(f"TestClient inutilisable : {exc}")


def short_project(server, tmp_path):
    src = tmp_path / "rush.mp4"
    src.write_bytes(b"pas vraiment une video")
    proj = Project("s1", str(src), server.WORK_DIR, Options(max_gap=0.5), str(tmp_path / "out.mp4"), "Mon short")
    proj.info = MediaInfo(duration=6.0, width=1920, height=1080, fps=29.97)
    proj.out_w, proj.out_h = 1080, 1920
    proj.words = [Word("Bonjour", 0.5, 0.9), Word("tout", 1.0, 1.3), Word("le", 3.0, 3.2), Word("monde", 3.3, 3.8)]
    proj.language = "fr"
    proj._apply_cuts()
    proj.captions = proj._build_captions()
    proj.captions[0]["y"] = 0.3          # une retouche à garder
    proj.save()
    return proj


def test_conversion(client, server_module, tmp_path):
    short = short_project(server_module, tmp_path)
    assert len(short.keep) == 2          # le blanc de 1,3 à 3,0 s est coupé
    r = client.post("/api/timeline/from-short/s1")
    assert r.status_code == 200
    tl = server_module.timeline_api.get(r.json()["id"])
    st = tl.state
    assert st["name"] == "Mon short (timeline)" and st["canvas"]["w"] == 1080 and st["canvas"]["fps"] == 30
    [m] = st["media"]
    assert m["path"] == short.source and not m["copied"]
    assert m["transcript"]["status"] == "done" and len(ai.load_words(tl, m["id"])) == 4
    vids = sorted((c for c in st["clips"] if c["kind"] == "video"), key=lambda c: c["start"])
    assert [(c["start"], c["in"]) for c in vids] == [(0.0, short.keep[0].start),
                                                    (round(short.keep[0].end - short.keep[0].start, 4),
                                                     short.keep[1].start)]
    caps = [c for c in st["clips"] if c["kind"] == "text"]
    words = [w for c in caps for w in c["words"]]
    assert [w["text"] for w in words] == ["Bonjour", "tout", "le", "monde"]
    assert all(w["m"] == m["id"] for w in words)
    assert [w["s"] for w in words] == pytest.approx([0.5, 1.0, 3.0, 3.3], abs=0.01)
    assert caps[0]["auto"] and caps[0]["y"] == 0.3
    jobs.MEDIA.join()                    # la préparation du faux média échoue proprement
    assert tl.media(m["id"])["status"] == "error"
    # le projet short est intact
    assert client.get("/api/project/s1").status_code == 200


def test_conversion_refusee(client, server_module, tmp_path):
    assert client.post("/api/timeline/from-short/inconnu").status_code == 400
    short = short_project(server_module, tmp_path)
    os.remove(short.source)
    server_module.PROJECTS.clear()
    r = client.post("/api/timeline/from-short/s1")
    assert r.status_code == 400 and "disparu" in r.json()["detail"]
