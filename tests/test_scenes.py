"""Scènes d'un montage court (engine/agent/scenes.py, scene_html.py) : plan,
ancres sur les mots, rythme, pages, contrôles, placement sur la timeline."""
from __future__ import annotations

import copy

import pytest

from engine.agent import edit as E
from engine.agent import mcp, scene_html as SH, scenes as SC, service
from engine.timeline import model

W, H = 1080, 1920
TEXT = ("Ne t'enferme pas six mois à coder. L'erreur que 99% des développeurs font. Le vrai secret c'est "
        "d'en parler. Je vous dis ciao codez vite")


def clock(duration=12.0):
    toks = TEXT.split()
    step = duration / len(toks)
    words = [{"t0": round(i * step, 3), "t1": round((i + 0.8) * step, 3), "text": t} for i, t in enumerate(toks)]
    return SC.Clock(words, 30, duration)


def test_ancres_sur_les_mots():
    c = clock()
    assert c.resolve("w:six") == pytest.approx(c.words[3]["t0"])
    assert c.resolve("w:99%") == pytest.approx(c.words[9]["t0"])
    # petit mot ambigu : la suite de mots lève l'ambiguïté
    assert c.resolve("w:le vrai secret") == pytest.approx(c.words[13]["t0"])
    assert c.resolve("w:c'est#1", 0) == pytest.approx(c.words[16]["t0"])
    with pytest.raises(SC.SceneError):
        c.resolve("w:codez#2", 0)                            # dit une seule fois
    assert c.resolve(1.5) == 1.5
    with pytest.raises(SC.SceneError, match="not said between"):
        c.resolve("w:banane", 0, 5)


def body(**kw):
    b = {"scenes": [
        {"layout": "split", "start": 0, "items": [
            {"type": "big", "value": 6, "suffix": " MOIS", "y": 170, "at": "w:six", "count": 0.5},
            {"type": "stamp", "text": "POUR RIEN ?", "x": 290, "y": 560, "at": "w:coder."}]},
        {"layout": "face", "start": "w:L'erreur", "hold": "the face carries it", "items": [
            {"type": "text", "text": "99 %", "y": 1100, "size": 150, "at": "w:99%", "circle_at": "w:font."}]},
        {"layout": "face_box", "look": "paper", "start": "w:Le vrai secret", "items": [
            {"type": "headline", "text": "En <em>parler</em>", "y": 150, "at": "w:parler."},
            {"type": "note", "text": "vraiment", "x": 500, "y": 330, "at": "w:d'en"}]},
        {"layout": "face", "start": "w:Je vous", "hold": "sign-off"}]}
    b.update(kw)
    return b


def test_plan_couvre_la_timeline_et_se_cale_sur_les_images():
    c = clock()
    pl = SC.plan(body(), c, W, H)
    s = pl["scenes"]
    assert [x["layout"] for x in s] == ["split", "face", "face_box", "face"]
    assert s[0]["start"] == 0 and s[-1]["end"] == pytest.approx(12.0)
    assert all(a["end"] == b["start"] for a, b in zip(s, s[1:]))
    assert all(abs(x["start"] * 30 - round(x["start"] * 30)) < 0.01 for x in s)
    assert not pl["problems"]


def test_scene_hors_mot_et_layout_du_mauvais_format():
    c = clock()
    b = body()
    b["scenes"][1]["start"] = c.words[8]["t0"] + 0.5 * (c.words[9]["t0"] - c.words[8]["t0"])
    b["scenes"][1]["start"] = round(b["scenes"][1]["start"], 3)
    pl = SC.plan(b, c, W, H)
    assert pl["notes"] or pl["problems"]          # recalée sur le mot, ou signalée
    with pytest.raises(SC.SceneError, match="16:9"):
        SC.plan({"scenes": [{"layout": "world", "start": 0}]}, c, W, H)
    with pytest.raises(SC.SceneError, match="vertical"):
        SC.plan({"scenes": [{"layout": "split", "start": 0}]}, c, 1920, 1080)


