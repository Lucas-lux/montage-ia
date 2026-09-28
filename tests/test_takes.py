"""Dérush par prises (engine/timeline/takes.py) : découpe sur les niveaux,
jonctions sans chevauchement, rangement des mots, décisions entre prises."""
from __future__ import annotations

import numpy as np
import pytest

from engine.timeline import takes as T

RATE = T.RATE


def levels(spans, dur=12.0, voice=-12.0, floor=-55.0):
    """Niveaux synthétiques : de la voix sur `spans` [(a, b)], du bruit de fond ailleurs."""
    n = int(dur * RATE)
    rms = np.full(n, floor, np.float32)
    for a, b in spans:
        rms[int(a * RATE):int(b * RATE)] = voice
    peak = rms + 6.0
    return {"rms": rms, "peak": peak}


def words_in(a, b, text):
    toks = text.split()
    step = (b - a) / len(toks)
    return [{"text": t, "start": round(a + i * step, 3), "end": round(a + (i + 0.8) * step, 3)}
            for i, t in enumerate(toks)]


def test_seuils_mesures():
    lv = levels([(1, 3), (5, 8)])
    th = T.thresholds(lv["rms"])
    assert th["floor"] == pytest.approx(-55.0)
    assert th["speech"] == pytest.approx(-12.0)
    assert th["floor"] < th["silence"] < th["speech"]


def test_prises_bornees_sur_la_voix():
    lv = levels([(1.0, 3.0), (5.0, 8.0), (9.0, 11.0)])
    tk, _ = T.split_takes(lv["rms"], lv["peak"])
    assert len(tk) == 3
    # la première syllabe entière, un peu d'air autour
    assert tk[0]["in"] == pytest.approx(1.0 - T.HEAD, abs=0.02)
    assert tk[0]["out"] == pytest.approx(3.0 + T.TAIL, abs=0.02)


def test_deux_prises_ne_se_chevauchent_jamais():
    # un silence court (0,4 s) : les marges de l'une mordraient sur l'autre
    lv = levels([(1.0, 3.0), (3.4, 6.0)])
    tk, _ = T.split_takes(lv["rms"], lv["peak"])
    assert len(tk) == 2
    assert tk[0]["out"] <= tk[1]["in"] - T.SEAM + 1e-6
    assert tk[0]["out"] >= 3.0 and tk[1]["in"] <= 3.4        # aucune syllabe mangée


def test_silence_trop_court_ne_separe_pas():
    lv = levels([(1.0, 3.0), (3.2, 5.0)])
    tk, _ = T.split_takes(lv["rms"], lv["peak"])
    assert len(tk) == 1


def test_mots_ranges_dans_la_prise_la_plus_proche():
    tk = [{"in": 1.0, "out": 3.0}, {"in": 4.0, "out": 6.0}]
    # Whisper date souvent un mot un peu avant la voix
    T.assign_words(tk, [{"text": "ne", "start": 0.9, "end": 1.05}, {"text": "pas", "start": 1.2, "end": 1.5},
                        {"text": "oui", "start": 4.2, "end": 4.5}, {"text": "loin", "start": 9.0, "end": 9.2}])
    assert tk[0]["text"] == "ne pas" and tk[1]["text"] == "oui"


@pytest.mark.parametrize("a, b, keep, why", [
    ("je vais", "je vais te montrer un truc", False, "false start"),
    ("le vrai secret c'est", "bon le vrai secret c'est d'en parler", False, "contained"),
    ("le vrai secret en fait c'est d'en parler autour", "le vrai secret c'est d'en parler autour de vous",
     False, "retake"),
    ("j'ai une idée géniale ce matin", "et là tout a changé pour moi", True, ""),
])
def test_decisions(a, b, keep, why):
    tk = [{"in": 0.0, "out": 3.0, "words": words_in(0, 3, a), "text": a},
          {"in": 4.0, "out": 8.0, "words": words_in(4, 8, b), "text": b}]
    T.decide(tk)
    assert tk[0]["keep"] is keep
    assert why in tk[0]["why"]
    assert tk[1]["keep"]


def test_fin_redite_raccourcie():
    a, b = "j'en parle à ma mère", "ma mère en fait a aimé"
    tk = [{"in": 0.0, "out": 3.0, "words": words_in(0, 3, a), "text": a},
          {"in": 4.0, "out": 7.0, "words": words_in(4, 7, b), "text": b}]
    T.decide(tk)
    assert tk[0]["keep"] and tk[0]["trim_out"] < 3.0
    assert "ma mère" in tk[0]["why"]
    tk[0]["n"], tk[1]["n"] = 1, 2
    assert T.kept_ranges(tk) == [(0.0, tk[0]["trim_out"]), (4.0, 7.0)]
    assert T.kept_ranges(tk, [2, 1]) == [(4.0, 7.0), (0.0, tk[0]["trim_out"])]


def test_derush_complet_et_repli_sur_les_mots():
    lv = levels([(1.0, 3.0), (5.0, 8.0)])
    words = words_in(1.0, 3.0, "je vais") + words_in(5.0, 8.0, "je vais vous montrer")
    res = T.derush(lv, words)
    assert [t["n"] for t in res["takes"]] == [1, 2]
    assert res["takes"][0]["keep"] is False              # faux départ
    assert res["kept_seconds"] == pytest.approx(3.0 + T.HEAD + T.TAIL, abs=0.05)
    # piste où la voix ne redescend jamais : découpe aux pauses entre les mots
    flat = levels([(0.0, 40.0)], dur=40.0)
    words = words_in(1, 10, "une phrase assez longue pour faire une prise") + \
        words_in(12, 20, "puis une autre après une vraie pause")
    res = T.derush(flat, words)
    assert res["method"] == "word gaps" and len(res["takes"]) == 2
