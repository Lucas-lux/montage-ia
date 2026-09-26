"""Télécharge les modèles de l'application, une fois pour toutes.

    python scripts/download_models.py              # traduction fr→en (~80 Mo) et détourage (26 Mo)
    python scripts/download_models.py --whisper    # + Whisper large-v3-turbo (~1,6 Go)
    python scripts/download_models.py --convert    # traduction reconvertie depuis l'original

* Traduction : version CTranslate2 d'Opus-MT (Helsinki-NLP, licence Apache-2.0)
  rangée dans models/translate/opus-mt-fr-en. Sans elle l'application marche,
  mais le bouton « Traduire en anglais » reste grisé.
* Détourage : MODNet (licence Apache-2.0), conversion ONNX `Xenova/modnet`,
  rangé dans models/matting/modnet.onnx. Sans lui, l'application le télécharge
  à la première suppression d'arrière-plan.
* Whisper : facultatif ici — faster-whisper le télécharge de toute façon à la
  première analyse. Le récupérer d'avance évite d'attendre à ce moment-là.

`--convert` refait la conversion depuis le modèle d'origine au lieu d'utiliser
une conversion publiée ; il faut alors  pip install transformers torch.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.pipeline.translate import available, model_dir  # noqa: E402

# Conversions CTranslate2 publiques des modèles Opus-MT (même licence que l'original).
PREBUILT = {("fr", "en"): "michaelfeil/ct2fast-opus-mt-fr-en"}
MODEL_FILES = ["model.bin", "config.json", "shared_vocabulary.*", "source.spm", "target.spm"]


def say(msg: str) -> None:
    print(f"[modèles] {msg}", flush=True)


def get_translation(source: str, target: str, convert: bool) -> None:
    dest = model_dir(source, target)
    if available(source, target):
        say(f"traduction {source}→{target} : déjà présente ({dest})")
        return
    os.makedirs(dest, exist_ok=True)

    if convert:
        converter = shutil.which("ct2-transformers-converter")
        if not converter:
            raise SystemExit("ct2-transformers-converter introuvable : pip install transformers torch")
        say(f"traduction {source}→{target} : conversion de Helsinki-NLP/opus-mt-{source}-{target}…")
        subprocess.run([converter, "--model", f"Helsinki-NLP/opus-mt-{source}-{target}",
                        "--output_dir", dest, "--quantization", "int8",
                        "--copy_files", "source.spm", "target.spm", "--force"], check=True)
    else:
        repo = PREBUILT.get((source, target))
        if not repo:
            raise SystemExit(f"Pas de conversion publiée connue pour {source}→{target} : "
                             "relance avec --convert.")
        from huggingface_hub import snapshot_download

        say(f"traduction {source}→{target} : téléchargement de {repo}…")
        snapshot_download(repo, local_dir=dest, allow_patterns=MODEL_FILES)
        shutil.rmtree(os.path.join(dest, ".cache"), ignore_errors=True)

    if not available(source, target):
        raise SystemExit(f"Modèle incomplet dans {dest} (model.bin, source.spm, target.spm attendus).")
    say(f"traduction {source}→{target} : prête ({dest})")


def get_whisper(name: str) -> None:
    from faster_whisper.utils import download_model

    say(f"Whisper {name} : téléchargement (cache HuggingFace)…")
    say(f"Whisper {name} : prêt ({download_model(name)})")


def get_llm() -> None:
    from engine.pipeline import llm

    say(f"IA de montage ({llm.REPO}, ~4 Go) : téléchargement (cache HuggingFace)…")
    say(f"IA de montage : prête ({llm.download()})")


def get_matting() -> None:
    """Modèle de détourage (MODNet, 26 Mo) dans models/matting/."""
    dest = os.path.join(ROOT, "models", "matting", "modnet.onnx")
    if os.path.isfile(dest):
        print(f"détourage : déjà là ({dest})")
        return
    from engine.pipeline import matting
    src = matting.model_path() or matting.download()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(src, dest)
    print(f"détourage : {os.path.getsize(dest) / 1e6:.0f} Mo -> {dest}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Télécharge les modèles de Montage IA")
    ap.add_argument("--whisper", nargs="?", const="large-v3-turbo", default=None,
                    metavar="MODELE", help="récupère aussi Whisper (défaut : large-v3-turbo)")
    ap.add_argument("--llm", action="store_true",
                    help="récupère aussi le modèle de langage du montage automatique (~4 Go)")
    ap.add_argument("--convert", action="store_true",
                    help="reconvertit la traduction depuis Helsinki-NLP (transformers + torch)")
    args = ap.parse_args()

    get_translation("fr", "en", args.convert)
    get_matting()
    if args.whisper:
        get_whisper(args.whisper)
    if args.llm:
        get_llm()


if __name__ == "__main__":
    main()
