r"""Point d'entrée de l'application de bureau.

Démarre le moteur local, ouvre le navigateur sur l'interface, et reste en vie
tant que la fenêtre reste ouverte. C'est ce fichier que PyInstaller transforme
en `MontageIA.exe` (voir `build/`).

Une fois installée, l'application est autonome : ffmpeg et les DLL CUDA
voyagent dans son dossier. On les branche AVANT le moindre import lourd —
ffmpeg doit être dans le PATH quand `render.py` l'appelle, et CUDA doit être
chargeable par ctranslate2 au moment où Whisper démarre.

Le modèle Whisper, lui, est cherché dans cet ordre : celui livré avec
l'application, puis le cache HuggingFace déjà présent sur la machine, sinon un
dossier utilisateur où il sera téléchargé une seule fois. Ça évite de trimballer
1,6 Go dans l'installeur quand le modèle est déjà là.

Les fichiers de travail (projets, aperçus, exports) vont dans
%LOCALAPPDATA%\MontageIA : le dossier d'installation, lui, peut être en
lecture seule.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

APP_NAME = "Montage IA"
DEFAULT_PORT = 8765


def _utf8_console() -> None:
    """Passe la console en UTF-8 avant le moindre affichage.

    Une console Windows démarre en cp1252 : le premier accent — ou le cadre du
    bandeau — suffirait à faire planter l'application au lancement. On force la
    page de code ET l'encodage des flux, avec `errors="replace"` en filet.
    """
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001 - lancé sans console : sans importance
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_console()


def app_dir() -> str:
    """Dossier de l'application : à côté de l'exe, ou la racine du dépôt."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def user_dir() -> str:
    """Dossier des données utilisateur, toujours accessible en écriture."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "MontageIA")
    os.makedirs(path, exist_ok=True)
    return path


# Modèle Whisper de l'application (Options.model = "large-v3-turbo").
WHISPER_MODEL = "faster-whisper-large-v3-turbo"


def _has_model(hf_home: str) -> bool:
    """Vrai si ce cache contient LE modèle Whisper de l'application.

    Un autre modèle Whisper en cache (small, base…) ne compte pas : il couperait
    l'accès réseau alors que le bon modèle reste à télécharger.
    """
    hub = os.path.join(hf_home, "hub")
    if not os.path.isdir(hub):
        return False
    return any(d.startswith("models--") and d.lower().endswith(WHISPER_MODEL)
               for d in os.listdir(hub))


def wire_runtime() -> None:
    """Rend trouvables les binaires embarqués. À appeler avant tout import."""
    root = app_dir()

    # ffmpeg/ffprobe et les DLL CUDA livrés avec l'application.
    dirs = [os.path.join(root, sub) for sub in ("ffmpeg", "cuda")]
    dirs = [d for d in dirs if os.path.isdir(d)]
    for d in dirs:
        if os.name == "nt":
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")

    # Modèles de traduction des sous-titres (Opus-MT, quelques dizaines de Mo).
    translate = os.path.join(root, "models", "translate")
    if os.path.isdir(translate):
        os.environ.setdefault("MONTAGE_IA_TRANSLATE", translate)

    # Modèle Whisper : embarqué > cache existant de la machine > dossier utilisateur.
    if not os.environ.get("HF_HOME"):
        bundled = os.path.join(root, "models")
        cached = os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
        if _has_model(bundled):
            os.environ["HF_HOME"] = bundled
        elif _has_model(cached):
            os.environ["HF_HOME"] = cached
        else:
            os.environ["HF_HOME"] = os.path.join(user_dir(), "models")
    # Modèle déjà là = aucune raison de contacter le réseau.
    if _has_model(os.environ["HF_HOME"]):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    os.environ.setdefault("MONTAGE_IA_WORK", os.path.join(user_dir(), "work"))


def free_port(preferred: int) -> int:
    """Premier port libre à partir de `preferred` (une 2e instance coexiste)."""
    for port in range(preferred, preferred + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Aucun port libre entre {preferred} et {preferred + 19}.")


def _open_browser(url: str, server) -> None:
    """Ouvre l'interface dès que le serveur répond."""
    for _ in range(200):
        if getattr(server, "started", False):
            webbrowser.open(url)
            return
        time.sleep(0.05)


def banner(url: str, work: str) -> str:
    line = "─" * 58
    return (
        f"\n{line}\n"
        f"  {APP_NAME}\n"
        f"  Interface   : {url}\n"
        f"  Projets     : {work}\n"
        f"  Modèle      : {os.environ.get('HF_HOME', '?')}\n"
        f"\n  Laisse cette fenêtre ouverte. Ferme-la pour quitter.\n"
        f"{line}\n"
    )


def main() -> None:
    wire_runtime()

    # Imports APRÈS wire_runtime : le PATH et les variables doivent être posés.
    import uvicorn
    from engine.server import app

    port = free_port(int(os.environ.get("MONTAGE_IA_PORT", DEFAULT_PORT)))
    url = f"http://127.0.0.1:{port}"

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=_open_browser, args=(url, server), daemon=True).start()

    print(banner(url, os.environ["MONTAGE_IA_WORK"]))
    server.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - fenêtre lancée au double-clic
        print(f"\nERREUR : {exc}\n")
        import traceback
        traceback.print_exc()
        if getattr(sys, "frozen", False):
            try:
                input("\nAppuie sur Entrée pour fermer...")
            except EOFError:
                pass          # lancé sans console interactive
        sys.exit(1)
