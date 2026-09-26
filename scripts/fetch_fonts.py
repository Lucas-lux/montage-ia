"""Récupère les polices embarquées de Montage IA (Google Fonts, licence SIL OFL).

    pip install fonttools          # lecture des noms et de la couverture des glyphes
    python scripts/fetch_fonts.py

Écrit `engine/data/fonts/` : un fichier TTF statique par police (le poids voulu
d'une famille variable est demandé à l'API CSS de Google Fonts, qui renvoie une
instance fixe), `fonts.json` (le catalogue lu par le moteur et l'éditeur) et la
licence OFL de chaque famille dans `licenses/`.

Le nom retenu pour chaque police est son nom complet (« Montserrat Black ») :
c'est celui que libass reconnaît à l'export, et l'éditeur déclare la police
sous ce même nom en CSS. Une police qui n'a pas tous les caractères du
français est refusée.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from fontTools.ttLib import TTFont
except ImportError:
    raise SystemExit("fontTools manque : pip install fonttools")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "engine", "data", "fonts")
UA = {"User-Agent": "curl/8.4.0"}          # l'API CSS renvoie alors des TTF
FRENCH = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789éèêëàâäîïôöùûüçÉÈÊÀÂÎÔÙÛÇœŒ'’!?.,:;«»-%€"

# (famille Google Fonts, poids, catégorie, dossier du dépôt google/fonts)
FONTS: list[tuple[str, int, str, str]] = [
    # impact : titres, accroches, sous-titres qui claquent
    ("Anton", 400, "impact", "anton"),
    ("Bebas Neue", 400, "impact", "bebasneue"),
    ("Archivo Black", 400, "impact", "archivoblack"),
    ("Montserrat", 900, "impact", "montserrat"),
    ("Poppins", 800, "impact", "poppins"),
    ("Oswald", 700, "impact", "oswald"),
    ("Kanit", 800, "impact", "kanit"),
    ("Rubik", 900, "impact", "rubik"),
    ("Russo One", 400, "impact", "russoone"),
    ("Teko", 600, "impact", "teko"),
    ("Staatliches", 400, "impact", "staatliches"),
    ("Alfa Slab One", 400, "impact", "alfaslabone"),
    ("Black Ops One", 400, "impact", "blackopsone"),
    ("Passion One", 400, "impact", "passionone"),
    # créateurs : sans empattement modernes, lisibles
    ("Inter", 800, "createur", "inter"),
    ("Nunito", 900, "createur", "nunito"),
    ("Lexend", 700, "createur", "lexend"),
    ("Outfit", 800, "createur", "outfit"),
    ("Montserrat", 700, "createur", "montserrat"),
    ("Poppins", 600, "createur", "poppins"),
    # fun : rondes, BD, jeux
    ("Bangers", 400, "fun", "bangers"),
    ("Luckiest Guy", 400, "fun", "luckiestguy"),
    ("Titan One", 400, "fun", "titanone"),
    ("Lilita One", 400, "fun", "lilitaone"),
    ("Fredoka", 700, "fun", "fredoka"),
    ("Baloo 2", 800, "fun", "baloo2"),
    ("Chewy", 400, "fun", "chewy"),
    ("Righteous", 400, "fun", "righteous"),
    ("Bungee", 400, "fun", "bungee"),
    ("Knewave", 400, "fun", "knewave"),
    # manuscrites
    ("Permanent Marker", 400, "manuscrite", "permanentmarker"),
    ("Caveat", 700, "manuscrite", "caveat"),
    ("Pacifico", 400, "manuscrite", "pacifico"),
    ("Lobster", 400, "manuscrite", "lobster"),
    ("Dancing Script", 700, "manuscrite", "dancingscript"),
    ("Satisfy", 400, "manuscrite", "satisfy"),
    ("Great Vibes", 400, "manuscrite", "greatvibes"),
    ("Kalam", 700, "manuscrite", "kalam"),
    ("Shadows Into Light", 400, "manuscrite", "shadowsintolight"),
    ("Gochi Hand", 400, "manuscrite", "gochihand"),
    # rétro, tech, effets
    ("Press Start 2P", 400, "retro", "pressstart2p"),
    ("Monoton", 400, "retro", "monoton"),
    ("Orbitron", 900, "retro", "orbitron"),
    ("Audiowide", 400, "retro", "audiowide"),
    ("VT323", 400, "retro", "vt323"),
    ("Creepster", 400, "retro", "creepster"),
    ("Rubik Glitch", 400, "retro", "rubikglitch"),
    ("Syne Mono", 400, "retro", "synemono"),
    # élégantes
    ("Playfair Display", 800, "elegante", "playfairdisplay"),
    ("Abril Fatface", 400, "elegante", "abrilfatface"),
    ("DM Serif Display", 400, "elegante", "dmserifdisplay"),
    ("Cinzel", 700, "elegante", "cinzel"),
    ("Yeseva One", 400, "elegante", "yesevaone"),
    ("Cormorant Garamond", 700, "elegante", "cormorantgaramond"),
]

CATEGORIES = {"impact": "Impact", "createur": "Créateurs", "fun": "Fun", "manuscrite": "Manuscrites",
              "retro": "Rétro et tech", "elegante": "Élégantes"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def ttf_url(family: str, weight: int) -> str:
    q = urllib.parse.quote_plus(family)
    css = get(f"https://fonts.googleapis.com/css2?family={q}:wght@{weight}").decode()
    m = re.search(r"src: url\((\S+?\.ttf)\)", css)
    if not m:
        raise RuntimeError(f"pas de TTF pour {family} {weight}")
    return m.group(1)


def names(data: bytes) -> tuple[str, str]:
    """(famille, nom complet) lus dans la table `name`."""
    f = TTFont(io.BytesIO(data))
    table = f["name"]
    fam = table.getDebugName(1) or ""
    full = table.getDebugName(4) or fam
    return fam, full


def missing_chars(data: bytes) -> str:
    cmap = TTFont(io.BytesIO(data)).getBestCmap() or {}
    return "".join(ch for ch in FRENCH if ord(ch) not in cmap)


# La plupart des polices Google sont sous OFL ; quelques-unes sous Apache 2.0.
LICENSES = [("ofl", "OFL.txt", "OFL-1.1"), ("apache", "LICENSE.txt", "Apache-2.0"), ("ufl", "UFL.txt", "UFL-1.0")]


def fetch_license(folder: str) -> str:
    """Copie la licence de la famille dans `licenses/` ; renvoie son identifiant."""
    dest = os.path.join(OUT, "licenses", f"{folder}.txt")
    for kind, name, ident in LICENSES:
        try:
            data = get(f"https://raw.githubusercontent.com/google/fonts/main/{kind}/{folder}/{name}")
        except urllib.error.HTTPError:
            continue
        with open(dest, "wb") as fh:
            fh.write(data)
        return ident
    raise RuntimeError(f"licence introuvable pour {folder}")


def main() -> None:
    os.makedirs(os.path.join(OUT, "licenses"), exist_ok=True)
    catalog, refused, seen = [], [], set()
    for family, weight, cat, folder in FONTS:
        data = get(ttf_url(family, weight))
        fam, full = names(data)
        # nom reconnu par libass : la famille si elle suffit (« Bebas Neue »),
        # sinon le nom complet (« Montserrat Black »)
        name = fam if full in (fam, f"{fam} Regular") else full
        lack = missing_chars(data)
        if lack:
            refused.append(f"{name} (manque : {lack})")
            continue
        if name in seen:
            continue
        seen.add(name)
        file = re.sub(r"[^A-Za-z0-9]", "", name) + ".ttf"
        with open(os.path.join(OUT, file), "wb") as fh:
            fh.write(data)
        license_id = fetch_license(folder)
        catalog.append({"name": name, "file": file, "family": family, "weight": weight, "category": cat,
                        "license": license_id, "license_file": f"licenses/{folder}.txt"})
        print(f"  {name:28} {len(data) // 1024:5} Ko  {cat}")
    with open(os.path.join(OUT, "fonts.json"), "w", encoding="utf-8") as fh:
        json.dump({"categories": CATEGORIES, "fonts": catalog}, fh, ensure_ascii=False, indent=1)
    total = sum(os.path.getsize(os.path.join(OUT, c["file"])) for c in catalog)
    print(f"{len(catalog)} polices, {total / 1e6:.1f} Mo -> {OUT}")
    if refused:
        print("Refusées :", *refused, sep="\n  ")


if __name__ == "__main__":
    sys.exit(main())
