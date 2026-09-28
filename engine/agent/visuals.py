"""Visuels dessinés par l'agent : HTML/CSS ou SVG rendus en PNG (transparent).

L'agent écrit une carte, un bandeau, une liste, un graphique, une citation…
en HTML/CSS ; un navigateur sans fenêtre (Edge, présent sur tout Windows, ou
Chrome/Chromium) en fait une image à la taille du cadre, fond transparent. Les
polices livrées avec Montage IA y sont déclarées sous leur nom (« Anton »,
« Montserrat Black »…) : le visuel a la même typographie que les sous-titres.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

_LOCK = threading.Lock()


class VisualError(RuntimeError):
    pass


def find_browser() -> str | None:
    """Un navigateur Chromium capable de faire une capture sans fenêtre."""
    env = os.environ.get("MONTAGE_IA_BROWSER_BIN")
    if env and os.path.isfile(env):
        return env
    cands: list[str] = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif os.uname().sysname == "Darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            cands.append(found)
    return next((c for c in cands if c and os.path.isfile(c)), None)


def font_css() -> str:
    """@font-face des polices livrées, sous le nom affiché dans l'application."""
    from engine.pipeline import fonts
    rules = []
    for f in fonts.bundled():
        path = os.path.join(fonts.BUNDLED_DIR, f["file"])
        url = "file:///" + urllib.request.pathname2url(os.path.abspath(path)).lstrip("/")
        rules.append(f'@font-face{{font-family:"{f["name"]}";src:url("{url}")}}')
    return "\n".join(rules)


def page(markup: str, w: int, h: int, transparent: bool = True) -> str:
    """Document complet : fragment enveloppé à la taille du cadre, polices déclarées."""
    base = (f"html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden;"
            f"background:{'transparent' if transparent else '#000'}}}"
            "*{box-sizing:border-box}")
    style = f"<style>\n{font_css()}\n{base}\n</style>"
    low = markup.lower()
    if "<html" in low:
        if "<head>" in low:
            i = low.index("<head>") + len("<head>")
            return markup[:i] + '<meta charset="utf-8">' + style + markup[i:]
        return markup.replace("<html>", "<html><head>" + style + "</head>", 1)
    return f'<!doctype html><html><head><meta charset="utf-8">{style}</head><body>{markup}</body></html>'


def render(markup: str, w: int, h: int, out_png: str, profile_dir: str, transparent: bool = True,
           timeout: float = 60.0) -> str:
    """Rend le visuel en PNG (`w`×`h`). Transparent là où la page n'a pas de fond."""
    browser = find_browser()
    if not browser:
        raise VisualError("No Chromium browser found (Edge, Chrome, Chromium) to draw the visual. "
                          "Set MONTAGE_IA_BROWSER_BIN to its path.")
    w, h = max(16, min(4096, int(w))), max(16, min(4096, int(h)))
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="montage_visual_") as tmp:
        src = os.path.join(tmp, "visual.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(page(markup, w, h, transparent))
        shot = os.path.join(tmp, "shot.png")
        args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                "--force-device-scale-factor=1", f"--user-data-dir={profile_dir}",
                f"--window-size={w},{h}", "--virtual-time-budget=4000", "--allow-file-access-from-files",
                f"--screenshot={shot}"]
        if transparent:
            args.append("--default-background-color=00000000")
        args.append("file:///" + urllib.request.pathname2url(src).lstrip("/"))
        flags = 0x08000000 if os.name == "nt" else 0           # CREATE_NO_WINDOW
        res = None
        with _LOCK:
            # profil gardé d'un rendu à l'autre (plus rapide) ; s'il refuse de
            # servir (verrou, profil abîmé…), un profil neuf, jetable
            for prof in (profile_dir, os.path.join(tmp, "profile")):
                run = [f"--user-data-dir={prof}" if a.startswith("--user-data-dir=") else a for a in args]
                t0 = time.time()
                try:
                    res = subprocess.run(run, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                         timeout=timeout, creationflags=flags)
                except subprocess.TimeoutExpired:
                    raise VisualError("The browser took too long to draw the visual.") from None
                # Edge peut se relancer lui-même dans un autre processus (vu depuis l'application
                # installée) : celui qu'on a lancé rend la main tout de suite, l'image arrive après
                wait = min(20.0, max(1.0, timeout - (time.time() - t0))) if res.returncode == 0 else 2.0
                if _wait_file(shot, wait):
                    break
        if not os.path.isfile(shot):
            raise VisualError(f"The browser did not produce an image (exit code {res.returncode}): "
                              + (res.stderr or res.stdout or "")[-400:])
        _fit(shot, w, h)
        shutil.move(shot, out_png)
    return out_png


