"""Détourage d'une personne (ou d'un sujet) image par image, choisi d'un clic.

Modèle : MODNet (« Real-Time Trimap-Free Portrait Matting », licence
Apache-2.0), conversion ONNX `Xenova/modnet` (26 Mo), exécutée par ONNX
Runtime sur le processeur. Il donne pour chaque image un masque doux (0..1)
de l'avant-plan : la ou les personnes, et ce qu'elles tiennent.

Choisir le sujet : le clic désigne, parmi les zones d'avant-plan séparées
(deux personnes, par exemple), celle à garder. Une première passe rapide (deux
images par seconde) suit sa position dans toute la vidéo, en partant de
l'instant du clic vers l'avant et vers l'arrière ; la passe complète garde,
à chaque image, la zone la plus proche de cette trajectoire.

Sorties (même définition et même cadence que le proxy, temps 0 = début) :
    matte.mp4    le masque en niveaux de gris : l'export l'agrandit à la taille
                 de la source et s'en sert comme couche alpha ;
    cutout.webm  le proxy détouré (VP9 avec transparence, son compris) : lu
                 tel quel par l'aperçu ;
    et la trajectoire du sujet (centre et taille, 10 fois par seconde,
    lissée), pour le cadrer ou le suivre.

Une image fixe donne matte.png et cutout.png.
"""
from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

REPO = "Xenova/modnet"
FILE = "onnx/model.onnx"
SHORT = 320               # petit côté de l'image donnée au modèle (multiple de 32)
SAMPLE_FPS = 2.0          # première passe : suivi du sujet
TRACK_HZ = 10             # trajectoire enregistrée
MIN_AREA = 0.012          # une zone plus petite (fraction de l'image) n'est pas un sujet

DL = {"status": "idle", "message": ""}


class Cancelled(RuntimeError):
    pass


# ------------------------------------------------------------------ modèle

def model_path() -> str | None:
    """Fichier du modèle s'il est là (livré avec l'application ou en cache)."""
    bundled = os.environ.get("MONTAGE_IA_MATTING")
    if bundled and os.path.isfile(os.path.join(bundled, "modnet.onnx")):
        return os.path.join(bundled, "modnet.onnx")
    try:
        from huggingface_hub import hf_hub_download
        return hf_hub_download(REPO, FILE, local_files_only=True)
    except Exception:  # noqa: BLE001 - absent du cache
        return None


def download() -> str:
    """Télécharge le modèle (26 Mo) dans le cache HuggingFace, même hors ligne
    par défaut (`HF_HUB_OFFLINE`, posé par app.py quand Whisper est déjà là)."""
    from huggingface_hub import constants, hf_hub_download
    offline = constants.HF_HUB_OFFLINE
    constants.HF_HUB_OFFLINE = False
    try:
        return hf_hub_download(REPO, FILE)
    finally:
        constants.HF_HUB_OFFLINE = offline


def download_async() -> None:
    if DL["status"] == "running":
        return

    def run() -> None:
        DL.update(status="running", message="")
        try:
            download()
            DL.update(status="done")
        except Exception as exc:  # noqa: BLE001 - réseau, disque
            DL.update(status="error", message=str(exc)[:300])

    threading.Thread(target=run, daemon=True, name="modnet-dl").start()


def info() -> dict:
    return {"available": model_path() is not None, "download": dict(DL), "size_mb": 26}


