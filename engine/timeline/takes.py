"""Dérush par prises : les passages parlés d'un rush, et ce qu'un monteur en garde.

Un face caméra se tourne en prises : une phrase, un silence, la phrase redite
parce qu'elle a accroché, un faux départ… Ce module :

  1. mesure le rush (niveau toutes les 10 ms, `sound.envelope`) : bruit de fond
     et niveau de voix donnent les seuils — jamais un nombre de dB fixe, une
     prise au téléphone et une prise au micro n'ont pas le même fond ;
  2. découpe les prises entre les silences, bornées sur la forme d'onde (la
     première et la dernière syllabe entières). Deux prises voisines ne se
     chevauchent jamais : la jonction tombe au creux du son entre elles, sinon
     le montage rejouerait un bout de phrase (« déjà… déjà ») ;
  3. range les mots dans leur prise (transcription du fichier entier, ou
     mieux, prise par prise : `transcribe(..., clips=...)`) ;
  4. compare chaque prise à la suivante et propose : garder, écarter
     (reprise, faux départ, prise contenue dans la suivante) ou raccourcir (la
     fin redite au début de la suivante). Chaque décision a sa raison : c'est
     la liste que le monteur (ou l'agent) relit, jamais une vérité.

Tout est en secondes du média source.
"""
from __future__ import annotations

import os
import re
import subprocess
import unicodedata

import numpy as np

RATE = 100                 # niveaux par seconde (une trame = 10 ms)
MIN_SILENCE = 0.35         # s : un silence plus court ne sépare pas deux prises
HEAD = 0.05                # s d'air gardés avant la première syllabe
TAIL = 0.10                # s d'air gardés après la dernière
MIN_TAKE = 0.35            # s : en dessous, un bruit, pas une prise
SEAM = 0.02                # s d'écart minimal entre deux prises


VERSION = 1               # cache des mesures (media/<id>/takes_level.npz)


def measure(path: str, rate: int = RATE) -> dict[str, np.ndarray]:
    """Niveaux du rush toutes les 10 ms, coupe-bas 120 Hz (le grondement ne
    compte pas, les consonnes voisées « l », « m », « n » oui) : `rms` (dB,
    énergie) pour les seuils et les bornes des syllabes, `peak` (dB, crête)
    pour les silences — un silence, c'est aucun échantillon qui dépasse."""
    sr = 16000
    res = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                          "-af", "highpass=f=120", "-f", "s16le", "-c:a", "pcm_s16le", "-"],
                         capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    if res.returncode != 0:
        raise RuntimeError("Reading the sound failed: " + res.stderr.decode("utf-8", "replace")[-300:])
    x = np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768.0
    hop = sr // rate
    n = len(x) // hop
    if n == 0:
        return {"rms": np.zeros(0, np.float32), "peak": np.zeros(0, np.float32)}
    frames = x[: n * hop].reshape(n, hop)
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(1) + 1e-12) + 1e-9)
    peak = 20 * np.log10(np.abs(frames).max(1) + 1e-9)
    return {"rms": rms.astype(np.float32), "peak": peak.astype(np.float32)}


def media_levels(proj, m: dict) -> dict[str, np.ndarray]:
    """`measure` d'un média du projet, en cache à côté de ses autres analyses."""
    path = os.path.join(proj.media_folder(m["id"]), "takes_level.npz")
    try:
        stamp = f"{os.path.getmtime(m['path'])}:{os.path.getsize(m['path'])}:{VERSION}"
    except OSError:
        stamp = ""
    try:
        with np.load(path) as z:
            if stamp and str(z["stamp"]) == stamp:
                return {"rms": z["rms"], "peak": z["peak"]}
    except (OSError, ValueError, KeyError):
        pass
    lv = measure(m["path"])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez(path, rms=lv["rms"], peak=lv["peak"], stamp=np.array(stamp))
    except OSError:
        pass
    return lv


