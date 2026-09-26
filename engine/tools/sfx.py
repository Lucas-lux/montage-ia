"""Effets sonores : une bibliothèque synthétisée, et le synthétiseur pour créer les siens.

Aucun fichier sonore n'est livré ni téléchargé : chaque son de la bibliothèque
est une RECETTE (un moteur et ses réglages) que le synthétiseur ci-dessous
calcule à la demande, puis garde en cache (WAV 48 kHz stéréo). « Créer un son »
expose les mêmes moteurs avec leurs curseurs : un son créé est lui aussi une
recette, qu'on peut rouvrir et modifier.

Moteurs (chacun une fonction `réglages -> échantillons`) :
    whoosh   souffle filtré qui balaie les fréquences (transitions)
    pop      note brève qui tombe (pops, bulles, clics)
    bell     partiels de cloche ou de carillon (notifications, succès)
    impact   choc grave : sous-basse qui tombe + bruit (boum, coup)
    riser    montée de tension (bruit + dent de scie qui montent)
    laser    balayage rapide de hauteur (laser, zap, chute)
    spring   ressort : hauteur qui oscille et s'amortit (boing)
    notes    suite de notes (pièce, succès, erreur, trompette triste)
    drum     percussions (grosse caisse, caisse claire, clap, charley, tom)
    glitch   tranches aléatoires de bruit et de tons (glitch, parasites)
    noise    bruit d'ambiance modulé (vent, pluie, radio)
    pulse    battement régulier (cœur, tic-tac)

Toutes les fonctions sont déterministes (graine fixe) : un même réglage donne
toujours le même son.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave

import numpy as np

SR = 48000
MAX_DUR = 12.0


# ------------------------------------------------------------------ outils

def _t(dur: float) -> np.ndarray:
    return np.arange(int(max(0.01, min(MAX_DUR, dur)) * SR)) / SR


def _phase(freq) -> np.ndarray:
    return 2 * np.pi * np.cumsum(freq) / SR


def _osc(kind: str, freq) -> np.ndarray:
    ph = _phase(freq)
    if kind == "square":
        return np.sign(np.sin(ph))
    if kind == "saw":
        return 2 * ((ph / (2 * np.pi)) % 1.0) - 1
    if kind == "triangle":
        return 2 * np.abs(2 * ((ph / (2 * np.pi)) % 1.0) - 1) - 1
    return np.sin(ph)


def _noise(n: int, seed: int = 1, color: str = "white") -> np.ndarray:
    x = np.random.default_rng(seed).standard_normal(n)
    if color == "pink":
        x = _lowpass(x, 1200) * 0.7 + x * 0.3
    elif color == "brown":
        x = np.cumsum(x)
        x -= _lowpass(x, 20)
        x /= np.max(np.abs(x)) + 1e-9
    return x


def _lowpass(x: np.ndarray, cutoff) -> np.ndarray:
    """Passe-bas à un pôle ; `cutoff` : fréquence fixe ou une par échantillon."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    a = 1 - np.exp(-2 * np.pi * np.clip(c, 5, SR / 2.2) / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, (xi, ai) in enumerate(zip(x.tolist(), a.tolist())):
        acc += ai * (xi - acc)
        y[i] = acc
    return y


def _bandpass(x: np.ndarray, center, q: float = 1.2) -> np.ndarray:
    """Passe-bande à variables d'état ; `center` fixe ou variable."""
    f = np.broadcast_to(np.asarray(center, dtype=float), x.shape)
    g = 2 * np.sin(np.pi * np.clip(f, 20, SR / 6) / SR)
    damp = 1.0 / max(0.3, q)
    low = band = 0.0
    y = np.empty_like(x)
    for i, (xi, gi) in enumerate(zip(x.tolist(), g.tolist())):
        high = xi - low - damp * band
        band += gi * high
        low += gi * band
        y[i] = band
    return y


def _highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    return x - _lowpass(x, cutoff)


def _env(n: int, attack: float, decay: float, curve: float = 3.0) -> np.ndarray:
    """Enveloppe : montée linéaire puis descente exponentielle."""
    t = np.arange(n) / SR
    a = max(1e-4, attack)
    up = np.clip(t / a, 0, 1)
    down = np.exp(-np.maximum(0, t - a) / max(1e-3, decay) * curve / 3)
    return up * down


def _swell(n: int, peak: float) -> np.ndarray:
    """Monte jusqu'à `peak` (fraction de la durée) puis redescend."""
    p = np.linspace(0, 1, n)
    k = min(0.95, max(0.05, peak))
    return np.where(p < k, (p / k) ** 1.6, ((1 - p) / (1 - k)) ** 1.4)


