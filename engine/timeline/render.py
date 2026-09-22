"""Export d'un montage timeline avec ffmpeg, en une passe.

Construction du filtergraph :

  * image — un fond de la couleur du projet, puis chaque piste vidéo, de la
    plus basse à la plus haute, superposée dessus. Une piste est un flux
    continu : ses clips mis bout à bout avec des trous transparents entre eux,
    chaque clip déjà placé dans le cadre (même géométrie que l'aperçu :
    remplir/adapter, échelle, position, rotation, miroir, opacité, réglages).
    Un clip plus grand que le cadre est recadré AVANT d'être mis à l'échelle :
    un rush 4K en « remplir » ne produit jamais d'image géante ;
  * son — un flux par piste (clips + silences), puis un mixage sans
    normalisation, protégé par un limiteur ;
  * textes — le .ass des sous-titres (repère = format du projet, libass le
    met à l'échelle de l'export) et les émojis en PNG couleur ;
  * transitions — entre deux clips qui se touchent, centrées sur la coupe :
    chaque clip est prolongé d'une demi-transition dans sa source (image figée
    si la source s'arrête), puis `xfade` les fond. Rien ne se décale : les
    sous-titres restent calés.

Lecture des sources : une entrée ffmpeg par « série » de clips qui lisent le
même média en avançant (cas des blancs supprimés). Un clip qui revient en
arrière dans la source ouvre une nouvelle entrée : on ne garde jamais en
mémoire les images d'un morceau en attendant son tour.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import threading

from engine.pipeline.ass_edit import build_ass_edited
from engine.pipeline.render import _encoder_works

EPS = 1e-3
SR = 48000
_INLINE_GRAPH_LIMIT = 6000
_OUT_TIME_RE = re.compile(r"out_time_us=(\d+)")

RESOLUTIONS = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}
QUALITY = {"low": 0.55, "standard": 1.0, "high": 1.7}
X264_CRF = {"low": 25, "standard": 20, "high": 16}
X265_CRF = {"low": 28, "standard": 24, "high": 20}


class Cancelled(RuntimeError):
    pass


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def export_size(canvas: dict, resolution: str | None) -> tuple[int, int]:
    """Définition de sortie : le petit côté du format vaut la résolution choisie."""
    w, h = int(canvas["w"]), int(canvas["h"])
    target = RESOLUTIONS.get(resolution or "")
    if not target:
        return _even(w), _even(h)
    k = target / min(w, h)
    return _even(w * k), _even(h * k)


def duration(state: dict) -> float:
    return max((c["start"] + c["dur"] for c in state["clips"] if not c.get("gone")), default=0.0)


# ------------------------------------------------------------------ entrées

class Inputs:
    """Entrées ffmpeg : une par série de lecture (voir la docstring du module)."""

    def __init__(self) -> None:
        self.args: list[str] = []
        self.count = 0
        self.runs: dict[tuple, list[dict]] = {}

    def add(self, args: list[str]) -> int:
        self.args += args
        self.count += 1
        return self.count - 1

    def source(self, media: dict, clip: dict, track_id: str) -> tuple[int, float]:
        """(numéro d'entrée, origine des temps) pour lire `clip` dans son média.

        Les clips d'une même piste et d'un même média qui avancent dans la
        source partagent une entrée, ouverte au début de la série.
        """
        key = (track_id, media["id"])
        runs = self.runs.setdefault(key, [])
        # plage lue, transitions comprises (voir plan_transitions)
        a = float(clip.get("_a", clip["in"]))
        b = float(clip.get("_b", float(clip["in"]) + clip["dur"] * float(clip.get("speed") or 1.0)))
        for run in runs:
            if a >= run["end"] - EPS:
                run["end"] = b
                run["clips"].append(clip["id"])
                return run["input"], run["start"]
        run = {"start": a, "end": b, "clips": [clip["id"]], "input": None}
        runs.append(run)
        run["input"] = self.add(["-ss", _f(a), "-i", media["path"]])
        return run["input"], a

    def image(self, media: dict, dur: float, fps: int) -> int:
        return self.add(["-loop", "1", "-framerate", str(fps), "-t", _f(dur + 0.5), "-i", media["path"]])

    def png(self, path: str) -> int:
        return self.add(["-i", path])


def plan_transitions(state: dict, media: dict[str, dict]) -> None:
    """Repère les transitions valides et la plage de source que chaque clip lit.

    Une transition d'entrée ne vaut que si le clip précédent de la piste se
    termine exactement là où le clip commence. Sa durée ne dépasse pas la
    moitié… de rien : elle est bornée par les deux clips. Chaque clip reçoit
    `_pre`/`_post` (prolongations, en secondes de timeline) et `_a`/`_b` (plage
    de source réellement lue, bornée par le média).
    """
    for t in state["tracks"]:
        if t["kind"] != "video":
            continue
        row = sorted((c for c in state["clips"] if c["track"] == t["id"] and c["kind"] in ("video", "image")),
                     key=lambda c: c["start"])
        for a, b in zip(row, row[1:]):
            tr = b.get("trans")
            if not tr or abs(a["start"] + a["dur"] - b["start"]) > 1e-3:
                continue
            d = min(float(tr.get("dur") or 0.5), float(a["dur"]), float(b["dur"]))
            if d < 0.05:
                continue
            b["_tr"] = (str(tr.get("type") or "fade"), d)
            b["_pre"] = d / 2
            a["_post"] = d / 2
    for c in state["clips"]:
        if c["kind"] != "video":
            continue
        m = media.get(c.get("media")) or {}
        speed = float(c.get("speed") or 1.0)
        a = float(c["in"]) - float(c.get("_pre", 0.0)) * speed
        b = float(c["in"]) + (float(c["dur"]) + float(c.get("_post", 0.0))) * speed
        c["_a"] = max(0.0, a)
        c["_b"] = min(float(m.get("duration") or b), b) if m.get("duration") else b


def plan_runs(state: dict, media: dict[str, dict]) -> Inputs:
    """Réserve les entrées dans l'ordre de la timeline (déterministe)."""
    inp = Inputs()
    for t in state["tracks"]:
        for c in sorted((c for c in state["clips"] if c["track"] == t["id"]), key=lambda c: c["start"]):
            m = media.get(c.get("media"))
            if not m or c["kind"] not in ("video", "audio"):
                continue
            c["_input"], c["_origin"] = inp.source(m, c, t["id"])
    return inp


# ------------------------------------------------------------------ image

def _placement(c: dict, m: dict, W: float, H: float, f: float) -> dict:
    """Géométrie d'un clip dans l'export (pixels de sortie)."""
    w, h = float(m.get("w") or W), float(m.get("h") or H)
    base = min(W / w, H / h) if c.get("fit") == "contain" else max(W / w, H / h)
    k = base * float(c.get("scale", 1.0)) * f         # pixels de sortie par pixel source
    return {"k": k, "w": w, "h": h, "sw": w * k, "sh": h * k,
            "cx": float(c.get("x", 0.5)) * W * f, "cy": float(c.get("y", 0.5)) * H * f,
            "rot": float(c.get("rotation") or 0.0)}


def _eq(c: dict) -> str:
    fl = c.get("filters") or {}
    parts = []
    if fl.get("brightness"):
        parts.append(f"brightness={_f(fl['brightness'] * 0.25)}")
    if fl.get("contrast"):
        parts.append(f"contrast={_f(1 + fl['contrast'])}")
    if fl.get("saturation"):
        parts.append(f"saturation={_f(max(0.0, 1 + fl['saturation']))}")
    chain = [f"eq={':'.join(parts)}"] if parts else []
    if fl.get("temperature"):
        t = fl["temperature"] * 0.12
        chain.append(f"colorbalance=rm={_f(t)}:bm={_f(-t)}")
    return ",".join(chain)


def _read(c: dict, dur: float, fps: int) -> list[str]:
    """Lecture de la source d'un clip, transitions comprises, durée `dur` exacte.

    `tpad` vient APRÈS `fps` : avant, la cadence n'est pas encore connue et
    il n'ajoute aucune image."""
    pre, post = float(c.get("_pre", 0.0)), float(c.get("_post", 0.0))
    if c["kind"] != "video":
        return ["setpts=PTS-STARTPTS", f"fps={fps}"]
    speed = float(c.get("speed") or 1.0)
    origin = float(c.get("_origin", c["in"]))
    a = float(c.get("_a", c["in"]))
    b = float(c.get("_b", float(c["in"]) + float(c["dur"]) * speed))
    chain = [f"trim=start={_f(a - origin)}:end={_f(b - origin)},setpts=(PTS-STARTPTS)/{_f(speed)}",
             f"fps={fps}"]
    # ce que la source n'a pas (début ou fin du rush) : image figée
    miss_a = pre - (float(c["in"]) - a) / speed
    miss_b = post - (b - float(c["in"]) - float(c["dur"]) * speed) / speed
    pads = []
    if miss_a > 0.01:
        pads.append(f"start_duration={_f(miss_a)}:start_mode=clone")
    if miss_b > 0.01:
        pads.append(f"stop_duration={_f(miss_b)}:stop_mode=clone")
    if pads:
        chain.append("tpad=" + ":".join(pads))
    return chain


def video_segment(c: dict, m: dict, src: str, W: int, H: int, f: float, fps: int, label: str,
                  blur: bool = False) -> list[str]:
    """Chaîne d'un clip visuel : [src] -> image du cadre (transparente autour),
    durée exacte (prolongations de transition comprises). Avec `blur`, le
    fond est l'image du clip elle-même, agrandie et floutée."""
    dur = float(c["dur"]) + float(c.get("_pre", 0.0)) + float(c.get("_post", 0.0))
    g = _placement(c, m, W / f, H / f, f)
    chain: list[str] = _read(c, dur, fps)

    rot = g["rot"] % 360
    if abs(rot) < 0.01:
        # sans rotation : on ne garde de la source que la partie visible
        left, top = g["cx"] - g["sw"] / 2, g["cy"] - g["sh"] / 2
        vx0, vy0 = max(0.0, left), max(0.0, top)
        vx1, vy1 = min(float(W), left + g["sw"]), min(float(H), top + g["sh"])
        if vx1 - vx0 < 2 or vy1 - vy0 < 2:
            return _transparent(W, H, fps, dur, label)       # entièrement hors cadre
        sx0, sx1 = (vx0 - left) / g["k"], (vx1 - left) / g["k"]
        sy0, sy1 = (vy0 - top) / g["k"], (vy1 - top) / g["k"]
        if c.get("flip_h"):
            sx0, sx1 = g["w"] - sx1, g["w"] - sx0
        if c.get("flip_v"):
            sy0, sy1 = g["h"] - sy1, g["h"] - sy0
        cx0, cy0 = max(0, int(round(sx0))), max(0, int(round(sy0)))
        cw = max(2, min(int(math.floor(sx1 - sx0)), int(g["w"]) - cx0))
        ch = max(2, min(int(math.floor(sy1 - sy0)), int(g["h"]) - cy0))
        ow, oh = _even(vx1 - vx0), _even(vy1 - vy0)
        chain.append(f"crop={cw}:{ch}:{cx0}:{cy0}")
        chain.append(f"scale={ow}:{oh}:flags=bicubic")
        if c.get("flip_h"):
            chain.append("hflip")
        if c.get("flip_v"):
            chain.append("vflip")
        px, py = int(round(vx0)), int(round(vy0))
    else:
        sw, sh = _even(g["sw"]), _even(g["sh"])
        chain.append(f"scale={sw}:{sh}:flags=bicubic")
        if c.get("flip_h"):
            chain.append("hflip")
        if c.get("flip_v"):
            chain.append("vflip")
        rad = math.radians(g["rot"])
        ow = _even(abs(sw * math.cos(rad)) + abs(sh * math.sin(rad)))
        oh = _even(abs(sw * math.sin(rad)) + abs(sh * math.cos(rad)))
        chain.append("format=yuva420p")
        chain.append(f"rotate={_f(rad)}:ow={ow}:oh={oh}:c=black@0")
        px, py = int(round(g["cx"] - ow / 2)), int(round(g["cy"] - oh / 2))
    eq = _eq(c)
    if eq:
        chain.append(eq)
    chain.append("format=yuva420p")
    op = float(c.get("opacity", 1.0))
    if op < 0.999:
        chain.append(f"colorchannelmixer=aa={_f(op)}")
    chain.append("setsar=1")
    if blur:
        # arrière-plan flou : l'image en « remplir », réduite, floutée, agrandie
        bw, bh = _even(W / 6), _even(H / 6)
        base = (f"{src}{','.join(_read(c, dur, fps))},"
                f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=5:2,"
                f"scale={W}:{H},eq=brightness=-0.06,format=yuva420p,setsar=1[{label}b]")
    else:
        base = f"color=c=black@0:s={W}x{H}:r={fps}:d={_f(dur)},format=yuva420p[{label}b]"
    return [
        f"{src}{','.join(chain)}[{label}c]",
        base,
        f"[{label}b][{label}c]overlay=x={px}:y={py}:eof_action=repeat:format=auto,setsar=1,"
        f"trim=duration={_f(dur)}[{label}]",
    ]


def _transparent(W: int, H: int, fps: int, dur: float, label: str) -> list[str]:
    return [f"color=c=black@0:s={W}x{H}:r={fps}:d={_f(dur)},format=yuva420p,setsar=1[{label}]"]


def video_track(t: dict, clips: list[dict], media: dict, inp: Inputs, W: int, H: int, f: float,
                fps: int, total: float, n: int, blur: bool = False) -> tuple[list[str], str | None]:
    """Une piste vidéo : clips (fondus par leurs transitions) et trous
    transparents bout à bout, durée `total`."""
    parts: list[str] = []
    labels: list[str] = []
    cursor = 0.0
    k = 0
    items = [c for c in sorted(clips, key=lambda c: c["start"])
             if media.get(c.get("media")) and c["kind"] in ("video", "image")]
    group: list[tuple[str, float, dict]] = []   # (étiquette, longueur, clip) reliés par transitions

    def close_group() -> None:
        nonlocal k
        if not group:
            return
        lab, length, _ = group[0]
        for i, (nxt, nlen, c) in enumerate(group[1:], start=1):
            kind, d = c["_tr"]
            out = f"t{n}x{k}_{i}"
            parts.append(f"[{lab}][{nxt}]xfade=transition={kind}:duration={_f(d)}:offset={_f(length - d)}[{out}]")
            lab, length = out, length + nlen - d
        labels.append(lab)
        group.clear()
        k += 1

    for c in items:
        m = media[c["media"]]
        start = max(cursor, float(c["start"]))
        linked = "_tr" in c and group and abs(start - cursor) < 1e-3
        if not linked:
            close_group()
            if start - cursor > EPS:
                lab = f"t{n}g{k}"
                parts += _transparent(W, H, fps, start - cursor, lab)
                labels.append(lab)
                k += 1
        dur = min(float(c["dur"]), total - start)
        if dur < EPS:
            continue
        cc = dict(c, dur=dur)
        if not linked and "_pre" in cc:
            # transition impossible (clip précédent absent) : lecture normale
            cc.pop("_pre")
            cc["_a"] = cc.get("in", 0.0)
        length = dur + float(cc.get("_pre", 0.0)) + float(cc.get("_post", 0.0))
        if c["kind"] == "image":
            src = f"[{inp.image(m, length, fps)}:v]"
        else:
            src = f"[{c['_input']}:v]"
        lab = f"t{n}s{k}_{len(group)}"
        parts += video_segment(cc, m, src, W, H, f, fps, lab, blur=blur)
        group.append((lab, length, cc))
        cursor = start + dur
    close_group()
    if not labels:
        return [], None
    if total - cursor > EPS:
        lab = f"t{n}g{k}"
        parts += _transparent(W, H, fps, total - cursor, lab)
        labels.append(lab)
    out = f"v{n}"
    if len(labels) == 1:
        parts.append(f"[{labels[0]}]null[{out}]")
    else:
        parts.append("".join(f"[{x}]" for x in labels) + f"concat=n={len(labels)}:v=1:a=0[{out}]")
    return parts, out


# -------------------------------------------------------------------- son

def _atempo(speed: float) -> list[str]:
    """atempo n'accepte que 0,5..100 : on enchaîne pour aller plus bas."""
    out = []
    while speed < 0.5:
        out.append("atempo=0.5")
        speed /= 0.5
    while speed > 100:
        out.append("atempo=100")
        speed /= 100
    if abs(speed - 1.0) > 1e-4:
        out.append(f"atempo={_f(speed)}")
    return out


RNNOISE_MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rnnoise_sh.rnnn")
RNNOISE_FILE = "rnnoise.rnnn"      # copie du modèle dans le dossier de travail (chemin sans échappement)


def voice_chain(fx: dict, rnnoise: bool = False) -> list[str]:
    """Filtres ffmpeg du traitement de la voix (voir model.VOICE_FX), dans
    l'ordre d'une chaîne de studio : nettoyage, dynamique, couleur, niveau."""
    fx = fx or {}
    out: list[str] = []
    if fx.get("lowcut"):
        out.append("highpass=f=80")
    dn = float(fx.get("denoise") or 0)
    if dn > 0:
        if rnnoise:
            out.append(f"arnndn=m={RNNOISE_FILE}:mix={_f(0.3 + 0.7 * dn)}")
        else:
            out.append(f"afftdn=nr={_f(6 + 18 * dn)}:nf=-30:tn=1")
    if fx.get("gate"):
        out.append("agate=threshold=0.02:ratio=4:attack=5:release=180:knee=4")
    de = float(fx.get("deess") or 0)
    if de > 0:
        out.append(f"deesser=i={_f(0.2 + 0.6 * de)}:m=0.5:f=0.5")
    co = float(fx.get("compress") or 0)
    if co > 0:
        out.append(f"acompressor=threshold={_f(-12 - 10 * co)}dB:ratio={_f(1 + 4 * co)}:attack=8:release=150"
                   f":makeup={_f(1 + 2.5 * co)}:knee=4")
    cl = float(fx.get("clarity") or 0)
    if cl > 0:
        out += [f"equalizer=f=220:t=q:w=1.1:g={_f(-3 * cl)}", f"equalizer=f=3000:t=q:w=1.2:g={_f(4 * cl)}",
                f"treble=g={_f(2 * cl)}:f=7000"]
    wa = float(fx.get("warmth") or 0)
    if wa > 0:
        out.append(f"bass=g={_f(4 * wa)}:f=180:w=0.6")
    if fx.get("level"):
        out.append("dynaudnorm=f=250:g=15:p=0.9:m=6")
    return out


def audio_segment(c: dict, src: str, label: str) -> list[str]:
    dur = float(c["dur"])
    speed = float(c.get("speed") or 1.0)
    a = float(c["in"]) - float(c.get("_origin", c["in"]))
    chain = [f"atrim=start={_f(a)}:end={_f(a + dur * speed)}", "asetpts=PTS-STARTPTS", *_atempo(speed),
             f"aresample={SR}", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    chain += voice_chain(c.get("audio_fx") or {}, bool(c.get("_rnnoise")))
    vol = float(c.get("volume", 1.0))
    if abs(vol - 1.0) > 1e-3:
        chain.append(f"volume={_f(vol)}")
    fi, fo = float(c.get("fade_in") or 0), float(c.get("fade_out") or 0)
    if fi > 0:
        chain.append(f"afade=t=in:st=0:d={_f(min(fi, dur))}")
    if fo > 0:
        chain.append(f"afade=t=out:st={_f(max(0.0, dur - fo))}:d={_f(min(fo, dur))}")
    chain += ["apad", f"atrim=duration={_f(dur)}"]
    return [f"{src}{','.join(chain)}[{label}]"]


def _silence(dur: float, label: str) -> str:
    return f"anullsrc=r={SR}:cl=stereo,atrim=duration={_f(dur)}[{label}]"


def audio_track(t: dict, clips: list[dict], media: dict, total: float, n: int) -> tuple[list[str], str | None]:
    """Le son d'une piste : clips audibles et silences, durée `total`."""
    if t.get("muted"):
        return [], None
    parts: list[str] = []
    labels: list[str] = []
    cursor = 0.0
    k = 0
    for c in sorted(clips, key=lambda c: c["start"]):
        m = media.get(c.get("media"))
        if not m or not m.get("has_audio") or c.get("muted"):
            continue
        if not (c["kind"] == "audio" or (c["kind"] == "video" and not c.get("detached"))):
            continue
        start = max(cursor, float(c["start"]))
        if start - cursor > EPS:
            lab = f"a{n}g{k}"
            parts.append(_silence(start - cursor, lab))
            labels.append(lab)
            k += 1
        dur = min(float(c["dur"]), total - start)
        if dur < EPS:
            continue
        lab = f"a{n}s{k}"
        parts += audio_segment(dict(c, dur=dur), f"[{c['_input']}:a]", lab)
        labels.append(lab)
        k += 1
        cursor = start + dur
    if not labels:
        return [], None
    if total - cursor > EPS:
        lab = f"a{n}g{k}"
        parts.append(_silence(total - cursor, lab))
        labels.append(lab)
    out = f"au{n}"
    if len(labels) == 1:
        parts.append(f"[{labels[0]}]anull[{out}]")
    else:
        parts.append("".join(f"[{x}]" for x in labels) + f"concat=n={len(labels)}:v=0:a=1[{out}]")
    return parts, out


def mix(labels: list[str], total: float, loudness: bool = False) -> tuple[list[str], str]:
    """Mixage des pistes, sans normalisation, protégé par un limiteur. Avec
    `loudness`, le tout est ramené à -14 LUFS (niveau des réseaux sociaux)."""
    tail = f"alimiter=limit=0.98:level=0,atrim=duration={_f(total)}"
    if loudness:
        # loudnorm travaille à 192 kHz : on revient à 48 kHz derrière
        tail += f",loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SR},atrim=duration={_f(total)}"
    if not labels:
        return [_silence(total, "amix")], "amix"
    if len(labels) == 1:
        return [f"[{labels[0]}]{tail}[amix]"], "amix"
    return [("".join(f"[{x}]" for x in labels) +
             f"amix=inputs={len(labels)}:normalize=0:duration=longest,{tail}[amix]")], "amix"


# ----------------------------------------------------------------- textes

def captions_of(state: dict, total: float) -> list[dict]:
    """Textes à incruster, au format de `ass_edit` (start/end, mots visibles).

    Pistes du bas d'abord : dans le .ass, une ligne écrite plus tard passe
    devant — comme une piste plus haute dans la timeline.
    """
    order = [t["id"] for t in reversed(state["tracks"]) if t["kind"] == "text" and not t.get("hidden")]
    out = []
    for c in state["clips"]:
        if c["kind"] != "text" or c.get("gone") or c.get("hidden") or c["track"] not in order:
            continue
        words = [dict(w) for w in c.get("words") or [] if not w.get("cut")]
        if not words:
            continue
        start = float(c["start"])
        end = min(total, start + float(c["dur"]))
        if end - start < EPS:
            continue
        cap = dict(c, start=start, end=end, words=words)
        cap["_z"] = order.index(c["track"])
        out.append(cap)
    return sorted(out, key=lambda c: (c["_z"], c["start"]))


# ----------------------------------------------------------------- graphe

def build(state: dict, media: dict[str, dict], out_w: int, out_h: int, fps: int, workdir: str,
          audio_only: bool = False, loudness: bool = False) -> dict:
    """Entrées, filtergraph et étiquettes de sortie de l'export."""
    state = {**state, "clips": [dict(c) for c in state["clips"]]}
    total = duration(state)
    if total <= 0:
        raise ValueError("Le montage est vide : rien à exporter.")
    # réduction de bruit : le modèle RNNoise est copié à côté du graphe
    if any((c.get("audio_fx") or {}).get("denoise") for c in state["clips"]) and os.path.isfile(RNNOISE_MODEL):
        try:
            shutil.copyfile(RNNOISE_MODEL, os.path.join(workdir, RNNOISE_FILE))
            for c in state["clips"]:
                c["_rnnoise"] = True
        except OSError:
            pass
    canvas = state["canvas"]
    f = out_w / float(canvas["w"])
    plan_transitions(state, media)
    inp = plan_runs(state, media)
    parts: list[str] = []

    vout = None
    if not audio_only:
        parts.append(f"color=c={canvas.get('bg', '#000000').replace('#', '0x')}:s={out_w}x{out_h}:r={fps}"
                     f":d={_f(total)},format=yuv420p[base]")
        prev = "base"
        tracks = [t for t in reversed(state["tracks"]) if t["kind"] == "video" and not t.get("hidden")]
        for n, t in enumerate(tracks):
            p, lab = video_track(t, [c for c in state["clips"] if c["track"] == t["id"]], media, inp,
                                 out_w, out_h, f, fps, total, n, blur=bool(canvas.get("blur") and t.get("main")))
            if not lab:
                continue
            parts += p
            parts.append(f"[{prev}][{lab}]overlay=0:0:eof_action=pass:format=auto[o{n}]")
            prev = f"o{n}"
        caps = captions_of(state, total)
        if caps:
            ass = os.path.join(workdir, "captions.ass")
            emojis = build_ass_edited(caps, ass, int(canvas["w"]), int(canvas["h"]))
            parts.append(f"[{prev}]subtitles=captions.ass[subs]")
            prev = "subs"
            prev = _emojis(parts, inp, emojis, f, prev)
        parts.append(f"[{prev}]format=yuv420p,setsar=1[vout]")
        vout = "vout"

    labels = []
    for n, t in enumerate(state["tracks"]):
        if t["kind"] not in ("video", "audio"):
            continue
        p, lab = audio_track(t, [c for c in state["clips"] if c["track"] == t["id"]], media, total, n)
        parts += p
        if lab:
            labels.append(lab)
    p, aout = mix(labels, total, loudness)
    parts += p
    return {"inputs": inp.args, "graph": ";".join(parts), "vout": vout, "aout": aout,
            "duration": total, "count": inp.count}


def _emojis(parts: list[str], inp: Inputs, emojis: list[dict], f: float, prev: str) -> str:
    """Émojis couleur (PNG) par-dessus les sous-titres, à leur place."""
    if not emojis:
        return prev
    from engine.pipeline.emoji_overlay import render_emoji_png
    for i, e in enumerate(emojis):
        try:
            png = render_emoji_png(e["char"], int(e["size"] * f))
        except (RuntimeError, OSError, KeyError):
            continue       # police émoji absente : la vidéo sort sans cet émoji
        n = inp.png(os.path.abspath(png))
        size = _even(e["size"] * f)
        parts.append(f"[{n}:v]scale={size}:-2[em{i}]")
        parts.append(f"[{prev}][em{i}]overlay=x={int(e['x'] * f)}-w/2:y={int(e['y'] * f)}-h/2:"
                     f"enable='between(t,{e['start']:.2f},{e['end']:.2f})'[ep{i}]")
        prev = f"ep{i}"
    return prev


# ---------------------------------------------------------------- encodage

def video_codec_args(codec: str, quality: str, width: int, height: int, encoder: str = "auto") -> list[str]:
    """H.264 (lisible partout) ou HEVC (deux fois plus léger), GPU si possible."""
    q = QUALITY.get(quality, 1.0)
    # 8 Mb/s pour du 1080x1920, proportionnel aux pixels, jamais moins de 2,5
    mbps = max(2.5, 8 * (width * height) / (1080 * 1920)) * q
    rate = f"{min(120, max(1, round(mbps)))}M"
    hevc = codec == "hevc" or max(width, height) > 4096
    hw = "hevc_nvenc" if hevc else "h264_nvenc"
    if encoder != "cpu" and _encoder_works(hw, f"{_even(width)}x{_even(height)}"):
        args = ["-c:v", hw, "-preset", "p5", "-rc", "vbr", "-b:v", rate, "-maxrate", rate,
                "-pix_fmt", "yuv420p"]
        return args + (["-tag:v", "hvc1"] if hevc else [])
    if hevc and _encoder_works("libx265"):
        return ["-c:v", "libx265", "-preset", "medium", "-crf", str(X265_CRF.get(quality, 24)),
                "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(X264_CRF.get(quality, 20)),
            "-pix_fmt", "yuv420p"]


def run(args: list[str], graph: str, duration: float, cwd: str, on_progress=None,
        cancel: threading.Event | None = None) -> None:
    """ffmpeg avec progression, annulable, erreur lisible."""
    cmd = ["ffmpeg", "-y", "-hide_banner", *args[:args.index("__GRAPH__")]]
    rest = args[args.index("__GRAPH__") + 1:]
    graph_file = None
    if len(graph) <= _INLINE_GRAPH_LIMIT:
        cmd += ["-filter_complex", graph]
    else:
        fd, graph_file = tempfile.mkstemp(suffix=".txt", dir=cwd, text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(graph)
        cmd += ["-filter_complex_script", os.path.basename(graph_file)]
    cmd += rest + ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    tail: list[str] = []
    try:
        for line in proc.stdout or []:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled("Export annulé.")
            m = _OUT_TIME_RE.search(line)
            if m and on_progress and duration > 0:
                on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
            elif not line.startswith(("frame=", "fps=", "bitrate=", "total_size=", "out_time", "dup_frames=",
                                      "drop_frames=", "speed=", "progress=", "stream_")):
                tail.append(line.rstrip())
                del tail[:-30]
        code = proc.wait()
        if cancel is not None and cancel.is_set():
            raise Cancelled("Export annulé.")
        if code != 0:
            raise RuntimeError("ffmpeg a échoué :\n" + "\n".join(tail[-14:]))
    finally:
        if proc.poll() is None:
            proc.kill()
        if graph_file:
            try:
                os.remove(graph_file)
            except OSError:
                pass


def export(state: dict, media_list: list[dict], out_path: str, *, resolution: str | None = None,
           fps: int | None = None, quality: str = "standard", codec: str = "h264", encoder: str = "auto",
           audio_only: bool = False, audio_args: list[str] | None = None, on_progress=None,
           cancel: threading.Event | None = None, loudness: bool = False) -> dict:
    """Rend le montage dans `out_path`. Renvoie durée, définition, taille."""
    media = {m["id"]: m for m in media_list if m.get("status") == "ready"}
    missing = sorted({c["media"] for c in state["clips"] if c.get("media") and c["media"] not in media})
    if missing:
        names = [m.get("name", m["id"]) for m in media_list if m["id"] in missing] or missing
        raise ValueError("Médias pas prêts ou introuvables : " + ", ".join(names))
    for m in media.values():
        if not os.path.isfile(m["path"]):
            raise ValueError("Fichier introuvable : " + m["path"])
    fps = int(fps or state["canvas"].get("fps") or 30)
    out_w, out_h = export_size(state["canvas"], resolution)
    work = tempfile.mkdtemp(prefix="montage_export_")
    try:
        g = build(state, media, out_w, out_h, fps, work, audio_only=audio_only, loudness=loudness)
        out = os.path.abspath(out_path)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp_out = os.path.join(os.path.dirname(out), "_part_" + os.path.basename(out))
        if audio_only:
            tail = ["-map", f"[{g['aout']}]", *(audio_args or ["-c:a", "libmp3lame", "-b:a", "192k"]),
                    "-t", _f(g["duration"]), tmp_out]
        else:
            tail = ["-map", f"[{g['vout']}]", "-map", f"[{g['aout']}]",
                    *video_codec_args(codec, quality, out_w, out_h, encoder), "-r", str(fps),
                    "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-movflags", "+faststart",
                    "-t", _f(g["duration"]), tmp_out]
        run([*g["inputs"], "__GRAPH__", *tail], g["graph"], g["duration"], work, on_progress, cancel)
        os.replace(tmp_out, out)
        return {"output": out, "duration": round(g["duration"], 3), "width": out_w, "height": out_h,
                "fps": fps, "size": os.path.getsize(out), "inputs": g["count"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            if "tmp_out" in locals() and os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass
