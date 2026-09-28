"""Routes des agents IA (`/api/agent/...`) et cohabitation avec le studio."""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from engine.agent import service
from engine.timeline import api as timeline_api

SENTS = [("Arrête", "de", "perdre", "ton", "temps."), ("Bonjour", "à", "tous."),
         ("Voici", "la", "première", "astuce."), ("Voici", "la", "première", "astuce."),
         ("Abonne-toi", "pour", "la", "suite.")]


@pytest.fixture
def server(server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    timeline_api.TIMELINES.clear()
    service.HISTORY.clear()
    yield server_module
    timeline_api.TIMELINES.clear()


@pytest.fixture
def client(server):
    try:
        return TestClient(server.app)
    except TypeError as exc:  # starlette < 0.28 avec httpx >= 0.28
        pytest.skip(f"TestClient inutilisable : {exc}")


@pytest.fixture
def pid(client, tmp_path):
    """Projet avec un rush « prêt » et transcrit (5 phrases, dont une reprise et une fin d'appel)."""
    p = client.post("/api/agent/projects", json={"name": "Agent", "format": "9:16"}).json()["id"]
    proj = timeline_api.get(p)
    words, t = [], 0.3
    for sent in SENTS:
        for word in sent:
            words.append({"text": word, "start": round(t, 2), "end": round(t + 0.3, 2)})
            t += 0.35
        t += 1.2
    mid = "m1"
    proj.state["media"].append({"id": mid, "name": "rush.mp4", "path": str(tmp_path / "rush.mp4"), "kind": "video",
                                "duration": round(t + 1, 2), "w": 1080, "h": 1920, "fps": 30, "has_audio": True,
                                "status": "ready", "transcript": {"status": "done", "count": len(words),
                                                                  "language": "fr"}, "proxy_file": ""})
    os.makedirs(proj.media_folder(mid), exist_ok=True)
    with open(os.path.join(proj.media_folder(mid), "words.json"), "w", encoding="utf-8") as f:
        json.dump({"words": words, "language": "fr"}, f)
    proj.save()
    return p


def test_infos_et_catalogues(client):
    info = client.get("/api/agent/info").json()
    assert info["fonts"] >= 40 and info["caption_styles"] >= 60 and info["title_styles"] >= 40
    assert "fade" in info["transitions"] and "9:16" in info["formats"]
    titles = client.get("/api/agent/catalog/title_styles").json()["items"]
    assert any(t["name"] == "neon_rose" for t in titles)
    anims = client.get("/api/agent/catalog/animations").json()["items"]
    assert {"pop", "typewriter", "pulse"} <= {a["type"] for a in anims}
    sounds = client.get("/api/agent/catalog/sounds").json()
    assert any(s["id"] == "whoosh" for s in sounds["items"])
    assert client.get("/api/agent/catalog/nope").status_code == 404


def test_transcription_et_analyse(client, pid):
    tr = client.get(f"/api/agent/{pid}/transcript").json()
    assert tr["sentences_total"] == 5 and tr["sentences"][0]["text"].startswith("Arrête")
    flags = {s["i"]: s["flags"] for s in tr["sentences"]}
    assert "fluff" in flags[1] and "retake" in flags[2] and "fluff" in flags[4]
    words = client.get(f"/api/agent/{pid}/transcript", params={"words": "true", "start": 0, "end": 1}).json()
    assert words["words"][0][0] == "Arrête"
    plan = client.post(f"/api/agent/{pid}/analyze", json={}).json()
    assert plan["hook"]["sentence"] == 0 and {1, 2, 4} <= set(plan["drop_sentences"])


def test_montage_par_phrases_puis_retouches(client, pid):
    r = client.post(f"/api/agent/{pid}/edit", json={
        "segments": [{"media": "rush", "sentences": [3]}, {"media": "m1", "sentences": "0"}],
        "rhythm": "none", "captions": {"style": "hype", "word_by_word": True}}).json()
    assert r["rev"] == 1 and r["captions"] >= 8
    said = [s["text"] for s in r["timeline_text"]]
    assert said[0].startswith("Voici") and said[1].startswith("Arrête")
    st = client.get(f"/api/agent/{pid}").json()
    main = next(t for t in st["tracks"] if t["main"])
    assert main["clips"][0]["in"] < main["clips"][-1]["in"] + 10
    caps = next(t for t in st["tracks"] if t["name"] == "Sous-titres")
    assert caps["captions"]["word_by_word"] is True

    t = client.post(f"/api/agent/{pid}/text", json={"text": "3 astuces", "start": 0, "duration": 2,
                                                     "style": "Néon rose", "anim_in": "pop"}).json()
    assert t["rev"] == 2 and not t["warnings"]
    bad = client.post(f"/api/agent/{pid}/text", json={"text": "x", "start": 0, "anim_in": "envol"})
    assert bad.status_code == 400 and "pop" in bad.json()["detail"]
    assert client.post(f"/api/agent/{pid}/text", json={"text": "x", "style": "inconnu"}).status_code == 400

    up = client.post(f"/api/agent/{pid}/update", json={"clips": [{"id": t["clip"], "text": "Trois astuces",
                                                                  "y": 0.25, "anim_loop": "pulse"}]}).json()
    assert up["updated"] == 1
    clip = next(c for c in timeline_api.get(pid).state["clips"] if c["id"] == t["clip"])
    assert clip["words"][0]["text"] == "Trois astuces" and clip["y"] == 0.25 and clip["anim_loop"]["type"] == "pulse"

    total = timeline_api.get(pid).state["clips"]
    cut = client.post(f"/api/agent/{pid}/cut", json={"start": 0.5, "end": 1.0}).json()
    assert cut["removed"] == 0.5 and total
    assert client.post(f"/api/agent/{pid}/undo").json()["undone"] == "cut_range"
    assert client.post(f"/api/agent/{pid}/delete", json={"ids": [t["clip"]]}).json()["deleted"] == 1
    assert client.post(f"/api/agent/{pid}/delete", json={"ids": ["nope"]}).status_code == 404


def test_images_du_projet_designees_par_leur_id(client, pid):
    proj = timeline_api.get(pid)
    html = ('<img src="media:m1"><img src=\'media:m1\'>'
            '<style>@media (min-width:1px){b{background:url(media:m1)}}</style>')
    out = service.media_refs(proj, html)
    assert "media:m1" not in out and out.count("file:///") == 3 and "rush.mp4" in out and "@media (" in out
    with pytest.raises(service.AgentError, match="Unknown media mdeadbeef"):
        service.media_refs(proj, '<img src="media:mdeadbeef">')


def test_erreurs_qui_disent_quoi_faire(client, pid):
    proj = timeline_api.get(pid)
    proj.media("m1")["transcript"] = {"status": "running"}
    r = client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "m1", "sentences": [0]}]})
    assert r.status_code == 409 and "wait" in r.json()["detail"]
    assert client.post(f"/api/agent/{pid}/edit", json={"segments": []}).status_code == 400
    assert client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "zzz"}]}).status_code == 404
    proj.media("m1")["transcript"] = {"status": "done"}
    r = client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "m1", "sentences": [42]}]})
    assert r.status_code == 400 and "42" in r.json()["detail"]
    assert client.post(f"/api/agent/{pid}/undo").status_code == 409


