"""Construction des commandes ffmpeg (sans lancer ffmpeg)."""
from __future__ import annotations

import pytest

from engine.edl import KeepSegment
from engine.pipeline import render
from engine.pipeline.render import _cut_graph, _video_codec_args, output_size

X264 = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]


@pytest.fixture
def encoder_works(monkeypatch):
    """Simule le test d'ouverture de l'encodeur ; note les encodeurs sondés."""
    original = render._encoder_works
    original.cache_clear()
    probed: list[str] = []

    def install(result: bool):
        def fake(enc):
            probed.append(enc)
            return result
        monkeypatch.setattr(render, "_encoder_works", fake)
        return probed

    yield install
    original.cache_clear()


@pytest.mark.parametrize("enc", ["h264_nvenc", "hevc_qsv", "h264_amf", "h264_videotoolbox"])
def test_encodeur_materiel_disponible(encoder_works, enc):
    probed = encoder_works(True)
    assert _video_codec_args(enc) == ["-c:v", enc, "-b:v", "8M"]
    assert probed == [enc]


def test_encodeur_materiel_indisponible_repli_x264(encoder_works, capsys):
    encoder_works(False)
    assert _video_codec_args("h264_nvenc") == X264
    assert "h264_nvenc indisponible" in capsys.readouterr().out


@pytest.mark.parametrize("enc, expected", [
    ("libx264", X264),
    ("libx265", ["-c:v", "libx265", "-b:v", "8M"]),
    ("mpeg4", ["-c:v", "mpeg4", "-b:v", "8M"]),
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