# ------------------------------------------------------------ animations

MAX_ANIM = 20.0          # s : au-delà, un visuel animé devient une vidéo à part entière
# Fige la page à l'instant t. Renvoie 1 si quelque chose y bouge (une animation
# en cours, ou `window.activeAt(t)` quand la page le dit ; une page qui dessine
# par `window.seek` sans le dire est supposée toujours bouger), 0 sinon : le
# moteur réutilise alors l'image d'avant au lieu d'en reprendre une.
SEEK = """(async (t) => {
  let moving = false;
  document.getAnimations().forEach((a) => { try {
    a.pause(); a.currentTime = t * 1000;
    const c = a.effect.getComputedTiming(), lt = c.localTime ?? 0;
    if (lt >= c.delay - 40 && lt <= c.delay + c.activeDuration + 40) moving = true;
  } catch (e) {} });
  if (typeof window.seek === "function") {
    await window.seek(t);
    moving = moving || (typeof window.activeAt === "function" ? !!window.activeAt(t) : true);
  }
  await Promise.race([new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
                      new Promise((r) => setTimeout(r, 80))]);
  return moving ? 1 : 0;
})(%s)"""


class _CDP:
    """Le strict nécessaire du protocole de débogage de Chromium (Edge, Chrome)."""

    def __init__(self, url: str) -> None:
        from websockets.sync.client import connect
        self.ws = connect(url, max_size=None, open_timeout=15)
        self.n = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        import json
        import time
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise VisualError(f"The browser did not answer ({method}).")
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise VisualError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result") or {}

    def eval(self, expr: str, timeout: float = 30.0):
        res = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True},
                        timeout)
        if res.get("exceptionDetails"):
            text = res["exceptionDetails"].get("exception", {}).get("description") or res["exceptionDetails"].get(
                "text", "")
            raise VisualError("Script error in the visual: " + str(text)[:300])
        return (res.get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _wait_file(path: str, timeout: float) -> bool:
    """Le fichier est-il là (et fini d'écrire) avant `timeout` secondes ?"""
    end = time.time() + timeout
    size = -1
    while time.time() < end:
        try:
            now = os.path.getsize(path)
        except OSError:
            now = -1
        if now > 0 and now == size:
            return True
        size = now
        time.sleep(0.15)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _kill_profile(prof: str) -> None:
    """Dernier recours : tue les navigateurs lancés sur ce dossier de profil (Edge relancé
    dans un autre processus que le nôtre, qui n'a jamais répondu)."""
    if os.name != "nt":
        return
    path = os.path.abspath(prof).replace("'", "''")
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
              + path + "') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       timeout=20, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _stop(proc, cdp: _CDP | None = None, prof: str = "") -> None:
    """Ferme le navigateur et attend qu'il ait lâché son dossier de profil. Par le
    protocole d'abord : le navigateur n'est pas forcément le processus qu'on a lancé
    (Edge peut se relancer lui-même)."""
    if cdp is not None:
        try:
            cdp.call("Browser.close", timeout=5)
        except Exception:  # noqa: BLE001 - la connexion se ferme avec le navigateur
            pass
        cdp.close()
    proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass
    if prof:
        lock = os.path.join(prof, "lockfile")
        end = time.time() + 5
        while os.path.exists(lock) and time.time() < end:
            try:
                os.remove(lock)                   # possible dès que plus aucun navigateur ne le tient
            except OSError:
                time.sleep(0.1)
        if os.path.exists(lock):
            _kill_profile(prof)


def _devtools(browser: str, w: int, h: int, prof: str):
    """Navigateur sans fenêtre, piloté : (processus, session sur la page). Le port est
    lu dans le profil, où l'écrit le vrai navigateur : Edge lancé depuis l'application
    installée se relance dans un autre processus et celui qu'on a lancé s'arrête aussitôt."""
    import json
    args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--mute-audio", "--force-device-scale-factor=1",
            f"--user-data-dir={prof}", "--remote-debugging-port=0", f"--window-size={w},{h}",
            "--allow-file-access-from-files", "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding", "about:blank"]
    flags = 0x08000000 if os.name == "nt" else 0
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    port_file = os.path.join(prof, "DevToolsActivePort")
    t0 = time.time()
    end = t0 + 25
    port = None
    exited = False
    while time.time() < end:
        try:
            with open(port_file, encoding="utf-8") as f:
                first = f.readline().strip()
            if first.isdigit():
                port = int(first)
                break
        except OSError:
            pass
        if not exited and proc.poll() is not None:
            exited = True                        # relancé ailleurs, ou planté : 10 s pour se montrer
            end = min(end, time.time() + 10)
        time.sleep(0.1)
    if not port:
        _stop(proc, None, prof)
        raise VisualError("The browser did not start its debugging port.")
    url = None
    while time.time() - t0 < 25 and not url:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
                pages = [p for p in json.loads(r.read()) if p.get("type") == "page"]
            url = pages[0]["webSocketDebuggerUrl"] if pages else None
        except (OSError, ValueError, KeyError):
            time.sleep(0.1)
    if not url:
        _stop(proc, None, prof)
        raise VisualError("No page in the browser.")
    return proc, _CDP(url)


