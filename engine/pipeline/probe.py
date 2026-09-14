"""Sonde la source via ffprobe (durée, résolution, fps).

Les vidéos de téléphone portent souvent une matrice de rotation : ffprobe
annonce 1920x1080 alors que ffmpeg redresse l'image et travaille sur du
1080x1920. On renvoie donc les dimensions APRÈS redressement — celles que
verront les filtres, le recadrage et le repère des sous-titres.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float


def probe(path: str) -> MediaInfo:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")

    num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
    fps = float(num) / float(den) if float(den) else 0.0

    duration = float(data["format"].get("duration") or v.get("duration") or 0.0)
    width, height = int(v["width"]), int(v["height"])
    if abs(_rotation(v)) % 180 == 90:
        width, height = height, width
    return MediaInfo(duration=duration, width=width, height=height, fps=fps)


def _rotation(stream: dict) -> int:
    """Rotation déclarée par la source, en degrés (0 si absente)."""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"])))
            except (TypeError, ValueError):
                pass
    try:
        return int(round(float(stream.get("tags", {}).get("rotate", 0))))
    except (TypeError, ValueError):
        return 0
