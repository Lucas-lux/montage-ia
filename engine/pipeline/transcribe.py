"""Transcription locale avec faster-whisper (timestamps au mot).

Cette unique passe alimente sous-titres et coupe des blancs : on ne transcrit
qu'une seule fois.

Matériel : `device="auto"` prend le GPU NVIDIA s'il est utilisable, sinon le
processeur. Si le GPU échoue en route (DLL CUDA absentes, pilote trop ancien,
mémoire saturée), la transcription est relancée sur le processeur au lieu
d'abandonner — l'application doit marcher sur n'importe quel PC.
"""
from __future__ import annotations

import os

from engine.edl import Word


def _register_cuda_dll_dirs() -> None:
    """Windows : rend chargeables les DLL CUDA des paquets pip `nvidia-*`.

    ctranslate2 cherche `cublas64_12.dll` (qui dépend lui-même de
    `cudart64_12.dll`) et `cudnn64_9.dll`, mais pip les range dans
    `site-packages/nvidia/<lib>/bin`, hors du chemin de recherche par défaut.
    On enregistre ces dossiers via add_dll_directory ET via le PATH du process
    (selon comment le loader résout les dépendances). Sans effet hors Windows.
    """
    if os.name != "nt":
        return

    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    roots = list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []

    bindirs: list[str] = []
    for root in roots:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            bindir = os.path.join(root, sub, "bin")
            if os.path.isdir(bindir):
                bindirs.append(bindir)

    for bindir in bindirs:
        try:
            os.add_dll_directory(bindir)
        except OSError:
            pass

    if bindirs:
        os.environ["PATH"] = os.pathsep.join(bindirs) + os.pathsep + os.environ.get("PATH", "")


def resolve_device(device: str = "auto") -> str:
    """"auto" -> "cuda" si ctranslate2 voit un GPU NVIDIA, sinon "cpu"."""
    if device != "auto":
        return device
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001 - pas de CUDA du tout
        return "cpu"


def resolve_compute_type(device: str, compute_type: str = "auto") -> str:
    """float16 sur GPU, int8 sur processeur (qui refuse le float16)."""
    if compute_type == "auto" or (device == "cpu" and compute_type == "float16"):
        return "float16" if device == "cuda" else "int8"
    return compute_type


def transcribe(
    path: str,
    model_size: str = "large-v3-turbo",
    device: str = "auto",
    compute_type: str = "auto",
    language: str | None = None,
    info: dict | None = None,
) -> list[Word]:
    """Mots horodatés de `path`.

    `info`, si fourni, reçoit la langue détectée (`language`) et le matériel
    réellement utilisé (`device`). Seul le mode "auto" se replie sur le
    processeur : un `device="cuda"` explicite laisse remonter l'erreur.
    """
    # Windows + CUDA : enregistrer les DLL nvidia AVANT de charger le modèle.
    _register_cuda_dll_dirs()

    if resolve_device(device) == "cuda":
        try:
            return _run(path, model_size, "cuda", resolve_compute_type("cuda", compute_type),
                        language, info)
        except Exception as exc:  # noqa: BLE001 - toute panne GPU mérite un 2e essai
            if device != "auto":
                raise
            print(f"[transcribe] GPU inutilisable ({exc}) : transcription sur processeur.")
    return _run(path, model_size, "cpu", resolve_compute_type("cpu", compute_type),
                language, info)


def _run(path: str, model_size: str, device: str, compute_type: str,
         language: str | None, info: dict | None) -> list[Word]:
    # Import paresseux : le modèle ne se charge que si on transcrit.
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, meta = model.transcribe(
        path,
        word_timestamps=True,
        vad_filter=True,  # coupe déjà le gros des silences côté détection
        language=language,
    )

    words: list[Word] = []
    for seg in segments:  # générateur paresseux : itérer lance le calcul
        for w in seg.words or []:
            if w.start is None or w.end is None:
                continue
            words.append(Word(text=w.word, start=float(w.start), end=float(w.end)))

    if info is not None:
        info["language"] = getattr(meta, "language", None)
        info["device"] = device
    return words