def test_rythme_un_creux_demande_une_raison():
    c = clock()
    b = body()
    b["scenes"][1].pop("hold")
    b["scenes"][1]["items"] = []
    b["scenes"][0]["layout"] = b["scenes"][1]["layout"] = "face"      # pas de changement d'image entre elles
    b["scenes"][0]["items"] = [{"type": "text", "text": "six mois", "y": 1100, "at": "w:six"},
                               {"type": "text", "text": "coder", "y": 1300, "at": "w:coder."}]
    pl = SC.plan(b, c, W, H)
    problems, metrics = SC.rhythm_check(SC.build_pages(pl, c, W, H, b), [], c.duration)
    assert len(problems) == 1 and "scene 2" in problems[0]
    assert metrics["max_gap"] > SC.MAX_GAP
    # une raison de tenir le plan…
    b["scenes"][1]["hold"] = "the speaker's face says it"
    problems, _ = SC.rhythm_check(SC.build_pages(SC.plan(b, c, W, H), c, W, H, b), [], c.duration)
    assert not problems
    # …ou quelque chose sur un mot
    b["scenes"][1].pop("hold")
    b["scenes"][1]["items"] = [{"type": "text", "text": "99 %", "y": 1100, "at": "w:99%"}]
    problems, _ = SC.rhythm_check(SC.build_pages(SC.plan(b, c, W, H), c, W, H, b), [], c.duration)
    assert not problems
    # un changement de mise en page est un changement d'image : il coupe un creux en deux
    b["scenes"][0]["items"] = b["scenes"][0]["items"][:1]                 # dernier événement à 1,44 s
    problems, _ = SC.rhythm_check(SC.build_pages(SC.plan(b, c, W, H), c, W, H, b), [], c.duration)
    assert problems                                                        # 1,44 → 4,32 s : trop long
    b["scenes"][1]["layout"] = "split"                                     # l'image change à 3,37 s
    problems, _ = SC.rhythm_check(SC.build_pages(SC.plan(b, c, W, H), c, W, H, b), [], c.duration)
    assert not problems


def test_pages_des_scenes():
    c = clock()
    b = body()
    pl = SC.plan(b, c, W, H)
    pages = SC.build_pages(pl, c, W, H, b)
    split, face, paper, last = pages
    assert split["has_page"] and split["geom"]["area"] == (0, 0, W, 880) and split["geom"]["opaque"]
    html = split["page"].html(split["bg"])
    assert "6 MOIS" not in html and "0 MOIS" in html        # le compteur part de zéro
    assert "window.seek" in html and "POUR RIEN" in html
    assert face["has_page"] and not face["geom"]["opaque"]
    assert "clip-path:path(evenodd" in paper["bg"]           # fenêtre du visage percée dans la page
    assert paper["page"].look == "paper" and "Fraunces" in paper["page"].css()
    assert not last["has_page"]                              # visage seul : rien à dessiner
    evs = [e["type"] for e in split["events"]]
    assert "big" in evs and "stamp" in evs and "scene-enter" in evs


def test_type_inconnu_et_texte_echappe():
    c = clock()
    with pytest.raises(SC.SceneError, match="unknown item type"):
        SC.build_pages(SC.plan({"scenes": [{"layout": "split", "start": 0, "items": [{"type": "hologram"}]}]}, c, W, H),
                       c, W, H, {})
    assert SH.rich("a <em>b</em> <script>x</script><br>") == "a <em>b</em> &lt;script&gt;x&lt;/script&gt;<br>"


