"""Montage composable : chaque tool produit des intervalles à COUPER (sur la
timeline source). On les fusionne, on en déduit les segments à GARDER, puis on
remappe les mots sur la timeline de sortie.

Tools fournis ici : coupe des blancs (silence) et tics de langage (fillers).
La coupe manuelle = des intervalles fournis par l'UI (mêmes unités).
"""
from __future__ import annotations

import unicodedata

from engine.edl import KeepSegment, Word

# --- Tics de langage (français) ---
FILLERS: set[str] = {
    "euh", "heu", "heuh", "euhm", "hum", "hmm", "mmh", "mh",
    "bah", "ben", "hein", "bof", "pff", "genre",
}
MULTIWORD_FILLERS: list[list[str]] = [
    ["en", "fait"], ["du", "coup"], ["tu", "vois"], ["tu", "sais"],
    ["en", "gros"], ["et", "tout"], ["je", "veux", "dire"],
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c for c in s if c.isalnum())


def silence_cuts(
    words: list[Word], duration: float, max_gap: float = 0.5, pad: float = 0.08
) -> list[tuple[float, float]]:
    """Intervalles de silence à couper (avant/entre/après les mots)."""
    if not words:
        return [(0.0, duration)]
    cuts: list[tuple[float, float]] = []
    if words[0].start - pad > 0:
        cuts.append((0.0, words[0].start - pad))
    for a, b in zip(words, words[1:]):
        if b.start - a.end > max_gap:
            cuts.append((a.end + pad, b.start - pad))
    if words[-1].end + pad < duration:
        cuts.append((words[-1].end + pad, duration))
    return cuts


def filler_cuts(words: list[Word], pad: float = 0.05) -> list[tuple[float, float]]:
    """Intervalles des tics de langage à couper (mots simples + expressions)."""
    norms = [_norm(w.text) for w in words]
    cuts: list[tuple[float, float]] = []
    i, n = 0, len(words)
    while i < n:
        matched = False
        for phrase in MULTIWORD_FILLERS:
            L = len(phrase)
            if norms[i:i + L] == phrase:
                cuts.append((words[i].start - pad, words[i + L - 1].end + pad))
                i += L
                matched = True
                break
        if matched:
            continue
        if norms[i] in FILLERS:
            cuts.append((words[i].start - pad, words[i].end + pad))
        i += 1
    return cuts


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    iv = sorted((max(0.0, s), e) for s, e in intervals if e > s)
    out: list[tuple[float, float]] = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def clean_ranges(ranges: list, duration: float) -> list[list[float]]:
    """Plages [début, fin] venues de l'UI : validées, bornées à la source, fusionnées."""
    iv: list[tuple[float, float]] = []
    for r in ranges or []:
        try:
            s, e = float(r[0]), float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        iv.append((max(0.0, s), min(duration, e)))
    return [[round(s, 3), round(e, 3)] for s, e in _merge(iv)]


def subtract_ranges(
    cuts: list[tuple[float, float]], keeps: list[tuple[float, float]], min_len: float = 0.02
) -> list[tuple[float, float]]:
    """Retire des coupes les plages que l'utilisateur a choisi de garder.

    Une coupe à cheval sur une plage gardée est rognée (ou scindée en deux) ;
    les miettes restantes sous `min_len` sont ignorées.
    """
    out: list[tuple[float, float]] = []
    for s, e in cuts:
        pieces = [(s, e)]
        for ks, ke in keeps:
            nxt: list[tuple[float, float]] = []
            for a, b in pieces:
                if ke <= a or ks >= b:
                    nxt.append((a, b))
                    continue
                if ks > a:
                    nxt.append((a, ks))
                if ke < b:
                    nxt.append((ke, b))
            pieces = nxt
        out += [(a, b) for a, b in pieces if b - a >= min_len]
    return out


def keep_from_cuts(
    duration: float, cuts: list[tuple[float, float]], min_len: float = 0.10
) -> list[KeepSegment]:
    """Complément des coupes = segments à garder (filtre les miettes < min_len)."""
    merged = _merge([(max(0.0, min(s, duration)), max(0.0, min(e, duration))) for s, e in cuts])
    keep: list[KeepSegment] = []
    cur = 0.0
    for s, e in merged:
        if s > cur:
            keep.append(KeepSegment(start=cur, end=s))
        cur = max(cur, e)
    if cur < duration:
        keep.append(KeepSegment(start=cur, end=duration))
    return [k for k in keep if k.end - k.start >= min_len]


def remap_words(words: list[Word], keep: list[KeepSegment]) -> tuple[list[Word], float]:
    """Reprojette les mots gardés sur la timeline de sortie (raccourcie)."""
    offsets: list[float] = []
    acc = 0.0
    for k in keep:
        offsets.append(acc)
        acc += k.end - k.start
    out: list[Word] = []
    for w in words:
        for i, k in enumerate(keep):
            if k.start <= w.start < k.end:
                ns = offsets[i] + (w.start - k.start)
                ne = offsets[i] + (min(w.end, k.end) - k.start)
                out.append(Word(text=w.text, start=ns, end=max(ns, ne)))
                break
    return out, acc