class Matter:
    """Session ONNX partagée : plusieurs images calculées en parallèle."""

    _lock = threading.Lock()
    _cache: dict[str, "Matter"] = {}

    def __init__(self, path: str) -> None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 3
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.workers = max(1, min(6, (os.cpu_count() or 4) // 3 - 1))

    @classmethod
    def get(cls) -> "Matter":
        path = model_path()
        if not path:
            raise RuntimeError("Modèle de détourage absent : télécharge-le d'abord.")
        with cls._lock:
            if path not in cls._cache:
                cls._cache[path] = cls(path)
            return cls._cache[path]

    def matte(self, rgb: np.ndarray) -> np.ndarray:
        """Masque (float32 0..1) à la taille de l'image `rgb` (H, W, 3, uint8)."""
        import cv2
        h, w = rgb.shape[:2]
        k = SHORT / min(h, w)
        mh, mw = max(32, int(round(h * k / 32)) * 32), max(32, int(round(w * k / 32)) * 32)
        x = cv2.resize(rgb, (mw, mh), interpolation=cv2.INTER_AREA).astype(np.float32) / 127.5 - 1.0
        out = self.session.run(None, {"input": x.transpose(2, 0, 1)[None]})[0][0, 0]
        return cv2.resize(np.clip(out, 0, 1), (w, h), interpolation=cv2.INTER_LINEAR)


# ------------------------------------------------------------ choix du sujet

def _components(m: np.ndarray):
    """Zones d'avant-plan : (étiquettes, [(n°, aire, centre x, centre y, boîte)])."""
    import cv2
    b = (m > 0.5).astype(np.uint8)
    n, labels, stats, cent = cv2.connectedComponentsWithStats(b, connectivity=8)
    h, w = m.shape
    comps = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA] / float(h * w)
        if area < MIN_AREA:
            continue
        x0, y0 = stats[i, cv2.CC_STAT_LEFT] / w, stats[i, cv2.CC_STAT_TOP] / h
        bw, bh = stats[i, cv2.CC_STAT_WIDTH] / w, stats[i, cv2.CC_STAT_HEIGHT] / h
        comps.append((i, area, cent[i][0] / w, cent[i][1] / h, (x0, y0, bw, bh)))
    return labels, comps


def _pick(labels, comps, p: tuple[float, float]):
    """Zone désignée par le point `p` (0..1) : celle qui le contient, sinon la plus proche."""
    if not comps:
        return None
    h, w = labels.shape
    px, py = min(w - 1, max(0, int(p[0] * w))), min(h - 1, max(0, int(p[1] * h)))
    hit = labels[py, px]
    for c in comps:
        if c[0] == hit:
            return c

    def dist(c):
        x0, y0, bw, bh = c[4]
        dx = max(x0 - p[0], 0, p[0] - (x0 + bw))
        dy = max(y0 - p[1], 0, p[1] - (y0 + bh))
        return dx * dx + dy * dy
    return min(comps, key=dist)


def keep_subject(m: np.ndarray, p: tuple[float, float]) -> tuple[np.ndarray, tuple | None]:
    """Masque réduit au sujet désigné par `p`, et sa boîte (x, y, w, h en 0..1)."""
    import cv2
    labels, comps = _components(m)
    if len(comps) <= 1:
        if not comps:
            return m * 0, None
        return m, comps[0][4]
    c = _pick(labels, comps, p)
    keep = (labels == c[0]).astype(np.uint8)
    # bord doux : la zone gardée est élargie un peu pour ne pas rogner les cheveux
    r = max(3, int(min(m.shape) * 0.012))
    keep = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    keep = cv2.GaussianBlur(keep.astype(np.float32), (0, 0), r / 2)
    return m * np.clip(keep, 0, 1), c[4]


def refine(m: np.ndarray) -> np.ndarray:
    """Resserre le masque : les zones incertaines (ombres, meubles sombres
    contre un vêtement sombre) disparaissent, les bords restent doux."""
    return np.clip((m - 0.2) / 0.6, 0.0, 1.0)


# ---------------------------------------------------------------- vidéo

def _reader(path: str, w: int, h: int, fps: float | None = None):
    """Images RGB d'une vidéo (à la cadence `fps` si donnée)."""
    vf = [f"scale={w}:{h}"] + ([f"fps={fps}"] if fps else [])
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-an",
                             "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.kill()
        proc.wait()


def _track(matter: Matter, path: str, w: int, h: int, t0: float, point: tuple[float, float]):
    """Première passe : position du sujet toutes les 1/SAMPLE_FPS secondes."""
    samples = []
    for i, rgb in enumerate(_reader(path, w, h, SAMPLE_FPS)):
        labels, comps = _components(matter.matte(rgb))
        samples.append((i / SAMPLE_FPS, labels, comps))
    if not samples:
        return []
    k0 = min(range(len(samples)), key=lambda i: abs(samples[i][0] - t0))
    pos: dict[int, tuple[float, float]] = {}
    c = _pick(samples[k0][1], samples[k0][2], point)
    pos[k0] = (c[2], c[3]) if c else point
    for step in (1, -1):
        prev = pos[k0]
        k = k0 + step
        while 0 <= k < len(samples):
            c = _pick(samples[k][1], samples[k][2], prev)
            if c:
                prev = (c[2], c[3])
            pos[k] = prev
            k += step
    return [(samples[k][0], *pos[k]) for k in sorted(pos)]


def _at(track, t: float) -> tuple[float, float]:
    if not track:
        return (0.5, 0.5)
    if t <= track[0][0]:
        return track[0][1:]
    for a, b in zip(track, track[1:]):
        if t <= b[0]:
            u = (t - a[0]) / (b[0] - a[0] or 1)
            return (a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u)
    return track[-1][1:]


def _smooth(boxes: list[tuple], window: float) -> list[list[float]]:
    """Trajectoire du sujet à TRACK_HZ, lissée (moyenne glissante sur `window` s)."""
    if not boxes:
        return []
    t_end = boxes[-1][0]
    out = []
    arr = np.array([b for b in boxes if b[1] is not None] or [[0, 0.5, 0.5, 0, 0]], dtype=float)
    for k in range(int(t_end * TRACK_HZ) + 1):
        t = k / TRACK_HZ
        sel = arr[np.abs(arr[:, 0] - t) <= window / 2]
        if not len(sel):
            sel = arr[[int(np.argmin(np.abs(arr[:, 0] - t)))]]
        cx, cy, bw, bh = (float(v) for v in sel[:, 1:].mean(axis=0))
        out.append([round(t, 2), round(cx, 4), round(cy, 4), round(bw, 4), round(bh, 4)])
    return out


def process_video(path: str, info: dict, point: tuple[float, float], t0: float, out_dir: str,
                  on_progress=None, cancel: threading.Event | None = None, cutout: bool = True) -> dict:
    """Détoure le sujet d'une vidéo (le proxy) ; écrit matte.mp4 et, avec
    `cutout`, cutout.webm dans `out_dir`. Renvoie la trajectoire lissée du sujet."""
    matter = Matter.get()
    w, h = int(info["w"]), int(info["h"])
    w, h = w - w % 2, h - h % 2
    fps = float(info.get("fps") or 30)
    total = max(1, int(float(info.get("duration") or 0) * fps))
    track = _track(matter, path, w, h, t0, point)
    if on_progress:
        on_progress(0.05)

    matte_tmp = os.path.join(out_dir, "tmp_matte.mp4")
    cut_tmp = os.path.join(out_dir, "tmp_cutout.webm")
    enc_m = subprocess.Popen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-",
                              "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
                              "-movflags", "+faststart", matte_tmp],
                             stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    audio = ["-i", path, "-map", "0:v", "-map", "1:a?", "-c:a", "libopus", "-b:a", "96k"] if info.get("has_audio") \
        else ["-map", "0:v"]
    enc_c = subprocess.Popen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                              "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-", *audio,
                              "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "34",
                              "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0",
                              "-shortest", cut_tmp],
                             stdin=subprocess.PIPE, stderr=subprocess.PIPE) if cutout else None
    encoders = [e for e in (enc_m, enc_c) if e]
    boxes: list[tuple] = []
    prev = None
    try:
        with ThreadPoolExecutor(matter.workers) as pool:
            pending: list = []
            frames = _reader(path, w, h)

            def flush(n_keep: int) -> None:
                nonlocal prev
                while len(pending) > n_keep:
                    k, rgb, fut = pending.pop(0)
                    t = k / fps
                    m, box = keep_subject(refine(fut.result()), _at(track, t))
                    # un peu de lissage dans le temps : moins de scintillement des bords
                    m = m if prev is None else 0.8 * m + 0.2 * prev
                    prev = m
                    a = (np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8)
                    enc_m.stdin.write(a.tobytes())
                    if enc_c:
                        enc_c.stdin.write(np.dstack([rgb, a]).tobytes())
                    boxes.append((t, *(((box[0] + box[2] / 2), (box[1] + box[3] / 2), box[2], box[3])
                                       if box else (None, None, None, None))))
                    if on_progress and k % 10 == 0:
                        on_progress(0.05 + 0.93 * min(1.0, k / total))

            for k, rgb in enumerate(frames):
                if cancel is not None and cancel.is_set():
                    raise Cancelled("Détourage annulé.")
                pending.append((k, rgb, pool.submit(matter.matte, rgb)))
                flush(matter.workers * 2)
            flush(0)
        for enc in encoders:
            enc.stdin.close()
            if enc.wait() != 0:
                raise RuntimeError("encodage du détourage : " + enc.stderr.read().decode("utf-8", "replace")[-300:])
    except BaseException:
        for enc in encoders:
            enc.kill()
        for f in (matte_tmp, cut_tmp):
            try:
                os.remove(f)
            except OSError:
                pass
        raise
    os.replace(matte_tmp, os.path.join(out_dir, "matte.mp4"))
    if enc_c:
        os.replace(cut_tmp, os.path.join(out_dir, "cutout.webm"))
    return {"track": _smooth([b for b in boxes if b[1] is not None], 0.8)}


def process_image(path: str, point: tuple[float, float], out_dir: str) -> dict:
    """Détoure une image fixe : matte.png et cutout.png dans `out_dir`."""
    from PIL import Image
    img = Image.open(path).convert("RGB")
    rgb = np.asarray(img)
    m, box = keep_subject(refine(Matter.get().matte(rgb)), point)
    a = (np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8)
    Image.fromarray(a, "L").save(os.path.join(out_dir, "matte.png"))
    Image.fromarray(np.dstack([rgb, a]), "RGBA").save(os.path.join(out_dir, "cutout.png"))
    track = [[0.0, round(float(box[0] + box[2] / 2), 4), round(float(box[1] + box[3] / 2), 4),
              round(float(box[2]), 4), round(float(box[3]), 4)]] if box else []
    return {"track": track}