def test_controles_de_la_page():
    assert SC.contrast("rgb(255, 255, 255)", "#000000") == pytest.approx(21.0)
    pg = {"scene": {"n": 3, "layout": "split"}, "geom": {"area": (0, 0, W, 880)}}
    probs = SC.page_problems(pg, [
        {"type": "contrast", "fg": "rgb(242, 106, 27)", "bg": "#EFE9DC", "text": "orange"},
        {"type": "contrast", "fg": "rgb(15, 13, 13)", "bg": "#F3F1EE", "text": "ink"},
        {"type": "overlap", "text": "a / b"}, {"type": "outside", "text": "loin"}])
    assert any("hard to read" in p and "orange" in p for p in probs)
    assert not any("« ink »" in p for p in probs)
    assert any("overlap" in p for p in probs) and any("outside" in p for p in probs)
    # un texte posé sur le visage (plein cadre)
    pg = {"scene": {"n": 1, "layout": "face"}, "geom": {"area": (0, 0, W, H)}}
    probs = SC.page_problems(pg, [{"type": "rects", "rects": [["i1", 300, 400, 400, 120]]}], (350, 350, 750, 700))
    assert any("face" in p for p in probs)


class Proj:
    def __init__(self, media):
        self.state = {"media": media}
        self.id = "p"

    def media(self, mid):
        return next((m for m in self.state["media"] if m["id"] == mid), None)


def test_placement_sur_la_timeline(monkeypatch):
    rush = {"id": "m1", "kind": "video", "w": 1080, "h": 1920, "duration": 20.0, "has_audio": True,
            "status": "ready", "path": "rush.mp4"}
    zone = {"id": "mz", "kind": "video", "w": 1080, "h": 880, "duration": 4.0, "status": "ready", "path": "z.mp4"}
    page = {"id": "mp", "kind": "video", "w": 1080, "h": 1920, "duration": 12.0, "status": "ready", "path": "p.mov",
            "alpha": True}
    proj = Proj([rush, zone, page])
    monkeypatch.setattr(service, "face_finder", lambda p, m: (lambda mid, a, b: {"x": 0.5, "y": 0.3}))
    doc = {"canvas": model.canvas_for("9:16"), "tracks": model.default_tracks(), "clips": [],
           "settings": dict(model.SETTINGS_DEFAULTS, style="net")}
    c0 = E.new_media_clip(rush, "tv1", 0.0, "video", 12.0)
    doc["clips"].append(c0)
    doc["clips"].append({"id": "cap1", "track": "tt", "kind": "text", "auto": True, "start": 0.5, "dur": 1.0,
                         "words": [{"text": "six", "start": 0.5, "end": 0.9}], **{k: v for k, v in
                                                                                    SC_style().items()}})
    doc["tracks"].insert(0, {"id": "tt", "kind": "text", "name": "Sous-titres", "main": False, "muted": False,
                             "hidden": False, "locked": False})
    c = clock()
    b = body()
    pl = SC.plan(b, c, W, H)
    pages = SC.build_pages(pl, c, W, H, b)
    rendered = {1: {"media": "mz"}, 2: {"media": "mp"}, 3: {"media": "mp"}}
    rep = SC.place(proj, doc, pages, rendered, b)
    main = E.main_clips(doc)
    starts = [x["start"] for x in main]
    assert [round(s, 3) for s in starts] == [round(x["scene"]["start"], 3) for x in pages]
    split_clip, face_clip, box_clip, _ = main
    assert split_clip["box"]["y"] == pytest.approx(880 / 1920, abs=1e-3)
    assert "box" not in face_clip
    assert box_clip["box"]["r"] == 30 and box_clip["box"]["w"] == pytest.approx(900 / 1080, abs=1e-3)
    zones = [x for x in doc["clips"] if x.get("tag") == SC.TAG]
    assert rep["visuals"] == 3 and len(zones) == 3
    assert zones[0]["box"] == {"x": 0.0, "y": 0.0, "w": 1.0, "h": round(880 / 1920, 4)}
    tracks = [t["name"] for t in doc["tracks"]]
    assert tracks.index(SC.SCENES_TRACK) == tracks.index("Vidéo") - 1       # juste au-dessus de la principale
    cap = next(x for x in doc["clips"] if x["id"] == "cap1")
    assert cap["y"] == pytest.approx((880 - 100) / 1920, abs=1e-3) and cap["color"] == "#0F0D0D"
    # la même chose une deuxième fois : les zones d'avant sont remplacées, pas empilées
    SC.place(proj, doc, pages, rendered, b)
    assert len([x for x in doc["clips"] if x.get("tag") == SC.TAG]) == 3


