"""Boîte à outils : extraction du son d'une vidéo."""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from engine.tools import audio


# ------------------------------------------------------------------ réglages

def test_mp3_192_par_defaut():
    args = audio.encode_args()
    assert args[:4] == ["-c:a", "libmp3lame", "-b:a", "192k"]


@pytest.mark.parametrize("fmt, codec", [("m4a", "aac"), ("opus", "libopus"),
                                        ("ogg", "libvorbis"), ("flac", "flac")])
def test_chaque_format_a_son_encodeur(fmt, codec):
    assert audio.encode_args(fmt)[:2] == ["-c:a", codec]


def test_wav_choisit_le_pcm_selon_la_profondeur():
    assert audio.encode_args("wav", 16)[:2] == ["-c:a", "pcm_s16le"]
    assert audio.encode_args("wav", 24)[:2] == ["-c:a", "pcm_s24le"]


def test_sans_perte_n_a_pas_de_debit():
    assert "-b:a" not in audio.encode_args("flac", 24)
    assert "-b:a" not in audio.encode_args("wav")


def test_opus_force_48_khz_sauf_choix_explicite():
    assert audio.encode_args("opus")[-2:] == ["-ar", "48000"]
    args = audio.encode_args("opus", sample_rate=44100)
    assert args[args.index("-ar") + 1] == "44100"


def test_frequence_et_canaux():
    args = audio.encode_args("mp3", 320, sample_rate=44100, channels="mono")
    assert args[args.index("-ar") + 1] == "44100"
    assert args[args.index("-ac") + 1] == "1"


@pytest.mark.parametrize("kw", [{"fmt": "xyz"}, {"fmt": "mp3", "quality": 999},
                                {"fmt": "wav", "quality": 192},
                                {"sample_rate": 12345}, {"channels": "5.1"}])
def test_reglages_invalides(kw):
    with pytest.raises(ValueError):
        audio.encode_args(**kw)


def test_catalogue_coherent():
    cat = audio.catalog()
    assert cat["default"] == "mp3"
    for f in cat["formats"]:
        assert f["default"] in f["qualities"]


def test_output_path_n_ecrase_rien(tmp_path):
    src = tmp_path / "clip.mp4"
    assert audio.output_path(str(src)) == str(tmp_path / "clip.mp3")
    (tmp_path / "clip.mp3").write_bytes(b"")
    (tmp_path / "clip (1).mp3").write_bytes(b"")
    assert audio.output_path(str(src)) == str(tmp_path / "clip (2).mp3")
    assert audio.output_path("clip.mp4", "flac", folder=str(tmp_path)) == str(tmp_path / "clip.flac")


# ------------------------------------------------------------- vrai ffmpeg

@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=44100",
         "-c:v", "mpeg4", "-c:a", "aac", "-ac", "2", "-shortest", str(path)],
        check=True, capture_output=True, timeout=60,
    )
    return path


def _streams(path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return out.stdout.split()


@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt", list(audio.FORMATS))
def test_extraction_dans_chaque_format(clip, tmp_path, fmt):
    out = tmp_path / f"out.{audio.FORMATS[fmt]['ext']}"
    progress: list[float] = []
    res = audio.extract_audio(str(clip), str(out), fmt, on_progress=progress.append)
    assert out.is_file() and res["size"] > 0
    assert _streams(out) == ["audio"]            # plus d'image
    assert audio.probe_audio(str(out)).duration == pytest.approx(2.0, abs=0.15)
    assert all(0.0 <= p <= 1.0 for p in progress)


@pytest.mark.ffmpeg
def test_flac_24_bits_et_mono(clip, tmp_path):
    out = tmp_path / "out.flac"
    audio.extract_audio(str(clip), str(out), "flac", 24, sample_rate=48000, channels="mono")
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=bits_per_raw_sample,sample_rate,channels", "-of", "json", str(out)],
        capture_output=True, text=True).stdout
    assert '"bits_per_raw_sample": "24"' in info
    assert '"sample_rate": "48000"' in info
    assert '"channels": 1' in info


@pytest.mark.ffmpeg
def test_video_muette_refusee(tmp_path):
    mute = tmp_path / "mute.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=160x120:rate=25:duration=1", "-c:v", "mpeg4",
                    str(mute)], check=True, capture_output=True, timeout=60)
    with pytest.raises(ValueError, match="piste audio"):
        audio.extract_audio(str(mute), str(tmp_path / "x.mp3"))


# ---------------------------------------------------------------------- API

@pytest.fixture
def client(server_module, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server_module, "TOOLS_DIR", str(tmp_path / "tools"))
    server_module.TOOL_JOBS.clear()
    try:
        return TestClient(server_module.app)
    except TypeError as exc:  # starlette < 0.28 avec httpx >= 0.28
        pytest.skip(f"TestClient inutilisable : {exc}")


def _wait(client, tid: str) -> dict:
    for _ in range(200):
        s = client.get(f"/api/tools/jobs/{tid}").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("tâche jamais terminée")


def test_api_formats(client):
    assert client.get("/api/tools/audio/formats").json()["default"] == "mp3"


def test_api_refuse_sans_video_ou_reglage_invalide(client, tmp_path):
    assert client.post("/api/tools/audio", data={}).status_code == 400
    assert client.post("/api/tools/audio", data={"path": str(tmp_path / "rien.mp4")}).status_code == 400
    r = client.post("/api/tools/audio", data={"path": "x.mp4", "format": "mp3", "quality": 7})
    assert r.status_code == 400 and "Qualité" in r.json()["detail"]


def test_api_tache_inconnue(client):
    assert client.get("/api/tools/jobs/nope").status_code == 404
    assert client.get("/api/tools/result/nope").status_code == 404


@pytest.mark.ffmpeg
def test_api_chemin_ecrit_a_cote_de_la_video(client, clip):
    tid = client.post("/api/tools/audio", data={"path": str(clip)}).json()["job_id"]
    s = _wait(client, tid)
    assert s["status"] == "done", s["message"]
    assert s["result"]["output"] == str(clip.with_suffix(".mp3"))
    r = client.get(f"/api/tools/result/{tid}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"


@pytest.mark.ffmpeg
def test_api_envoi_efface_la_copie_de_la_video(client, clip, server_module):
    with open(clip, "rb") as f:
        tid = client.post("/api/tools/audio", files={"file": ("mon clip.mp4", f, "video/mp4")},
                          data={"format": "wav", "quality": "24"}).json()["job_id"]
    s = _wait(client, tid)
    assert s["status"] == "done", s["message"]
    assert s["result"]["output"].endswith("mon clip.wav")
    assert os.listdir(os.path.dirname(s["result"]["output"])) == ["mon clip.wav"]
    assert client.get(f"/api/tools/result/{tid}").headers["content-type"] == "audio/wav"
