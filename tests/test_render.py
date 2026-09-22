"""Construction des commandes ffmpeg (sans lancer ffmpeg)."""
from __future__ import annotations

import sys

import pytest

from engine.edl import KeepSegment
from engine.pipeline import render
from engine.pipeline.render import _cut_graph, _video_codec_args, output_size

X264 = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]


@pytest.fixture
def encoder_works(monkeypatch):
    """Simule le test d'ouverture de l'encodeur ; note (encodeur, taille) sondés."""
    original = render._encoder_works
    original.cache_clear()
    probed: list[tuple[str, str]] = []

    def install(result):
        def fake(enc, size="320x240"):
            probed.append((enc, size))
            return result(enc) if callable(result) else result
        monkeypatch.setattr(render, "_encoder_works", fake)
        return probed

    yield install
    original.cache_clear()


@pytest.mark.parametrize("enc", ["h264_nvenc", "hevc_qsv", "h264_amf", "h264_videotoolbox"])
def test_encodeur_materiel_disponible(encoder_works, enc):
    probed = encoder_works(True)
    args = _video_codec_args(enc, 1080, 1920)
    assert args[:4] == ["-c:v", enc, "-b:v", "8M"]
    assert args[4:6] == ["-pix_fmt", "yuv420p"]   # les sources 10 bits repassent en 8 bits
    assert probed == [(enc, "320x240")]


def test_hevc_marque_pour_apple(encoder_works):
    encoder_works(True)
    assert _video_codec_args("hevc_nvenc", 1080, 1920)[-2:] == ["-tag:v", "hvc1"]


def test_auto_prend_nvenc(encoder_works, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    probed = encoder_works(True)
    assert _video_codec_args("auto", 1080, 1920)[1] == "h264_nvenc"
    assert probed == [("h264_nvenc", "320x240")]


def test_auto_prend_videotoolbox_sur_mac(encoder_works, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    encoder_works(True)
    assert _video_codec_args("auto", 1080, 1920)[1] == "h264_videotoolbox"
    assert _video_codec_args("auto", 7680, 4320)[1] == "hevc_videotoolbox"


def test_encodeur_materiel_indisponible_repli_x264(encoder_works, capsys):
    encoder_works(False)
    assert _video_codec_args("h264_nvenc") == X264
    assert "h264_nvenc indisponible" in capsys.readouterr().out


def test_auto_sans_gpu_repli_silencieux(encoder_works, capsys):
    encoder_works(False)
    assert _video_codec_args("auto") == X264
    assert capsys.readouterr().out == ""


def test_8k_passe_en_hevc_teste_a_la_vraie_taille(encoder_works, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    probed = encoder_works(True)
    args = _video_codec_args("auto", 7680, 4320)
    assert args[:2] == ["-c:v", "hevc_nvenc"]
    assert probed == [("hevc_nvenc", "7680x4320")]


def test_8k_sans_hevc_materiel_repli_x264(encoder_works):
    encoder_works(lambda enc: enc != "hevc_nvenc")
    assert _video_codec_args("h264_nvenc", 7680, 4320) == X264


@pytest.mark.parametrize("w, h, rate", [
    (1080, 1920, "8M"), (720, 1280, "8M"), (0, 0, "8M"),
    (3840, 2160, "32M"), (7680, 4320, "80M"),
])
def test_debit_proportionnel_a_la_definition(encoder_works, w, h, rate):
    encoder_works(True)
    assert _video_codec_args("h264_nvenc", w, h)[3] == rate


@pytest.mark.parametrize("enc, expected", [
    ("libx264", X264),
    ("libx265", ["-c:v", "libx265", "-b:v", "8M", "-pix_fmt", "yuv420p"]),
    ("mpeg4", ["-c:v", "mpeg4", "-b:v", "8M", "-pix_fmt", "yuv420p"]),
])
def test_encodeur_logiciel_sans_sonde(encoder_works, enc, expected):
    probed = encoder_works(False)
    assert _video_codec_args(enc) == expected
    assert probed == []


SEGS = [KeepSegment(start=1.0, end=2.5), KeepSegment(start=4.25, end=6.0)]


def test_cut_graph_vertical():
    graph, vmap, amap = _cut_graph(SEGS, True, 1920, 1080)
    assert (vmap, amap) == ("[vout]", "[ac]")
    assert "[0:v]trim=start=1.000:end=2.500,setpts=PTS-STARTPTS[v0]" in graph
    assert "[0:a]atrim=start=4.250:end=6.000,asetpts=PTS-STARTPTS[a1]" in graph
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac]" in graph
    assert "crop=w='min(iw,ih*9/16)'" in graph
    assert graph.endswith("scale=1080:1920[vout]")


def test_cut_graph_vertical_proxy():
    graph, _, _ = _cut_graph(SEGS, True, 1920, 1080, scale=0.5)
    assert graph.endswith("scale=540:960[vout]")


def test_cut_graph_horizontal_sans_mise_a_l_echelle():
    graph, vmap, amap = _cut_graph(SEGS, False, 1280, 720)
    assert (vmap, amap) == ("[vc]", "[ac]")
    assert "scale=" not in graph and "crop=" not in graph
    assert graph.endswith("concat=n=2:v=1:a=1[vc][ac]")


def test_cut_graph_horizontal_proxy_dimensions_paires():
    graph, vmap, _ = _cut_graph(SEGS, False, 1279, 719, scale=0.5)
    assert vmap == "[vout]"
    assert graph.endswith("[vc]scale=640:360[vout]")


def test_output_size():
    assert output_size(1920, 1080, True) == (1080, 1920)
    assert output_size(1920, 1080, False) == (1920, 1080)
    assert output_size("640", "480", False) == (640, 480)
