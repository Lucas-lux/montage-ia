"""Polices des sous-titres d'un système à l'autre.

Les styles sont pensés avec les polices livrées par Windows (Segoe UI,
Bahnschrift, Consolas…). Sur un Mac, une partie n'existe pas : sans rien
faire, libass prendrait une police quelconque à l'export et le navigateur une
police à empattements dans l'aperçu. On les remplace par l'équivalent le plus
proche, préinstallé sur macOS — le même tableau sert à l'aperçu
(`engine/web/studio/fonts.js`).

Les polices embarquées (`engine/data/fonts`, licence OFL ou Apache) portent
leur nom complet (« Montserrat Black ») : c'est sous ce nom que l'aperçu les
déclare et que libass les trouve, à condition de lui donner le dossier qui
les contient (`fontsdir`, voir `export_fonts`).

`fontconfig_file()` écrit la configuration dont a besoin le ffmpeg statique
embarqué dans l'application Mac : sans elle, fontconfig ne sait pas où sont
les polices du système et les sous-titres sortent vides.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import struct
import sys

# Polices libres livrées avec l'application (scripts/fetch_fonts.py) : l'aperçu
# les charge par @font-face, libass les lit dans le dossier passé à `fontsdir`.
BUNDLED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fonts")

# Police Windows -> police préinstallée sur macOS (les autres existent des deux côtés).
MAC_FONTS = {
    "Segoe UI": "Helvetica Neue",
    "Segoe UI Black": "Helvetica Neue",
    "Segoe UI Emoji": "Apple Color Emoji",
    "Bahnschrift": "DIN Condensed",
    "Calibri": "Helvetica Neue",
    "Candara": "Optima",
    "Corbel": "Gill Sans",
    "Cambria": "Georgia",
    "Franklin Gothic Medium": "Avenir Next Condensed",
    "Consolas": "Menlo",
    "Segoe Script": "Snell Roundhand",
    "Ink Free": "Chalkboard SE",
}

MAC_FONT_DIRS = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
                 "~/Library/Fonts"]


# Polices du système proposées par l'éditeur (Windows ; remplacées sur Mac).
SYSTEM_FONTS = ["Arial", "Arial Black", "Bahnschrift", "Impact", "Segoe UI", "Segoe UI Black", "Verdana",
                "Tahoma", "Trebuchet MS", "Franklin Gothic Medium", "Candara", "Corbel", "Calibri", "Georgia",
                "Cambria", "Times New Roman", "Courier New", "Consolas", "Comic Sans MS", "Segoe Script",
                "Ink Free"]


def system_font(name: str, platform: str | None = None) -> str:
    """Nom de police à donner au moteur de rendu sur ce système."""
    if (platform or sys.platform) == "darwin":
        return MAC_FONTS.get(name, name)
    return name


def fontconfig_file(folder: str) -> str | None:
    """Écrit `fonts.conf` (polices du système, cache dans `folder`) et renvoie
    son chemin ; None hors macOS."""
    if sys.platform != "darwin":
        return None
    os.makedirs(folder, exist_ok=True)
    cache = os.path.join(folder, "fontconfig-cache")
    dirs = "\n".join(f"  <dir>{os.path.expanduser(d)}</dir>" for d in MAC_FONT_DIRS)
    conf = f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<!-- Écrit par Montage IA : polices de macOS pour le ffmpeg embarqué. -->
<fontconfig>
{dirs}
  <cachedir>{cache}</cachedir>
  <alias><family>sans-serif</family><prefer><family>Helvetica Neue</family></prefer></alias>
  <alias><family>serif</family><prefer><family>Georgia</family></prefer></alias>
  <alias><family>monospace</family><prefer><family>Menlo</family></prefer></alias>
</fontconfig>
"""
    path = os.path.join(folder, "fonts.conf")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == conf:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(conf)
    return path


# ------------------------------------------------------------ polices embarquées

