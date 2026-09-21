"""Outil : extraire la bande son d'une vidéo.

    python -m engine.tools.audio "C:\\chemin\\clip.mp4"                 # MP3 192 kbit/s
    python -m engine.tools.audio clip.mp4 -f flac -q 24 --rate 48000

Formats compressés (MP3, AAC, Opus, Vorbis) : la qualité est un débit en
kbit/s. Formats sans perte (WAV, FLAC) : la qualité est une profondeur en bits.
Seule la première piste audio est extraite ; l'image est ignorée.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass

from engine.pipeline.render import _run_ffmpeg

DEFAULT_FORMAT = "mp3"

# name -> extension, encodeur, type MIME, qualités proposées, qualité par défaut.
FORMATS: dict[str, dict] = {
    "mp3": {"label": "MP3", "ext": "mp3", "codec": "libmp3lame", "mime": "audio/mpeg",
            "lossless": False, "qualities": [96, 128, 160, 192, 256, 320], "default": 192},
    "m4a": {"label": "AAC (M4A)", "ext": "m4a", "codec": "aac", "mime": "audio/mp4",
            "lossless": False, "qualities": [96, 128, 160, 192, 256, 320], "default": 192},
    "opus": {"label": "Opus", "ext": "opus", "codec": "libopus", "mime": "audio/ogg",
             "lossless": False, "qualities": [64, 96, 128, 160, 192, 256], "default": 128},
    "ogg": {"label": "Ogg Vorbis", "ext": "ogg", "codec": "libvorbis", "mime": "audio/ogg",
            "lossless": False, "qualities": [96, 128, 160, 192, 256, 320], "default": 192},
    "wav": {"label": "WAV", "ext": "wav", "codec": "pcm_s{bits}le", "mime": "audio/wav",
            "lossless": True, "qualities": [16, 24], "default": 16},
    "flac": {"label": "FLAC", "ext": "flac", "codec": "flac", "mime": "audio/flac",
             "lossless": True, "qualities": [16, 24], "default": 16},
}
SAMPLE_RATES = [22050, 44100, 48000]
CHANNELS = {"mono": 1, "stereo": 2}


@dataclass
class AudioInfo:
    duration: float
    codec: str
    sample_rate: int
    channels: int


def catalog() -> dict:
    """Ce que l'interface doit proposer (formats, qualités, fréquences)."""
    return {
        "default": DEFAULT_FORMAT,
        "formats": [{"name": k, "label": f["label"], "ext": f["ext"],
                     "lossless": f["lossless"], "qualities": f["qualities"],
                     "default": f["default"]} for k, f in FORMATS.items()],
        "sample_rates": SAMPLE_RATES,
        "channels": list(CHANNELS),
    }


def encode_args(fmt: str = DEFAULT_FORMAT, quality: int | None = None,
                sample_rate: int | None = None, channels: str | None = None) -> list[str]:
    """Arguments ffmpeg d'encodage. Lève ValueError sur un réglage inconnu."""
    spec = FORMATS.get(fmt)
    if spec is None:
        raise ValueError(f"Format inconnu : {fmt}")
    q = spec["default"] if quality is None else int(quality)
    if q not in spec["qualities"]:
        unit = "bits" if spec["lossless"] else "kbit/s"
        raise ValueError(f"Qualité {q} {unit} non proposée pour {spec['label']}.")

    args = ["-c:a", spec["codec"].format(bits=q)]
    if not spec["lossless"]:
        args += ["-b:a", f"{q}k"]
    elif fmt == "flac":
        # FLAC 24 bits est stocké dans des échantillons 32 bits côté ffmpeg.
        args += ["-sample_fmt", "s16" if q == 16 else "s32"]
        if q == 24:
            args += ["-bits_per_raw_sample", "24"]

    if sample_rate:
        if int(sample_rate) not in SAMPLE_RATES:
            raise ValueError(f"Fréquence non proposée : {sample_rate} Hz")
        args += ["-ar", str(int(sample_rate))]
    elif fmt == "opus":
        args += ["-ar", "48000"]   # Opus n'encode qu'en 8/12/16/24/48 kHz
    if channels:
        if channels not in CHANNELS:
            raise ValueError(f"Canaux inconnus : {channels}")
        args += ["-ac", str(CHANNELS[channels])]
    if fmt == "mp3":
        args += ["-id3v2_version", "3"]   # balises lues par tous les lecteurs
    return args


def probe_audio(path: str) -> AudioInfo:
    """Première piste audio de `path`. Lève ValueError si le fichier n'en a pas."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
           "-show_streams", "-select_streams", "a:0", path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise ValueError("Fichier illisible : ce n'est pas une vidéo ou un son reconnu.")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("Cette vidéo ne contient pas de piste audio.")
    a = streams[0]
    duration = float((data.get("format") or {}).get("duration") or a.get("duration") or 0.0)
    return AudioInfo(duration=duration, codec=a.get("codec_name", ""),
                     sample_rate=int(a.get("sample_rate") or 0),
                     channels=int(a.get("channels") or 0))


def output_path(src: str, fmt: str = DEFAULT_FORMAT, folder: str | None = None) -> str:
    """`<dossier>/<nom de la source>.<ext>`, sans jamais écraser un fichier existant."""
    base = os.path.splitext(os.path.basename(src))[0]
    folder = folder or os.path.dirname(os.path.abspath(src))
    ext = FORMATS[fmt]["ext"]
    path = os.path.join(folder, f"{base}.{ext}")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).{ext}")
        n += 1
    return path


def extract_audio(src: str, out: str, fmt: str = DEFAULT_FORMAT, quality: int | None = None,
                  sample_rate: int | None = None, channels: str | None = None,
                  on_progress=None) -> dict:
    """Extrait la première piste audio de `src` dans `out`."""
    args = encode_args(fmt, quality, sample_rate, channels)   # valide avant de sonder
    info = probe_audio(src)
    _run_ffmpeg(["-i", src, "-map", "0:a:0", "-vn", "-sn", "-dn",
                 "-map_metadata", "0", *args, out],
                duration=info.duration, on_progress=on_progress)
    return {"output": out, "duration": info.duration, "size": os.path.getsize(out),
            "format": fmt, "mime": FORMATS[fmt]["mime"]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Montage IA - extraire le son d'une vidéo")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default=None,
                    help="fichier de sortie (défaut : à côté de la vidéo)")
    ap.add_argument("-f", "--format", default=DEFAULT_FORMAT, choices=list(FORMATS))
    ap.add_argument("-q", "--quality", type=int, default=None,
                    help="kbit/s (mp3, m4a, opus, ogg) ou bits (wav, flac)")
    ap.add_argument("--rate", type=int, default=None, choices=SAMPLE_RATES,
                    help="fréquence d'échantillonnage (défaut : celle de la source)")
    ap.add_argument("--channels", default=None, choices=list(CHANNELS))
    args = ap.parse_args()

    out = args.output or output_path(args.input, args.format)
    try:
        res = extract_audio(args.input, out, args.format, args.quality, args.rate,
                            args.channels,
                            on_progress=lambda p: print(f"\r{p * 100:5.1f} %", end=""))
    except ValueError as exc:
        ap.error(str(exc))
    print(f"\nOK -> {res['output']} ({res['size'] / 1048576:.1f} Mo)")


if __name__ == "__main__":
    main()
