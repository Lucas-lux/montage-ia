"""Préparation des médias d'un montage : sonde, proxy, vignettes, forme d'onde.

L'éditeur ne lit jamais les originaux : une vidéo 4K HEVC de téléphone se
déplace mal dans un navigateur, et il faut pouvoir en superposer plusieurs.
Chaque média reçoit donc :

  * un proxy : vidéo H.264 ≤ 960 px, 30 i/s au plus, une image clé toutes les
    demi-secondes et sans images B (un déplacement de la tête de lecture tombe
    presque toujours juste) ; son AAC stéréo ; image JPEG ≤ 1920 px ;
  * une planche de vignettes (JPEG en grille) que la timeline découpe ;
  * une forme d'onde : un octet (pic 0..255) par centième de seconde ;
  * une affiche pour le panneau Médias.

Avec une carte NVIDIA, la vidéo est décodée et réduite par la carte (NVDEC +
`scale_cuda`) : trois fois plus rapide sur une vidéo 4K HEVC de téléphone. Au
moindre refus (codec non pris en charge, pas de carte), on repasse par le
processeur.

Le proxy garde la même origine des temps que la source (0 = début) : un point
d'entrée `in` vaut autant pour l'aperçu que pour l'export, qui relit
l'original.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from array import array

from engine.pipeline.render import _run_ffmpeg

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts",
             ".wmv", ".flv", ".3gp", ".mpg", ".mpeg", ".mxf", ".ogv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma",
             ".aif", ".aiff", ".caf", ".amr", ".ac3"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
             ".heic", ".heif", ".avif"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT

PROXY_BOX = 960          # plus grand côté du proxy vidéo
PROXY_FPS_MAX = 30
IMAGE_BOX = 1920
THUMB_H = 64             # hauteur d'une vignette de la timeline
THUMB_MAX = 400          # vignettes par média, au plus
THUMB_COLS = 20
WAVE_RATE = 100          # pics par seconde
_WAVE_SR = 4000          # échantillonnage de travail de la forme d'onde


def kind_of(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return None


def is_media(path: str) -> bool:
    return kind_of(path) is not None


def _natural(name: str) -> list:
    """Tri « humain » : clip2 avant clip10."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def scan_folder(folder: str, recursive: bool = False) -> list[str]:
    """Médias d'un dossier, dans l'ordre naturel des noms."""
    out: list[str] = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=_natural)
            out += [os.path.join(root, f) for f in sorted(files, key=_natural) if is_media(f)]
        return out
    for f in sorted(os.listdir(folder), key=_natural):
        full = os.path.join(folder, f)
        if os.path.isfile(full) and is_media(f):
            out.append(full)
    return out


# ------------------------------------------------------------------- sonde

def probe_media(path: str) -> dict:
    """Genre, durée, dimensions (après redressement), images/s, présence de son.

    Lève ValueError si le fichier n'est pas un média lisible.
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise ValueError("Fichier illisible : format non reconnu.")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    ext_kind = kind_of(path)

    # Pochette d'un MP3 = flux vidéo « attached_pic » : ce n'est pas une vidéo.
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    if ext_kind == "image" or (videos and _still(videos[0], fmt) and not audios):
        kind = "image"
    elif videos:
        kind = "video"
    elif audios:
        kind = "audio"
    else:
        raise ValueError("Ce fichier ne contient ni image ni son.")

    info = {"kind": kind, "duration": 0.0, "w": 0, "h": 0, "fps": 0.0,
            "has_audio": bool(audios) and kind != "image"}
    if kind in ("video", "image"):
        v = videos[0] if videos else {}
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if abs(_rotation(v)) % 180 == 90:
            w, h = h, w
        info["w"], info["h"] = w, h
        if not w or not h:
            raise ValueError("Dimensions de l'image illisibles.")
    if kind == "video":
        info["fps"] = round(_fps(videos[0]), 3)
    if kind in ("video", "audio"):
        dur = _float(fmt.get("duration"))
        if not dur:
            dur = max((_float(s.get("duration")) for s in videos + audios), default=0.0)
        if dur <= 0.05:
            raise ValueError("Durée illisible ou nulle.")
        info["duration"] = round(dur, 3)
    return info


def _float(v) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = str(stream.get(key) or "0/1").partition("/")
        try:
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= fps <= 240:
            return fps
    return 30.0


def _still(stream: dict, fmt: dict) -> bool:
    """Image fixe déguisée en vidéo (PNG, JPEG, GIF d'une image…)."""
    codec = stream.get("codec_name", "")
    frames = int(stream.get("nb_frames") or 0) if str(stream.get("nb_frames", "")).isdigit() else 0
    return codec in ("mjpeg", "png", "bmp", "webp", "tiff", "gif") and frames <= 1 and \
        _float(fmt.get("duration")) < 0.5


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"])))
            except (TypeError, ValueError):
                pass
    try:
        return int(round(float((stream.get("tags") or {}).get("rotate", 0))))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------- proxy

def proxy_size(w: int, h: int, box: int = PROXY_BOX) -> tuple[int, int]:
    """Dimensions paires tenant dans `box` x `box`, sans agrandir."""
    scale = min(1.0, box / max(w, h, 1))
    return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)