@functools.lru_cache(maxsize=1)
def _catalog() -> dict:
    try:
        with open(os.path.join(BUNDLED_DIR, "fonts.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"categories": {}, "fonts": []}


def bundled() -> list[dict]:
    """Polices livrées : nom, fichier, catégorie, licence."""
    return [f for f in _catalog()["fonts"] if os.path.isfile(os.path.join(BUNDLED_DIR, f["file"]))]


def categories() -> dict:
    return dict(_catalog().get("categories") or {})


def bundled_file(name: str) -> str | None:
    """Fichier d'une police livrée, ou None (police du système)."""
    for f in bundled():
        if f["name"] == name:
            return os.path.join(BUNDLED_DIR, f["file"])
    return None


def export_fonts(names, workdir: str) -> str | None:
    """Copie dans `workdir/fonts` les polices livrées parmi `names` ; renvoie
    ce dossier relatif (« fonts », à passer à `fontsdir=` avec `workdir` comme
    dossier courant de ffmpeg), ou None si aucune n'est à fournir.

    Seulement les polices utilisées, et seulement des polices : libass essaie
    d'ouvrir comme police chaque fichier du dossier."""
    files = {bundled_file(n) for n in names} - {None}
    if not files:
        return None
    dest = os.path.join(workdir, "fonts")
    os.makedirs(dest, exist_ok=True)
    for path in files:
        shutil.copyfile(path, os.path.join(dest, os.path.basename(path)))
    return "fonts"


def subtitles_filter(ass_name: str, names, workdir: str) -> str:
    """Filtre `subtitles` du fichier `ass_name`, polices livrées comprises."""
    folder = export_fonts(names, workdir)
    return f"subtitles={ass_name}" + (f":fontsdir={folder}" if folder else "")


# --------------------------------------------------------- taille réelle du texte
#
# libass ne dessine pas une police de taille N comme un navigateur : il règle la
# police pour que sa hauteur totale (ascendante + descendante « Windows » de la
# table OS/2) fasse N pixels, là où CSS donne N pixels au cadratin. Le
# rapport cadratin / hauteur dépend de chaque police (0,90 pour Arial, 0,64 pour
# Montserrat Black) : l'aperçu multiplie la taille par ce rapport, et prend la
# même hauteur comme interligne. Voir ass_face_set_size / set_font_metrics
# dans libass.

DEFAULT_EM = 0.87            # police introuvable : rapport courant


def _sfnt_faces(path: str) -> list[dict]:
    """Noms et métriques de chaque police d'un fichier TTF/OTF/TTC (lecture des
    seules tables utiles)."""
    out = []
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12:
            return out
        if head[:4] == b"ttcf":
            n = struct.unpack(">I", head[8:12])[0]
            offsets = list(struct.unpack(f">{n}I", f.read(4 * n)))
        else:
            offsets = [0]
        for off in offsets:
            f.seek(off)
            d = f.read(12)
            if len(d) < 12:
                continue
            ntab = struct.unpack(">H", d[4:6])[0]
            tables = {}
            for _ in range(ntab):
                rec = f.read(16)
                tag, _, t_off, t_len = struct.unpack(">4sIII", rec)
                tables[tag] = (t_off, t_len)
            if b"head" not in tables or b"name" not in tables:
                continue
            f.seek(tables[b"head"][0] + 18)
            upm = struct.unpack(">H", f.read(2))[0]
            win = None
            if b"OS/2" in tables:
                f.seek(tables[b"OS/2"][0] + 74)
                asc, desc = struct.unpack(">hh", f.read(4))
                if asc + desc:
                    win = (asc, desc)
            if win is None and b"hhea" in tables:
                f.seek(tables[b"hhea"][0] + 4)
                asc, desc = struct.unpack(">hh", f.read(4))
                win = (asc, -desc)
            n_off, n_len = tables[b"name"]
            f.seek(n_off)
            blob = f.read(n_len)
            names, sub = set(), ""
            if len(blob) >= 6:
                _, count, str_off = struct.unpack(">HHH", blob[:6])
                for i in range(count):
                    pid, eid, _, nid, ln, so = struct.unpack(">HHHHHH", blob[6 + 12 * i:18 + 12 * i])
                    if nid not in (1, 2, 4, 16) or pid != 3 or eid not in (0, 1, 10):
                        continue
                    raw = blob[str_off + so:str_off + so + ln]
                    try:
                        txt = raw.decode("utf-16-be").strip()
                    except UnicodeDecodeError:
                        continue
                    if nid == 2:
                        sub = sub or txt
                    else:
                        names.add(txt.lower())
            if upm and win and sum(win) > 0:
                out.append({"names": names, "sub": sub.lower(), "em": upm / float(sum(win)),
                            "asc": win[0] / float(sum(win))})
    return out


def _font_dirs() -> list[str]:
    if sys.platform == "win32":
        dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs = [os.path.expanduser(d) for d in MAC_FONT_DIRS]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if os.path.isdir(d)]


@functools.lru_cache(maxsize=1)
def _system_index() -> dict[str, float]:
    """Nom de police (minuscules) -> rapport cadratin / hauteur, pour les
    polices du système. La face « Regular » d'une famille l'emporte."""
    index: dict[str, tuple[int, dict]] = {}
    for root_dir in _font_dirs():
        for root, _dirs, files in os.walk(root_dir):
            for name in files:
                if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                try:
                    faces = _sfnt_faces(os.path.join(root, name))
                except (OSError, struct.error):
                    continue
                for face in faces:
                    rank = 0 if face["sub"] in ("regular", "normal", "book", "roman") else 1
                    for n in face["names"]:
                        if n not in index or rank < index[n][0]:
                            index[n] = (rank, face)
    return {k: v[1] for k, v in index.items()}


@functools.lru_cache(maxsize=256)
def font_metrics(name: str) -> dict:
    """Métriques de la police `name` telle que libass la trouve : `em`, rapport
    cadratin / hauteur ; `asc`, part de la hauteur au-dessus de la ligne de base."""
    face = None
    path = bundled_file(name)
    if path:
        try:
            faces = _sfnt_faces(path)
            face = faces[0] if faces else None
        except (OSError, struct.error):
            face = None
    face = face or _system_index().get(system_font(name).lower())
    if not face:
        return {"em": DEFAULT_EM, "asc": 0.8}
    return {"em": round(face["em"], 5), "asc": round(face["asc"], 5)}


def em_scale(name: str) -> float:
    return font_metrics(name)["em"]


def metrics() -> dict[str, dict]:
    """`font_metrics` de chaque police proposée (livrées et système)."""
    names = [f["name"] for f in bundled()] + SYSTEM_FONTS
    return {n: font_metrics(n) for n in names}