def render_animation(markup: str, w: int, h: int, duration: float, out: str, fps: int = 30,
                     transparent: bool = True, on_progress=None, max_duration: float = MAX_ANIM,
                     probe: str = "", encode: list[str] | None = None) -> dict:
    """Visuel ANIMÉ (animations CSS, Web Animations, ou `window.seek(t)` pour un
    dessin en JavaScript) capturé image par image : chaque animation est figée à
    l'instant voulu, puis la page est photographiée (fond transparent). Sortie :
    MOV (PNG, alpha) si `transparent`, sinon MP4. Renvoie images, durée, taille.
    Quand rien ne bouge (voir SEEK), l'image précédente est reprise telle quelle.
    `probe` : expression JavaScript évaluée sur la dernière image (contrôles de
    mise en page), son résultat revient dans `probe`. `encode` : arguments
    ffmpeg de sortie à la place des réglages par défaut."""
    import base64
    import time
    browser = find_browser()
    if not browser:
        raise VisualError("No Chromium browser found (Edge, Chrome, Chromium) to draw the visual.")
    w, h = max(16, min(3840, int(w))), max(16, min(3840, int(h)))
    duration = max(0.2, min(max_duration, float(duration)))
    fps = max(10, min(60, int(fps)))
    n = max(2, int(round(duration * fps)))
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="montage_anim_", ignore_cleanup_errors=True) as tmp:
        src = os.path.join(tmp, "visual.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(page(markup, w, h, transparent))
        frames = os.path.join(tmp, "frames")
        ext = "png" if transparent else "jpg"      # images opaques : JPEG (voir plus bas)
        os.makedirs(frames)
        with _LOCK:
            proc, cdp = _devtools(browser, w, h, os.path.join(tmp, "profile"))
            try:
                cdp.call("Page.enable")
                cdp.call("Emulation.setDeviceMetricsOverride",
                         {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
                if transparent:
                    cdp.call("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
                cdp.call("Page.navigate", {"url": "file:///" + urllib.request.pathname2url(src).lstrip("/")})
                for _ in range(200):
                    if cdp.eval("document.readyState") == "complete":
                        break
                    time.sleep(0.05)
                cdp.eval("document.fonts.ready.then(() => 1)")
                prev_moving = True
                reused = 0
                probed = None
                for i in range(n):
                    moving = bool(cdp.eval(SEEK % f"{i / fps:.5f}"))
                    path = os.path.join(frames, f"{i:05d}.{ext}")
                    if i and not moving and not prev_moving:
                        # rien n'a bougé depuis l'image d'avant : on la reprend
                        shutil.copyfile(os.path.join(frames, f"{i - 1:05d}.{ext}"), path)
                        reused += 1
                    else:
                        # image opaque : JPEG de haute qualité, bien plus rapide à produire que le PNG
                        fmt = {"format": "png"} if transparent else {"format": "jpeg", "quality": 94}
                        shot = cdp.call("Page.captureScreenshot", {**fmt, "fromSurface": True})
                        with open(path, "wb") as f:
                            f.write(base64.b64decode(shot["data"]))
                    prev_moving = moving
                    if on_progress and i % 10 == 0:
                        on_progress(0.9 * i / n)
                if probe:
                    try:
                        probed = cdp.eval(probe)
                    except Exception as exc:  # noqa: BLE001 - contrôle facultatif
                        probed = {"error": str(exc)[:200]}
            finally:
                _stop(proc, cdp, os.path.join(tmp, "profile"))
        first = os.path.join(frames, f"00000.{ext}")
        _fit(first, w, h)                     # même taille pour toutes (barres éventuelles)
        size_ok = True
        from PIL import Image
        with Image.open(os.path.join(frames, f"{n - 1:05d}.{ext}")) as im:
            size_ok = im.size == (w, h)
        if not size_ok:
            for i in range(n):
                _fit(os.path.join(frames, f"{i:05d}.{ext}"), w, h)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        enc = encode or (["-c:v", "png", "-pix_fmt", "rgba"] if transparent
                         else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "fast"])
        res = subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps), "-i",
                              os.path.join(frames, f"%05d.{ext}"), *enc, "-r", str(fps), out], capture_output=True)
        if res.returncode != 0 or not os.path.isfile(out):
            raise VisualError("Encoding the animation failed: " + res.stderr.decode("utf-8", "replace")[-300:])
        sheet = os.path.splitext(out)[0] + "_frames.jpg"
        frames_sheet([os.path.join(frames, f"{int(round(k * (n - 1) / 4)):05d}.{ext}") for k in range(5)], sheet)
        last = os.path.splitext(out)[0] + "_last." + ext
        shutil.copyfile(os.path.join(frames, f"{n - 1:05d}.{ext}"), last)
    return {"frames": n, "fps": fps, "duration": round(n / fps, 3), "width": w, "height": h,
            "seconds": round(time.time() - t0, 1), "sheet": sheet, "last": last, "reused": reused,
            "probe": probed}


