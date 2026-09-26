"""Boîte à outils : supprimer l'arrière-plan d'une image ou d'une vidéo.

Le sujet (une personne, avec ce qu'elle tient) est détouré par MODNet
(engine/pipeline/matting.py), choisi d'un clic s'il y en a plusieurs. Le masque
d'une vidéo est calculé en définition réduite (≤ 1280 px), puis agrandi et
appliqué à la source en pleine définition par ffmpeg.

Formats de sortie :
    image  png  transparent          jpg  sur une couleur
    vidéo  webm transparent (VP9)    mov  transparent (ProRes 4444, pour les
           logiciels de montage)     mp4  sur une couleur (H.264)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from engine.pipeline import matting
from engine.pipeline.render import _run_ffmpeg
from engine.timeline.media import probe_media

FORMATS = {
    "image": {"png": "PNG transparent", "jpg": "JPG sur une couleur"},
    "video": {"webm": "WebM transparent", "mov": "MOV transparent (ProRes 4444)", "mp4": "MP4 sur une couleur"},
}
MIMES = {"png": "image/png", "jpg": "image/jpeg", "webm": "video/webm", "mov": "video/quicktime", "mp4": "video/mp4"}
WORK_SIDE = 1280           # plus grand côté du calcul du masque d'une vidéo


def prepare(src: str, preview: str) -> dict:
    """Sonde la source et en tire une image d'aperçu (pour choisir le sujet)."""
    info = probe_media(src)
    if info["kind"] not in ("image", "video"):
        raise ValueError("Il faut une image ou une vidéo.")
    seek = [] if info["kind"] == "image" else ["-ss", f"{min(1.0, info['duration'] * 0.2):.2f}"]
    _run_ffmpeg([*seek, "-i", src, "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "3", preview])
    return info


def output_name(src: str, fmt: str, folder: str | None = None) -> str:
    base = os.path.splitext(os.path.basename(src))[0] + "_detoure"
    folder = folder or os.path.dirname(os.path.abspath(src))
    path = os.path.join(folder, f"{base}.{fmt}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).{fmt}")
        n += 1
    return path


def run(src: str, info: dict, point: tuple[float, float], t: float, fmt: str, bg: str, out: str,
        on_progress=None) -> dict:
    """Détoure `src` vers `out`. Renvoie le résultat (chemin, type, taille)."""
    kind = info["kind"]
    if fmt not in FORMATS[kind]:
        raise ValueError(f"Format inconnu pour une {'image' if kind == 'image' else 'vidéo'} : {fmt}")
    color = (bg or "#00B140").lstrip("#")
    work = tempfile.mkdtemp(prefix="cutout_")
    try:
        if kind == "image":
            matting.process_image(src, point, work)
            from PIL import Image
            cut = Image.open(os.path.join(work, "cutout.png"))
            if fmt == "png":
                cut.save(out)
            else:
                rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
                flat = Image.new("RGBA", cut.size, rgb + (255,))
                flat.alpha_composite(cut)
                flat.convert("RGB").save(out, quality=94)
        else:
            k = min(1.0, WORK_SIDE / max(info["w"], info["h"]))
            small = dict(info, w=int(info["w"] * k) // 2 * 2, h=int(info["h"] * k) // 2 * 2)
            matting.process_video(src, small, point, t, work, cutout=False,
                                  on_progress=lambda f: on_progress and on_progress(0.85 * f))
            w, h = info["w"] - info["w"] % 2, info["h"] - info["h"] % 2
            fps = f"{float(info.get('fps') or 30):g}"
            graph = (f"[1:v]scale={w}:{h}:flags=bicubic,format=gray[m];"
                     f"[0:v]scale={w}:{h},format=yuva420p[v];[v][m]alphamerge[a]")
            audio = ["-map", "0:a?"]
            if fmt == "webm":
                graph += ";[a]format=yuva420p[o]"
                codec = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "30", "-b:v", "0", "-deadline", "good",
                         "-cpu-used", "4", "-row-mt", "1", "-auto-alt-ref", "0", "-c:a", "libopus", "-b:a", "128k"]
            elif fmt == "mov":
                graph += ";[a]format=yuva444p10le[o]"
                codec = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-c:a", "pcm_s16le"]
            else:
                graph += f";color=c=0x{color}:s={w}x{h}:r={fps}[bg];[bg][a]overlay=shortest=1:format=auto,format=yuv420p[o]"
                codec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
            _run_ffmpeg(["-i", src, "-i", os.path.join(work, "matte.mp4"), "-map", "[o]", *audio, *codec, out],
                        filtergraph=graph, duration=float(info.get("duration") or 0),
                        on_progress=lambda f: on_progress and on_progress(0.85 + 0.15 * f))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"output": out, "mime": MIMES[fmt], "size": os.path.getsize(out), "kind": kind}
