"""Export d'un montage timeline (filtergraph et vrai rendu ffmpeg)."""
from __future__ import annotations

import json
import subprocess

import pytest

from engine.timeline import render

RED = {"id": "mr", "kind": "video", "path": "rouge.mp4", "w": 640, "h": 360, "duration": 4.0,
       "has_audio": True, "status": "ready"}


def state(clips, tracks=None, canvas=None):
    return {
        "canvas": canvas or {"w": 360, "h": 640, "fps": 25, "bg": "#000000"},
        "tracks": tracks or [
            {"id": "tt", "kind": "text"}, {"id": "tv2", "kind": "video"},
            {"id": "tv1", "kind": "video", "main": True}, {"id": "ta1", "kind": "audio"}],
        "clips": clips,
    }


def vclip(**kw):
    c = {"id": "c1", "track": "tv1", "kind": "video", "media": "mr", "start": 0.0, "dur": 2.0, "in": 0.0,
         "speed": 1.0, "x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0, "opacity": 1, "fit": "cover"}
    c.update(kw)
    return c


# ------------------------------------------------------------------ unitaires

def test_export_size():
    c = {"w": 1080, "h": 1920}
    assert render.export_size(c, None) == (1080, 1920)
    assert render.export_size(c, "720p") == (720, 1280)
    assert render.export_size(c, "2160p") == (2160, 3840)
    assert render.export_size({"w": 1920, "h": 1080}, "720p") == (1280, 720)


def test_atempo_enchaine_sous_un_demi():
    assert render._atempo(1.0) == []
    assert render._atempo(2.0) == ["atempo=2"]
    assert render._atempo(0.25) == ["atempo=0.5", "atempo=0.5"]


def test_series_de_lecture():
    """Des clips qui avancent dans la source partagent une entrée ; revenir en arrière en ouvre une."""
    clips = [vclip(id="a", start=0, dur=1, **{"in": 0}), vclip(id="b", start=1, dur=1, **{"in": 2}),
             vclip(id="c", start=2, dur=1, **{"in": 0.5})]
    st = state(clips)
    inp = render.plan_runs(st, {"mr": RED})
    assert inp.count == 2
    assert [c["_input"] for c in clips] == [0, 0, 1]
    assert inp.args[:4] == ["-ss", "0", "-i", "rouge.mp4"]


def test_remplir_recadre_avant_de_mettre_a_l_echelle(tmp_path):
    g = render.build(state([vclip()]), {"mr": RED}, 360, 640, 25, str(tmp_path))
    # 640x360 en « remplir » dans du 360x640 : on garde une bande de 202x360 de la source
    assert "crop=202:360:219:0" in g["graph"] and "scale=360:640" in g["graph"]
    assert g["vout"] == "vout" and g["aout"] == "amix" and g["duration"] == 2.0


def test_clip_hors_cadre_et_pistes_masquees(tmp_path):
    out = vclip(id="o", track="tv2", x=3.0)
    st = state([vclip(), out])
    g = render.build(st, {"mr": RED}, 360, 640, 25, str(tmp_path))
    assert g["graph"].count("crop=") == 1                # le clip hors cadre ne lit rien
    st["tracks"][1]["hidden"] = True
    g2 = render.build(st, {"mr": RED}, 360, 640, 25, str(tmp_path))
    assert "[o0][v1]" not in g2["graph"]


def test_son_muet_separe_et_piste_coupee(tmp_path):
    st = state([vclip(detached=True), {"id": "a", "track": "ta1", "kind": "audio", "media": "mr",
                                        "start": 0, "dur": 2, "in": 0, "speed": 1, "volume": 1.5,
                                        "fade_in": 0.5}])
    g = render.build(st, {"mr": RED}, 360, 640, 25, str(tmp_path))
    assert g["graph"].count("atrim=start=") == 1          # la vidéo séparée se tait
    assert "volume=1.5" in g["graph"] and "afade=t=in:st=0:d=0.5" in g["graph"]
    st["tracks"][3]["muted"] = True
    g = render.build(st, {"mr": RED}, 360, 640, 25, str(tmp_path))
    assert "atrim=start=" not in g["graph"] and "anullsrc" in g["graph"]


def test_textes_et_mots_coupes():
    st = state([{"id": "t", "track": "tt", "kind": "text", "start": 0, "dur": 1,
                 "words": [{"text": "a", "start": 0, "end": 0.5, "cut": True},
                           {"text": "b", "start": 0.5, "end": 1}]},
                {"id": "g", "track": "tt", "kind": "text", "start": 1, "dur": 1, "gone": True,
                 "words": [{"text": "x", "start": 1, "end": 2}]}])
    caps = render.captions_of(st, 5.0)
    assert [[w["text"] for w in c["words"]] for c in caps] == [["b"]]


def test_montage_vide_refuse(tmp_path):
    with pytest.raises(ValueError):
        render.build(state([]), {}, 360, 640, 25, str(tmp_path))


def test_media_manquant_refuse(tmp_path):
    with pytest.raises(ValueError, match="introuvables"):
        render.export(state([vclip()]), [], str(tmp_path / "x.mp4"))


# -------------------------------------------------------------------- rendu

def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                   check=True, capture_output=True, timeout=120)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def pixel(path, t, x, y):
    """Couleur (r, g, b) d'un pixel de la vidéo à l'instant t."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    info = probe(path)
    w = next(s for s in info["streams"] if s["codec_type"] == "video")["width"]
    i = (y * w + x) * 3
    return tuple(raw[i:i + 3])


def close(rgb, ref, tol=60):
    return all(abs(a - b) <= tol for a, b in zip(rgb, ref))


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("render")
    red, green, blue = d / "rouge.mp4", d / "vert.mp4", d / "bleu.png"
    ff("-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=4", "-f", "lavfi",
       "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
       "-shortest", red)
    ff("-f", "lavfi", "-i", "color=c=0x00FF00:s=320x320:r=25:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", green)
    ff("-f", "lavfi", "-i", "color=c=blue:s=400x300", "-frames:v", "1", blue)
    return {
        "mr": dict(RED, path=str(red)),
        "mg": {"id": "mg", "kind": "video", "path": str(green), "w": 320, "h": 320, "duration": 3.0,
               "has_audio": False, "status": "ready"},
        "mb": {"id": "mb", "kind": "image", "path": str(blue), "w": 400, "h": 300, "duration": 0,
               "has_audio": False, "status": "ready"},
    }



@pytest.mark.ffmpeg
def test_rendu_complet(media, tmp_path):
    if not render._encoder_works("libx264"):
        pytest.skip("ffmpeg sans libx264")
    clips = [
        vclip(id="r", start=0, dur=2),                                        # rouge, plein cadre
        {"id": "b", "track": "tv1", "kind": "image", "media": "mb", "start": 2, "dur": 1,
         "x": 0.5, "y": 0.5, "scale": 1, "fit": "contain", "rotation": 0, "opacity": 1},
        {"id": "g", "track": "tv2", "kind": "video", "media": "mg", "start": 0.5, "dur": 1, "in": 0,
         "speed": 1, "x": 0.25, "y": 0.25, "scale": 0.25, "fit": "contain", "rotation": 0, "opacity": 1},
        {"id": "a", "track": "ta1", "kind": "audio", "media": "mr", "start": 1, "dur": 1.5, "in": 1,
         "speed": 2, "volume": 0.5},
        {"id": "t", "track": "tt", "kind": "text", "start": 0.2, "dur": 1, "font": "Arial", "size": 60,
         "color": "#FFFFFF", "hl": "#FFFF00", "outline_col": "#000000", "outline": 3, "x": 0.5, "y": 0.8,
         "mode": "word", "words": [{"text": "SALUT", "start": 0.2, "end": 1.2}]},
    ]
    st = state(clips, canvas={"w": 360, "h": 640, "fps": 25, "bg": "#FFFFFF"})
    progress = []
    out = tmp_path / "export.mp4"
    res = render.export(st, list(media.values()), str(out), quality="low", encoder="cpu",
                        on_progress=progress.append)
    info = probe(out)
    kinds = sorted(s["codec_type"] for s in info["streams"])
    assert kinds == ["audio", "video"]
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (360, 640) and v["r_frame_rate"] == "25/1"
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.1)
    assert res["duration"] == 3.0 and progress and max(progress) > 0.5

    assert close(pixel(out, 0.1, 300, 300), (255, 0, 0))      # rouge plein cadre
    assert close(pixel(out, 1.0, 90, 160), (0, 255, 0))       # vert en surimpression, en haut à gauche
    assert close(pixel(out, 1.0, 300, 600), (255, 0, 0))
    assert close(pixel(out, 2.5, 180, 320), (0, 0, 255))      # image « adaptée »…
    assert close(pixel(out, 2.5, 180, 30), (255, 255, 255))   # …avec le fond du projet autour


@pytest.mark.ffmpeg
def test_rendu_rotation_miroir_opacite_et_720p(media, tmp_path):
    if not render._encoder_works("libx264"):
        pytest.skip("ffmpeg sans libx264")
    clips = [{"id": "g", "track": "tv1", "kind": "video", "media": "mg", "start": 0, "dur": 1, "in": 0.5,
              "speed": 0.5, "x": 0.5, "y": 0.5, "scale": 0.5, "fit": "contain", "rotation": 45,
              "opacity": 0.5, "flip_h": True, "filters": {"brightness": 0.2}}]
    st = state(clips, canvas={"w": 1080, "h": 1920, "fps": 30, "bg": "#000000"})
    out = tmp_path / "rot.mp4"
    render.export(st, list(media.values()), str(out), resolution="720p", encoder="cpu")
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (720, 1280)
    mid = pixel(out, 0.5, 360, 640)
    assert 60 < mid[1] < 220 and mid[0] < 60                  # vert à demi transparent sur noir
    assert close(pixel(out, 0.5, 20, 20), (0, 0, 0), 30)      # coin : le fond


@pytest.mark.ffmpeg
def test_son_seul(media, tmp_path):
    out = tmp_path / "son.mp3"
    res = render.export(state([vclip()]), list(media.values()), str(out), audio_only=True,
                        audio_args=["-c:a", "libmp3lame", "-b:a", "128k"])
    info = probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["audio"]
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.1)
    assert res["size"] > 0


@pytest.mark.ffmpeg
def test_annulation(media, tmp_path):
    import threading
    stop = threading.Event()
    stop.set()
    with pytest.raises(render.Cancelled):
        render.export(state([vclip()]), list(media.values()), str(tmp_path / "x.mp4"), encoder="cpu",
                      cancel=stop)
    assert not (tmp_path / "x.mp4").exists()


# ----------------------------------------------------------------------- API

@pytest.mark.ffmpeg
def test_api_export(server_module, media, tmp_path, monkeypatch, exports_dir):
    import time
    from fastapi.testclient import TestClient
    if not render._encoder_works("libx264"):
        pytest.skip("ffmpeg sans libx264")
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    server_module.timeline_api.TIMELINES.clear()
    c = TestClient(server_module.app)
    pid = c.post("/api/timeline", json={"name": "Mon: film"}).json()["id"]
    proj = server_module.timeline_api.get(pid)
    proj.state["media"] += [dict(m) for m in media.values()]
    assert c.post(f"/api/timeline/{pid}/export", json={}).status_code == 400      # vide
    c.post(f"/api/timeline/{pid}/save", json={"clips": [vclip(dur=1)]})
    assert c.post(f"/api/timeline/{pid}/export", json={"resolution": "8K"}).status_code == 400
    for expected in ("Mon_ film.mp4", "Mon_ film (2).mp4"):
        assert c.post(f"/api/timeline/{pid}/export", json={"encoder": "cpu", "signature": "abc"}).json()["ok"]
        for _ in range(200):
            st = c.get(f"/api/timeline/{pid}/export").json()
            if st["task"]["status"] != "running":
                break
            time.sleep(0.05)
        assert st["task"]["status"] == "done", st["task"]["message"]
        assert st["export"]["output"].endswith(expected) and st["export"]["signature"] == "abc"
    assert sorted(p.name for p in exports_dir.iterdir()) == ["Mon_ film (2).mp4", "Mon_ film.mp4"]
    r = c.get(f"/api/timeline/{pid}/export/file")
    assert r.status_code == 200 and len(r.content) == st["export"]["size"]
    assert not c.post(f"/api/timeline/{pid}/export/cancel").json()["ok"]         # rien à annuler
    # son seul, au format de la boîte à outils
    c.post(f"/api/timeline/{pid}/export", json={"audio_only": True, "audio_format": "wav"})
    for _ in range(200):
        st = c.get(f"/api/timeline/{pid}/export").json()
        if st["task"]["status"] != "running":
            break
        time.sleep(0.05)
    assert st["export"]["output"].endswith(".wav") and st["export"]["audio_only"]