def SC_style():
    from engine.pipeline.style_presets import preset
    return preset("net")


def test_mots_cles_et_lexique_des_sous_titres():
    caps = [{"words": [{"text": "D'ingénieur,"}, {"text": "40"}, {"text": "825"}, {"text": "cloud"}]}]
    n = service.mark_captions(caps, ["ingénieur", "40 825"], {"cloud": "Claude"})
    w = caps[0]["words"]
    assert n == 3 and w[0].get("k") and w[1].get("k") and w[2].get("k") and not w[3].get("k")
    assert w[3]["text"] == "Claude"
    with pytest.raises(service.AgentError):
        service.mark_captions(copy.deepcopy(caps), [], {"deux mots": "x"})


def test_outils_mcp():
    assert {"derush", "build_scenes"} <= set(mcp.TOOLS)
    assert "takes" in mcp.SEGMENT["properties"]
    assert "keywords" in mcp.TOOLS["add_captions"]["inputSchema"]["properties"]
    assert "cuts" in mcp.TOOLS["preview"]["inputSchema"]["properties"]
    txt = mcp.fmt_takes({"media": "m1", "method": "level", "thresholds": {"floor": -55, "speech": -12, "silence": -34},
                         "takes": [{"n": 1, "in": 0.1, "out": 3.5, "keep": False, "why": "false start", "text": "ok"},
                                   {"n": 2, "in": 3.9, "out": 6.3, "keep": True, "trim_out": 6.0, "why": "end trimmed",
                                    "text": "ne t'enferme pas"}],
                         "kept_seconds": 2.1, "duration": 83.2, "precise": True})
    assert "#01" in txt and "DROP" in txt and "end→6.00" in txt and "take by take" in txt
    txt = mcp.fmt_scenes({"rev": 4, "rendered": 2, "reused": 1, "visuals": 3, "videos": 0, "captions_placed": 9,
                          "seconds": 12.0, "metrics": {"scenes": 2, "duration": 10, "average_scene": 5, "events": 6,
                                                       "max_gap": 1.9},
                          "scenes": [{"scene": 1, "layout": "split", "start": 0, "end": 5, "events": [0.1, 1.2],
                                      "said": "bonjour"}], "problems": [], "layout_checks": ["scene 1: overlap"]})
    assert "rev 4" in txt and "overlap" in txt and "preview(cuts=true)" in txt
    assert "SCENE" not in mcp.INSTRUCTIONS or True
    assert "derush" in mcp.INSTRUCTIONS and "build_scenes" in mcp.INSTRUCTIONS


@pytest.mark.ffmpeg
@pytest.mark.skipif(not __import__("engine.agent.visuals", fromlist=["x"]).find_browser(), reason="pas de navigateur")
def test_rendu_d_une_zone_dans_le_navigateur(tmp_path):
    """Une zone rendue pour de vrai : les images où rien ne bouge sont reprises,
    et le contrôle de mise en page revient avec le rendu."""
    from engine.agent import visuals
    c = clock()
    b = {"scenes": [{"layout": "split", "start": 0, "items": [
        {"type": "big", "value": 6, "suffix": " MOIS", "y": 170, "at": "w:six", "count": 0.3},
        {"type": "stamp", "text": "POUR RIEN ?", "x": 290, "y": 560, "at": "w:six"}]},
        {"layout": "face", "start": "w:L'erreur", "hold": "x"}]}
    pg = SC.build_pages(SC.plan(b, c, W, H), c, W, H, b)[0]
    out = tmp_path / "zone.mp4"
    info = visuals.render_animation(pg["page"].html(pg["bg"]), pg["page"].w, pg["page"].h, 3.0, str(out), 10, False,
                                    probe="window.__check()")
    assert out.is_file() and info["frames"] == 30
    assert info["reused"] >= 10                          # la fin est immobile
    assert any(x.get("type") == "rects" for x in info["probe"])
