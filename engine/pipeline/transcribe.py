"""Transcription locale avec faster-whisper (timestamps au mot).

Cette unique passe alimente sous-titres et coupe des blancs : on ne transcrit
qu'une seule fois.

Matériel : `device="auto"` prend le GPU NVIDIA s'il est utilisable, sinon le
processeur. Sur GPU, la transcription tourne dans un processus à part :
CTranslate2/CUDA peuvent planter en natif (DLL CUDA incompatibles, pilote trop
ancien, mémoire saturée) et, dans le processus du serveur, un tel plantage
fermerait toute l'application. Isolé, il devient une erreur ordinaire, et le
mode "auto" relance alors la transcription sur le processeur.
"""
from __future__ import annotations

import os

from engine.edl import Word


def _register_cuda_dll_dirs() -> None:
    """Windows : rend chargeables les DLL CUDA.

    Deux sources : les dossiers listés dans `MONTAGE_IA_DLL_DIRS` (posés par
    app.py pour l'application installée — un processus enfant n'hérite pas des
    `add_dll_directory` de son parent) et les paquets pip `nvidia-*`, que pip
    range dans `site-packages/nvidia/<lib>/bin`, hors du chemin de recherche.
    Sans effet hors Windows.
    """
    if os.name != "nt":
        return

    import importlib.util

    for d in os.environ.get("MONTAGE_IA_DLL_DIRS", "").split(os.pathsep):
        if d and os.path.isdir(d):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass

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

    # ctranslate2 résout aussi certaines dépendances via le PATH.
    path = os.environ.get("PATH", "")
    missing = [b for b in bindirs if b not in path]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing) + os.pathsep + path


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
    # Windows + CUDA : enregistrer les DLL nvidia AVANT de sonder le GPU.
    _register_cuda_dll_dirs()

    if resolve_device(device) == "cuda":
        try:
            return _isolated(path, model_size, "cuda", resolve_compute_type("cuda", compute_type),
                             language, info)
        except Exception as exc:  # noqa: BLE001 - toute panne GPU mérite un 2e essai
            if device != "auto":
                raise
            print(f"[transcribe] GPU inutilisable ({exc}) : transcription sur processeur.")
    return _run(path, model_size, "cpu", resolve_compute_type("cpu", compute_type),
                language, info)


def _isolated(path: str, model_size: str, device: str, compute_type: str,
              language: str | None, info: dict | None, target=None) -> list[Word]:
    """Exécute la transcription dans un processus enfant et en rapporte le résultat.

    Un plantage natif de l'enfant devient un RuntimeError ici. La mémoire du GPU
    est rendue au système dès la fin de la transcription. `target` ne sert
    qu'aux tests.
    """
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=target or _child, daemon=True,
                       args=(send, path, model_size, device, compute_type, language))
    proc.start()
    send.close()          # sinon recv() attendrait indéfiniment un enfant mort
    try:
        status, payload = recv.recv()
    except EOFError:      # l'enfant est mort sans répondre
        status, payload = "crash", None
    finally:
        recv.close()
        proc.join()

    if status == "ok":
        words, meta = payload
        if info is not None:
            info.update(meta)
        return [Word(text=t, start=s, end=e) for t, s, e in words]
    if status == "error":
        raise RuntimeError(payload)
    code = proc.exitcode
    shown = f"0x{code & 0xFFFFFFFF:08X}" if os.name == "nt" and code else str(code)
    raise RuntimeError(f"le moteur de transcription s'est arrêté brutalement (code {shown})")


def _child(conn, path, model_size, device, compute_type, language) -> None:
    """Corps du processus enfant : transcrit et renvoie des tuples picklables."""
    try:
        _register_cuda_dll_dirs()
        meta: dict = {}
        words = _run(path, model_size, device, compute_type, language, meta)
        conn.send(("ok", ([(w.text, w.start, w.end) for w in words], meta)))
    except Exception as exc:  # noqa: BLE001 - relayé au parent
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


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
