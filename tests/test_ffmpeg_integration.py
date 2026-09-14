"""Intégration réelle : sonde + coupe/recadrage d'une petite vidéo synthétique."""
from __future__ import annotations

import subprocess

import pytest

from engine.edl import KeepSegment
from engine.pipeline import render
from engine.pipeline.probe import probe

pytestmark = pytest.mark.ffmpeg


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=60,
    )
    return path


def test_probe_puis_render_cut_vertical(source, tmp_path):
    if not render._encoder_works("libx264"):
        pytest.skip("ffmpeg compilé sans libx264")

    info = probe(str(source))
    assert info.duration == pytest.approx(3.0, abs=0.1)
    assert (info.width, info.height) == (320, 240)
    assert info.fps == pytest.approx(25.0)

    segs = [KeepSegment(start=0.2, end=1.2), KeepSegment(start=1.8, end=2.6)]
    expected = sum(s.end - s.start for s in segs)
    out = tmp_path / "cut.mp4"
    progress: list[float] = []
    render.render_cut(str(source), segs, str(out), vertical=True, encoder="libx264",
                      width=info.width, height=info.height, duration=expected,
                      on_progress=progress.append)

    assert out.is_file()
    result = probe(str(out))
    assert result.duration == pytest.approx(expected, abs=0.15)
    assert (result.width, result.height) == (1080, 1920)
    assert all(0.0 <= p <= 1.0 for p in progress)
