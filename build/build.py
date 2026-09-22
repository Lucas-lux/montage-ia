r"""Fabrique MontageIA.exe puis son installeur (Windows), ou Montage IA.app et
son image disque sur un Mac (voir build_mac.py).

    python build\build.py                # application + installeur
    python build\build.py --app-only     # s'arrête après l'application
    python build\build.py --with-model   # embarque le modèle Whisper (~1,6 Go)
    python build\build.py --no-cuda      # sans accélération GPU (~1,9 Go de moins)

Trois étapes :

 1. PyInstaller empaquette Python, le moteur et l'interface  ->  dist/MontageIA/
 2. On copie à côté les gros binaires que PyInstaller n'a aucune raison
    d'analyser : ffmpeg/ffprobe et les DLL CUDA (et le modèle si demandé).
    `app.py` les rebranche au démarrage.
 3. Inno Setup produit MontageIA-Setup.exe, qui s'installe sans droits
    administrateur dans %LOCALAPPDATA%\Programs\MontageIA.

Le modèle Whisper n'est pas embarqué par défaut : l'application réutilise
celui déjà présent dans le cache HuggingFace de la machine, et ne le télécharge
(une seule fois) que s'il manque. Ça épargne 1,6 Go à l'installeur.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DIST = os.path.join(ROOT, "dist", "MontageIA")
SPEC = os.path.join(HERE, "montage_ia.spec")
# Modèle Whisper embarqué par --with-model (même nom que dans app.py).
WHISPER_MODEL = "faster-whisper-large-v3-turbo"

# Inno Setup s'installe soit pour la machine, soit — via winget — pour le seul
# utilisateur courant : on regarde les deux.
ISCC_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def say(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def human(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit in ("o", "Ko") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} Go"


def tree_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ----------------------------------------------------------------- ingrédients

def find_ffmpeg() -> list[str]:
    """ffmpeg.exe et ffprobe.exe, où qu'ils soient dans le PATH."""
    found = []
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if not path:
            raise SystemExit(
                f"{name} introuvable dans le PATH.\n"
                "Installe-le :  winget install Gyan.FFmpeg  (puis rouvre le terminal)")
        found.append(path)
    return found


def find_cuda_dlls() -> list[str]:
    """Les DLL CUDA des paquets pip `nvidia-*` de l'environnement courant."""
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    roots = list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []
    if not roots:
        # Python sans paquets nvidia-* : on reprend ceux du .venv du dépôt. Même
        # quand ce .venv vient d'une autre machine (interpréteur introuvable),
        # ses DLL restent valables.
        venv = os.path.join(ROOT, ".venv", "Lib", "site-packages", "nvidia")
        if os.path.isdir(venv):
            roots = [venv]
    dlls: list[str] = []
    for root in roots:
        for sub in sorted(os.listdir(root)):
            bindir = os.path.join(root, sub, "bin")
            if not os.path.isdir(bindir):
                continue
            dlls += [os.path.join(bindir, f) for f in os.listdir(bindir)
                     if f.lower().endswith(".dll")]
    return dlls


def _whisper_dirs(hub: str) -> list[str]:
    """Dossiers du modèle Whisper de l'application dans un cache HuggingFace."""
    if not os.path.isdir(hub):
        return []
    return [d for d in os.listdir(hub)
            if d.startswith("models--") and d.lower().endswith(WHISPER_MODEL)]


def check_cudnn(dlls: list[str]) -> None:
    """Refuse un build où cuDNN est dépareillé.

    ctranslate2 livre son propre cudnn64_9.dll ; les sous-bibliothèques
    (cudnn_ops, cudnn_cnn…) viennent de nvidia-cudnn-cu12. Si les versions
    diffèrent, la transcription GPU plante en natif (0xC0000409) sans le
    moindre message — c'est arrivé.
    """
    import importlib.util

    spec = importlib.util.find_spec("ctranslate2")
    if not spec or not spec.origin:
        return
    bundled = os.path.join(os.path.dirname(spec.origin), "cudnn64_9.dll")
    ours = next((d for d in dlls if os.path.basename(d).lower() == "cudnn64_9.dll"), None)
    if os.path.isfile(bundled) and ours and not filecmp.cmp(bundled, ours, shallow=False):
        raise SystemExit(
            "cuDNN dépareillé : le cudnn64_9.dll de ctranslate2 diffère de celui de "
            "nvidia-cudnn-cu12.\nAligne la version de nvidia-cudnn-cu12 dans "
            "requirements-gpu.txt sur celle livrée par ctranslate2.")
    say("cuDNN : versions cohérentes avec ctranslate2")


def find_model() -> str | None:
    """Dossier de cache HuggingFace contenant le modèle Whisper, s'il existe."""
    candidates = [os.environ.get("HF_HOME"),
                  os.path.join(os.path.expanduser("~"), ".cache", "huggingface")]
    for base in candidates:
        if base and _whisper_dirs(os.path.join(base, "hub")):
            return base
    return None


# --------------------------------------------------------------------- étapes

