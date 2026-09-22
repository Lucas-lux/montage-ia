r"""Point d'entrée de l'application de bureau.

Démarre le moteur local, ouvre le navigateur sur l'interface, et reste en vie
tant que la fenêtre reste ouverte. C'est ce fichier que PyInstaller transforme
en `MontageIA.exe` sous Windows et en `Montage IA.app` sous macOS (voir
`build/`). Sur Mac, pas de terminal : l'application vit dans le Dock et la
barre des menus (`macapp.py`), son journal va dans ~/Library/Logs.

Une fois installée, l'application est autonome : ffmpeg et les DLL CUDA
voyagent dans son dossier. On les branche AVANT le moindre import lourd —
ffmpeg doit être dans le PATH quand `render.py` l'appelle, et CUDA doit être
chargeable par ctranslate2 au moment où Whisper démarre.

Le modèle Whisper, lui, est cherché dans cet ordre : celui livré avec
l'application, puis le cache HuggingFace déjà présent sur la machine, sinon un
dossier utilisateur où il sera téléchargé une seule fois. Ça évite de trimballer
1,6 Go dans l'installeur quand le modèle est déjà là.

Les fichiers de travail (projets, aperçus, exports) vont dans
%LOCALAPPDATA%\MontageIA (Windows) ou ~/Library/Application Support/Montage IA
(macOS) : le dossier d'installation, lui, peut être en lecture seule.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

APP_NAME = "Montage IA"
DEFAULT_PORT = 8765
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))


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
    if FROZEN:
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dirs() -> list[str]:
    """Où chercher ffmpeg et les modèles livrés : à côté de l'exe et, dans une
    app macOS, dans Contents/Resources."""
    root = app_dir()
    dirs = [root]
    res = os.path.join(os.path.dirname(root), "Resources")
    if IS_MAC and FROZEN and os.path.isdir(res):
        dirs.append(res)
    return dirs


def user_dir() -> str:
    """Dossier des données utilisateur, toujours accessible en écriture."""
    if IS_MAC:
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "MontageIA")
    os.makedirs(path, exist_ok=True)
    return path


def log_file() -> str | None:
    """Journal de l'app macOS (lancée sans terminal) : ~/Library/Logs/Montage IA."""
    if not (IS_MAC and FROZEN):
        return None
    folder = os.path.join(os.path.expanduser("~"), "Library", "Logs", APP_NAME)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "montage-ia.log")
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    return path


def _to_log(path: str) -> None:
    f = open(path, "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = sys.stderr = f


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
    places = resource_dirs()

    # ffmpeg/ffprobe et les DLL CUDA livrés avec l'application.
    dirs = [os.path.join(p, sub) for p in places for sub in ("ffmpeg", "cuda")]
    dirs = [d for d in dirs if os.path.isdir(d)]
    for d in dirs:
        if os.name == "nt":
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
        # Relu par le processus de transcription, qui n'hérite pas des add_dll_directory.
        os.environ["MONTAGE_IA_DLL_DIRS"] = os.pathsep.join(dirs)

    # ffmpeg statique de l'app macOS : fontconfig doit savoir où sont les
    # polices du système, sinon les sous-titres sortent vides.
    if IS_MAC and dirs and not os.environ.get("FONTCONFIG_FILE"):
        from engine.pipeline.fonts import fontconfig_file
        conf = fontconfig_file(os.path.join(user_dir(), "fontconfig"))
        if conf:
            os.environ["FONTCONFIG_FILE"] = conf

    # Modèles de traduction des sous-titres (Opus-MT, quelques dizaines de Mo).
    for p in places:
        translate = os.path.join(p, "models", "translate")
        if os.path.isdir(translate):
            os.environ.setdefault("MONTAGE_IA_TRANSLATE", translate)
            break

    # Modèle Whisper : embarqué > cache existant de la machine > dossier utilisateur.
    if not os.environ.get("HF_HOME"):
        bundled = next((os.path.join(p, "models") for p in places if _has_model(os.path.join(p, "models"))),
                       os.path.join(root, "models"))
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


# ------------------------------------------------------- instance unique (Mac)
# Sur Mac, relancer l'app pendant qu'elle tourne doit rouvrir l'interface, pas
# démarrer un second moteur (le Dock le fait déjà ; ceci couvre le lancement
# direct du binaire et un moteur resté sans icône).

def _instance_file() -> str:
    return os.path.join(user_dir(), "instance.json")


def running_instance() -> str | None:
    """URL d'un moteur Montage IA déjà lancé par cet utilisateur, ou None."""
    try:
        with open(_instance_file(), encoding="utf-8") as f:
            url = json.load(f).get("url") or ""
    except (OSError, ValueError):
        return None
    if not url:
        return None
    try:
        with urllib.request.urlopen(url + "/api/projects", timeout=1.5) as r:
            return url if r.status == 200 else None
    except OSError:
        return None


def remember_instance(url: str) -> None:
    try:
        with open(_instance_file(), "w", encoding="utf-8") as f:
            json.dump({"url": url, "pid": os.getpid()}, f)
    except OSError:
        pass


def forget_instance() -> None:
    try:
        with open(_instance_file(), encoding="utf-8") as f:
            if json.load(f).get("pid") != os.getpid():
                return
        os.remove(_instance_file())
    except (OSError, ValueError):
        pass


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
    log = log_file()
    if log:
        _to_log(log)
    mac_app = IS_MAC and FROZEN and os.environ.get("MONTAGE_IA_NO_DOCK") != "1"
    if IS_MAC:
        other = running_instance()
        if other:
            print(f"Montage IA tourne déjà : {other}")
            webbrowser.open(other)
            return

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
    if IS_MAC:
        remember_instance(url)
    try:
        if mac_app:
            import macapp
            if macapp.available():
                macapp.run(server, url, os.environ["MONTAGE_IA_WORK"], log)
                return
            print("PyObjC absent : moteur lancé sans icône dans le Dock.")
        server.run()
    finally:
        if IS_MAC:
            forget_instance()


if __name__ == "__main__":
    # Exe PyInstaller : la transcription tourne dans un processus enfant, qui
    # relance ce même exe — freeze_support() l'aiguille vers son travail.
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - fenêtre lancée au double-clic
        print(f"\nERREUR : {exc}\n")
        import traceback
        traceback.print_exc()
        if FROZEN and IS_MAC:
            import macapp
            macapp.alert("Montage IA n'a pas pu démarrer",
                         f"{exc}\n\nDétails dans ~/Library/Logs/{APP_NAME}/montage-ia.log")
        elif FROZEN:
            try:
                input("\nAppuie sur Entrée pour fermer...")
            except EOFError:
                pass          # lancé sans console interactive
        sys.exit(1)
