r"""Montage IA sous Windows : une vraie fenêtre d'application, sans navigateur.

L'interface reste la même page web, affichée dans une fenêtre native par le
moteur Edge du système (WebView2, livré avec Windows 10 et 11) :

  - « Importer » ouvre les boîtes de dialogue de Windows, et les rushs sont
    lus là où ils sont : ni copie ni envoi, l'import est immédiat ;
  - glisser des fichiers ou un dossier depuis l'Explorateur donne leurs vrais
    chemins, pour la même raison ;
  - fermer la fenêtre arrête le moteur (confirmation demandée si un export ou
    une analyse tourne) ; relancer l'application ramène la fenêtre devant.

La fenêtre s'ouvre tout de suite sur un écran d'attente pendant que le moteur
charge ses bibliothèques, puis bascule sur l'interface. Sans WebView2 (ou avec
`MONTAGE_IA_BROWSER=1`), `app.py` garde l'ancienne façon : le navigateur.

Pour le débogage : `MONTAGE_IA_DEVTOOLS=1` (outils de développement, menus du
clic droit) et `MONTAGE_IA_DEBUG_PORT=9222` (Playwright s'y branche par CDP).
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time

APP_NAME = "Montage IA"
BACKGROUND = "#ECECEE"          # fond de l'accueil : pas d'éclair blanc ou noir à l'ouverture

SPLASH = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#ECECEE;color:#17181C;font:14px system-ui,"Segoe UI",sans-serif}
body{display:grid;place-items:center;user-select:none;cursor:default}
.box{display:grid;justify-items:center;gap:14px}
.mark{width:44px;height:44px;border-radius:11px;background:#F23A52;display:grid;place-items:center}
b{font-size:17px;font-weight:650}
.meta{color:#6B6F76;font-size:12.5px}
.bar{width:180px;height:3px;border-radius:2px;background:#D9DADF;overflow:hidden}
.bar i{display:block;width:40%;height:100%;background:#F23A52;animation:go 1.1s ease-in-out infinite}
@keyframes go{from{transform:translateX(-100%)}to{transform:translateX(250%)}}
@media (prefers-reduced-motion:reduce){.bar i{animation:none;width:100%}}
</style></head><body><div class="box">
<div class="mark"><svg viewBox="0 0 20 20" width="22" height="22" fill="#fff"><path d="M6 4v12l10-6z"/></svg></div>
<b>Montage IA</b><div class="bar"><i></i></div><div class="meta">Démarrage du moteur…</div>
</div></body></html>"""