def thresholds(env: np.ndarray) -> dict:
    """Seuils mesurés : bruit de fond (5e centile), voix (80e), seuil de
    silence à mi-chemin, seuil de voix pour borner les syllabes."""
    floor = float(np.percentile(env, 5))
    speech = float(np.percentile(env, 80))
    return {"floor": round(floor, 1), "speech": round(speech, 1),
            "silence": round((floor + speech) / 2, 1),
            # une prise discrète (voix 16 dB au-dessus du fond) doit encore le franchir
            "voice": round(floor + min(18.0, 0.7 * (speech - floor)), 1)}


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Plages [a, b) où `mask` est vrai."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def split_takes(env: np.ndarray, peak: np.ndarray | None = None, rate: int = RATE,
                min_silence: float = MIN_SILENCE) -> tuple[list[dict], dict]:
    """Prises d'un rush d'après ses niveaux (`measure`) : [{in, out}] (s) + seuils.
    `env` : niveau RMS ; `peak` : niveau crête (silences), l'un ou l'autre sinon."""
    n = len(env)
    if n == 0:
        return [], {}
    th = thresholds(env)
    loud = (peak if peak is not None and len(peak) == n else env) > th["silence"]
    # parole = tout ce qui n'est pas un silence assez long
    speech = np.ones(n, bool)
    for a, b in _runs(~loud):
        if (b - a) / rate >= min_silence:
            speech[a:b] = False
    voice = env > th["voice"]
    runs = [(a, b) for a, b in _runs(speech) if (b - a) / rate >= 0.1]
    takes = []
    for i, (a, b) in enumerate(runs):
        # bornes cherchées DANS la plage et le silence qui l'entoure, jamais
        # chez la prise voisine
        lo = runs[i - 1][1] if i else 0
        hi = runs[i + 1][0] if i + 1 < len(runs) else n
        on = _onset(voice, max(lo, a - int(0.3 * rate)), b)
        off = _offset(voice, a, min(hi, b + int(0.3 * rate)))
        if on is None or off is None or (off - on) / rate < MIN_TAKE:
            continue
        takes.append({"in": max(0.0, on / rate - HEAD), "out": min(n / rate, off / rate + TAIL),
                      "_on": on / rate, "_off": off / rate})
    _separate(takes, env, rate)
    for t in takes:
        t["in"], t["out"] = round(t["in"], 2), round(t["out"], 2)
        del t["_on"], t["_off"]
    return takes, th


def _onset(voice: np.ndarray, a: int, b: int) -> int | None:
    """Première trame d'au moins 3 trames de voix d'affilée dans [a, b)."""
    for i in range(max(0, a), max(0, b - 2)):
        if voice[i] and voice[i + 1] and voice[i + 2]:
            return i
    return None


def _offset(voice: np.ndarray, a: int, b: int) -> int | None:
    """Fin (exclue) de la dernière voix d'au moins 3 trames dans [a, b)."""
    for i in range(min(len(voice), b) - 1, a + 1, -1):
        if voice[i] and voice[i - 1] and voice[i - 2]:
            return i + 1
    return None


def _separate(takes: list[dict], env: np.ndarray, rate: int) -> None:
    """Deux prises voisines ne se chevauchent pas : leur jonction tombe au
    point le plus calme entre la fin de l'une et le début de l'autre."""
    for a, b in zip(takes, takes[1:]):
        if a["out"] <= b["in"] - SEAM:
            continue
        lo, hi = int(a["_off"] * rate), int(b["_on"] * rate)
        if hi > lo:
            k = lo + int(np.argmin(env[lo:hi]))
            seam = k / rate
        else:
            seam = (a["_off"] + b["_on"]) / 2
        a["out"] = max(a["_off"], min(a["out"], seam - SEAM / 2))
        b["in"] = min(b["_on"], max(b["in"], seam + SEAM / 2))
        if a["out"] > b["in"] - SEAM:           # voix collées : on coupe pile au creux
            a["out"], b["in"] = seam - SEAM / 2, seam + SEAM / 2


def split_on_words(words: list[dict], env: np.ndarray, rate: int = RATE, gap: float = 0.6) -> list[dict]:
    """Repli pour une piste où la voix ne redescend jamais au niveau du fond
    (fond bruyant, gain automatique de la caméra) : les prises se découpent aux
    pauses entre les mots."""
    groups: list[list[dict]] = []
    for w in words:
        if groups and w["start"] - groups[-1][-1]["end"] < gap:
            groups[-1].append(w)
        else:
            groups.append([w])
    dur = len(env) / rate if len(env) else (words[-1]["end"] + 1 if words else 0.0)
    takes = [{"in": max(0.0, g[0]["start"] - HEAD), "out": min(dur, g[-1]["end"] + TAIL),
              "_on": g[0]["start"], "_off": g[-1]["end"]} for g in groups]
    _separate(takes, env, rate)
    for t in takes:
        t["in"], t["out"] = round(t["in"], 2), round(t["out"], 2)
        del t["_on"], t["_off"]
    return takes


# ------------------------------------------------------------ comparaison

def _norm(text: str) -> list[str]:
    """Mots comparables : minuscules, sans accents ni ponctuation, pluriels simples."""
    s = unicodedata.normalize("NFD", str(text).lower().replace("’", "'"))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9' ]", " ", s)
    return [w.rstrip("s") if len(w) > 3 else w for w in s.split()]


