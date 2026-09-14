"""Orchestration du pipeline, partagée par la CLI et le serveur web.

Le montage est composable : chaque tool activé (coupe des blancs, tics de
langage, coupe manuelle) produit des intervalles à couper ; on en déduit les
segments à garder, puis on rend (recadrage + sous-titres/émojis optionnels).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from engine.pipeline.captions import build_ass
from engine.pipeline.edit import filler_cuts, keep_from_cuts, remap_words, silence_cuts
from engine.pipeline.probe import probe
from engine.pipeline.render import burn_and_overlay, output_size, render_cut
from engine.pipeline.transcribe import transcribe

# progress(step, total, message)
ProgressFn = Callable[[int, int, str], None]


@dataclass
class Options:
    model: str = "large-v3-turbo"
    device: str = "auto"                 # GPU NVIDIA s'il répond, sinon processeur
    compute_type: str = "auto"           # float16 sur GPU, int8 sur processeur
    language: Optional[str] = None
    max_gap: float = 0.5
    pad: float = 0.08
    vertical: bool = True
    encoder: str = "auto"                # h264_nvenc s'il s'ouvre, sinon libx264
    words_per_line: int = 4
    max_chars: int = 18
    style: str = "hype"
    emojis: bool = True
    # Tools de montage (activables indépendamment)
    cut_silence: bool = True
    cut_fillers: bool = False
    subtitles: bool = True
    manual_cuts: list = field(default_factory=list)  # [(start, end), ...] timeline source
    keep_ranges: list = field(default_factory=list)  # [(start, end), ...] gardés malgré les coupes


def default_output(input_path: str) -> str:
    base, _ = os.path.splitext(os.path.abspath(input_path))
    return base + "_short.mp4"


def run_pipeline(
    input_path: str,
    output_path: str,
    opts: Options,
    progress: Optional[ProgressFn] = None,
) -> dict:
    def report(step: int, msg: str) -> None:
        if progress:
            progress(step, 5, msg)

    out = os.path.abspath(output_path)
    out_dir = os.path.dirname(out) or "."
    os.makedirs(out_dir, exist_ok=True)
    pid = os.getpid()
    cut_path = os.path.join(out_dir, f"_cut_{pid}.mp4")
    ass_path = os.path.join(out_dir, f"_caps_{pid}.ass")

    info = probe(input_path)
    report(1, f"Source : {info.duration:.1f}s {info.width}x{info.height} @ {info.fps:.0f}fps")

    report(2, "Transcription...")
    words = transcribe(input_path, opts.model, opts.device, opts.compute_type, opts.language)
    if not words:
        raise RuntimeError("Aucune parole détectée : rien à monter.")

    # Compose les coupes des tools activés.
    cuts: list[tuple[float, float]] = []
    if opts.cut_silence:
        cuts += silence_cuts(words, info.duration, opts.max_gap, opts.pad)
    if opts.cut_fillers:
        cuts += filler_cuts(words)
    if opts.manual_cuts:
        cuts += [(float(s), float(e)) for s, e in opts.manual_cuts]

    keep = keep_from_cuts(info.duration, cuts)
    if not keep:
        raise RuntimeError("Tous les segments ont été coupés : rien à garder.")
    new_words, kept = remap_words(words, keep)
    report(3, f"Montage : {len(keep)} segments, {kept:.1f}s (-{info.duration - kept:.1f}s)")

    report(4, "Rendu coupe + recadrage...")
    if opts.subtitles:
        render_cut(input_path, keep, cut_path, vertical=opts.vertical, encoder=opts.encoder,
                   width=info.width, height=info.height, duration=kept)
        overlays = build_ass(new_words, ass_path, words_per_line=opts.words_per_line,
                             max_chars=opts.max_chars, style=opts.style, emojis=opts.emojis)
        report(5, "Sous-titres...")
        # Émojis centrés au-dessus des sous-titres (l'éditeur web, lui, place
        # chacun où l'utilisateur l'a posé).
        ow, oh = output_size(info.width, info.height, opts.vertical)
        emojis = [{"char": e, "x": ow // 2, "y": int(oh * 0.72), "size": 150,
                   "start": s, "end": t} for e, s, t in overlays]
        burn_and_overlay(cut_path, ass_path, emojis, out, encoder=opts.encoder,
                         duration=kept)
    else:
        report(5, "Finalisation...")
        render_cut(input_path, keep, out, vertical=opts.vertical, encoder=opts.encoder,
                   width=info.width, height=info.height, duration=kept)

    for tmp in (cut_path, ass_path):
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    return {
        "output": out,
        "source_duration": round(info.duration, 1),
        "output_duration": round(kept, 1),
        "removed": round(info.duration - kept, 1),
        "segments": len(keep),
        "words": len(words),
    }
