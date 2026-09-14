"""Montage composable : coupes -> segments gardés -> mots remappés."""
from __future__ import annotations

import pytest

from engine.edl import KeepSegment, Word
from engine.pipeline.edit import (clean_ranges, filler_cuts, keep_from_cuts, remap_words,
                                  silence_cuts, subtract_ranges)

approx = pytest.approx


def W(*items):
    return [Word(text=t, start=s, end=e) for t, s, e in items]


def spans(keep):
    return [(k.start, k.end) for k in keep]


# ------------------------------------------------------------------ silences


def test_silence_cuts_sans_mots_coupe_tout():
    assert silence_cuts([], 12.0) == [(0.0, 12.0)]


def test_silence_cuts_avant_entre_apres():
    ws = W(("a", 1.0, 1.5), ("b", 1.6, 2.0), ("c", 3.0, 3.5))
    cuts = silence_cuts(ws, 5.0, max_gap=0.5, pad=0.08)
    assert cuts == [approx((0.0, 0.92)), approx((2.08, 2.92)), approx((3.58, 5.0))]


def test_silence_cuts_respecte_le_seuil_et_les_bords():
    # Parole collée au début et à la fin, blanc égal au seuil : rien à couper.
    ws = W(("a", 0.0, 1.0), ("b", 1.5, 2.0))
    assert silence_cuts(ws, 2.0, max_gap=0.5, pad=0.08) == []


# ------------------------------------------------------------ tics de langage


def test_filler_mot_simple_avec_ponctuation_et_casse():
    ws = W(("Euh,", 1.0, 1.3), ("bonjour", 1.4, 1.8), ("Hum…", 2.0, 2.2))
    assert filler_cuts(ws, pad=0.05) == [approx((0.95, 1.35)), approx((1.95, 2.25))]


def test_filler_accents_ignores():
    ws = W(("Bâh", 0.5, 0.8), ("Bénéfice", 1.0, 1.5))
    assert filler_cuts(ws, pad=0.0) == [approx((0.5, 0.8))]


@pytest.mark.parametrize("phrase", [["En", "fait,"], ["du", "coup"], ["Je", "veux", "dire"]])
def test_filler_expression_multi_mots(phrase):
    ws = W(("Alors", 0.0, 0.4), *[(t, 1.0 + i, 1.5 + i) for i, t in enumerate(phrase)],
           ("voilà", 9.0, 9.5))
    assert filler_cuts(ws, pad=0.05) == [approx((0.95, 1.5 + len(phrase) - 1 + 0.05))]


def test_filler_mots_isoles_d_une_expression_non_coupes():
    ws = W(("en", 0.0, 0.2), ("vrai", 0.3, 0.6), ("fait", 1.0, 1.2), ("du", 2.0, 2.2))
    assert filler_cuts(ws) == []


def test_filler_expression_puis_mot_simple():
    ws = W(("en", 0.0, 0.2), ("fait", 0.3, 0.5), ("euh", 0.6, 0.9))
    assert filler_cuts(ws, pad=0.0) == [approx((0.0, 0.5)), approx((0.6, 0.9))]


# ------------------------------------------------------------ segments gardés


def test_keep_sans_coupe_garde_tout():
    assert spans(keep_from_cuts(10.0, [])) == [(0.0, 10.0)]


def test_keep_fusionne_les_coupes_qui_se_chevauchent():
    keep = keep_from_cuts(10.0, [(1.5, 3.0), (1.0, 2.0), (3.0, 4.0)])
    assert spans(keep) == [(0.0, 1.0), (4.0, 10.0)]


def test_keep_borne_les_coupes_a_la_source():
    keep = keep_from_cuts(10.0, [(-1.0, 0.5), (9.0, 12.0), (11.0, 13.0)])
    assert spans(keep) == [(0.5, 9.0)]


def test_keep_ignore_les_miettes():
    keep = keep_from_cuts(10.0, [(1.0, 2.0), (2.05, 3.0)], min_len=0.10)
    assert spans(keep) == [(0.0, 1.0), (3.0, 10.0)]


def test_keep_tout_coupe():
    assert keep_from_cuts(5.0, [(0.0, 5.0)]) == []


# --------------------------------------------------------------------- remap


def test_remap_projette_sur_la_timeline_de_sortie():
    keep = [KeepSegment(start=1.0, end=2.0), KeepSegment(start=4.0, end=5.0)]
    ws = W(("a", 1.2, 1.5), ("coupé", 3.0, 3.5), ("b", 4.1, 4.4),
           ("déborde", 1.8, 2.3), ("bord", 2.0, 2.1))
    out, total = remap_words(ws, keep)
    assert total == approx(2.0)
    assert [(w.text, w.start, w.end) for w in out] == [
        ("a", approx(0.2), approx(0.5)),
        ("b", approx(1.1), approx(1.4)),
        ("déborde", approx(0.8), approx(1.0)),  # fin rognée au segment
    ]


# --------------------------------------------------------------- plages d'UI


def test_clean_ranges_valide_borne_fusionne():
    raw = [["x", 1], None, [1], 5, [8.0, 12.0], ["2.5", "3.5"], [3.0, 4.12345],
           [-1, 0.5], [11, 15]]
    assert clean_ranges(raw, 10.0) == [[0.0, 0.5], [2.5, 4.123], [8.0, 10.0]]


def test_clean_ranges_vide():
    assert clean_ranges(None, 10.0) == []
    assert clean_ranges([[3, 3], [4, 2]], 10.0) == []


# ------------------------------------------------------ passages gardés


def test_subtract_scinde_une_coupe():
    assert subtract_ranges([(1.0, 5.0)], [(2.0, 3.0)]) == [(1.0, 2.0), (3.0, 5.0)]


@pytest.mark.parametrize("keep, expected", [
    ((0.0, 2.0), [(2.0, 5.0)]),     # rogne le début
    ((4.0, 6.0), [(1.0, 4.0)]),     # rogne la fin
    ((0.0, 10.0), []),              # supprime toute la coupe
    ((6.0, 7.0), [(1.0, 5.0)]),     # disjoint
    ((5.0, 7.0), [(1.0, 5.0)]),     # bord à bord
])
def test_subtract_rogne_ou_supprime(keep, expected):
    assert subtract_ranges([(1.0, 5.0)], [keep]) == expected


def test_subtract_ignore_les_miettes():
    assert subtract_ranges([(1.0, 5.0)], [(1.01, 4.99)], min_len=0.02) == []


def test_subtract_plusieurs_plages():
    out = subtract_ranges([(0.0, 10.0), (12.0, 13.0)], [(5.0, 6.0), (2.0, 3.0)])
    assert sorted(out) == [(0.0, 2.0), (3.0, 5.0), (6.0, 10.0), (12.0, 13.0)]
