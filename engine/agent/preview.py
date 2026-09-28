"""Aperçu du montage pour l'agent : rendu basse définition, planche d'images.

L'agent ne voit pas l'écran du studio : il demande un aperçu. Le montage est
rendu par le même chemin que l'export (donc exactement ce qui sortira), en
petite définition et à cadence réduite, une fois par révision ; les images
demandées en sont extraites et assemblées en planche, chaque case portant son
instant. Le fichier d'aperçu reste lisible par l'utilisateur.
"""
from __future__ import annotations

import copy
import glob
import os
import subprocess
import threading
import time

PREVIEWS: dict[str, dict] = {}
_LOCK = threading.Lock()


def folder(proj) -> str:
    d = os.path.join(proj.dir, "agent")
    os.makedirs(d, exist_ok=True)
    return d


def _path(proj, rev: int, height: int) -> str:
    return os.path.join(folder(proj), f"preview_r{rev}_{height}p.mp4")


def status(proj) -> dict:
    p = PREVIEWS.get(proj.id)
    if p and p.get("rev") == proj.rev:
        return dict(p)
    for height in (480, 360, 720):
        path = _path(proj, proj.rev, height)
        if os.path.isfile(path):
            return {"status": "done", "rev": proj.rev, "path": path, "height": height, "pct": 100}
    return {"status": "none", "rev": proj.rev} if not p else {**p, "stale": True}


def start(proj, height: int = 480, fps: int = 15) -> dict:
    """Lance le rendu de l'aperçu de la révision actuelle (s'il n'existe pas)."""
    from engine.timeline import render
    with _LOCK:
        cur = status(proj)
        # un aperçu d'une révision passée (même terminé) ne compte plus : on refait
        if not cur.get("stale") and cur.get("status") in ("done", "running"):
            return cur
        with proj.lock:
            snap = copy.deepcopy(proj.state)
            rev = proj.rev
        if not snap["clips"]:
            raise ValueError("The timeline is empty: nothing to preview.")
        out = _path(proj, rev, height)
        job = {"status": "running", "rev": rev, "path": out, "height": height, "pct": 0, "started": time.time()}
        PREVIEWS[proj.id] = job

    def run() -> None:
        try:
            res = render.export(snap, snap["media"], out, resolution=f"{height}p", fps=fps, quality="low",
                                on_progress=lambda f: job.update(pct=round(100 * f, 1)))
            job.update(status="done", pct=100, duration=res["duration"], width=res["width"],
                       height_px=res["height"], seconds=round(time.time() - job["started"], 1))
            for old in glob.glob(os.path.join(folder(proj), "preview_r*.mp4")):
                if os.path.abspath(old) != os.path.abspath(out):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001 - remonté à l'agent
            job.update(status="error", message=str(exc)[:1200])
    threading.Thread(target=run, name="agent-preview", daemon=True).start()
    return dict(job)


def _grab(video: str, t: float, out: str) -> bool:
    res = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}",
                          "-i", video, "-frames:v", "1", "-q:v", "3", out], capture_output=True)
    return res.returncode == 0 and os.path.isfile(out)