# ------------------------------------------------ le web, par un vrai navigateur

_WEB_LOCK = threading.Lock()
MAX_PAGE = 12000         # px : hauteur maximale d'une page capturée d'un seul tenant
BING_FILTERS = {"commercial": "+filterui:license-L2_L3", "any": "", "portrait": "+filterui:aspect-tall",
                "landscape": "+filterui:aspect-wide", "square": "+filterui:aspect-square",
                "transparent": "+filterui:photo-transparent"}


def web_images(query: str, n: int = 12, orientation: str | None = None, license: str = "any",
               transparent: bool = False) -> list[dict]:
    """Images du web entier (moteur Bing, dans un navigateur sans fenêtre : un
    simple téléchargement de la page renvoie des résultats hors sujet). Mèmes,
    logos, captures, illustrations — leur licence n'est pas connue."""
    import json
    import time
    import urllib.parse
    browser = find_browser()
    if not browser:
        raise VisualError("No Chromium browser found (Edge, Chrome, Chromium) for web search.")
    qft = "".join(BING_FILTERS.get(k, "") for k in (license, orientation or "", "transparent" if transparent else ""))
    params = {"q": query, "form": "HDRSC2", "adlt": "strict", "safeSearch": "strict"}
    if qft:
        params["qft"] = qft
    url = "https://www.bing.com/images/search?" + urllib.parse.urlencode(params)
    grab = ("[...document.querySelectorAll('a.iusc')].map(a => { try { return JSON.parse(a.getAttribute('m')); }"
            " catch (e) { return null; } }).filter(Boolean).map(m => ({t: m.t || '', murl: m.murl, turl: m.turl,"
            " purl: m.purl || '', w: m.mw || 0, h: m.mh || 0}))")
    with _WEB_LOCK, tempfile.TemporaryDirectory(prefix="montage_web_", ignore_cleanup_errors=True) as prof:
        proc, cdp = _devtools(browser, 1280, 900, prof)
        try:
            cdp.call("Page.enable")
            cdp.call("Page.navigate", {"url": url})
            items: list = []
            t0 = time.time()
            while time.time() - t0 < 15:
                time.sleep(0.3)
                items = cdp.eval(grab) or []
                if len(items) >= n:
                    break
        finally:
            _stop(proc, cdp, prof)
    out = []
    for i, it in enumerate(items[:n], 1):
        if not it.get("murl"):
            continue
        # le titre d'une page trouvée par le moteur peut être n'importe quoi (spam) :
        # l'image porte le nom de la recherche et le site d'où elle vient
        site = urllib.parse.urlparse(it.get("purl") or it["murl"]).netloc.removeprefix("www.")
        out.append({"source": "web", "kind": "image", "title": f"{query} {i}", "site": site,
                    "author": "", "license": "unknown (web)", "license_url": "", "page": it.get("purl") or "",
                    "url": it["murl"], "thumb": it.get("turl") or it["murl"], "fallback": it.get("turl") or "",
                    "w": it.get("w") or None, "h": it.get("h") or None,
                    "credit": f"Source : {it.get('purl') or it['murl']}"})
    return out


PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1")


def capture_page(url: str, out: str, w: int = 1080, h: int = 1920, scroll: float = 0.0, fps: int = 30,
                 wait: float = 2.5, device: str = "") -> dict:
    """Capture d'une page web : une image (PNG), ou avec `scroll` secondes, une
    vidéo qui la fait défiler en douceur (comme un enregistrement d'écran).
    `device` : « phone » (version mobile, texte lisible dans un cadre vertical,
    par défaut quand le cadre est vertical) ou « desktop » (1440 px de large,
    réduits à la taille demandée)."""
    import base64
    import time
    browser = find_browser()
    if not browser:
        raise VisualError("No Chromium browser found (Edge, Chrome, Chromium) to capture the page.")
    if not re.match(r"^https?://", url or ""):
        raise VisualError(f"Not a web address: {url!r}")
    w, h = max(320, min(2160, int(w))), max(320, min(3840, int(h)))
    with _WEB_LOCK, tempfile.TemporaryDirectory(prefix="montage_page_", ignore_cleanup_errors=True) as tmp:
        proc, cdp = _devtools(browser, w, h, os.path.join(tmp, "profile"))
        try:
            cdp.call("Page.enable")
            phone = (device or ("phone" if h > w else "desktop")) == "phone"
            dsf = w / 432 if phone else w / 1440          # largeur CSS : 432 (téléphone) ou 1440 (ordinateur)
            cw, ch = round(w / dsf), round(h / dsf)
            if phone:
                cdp.call("Emulation.setUserAgentOverride", {"userAgent": PHONE_UA, "platform": "iPhone"})
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": cw, "height": ch, "deviceScaleFactor": dsf,
                                                            "mobile": phone})
            cdp.call("Page.navigate", {"url": url})
            t0 = time.time()
            while time.time() - t0 < 20 and cdp.eval("document.readyState") != "complete":
                time.sleep(0.2)
            time.sleep(wait)                              # images, polices, bannières qui se posent
            # bandeaux de cookies : on clique « Refuser » / « Accepter » s'il y en a un
            cdp.eval("""(() => { const re = /^(tout refuser|refuser|reject all|decline|accepter|accept all|j'accepte|ok)$/i;
              for (const b of document.querySelectorAll('button, [role=button], a')) {
                if (re.test((b.innerText || '').trim())) { b.click(); return 1; } } return 0; })()""")
            time.sleep(0.6)
            if scroll <= 0:
                shot = cdp.call("Page.captureScreenshot", {"format": "png"})
                with open(out, "wb") as f:
                    f.write(base64.b64decode(shot["data"]))
                return {"kind": "image", "width": w, "height": h}
            # une seule capture de toute la page (images paresseuses chargées en la
            # parcourant d'abord), puis ffmpeg la fait défiler : rapide et parfaitement fluide
            full = float(cdp.eval("document.documentElement.scrollHeight") or h)
            for k in range(1, 9):
                cdp.eval(f"window.scrollTo(0, {full * k / 8:.0f})")
                time.sleep(0.25)
            cdp.eval("window.scrollTo(0, 0)")
            time.sleep(0.5)
            full_css = min(MAX_PAGE / dsf, max(ch, float(cdp.eval("document.documentElement.scrollHeight") or ch)))
            shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                       "clip": {"x": 0, "y": 0, "width": cw, "height": full_css,
                                                                "scale": 1}}, timeout=60)
            full = round(full_css * dsf)
            tall = os.path.join(tmp, "page.png")
            with open(tall, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
        finally:
            _stop(proc, cdp, os.path.join(tmp, "profile"))
        d = max(1.0, min(30.0, float(scroll)))
        total = max(0.0, full - h)
        # défilement adouci au départ et à l'arrivée : u*u*(3-2u), u = t/d
        y = f"{total:.1f}*(min(t/{d:.3f},1)*min(t/{d:.3f},1)*(3-2*min(t/{d:.3f},1)))"
        res = subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(fps), "-i", tall,
                              "-vf", f"scale={w}:-2,crop={w - w % 2}:{h - h % 2}:0:'{y}',format=yuv420p",
                              "-t", f"{d:.3f}",
                              "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-r", str(fps), out],
                             capture_output=True)
        if res.returncode != 0:
            raise VisualError("Encoding the page capture failed: " + res.stderr.decode("utf-8", "replace")[-300:])
    return {"kind": "video", "width": w, "height": h, "duration": round(d, 2), "scroll_px": int(total)}