def proxy_name(kind: str) -> str:
    return {"video": "proxy.mp4", "audio": "proxy.m4a", "image": "proxy.jpg"}[kind]


# Proxy décodé par la carte graphique : essayé à chaque vidéo tant qu'un échec
# n'a pas montré que la machine ou ffmpeg n'en sont pas capables.
_GPU = {"ok": sys.platform != "darwin" and os.environ.get("MONTAGE_IA_GPU_PROXY") != "0"}
_NO_GPU = ("unrecognized hwaccel", "device creation failed", "no such filter", "cannot load",
           "cuda_error", "no device available", "cuinit", "unrecognized option")
_TURN = {90: ",transpose=clock", 180: ",hflip,vflip", 270: ",transpose=cclock"}


def display_turn(path: str) -> int | None:
    """Rotation (0, 90, 180, 270, sens horaire) que ffmpeg applique pour
    redresser la vidéo. None si l'image est aussi retournée en miroir : ce cas
    rare reste au processeur, qui le redresse tout seul."""
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                          "-print_format", "json", path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    streams = json.loads(res.stdout or "{}").get("streams") or [{}]
    for side in streams[0].get("side_data_list") or []:
        if "rotation" not in side:
            continue
        m = [float(x) for x in re.findall(r"-?\d+", re.sub(r"^\s*\d+:", "", side.get("displaymatrix") or "",
                                                           flags=re.M))]
        if len(m) >= 5 and m[0] * m[4] - m[1] * m[3] < 0:
            return None
        return round(-float(side["rotation"]) / 90) % 4 * 90
    return 0


def gpu_proxy_filter(w: int, h: int, fps: int, turn: int = 0) -> str:
    """Filtre du proxy décodé par la carte : cadence et réduction sur la carte,
    puis rotation sur le processeur (quelques centaines de pixels, c'est gratuit).
    `w` x `h` : taille du proxy à l'écran, rotation faite."""
    sw, sh = (h, w) if turn in (90, 270) else (w, h)
    return f"fps={fps},scale_cuda={sw}:{sh}:format=nv12,hwdownload,format=nv12{_TURN.get(turn, '')},format=yuv420p"


def make_proxy(src: str, out: str, info: dict, on_progress=None) -> None:
    kind = info["kind"]
    if kind == "image":
        w, h = proxy_size(info["w"], info["h"], IMAGE_BOX)
        _run_ffmpeg(["-i", src, "-frames:v", "1", "-vf", f"scale={w}:{h}", "-q:v", "3", out])
        return
    if kind == "audio":
        _run_ffmpeg(["-i", src, "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k",
                     "-ac", "2", "-ar", "48000", "-movflags", "+faststart", out],
                    duration=info["duration"], on_progress=on_progress)
        return
    w, h = proxy_size(info["w"], info["h"])
    fps = min(PROXY_FPS_MAX, round(info.get("fps") or 30))
    gop = max(1, round(fps / 2))
    maps = ["-map", "0:v:0"] + (["-map", "0:a:0"] if info.get("has_audio") else [])
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-tune", "fastdecode",
           "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0", "-bf", "0"]
    if info.get("has_audio"):
        enc += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    enc += ["-movflags", "+faststart", out]
    turn = display_turn(src) if _GPU["ok"] else None
    if turn is not None:
        try:
            # rotation d'origine remise à zéro : c'est le filtre qui redresse,
            # le proxy sort droit et sans métadonnée de rotation
            _run_ffmpeg(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-display_rotation", "0", "-i", src,
                         *maps, "-vf", gpu_proxy_filter(w, h, fps, turn), *enc],
                        duration=info["duration"], on_progress=on_progress)
            return
        except RuntimeError as exc:
            # pas de carte, ou ffmpeg sans CUDA : inutile de réessayer ; un
            # codec que la carte ne lit pas (ProRes…) ne concerne que ce fichier
            if any(hint in str(exc).lower() for hint in _NO_GPU):
                _GPU["ok"] = False
    _run_ffmpeg(["-i", src, *maps, "-vf", f"scale={w}:{h},fps={fps},format=yuv420p", *enc],
                duration=info["duration"], on_progress=on_progress)


