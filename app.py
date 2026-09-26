r"""Point d'entrée de l'application de bureau.

Démarre le moteur local et affiche l'interface. C'est ce fichier que
PyInstaller transforme en `MontageIA.exe` sous Windows et en `Montage IA.app`
sous macOS (voir `build/`).

  - Windows : une vraie fenêtre d'application (`desktop.py`, moteur Edge
    WebView2), sans console ; son journal va dans %LOCALAPPDATA%\MontageIA\logs.
    `MONTAGE_IA_BROWSER=1` (ou `--browser`) garde l'ancienne façon : le
    navigateur, avec la console comme journal et bouton « quitter ».
  - macOS : l'interface s'ouvre dans le navigateur ; l'application vit dans le
    Dock et la barre des menus (`macapp.py`), son journal va dans ~/Library/Logs.

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


# Exe fenêtré (Windows) : pas de console, donc ni stdout ni stderr. Le premier
# print, trace d'erreur ou barre de progression (téléchargement d'un modèle)
# planterait : ils partent dans le vide, puis dans le journal (`main`). Vaut
# aussi pour le processus enfant de la transcription, qui relit ce fichier.
STREAMLESS = sys.stdout is None or sys.stderr is None
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))


def _has_console() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except (AttributeError, OSError):
        return True


NO_CONSOLE = not _has_console()


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
    r"""Journal de l'application lancée sans terminal : app macOS
    (~/Library/Logs/Montage IA) ou exe Windows fenêtré (%LOCALAPPDATA%\MontageIA\logs)."""
    if not FROZEN or not (IS_MAC or STREAMLESS):
        return None
    folder = (os.path.join(os.path.expanduser("~"), "Library", "Logs", APP_NAME) if IS_MAC
              else os.path.join(user_dir(), "logs"))
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

    # Modèles de traduction des sous-titres (Opus-MT, quelques dizaines de Mo)
    # et de détourage (MODNet, 26 Mo).
    for p in places:
        translate = os.path.join(p, "models", "translate")
        if os.path.isdir(translate):
            os.environ.setdefault("MONTAGE_IA_TRANSLATE", translate)
            break
    for p in places:
        matting = os.path.join(p, "models", "matting")
        if os.path.isfile(os.path.join(matting, "modnet.onnx")):
            os.environ.setdefault("MONTAGE_IA_MATTING", matting)
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

    os.environ.setdefault("MONTAGE_IA_WORK", work_dir())


def work_dir() -> str:
    """Dossier des projets (`MONTAGE_IA_WORK` pour les essais, sinon celui de l'utilisateur)."""
    return os.environ.get("MONTAGE_IA_WORK") or os.path.join(user_dir(), "work")


def native_window() -> bool:
    """Windows : fenêtre d'application plutôt que navigateur (sauf demande contraire)."""
    return os.name == "nt" and os.environ.get("MONTAGE_IA_BROWSER") != "1" and "--browser" not in sys.argv


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


# ------------------------------------------------------------ instance unique
# Relancer l'application pendant qu'elle tourne doit ramener sa fenêtre
# (Windows) ou rouvrir l'interface (Mac), pas démarrer un second moteur sur les
# mêmes projets. Une instance d'essai (autre dossier de projets) est à part.

def _instance_file() -> str:
    return os.path.join(user_dir(), "instance.json")


def running_instance() -> str | None:
    """URL d'un moteur Montage IA déjà lancé par cet utilisateur, ou None."""
    try:
        with open(_instance_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    url = data.get("url") or ""
    if not url or os.path.normcase(data.get("work") or work_dir()) != os.path.normcase(work_dir()):
        return None
    try:
        with urllib.request.urlopen(url + "/api/projects", timeout=1.5) as r:
            return url if r.status == 200 else None
    except OSError:
        return None


def remember_instance(url: str) -> None:
    try:
        with open(_instance_file(), "w", encoding="utf-8") as f:
            json.dump({"url": url, "pid": os.getpid(), "work": work_dir()}, f)
    except OSError:
        pass


def focus_instance(url: str) -> bool:
    """Ramène devant la fenêtre d'une instance déjà lancée (Windows)."""
    try:
        req = urllib.request.Request(url + "/api/app/focus", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


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


def start_engine():
    """Charge le moteur (imports lourds) et le prépare sur un port libre.
    Renvoie `(serveur uvicorn, url)` ; le serveur n'est pas encore lancé."""
    import uvicorn
    from engine.server import app

    port = free_port(int(os.environ.get("MONTAGE_IA_PORT", DEFAULT_PORT)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    return server, f"http://127.0.0.1:{port}"


def main() -> None:
    log = log_file()
    if log:
        _to_log(log)
    native = native_window()
    mac_app = IS_MAC and FROZEN and os.environ.get("MONTAGE_IA_NO_DOCK") != "1"
    if IS_MAC or native:
        other = running_instance()
        if other:
            print(f"Montage IA tourne déjà : {other}")
            if not (native and focus_instance(other)):
                webbrowser.open(other)
            return

    wire_runtime()
    if os.name == "nt":
        import desktop
        desktop.tie_child_processes(no_window=NO_CONSOLE)

    if native:
        import desktop
        problem = desktop.unavailable()
        if not problem:
            def ready(url: str) -> None:
                remember_instance(url)
                print(f"{APP_NAME} : moteur sur {url}, projets dans {os.environ['MONTAGE_IA_WORK']}")
            try:
                desktop.run(start_engine, user_dir(), log, on_ready=ready)
            finally:
                forget_instance()
            return
        print(f"Fenêtre de l'application indisponible : {problem}. Interface dans le navigateur.")

    # Imports APRÈS wire_runtime : le PATH et les variables doivent être posés.
    server, url = start_engine()
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
        if NO_CONSOLE:
            # Windows sans WebView2 ni console : une boîte sert de bouton « Quitter ».
            import desktop
            engine = threading.Thread(target=server.run, name="moteur", daemon=True)
            engine.start()
            desktop.message(f"Montage IA est ouvert dans ton navigateur :\n{url}\n\n"
                            "Garde cette fenêtre : clique sur OK pour quitter Montage IA.")
            server.should_exit = True
            engine.join(timeout=5)
            return
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
        elif FROZEN and NO_CONSOLE:
            import desktop
            where = log_file()
            desktop.message(f"{exc}" + (f"\n\nDétails dans {where}" if where else ""),
                            "Montage IA n'a pas pu démarrer", desktop.MB_ICONERROR)
        elif FROZEN:
            try:
                input("\nAppuie sur Entrée pour fermer...")
            except EOFError:
                pass          # lancé sans console interactive
        sys.exit(1)
