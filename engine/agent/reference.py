"""Étudier une vidéo de référence (« je veux un montage comme celui-là »).

L'agent regarde des planches d'images (une image toutes les ~0,8 s, avec leur
instant) et lit des mesures : rythme des plans (changements d'image détectés),
débit de parole et blancs laissés, place des coupes par rapport aux mots,
niveau sonore, présence d'une musique sous la voix. Il en tire un style à
reproduire : typographie, mise en page, rythme, animations, son.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess

from PIL import Image, ImageDraw, ImageFont

SCENE = 0.25             # seuil de changement d'image (ffmpeg « scene »)


def _probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), {})
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    return {"duration": round(float((data.get("format") or {}).get("duration") or 0), 2),
            "width": v.get("width"), "height": v.get("height"),
            "fps": round(float(num) / float(den or 1), 2) if float(den or 1) else 0,
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def _cuts(path: str) -> list[float]:
    res = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-map", "0:v:0", "-vf",
                          f"select='gt(scene,{SCENE})',showinfo", "-f", "null", "-"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return [round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", res.stderr)]


def _sheets(path: str, duration: float, folder: str, per_sheet: int = 20, cols: int = 10) -> list[str]:
    """Planches d'images horodatées (au plus 3), ~0,8 s entre deux images."""
    step = max(0.5, duration / (3 * per_sheet))
    times = [round(i * step, 2) for i in range(int(duration / step) + 1) if i * step < duration - 0.05]
    try:
        font = ImageFont.truetype("arialbd.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    out = []
    for s in range(0, len(times), per_sheet):
        chunk = times[s:s + per_sheet]
        frames = []
        for t in chunk:
            png = os.path.join(folder, f"_f{t:.2f}.jpg")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-vf",
                            "scale=180:-2", png], capture_output=True)
            if os.path.isfile(png):
                with Image.open(png) as im:
                    frames.append((t, im.convert("RGB").copy()))
                os.remove(png)
        if not frames:
            continue
        fw, fh = frames[0][1].size
        rows = (len(frames) + cols - 1) // cols
        board = Image.new("RGB", (cols * (fw + 4) + 4, rows * (fh + 4) + 4), (15, 15, 18))
        draw = ImageDraw.Draw(board)
        for i, (t, im) in enumerate(frames):
            x, y = 4 + (i % cols) * (fw + 4), 4 + (i // cols) * (fh + 4)
            board.paste(im.resize((fw, fh)), (x, y))
            draw.rectangle([x, y, x + 52, y + 19], fill=(0, 0, 0))
            draw.text((x + 3, y + 1), f"{t:.1f}s", fill=(255, 212, 0), font=font)
        p = os.path.join(folder, f"sheet_{len(out) + 1}.jpg")
        board.save(p, "JPEG", quality=82)
        out.append(p)
        if len(out) == 3:
            break
    return out


def _loudness(path: str) -> dict:
    res = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    grab = lambda pat: (re.findall(pat, res.stderr) or [None])[-1]  # noqa: E731
    return {"lufs": grab(r"I:\s+(-?[\d.]+) LUFS"), "lra": grab(r"LRA:\s+([\d.]+) LU"),
            "peak": grab(r"Peak:\s+(-?[\d.]+) dBFS")}


def study(path: str, work: str, transcribe=None, say=None) -> dict:
    """Mesures et planches d'une vidéo de référence (en cache par fichier)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}:{st.st_size}:{st.st_mtime}".encode()).hexdigest()[:12]
    folder = os.path.join(work, "agent", "references", key)
    cache = os.path.join(folder, "study.json")
    try:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        if all(os.path.isfile(p) for p in data["sheets"]):
            return data
    except (OSError, ValueError, KeyError):
        pass
    os.makedirs(folder, exist_ok=True)
    say and say("Mesure de l'image…")
    info = _probe(path)
    cuts = _cuts(path)
    shots = [b - a for a, b in zip([0.0] + cuts, cuts + [info["duration"]]) if b - a > 0.05]
    say and say("Planches d'images…")
    sheets = _sheets(path, info["duration"], folder)
    data = {"path": path, **info, "cuts": cuts, "sheets": sheets,
            "shots": {"count": len(shots), "median": round(statistics.median(shots), 2) if shots else None,
                      "mean": round(statistics.mean(shots), 2) if shots else None,
                      "shortest": round(min(shots), 2) if shots else None,
                      "longest": round(max(shots), 2) if shots else None}}
    if info["has_audio"]:
        data["loudness"] = _loudness(path)
        if transcribe:
            say and say("Transcription (débit, blancs)…")
            words = transcribe(path)
            data["words"] = len(words)
            if len(words) > 3:
                speech = words[-1]["end"] - words[0]["start"]
                gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
                data["speech"] = {"words_per_second": round(len(words) / max(0.1, speech), 2),
                                  "gap_median": round(statistics.median(gaps), 3),
                                  "gap_p90": round(sorted(gaps)[int(len(gaps) * 0.9)], 3),
                                  "gaps_over_300ms": sum(g > 0.3 for g in gaps),
                                  "starts_at": words[0]["start"], "ends_at": words[-1]["end"]}
                on_word = sum(1 for c in cuts if any(w["start"] - 0.05 <= c <= w["start"] + 0.15 for w in words))
                data["cuts_on_word_starts"] = on_word
                data["text"] = " ".join(w["text"] for w in words)
                from engine.timeline import sound
                a = sound.analyze(path, words)
                if a.get("speech"):
                    b = a.get("bands") or {}
                    data["voice"] = {"speech_db": a["speech_db"], "gap_level_db": a["noise_db"],
                                     "music_or_room_under_voice": a["noise_db"] > -50,
                                     "low_rumble_vs_voice_db": b.get("rumble")}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data
