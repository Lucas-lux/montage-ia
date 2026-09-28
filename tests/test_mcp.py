"""Serveur MCP des agents IA (engine/agent/mcp.py) : protocole et mise en forme.

Le moteur est remplacé par un faux : ici on vérifie la conversation JSON-RPC
(poignée de main, outils, invites, erreurs, progression), pas le montage."""
from __future__ import annotations

import io
import json
import threading

from engine.agent import mcp


class FakeEngine:
    url = "http://127.0.0.1:0"
    launched = False

    def __init__(self):
        self.calls = []

    def get(self, path, **q):
        self.calls.append(("GET", path, q))
        if path == "/api/agent/catalog/animations":
            return {"items": [{"type": "pop", "kind": "in", "for": ["text", "media"]},
                              {"type": "typewriter", "kind": "in", "for": ["text"]},
                              {"type": "pulse", "kind": "loop", "for": ["text", "media"]}]}
        if path.startswith("/api/agent/jobs/"):
            return {"id": "j1", "status": "running", "pct": 40, "message": "Analyse…"}
        raise mcp.EngineError(404, "Project not found.")

    def post(self, path, body=None, timeout=600):
        self.calls.append(("POST", path, body))
        if path.endswith("/auto"):
            return {"job_id": "j1"}
        raise mcp.EngineError(409, "Media still being prepared: call wait(what='media') first.")

    def request(self, method, path, body=None, timeout=600, raw=False):
        return self.post(path, body)

    def ensure(self):
        return self.url


def converse(messages, engine=None):
    """Envoie des messages au serveur, rend ses réponses (dans l'ordre)."""
    rfile = io.BytesIO(b"".join((json.dumps(m) + "\n").encode() for m in messages))
    out = io.BytesIO()
    srv = mcp.Server(engine or FakeEngine(), rfile, out)
    srv.run()
    return [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines() if line.strip()]


def by_id(replies):
    return {r["id"]: r for r in replies if "id" in r}


def test_poignee_de_main_outils_et_invites():
    r = by_id(converse([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
         "params": {"name": "edit_video", "arguments": {"source": "C:/rush.mp4", "length": "45 s"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 6, "method": "nope/nope"},
        {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}},
    ]))
    init = r[1]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "montage-ia"
    assert "build_edit" in init["instructions"] and init["capabilities"]["tools"]
    tools = {t["name"]: t for t in r[2]["result"]["tools"]}
    assert {"create_project", "get_transcript", "build_edit", "add_captions", "search_images", "add_visual",
            "preview", "export", "wait", "undo", "open_in_app"} <= set(tools)
    for t in tools.values():
        assert t["inputSchema"]["type"] == "object" and t["description"] and "fn" not in t
    assert tools["get_transcript"]["annotations"]["readOnlyHint"] is True
    assert r[3]["result"]["prompts"][0]["name"] == "edit_video"
    text = r[4]["result"]["messages"][0]["content"]["text"]
    assert "C:/rush.mp4" in text and "45 s" in text
    assert r[5]["result"] == {} and r[6]["error"]["code"] == -32601
    assert r[7]["result"]["protocolVersion"] == mcp.PROTOCOLS[0]


def test_outil_qui_reussit_et_outil_en_erreur():
    eng = FakeEngine()
    r = by_id(converse([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "catalog",
                                                                       "arguments": {"what": "animations"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "build_edit", "arguments": {
            "project": "p1", "segments": [{"media": "m1", "sentences": [0]}]}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "inconnu", "arguments": {}}},
    ], eng))
    ok = r[1]["result"]
    assert not ok["isError"] and "in: pop, typewriter(text only)" in ok["content"][0]["text"]
    err = r[2]["result"]
    assert err["isError"] and "409" in err["content"][0]["text"] and "wait" in err["content"][0]["text"]
    assert r[3]["result"]["isError"] and "Unknown tool" in r[3]["result"]["content"][0]["text"]
    assert ("POST", "/api/agent/p1/edit", {"segments": [{"media": "m1", "sentences": [0]}]}) in eng.calls


def test_tache_longue_rend_la_main_et_dit_d_attendre():
    r = by_id(converse([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "auto_edit", "arguments": {"project": "p1", "wait": 0},
                                    "_meta": {"progressToken": "tok"}}}]))
    text = r[1]["result"]["content"][0]["text"]
    assert "Still running" in text and "job_id='j1'" in text


def test_annulation_d_une_attente():
    """Un client qui annule (notifications/cancelled) ne reçoit plus de réponse."""
    class Slow(FakeEngine):
        def get(self, path, **q):
            return {"id": "j1", "status": "running", "pct": 1, "message": ""}
    r_pipe_r, r_pipe_w = __import__("os").pipe()
    rfile = open(r_pipe_r, "rb", buffering=0)
    out = io.BytesIO()
    srv = mcp.Server(Slow(), rfile, out)
    th = threading.Thread(target=srv.run)
    th.start()
    w = open(r_pipe_w, "wb", buffering=0)
    w.write((json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
        "name": "wait", "arguments": {"what": "job", "job_id": "j1", "wait": 30}}}) + "\n").encode())
    import time
    time.sleep(0.5)
    w.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": 9}}) + "\n").encode())
    w.close()
    th.join(10)
    assert not th.is_alive()
    assert all(json.loads(line).get("id") != 9 for line in out.getvalue().decode().splitlines() if line.strip())


def test_mise_en_forme_lisible():
    p = {"id": "p1", "name": "Essai", "rev": 3, "duration": 12.5,
         "canvas": {"w": 1080, "h": 1920, "fps": 30, "bg": "#000000", "blur": False},
         "media": [{"id": "m1", "name": "rush.mp4", "kind": "video", "duration": 20, "w": 1080, "h": 1920,
                    "status": "ready", "has_audio": True, "transcript": {"status": "done", "count": 40,
                                                                        "language": "fr"}}],
         "tracks": [{"id": "tc", "name": "Sous-titres", "kind": "text", "main": False,
                     "captions": {"lines": 12, "style": "hype", "word_by_word": True}},
                    {"id": "tv1", "name": "Vidéo", "kind": "video", "main": True, "clips": [
                        {"id": "c1", "kind": "video", "start": 0, "end": 4.2, "name": "rush.mp4", "in": 3.1,
                         "out": 7.3, "scale": 1.15, "anim_in": {"type": "zoom_in"}}]}],
         "markers": [{"t": 2.0, "label": "★ Moment fort"}], "export": None}
    s = mcp.fmt_summary(p)
    assert "1080x1920" in s and "12 caption lines, style hype, word by word" in s
    assert "c1 [0.00–4.20] video \"rush.mp4\" src 3.10–7.30 scale=1.15 anim_in=zoom_in" in s
    assert "MAIN (magnetic)" in s and "2.00s ★ Moment fort" in s
    sents = mcp.fmt_sentences([{"i": 4, "start": 1, "end": 2.5, "text": "Salut", "score": -3, "flags": ["fluff"]}])
    assert sents == "#4 [1.00–2.50] (score -3.0) {fluff} Salut"