def frames_sheet(paths: list[str], out_jpg: str, height: int = 360) -> str:
    """Quelques images d'une animation côte à côte, sur un damier."""
    from PIL import Image
    tiles = []
    for p in paths:
        with Image.open(p) as im:
            im = im.convert("RGBA")
            k = height / im.height
            im = im.resize((max(1, int(im.width * k)), height))
            tile = 16
            cols, rows = im.width // tile + 1, im.height // tile + 1
            small = Image.new("RGBA", (cols, rows), (255, 255, 255, 255))
            for y in range(rows):
                for x in range((y + 1) % 2, cols, 2):
                    small.putpixel((x, y), (205, 205, 210, 255))
            bg = small.resize((cols * tile, rows * tile), Image.NEAREST).crop((0, 0, im.width, im.height))
            bg.alpha_composite(im)
            tiles.append(bg.convert("RGB"))
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 6 * (len(tiles) + 1), height + 12), (20, 20, 24))
    x = 6
    for t in tiles:
        sheet.paste(t, (x, 6))
        x += t.width + 6
    sheet.save(out_jpg, "JPEG", quality=84)
    return out_jpg


def _fit(path: str, w: int, h: int) -> None:
    """La capture fait parfois quelques pixels de moins (barres) : on la remet à la taille."""
    from PIL import Image
    with Image.open(path) as im:
        if im.size == (w, h):
            return
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(im.convert("RGBA"), (0, 0))
    if path.lower().endswith((".jpg", ".jpeg")):
        canvas = canvas.convert("RGB")          # le JPEG n'a pas de transparence
    canvas.save(path)


def thumbnail(png: str, out_jpg: str, max_side: int = 640) -> str:
    """Aperçu du visuel sur un damier (la transparence reste visible)."""
    from PIL import Image
    with Image.open(png) as im:
        im = im.convert("RGBA")
        im.thumbnail((max_side, max_side))
        tile = 16
        cols, rows = im.width // tile + 1, im.height // tile + 1
        small = Image.new("RGBA", (cols, rows), (255, 255, 255, 255))
        for y in range(rows):
            for x in range((y + 1) % 2, cols, 2):
                small.putpixel((x, y), (205, 205, 210, 255))
        bg = small.resize((cols * tile, rows * tile), Image.NEAREST).crop((0, 0, im.width, im.height))
        bg.alpha_composite(im)
        bg.convert("RGB").save(out_jpg, "JPEG", quality=85)
    return out_jpg
