"""Rendu ffmpeg : coupe+concat des segments gardés, recadrage 9:16, puis
incrustation des sous-titres et des émojis.

Deux qualités de rendu pour un seul montage :
  * `render_preview` fabrique un proxy léger (demi-résolution, x264 rapide) —
    c'est lui que l'éditeur lit dans le navigateur ;
  * `render_cut` + `burn_and_overlay` produisent l'export final en pleine
    résolution, sous-titres et émojis incrustés en UNE passe.

Le filtergraph d'un montage à beaucoup de coupes dépasse vite la limite de
longueur d'une ligne de commande Windows : au-delà d'un seuil on le passe par
fichier (`-filter_complex_script`).
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
import tempfile

from engine.edl import KeepSegment

# Au-delà, le filtergraph part dans un fichier plutôt que sur la ligne de commande.
_INLINE_GRAPH_LIMIT = 6000

_OUT_TIME_RE = re.compile(r"out_time_us=(\d+)")


def _run_ffmpeg(
    args: list[str],
    filtergraph: str | None = None,
    duration: float = 0.0,
    on_progress=None,
    cwd: str | None = None,
) -> None:
    """Lance ffmpeg, relaie la progression, lève une erreur lisible si échec.

    `args` contient tout sauf le filtergraph (inséré ici, inline ou par fichier).
    `on_progress` reçoit une fraction 0..1 si `duration` est connue.
    """
    graph_file = None
    cmd = ["ffmpeg", "-y", "-hide_banner", *args]
    if filtergraph:
        if len(filtergraph) <= _INLINE_GRAPH_LIMIT:
            cmd += ["-filter_complex", filtergraph]
        else:
            fd, graph_file = tempfile.mkstemp(suffix=".txt", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(filtergraph)
            cmd += ["-filter_complex_script", graph_file]
    cmd += ["-progress", "pipe:1", "-nostats"]

    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        tail: list[str] = []
        for line in proc.stdout or []:
            m = _OUT_TIME_RE.search(line)
            if m and on_progress and duration > 0:
                on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
            elif not line.startswith(("frame=", "fps=", "bitrate=", "total_size=",
                                      "out_time", "dup_frames=", "drop_frames=",
                                      "speed=", "progress=", "stream_")):
                tail.append(line.rstrip())
                del tail[:-25]
        code = proc.wait()
        if code != 0:
            raise RuntimeError("ffmpeg a échoué :\n" + "\n".join(tail[-12:]))
    finally:
        if graph_file:
            try:
                os.remove(graph_file)
            except OSError:
                pass


def output_size(width: int, height: int, vertical: bool) -> tuple[int, int]:
    """Dimensions de la vidéo produite (le repère des sous-titres)."""
    return (1080, 1920) if vertical else (int(width), int(height))


def _cut_graph(
    segs: list[KeepSegment], vertical: bool, src_w: int, src_h: int, scale: float = 1.0
) -> tuple[str, str, str]:
    """trim/concat des segments gardés, puis recadrage 9:16 optionnel.

    `src_w`/`src_h` décrivent la SOURCE ; la taille de sortie en découle
    (1080x1920 en vertical, la source sinon), multipliée par `scale` pour le
    proxy de prévisualisation.
    """
    parts: list[str] = []
    for i, s in enumerate(segs):
        parts.append(f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},"
                     f"setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={s.start:.3f}:end={s.end:.3f},"
                     f"asetpts=PTS-STARTPTS[a{i}]")
    n = len(segs)
    parts.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    ow, oh = output_size(src_w, src_h, vertical)
    ow, oh = _even(ow * scale), _even(oh * scale)

    if vertical:
        # Crop centré au ratio 9:16 puis mise à l'échelle.
        parts.append("[vc]crop=w='min(iw,ih*9/16)':h=ih:x='(iw-min(iw,ih*9/16))/2':y=0,"
                     f"scale={ow}:{oh}[vout]")
        return ";".join(parts), "[vout]", "[ac]"
    if scale != 1.0:
        parts.append(f"[vc]scale={ow}:{oh}[vout]")
        return ";".join(parts), "[vout]", "[ac]"
    return ";".join(parts), "[vc]", "[ac]"


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


_HW_SUFFIXES = ("_nvenc", "_qsv", "_amf", "_vaapi", "_videotoolbox", "_mf")

# Les encodeurs H.264 matériels plafonnent à 4096 px de côté : au-delà (8K au
# format d'origine), on passe en HEVC, qui monte à 8192 px.
_H264_HW_MAX = 4096


def hw_encoder(hevc: bool = False, platform: str | None = None) -> str:
    """Encodeur matériel du système : VideoToolbox sur Mac, NVENC ailleurs."""
    if (platform or sys.platform) == "darwin":
        return "hevc_videotoolbox" if hevc else "h264_videotoolbox"
    return "hevc_nvenc" if hevc else "h264_nvenc"


@functools.lru_cache(maxsize=None)
def _encoder_works(encoder: str, size: str = "320x240") -> bool:
    """Encode une image de test pour vérifier que l'encodeur s'ouvre vraiment.

    `ffmpeg -encoders` ne suffit pas : NVENC y figure même quand le pilote
    NVIDIA est trop ancien pour ce build de ffmpeg (« Driver does not support
    the required nvenc API version »), et l'échec n'arrive qu'à l'export.
    `size` permet de vérifier aussi qu'il accepte la définition de sortie.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"color=c=black:s={size}:d=0.1",
           "-frames:v", "1", "-pix_fmt", "yuv420p", "-c:v", encoder, "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _bitrate(width: int, height: int) -> str:
    """8 Mb/s pour du 1080x1920, proportionnel au nombre de pixels au-delà."""
    mbps = 8 * max(1.0, (width * height) / (1080 * 1920))
    return f"{min(80, round(mbps))}M"


