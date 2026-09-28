"""Images (et vidéos) libres pour illustrer un montage.

Sources :
  * le web entier (Bing Images, par le navigateur sans fenêtre) : mèmes, logos,
    captures, illustrations — licence inconnue, fond transparent sur demande ;
  * Openverse (images sous licence Creative Commons ou domaine public, sans clé) ;
  * Wikimedia Commons (sans clé) ;
  * Pexels (photos et vidéos libres de droits) si une clé est fournie
    (`PEXELS_API_KEY`, gratuite sur pexels.com/api).

Une recherche renvoie ses résultats numérotés et une planche de vignettes
(`sheet.jpg`) que l'agent regarde pour choisir ; `download` importe ensuite le
fichier dans le projet, avec son crédit (auteur, licence, page d'origine).
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import shutil
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "MontageIA/0.1 (+https://github.com/Lucas-lux/montage-ia)"
# certains sites (images du web) refusent un client qui ne ressemble pas à un navigateur
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0 Safari/537.36")
MAX_IMAGE = 40 * 1024 * 1024
MAX_VIDEO = 500 * 1024 * 1024
TIMEOUT = 20
SEARCHES: dict[str, dict] = {}
_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
        "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
        "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg", "audio/mp4": ".m4a"}


class SearchError(RuntimeError):
    pass


def _get(url: str, headers: dict | None = None, timeout: float = TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _json(url: str, headers: dict | None = None) -> dict:
    with _get(url, headers) as r:
        return json.loads(r.read().decode("utf-8"))


def _text(v) -> str:
    """Texte brut d'un champ Wikimedia (souvent du HTML)."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(v or "")))).strip()


def _orient_ok(w, h, orientation: str | None) -> bool:
    if not orientation or not w or not h:
        return True
    r = w / h
    return {"portrait": r < 0.9, "landscape": r > 1.1, "square": 0.85 <= r <= 1.18}.get(orientation, True)


# ------------------------------------------------------------- sources

def openverse(q: str, n: int, orientation: str | None, license: str) -> list[dict]:
    params = {"q": q, "page_size": min(20, max(1, n)), "mature": "false"}
    if license == "commercial":
        params["license_type"] = "commercial,modification"
    if orientation in ("portrait", "landscape", "square"):
        params["aspect_ratio"] = {"portrait": "tall", "landscape": "wide", "square": "square"}[orientation]
    data = _json("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params))
    out = []
    for r in data.get("results") or []:
        lic = f"CC {r.get('license', '').upper()} {r.get('license_version') or ''}".strip()
        if r.get("license") in ("cc0", "pdm"):
            lic = "CC0" if r["license"] == "cc0" else "Public domain"
        out.append({"source": "openverse", "kind": "image", "title": r.get("title") or "", "author": r.get("creator")
                    or "", "license": lic, "license_url": r.get("license_url") or "",
                    "page": r.get("foreign_landing_url") or "", "url": r.get("url") or "",
                    "thumb": r.get("thumbnail") or r.get("url") or "", "w": r.get("width"), "h": r.get("height"),
                    "credit": r.get("attribution") or ""})
    return out


def wikimedia(q: str, n: int, orientation: str | None, license: str) -> list[dict]:
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{q} filetype:bitmap",
              "gsrnamespace": 6, "gsrlimit": min(30, max(1, n * 2)), "prop": "imageinfo",
              "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 400}
    data = _json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params))
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        meta = ii.get("extmetadata") or {}
        lic = _text((meta.get("LicenseShortName") or {}).get("value"))
        if license == "commercial" and re.search(r"\bNC\b|non-?commercial", lic, re.I):
            continue
        author = _text((meta.get("Artist") or {}).get("value"))
        title = _text((meta.get("ObjectName") or {}).get("value")) or p.get("title", "").replace("File:", "")
        out.append({"source": "wikimedia", "kind": "image", "title": title, "author": author, "license": lic,
                    "license_url": _text((meta.get("LicenseUrl") or {}).get("value")),
                    "page": ii.get("descriptionurl") or "", "url": ii.get("url") or "",
                    "thumb": ii.get("thumburl") or ii.get("url") or "", "w": ii.get("width"), "h": ii.get("height"),
                    "credit": f"{title} — {author or 'Wikimedia Commons'} ({lic}), {ii.get('descriptionurl', '')}"})
    return out