ERROR_PAGE = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%%;background:#ECECEE;color:#17181C;font:14px system-ui,"Segoe UI",sans-serif}
body{display:grid;place-items:center}.box{max-width:560px;padding:24px}
b{font-size:17px}pre{white-space:pre-wrap;background:#fff;border-radius:8px;padding:12px;font-size:12px}
.meta{color:#6B6F76;font-size:12.5px}
</style></head><body><div class="box"><b>Montage IA n'a pas pu démarrer</b><pre>%s</pre>
<div class="meta">%s</div></div></body></html>"""

MB_OK, MB_OKCANCEL, MB_ICONWARNING, MB_ICONERROR = 0x0, 0x1, 0x30, 0x10
IDOK = 1


def message(text: str, title: str = APP_NAME, flags: int = MB_OK, owner: int = 0) -> int:
    """Boîte de message Windows (utilisable sans fenêtre ni console)."""
    try:
        return ctypes.windll.user32.MessageBoxW(owner, text, title, flags | (0 if owner else 0x40000))
    except (AttributeError, OSError):
        return IDOK


def webview2_version() -> str:
    """Version du moteur WebView2 installé ("" s'il manque)."""
    import winreg
    key = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    places = [(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + key),
              (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + key),
              (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + key)]
    for root, path in places:
        try:
            with winreg.OpenKey(root, path) as k:
                version = str(winreg.QueryValueEx(k, "pv")[0])
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return version
    return ""


def unavailable() -> str:
    """Pourquoi la fenêtre native ne peut pas s'ouvrir ("" si elle le peut)."""
    if sys.platform != "win32":
        return "fenêtre native réservée à Windows"
    try:
        import webview  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - pywebview ou pythonnet absent ou cassé
        return f"pywebview indisponible ({exc})"
    if not webview2_version():
        return "moteur WebView2 absent (https://go.microsoft.com/fwlink/p/?LinkId=2124703)"
    return ""


# ---------------------------------------------------------- pont avec la page

class Bridge:
    """Fonctions appelées par la page : `window.pywebview.api.<nom>(…)`.

    Les boîtes de dialogue renvoient de vrais chemins : le moteur lit les
    fichiers sur place au lieu de recevoir une copie par le réseau local."""

    def __init__(self) -> None:
        self._window = None                 # privé : pywebview n'expose pas les `_…`

    def pick_files(self, kind: str = "media", multiple: bool = True) -> list[str]:
        """Choix de fichiers. `kind` : "media" (vidéo, son, image), "video" ou
        "sound" (un son, ou la bande son d'une vidéo)."""
        import webview
        from engine.timeline.media import AUDIO_EXT, MEDIA_EXT, VIDEO_EXT
        exts = sorted({"video": VIDEO_EXT, "sound": AUDIO_EXT | VIDEO_EXT}.get(kind, MEDIA_EXT))
        label = {"video": "Vidéos", "sound": "Sons"}.get(kind, "Médias")
        types = (f"{label} ({';'.join('*' + e for e in exts)})", "Tous les fichiers (*.*)")
        got = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=bool(multiple),
                                              file_types=types)
        return [str(p) for p in got or []]

    def pick_folder(self) -> str:
        """Choix d'un dossier ("" si annulé)."""
        import webview
        got = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(got[0]) if got else ""


def _watch_drops(window) -> None:
    """Fichiers glissés depuis l'Explorateur : leurs chemins complets (fournis
    par WebView2) sont renvoyés à la page dans l'évènement `montage:paths`."""
    from webview.dom import DOMEventHandler

    def on_drop(event: dict) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.run_js("window.dispatchEvent(new CustomEvent('montage:paths', { detail: %s }))"
                          % json.dumps(paths))

    def on_loaded() -> None:
        try:
            window.dom.document.events.drop += DOMEventHandler(on_drop, False, False)
        except Exception as exc:  # noqa: BLE001 - page quittée entre-temps
            print(f"[fenêtre] glisser-déposer indisponible sur cette page : {exc}")

    window.events.loaded += on_loaded


def busy_reason() -> str:
    """Tâche longue en cours, qu'une fermeture interromprait ("" sinon)."""
    try:
        from engine import server
        from engine.timeline import api as timeline
        if any(p.is_busy for p in list(timeline.TIMELINES.values())):
            return "Un export est en cours."
        if any(p.is_busy for p in list(server.PROJECTS.values())):
            return "Une analyse ou un export est en cours."
    except Exception:  # noqa: BLE001 - moteur pas encore chargé
        pass
    return ""


def _hwnd(window) -> int:
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:  # noqa: BLE001 - fenêtre pas encore créée
        return 0


def bring_to_front(window) -> None:
    """Ramène la fenêtre devant (deuxième lancement de l'application)."""
    try:
        window.restore()
        window.show()
        window.on_top = True           # Windows refuse le premier plan aux
        window.on_top = False          # autres processus : on passe par « toujours devant »
    except Exception as exc:  # noqa: BLE001
        print(f"[fenêtre] impossible de la ramener devant : {exc}")


# ------------------------------------------------------------------ lancement

def run(start_engine, user_dir: str, log: str | None, on_ready=None) -> None:
    """Ouvre la fenêtre, démarre le moteur, et rend la main à sa fermeture.

    `start_engine()` charge le moteur et renvoie `(serveur uvicorn, url)` ;
    `on_ready(url)` est appelé quand l'interface répond."""
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True          # « Télécharger » : boîte « Enregistrer sous »
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    port = os.environ.get("MONTAGE_IA_DEBUG_PORT")
    if port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(port)
    debug = os.environ.get("MONTAGE_IA_DEVTOOLS") == "1"
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False

    bridge = Bridge()
    window = webview.create_window(
        APP_NAME, html=SPLASH, js_api=bridge, width=1440, height=900, min_size=(1100, 680),
        maximized=True, background_color=BACKGROUND, text_select=True)
    bridge._window = window
    _watch_drops(window)
    engine: dict = {}

    def on_closing():
        reason = busy_reason()
        if not reason:
            return True
        answer = message(f"{reason}\n\nQuitter maintenant l'interrompra.", "Quitter Montage IA ?",
                         MB_OKCANCEL | MB_ICONWARNING, _hwnd(window))
        return answer == IDOK

    window.events.closing += on_closing

    def boot() -> None:
        try:
            server, url = start_engine()
            _add_routes(server.config.app, window)
            thread = threading.Thread(target=server.run, name="moteur", daemon=True)
            engine.update(server=server, thread=thread)
            thread.start()
            while not getattr(server, "started", False):
                if not thread.is_alive():
                    raise RuntimeError("le moteur s'est arrêté au démarrage (voir le journal)")
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001 - affiché dans la fenêtre
            import html
            import traceback
            traceback.print_exc()
            where = f"Détails dans le journal : {log}" if log else "Détails dans la console."
            window.events.loaded.wait(60)
            window.load_html(ERROR_PAGE % (html.escape(str(exc)), html.escape(where)))
            return
        if on_ready:
            on_ready(url)
        # WebView2 doit avoir fini de s'initialiser (l'écran d'attente est
        # affiché) : une adresse donnée avant se perd, la fenêtre resterait
        # sur l'écran d'attente.
        window.events.loaded.wait(60)
        window.load_url(url)

    webview.start(boot, gui="edgechromium", debug=debug, private_mode=False,
                  storage_path=os.path.join(user_dir, "webview"))

    server = engine.get("server")
    if server is not None:
        server.should_exit = True
        engine["thread"].join(timeout=5)


def _add_routes(app, window) -> None:
    """Routes propres à l'application de bureau (à côté de celles du moteur)."""

    def app_info() -> dict:
        return {"native": True, "webview2": webview2_version()}

    def app_focus() -> dict:
        threading.Thread(target=bring_to_front, args=(window,), daemon=True).start()
        return {"ok": True}

    app.add_api_route("/api/app", app_info, methods=["GET"])
    app.add_api_route("/api/app/focus", app_focus, methods=["POST"])


# ------------------------------------------------ processus enfants (Windows)

def tie_child_processes(no_window: bool) -> None:
    """ffmpeg et ffprobe meurent avec l'application.

    Ils sont rangés dans un « job » Windows qui les tue quand l'application se
    ferme, même brutalement : sans console, fermer la fenêtre ne les arrêterait
    pas, et un export continuerait seul. Seulement eux : un navigateur ouvert
    depuis l'application doit lui survivre. Sans console (`no_window`), tout
    processus lancé par `subprocess` démarre aussi sans fenêtre noire."""
    import subprocess
    if sys.platform != "win32" or getattr(subprocess.Popen, "_montage", False):
        return
    job = _kill_on_close_job()
    base = subprocess.Popen

    class Popen(base):
        _montage = True

        def __init__(self, args, *rest, **kwargs):
            if no_window:
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(args, *rest, **kwargs)
            prog = args if isinstance(args, (str, bytes, os.PathLike)) else (args[0] if args else "")
            name = os.path.basename(os.fsdecode(prog)).lower()
            if job and name.startswith(("ffmpeg", "ffprobe")):
                ctypes.windll.kernel32.AssignProcessToJobObject(job, int(self._handle))

    subprocess.Popen = Popen


def _kill_on_close_job() -> int:
    """Job Windows dont les processus meurent quand son dernier handle se
    ferme, c'est-à-dire à la fin de l'application (0 si impossible)."""
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return 0
    info = EXTENDED()
    info.Basic.LimitFlags = 0x2000                   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return 0
    return job                                       # jamais fermé : il vit avec l'application