def sheet(proj, times: list[float] | None = None, count: int = 9, cols: int = 3,
          cell_h: int = 420) -> dict:
    """Planche d'images de l'aperçu à jour : instants demandés, sinon `count`
    instants répartis sur le montage."""
    from PIL import Image, ImageDraw, ImageFont
    st = status(proj)
    if st.get("status") != "done" or st.get("stale"):
        raise LookupError("preview")
    video = st["path"]
    dur = float(st.get("duration") or _probe(video))
    if not times:
        n = max(1, min(24, int(count or 9)))
        times = [round(dur * (i + 0.5) / n, 2) for i in range(n)]
    times = [min(max(0.0, float(t)), max(0.0, dur - 0.05)) for t in times][:24]
    shots = []
    tmp = folder(proj)
    for i, t in enumerate(times):
        f = os.path.join(tmp, f"_frame{i}.jpg")
        if _grab(video, t, f):
            with Image.open(f) as im:
                shots.append((t, im.convert("RGB").copy()))
            os.remove(f)
    if not shots:
        raise RuntimeError("No image could be read from the preview.")
    w0, h0 = shots[0][1].size
    k = min(1.0, cell_h / h0) if len(shots) > 1 else min(1.0, 960 / max(w0, h0))
    cw, ch = int(w0 * k), int(h0 * k)
    cols = max(1, min(cols, len(shots)))
    rows = (len(shots) + cols - 1) // cols
    label = 30
    board = Image.new("RGB", (cols * (cw + 6) + 6, rows * (ch + label + 6) + 6), (20, 20, 24))
    draw = ImageDraw.Draw(board)
    try:
        font = ImageFont.truetype("arialbd.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    for i, (t, im) in enumerate(shots):
        x0 = 6 + (i % cols) * (cw + 6)
        y0 = 6 + (i // cols) * (ch + label + 6)
        board.paste(im.resize((cw, ch)), (x0, y0 + label))
        draw.text((x0 + 4, y0 + 4), f"{t:.2f} s", fill=(255, 212, 0), font=font)
    out = os.path.join(tmp, "storyboard.jpg")
    board.save(out, "JPEG", quality=84)
    return {"path": out, "times": [t for t, _ in shots], "video": video, "rev": st["rev"], "duration": dur}


def source_sheet(proj, m: dict, times: list[float] | None = None, count: int = 6, cols: int = 3,
                 cell_h: int = 480) -> dict:
    """Images d'un média SOURCE avec une grille (0..1) : l'agent y lit où est
    la personne (point de détourage, cadrage) et ce que montre le plan."""
    from PIL import Image, ImageDraw, ImageFont
    folder_m = proj.media_folder(m["id"])
    src = os.path.join(folder_m, m.get("proxy_file") or "")
    if not os.path.isfile(src):
        src = m["path"]
    dur = float(m.get("duration") or 0)
    if m["kind"] == "image":
        times = [0.0]
    elif not times:
        n = max(1, min(12, int(count or 6)))
        times = [round(dur * (i + 0.5) / n, 2) for i in range(n)]
    times = [min(max(0.0, float(t)), max(0.0, dur - 0.05)) for t in times][:12]
    try:
        font = ImageFont.truetype("arialbd.ttf", 18)
        small = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = small = ImageFont.load_default()
    shots = []
    tmp = folder(proj)
    for i, t in enumerate(times):
        f = os.path.join(tmp, f"_src{i}.jpg")
        if m["kind"] == "image":
            im = Image.open(src).convert("RGB")
        elif _grab(src, t, f):
            with Image.open(f) as g:
                im = g.convert("RGB").copy()
            os.remove(f)
        else:
            continue
        k = cell_h / im.height
        im = im.resize((max(1, int(im.width * k)), cell_h))
        d = ImageDraw.Draw(im, "RGBA")
        for j in range(1, 10):
            x, y = im.width * j / 10, im.height * j / 10
            strong = j % 5 == 0
            col = (255, 212, 0, 170) if strong else (255, 255, 255, 70)
            d.line([(x, 0), (x, im.height)], fill=col, width=1)
            d.line([(0, y), (im.width, y)], fill=col, width=1)
            if j % 2 == 0:
                d.text((x + 2, 2), f".{j}", fill=(255, 212, 0, 230), font=small)
                d.text((2, y + 1), f".{j}", fill=(255, 212, 0, 230), font=small)
        shots.append((t, im))
    if not shots:
        raise RuntimeError("No image could be read from this media.")
    cw, ch = shots[0][1].size
    cols = max(1, min(cols, len(shots)))
    rows = (len(shots) + cols - 1) // cols
    label = 28
    board = Image.new("RGB", (cols * (cw + 6) + 6, rows * (ch + label + 6) + 6), (20, 20, 24))
    draw = ImageDraw.Draw(board)
    for i, (t, im) in enumerate(shots):
        x0 = 6 + (i % cols) * (cw + 6)
        y0 = 6 + (i // cols) * (ch + label + 6)
        board.paste(im, (x0, y0 + label))
        draw.text((x0 + 4, y0 + 4), f"{t:.2f} s (source)" if m["kind"] != "image" else "image", fill=(255, 212, 0),
                  font=font)
    out = os.path.join(tmp, "source_frames.jpg")
    board.save(out, "JPEG", quality=84)
    return {"path": out, "times": [t for t, _ in shots]}


def _probe(video: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=nw=1:nk=1", video], capture_output=True, text=True).stdout
        return float(out.strip() or 0)
    except (OSError, ValueError):
        return 0.0
