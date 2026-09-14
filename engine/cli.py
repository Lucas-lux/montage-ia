"""Pipeline v0 en ligne de commande.

    python -m engine.cli "C:\\chemin\\mon_rush.mp4"

Tools : coupe des blancs, tics de langage, sous-titres (5 styles + émojis),
recadrage 9:16. Tous activables/désactivables.
"""
from __future__ import annotations

import argparse
import time

from engine.core import Options, default_output, run_pipeline
from engine.pipeline.captions import STYLES


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Montage IA - coupe blancs/tics + sous-titres + vertical"
    )
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"],
                    help="auto = GPU NVIDIA s'il répond, sinon processeur")
    ap.add_argument("--compute-type", default="auto",
                    help="auto = float16 sur GPU, int8 sur processeur")
    ap.add_argument("--language", default=None, help="ex: fr (auto-détecté si omis)")
    ap.add_argument("--max-gap", type=float, default=0.5,
                    help="silence max conservé entre 2 mots (s)")
    ap.add_argument("--pad", type=float, default=0.08)
    ap.add_argument("--no-vertical", action="store_true")
    ap.add_argument("--encoder", default="auto",
                    help="auto (h264_nvenc si dispo, sinon libx264), h264_nvenc ou libx264")
    ap.add_argument("--words-per-line", type=int, default=4)
    ap.add_argument("--max-chars", type=int, default=18,
                    help="caractères max par ligne de sous-titre")
    ap.add_argument("--style", default="hype", choices=list(STYLES),
                    help="style de sous-titres (classic, punch, hype, neon, clean)")
    # --- Tools de montage ---
    ap.add_argument("--no-silence", dest="cut_silence", action="store_false",
                    help="ne pas couper les blancs")
    ap.add_argument("--fillers", dest="cut_fillers", action="store_true",
                    help="supprimer les tics de langage (euh, du coup, en fait...)")
    ap.add_argument("--no-subtitles", dest="subtitles", action="store_false",
                    help="ne pas incruster de sous-titres")
    ap.add_argument("--no-emojis", dest="emojis", action="store_false",
                    help="désactiver les émojis sur les mots importants")
    ap.set_defaults(cut_silence=True, cut_fillers=False, subtitles=True, emojis=True)
    args = ap.parse_args()

    opts = Options(
        model=args.model, device=args.device, compute_type=args.compute_type,
        language=args.language, max_gap=args.max_gap, pad=args.pad,
        vertical=not args.no_vertical, encoder=args.encoder,
        words_per_line=args.words_per_line, max_chars=args.max_chars, style=args.style,
        emojis=args.emojis, cut_silence=args.cut_silence, cut_fillers=args.cut_fillers,
        subtitles=args.subtitles,
    )
    out = args.output or default_output(args.input)

    t0 = time.time()
    result = run_pipeline(
        args.input, out, opts,
        progress=lambda s, t, m: print(f"[{s}/{t}] {m}"),
    )
    print(f"\nOK - terminé en {time.time() - t0:.1f}s  ->  {result['output']}")


if __name__ == "__main__":
    main()
