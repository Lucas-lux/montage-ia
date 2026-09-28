"""« Optimiser le son » : mesurer chaque rush, puis régler son traitement.

Des réglages fixes ne vont pas à toutes les prises : une voix enregistrée
faiblement ne déclenche jamais le compresseur, un micro qui souffle garde son
bruit, une porte réglée trop haut mange les fins de mots. On mesure donc,
sur le son de chaque média :

    niveau de la voix     énergie moyenne des passages parlés (dBFS)
    bruit de fond         niveau médian hors paroles (dBFS), et l'écart voix/bruit
    dynamique             écart entre syllabes fortes et faibles (p90 - p10, dB)
    irrégularité          variation du niveau d'une phrase à l'autre (dB)
    spectre               grondement, corps, boue, présence, sifflantes, air (dB)
    crête                 niveau maximal et échantillons saturés

puis on en tire les réglages de `model.VOICE_FX` : un gain qui amène la voix
à un niveau de travail constant (les seuils de la chaîne deviennent justes pour
toutes les prises), une réduction de bruit à la mesure du bruit, une porte
calée sur le bruit mesuré, compression, de-esser, clarté et chaleur selon la
voix. La musique sous la voix est baissée à un niveau de fond, et l'export est
ramené à -14 LUFS.

Les paroles viennent de la transcription quand elle existe (mots datés), sinon
de l'énergie. Les mesures sont gardées en cache à côté du média.
"""
from __future__ import annotations

import json
import os
import subprocess

import numpy as np

SR = 32000                 # fréquence d'analyse : de quoi voir jusqu'à 16 kHz
HOP = 0.05                 # s : une mesure de niveau toutes les 50 ms
MAX_SECONDS = 1800         # au-delà, les 30 premières minutes suffisent
TARGET = -20.0             # dBFS : niveau de travail de la voix (avant compression)
MUSIC_UNDER = 17.0         # dB : la musique de fond sous la voix traitée
VERSION = 2                # à changer quand les mesures changent (cache)

BANDS = {"rumble": (20, 80), "body": (100, 300), "mud": (250, 500), "mid": (500, 2000),
         "presence": (2000, 5000), "sibilance": (5000, 9000), "air": (9000, 14000)}


# -------------------------------------------------------------- mesures

