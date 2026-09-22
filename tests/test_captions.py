"""Nettoyage des sous-mots de Whisper et découpe en lignes."""
from __future__ import annotations

import pytest

from engine.edl import Word
from engine.pipeline.captions import _ts, clean_words, group_indices


def W(*items):
    return [Word(text=t, start=s, end=e) for t, s, e in items]


def test_clean_words_recolle_elisions_et_ponctuation():
    raw = W((" Il", 0.0, 0.2), (" t", 0.2, 0.3), ("'enferme", 0.3, 0.6),
            (" mots", 0.6, 0.8), (",", 0.8, 0.85), (" l'", 0.9, 1.0), (" homme", 1.0, 1.3),
            (" vrai", 1.4, 1.6), (" ?", 1.6, 1.7), (" aujourd", 2.0, 2.3), ("’hui", 2.3, 2.5),
            (" merveil", 3.0, 3.2), ("leux", 3.2, 3.5), (" »", 3.5, 3.6))
    out = clean_words(raw)
    assert [(w.text, w.start, w.end) for w in out] == [
        ("Il", 0.0, 0.2),
        ("t'enferme", 0.2, 0.6),
        ("mots,", 0.6, 0.85),
        ("l'homme", 0.9, 1.3),
        ("vrai?", 1.4, 1.7),
        ("aujourd’hui", 2.0, 2.5),
        ("merveilleux»", 3.0, 3.6),
    ]


def test_clean_words_ignore_les_blancs_et_ne_recolle_pas_le_premier():
    out = clean_words(W(("   ", 0.0, 0.1), (",", 0.1, 0.2), (" ok", 0.3, 0.5)))
    assert [w.text for w in out] == [",", "ok"]


def test_group_indices_max_mots():
    assert group_indices(list("abcde"), max_words=3, max_chars=100) == [[0, 1, 2], [3, 4]]


def test_group_indices_max_caracteres():
    texts = ["bonjour", "tout", "le", "monde"]
    # "bonjour tout" = 12 caractères : tient pile ; "le monde" passe à la ligne.
    assert group_indices(texts, max_words=10, max_chars=12) == [[0, 1], [2, 3]]


def test_group_indices_mot_trop_long_seul_sur_sa_ligne():
    texts = ["a", "anticonstitutionnellement", "b"]
    assert group_indices(texts, max_words=4, max_chars=10) == [[0], [1], [2]]


def test_group_indices_invariants():
    texts = ("le montage automatique coupe les blancs et pose des sous-titres "
             "lisibles même quand une phrase est interminable").split()
    groups = group_indices(texts, max_words=4, max_chars=18)
    assert [i for g in groups for i in g] == list(range(len(texts)))
    for g in groups:
        assert 1 <= len(g) <= 4
        assert len(g) == 1 or len(" ".join(texts[i] for i in g)) <= 18
    assert group_indices([], 4, 18) == []


@pytest.mark.parametrize("t, expected", [
    (0.0, "0:00:00.00"), (-3.0, "0:00:00.00"), (3661.5, "1:01:01.50"),
])
def test_ts(t, expected):
    assert _ts(t) == expected


def test_ts_report_des_secondes():
    # Régression : l'arrondi à 100 cs donnait 0:00:60.00.
    assert _ts(59.999) == "0:01:00.00"


def test_polices_windows_remplacees_sur_mac():
    from engine.pipeline import fonts
    assert fonts.system_font("Segoe UI", "darwin") == "Helvetica Neue"
    assert fonts.system_font("Consolas", "darwin") == "Menlo"
    assert fonts.system_font("Arial Black", "darwin") == "Arial Black"      # existe sur Mac
    assert fonts.system_font("Segoe UI", "win32") == "Segoe UI"