def _video_codec_args(encoder: str, width: int = 0, height: int = 0) -> list[str]:
    """Options vidéo de l'export pour une sortie `width`x`height`.

    "auto" = NVENC si le GPU répond, sinon x264 (sans bruit : c'est le cas
    normal d'un PC sans carte NVIDIA). Un encodeur matériel demandé
    explicitement mais indisponible retombe aussi sur x264, en le signalant.
    La sortie est toujours en 8 bits (yuv420p) : les vidéos 10 bits des
    téléphones sont refusées telles quelles par NVENC H.264, et peu de lecteurs
    les acceptent.
    """
    requested = encoder
    big = max(width, height) > _H264_HW_MAX
    if encoder == "auto":
        encoder = hw_encoder()
    if big and encoder.startswith("h264_") and encoder.endswith(_HW_SUFFIXES):
        encoder = "hevc_" + encoder[len("h264_"):]
    rate = _bitrate(width or 1080, height or 1920)

    if encoder.endswith(_HW_SUFFIXES):
        size = f"{_even(width)}x{_even(height)}" if big else "320x240"
        if _encoder_works(encoder, size):
            args = ["-c:v", encoder, "-b:v", rate, "-pix_fmt", "yuv420p"]
            if encoder.startswith("hevc"):
                args += ["-tag:v", "hvc1"]   # lisible par QuickTime et iOS
            return args
        if requested != "auto":
            print(f"[render] {encoder} indisponible (pilote GPU trop ancien ou absent) : "
                  "export en libx264.")
        encoder = "libx264"
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    return ["-c:v", encoder, "-b:v", rate, "-pix_fmt", "yuv420p"]


def render_cut(
    input_path: str,
    segs: list[KeepSegment],
    out_path: str,
    vertical: bool = True,
    encoder: str = "auto",
    width: int = 1080,
    height: int = 1920,
    duration: float = 0.0,
    on_progress=None,
) -> None:
    """Export : coupe + recadrage en pleine résolution."""
    fc, vmap, amap = _cut_graph(segs, vertical, width, height)
    out_w, out_h = output_size(width, height, vertical)
    _run_ffmpeg(
        ["-i", os.path.abspath(input_path), "-map", vmap, "-map", amap,
         *_video_codec_args(encoder, out_w, out_h), "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", os.path.abspath(out_path)],
        filtergraph=fc, duration=duration, on_progress=on_progress,
    )