def run_pyinstaller(clean: bool) -> None:
    say("PyInstaller : empaquetage de l'application…")
    cmd = [sys.executable, "-m", "PyInstaller", SPEC,
           "--distpath", os.path.join(ROOT, "dist"),
           "--workpath", os.path.join(ROOT, "build", "_work"),
           "--noconfirm", "--log-level", "WARN"]
    if clean:
        cmd.append("--clean")
    subprocess.run(cmd, cwd=ROOT, check=True)
    if not os.path.isfile(os.path.join(DIST, "MontageIA.exe")):
        raise SystemExit("PyInstaller n'a pas produit MontageIA.exe.")
    say(f"application : {human(tree_size(DIST))}")


def copy_into(files: list[str], subdir: str, label: str) -> None:
    dest = os.path.join(DIST, subdir)
    os.makedirs(dest, exist_ok=True)
    total = 0
    for src in files:
        target = os.path.join(dest, os.path.basename(src))
        if os.path.isfile(target) and os.path.getsize(target) == os.path.getsize(src):
            total += os.path.getsize(target)
            continue                      # déjà copié : on ne recopie pas 2 Go
        shutil.copy2(src, target)
        total += os.path.getsize(target)
    say(f"{label} : {len(files)} fichiers, {human(total)}  -> {subdir}/")


def copy_model(hf_home: str) -> None:
    dest = os.path.join(DIST, "models")
    if os.path.isdir(os.path.join(dest, "hub")):
        say("modèle : déjà copié")
        return
    say("modèle Whisper : copie (~1,6 Go, patiente)…")
    # Seulement ce modèle : le cache peut contenir des dizaines d'autres modèles.
    hub = os.path.join(hf_home, "hub")
    for d in _whisper_dirs(hub):
        shutil.copytree(os.path.join(hub, d), os.path.join(dest, "hub", d),
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock", ".locks"))
    say(f"modèle : {human(tree_size(dest))}")


def copy_translate_models() -> None:
    """Modèles de traduction des sous-titres : petits, toujours embarqués."""
    src = os.path.join(ROOT, "models", "translate")
    if not os.path.isdir(src) or not os.listdir(src):
        say("traduction : aucun modèle dans models/translate — fonction désactivée")
        say("  Récupère-le :  python scripts/download_models.py")
        return
    dest = os.path.join(DIST, "models", "translate")
    shutil.copytree(src, dest, dirs_exist_ok=True)
    say(f"traduction : {human(tree_size(dest))}  -> models/translate/")


def run_inno(with_model: bool) -> str | None:
    iscc = next((p for p in ISCC_CANDIDATES if os.path.isfile(p)), None) or shutil.which("ISCC")
    if not iscc:
        say("Inno Setup introuvable — pas d'installeur.")
        say("  Installe-le :  winget install JRSoftware.InnoSetup")
        say(f"  L'application reste utilisable telle quelle : {DIST}\\MontageIA.exe")
        return None

    say("Inno Setup : fabrication de l'installeur…")
    out = os.path.join(ROOT, "dist")
    subprocess.run([iscc, f"/DAppSource={DIST}", f"/O{out}", os.path.join(HERE, "installer.iss")],
                   check=True)
    setup = os.path.join(out, "MontageIA-Setup.exe")
    return setup if os.path.isfile(setup) else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Fabrique l'application et son installeur")
    ap.add_argument("--app-only", action="store_true", help="pas d'installeur")
    ap.add_argument("--with-model", action="store_true",
                    help="embarque le modèle Whisper (installeur 100 %% hors-ligne)")
    ap.add_argument("--no-cuda", action="store_true", help="sans les DLL GPU")
    ap.add_argument("--clean", action="store_true", help="repart de zéro")
    args = ap.parse_args()

    if sys.platform == "darwin":
        sys.path.insert(0, HERE)
        import build_mac
        build_mac.main(args, sys.modules[__name__])
        return

    t0 = time.time()
    if args.clean and os.path.isdir(DIST):
        shutil.rmtree(DIST, ignore_errors=True)

    run_pyinstaller(args.clean)
    copy_into(find_ffmpeg(), "ffmpeg", "ffmpeg")
    copy_translate_models()

    if args.no_cuda:
        say("CUDA : ignoré (l'application tournera sur processeur)")
    else:
        dlls = find_cuda_dlls()
        if dlls:
            check_cudnn(dlls)
            copy_into(dlls, "cuda", "DLL CUDA")
        else:
            say("CUDA : paquets nvidia-* absents de l'environnement, ignoré")

    if args.with_model:
        hf = find_model()
        if hf:
            copy_model(hf)
        else:
            say("modèle : aucun cache Whisper trouvé, il sera téléchargé au 1er lancement")

    say(f"dossier final : {human(tree_size(DIST))}  -> {DIST}")

    setup = None if args.app_only else run_inno(args.with_model)
    say(f"terminé en {time.time() - t0:.0f} s")
    print()
    print(f"  Application : {os.path.join(DIST, 'MontageIA.exe')}")
    if setup:
        print(f"  Installeur  : {setup}  ({human(os.path.getsize(setup))})")


if __name__ == "__main__":
    main()