# ----------------------------------------------------------- vignettes, affiche

def thumbs_layout(duration: float, kind: str) -> dict:
    """Combien de vignettes, à quel intervalle, en combien de colonnes."""
    if kind == "image" or duration <= 0:
        return {"count": 1, "interval": 0.0, "cols": 1, "rows": 1}
    interval = max(0.5, duration / THUMB_MAX)
    count = max(1, int(duration / interval) + (1 if duration % interval > interval * 0.25 else 0))
    count = min(count, THUMB_MAX)
    cols = min(THUMB_COLS, count)
    return {"count": count, "interval": round(interval, 4), "cols": cols,
            "rows": math.ceil(count / cols)}


def make_thumbs(proxy: str, out: str, info: dict) -> dict:
    """Planche de vignettes ; renvoie sa géométrie (taille d'une vignette comprise)."""
    lay = thumbs_layout(info.get("duration", 0.0), info["kind"])
    ratio = (info["w"] / info["h"]) if info.get("h") else 16 / 9
    tw = max(2, int(round(THUMB_H * ratio / 2)) * 2)
    if info["kind"] == "image":
        vf = f"scale={tw}:{THUMB_H}"
    else:
        vf = (f"fps=1/{lay['interval']},scale={tw}:{THUMB_H},"
              f"tile={lay['cols']}x{lay['rows']}")
    _run_ffmpeg(["-i", proxy, "-frames:v", "1", "-vf", vf, "-q:v", "5", out])
    return {**lay, "w": tw, "h": THUMB_H}


def make_poster(proxy: str, out: str, info: dict, height: int = 240) -> None:
    # Pas de -ss sur une image fixe : ffmpeg sauterait sa seule image et
    # terminerait « sans erreur » sans rien écrire.
    seek = [] if info["kind"] == "image" else         ["-ss", f"{min(1.0, info.get('duration', 0) * 0.1):.2f}"]
    _run_ffmpeg([*seek, "-i", proxy, "-frames:v", "1", "-vf", f"scale=-2:{height}", "-q:v", "4", out])
    if not os.path.isfile(out):
        raise RuntimeError("Affiche du média impossible à extraire.")


# ------------------------------------------------------------- forme d'onde

_WAVE_FLOOR_DB = -60.0    # en dessous : silence (0) ; 0 dB : 255


def make_waveform(src: str, out: str) -> dict:
    """Pics d'amplitude, un octet par centième de seconde, écrits dans `out`.

    Échelle en décibels, de -60 dB (0) à 0 dB (255) : une voix normale (pics
    vers -20 dB) reste lisible au lieu d'être écrasée par une échelle linéaire.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src, "-vn",
           "-ac", "1", "-ar", str(_WAVE_SR), "-f", "s16le", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError("Forme d'onde impossible : " +
                           res.stderr.decode("utf-8", "replace")[-300:])
    samples = array("h")
    samples.frombytes(res.stdout[: len(res.stdout) // 2 * 2])
    step = _WAVE_SR // WAVE_RATE
    peaks = bytearray()
    span = -_WAVE_FLOOR_DB
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        p = max(max(chunk), -min(chunk))
        if p <= 0:
            peaks.append(0)
            continue
        db = 20 * math.log10(p / 32767)
        peaks.append(max(0, min(255, int(255 * (db + span) / span))))
    with open(out, "wb") as f:
        f.write(bytes(peaks))
    return {"rate": WAVE_RATE, "count": len(peaks)}


# ------------------------------------------------------ silences au volume

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(path: str, noise_db: float = -35.0, min_dur: float = 0.4,
                    duration: float = 0.0) -> list[list[float]]:
    """Plages où le son reste sous `noise_db` pendant au moins `min_dur` secondes."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
           "-af", f"silencedetect=noise={noise_db:g}dB:d={min_dur:g}", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError("Détection des silences impossible.")
    out: list[list[float]] = []
    start = None
    for line in res.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append([round(start, 3), round(float(m.group(1)), 3)])
            start = None
    if start is not None:      # silence jusqu'à la fin du fichier
        out.append([round(start, 3), round(duration or start, 3)])
    return [s for s in out if s[1] > s[0]]
