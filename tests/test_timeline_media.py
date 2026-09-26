"""Médias d'un montage : sonde, proxy, vignettes, forme d'onde, import."""
from __future__ import annotations

import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from engine.timeline import jobs
from engine.timeline import media as mt
from engine.timeline.project import TimelineProject


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                   check=True, capture_output=True, timeout=120)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    """Un jeu de médias synthétiques, fabriqué une fois pour le module."""
    if not (os.environ.get("PATH") and __import__("shutil").which("ffmpeg")):
        pytest.skip("ffmpeg absent")
    d = tmp_path_factory.mktemp("media")
    out = {
        "video": d / "clip.mp4", "mute": d / "muet.mp4", "song": d / "musique.mp3",
        "photo": d / "photo.png", "tall": d / "portrait.mp4", "gap": d / "pause.wav",
    }
    ff("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
       "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out["video"])
    ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", out["mute"])
    ff("-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-c:a", "libmp3lame", out["song"])
    ff("-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1", out["photo"])
    ff("-f", "lavfi", "-i", "testsrc=size=360x640:rate=30:duration=1", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", out["tall"])
    # 1 s de son, 1,5 s de silence, 1 s de son
    ff("-f", "lavfi", "-i", "sine=frequency=500:duration=1", "-f", "lavfi",
       "-i", "anullsrc=r=44100:cl=mono:d=1.5", "-f", "lavfi", "-i", "sine=frequency=500:duration=1",
       "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", out["gap"])
    return out


pytestmark = pytest.mark.ffmpeg


# ------------------------------------------------------------------- sonde

def test_sonde_video(files):
    info = mt.probe_media(str(files["video"]))
    assert info["kind"] == "video" and info["has_audio"]
    assert (info["w"], info["h"]) == (640, 360)
    assert info["duration"] == pytest.approx(3.0, abs=0.1)
    assert info["fps"] == pytest.approx(25)


def test_sonde_video_muette_audio_image(files):
    assert not mt.probe_media(str(files["mute"]))["has_audio"]
    song = mt.probe_media(str(files["song"]))
    assert song["kind"] == "audio" and song["duration"] == pytest.approx(4.0, abs=0.15)
    photo = mt.probe_media(str(files["photo"]))
    assert photo["kind"] == "image" and (photo["w"], photo["h"]) == (800, 600)
    assert not photo["has_audio"]


def test_sonde_refuse_un_faux_media(tmp_path):
    bad = tmp_path / "faux.mp4"
    bad.write_bytes(b"pas une video")
    with pytest.raises(ValueError):
        mt.probe_media(str(bad))


def test_sonde_video_pivotee(files, tmp_path):
    rotated = tmp_path / "rot.mp4"
    try:
        ff("-display_rotation", "90", "-i", files["video"], "-c", "copy", rotated)
    except subprocess.CalledProcessError:
        pytest.skip("ffmpeg sans -display_rotation")
    info = mt.probe_media(str(rotated))
    assert (info["w"], info["h"]) == (360, 640)


# ------------------------------------------------------------ géométries

def test_proxy_size():
    assert mt.proxy_size(1920, 1080) == (960, 540)
    assert mt.proxy_size(1080, 1920) == (540, 960)
    assert mt.proxy_size(640, 360) == (640, 360)          # jamais agrandi
    assert mt.proxy_size(3840, 2160, 1920) == (1920, 1080)


def test_filtre_du_proxy_sur_la_carte():
    assert mt.gpu_proxy_filter(960, 540, 30) == \
        "fps=30,scale_cuda=960:540:format=nv12,hwdownload,format=nv12,format=yuv420p"
    # vidéo de téléphone en portrait : réduite couchée, redressée ensuite
    assert mt.gpu_proxy_filter(540, 960, 30, 90) == \
        "fps=30,scale_cuda=960:540:format=nv12,hwdownload,format=nv12,transpose=clock,format=yuv420p"
    assert ",transpose=cclock," in mt.gpu_proxy_filter(540, 960, 30, 270)
    assert ",hflip,vflip," in mt.gpu_proxy_filter(960, 540, 30, 180)


def test_rotation_a_redresser(files, tmp_path):
    assert mt.display_turn(str(files["video"])) == 0
    try:
        for rot in ("90", "-90", "180"):
            ff("-display_rotation", rot, "-i", files["video"], "-c", "copy", tmp_path / f"r{rot}.mp4")
        ff("-display_rotation", "90", "-display_hflip", "-i", files["video"], "-c", "copy", tmp_path / "miroir.mp4")
    except subprocess.CalledProcessError:
        pytest.skip("ffmpeg sans -display_rotation")
    # même sens que l'autorotation de ffmpeg (transpose=clock pour -90)
    assert [mt.display_turn(str(tmp_path / f"r{r}.mp4")) for r in ("90", "-90", "180")] == [270, 90, 180]
    assert mt.display_turn(str(tmp_path / "miroir.mp4")) is None     # laissé au processeur


@pytest.mark.parametrize("error, gpu_after", [
    ("Device creation failed: -542398533.", False),       # pas de carte NVIDIA
    ("No such filter: 'scale_cuda'", False),              # ffmpeg sans CUDA
    ("Impossible to convert between the formats", True),  # ce fichier-là seulement
])
def test_proxy_repli_sur_le_processeur(monkeypatch, tmp_path, error, gpu_after):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "-hwaccel" in args:
            raise RuntimeError("ffmpeg a échoué :\n" + error)

    monkeypatch.setitem(mt._GPU, "ok", True)
    monkeypatch.setattr(mt, "display_turn", lambda path: 0)
    monkeypatch.setattr(mt, "_run_ffmpeg", fake_run)
    info = {"kind": "video", "w": 3840, "h": 2160, "fps": 60, "duration": 5, "has_audio": True}
    mt.make_proxy("src.mp4", str(tmp_path / "p.mp4"), info)
    assert len(calls) == 2 and "-hwaccel" not in calls[1]
    assert "scale=960:540,fps=30,format=yuv420p" in calls[1]
    assert mt._GPU["ok"] is gpu_after


def test_thumbs_layout():
    lay = mt.thumbs_layout(10.0, "video")
    assert lay["interval"] == 0.5 and lay["count"] == 20 and lay["cols"] == 20
    long = mt.thumbs_layout(3600, "video")
    assert long["count"] <= mt.THUMB_MAX and long["rows"] * long["cols"] >= long["count"]
    assert mt.thumbs_layout(0, "image")["count"] == 1


def test_scan_folder(tmp_path):
    for name in ("clip10.mp4", "clip2.mp4", "notes.txt", "a.MP3"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sous").mkdir()
    (tmp_path / "sous" / "b.png").write_bytes(b"x")
    names = [os.path.basename(p) for p in mt.scan_folder(str(tmp_path))]
    assert names == ["a.MP3", "clip2.mp4", "clip10.mp4"]
    assert len(mt.scan_folder(str(tmp_path), recursive=True)) == 4


def test_silences_au_volume(files):
    sil = mt.detect_silences(str(files["gap"]), -40, 0.5, duration=3.5)
    assert len(sil) == 1
    assert sil[0][0] == pytest.approx(1.0, abs=0.1) and sil[0][1] == pytest.approx(2.5, abs=0.1)


# ------------------------------------------------------- préparation complète

@pytest.fixture
def proj(tmp_path):
    return TimelineProject.create(str(tmp_path / "work"), "Test")


def ready(proj, mid):
    jobs.MEDIA.join()
    return proj.media(mid)


def test_preparation_video(proj, files):
    m = ready(proj, proj.add_media(str(files["video"]))["id"])
    assert m["status"] == "ready", m["error"]
    folder = proj.media_folder(m["id"])
    assert sorted(os.listdir(folder)) == ["poster.jpg", "proxy.mp4", "thumbs.jpg", "wave.bin"]
    proxy = mt.probe_media(os.path.join(folder, "proxy.mp4"))
    assert proxy["duration"] == pytest.approx(3.0, abs=0.1) and proxy["has_audio"]
    assert m["thumbs"]["count"] == 6 and m["thumbs"]["h"] == mt.THUMB_H
    assert m["waveform"]["count"] == pytest.approx(300, abs=5)
    assert max(open(os.path.join(folder, "wave.bin"), "rb").read()) > 100   # un vrai signal
    assert os.path.isfile(os.path.join(proj.dir, "thumb.jpg"))           # vignette du projet


def test_preparation_audio_et_image(proj, files):
    song = ready(proj, proj.add_media(str(files["song"]))["id"])
    assert song["status"] == "ready" and song["proxy_file"] == "proxy.m4a"
    assert song["thumbs"] is None and song["waveform"]["count"] > 300
    photo = ready(proj, proj.add_media(str(files["photo"]))["id"])
    assert photo["status"] == "ready" and photo["proxy_file"] == "proxy.jpg"
    assert photo["thumbs"]["count"] == 1 and photo["waveform"] is None


def test_preparation_echoue_proprement(proj, tmp_path):
    bad = tmp_path / "faux.mov"
    bad.write_bytes(b"rien")
    m = ready(proj, proj.add_media(str(bad))["id"])
    assert m["status"] == "error" and m["error"]


def test_reprise_apres_redemarrage(proj, files):
    mid = proj.add_media(str(files["mute"]))["id"]
    jobs.MEDIA.join()
    os.remove(os.path.join(proj.media_folder(mid), "proxy.mp4"))
    again = TimelineProject.load(proj.work_dir, proj.id)       # proxy disparu : refait
    jobs.MEDIA.join()
    assert again.media(mid)["status"] == "ready"
    assert os.path.isfile(os.path.join(again.media_folder(mid), "proxy.mp4"))


def test_fichier_deplace_signale(proj, files, tmp_path):
    moved = tmp_path / "bientot_parti.mp4"
    moved.write_bytes(files["mute"].read_bytes())
    mid = proj.add_media(str(moved))["id"]
    jobs.MEDIA.join()
    moved.unlink()
    again = TimelineProject.load(proj.work_dir, proj.id)
    assert again.media(mid)["status"] == "missing"


def test_retirer_un_media_emporte_ses_clips(proj, files):
    mid = proj.add_media(str(files["mute"]))["id"]
    jobs.MEDIA.join()
    proj.apply({"clips": [{"id": "c1", "track": "tv1", "kind": "video", "media": mid,
                           "start": 0, "dur": 1, "in": 0}]})
    assert len(proj.state["clips"]) == 1
    assert proj.remove_media(mid)
    assert proj.state["clips"] == [] and not os.path.exists(proj.media_folder(mid))


# ---------------------------------------------------------------------- API

@pytest.fixture
def client(server_module, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "WORK_DIR", str(tmp_path / "work"))
    server_module.timeline_api.TIMELINES.clear()
    try:
        c = TestClient(server_module.app)
    except TypeError as exc:
        pytest.skip(f"TestClient inutilisable : {exc}")
    pid = c.post("/api/timeline", json={}).json()["id"]
    c.pid = pid
    return c


def test_api_televersement(client, files):
    data = files["video"].read_bytes()
    r = client.post(f"/api/timeline/{client.pid}/media/upload", params={"name": "Mon clip.mp4"},
                    content=data)
    view = r.json()
    assert r.status_code == 200 and view["copied"] and view["name"] == "Mon clip.mp4"
    jobs.MEDIA.join()
    media = client.get(f"/api/timeline/{client.pid}/media").json()
    assert not media["busy"] and media["media"][0]["status"] == "ready"
    urls = media["media"][0]["urls"]
    proxy = client.get(urls["proxy"])
    assert proxy.status_code == 200 and proxy.headers["content-type"] == "video/mp4"
    assert client.get(urls["thumbs"]).headers["content-type"] == "image/jpeg"
    assert len(client.get(urls["wave"]).content) == media["media"][0]["waveform"]["count"]


@pytest.mark.ffmpeg
def test_api_voix_off_enregistree(client, tmp_path):
    # le navigateur envoie un webm/opus : converti en wav mono 48 kHz, puis média audio prêt
    rec = tmp_path / "rec.webm"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1.5",
                    "-c:a", "libopus", str(rec)], check=True)
    r = client.post(f"/api/timeline/{client.pid}/media/record", params={"name": "Voix off 1"},
                    content=rec.read_bytes())
    view = r.json()
    assert r.status_code == 200, view
    assert view["name"] == "Voix off 1.wav" and view["kind"] == "audio" and view["copied"]
    jobs.MEDIA.join()
    m = client.get(f"/api/timeline/{client.pid}/media").json()["media"][0]
    assert m["status"] == "ready" and m["has_audio"] and abs(m["duration"] - 1.5) < 0.1
    r = client.post(f"/api/timeline/{client.pid}/media/record", params={"name": "vide"}, content=b"")
    assert r.status_code == 400


def test_api_televersement_refuse(client):
    r = client.post(f"/api/timeline/{client.pid}/media/upload", params={"name": "notes.txt"},
                    content=b"x")
    assert r.status_code == 400
    r = client.post(f"/api/timeline/{client.pid}/media/upload", params={"name": "vide.mp4"},
                    content=b"")
    assert r.status_code == 400


def test_api_import_par_chemins(client, files, tmp_path):
    folder = os.path.dirname(files["video"])
    r = client.post(f"/api/timeline/{client.pid}/media/paths",
                    json={"paths": [folder, str(files["video"]), str(tmp_path / "rien.mp4")]}).json()
    names = sorted(m["name"] for m in r["added"])
    assert names == ["clip.mp4", "muet.mp4", "musique.mp3", "pause.wav", "photo.png",
                     "portrait.mp4"]
    reasons = sorted(s["reason"] for s in r["skipped"])
    assert reasons == ["déjà dans le projet", "introuvable"]
    assert all(not m["copied"] for m in r["added"])
    jobs.MEDIA.join()


def test_api_supprimer_relancer_relier(client, files, tmp_path):
    copy = tmp_path / "copie.mp4"
    copy.write_bytes(files["mute"].read_bytes())
    [m] = client.post(f"/api/timeline/{client.pid}/media/paths",
                      json={"paths": [str(copy)]}).json()["added"]
    jobs.MEDIA.join()
    base = f"/api/timeline/{client.pid}/media/{m['id']}"
    assert client.post(base + "/retry").json()["ok"]
    jobs.MEDIA.join()
    assert client.post(base + "/relink", json={"path": str(tmp_path / "nulle_part.mp4")}).status_code == 400
    assert client.post(base + "/relink", json={"path": str(files["mute"])}).json()["ok"]
    jobs.MEDIA.join()
    assert client.get(base + "/nimportequoi").status_code == 404
    assert client.delete(base).json()["ok"]
    assert client.delete(base).status_code == 404
    assert client.get(f"/api/timeline/{client.pid}/media").json()["media"] == []


def test_api_arret_sur_image(client, files):
    [m] = client.post(f"/api/timeline/{client.pid}/media/paths", json={"paths": [str(files["video"])]}).json()["added"]
    jobs.MEDIA.join()
    r = client.post(f"/api/timeline/{client.pid}/freeze", json={"media": m["id"], "at": 1.5}).json()
    assert r["kind"] == "image" and r["copied"] and "arrêt 1.50 s" in r["name"]
    jobs.MEDIA.join()
    media = {x["id"]: x for x in client.get(f"/api/timeline/{client.pid}/media").json()["media"]}
    assert media[r["id"]]["status"] == "ready" and media[r["id"]]["w"] == 640
    assert client.post(f"/api/timeline/{client.pid}/freeze", json={"media": "nope", "at": 1}).status_code == 400