def _ngrams(ws: list[str], k: int = 3) -> set[tuple]:
    return {tuple(ws[i:i + k]) for i in range(len(ws) - k + 1)}


def assign_words(takes: list[dict], words: list[dict], reach: float = 0.6) -> None:
    """Range chaque mot (`words` : text/start/end) dans la prise qui contient son
    milieu, sinon la plus proche (à `reach` s près) : les dates de Whisper
    débordent souvent d'un dixième sur le silence."""
    for t in takes:
        t["words"] = []
    for w in sorted(words, key=lambda w: w["start"]):
        mid = (w["start"] + w["end"]) / 2
        best, dist = None, reach
        for t in takes:
            d = 0.0 if t["in"] <= mid < t["out"] else min(abs(mid - t["in"]), abs(mid - t["out"]))
            if d < dist or (d == 0.0 and best is None):
                best, dist = t, d
            if d == 0.0:
                break
        if best is not None:
            best["words"].append({"text": w["text"], "start": w["start"], "end": w["end"]})
    for t in takes:
        t["text"] = " ".join(w["text"] for w in t["words"])


def decide(takes: list[dict]) -> list[dict]:
    """Propose une décision par prise (voir la docstring du module). Écrit
    `keep`, `why` et, pour une fin redite, `trim_out` (s)."""
    for t in takes:
        t["keep"], t["why"] = True, ""
        t.pop("trim_out", None)
        t["_n"] = _norm(t.get("text", ""))
    for a, b in zip(takes, takes[1:]):
        wa, wb = a["_n"], b["_n"]
        if not wa:
            a["keep"], a["why"] = False, "no speech"
            continue
        if not wb:
            continue
        if len(wa) <= 3 and all(w in wb for w in wa):
            a["keep"], a["why"] = False, "false start (redone in the next take)"
            continue
        if " ".join(wa) in " ".join(wb):
            a["keep"], a["why"] = False, "contained in the next take"
            continue
        shared = _ngrams(wa) & _ngrams(wb)
        if shared and abs(len(wa) - len(wb)) <= 4 and len(shared) >= max(1, len(_ngrams(wa)) // 3):
            a["keep"], a["why"] = False, "retake (the next one says it again)"
            continue
        for k in (3, 2):
            if len(wa) > k and wa[-k:] == wb[:k] and len(a["words"]) > k:
                cut = a["words"][len(a["words"]) - k]["start"]
                a["trim_out"] = round(max(a["in"] + 0.1, cut - 0.05), 2)
                said = " ".join(w["text"] for w in a["words"][-k:])
                a["why"] = f"ends with « {said} », said again at the start of the next take: end trimmed"
                break
    if takes and not takes[-1]["_n"]:
        takes[-1]["keep"], takes[-1]["why"] = False, "no speech"
    for t in takes:
        del t["_n"]
    return takes


def derush(levels: dict, words: list[dict], rate: int = RATE, takes: list[dict] | None = None) -> dict:
    """Prises, mots rangés et décisions proposées : le dérush complet d'un rush.
    `levels` : `measure` ; `takes` : prises déjà découpées (sinon mesurées)."""
    env = levels["rms"]
    dur = len(env) / rate
    th = thresholds(env) if len(env) else {}
    method = "level"
    if takes is None:
        takes, th = split_takes(env, levels.get("peak"), rate)
        if len(takes) <= 1 and dur > 30 and words:
            takes = split_on_words(words, env, rate)
            method = "word gaps"
    takes = [{"in": t["in"], "out": t["out"]} for t in takes]
    assign_words(takes, words)
    decide(takes)
    for i, t in enumerate(takes, 1):
        t["n"] = i
    kept = [t for t in takes if t["keep"]]
    return {"takes": takes, "thresholds": th, "method": method, "duration": round(dur, 2),
            "kept_seconds": round(sum(t.get("trim_out", t["out"]) - t["in"] for t in kept), 2)}


def kept_ranges(takes: list[dict], numbers=None) -> list[tuple[float, float]]:
    """Plages source à monter : les prises gardées (ou celles de `numbers`, dans
    cet ordre), raccourcies quand leur fin est redite. Pour une prise entière
    malgré tout, l'agent donne ses bornes (start/end) lui-même."""
    by_n = {t["n"]: t for t in takes}
    chosen = [by_n[n] for n in numbers if n in by_n] if numbers else [t for t in takes if t["keep"]]
    return [(t["in"], t.get("trim_out", t["out"])) for t in chosen]