def _pcm(path: str, seconds: float = MAX_SECONDS) -> np.ndarray:
    """Son mono à SR, en flottants (-1..1)."""
    res = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-t",
                          str(seconds), "-f", "s16le", "-"], capture_output=True)
    if res.returncode != 0 and not res.stdout:
        raise RuntimeError("son illisible : " + res.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(res.stdout, "<i2").astype(np.float32) / 32768.0


def _db(v) -> np.ndarray | float:
    return 10.0 * np.log10(np.maximum(v, 1e-12))


def _mask(words: list[dict] | None, n: int, db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trames de paroles, et trames de « silence » (loin de tout mot)."""
    t = (np.arange(n) + 0.5) * HOP
    speech = np.zeros(n, bool)
    near = np.zeros(n, bool)
    if words:
        for w in words:
            a, b = float(w["start"]), float(w["end"])
            speech |= (t >= a) & (t <= b)
            near |= (t >= a - 0.25) & (t <= b + 0.25)
        # un mot très court entre deux blancs compte peu ; les trames vides de
        # vrai signal (fin de mot surestimée) ne sont pas de la voix
        speech &= db > np.percentile(db, 10) + 3
    else:
        floor = np.percentile(db, 15)
        speech = db > floor + 12
        grow = np.convolve(speech.astype(float), np.ones(9), "same") > 0
        near = grow
    return speech, ~near


def _bands(x: np.ndarray, speech: np.ndarray) -> dict:
    """Énergie de chaque bande (dB, par rapport au total), sur les paroles."""
    hop = int(HOP * SR)
    idx = np.flatnonzero(speech)
    if len(idx) > 1200:                             # une minute de parole suffit
        idx = idx[np.linspace(0, len(idx) - 1, 1200).astype(int)]
    chunks = [x[i * hop:(i + 1) * hop] for i in idx if (i + 1) * hop <= len(x)]
    if not chunks:
        return {k: -99.0 for k in BANDS}
    seg = np.stack(chunks) * np.hanning(hop)
    spec = (np.abs(np.fft.rfft(seg, axis=1)) ** 2).mean(0)
    freqs = np.fft.rfftfreq(hop, 1 / SR)
    total = spec[(freqs >= 60) & (freqs <= 14000)].sum() + 1e-12
    return {k: round(float(_db(spec[(freqs >= a) & (freqs < b)].sum() / total)), 2) for k, (a, b) in BANDS.items()}


def analyze(path: str, words: list[dict] | None = None) -> dict:
    """Mesures du son d'un fichier (voir le haut du module)."""
    x = _pcm(path)
    hop = int(HOP * SR)
    n = len(x) // hop
    if n < 10:
        return {"version": VERSION, "silent": True, "seconds": round(len(x) / SR, 2)}
    frames = x[: n * hop].reshape(n, hop)
    energy = (frames ** 2).mean(1)
    db = _db(energy)
    speech, quiet = _mask(words, n, db)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    out = {"version": VERSION, "seconds": round(len(x) / SR, 2), "peak_db": round(float(20 * np.log10(max(peak, 1e-6))), 2),
           "clipped": round(float(np.mean(np.abs(x) > 0.985)), 5)}
    if speech.sum() < 10:                          # moins d'une demi-seconde de voix : musique, ambiance
        live = db[db > np.percentile(db, 20)]
        out.update(silent=bool(np.max(db) < -60), speech=False,
                   level_db=round(float(_db(np.mean(10 ** (live / 10)))), 2) if len(live) else -99.0)
        return out
    speech_db = float(_db(np.mean(energy[speech])))
    noise_db = float(np.median(db[quiet])) if quiet.sum() >= 10 else float(np.percentile(db, 5))
    # dynamique : niveau « court terme » (fenêtres de 0,4 s surtout parlées),
    # écart p90 - p20 — comme la plage de loudness EBU, mais à l'échelle des syllabes
    k = 8
    short = [float(_db(np.mean(energy[i:i + k]))) for i in range(0, n - k + 1, k // 2)
             if speech[i:i + k].sum() >= k // 2]
    dyn = float(np.percentile(short, 90) - np.percentile(short, 20)) if len(short) >= 5 else 8.0
    # régularité d'une phrase à l'autre : niveau de la voix par fenêtres de 3 s
    win = int(3 / HOP)
    levels = [float(_db(np.mean(energy[i:i + win][speech[i:i + win]])))
              for i in range(0, n, win) if speech[i:i + win].sum() >= win // 4]
    out.update(speech=True, speech_db=round(speech_db, 2), noise_db=round(noise_db, 2),
               snr=round(speech_db - noise_db, 2), dyn=round(dyn, 2),
               drift=round(float(np.std(levels)) if len(levels) >= 3 else 0.0, 2),
               speech_seconds=round(float(speech.sum() * HOP), 1), bands=_bands(x, speech))
    return out


# ------------------------------------------------------------ réglages

def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def recommend(m: dict) -> dict:
    """Réglages `audio_fx` d'une voix, d'après ses mesures (`analyze`)."""
    if not m.get("speech"):
        return {}
    gain = round(_clamp(TARGET - m["speech_db"], -15.0, 24.0) * 2) / 2
    snr = m["snr"]
    fx: dict = {"lowcut": True, "preset": "auto"}
    if m.get("clipped", 0) > 1e-4:
        fx["declip"] = True                         # crêtes écrêtées à la prise (voix trop près du micro)
    if abs(gain) >= 1:
        fx["gain"] = gain
    denoise = 0.0 if snr >= 42 else 0.25 if snr >= 32 else 0.5 if snr >= 24 else 0.7 if snr >= 16 else 0.85
    if denoise:
        fx["denoise"] = denoise
    # bruit de fond après le gain (et à peu près après la réduction de bruit)
    noise_after = m["noise_db"] + gain - 12 * denoise
    fx["noise"] = round(_clamp(noise_after, -90.0, -10.0), 1)
    if 12 <= snr < 34 and noise_after > -62:
        fx["gate"] = True                           # le souffle entre les phrases part aussi
    b = m.get("bands") or {}
    sib = b.get("sibilance", -30) - b.get("presence", -20)
    deess = 0.55 if sib > -2 else 0.4 if sib > -5 else 0.25 if sib > -8 else 0.0
    if deess:
        fx["deess"] = deess
    dyn = m["dyn"]
    # un téléphone compresse déjà (3-6 dB) ; un micro posé laisse 8-15 dB
    fx["compress"] = 0.7 if dyn > 12 else 0.55 if dyn > 9 else 0.45 if dyn > 6 else 0.35
    mud = b.get("mud", -10) - b.get("presence", -20)
    fx["clarity"] = round(_clamp((mud - 4) / 14, 0.15, 0.8), 2)
    body = b.get("body", -10) - b.get("mid", -8)
    if body < -8:
        fx["warmth"] = round(_clamp((-8 - body) / 10, 0.15, 0.5), 2)
    if m.get("drift", 0) > 3 or dyn > 12:
        fx["level"] = True                           # le niveau change d'une phrase à l'autre
    return fx


def music_volume(m: dict, voice_level: float = TARGET + 3) -> float | None:
    """Volume d'une musique de fond pour qu'elle reste sous la voix traitée."""
    level = m.get("level_db") if not m.get("speech") else m.get("speech_db")
    if level is None or level <= -80:
        return None
    return round(_clamp(10 ** ((voice_level - MUSIC_UNDER - level) / 20), 0.03, 1.0), 3)


# ------------------------------------------------ fin réelle des mots

ENV_RATE = 100             # niveaux par seconde de l'enveloppe de la voix


def envelope(path: str) -> np.ndarray:
    """Niveau (dB) toutes les 10 ms, pleine bande 80 Hz - 14 kHz : les
    sifflantes et fricatives de fin de mot (« s », « f », « ch ») y sont, alors
    que la forme d'onde de l'éditeur (4 kHz) les perd."""
    x = _pcm(path)
    hop = SR // ENV_RATE
    n = len(x) // hop
    if n == 0:
        return np.zeros(0, np.float32)
    x = x[: n * hop]
    # coupe-bas sommaire (différence première) : le grondement ne compte pas comme de la voix
    x = np.concatenate([[0.0], np.diff(x)]).astype(np.float32)
    return _db((x.reshape(n, hop) ** 2).mean(1)).astype(np.float32)


def refine_words(words: list[dict], env: np.ndarray, rate: int = ENV_RATE, max_tail: float = 0.4,
                 max_head: float = 0.12) -> list[dict]:
    """Bornes SONORES des mots (`cs`, `ce`) : Whisper date souvent la fin d'un
    mot avant que sa dernière syllabe ne s'éteigne (surtout en fin de phrase),
    et couper à cette date la mange. On prolonge chaque fin tant que la voix
    reste au-dessus du bruit (sans dépasser le mot suivant), et on avance un
    peu les débuts (attaque des plosives). Les temps des mots, eux, ne bougent
    pas : les sous-titres restent calés sur la parole."""
    if not words or not len(env):
        return [dict(w, cs=w["start"], ce=w["end"]) for w in words]
    n = len(env)
    idx = lambda t: max(0, min(n - 1, int(round(t * rate))))  # noqa: E731
    inside = np.zeros(n, bool)
    for w in words:
        inside[idx(w["start"]):idx(w["end"]) + 1] = True
    speech = float(np.percentile(env[inside], 70)) if inside.any() else float(np.percentile(env, 90))
    quiet = env[~inside]
    floor = float(np.percentile(quiet, 30)) if len(quiet) > 20 else float(np.percentile(env, 10))
    thr = floor + max(6.0, 0.3 * (speech - floor))
    out = []
    prev_end = 0.0
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + max_tail
        # fin : tant que ça sonne (deux trames de silence d'affilée arrêtent)
        k, stop, quiet_run = idx(w["end"]), idx(min(nxt, w["end"] + max_tail)), 0
        last = k
        while k < stop:
            k += 1
            if k >= n:
                break
            if env[k] > thr:
                last, quiet_run = k, 0
            else:
                quiet_run += 1
                if quiet_run >= 2:
                    break
        ce = max(w["end"], min(nxt, (last + 1) / rate))
        # début : l'attaque d'une plosive précède souvent la date de Whisper
        k, lo = idx(w["start"]), idx(max(prev_end, w["start"] - max_head))
        first = k
        while k > lo and env[k - 1] > thr:
            k -= 1
            first = k
        cs = min(w["start"], max(prev_end, first / rate))
        out.append(dict(w, cs=round(cs, 3), ce=round(ce, 3)))
        prev_end = ce
    return out


def media_envelope(proj, m: dict) -> np.ndarray:
    """Enveloppe d'un média du projet, en cache (voice_env.bin + empreinte)."""
    folder = proj.media_folder(m["id"])
    path = os.path.join(folder, "voice_env.bin")
    try:
        stamp = f"{os.path.getmtime(m['path'])}:{os.path.getsize(m['path'])}:{VERSION}"
    except OSError:
        stamp = ""
    try:
        with open(path + ".stamp", encoding="utf-8") as f:
            if f.read() == stamp and stamp:
                return np.fromfile(path, np.float32)
    except OSError:
        pass
    env = envelope(m["path"])
    try:
        os.makedirs(folder, exist_ok=True)
        env.tofile(path)
        with open(path + ".stamp", "w", encoding="utf-8") as f:
            f.write(stamp)
    except OSError:
        pass
    return env


def cut_words(proj, mid: str, words: list[dict]) -> list[dict]:
    """Mots d'un média avec leurs bornes sonores (`cs`, `ce`), pour couper."""
    m = proj.media(mid)
    if not m or not words or not m.get("has_audio"):
        return [dict(w, cs=w["start"], ce=w["end"]) for w in words]
    try:
        return refine_words(words, media_envelope(proj, m))
    except (RuntimeError, OSError, ValueError):
        return [dict(w, cs=w["start"], ce=w["end"]) for w in words]


# -------------------------------------------------------------- projet

def media_analysis(proj, m: dict) -> dict:
    """Mesures d'un média du projet, en cache (sound.json) tant que le fichier
    et sa transcription n'ont pas changé."""
    from engine.timeline import ai
    folder = proj.media_folder(m["id"])
    words_file = ai.words_path(proj, m["id"])
    try:
        stamp = [os.path.getmtime(m["path"]), os.path.getsize(m["path"]),
                 os.path.getmtime(words_file) if os.path.isfile(words_file) else 0, VERSION]
    except OSError:
        stamp = None
    cache = os.path.join(folder, "sound.json")
    try:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        if stamp is not None and data.get("stamp") == stamp:
            return data["analysis"]
    except (OSError, ValueError, KeyError):
        pass
    words = ai.load_words(proj, m["id"]) if (m.get("transcript") or {}).get("status") == "done" else None
    res = analyze(m["path"], words)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump({"stamp": stamp, "analysis": res}, f)
    except OSError:
        pass
    return res


def optimize(proj, clips: list[dict]) -> dict:
    """Réglages du son de clips de la timeline : voix (traitement mesuré) et
    musiques de fond (volume sous la voix). Ne modifie rien : renvoie le plan."""
    media = {m["id"]: m for m in proj.state["media"]}
    audible = [c for c in clips if c.get("kind") in ("video", "audio") and not c.get("muted")
               and (media.get(c.get("media")) or {}).get("has_audio")
               and not (c.get("kind") == "video" and c.get("detached"))]
    analyses = {}
    for mid in sorted({c["media"] for c in audible}):
        try:
            analyses[mid] = media_analysis(proj, media[mid])
        except (RuntimeError, OSError, ValueError) as exc:
            analyses[mid] = {"error": str(exc)[:200]}
    voice, volume, report = {}, {}, []
    voice_ranges = [(float(c["start"]), float(c["start"]) + float(c["dur"])) for c in audible
                    if analyses.get(c["media"], {}).get("speech")]
    for mid, a in analyses.items():
        m = media[mid]
        entry = {"media": mid, "name": m["name"]}
        if a.get("error"):
            entry["error"] = a["error"]
        elif a.get("speech"):
            fx = recommend(a)
            entry.update(kind="voice", fx=fx, **{k: a[k] for k in ("speech_db", "noise_db", "snr", "dyn", "drift")},
                         clipped=a.get("clipped", 0))
            for c in audible:
                if c["media"] == mid:
                    voice[c["id"]] = fx
        else:
            vol = music_volume(a)
            entry.update(kind="music", level_db=a.get("level_db"))
            for c in audible:
                if c["media"] != mid or vol is None:
                    continue
                s, e = float(c["start"]), float(c["start"]) + float(c["dur"])
                long_enough = float(c["dur"]) >= 3.0
                under_voice = any(s < b and e > a0 for a0, b in voice_ranges)
                if long_enough and under_voice:           # une musique de fond, pas un effet sonore
                    volume[c["id"]] = vol
                    entry["volume"] = vol
            if "volume" not in entry:
                continue                                  # effet sonore, ambiance : laissés tels quels
        report.append(entry)
    voice_media = {e["media"]: e["fx"] for e in report if e.get("kind") == "voice"}
    return {"voice": voice, "voice_media": voice_media, "volume": volume, "loudness": True, "report": report}


def apply(doc: dict, plan: dict, ids=None) -> int:
    """Pose un plan d'`optimize` sur un montage (clips `ids`, sinon tous).
    Les voix sont prises par média : un clip coupé depuis garde le réglage."""
    n = 0
    for c in doc["clips"]:
        if ids is not None and c["id"] not in ids:
            continue
        fx = plan["voice_media"].get(c.get("media")) if c.get("kind") in ("video", "audio") else None
        if fx is not None and not (c["kind"] == "video" and c.get("detached")):
            c["audio_fx"] = dict(fx)
            n += 1
        if c["id"] in plan["volume"]:
            c["volume"] = plan["volume"][c["id"]]
            n += 1
    if plan.get("loudness"):
        doc["settings"] = {**(doc.get("settings") or {}), "loudness": True}
    return n