def pexels(q: str, n: int, orientation: str | None, key: str, kind: str = "photo") -> list[dict]:
    params = {"query": q, "per_page": min(40, max(1, n))}
    if orientation in ("portrait", "landscape", "square"):
        params["orientation"] = orientation
    base = "https://api.pexels.com/videos/search?" if kind == "video" else "https://api.pexels.com/v1/search?"
    data = _json(base + urllib.parse.urlencode(params), {"Authorization": key})
    out = []
    if kind == "video":
        for v in data.get("videos") or []:
            files = [f for f in v.get("video_files") or [] if f.get("file_type") == "video/mp4" and f.get("link")]
            # la plus proche de 1080 lignes (côté court), sans monter en 4K inutilement
            files.sort(key=lambda f: abs(min(f.get("width") or 0, f.get("height") or 0) - 1080))
            if not files:
                continue
            f = files[0]
            who = (v.get("user") or {}).get("name") or ""
            out.append({"source": "pexels", "kind": "video", "title": v.get("url", "").rstrip("/").split("/")[-1],
                        "author": who, "license": "Pexels License", "license_url": "https://www.pexels.com/license/",
                        "page": v.get("url") or "", "url": f["link"], "thumb": v.get("image") or "",
                        "w": f.get("width"), "h": f.get("height"), "duration": v.get("duration"),
                        "credit": f"Video by {who} on Pexels"})
    else:
        for p in data.get("photos") or []:
            src = p.get("src") or {}
            out.append({"source": "pexels", "kind": "image", "title": p.get("alt") or "",
                        "author": p.get("photographer") or "", "license": "Pexels License",
                        "license_url": "https://www.pexels.com/license/", "page": p.get("url") or "",
                        "url": src.get("large2x") or src.get("original") or "", "thumb": src.get("medium") or "",
                        "w": p.get("width"), "h": p.get("height"),
                        "credit": f"Photo by {p.get('photographer') or ''} on Pexels"})
    return out


def web(q: str, n: int, orientation: str | None, license: str, transparent: bool = False) -> list[dict]:
    from engine.agent import visuals
    try:
        return visuals.web_images(q, n, orientation, license, transparent)
    except visuals.VisualError as exc:
        raise SearchError(str(exc)) from exc


def search(q: str, work: str, n: int = 12, source: str = "auto", orientation: str | None = None,
           license: str = "any", kind: str = "photo", pexels_key: str | None = None,
           transparent: bool = False) -> dict:
    """Cherche sur les sources demandées, en parallèle ; planche de vignettes.
    `transparent` : logos, stickers, personnages détourés (web seulement)."""
    q = (q or "").strip()
    if not q:
        raise SearchError("Empty query.")
    n = max(1, min(24, int(n or 12)))
    if kind == "video" and not pexels_key:
        raise SearchError("Video search needs a Pexels API key (free at pexels.com/api): set PEXELS_API_KEY "
                          "in the MCP server environment.")
    wanted = {"auto": ["web", "pexels", "openverse", "wikimedia"] if pexels_key else ["web", "openverse", "wikimedia"],
              "free": ["pexels", "openverse", "wikimedia"] if pexels_key else ["openverse", "wikimedia"],
              "all": ["web", "pexels", "openverse", "wikimedia"]}.get(source, [source])
    wanted = [s for s in wanted if s != "pexels" or pexels_key]
    if kind == "video":
        wanted = ["pexels"]
    elif transparent:
        wanted = ["web"]
    if not wanted:
        raise SearchError(f"Unknown or unavailable source {source!r}: auto, free, web, openverse, wikimedia, "
                          "pexels (with a key).")
    jobs = {"web": lambda: web(q, n, orientation, license, transparent),
            "openverse": lambda: openverse(q, n, orientation, license),
            "wikimedia": lambda: wikimedia(q, n, orientation, license),
            "pexels": lambda: pexels(q, n, orientation, pexels_key or "", kind)}
    found: dict[str, list] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {s: ex.submit(jobs[s]) for s in wanted if s in jobs}
        for s, f in futs.items():
            try:
                found[s] = [r for r in f.result() if r.get("url") and _orient_ok(r.get("w"), r.get("h"), orientation)]
            except Exception as exc:  # noqa: BLE001 - réseau, quota : les autres sources suffisent
                errors[s] = str(exc)[:200]
    # résultats entrelacés : chaque source a sa place en tête de liste
    merged: list[dict] = []
    lists = [found.get(s, []) for s in wanted]
    while len(merged) < n and any(lists):
        for lst in lists:
            if lst and len(merged) < n:
                merged.append(lst.pop(0))
    if not merged:
        raise SearchError("No result" + (f" ({'; '.join(f'{k}: {v}' for k, v in errors.items())})" if errors else
                                         f" for {q!r}: try other words (English often finds more)."))
    sid = secrets.token_hex(3)
    for i, r in enumerate(merged, 1):
        r["n"] = i
        r["ref"] = f"{sid}:{i}"
    folder = os.path.join(work, "agent", "searches", sid)
    os.makedirs(folder, exist_ok=True)
    sheet = os.path.join(folder, "sheet.jpg")
    contact_sheet(merged, sheet)
    SEARCHES[sid] = {"query": q, "results": merged, "sheet": sheet}
    _prune(os.path.join(work, "agent", "searches"))
    return {"id": sid, "query": q, "results": merged, "sheet": sheet, "errors": errors}


