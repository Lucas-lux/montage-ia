"""Essai de l'application empaquetée, de bout en bout, comme un utilisateur.

    python scripts/smoke_test.py --app "dist/Montage IA.app"        # macOS
    python scripts/smoke_test.py --app dist/MontageIA/MontageIA.exe  # Windows
    python scripts/smoke_test.py --app app.py                        # depuis les sources

Lance l'application (sans navigateur), puis, par son API :

  1. importe une vidéo parlée (fabriquée avec la synthèse vocale du système,
     ou `--speech`) : proxy, vignettes, forme d'onde — ffmpeg/ffprobe embarqués ;
  2. la transcrit (Whisper « tiny », téléchargé si besoin) ;
  3. pose les sous-titres, un titre en police Windows (remplacée sur Mac) et
     une voix off enregistrée (route micro), traitée (RNNoise, compression…) ;
  4. prépare un montage automatique (règles) ;
  5. exporte, et vérifie la vidéo : durée, son, et du texte VRAIMENT dessiné
     (des pixels blancs sur un fond bleu uni : la police a été trouvée).

Code de sortie 0 si tout passe. Le journal de l'app est affiché en cas d'échec.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PORT = 8791
MAC_LOG = os.path.expanduser("~/Library/Logs/Montage IA/montage-ia.log")
WIN_LOG = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MontageIA", "logs", "montage-ia.log")
BASE = f"http://127.0.0.1:{PORT}"


def say(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def api(path: str, body=None, raw: bytes | None = None, timeout: float = 60):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    ctype = "application/octet-stream" if raw is not None else "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path} : HTTP {e.code} {e.read()[:400]!r}") from e


def mcp_check(cmd: list[str], env: dict, pid: str) -> None:
    """Le serveur MCP des agents IA (`--mcp`) : poignée de main, outils, et un
    outil qui lit le projet de l'essai dans l'application déjà lancée."""
    import threading
    proc = subprocess.Popen([*cmd, "--mcp"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    replies: dict = {}

    def read() -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                replies["garbage"] = line[:200]
                continue
            if "id" in msg:
                replies[msg["id"]] = msg
    threading.Thread(target=read, daemon=True).start()

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "get_timeline", "arguments": {"project": pid, "text": True}}})
        wait(lambda: 3 in replies or "garbage" in replies, "réponses du serveur MCP", 90)
        if "garbage" in replies:
            raise SystemExit(f"MCP : sortie qui n'est pas du JSON-RPC : {replies['garbage']!r}")
        tools = replies[2]["result"]["tools"]
        text = replies[3]["result"]["content"][0]["text"]
        if replies[3]["result"].get("isError") or pid not in text:
            raise SystemExit(f"MCP : get_timeline en erreur : {text[:400]}")
        say(f"serveur MCP (agents IA) : {len(tools)} outils, le projet d'essai se lit ({len(text)} caractères)")
    finally:
        proc.stdin.close()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill()


def wait(cond, what: str, timeout: float) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return
        time.sleep(0.5)
    raise SystemExit(f"délai dépassé : {what}")


def executable(app: str) -> str:
    if app.endswith(".app"):
        return os.path.join(app, "Contents", "MacOS", "MontageIA")
    return app


def ffmpeg_of(app: str) -> str:
    """Le ffmpeg livré avec l'application (c'est lui qu'on veut éprouver)."""
    exe = executable(app)
    for cand in (os.path.join(os.path.dirname(exe), "ffmpeg", "ffmpeg"),
                 os.path.join(os.path.dirname(exe), "ffmpeg", "ffmpeg.exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_of(ffmpeg: str) -> str:
    folder, name = os.path.split(ffmpeg)
    cand = os.path.join(folder, name.replace("ffmpeg", "ffprobe"))
    return cand if folder and os.path.isfile(cand) else (shutil.which("ffprobe") or "ffprobe")


def make_speech(work: str, ffmpeg: str, given: str | None) -> str:
    """Vidéo 9:16 sur fond bleu uni, avec une voix."""
    voice = given
    if not voice:
        voice = os.path.join(work, "voix.aiff")
        text = ("Ne jetez pas votre vieille liseuse. Voici trois astuces pour gagner du temps. "
                "La première astuce est très simple. Merci et à bientôt.")
        if shutil.which("say"):
            voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
            v = next((line.split()[0] for line in voices.splitlines() if "fr_FR" in line), None)
            subprocess.run(["say", *(["-v", v] if v else []), "-o", voice, text], check=True)
        else:
            raise SystemExit("pas de synthèse vocale ici : passe --speech fichier_audio")
    out = os.path.join(work, "parle.mp4")
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=0x1030A0:s=540x960:r=30", "-i", voice, "-map", "0:v", "-map", "1:a", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out], check=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="Montage IA.app ou MontageIA.exe")
    ap.add_argument("--speech", help="fichier audio parlé (sinon synthèse vocale du système)")
    ap.add_argument("--model", default="tiny", help="modèle Whisper de l'essai")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="montage-smoke-")
    ffmpeg = ffmpeg_of(args.app)
    env = dict(os.environ, MONTAGE_IA_PORT=str(PORT), MONTAGE_IA_WORK=os.path.join(tmp, "work"),
               MONTAGE_IA_EXPORTS=os.path.join(tmp, "exports"),
               BROWSER="true" if sys.platform != "win32" else "aucun_navigateur")
    clip = make_speech(tmp, ffmpeg, args.speech)
    say(f"vidéo d'essai : {clip}")

    exe = executable(args.app)
    say(f"lancement : {exe}")
    cmd = [sys.executable, os.path.abspath(exe)] if exe.endswith(".py") else [os.path.abspath(exe)]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ok = False
    try:
        def up() -> bool:
            if proc.poll() is not None:
                raise SystemExit(f"l'application s'est arrêtée (code {proc.returncode})")
            try:
                urllib.request.urlopen(BASE + "/studio", timeout=1)
                return True
            except OSError:
                return False
        wait(up, "démarrage", 120)
        say("moteur : prêt")
        if exe.endswith("MacOS/MontageIA"):
            # l'app Mac doit tourner avec son icône (Dock, barre des menus), pas en repli
            log = open(MAC_LOG, encoding="utf-8", errors="replace").read() if os.path.isfile(MAC_LOG) else ""
            if "PyObjC absent" in log or "Traceback" in log[-4000:]:
                raise SystemExit("app Mac : démarrée sans Dock ni barre des menus (voir le journal)")
            if "Interface" not in log:
                raise SystemExit(f"app Mac : journal {MAC_LOG} vide ou absent")
            say("app Mac : Dock et barre des menus, journal dans ~/Library/Logs")
        if sys.platform == "win32" and os.environ.get("MONTAGE_IA_BROWSER") != "1":
            # l'app Windows s'ouvre dans sa propre fenêtre (WebView2), pas dans le navigateur
            try:
                with urllib.request.urlopen(BASE + "/api/app", timeout=5) as r:
                    say(f"fenêtre de l'application : WebView2 {json.loads(r.read())['webview2']}")
            except OSError:
                if not exe.endswith(".py"):
                    raise SystemExit("app Windows : démarrée sans sa fenêtre (voir le journal)")
                say("fenêtre de l'application absente (pywebview non installé ?) : navigateur")
        llm = api("/api/llm")
        say(f"IA locale : {'présente' if llm['available'] else 'absente (règles)'}")

        pid = api("/api/timeline", {"canvas": "9:16", "name": "Essai"})["id"]
        api(f"/api/timeline/{pid}/save", {"settings": {"model": args.model, "language": "fr"}})
        api(f"/api/timeline/{pid}/media/paths", {"paths": [clip]})
        wait(lambda: not api(f"/api/timeline/{pid}/media")["busy"], "préparation du média", 180)
        m = api(f"/api/timeline/{pid}/media")["media"][0]
        if m["status"] != "ready":
            raise SystemExit(f"média en erreur : {m.get('error')}")
        say(f"média : {m['duration']:.1f} s, proxy + vignettes + forme d'onde")

        api(f"/api/timeline/{pid}/transcribe", {"media": [m["id"]]})
        wait(lambda: api(f"/api/timeline/{pid}")["media"][0]["transcript"]["status"] in ("done", "error"),
             "transcription", 900)
        tr = api(f"/api/timeline/{pid}")["media"][0]["transcript"]
        if tr["status"] != "done" or not tr.get("count"):
            raise SystemExit(f"transcription : {tr}")
        say(f"transcription : {tr['count']} mots ({tr.get('device')})")

        state = api(f"/api/timeline/{pid}")
        main_track = next(t for t in state["tracks"] if t.get("main"))
        audio_track = next(t for t in state["tracks"] if t["kind"] == "audio")
        video = {"id": "c1", "track": main_track["id"], "kind": "video", "media": m["id"], "start": 0,
                 "dur": m["duration"], "in": 0, "speed": 1, "audio_fx": {"lowcut": True, "clarity": 0.5}}
        caps = api(f"/api/timeline/{pid}/captions", {"clips": [video], "settings": {"style": "hype"}})["captions"]
        say(f"sous-titres : {len(caps)} lignes")

        # voix off « enregistrée » : un webm/opus comme celui du navigateur
        rec = os.path.join(tmp, "rec.webm")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.5",
                        "-c:a", "libopus", rec], check=True)
        vo = api(f"/api/timeline/{pid}/media/record?name=Voix%20off%201", raw=open(rec, "rb").read())
        wait(lambda: not api(f"/api/timeline/{pid}/media")["busy"], "voix off", 60)
        say(f"voix off : {vo['name']}")

        title_track = {"id": "tt", "kind": "text", "name": "Titres"}
        caps_track = {"id": "tc", "kind": "text", "name": "Sous-titres"}
        for c in caps:
            c["track"] = "tc"
        title = {"id": "x1", "track": "tt", "kind": "text", "start": 0, "dur": 3, "font": "Segoe UI Black",
                 "size": 110, "color": "#FFFFFF", "outline": 0, "outline_col": "#000000", "mode": "none",
                 "x": 0.5, "y": 0.3, "words": [{"text": "TITRE ESSAI", "start": 0, "end": 3}]}
        voice = {"id": "a1", "track": audio_track["id"], "kind": "audio", "media": vo["id"], "start": 0.5,
                 "dur": 1.5, "in": 0, "speed": 1,
                 "audio_fx": {"denoise": 0.7, "lowcut": True, "gate": True, "deess": 0.4, "compress": 0.6,
                              "clarity": 0.5, "level": True, "preset": "voixoff"}}
        api(f"/api/timeline/{pid}/save", {"tracks": [title_track, caps_track, *state["tracks"]],
                                           "clips": [video, voice, title, *caps]})

        job = api(f"/api/timeline/{pid}/autoedit", {"media": [m["id"]], "options": {"llm": False}})["job_id"]
        wait(lambda: api(f"/api/timeline/{pid}/autoedit/{job}")["status"] in ("done", "error"), "montage auto", 120)
        plan = api(f"/api/timeline/{pid}/autoedit/{job}")
        if plan["status"] != "done":
            raise SystemExit(f"montage auto : {plan['message']}")
        p = plan["plans"][m["id"]]
        say(f"montage auto : accroche « {(p.get('hook') or {}).get('text')} », {len(p['sentences'])} phrases")

        api(f"/api/timeline/{pid}/export", {"resolution": "720p", "fps": 30, "loudness": True})
        wait(lambda: api(f"/api/timeline/{pid}/export")["task"]["status"] != "running", "export", 600)
        ex = api(f"/api/timeline/{pid}/export")
        if ex["task"]["status"] != "done":
            raise SystemExit(f"export : {ex['task']}")
        out = (ex.get("export") or {}).get("output") or ""
        if not os.path.isfile(out):
            found = [os.path.join(r, f) for r, _, fs in os.walk(env["MONTAGE_IA_EXPORTS"]) for f in fs]
            out = found[0] if found else ""
        if not out:
            raise SystemExit("export : fichier introuvable")
        probe = subprocess.run([ffprobe_of(ffmpeg), "-v", "error", "-show_entries",
                                "stream=codec_type:format=duration", "-of", "json", out],
                               capture_output=True, text=True).stdout
        info = json.loads(probe or "{}")
        kinds = sorted(s["codec_type"] for s in info.get("streams", []))
        say(f"export : {os.path.basename(out)}, {float(info['format']['duration']):.1f} s, flux {kinds}")
        if kinds != ["audio", "video"]:
            raise SystemExit("export : il manque le son ou l'image")

        # le titre est-il dessiné ? pixels presque blancs dans le haut de l'image (fond bleu uni)
        raw = subprocess.run([ffmpeg, "-v", "error", "-ss", "1.0", "-i", out, "-frames:v", "1",
                              "-vf", "crop=iw:ih/2:0:0,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        white = sum(1 for b in raw if b > 200)
        say(f"titre : {white} pixels blancs dans la moitié haute")
        if white < 500:
            raise SystemExit("le texte n'est pas dessiné : police introuvable pour libass ?")
        mcp_check(cmd, env, pid)
        ok = True
        say("TOUT PASSE")
        return 0
    finally:
        proc.terminate()
        try:
            out_log = proc.communicate(timeout=20)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            out_log = proc.communicate()[0]
        if not ok:
            print("----- sortie de l'application -----")
            print((out_log or b"").decode("utf-8", "replace")[-6000:])
            for path in (MAC_LOG, WIN_LOG):
                if os.path.isfile(path):
                    print(f"----- journal {path} -----")
                    print(open(path, encoding="utf-8", errors="replace").read()[-8000:])


if __name__ == "__main__":
    sys.exit(main())