def test_format_et_recadrage(client, pid):
    client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "m1", "start": 0, "end": 5}],
                                                "remove_silences": False, "remove_fillers": False,
                                                "rhythm": "none"})
    r = client.post(f"/api/agent/{pid}/format", json={"format": "16:9", "background": "#112233"}).json()
    assert r["canvas"]["w"] == 1920 and r["canvas"]["bg"] == "#112233" and r["reframed"] == 1
    assert client.post(f"/api/agent/{pid}/format", json={"format": "7:3"}).status_code == 400


def test_le_studio_ne_peut_plus_ecraser_le_travail_de_l_agent(client, pid):
    state = client.get(f"/api/timeline/{pid}").json()
    rev0 = state["rev"]
    client.post(f"/api/agent/{pid}/text", json={"text": "Agent", "start": 0})
    assert client.get(f"/api/timeline/{pid}/rev").json()["rev"] == rev0 + 1
    # le studio envoie sa version, faite sur la révision d'avant : refusée
    stale = {k: state[k] for k in ("tracks", "clips", "canvas", "settings", "markers", "name")}
    r = client.post(f"/api/timeline/{pid}/save", json={**stale, "base_rev": rev0})
    assert r.status_code == 409 and r.json()["detail"]["rev"] == rev0 + 1
    # à jour : acceptée, et la révision avance
    fresh = client.get(f"/api/timeline/{pid}").json()
    ok = client.post(f"/api/timeline/{pid}/save", json={**{k: fresh[k] for k in stale}, "base_rev": rev0 + 1})
    assert ok.json()["rev"] == rev0 + 2
    # sans base (ancien studio, fermeture d'onglet) : acceptée comme avant
    assert client.post(f"/api/timeline/{pid}/save", json={"name": "X"}).json()["ok"]