def _prune(root: str, keep: int = 30) -> None:
    try:
        dirs = sorted((os.path.join(root, d) for d in os.listdir(root)), key=os.path.getmtime)
    except OSError:
        return
    for d in dirs[:-keep]:
        shutil.rmtree(d, ignore_errors=True)


def result(ref: str) -> dict:
    sid, _, num = str(ref).partition(":")
    s = SEARCHES.get(sid)
    if not s or not num.isdigit() or not 1 <= int(num) <= len(s["results"]):
        raise SearchError(f"Unknown search result {ref!r}: use a `ref` from search_images (like 'a1b2c3:4').")
    return s["results"][int(num) - 1]


# -------------------------------------------------------------- planche

def _thumb(url: str):
    from PIL import Image
    import io
    try:
        with _get(url, timeout=12) as r:
            data = r.read(6 * 1024 * 1024)
        im = Image.open(io.BytesIO(data))
        im.load()
        return im.convert("RGB")
    except Exception:  # noqa: BLE001 - vignette manquante : case grise
        return None


def contact_sheet(results: list[dict], out: str, cols: int = 4, cell: int = 250) -> str:
    """Vignettes numérotées, 4 par ligne, avec source et définition."""
    from PIL import Image, ImageDraw, ImageFont
    with ThreadPoolExecutor(max_workers=8) as ex:
        thumbs = list(ex.map(lambda r: _thumb(r.get("thumb") or r["url"]), results))
    rows = (len(results) + cols - 1) // cols
    label_h = 30
    sheet = Image.new("RGB", (cols * cell, rows * (cell + label_h)), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 17)
        big = ImageFont.truetype("arialbd.ttf", 26)
    except OSError:
        font = big = ImageFont.load_default()
    for i, (r, im) in enumerate(zip(results, thumbs)):
        x0, y0 = (i % cols) * cell, (i // cols) * (cell + label_h)
        if im is not None:
            im.thumbnail((cell - 8, cell - 8))
            sheet.paste(im, (x0 + (cell - im.width) // 2, y0 + (cell - im.height) // 2))
        else:
            draw.rectangle([x0 + 4, y0 + 4, x0 + cell - 4, y0 + cell - 4], fill=(60, 60, 66))
        draw.rectangle([x0 + 6, y0 + 6, x0 + 46, y0 + 40], fill=(255, 212, 0))
        draw.text((x0 + 12, y0 + 8), str(r["n"]), fill=(0, 0, 0), font=big)
        info = (f"{r['source']} {r['w']}×{r['h']}" if r.get("w") else f"{r['source']} {r.get('site') or ''}")[:30] \
            + (" vidéo" if r["kind"] == "video" else "")
        draw.text((x0 + 8, y0 + cell + 5), info, fill=(210, 210, 215), font=font)
    sheet.save(out, "JPEG", quality=82)
    return out


# ---------------------------------------------------------- téléchargement

def _name_from(title: str, url: str, ext: str) -> str:
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title or "").strip()[:60]
    if not base:
        base = os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0][:60] or "media"
    return base + ext


def download(url: str, dest_dir: str, title: str = "", kind: str = "", fallback: str = "") -> tuple[str, str]:
    """Télécharge un fichier (image, vidéo, son) dans `dest_dir` ; renvoie (chemin, nom).
    `fallback` : autre adresse si la première refuse (site qui bloque, lien mort)."""
    try:
        return _download(url, dest_dir, title, kind)
    except (SearchError, OSError, ValueError):
        if not fallback:
            raise
        return _download(fallback, dest_dir, title, kind)


def _download(url: str, dest_dir: str, title: str = "", kind: str = "") -> tuple[str, str]:
    if not re.match(r"^https?://", url or ""):
        raise SearchError(f"Not a web address: {url!r}")
    limit = MAX_VIDEO if kind == "video" or re.search(r"\.(mp4|mov|webm)(\?|$)", url, re.I) else MAX_IMAGE
    os.makedirs(dest_dir, exist_ok=True)
    tmp = os.path.join(dest_dir, "_download.part")
    with _get(url, {"User-Agent": BROWSER_UA, "Accept": "image/avif,image/webp,image/png,image/*,video/*,*/*"},
              timeout=60) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = _EXT.get(ctype) or os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in _EXT.values():
            raise SearchError(f"Unsupported file type {ctype or ext!r} at {url}")
        got = 0
        with open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                got += len(chunk)
                if got > limit:
                    f.close()
                    os.remove(tmp)
                    raise SearchError(f"File too large (over {limit // (1024 * 1024)} MB): {url}")
                f.write(chunk)
    path = os.path.join(dest_dir, "source" + ext)
    os.replace(tmp, path)
    return path, _name_from(title, url, ext)
