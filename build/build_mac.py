r"""Fabrique « Montage IA.app » et son image disque (macOS, Apple Silicon).

    python build/build.py                # sur un Mac : application + .dmg
    python build/build.py --app-only     # s'arrête après l'application
    python build/build.py --with-model   # embarque le modèle Whisper (~1,6 Go)

Étapes :

 1. PyInstaller empaquette Python, le moteur et l'interface en une
    application (`dist/Montage IA.app`, voir montage_ia.spec).
 2. ffmpeg/ffprobe STATIQUES (ffmpeg.martin-riedl.de, avec libass, x264,
    x265, opus…) vont dans Contents/MacOS/ffmpeg : ceux de Homebrew
    dépendent de bibliothèques qui n'existent que sur la machine de build.
    Les modèles de traduction vont dans Contents/Resources/models.
 3. Signature : ad hoc par défaut (l'app tourne, Gatekeeper demande une
    confirmation au premier lancement), ou avec un certificat « Developer ID
    Application » si `MAC_SIGN_IDENTITY` est défini — et alors notarisation
    si `MAC_NOTARY_APPLE_ID`, `MAC_NOTARY_TEAM_ID`, `MAC_NOTARY_PASSWORD` le
    sont aussi (mot de passe d'application Apple).
 4. Image disque `dist/MontageIA-<version>-macos-arm64.dmg`, avec le
    raccourci vers Applications.

Uniquement Apple Silicon : CTranslate2 (Whisper, IA locale) n'est plus
publié pour les Mac Intel.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
APP = os.path.join(ROOT, "dist", "Montage IA.app")
CACHE = os.path.join(HERE, "_mac")
FFMPEG_URL = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/release/{tool}.zip"
VERSION = os.environ.get("MONTAGE_IA_VERSION", "0.1.0")
UA = "montage-ia-build/1.0 (+https://github.com/Lucas-lux/montage-ia)"
# bibliothèques qu'un binaire embarqué ne doit pas réclamer (absentes chez l'utilisateur)
FOREIGN = ("/opt/homebrew/", "/usr/local/opt/", "/usr/local/Cellar/", "/usr/local/lib/")


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
            p = os.path.join(root, f)
            if not os.path.islink(p):
                total += os.path.getsize(p)
    return total


def run(cmd: list[str], quiet: bool = False, **kw) -> subprocess.CompletedProcess:
    """Lance une commande ; en cas d'échec, sa sortie est affichée."""
    res = subprocess.run(cmd, capture_output=quiet, text=True, **kw)
    if res.returncode != 0:
        if quiet:
            print(res.stdout or "", res.stderr or "", sep="\n")
        raise SystemExit(f"échec : {' '.join(cmd[:3])}… (code {res.returncode})")
    return res


def arch() -> str:
    return "arm64" if platform.machine() in ("arm64", "aarch64") else "amd64"


# ------------------------------------------------------------------- ffmpeg

def fetch_ffmpeg() -> list[str]:
    """ffmpeg et ffprobe statiques : `MONTAGE_IA_FFMPEG_DIR`, sinon téléchargés
    (une fois) dans build/_mac/ffmpeg."""
    given = os.environ.get("MONTAGE_IA_FFMPEG_DIR")
    folder = given or os.path.join(CACHE, "ffmpeg-" + arch())
    os.makedirs(folder, exist_ok=True)
    tools = []
    for tool in ("ffmpeg", "ffprobe"):
        path = os.path.join(folder, tool)
        if not os.path.isfile(path):
            if given:
                raise SystemExit(f"{tool} introuvable dans MONTAGE_IA_FFMPEG_DIR ({given}).")
            url = FFMPEG_URL.format(arch=arch(), tool=tool)
            say(f"{tool} : téléchargement ({url})…")
            archive = path + ".zip"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r, open(archive, "wb") as f:
                shutil.copyfileobj(r, f)
            with zipfile.ZipFile(archive) as z:
                member = next(n for n in z.namelist() if os.path.basename(n) == tool)
                with z.open(member) as src, open(path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            os.remove(archive)
        os.chmod(path, 0o755)
        subprocess.run(["xattr", "-c", path], check=False)
        tools.append(path)
    check_ffmpeg(tools)
    return tools


def check_ffmpeg(tools: list[str]) -> None:
    """Refuse un ffmpeg lié à des bibliothèques de la machine de build, ou
    auquel il manque ce dont l'application a besoin."""
    for t in tools:
        libs = subprocess.run(["otool", "-L", t], capture_output=True, text=True).stdout
        bad = [line.strip() for line in libs.splitlines() if line.strip().startswith(FOREIGN)]
        if bad:
            raise SystemExit(f"{os.path.basename(t)} n'est pas autonome (dépend de {bad[0]}).\n"
                             "Utilise un build statique (laisse build_mac le télécharger).")
    ffmpeg = tools[0]
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    need_f = ["subtitles", "arnndn", "loudnorm", "xfade", "deesser", "acompressor", "afftdn"]
    need_e = ["libx264", "aac", "libmp3lame", "libopus"]
    missing = [f for f in need_f if f" {f} " not in filters] + [e for e in need_e if f" {e} " not in encoders]
    if missing:
        raise SystemExit(f"ffmpeg sans : {', '.join(missing)}")
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    extra = " + VideoToolbox" if "h264_videotoolbox" in encoders else ""
    say(f"ffmpeg : {version.split(' Copyright')[0]}{extra}")


# ------------------------------------------------------------------- étapes

def pyinstaller(clean: bool) -> None:
    say("PyInstaller : empaquetage de l'application…")
    cmd = [sys.executable, "-m", "PyInstaller", os.path.join(HERE, "montage_ia.spec"),
           "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(HERE, "_work"),
           "--noconfirm", "--log-level", "WARN"]
    if clean:
        cmd.append("--clean")
    run(cmd, cwd=ROOT, env=dict(os.environ, MONTAGE_IA_VERSION=VERSION))
    exe = os.path.join(APP, "Contents", "MacOS", "MontageIA")
    if not os.path.isfile(exe):
        raise SystemExit("PyInstaller n'a pas produit Montage IA.app.")
    say(f"application : {human(tree_size(APP))}")


def add_ffmpeg(tools: list[str]) -> None:
    dest = os.path.join(APP, "Contents", "MacOS", "ffmpeg")
    os.makedirs(dest, exist_ok=True)
    for t in tools:
        shutil.copy2(t, os.path.join(dest, os.path.basename(t)))
    say(f"ffmpeg : {human(tree_size(dest))}  -> Contents/MacOS/ffmpeg/")


def add_models(with_model: bool, win) -> None:
    res = os.path.join(APP, "Contents", "Resources", "models")
    src = os.path.join(ROOT, "models", "translate")
    if os.path.isdir(src) and os.listdir(src):
        shutil.copytree(src, os.path.join(res, "translate"), dirs_exist_ok=True)
        say(f"traduction : {human(tree_size(os.path.join(res, 'translate')))}  -> Contents/Resources/models/")
    else:
        say("traduction : aucun modèle dans models/translate — fonction désactivée")
        say("  Récupère-le :  python scripts/download_models.py")
    modnet = os.path.join(ROOT, "models", "matting", "modnet.onnx")
    if os.path.isfile(modnet):
        os.makedirs(os.path.join(res, "matting"), exist_ok=True)
        shutil.copy2(modnet, os.path.join(res, "matting", "modnet.onnx"))
        say("détourage : 26 Mo  -> Contents/Resources/models/matting/")
    if with_model:
        hf = win.find_model()             # même recherche que sous Windows (build.py)
        if not hf:
            say("modèle : aucun cache Whisper trouvé, il sera téléchargé au 1er lancement")
            return
        hub = os.path.join(hf, "hub")
        for d in win._whisper_dirs(hub):
            # snapshots/ pointe vers blobs/ par liens : on copie le contenu, pas les blobs
            shutil.copytree(os.path.join(hub, d), os.path.join(res, "hub", d), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.lock", ".locks", "blobs"), symlinks=False)
        say(f"modèle {win.WHISPER_MODEL} : embarqué")


def sign() -> str:
    """Signe l'application ; renvoie l'identité utilisée (« - » = ad hoc)."""
    identity = os.environ.get("MAC_SIGN_IDENTITY") or "-"
    hardened = identity != "-"
    base = ["codesign", "--force", "--sign", identity]
    if hardened:
        base += ["--options", "runtime", "--timestamp",
                 "--entitlements", os.path.join(HERE, "entitlements.plist")]
    # les binaires ajoutés après PyInstaller d'abord, puis l'application entière
    for t in ("ffmpeg", "ffprobe"):
        run(base + [os.path.join(APP, "Contents", "MacOS", "ffmpeg", t)], quiet=True)
    run(base + ["--deep", APP], quiet=True)
    run(["codesign", "--verify", "--deep", "--strict", APP], quiet=True)
    say("signature : " + ("ad hoc (pas de certificat Developer ID)" if not hardened else identity))
    return identity


def make_dmg() -> str:
    out = os.path.join(ROOT, "dist", f"MontageIA-{VERSION}-macos-arm64.dmg")
    stage = os.path.join(HERE, "_work", "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    say("image disque : fabrication…")
    run(["ditto", APP, os.path.join(stage, "Montage IA.app")])
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    if os.path.exists(out):
        os.remove(out)
    run(["hdiutil", "create", "-volname", "Montage IA", "-srcfolder", stage, "-ov",
         "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9", out], quiet=True)
    shutil.rmtree(stage, ignore_errors=True)
    return out


def notarize(dmg: str, identity: str) -> None:
    need = ("MAC_NOTARY_APPLE_ID", "MAC_NOTARY_TEAM_ID", "MAC_NOTARY_PASSWORD")
    if identity == "-" or not all(os.environ.get(k) for k in need):
        say("notarisation : ignorée (certificat ou identifiants Apple absents)")
        return
    say("notarisation : envoi à Apple (quelques minutes)…")
    run(["codesign", "--force", "--sign", identity, "--timestamp", dmg], quiet=True)
    run(["xcrun", "notarytool", "submit", dmg, "--apple-id", os.environ["MAC_NOTARY_APPLE_ID"],
         "--team-id", os.environ["MAC_NOTARY_TEAM_ID"], "--password", os.environ["MAC_NOTARY_PASSWORD"],
         "--wait"])
    run(["xcrun", "stapler", "staple", dmg])
    say("notarisation : acceptée, ticket agrafé")


def main(args, win) -> None:
    """`win` : le module build.py (recherche du modèle Whisper partagée)."""
    import time
    if arch() != "arm64":
        say("attention : Mac Intel — CTranslate2 (Whisper) n'y est plus publié, le build risque d'échouer")
    t0 = time.time()
    if args.clean and os.path.isdir(APP):
        shutil.rmtree(APP, ignore_errors=True)
    tools = fetch_ffmpeg()
    pyinstaller(args.clean)
    add_ffmpeg(tools)
    add_models(args.with_model, win)
    identity = sign()
    say(f"application finale : {human(tree_size(APP))}  -> {APP}")
    dmg = None
    if not args.app_only:
        dmg = make_dmg()
        notarize(dmg, identity)
    say(f"terminé en {time.time() - t0:.0f} s")
    print()
    print(f"  Application : {APP}")
    if dmg:
        print(f"  Image disque : {dmg}  ({human(os.path.getsize(dmg))})")