def test_connexion_des_agents(client, monkeypatch):
    from engine.agent import api as agent_api
    monkeypatch.setattr(agent_api, "_cli", lambda name: None)
    info = client.get("/api/agent/connect").json()
    assert info["command"][-1] == "--mcp" and not info["clients"]["claude"]["found"]
    assert "claude mcp add --scope user montage-ia" in info["snippets"]["claude"]
    assert json.loads(info["snippets"]["json"])["mcpServers"]["montage-ia"]["args"][-1] == "--mcp"
    assert client.post("/api/agent/connect", json={"client": "claude"}).status_code == 404


def test_cle_pexels_gardee_par_l_application(client, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert client.get("/api/agent/settings").json()["pexels"] is False
    st = client.post("/api/agent/settings", json={"pexels_key": "abcdef123456789"}).json()
    assert st["pexels"] and st["pexels_hint"] == "abcd…789"
    assert client.get("/api/agent/info").json()["pexels"] is True
    assert client.post("/api/agent/settings", json={"pexels_key": ""}).json()["pexels"] is False


def _levels_of(proj, mid):
    """Niveaux synthétiques du rush de test : la voix sur les mots, le fond ailleurs."""
    import numpy as np
    words = json.load(open(os.path.join(proj.media_folder(mid), "words.json"), encoding="utf-8"))["words"]
    n = int((words[-1]["end"] + 1.5) * 100)
    rms = np.full(n, -55.0, np.float32)
    for w in words:
        rms[int(w["start"] * 100):int(w["end"] * 100) + 5] = -12.0
    return {"rms": rms, "peak": rms + 6}


def test_derush_puis_montage_par_prises(client, pid, monkeypatch):
    from engine.timeline import takes as T
    monkeypatch.setattr(T, "media_levels", lambda proj, m: _levels_of(proj, m["id"]))
    r = client.post(f"/api/agent/{pid}/derush", json={"media": "m1"}).json()
    assert [t["n"] for t in r["takes"]] == [1, 2, 3, 4, 5]
    assert r["takes"][2]["keep"] is False and "contained" in r["takes"][2]["why"]   # « Voici… » redit juste après
    assert r["takes"][0]["text"].startswith("Arrête")
    e = client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "m1", "takes": "4,1"}],
                                                     "rhythm": "none"}).json()
    said = [s["text"] for s in e["timeline_text"]]
    assert said[0].startswith("Voici") and said[1].startswith("Arrête")
    bad = client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "m1", "takes": "9"}]})
    assert bad.status_code == 400 and "do not exist" in bad.json()["detail"]


def test_scenes_controlees_avant_tout_rendu(client, pid):
    client.post(f"/api/agent/{pid}/edit", json={"segments": [{"media": "m1", "sentences": "0,3"}],
                                                "rhythm": "none"})
    ok = client.post(f"/api/agent/{pid}/scenes", json={"check_only": True, "captions": {"style": "net"}, "scenes": [
        {"layout": "split", "start": 0, "items": [{"type": "big", "value": 3, "at": "w:perdre"},
                                                  {"type": "stamp", "text": "STOP", "at": "w:temps."}]},
        {"layout": "face", "start": "w:Voici", "hold": "the tip, said to camera"}]})
    assert ok.status_code == 200, ok.text
    r = ok.json()
    assert r["ok"] and r["rendered"] is False and [s["layout"] for s in r["scenes"]] == ["split", "face"]
    bad = client.post(f"/api/agent/{pid}/scenes", json={"check_only": True, "scenes": [
        {"layout": "split", "start": 0}, {"layout": "face", "start": "w:banane"}]})
    assert bad.status_code == 400 and "banane" in bad.json()["detail"]
    held = client.post(f"/api/agent/{pid}/scenes", json={"check_only": True, "scenes": [{"layout": "split", "start": 0}]})
    assert held.status_code == 400 and "Nothing happens on screen" in held.json()["detail"]
    assert client.get("/api/agent/catalog/scenes").json()["layouts"]["split"]