def _echo(x: np.ndarray, amount: float, delay: float = 0.12) -> np.ndarray:
    if amount <= 0:
        return x
    d = int(delay * SR)
    out = np.concatenate([x, np.zeros(d * 4)])
    for k in range(1, 5):
        out[d * k:d * k + len(x)] += x * (amount ** k) * 0.6
    return out


def _finish(x: np.ndarray, gain: float = 0.9) -> np.ndarray:
    """Coupe les clics aux bords et normalise."""
    x = np.nan_to_num(x.astype(float))
    n = len(x)
    f = min(n // 4, int(0.004 * SR))
    if f > 0:
        ramp = np.linspace(0, 1, f)
        x[:f] *= ramp
        x[-f:] *= ramp[::-1]
    peak = np.max(np.abs(x)) or 1.0
    return x / peak * gain


# ------------------------------------------------------------------ moteurs

def whoosh(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    n = len(t)
    f0, f1 = p["from"], p["to"]
    center = f0 * (f1 / f0) ** np.linspace(0, 1, n) if f0 > 0 and f1 > 0 else np.linspace(f0, f1, n)
    x = _bandpass(_noise(n, 3, "white"), center, p["q"])
    x += _lowpass(_noise(n, 5, "brown"), 300) * p.get("body", 0.3)
    return x * _swell(n, p["peak"])


def pop(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    f = p["to"] + (p["from"] - p["to"]) * np.exp(-t / max(0.004, p["drop"]))
    x = _osc(p.get("wave", "sine"), f) * _env(len(t), 0.002, p["dur"] * 0.35)
    click = _highpass(_noise(len(t), 7), 2000) * _env(len(t), 0.0005, 0.006) * p.get("click", 0.3)
    return x + click


def bell(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    ratios = {"bell": [1, 2.76, 5.4, 8.93], "chime": [1, 2, 3.01, 4.1], "beep": [1], "glass": [1, 2.32, 4.25, 6.63]}
    x = np.zeros(len(t))
    for i, r in enumerate(ratios.get(p.get("kind", "bell"), [1])):
        x += np.sin(_phase(np.full(len(t), p["freq"] * r))) * np.exp(-t * (1.5 + i * 1.8) / p["decay"]) / (1 + i)
    return x * _env(len(t), 0.002, p["dur"])


def impact(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    n = len(t)
    f = p["low"] + (p["high"] - p["low"]) * np.exp(-t / 0.05)
    body = np.sin(_phase(f)) * np.exp(-t / max(0.05, p["decay"]))
    hit = _lowpass(_noise(n, 11), p["bright"]) * np.exp(-t / 0.04) * 1.5
    rumble = _lowpass(_noise(n, 13, "brown"), 120) * np.exp(-t / max(0.05, p["decay"])) * p.get("rumble", 0.4)
    return np.tanh((body + hit + rumble) * p.get("drive", 1.5))


def riser(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    n = len(t)
    ramp = np.linspace(0, 1, n)
    if p.get("down"):
        ramp = ramp[::-1]
    f = p["from"] * (p["to"] / p["from"]) ** ramp
    tone = (_osc("saw", f) + _osc("saw", f * 1.007)) * 0.3
    x = _lowpass(tone + _noise(n, 17) * p.get("air", 0.6), 200 + 7000 * ramp ** 2)
    amp = ramp ** 1.5 if not p.get("down") else ramp ** 0.7
    return x * amp


def laser(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    f = p["from"] * (p["to"] / p["from"]) ** (t / t[-1])
    f = f * (1 + p.get("wobble", 0) * np.sin(2 * np.pi * 30 * t))
    return _osc(p.get("wave", "square"), f) * _env(len(t), 0.003, p["dur"] * 0.6) * 0.6


def spring(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    f = p["freq"] * (1 + p["depth"] * np.sin(2 * np.pi * p["rate"] * t) * np.exp(-t / p["decay"]))
    return _osc(p.get("wave", "triangle"), f) * _env(len(t), 0.003, p["dur"] * 0.5)


def notes(p: dict) -> np.ndarray:
    seq = [float(x) for x in p["seq"]]
    step = p["step"]
    total = _t(step * len(seq) + p.get("tail", 0.3))
    x = np.zeros(len(total))
    for i, f in enumerate(seq):
        a = int(i * step * SR)
        seg = _t(step + p.get("tail", 0.3))
        vib = 1 + p.get("vibrato", 0) * np.sin(2 * np.pi * 6 * seg)
        glide = 1 - p.get("bend", 0) * seg / seg[-1] if p.get("bend") else 1
        tone = _osc(p.get("wave", "square"), f * vib * glide) * _env(len(seg), 0.004, step * p.get("hold", 1.2))
        b = min(len(x), a + len(seg))
        x[a:b] += tone[: b - a]
    return x


def drum(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    n = len(t)
    kind = p.get("kind", "kick")
    if kind == "kick":
        f = 45 + 110 * np.exp(-t / 0.03)
        return np.tanh(np.sin(_phase(f)) * np.exp(-t / p.get("decay", 0.25)) * 2.5)
    if kind == "snare":
        tone = np.sin(_phase(np.full(n, 190))) * np.exp(-t / 0.06)
        return tone * 0.6 + _highpass(_noise(n, 19), 1500) * np.exp(-t / p.get("decay", 0.14))
    if kind == "clap":
        x = np.zeros(n)
        for k, d in enumerate((0, 0.011, 0.022, 0.034)):
            a = int(d * SR)
            burst = _bandpass(_noise(n - a, 23 + k), 1300, 1.5) * np.exp(-np.arange(n - a) / SR / (0.012 if k < 3 else 0.12))
            x[a:] += burst
        return x
    if kind == "hat":
        return _highpass(_noise(n, 29), 7000) * np.exp(-t / p.get("decay", 0.05))
    if kind == "badum":
        # ba-dum-tss : deux toms, puis la cymbale
        x = np.zeros(n)
        for at, f in ((0.0, 190), (0.16, 140)):
            a = int(at * SR)
            seg = t[: n - a]
            x[a:] += np.sin(_phase(f * (1 + 0.5 * np.exp(-seg / 0.04)))) * np.exp(-seg / 0.18)
        a = int(0.34 * SR)
        x[a:] += _highpass(_noise(n - a, 41), 5000) * np.exp(-t[: n - a] / 0.6) * 0.9
        return x
    # tom
    f = p.get("freq", 120) * (1 + 0.6 * np.exp(-t / 0.05))
    return np.sin(_phase(f)) * np.exp(-t / p.get("decay", 0.3))


def glitch(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    n = len(t)
    rng = np.random.default_rng(int(p.get("seed", 3)))
    x = np.zeros(n)
    i = 0
    while i < n:
        ln = int(rng.uniform(0.01, 0.07) / p.get("density", 1.0) * SR)
        kind = rng.integers(0, 3)
        seg = np.arange(min(ln, n - i)) / SR
        if kind == 0:
            chunk = rng.standard_normal(len(seg))
        elif kind == 1:
            chunk = np.sign(np.sin(2 * np.pi * rng.uniform(200, 3000) * seg))
        else:
            chunk = np.zeros(len(seg))
        x[i:i + len(seg)] = chunk * rng.uniform(0.3, 1)
        i += len(seg) + int(rng.uniform(0, 0.03) * SR)
    bits = max(2, int(p.get("bits", 6)))
    return np.round(x * 2 ** bits) / 2 ** bits


def noise_bed(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    n = len(t)
    x = _lowpass(_noise(n, 31, p.get("color", "pink")), p["tone"])
    mod = 1 + p.get("gust", 0.5) * np.sin(2 * np.pi * p.get("rate", 0.3) * t + np.sin(2 * np.pi * 0.11 * t))
    if p.get("crackle"):
        rng = np.random.default_rng(37)
        pops = (rng.random(n) < p["crackle"] / SR * 40) * rng.standard_normal(n) * 3
        x += _lowpass(pops, 3000)
    return x * mod * _swell(n, 0.5) ** 0.3


def pulse_beat(p: dict) -> np.ndarray:
    t = _t(p["dur"])
    x = np.zeros(len(t))
    period = 60.0 / p["bpm"]
    k = 0
    while k * period < p["dur"]:
        for j, off in enumerate(p.get("pattern", [0.0])):
            a = int((k * period + off) * SR)
            if a >= len(x):
                continue
            seg = np.arange(min(len(x) - a, int(0.25 * SR))) / SR
            f = p["freq"] * (1.25 if (p.get("alt") and (k + j) % 2) else 1)
            hit = np.sin(2 * np.pi * f * seg * (1 + 0.8 * np.exp(-seg / 0.02))) * np.exp(-seg / p["decay"])
            x[a:a + len(seg)] += hit * (1 if j == 0 else 0.75)
        k += 1
    return x


ENGINES = {
    "whoosh": (whoosh, "Souffle", {
        "dur": (0.1, 3.0, 0.6, "Durée"), "from": (80, 8000, 300, "Fréquence de départ"),
        "to": (80, 12000, 3500, "Fréquence d'arrivée"), "q": (0.4, 6, 1.4, "Résonance"),
        "peak": (0.1, 0.9, 0.55, "Moment le plus fort"), "body": (0, 1, 0.3, "Grave")}),
    "pop": (pop, "Pop", {
        "dur": (0.03, 0.8, 0.12, "Durée"), "from": (100, 4000, 900, "Hauteur de départ"),
        "to": (40, 3000, 180, "Hauteur d'arrivée"), "drop": (0.004, 0.2, 0.03, "Chute"),
        "click": (0, 1, 0.3, "Clic")}),
    "bell": (bell, "Cloche", {
        "dur": (0.2, 4.0, 1.2, "Durée"), "freq": (200, 3000, 1046, "Hauteur"), "decay": (0.2, 4, 1.0, "Résonance")}),
    "impact": (impact, "Impact", {
        "dur": (0.2, 4.0, 1.2, "Durée"), "low": (25, 120, 38, "Grave"), "high": (60, 400, 140, "Attaque"),
        "decay": (0.05, 2.5, 0.6, "Tenue"), "bright": (200, 8000, 1500, "Brillance"), "rumble": (0, 1, 0.4, "Grondement"),
        "drive": (0.5, 6, 1.5, "Saturation")}),
    "riser": (riser, "Montée", {
        "dur": (0.5, 8.0, 2.5, "Durée"), "from": (40, 800, 110, "Hauteur de départ"),
        "to": (100, 4000, 880, "Hauteur d'arrivée"), "air": (0, 1.5, 0.6, "Souffle")}),
    "laser": (laser, "Laser", {
        "dur": (0.05, 2.0, 0.35, "Durée"), "from": (100, 6000, 2400, "Hauteur de départ"),
        "to": (40, 6000, 180, "Hauteur d'arrivée"), "wobble": (0, 0.4, 0.0, "Vibration")}),
    "spring": (spring, "Ressort", {
        "dur": (0.2, 3.0, 0.9, "Durée"), "freq": (60, 1200, 220, "Hauteur"), "depth": (0, 1, 0.45, "Amplitude"),
        "rate": (2, 40, 14, "Vitesse"), "decay": (0.05, 2, 0.35, "Amortissement")}),
    "notes": (notes, "Notes", {"step": (0.04, 0.8, 0.1, "Écart entre les notes")}),
    "drum": (drum, "Percussion", {"dur": (0.05, 2.0, 0.5, "Durée"), "decay": (0.02, 1.5, 0.25, "Tenue")}),
    "glitch": (glitch, "Glitch", {
        "dur": (0.1, 4.0, 0.8, "Durée"), "density": (0.3, 3, 1.0, "Densité"), "bits": (2, 12, 6, "Résolution"),
        "seed": (1, 99, 3, "Variante")}),
    "noise": (noise_bed, "Ambiance", {
        "dur": (1.0, 12.0, 4.0, "Durée"), "tone": (100, 8000, 900, "Couleur"), "gust": (0, 1, 0.5, "Rafales"),
        "rate": (0.05, 3, 0.3, "Vitesse des rafales")}),
    "pulse": (pulse_beat, "Battement", {
        "dur": (0.5, 8.0, 2.4, "Durée"), "bpm": (30, 240, 72, "Tempo"), "freq": (30, 2000, 55, "Hauteur"),
        "decay": (0.01, 0.4, 0.12, "Tenue")}),
}

# Réglages communs à tous les moteurs (post-traitement).
COMMON = {"pitch": (-12, 12, 0, "Hauteur (demi-tons)"), "echo": (0, 0.9, 0, "Écho"),
          "reverse": (0, 1, 0, "À l'envers"), "volume": (0.1, 1.0, 0.9, "Volume")}

CATEGORIES = {
    "transitions": "Transitions", "ui": "Pops et clics", "notifs": "Notifications", "impacts": "Impacts",
    "cartoon": "Cartoon", "tension": "Tension", "tech": "Glitch et tech", "rythme": "Rythme",
    "comedie": "Comédie", "ambiance": "Ambiance",
}

C, E, G, A = 523.25, 659.25, 783.99, 880.0
LIBRARY: list[tuple[str, str, str, str, dict]] = [
    # transitions
    ("whoosh", "Whoosh", "transitions", "whoosh", {}),
    ("whoosh_court", "Whoosh court", "transitions", "whoosh", {"dur": 0.3, "from": 600, "to": 5000, "peak": 0.5}),
    ("whoosh_long", "Whoosh long", "transitions", "whoosh", {"dur": 1.4, "from": 200, "to": 2500, "q": 1.0, "peak": 0.6}),
    ("swoosh_grave", "Swoosh grave", "transitions", "whoosh", {"dur": 0.8, "from": 900, "to": 120, "q": 1.1, "body": 0.8}),
    ("swish", "Swish", "transitions", "whoosh", {"dur": 0.22, "from": 2500, "to": 9000, "q": 2.5, "peak": 0.4, "body": 0}),
    ("whoosh_inverse", "Whoosh inversé", "transitions", "whoosh", {"dur": 0.9, "peak": 0.85, "reverse": 1}),
    ("passage", "Passage rapide", "transitions", "whoosh", {"dur": 0.5, "from": 300, "to": 6000, "q": 3.5, "peak": 0.5}),
    ("zip", "Zip", "transitions", "laser", {"dur": 0.18, "from": 400, "to": 3200, "wobble": 0.2, "wave": "saw"}),
    # pops et clics
    ("pop", "Pop", "ui", "pop", {}),
    ("pop_grave", "Pop grave", "ui", "pop", {"from": 400, "to": 90, "dur": 0.15}),
    ("bulle", "Bulle", "ui", "pop", {"from": 300, "to": 1400, "drop": 0.05, "dur": 0.14, "click": 0}),
    ("clic", "Clic", "ui", "pop", {"from": 3000, "to": 1800, "dur": 0.03, "drop": 0.005, "click": 1}),
    ("tic", "Tic", "ui", "pop", {"from": 2200, "to": 2000, "dur": 0.04, "click": 0.6}),
    ("toggle", "Interrupteur", "ui", "notes", {"seq": [1400, 1900], "step": 0.04, "tail": 0.04, "wave": "sine", "hold": 0.4}),
    ("bip", "Bip", "ui", "bell", {"kind": "beep", "freq": 1320, "dur": 0.15, "decay": 0.3}),
    ("goutte", "Goutte", "ui", "pop", {"from": 500, "to": 2200, "drop": 0.02, "dur": 0.1, "click": 0}),
    # notifications
    ("ding", "Ding", "notifs", "bell", {"freq": 1318, "dur": 1.2}),
    ("carillon", "Carillon", "notifs", "notes", {"seq": [E * 2, C * 2, G, C * 2], "step": 0.14, "wave": "sine", "tail": 0.8, "hold": 3}),
    ("notification", "Notification", "notifs", "notes", {"seq": [G * 2, C * 4], "step": 0.09, "wave": "sine", "tail": 0.4, "hold": 2}),
    ("message", "Message", "notifs", "notes", {"seq": [A * 2, E * 2], "step": 0.08, "wave": "triangle", "tail": 0.3, "hold": 1.5}),
    ("piece", "Pièce", "notifs", "notes", {"seq": [987.8, 1318.5], "step": 0.08, "wave": "square", "tail": 0.35}),
    ("succes", "Succès", "notifs", "notes", {"seq": [C, E, G, C * 2], "step": 0.09, "wave": "square", "tail": 0.4}),
    ("verre", "Verre", "notifs", "bell", {"kind": "glass", "freq": 1760, "dur": 1.6, "decay": 1.4}),
    ("cloche", "Cloche", "notifs", "bell", {"kind": "bell", "freq": 523, "dur": 2.5, "decay": 2.4}),
    # impacts
    ("boum", "Boum", "impacts", "impact", {}),
    ("coup_sourd", "Coup sourd", "impacts", "impact", {"dur": 0.5, "decay": 0.18, "bright": 600, "rumble": 0.1}),
    ("coup_poing", "Coup de poing", "impacts", "impact", {"dur": 0.35, "low": 70, "high": 260, "decay": 0.1, "bright": 4000, "drive": 3}),
    ("claque", "Claque", "impacts", "drum", {"kind": "clap", "dur": 0.3}),
    ("chute_basse", "Basse qui tombe", "impacts", "impact", {"dur": 2.5, "low": 28, "high": 110, "decay": 1.6, "rumble": 0.6, "drive": 2}),
    ("cinema", "Impact cinéma", "impacts", "impact", {"dur": 3.0, "decay": 1.2, "bright": 2500, "rumble": 1, "drive": 2.5, "echo": 0.35}),
    ("tonnerre", "Tonnerre", "impacts", "noise", {"dur": 4.0, "tone": 400, "gust": 0.9, "rate": 0.6, "color": "brown"}),
    ("gong", "Gong", "impacts", "bell", {"kind": "bell", "freq": 140, "dur": 4.0, "decay": 3.5}),
    # cartoon
    ("boing", "Boing", "cartoon", "spring", {}),
    ("ressort", "Ressort", "cartoon", "spring", {"freq": 420, "depth": 0.6, "rate": 22, "decay": 0.5, "dur": 1.0}),
    ("sifflet_haut", "Sifflet qui monte", "cartoon", "laser", {"dur": 0.8, "from": 500, "to": 2000, "wave": "sine", "wobble": 0.03}),
    ("sifflet_bas", "Sifflet qui descend", "cartoon", "laser", {"dur": 0.9, "from": 2000, "to": 400, "wave": "sine", "wobble": 0.03}),
    ("laser", "Laser", "cartoon", "laser", {}),
    ("zap", "Zap", "cartoon", "laser", {"dur": 0.2, "from": 5000, "to": 300, "wave": "saw", "wobble": 0.3}),
    ("bloop", "Bloop", "cartoon", "pop", {"from": 150, "to": 600, "drop": 0.06, "dur": 0.2, "click": 0}),
    ("saut", "Saut", "cartoon", "laser", {"dur": 0.25, "from": 300, "to": 900, "wave": "square"}),
    ("chute", "Chute", "cartoon", "laser", {"dur": 1.1, "from": 1500, "to": 90, "wave": "sine"}),
    ("couinement", "Couinement", "cartoon", "spring", {"freq": 1200, "depth": 0.3, "rate": 30, "decay": 0.2, "dur": 0.3, "wave": "sine"}),
    # tension
    ("montee", "Montée", "tension", "riser", {}),
    ("montee_longue", "Montée longue", "tension", "riser", {"dur": 6.0, "to": 1200}),
    ("descente", "Descente", "tension", "riser", {"dur": 2.0, "down": 1}),
    ("suspense", "Suspense", "tension", "noise", {"dur": 6.0, "tone": 220, "gust": 0.2, "rate": 0.15, "color": "brown"}),
    ("coeur", "Battement de cœur", "tension", "pulse", {"pattern": [0.0, 0.22], "bpm": 70}),
    ("tic_tac", "Tic-tac", "tension", "pulse", {"bpm": 120, "freq": 1800, "decay": 0.02, "alt": 1, "dur": 3.0}),
    # glitch et tech
    ("glitch", "Glitch", "tech", "glitch", {}),
    ("parasites", "Parasites", "tech", "glitch", {"dur": 1.5, "density": 2.5, "bits": 4, "seed": 7}),
    ("erreur_num", "Erreur numérique", "tech", "notes", {"seq": [220, 207], "step": 0.18, "wave": "square", "tail": 0.1, "hold": 0.9}),
    ("arret_bande", "Arrêt de bande", "tech", "laser", {"dur": 1.0, "from": 440, "to": 30, "wave": "saw"}),
    ("scan", "Scan", "tech", "laser", {"dur": 1.2, "from": 200, "to": 3200, "wave": "square", "wobble": 0.15}),
    ("robot", "Bip robot", "tech", "notes", {"seq": [880, 1175, 660, 1320], "step": 0.07, "wave": "square", "tail": 0.1, "hold": 0.8}),
    # rythme
    ("kick", "Grosse caisse", "rythme", "drum", {"kind": "kick"}),
    ("caisse_claire", "Caisse claire", "rythme", "drum", {"kind": "snare", "dur": 0.4, "decay": 0.16}),
    ("clap", "Clap", "rythme", "drum", {"kind": "clap", "dur": 0.4}),
    ("charley", "Charley", "rythme", "drum", {"kind": "hat", "dur": 0.15, "decay": 0.04}),
    ("tom", "Tom", "rythme", "drum", {"kind": "tom", "dur": 0.6, "decay": 0.3}),
    # comédie
    ("buzzer", "Buzzer", "comedie", "notes", {"seq": [150, 150], "step": 0.3, "wave": "square", "tail": 0.05, "hold": 1.4}),
    ("mauvaise_reponse", "Mauvaise réponse", "comedie", "notes", {"seq": [311, 277], "step": 0.28, "wave": "saw", "tail": 0.2, "hold": 1.3}),
    ("bonne_reponse", "Bonne réponse", "comedie", "notes", {"seq": [E * 2, A * 2], "step": 0.12, "wave": "sine", "tail": 0.6, "hold": 3}),
    ("trompette_triste", "Trompette triste", "comedie", "notes", {"seq": [392, 370, 349, 330], "step": 0.45, "wave": "saw",
                                                                   "vibrato": 0.02, "tail": 0.5, "hold": 1.4, "bend": 0.08}),
    ("tambour", "Ba-dum-tss", "comedie", "drum", {"kind": "badum", "dur": 1.6}),
    ("fanfare", "Fanfare", "comedie", "notes", {"seq": [C, C, C, G, E * 2], "step": 0.12, "wave": "saw", "tail": 0.6}),
    # ambiance
    ("vent", "Vent", "ambiance", "noise", {"dur": 6.0}),
    ("pluie", "Pluie", "ambiance", "noise", {"dur": 6.0, "tone": 5000, "gust": 0.1, "color": "white", "crackle": 0.4}),
    ("radio", "Radio", "ambiance", "noise", {"dur": 3.0, "tone": 3000, "gust": 0.3, "rate": 2, "color": "white", "crackle": 1}),
    ("foule", "Brouhaha", "ambiance", "noise", {"dur": 5.0, "tone": 700, "gust": 0.4, "rate": 1.3, "color": "pink"}),
]


def params_of(engine: str, given: dict | None) -> dict:
    """Réglages complets et bornés d'un moteur (défauts + communs)."""
    if engine not in ENGINES:
        raise ValueError(f"Moteur inconnu : {engine}")
    schema = ENGINES[engine][2]
    out = {k: v[2] for k, v in {**schema, **COMMON}.items()}
    for k, v in (given or {}).items():
        spec = {**schema, **COMMON}.get(k)
        if spec:
            try:
                out[k] = min(spec[1], max(spec[0], float(v)))
            except (TypeError, ValueError):
                pass
        elif k in ("seq", "pattern") and isinstance(v, list):
            out[k] = [float(x) for x in v][:32]
        elif k in ("kind", "wave", "color") and isinstance(v, str):
            out[k] = v[:12]
        elif k in ("down", "alt", "tail", "hold", "vibrato", "bend", "crackle"):
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                pass
    if engine == "notes" and "seq" not in out:
        out["seq"] = [C, E, G]
    return out


def synth(engine: str, params: dict | None = None) -> np.ndarray:
    """Échantillons stéréo (n, 2) du son décrit."""
    if engine not in ENGINES:
        raise ValueError(f"Moteur inconnu : {engine}")
    p = params_of(engine, params)
    x = ENGINES[engine][0](p)
    semis = p.get("pitch", 0)
    if abs(semis) > 0.01:
        k = 2 ** (semis / 12)
        idx = np.arange(0, len(x) - 1, k)
        x = np.interp(idx, np.arange(len(x)), x)
    x = _echo(x, p.get("echo", 0))
    if p.get("reverse", 0) >= 0.5:
        x = x[::-1]
    x = _finish(x, p.get("volume", 0.9))
    # stéréo légèrement élargie (quelques échantillons de décalage)
    right = np.concatenate([np.zeros(12), x[:-12]]) if len(x) > 24 else x
    return np.stack([x, right * 0.97 + x * 0.03], axis=1)


def write_wav(samples: np.ndarray, path: str) -> float:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)
    return round(len(samples) / SR, 3)


# --------------------------------------------------------------- stockage

class Sounds:
    """Bibliothèque (recettes, sons calculés en cache) et « Mes sons »."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.lib_dir = os.path.join(root, "library")
        self.mine_dir = os.path.join(root, "mine")
        self.tmp_dir = os.path.join(root, "preview")
        for d in (self.lib_dir, self.mine_dir, self.tmp_dir):
            os.makedirs(d, exist_ok=True)
        self.lock = threading.Lock()

    # -- bibliothèque
    def library(self) -> list[dict]:
        return [{"id": sid, "label": label, "category": cat, "engine": eng, "params": params_of(eng, p)}
                for sid, label, cat, eng, p in LIBRARY]

    def _recipe(self, sid: str) -> tuple[str, dict] | None:
        for s, _, _, eng, p in LIBRARY:
            if s == sid:
                return eng, p
        return None

    def file(self, sid: str) -> str:
        """Fichier WAV d'un son (bibliothèque, « Mes sons » ou aperçu)."""
        if sid.startswith("mine_"):
            path = os.path.join(self.mine_dir, sid + ".wav")
        elif sid.startswith("tmp_"):
            path = os.path.join(self.tmp_dir, sid + ".wav")
        else:
            rec = self._recipe(sid)
            if not rec:
                raise KeyError(sid)
            path = os.path.join(self.lib_dir, sid + ".wav")
            if not os.path.isfile(path):
                write_wav(synth(*rec), path)
        if not os.path.isfile(path):
            raise KeyError(sid)
        return path

    def preview(self, engine: str, params: dict) -> dict:
        """Calcule un son à écouter (création) ; même réglage = même fichier."""
        p = params_of(engine, params)
        key = hashlib.sha1(json.dumps([engine, p], sort_keys=True).encode()).hexdigest()[:12]
        sid = f"tmp_{key}"
        path = os.path.join(self.tmp_dir, sid + ".wav")
        dur = write_wav(synth(engine, p), path) if not os.path.isfile(path) else _duration(path)
        self._prune_previews()
        return {"id": sid, "dur": dur, "engine": engine, "params": p}

    def _prune_previews(self, keep: int = 60) -> None:
        files = sorted((os.path.join(self.tmp_dir, f) for f in os.listdir(self.tmp_dir)), key=os.path.getmtime)
        for f in files[:-keep]:
            try:
                os.remove(f)
            except OSError:
                pass

    # -- mes sons
    def _index_path(self) -> str:
        return os.path.join(self.mine_dir, "index.json")

    def mine(self) -> list[dict]:
        try:
            with open(self._index_path(), encoding="utf-8") as f:
                items = json.load(f)
        except (OSError, ValueError):
            return []
        return [it for it in items if os.path.isfile(os.path.join(self.mine_dir, it["id"] + ".wav"))]

    def _save_index(self, items: list[dict]) -> None:
        tmp = self._index_path() + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._index_path())

    def _add(self, entry: dict) -> dict:
        with self.lock:
            items = self.mine()
            items.insert(0, entry)
            self._save_index(items)
        return entry

    def save_synth(self, name: str, engine: str, params: dict) -> dict:
        sid = "mine_" + hashlib.sha1(f"{time.time()}{name}".encode()).hexdigest()[:10]
        p = params_of(engine, params)
        dur = write_wav(synth(engine, p), os.path.join(self.mine_dir, sid + ".wav"))
        return self._add({"id": sid, "label": _label(name, "Mon son"), "source": "synth", "engine": engine,
                          "params": p, "dur": dur, "added": time.time()})

    def save_file(self, name: str, src: str, source: str = "import") -> dict:
        """Importe un fichier son (ou la bande son d'une vidéo) : converti en WAV 48 kHz stéréo."""
        sid = "mine_" + hashlib.sha1(f"{time.time()}{src}".encode()).hexdigest()[:10]
        dest = os.path.join(self.mine_dir, sid + ".wav")
        res = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-t", "60",
                              "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", dest],
                             capture_output=True, text=True, timeout=300)
        if res.returncode != 0 or not os.path.isfile(dest):
            raise ValueError((res.stderr or "fichier illisible").strip()[-200:])
        return self._add({"id": sid, "label": _label(name, "Mon son"), "source": source, "dur": _duration(dest),
                          "added": time.time()})

    def remove(self, sid: str) -> bool:
        with self.lock:
            items = self.mine()
            keep = [it for it in items if it["id"] != sid]
            if len(keep) == len(items):
                return False
            self._save_index(keep)
        try:
            os.remove(os.path.join(self.mine_dir, sid + ".wav"))
        except OSError:
            pass
        return True

    def rename(self, sid: str, name: str) -> dict | None:
        with self.lock:
            items = self.mine()
            for it in items:
                if it["id"] == sid:
                    it["label"] = _label(name, it["label"])
                    self._save_index(items)
                    return it
        return None

    def copy_to(self, sid: str, dest_dir: str) -> tuple[str, str]:
        """Copie un son dans un projet : (chemin, nom affiché)."""
        src = self.file(sid)
        label = next((it["label"] for it in self.mine() if it["id"] == sid), None) or \
            next((lab for s, lab, *_ in LIBRARY if s == sid), "Son")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "source.wav")
        shutil.copyfile(src, dest)
        return dest, f"{label}.wav"


def _label(name, default: str) -> str:
    s = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    return s or default


def _duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 3)
    except (OSError, wave.Error, EOFError):
        return 0.0


def catalog() -> dict:
    """Moteurs et leurs réglages, pour l'éditeur de sons."""
    engines = []
    for name, (_, label, schema) in ENGINES.items():
        engines.append({"name": name, "label": label, "params": [
            {"key": k, "min": v[0], "max": v[1], "default": v[2], "label": v[3]} for k, v in {**schema, **COMMON}.items()]})
    return {"categories": CATEGORIES, "engines": engines}