def render_preview(
    input_path: str,
    segs: list[KeepSegment],
    out_path: str,
    vertical: bool = True,
    width: int = 1080,
    height: int = 1920,
    max_height: int = 960,
    duration: float = 0.0,
    on_progress=None,
) -> None:
    """Proxy de l'éditeur : même cadrage, moitié de définition, x264 rapide.

    Volontairement sans sous-titres : ils sont dessinés en HTML par-dessus la
    vidéo, ce qui les rend déplaçables et corrigibles sans re-rendre quoi que
    ce soit. On force x264 (pas NVENC) : à cette taille le CPU suffit et on
    garde le GPU libre.
    """
    out_h = output_size(width, height, vertical)[1] or max_height
    scale = min(1.0, max_height / float(out_h))
    fc, vmap, amap = _cut_graph(segs, vertical, width, height, scale)
    _run_ffmpeg(
        ["-i", os.path.abspath(input_path), "-map", vmap, "-map", amap,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         os.path.abspath(out_path)],
        filtergraph=fc, duration=duration, on_progress=on_progress,
    )


def grab_thumbnail(video_path: str, out_path: str, at: float = 1.0,
                   width: int = 420) -> bool:
    """Vignette JPEG pour l'écran des projets. Échoue en silence : une
    vignette manquante ne doit pas faire capoter un montage."""
    try:
        _run_ffmpeg(["-ss", f"{max(0.0, at):.2f}", "-i", os.path.abspath(video_path),
                     "-frames:v", "1", "-vf", f"scale={_even(width)}:-2",
                     "-q:v", "4", os.path.abspath(out_path)])
        return os.path.isfile(out_path)
    except (RuntimeError, OSError):
        return False


def burn_and_overlay(
    input_path: str,
    ass_path: str,
    emojis: list[dict],
    out_path: str,
    encoder: str = "auto",
    duration: float = 0.0,
    on_progress=None,
    width: int = 0,
    height: int = 0,
) -> None:
    """Sous-titres + émojis couleur en une seule passe d'encodage.

    `width`/`height` : définition de la vidéo d'entrée (déjà coupée et
    recadrée), qui choisit l'encodeur et le débit.

    Le filtre `subtitles` avale mal les chemins Windows (`C:\\...`) : on lance
    ffmpeg avec le dossier du .ass comme répertoire courant et on ne passe que
    le nom de fichier.
    """
    from engine.pipeline.emoji_overlay import render_emoji_png

    work = os.path.dirname(os.path.abspath(ass_path)) or "."
    inputs = ["-i", os.path.abspath(input_path)]
    chain = [f"[0:v]subtitles={os.path.basename(ass_path)}[b0]"]
    prev = "b0"

    for i, e in enumerate(emojis, start=1):
        try:
            png = render_emoji_png(e["char"], int(e["size"]))
        except (RuntimeError, OSError, KeyError):
            continue  # police émoji absente : on rend la vidéo sans l'émoji
        inputs += ["-i", os.path.abspath(png)]
        chain.append(f"[{i}:v]scale={_even(e['size'])}:-2[e{i}]")
        chain.append(
            f"[{prev}][e{i}]overlay=x={int(e['x'])}-w/2:y={int(e['y'])}-h/2:"
            f"enable='between(t,{float(e['start']):.2f},{float(e['end']):.2f})'[b{i}]"
        )
        prev = f"b{i}"

    _run_ffmpeg(
        [*inputs, "-map", f"[{prev}]", "-map", "0:a?",
         *_video_codec_args(encoder, width, height), "-c:a", "copy",
         "-movflags", "+faststart", os.path.abspath(out_path)],
        filtergraph=";".join(chain), duration=duration, on_progress=on_progress,
        cwd=work,
    )
