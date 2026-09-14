"""Traduction des sous-titres, sans modèle : `translate_texts` est simulé."""
from __future__ import annotations

import copy
import random

import pytest

from engine.pipeline import translate
from engine.pipeline.translate import (available, distribute, retime, sentence_units,
                                       translate_captions)


def line(cid, text, start, end, **extra):
    toks = text.split()
    step = (end - start) / max(1, len(toks))
    words = [{"text": t, "start": round(start + i * step, 3),
              "end": round(start + (i + 1) * step, 3)} for i, t in enumerate(toks)]
    return {"id": cid, "start": start, "end": end, "words": words, "x": 0.5, **extra}


class FakeTranslator:
    """Remplace translate_texts : rend des réponses préparées et note les appels."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, texts, source="fr", target="en"):
        self.calls.append((list(texts), source, target))
        return [self.answers[t] if isinstance(self.answers, dict) else self.answers(t)
                for t in texts]


@pytest.fixture
def fake(monkeypatch):
    def install(answers):
        f = FakeTranslator(answers)
        monkeypatch.setattr(translate, "translate_texts", f)
        return f
    return install


# ------------------------------------------------------------------ phrases


def test_sentence_units_ponctuation_et_guillemets():
    caps = [line("a", "Bonjour à", 0, 1), line("b", "tous.", 1, 2),
            line("c", "Il a dit « oui »", 2, 3), line("d", "vraiment ?", 3, 4),
            line("e", "et puis…", 4, 5), line("f", "fin sans point", 5, 6)]
    assert sentence_units(caps) == [[0, 1], [2, 3], [4], [5]]


def test_sentence_units_coupe_les_monologues():
    caps = [line(str(i), "sans ponctuation", i, i + 1) for i in range(8)]
    assert sentence_units(caps, max_lines=3) == [[0, 1, 2], [3, 4, 5], [6, 7]]


def test_sentence_units_lit_le_texte_source():
    c = line("a", "Hello.", 0, 1, src_words=[{"text": "Bonjour", "start": 0, "end": 1}])
    assert sentence_units([c, line("b", "fin.", 1, 2)]) == [[0, 1]]


# --------------------------------------------------------------- répartition


def test_distribute_invariants_aleatoires():
    rng = random.Random(1234)
    for _ in range(500):
        n = rng.randint(1, 6)
        tokens = ["x" * rng.randint(1, 12) for _ in range(rng.randint(0, 14))]
        weights = [rng.uniform(0, 40) for _ in range(n)]
        out = distribute(tokens, weights)
        assert len(out) == n
        assert [t for part in out for t in part] == tokens
        if len(tokens) >= n:
            assert all(out)
        else:
            assert all(len(p) == 1 for p in out[:len(tokens)])
            assert not any(out[len(tokens):])


def test_distribute_au_prorata_des_poids():
    out = distribute(["mot"] * 8, [30, 10])
    assert len(out[0]) > len(out[1]) >= 1


def test_distribute_bords():
    assert distribute(["a"], []) == []
    assert distribute([], [3, 4]) == [[], []]
    assert distribute(["a", "b"], [5, 5, 5]) == [["a"], ["b"], []]


# ------------------------------------------------------------------ horaires


@pytest.mark.parametrize("start, end, tokens", [
    (12.345, 12.445, ["I", "am", "here", "right", "now"]),
    (0.0, 0.1, ["a", "bb", "ccc"]),
    (3.0, 7.25, ["Hello", "everyone"]),
])
def test_retime_reste_dans_la_ligne(start, end, tokens):
    out = retime(tokens, start, end)
    assert [w["text"] for w in out] == tokens
    assert out[0]["start"] == start and out[-1]["end"] == end
    for a, b in zip(out, out[1:]):
        assert a["end"] == b["start"]
    for w in out:
        assert start <= w["start"] <= w["end"] <= end


def test_retime_au_prorata_de_la_longueur():
    out = retime(["a", "bbb"], 0.0, 4.0)
    assert (out[0]["end"] - out[0]["start"], out[1]["end"] - out[1]["start"]) == (1.0, 3.0)


def test_retime_bords():
    assert retime([], 0, 1) == []
    assert all(w["start"] == w["end"] == 2.0 for w in retime(["a", "b"], 2.0, 1.0))


# --------------------------------------------------------------- traduction


def sample():
    return [line("c0", "Bonjour à tous.", 0.0, 1.2),
            line("c1", "Je suis", 1.2, 2.0),
            line("c2", "très content.", 2.0, 3.1),
            line("c3", "caché exprès", 3.1, 4.0, hidden=True)]


def test_translate_captions_remplace_le_texte_seulement(fake):
    f = fake({"Bonjour à tous.": "Hello everyone.",
              "Je suis très content.": "I am very happy indeed."})
    caps = sample()
    before = copy.deepcopy(caps)
    out = translate_captions(caps, "fr", "en")

    assert caps == before  # l'entrée n'est pas modifiée
    assert f.calls == [(["Bonjour à tous.", "Je suis très content."], "fr", "en")]
    assert [(c["id"], c["start"], c["end"], c["x"]) for c in out] == \
        [(c["id"], c["start"], c["end"], c["x"]) for c in before]
    assert " ".join(w["text"] for c in out[:3] for w in c["words"]) == \
        "Hello everyone. I am very happy indeed."
    for c, src in zip(out[:3], before):
        assert c["lang"] == "en"
        assert c["src_words"] == src["words"]
        assert c["words"] and not c.get("hidden")
        assert all(c["start"] <= w["start"] <= w["end"] <= c["end"] for w in c["words"])
    assert out[3] == before[3]  # masquée par l'utilisateur : intacte


def test_retraduire_repart_du_texte_source(fake):
    fake({"Bonjour à tous.": "Hello everyone.", "Je suis très content.": "I am happy."})
    first = translate_captions(sample(), "fr", "en")
    f = fake(lambda t: t.upper())
    second = translate_captions(first, "fr", "en")
    assert f.calls[0][0] == ["Bonjour à tous.", "Je suis très content."]
    assert [w["text"] for w in second[0]["words"]] == ["BONJOUR", "À", "TOUS."]
    assert second[0]["src_words"] == sample()[0]["words"]


def test_ligne_videe_par_la_traduction_puis_restauree(fake):
    fake({"Bonjour à tous.": "Hi!", "Je suis très content.": "Happy."})
    first = translate_captions(sample(), "fr", "en")
    c1, c2 = first[1], first[2]
    assert [w["text"] for w in c1["words"]] == ["Happy."]
    assert not c1.get("hidden")
    # Plus assez de mots : la ligne est masquée par la traduction, texte source conservé.
    assert c2["hidden"] is True and c2["tr_hidden"] is True
    assert c2["words"] == sample()[2]["words"] and c2["words"] is not c2["src_words"]

    fake(lambda t: "I am very happy." if t.startswith("Je") else "Hello all.")
    second = translate_captions(first, "fr", "en")
    assert second[2]["hidden"] is False and "tr_hidden" not in second[2]
    assert second[2]["words"]
    assert second[3] == sample()[3]


# --------------------------------------------------------------- disponibilité


def test_available_exige_les_trois_fichiers(no_translate_model, monkeypatch):
    root = no_translate_model / "models"
    monkeypatch.setenv("MONTAGE_IA_TRANSLATE", str(root))
    assert translate.model_dir("fr", "en") == str(root / "opus-mt-fr-en")
    assert available("fr", "en") is False

    d = root / "opus-mt-fr-en"
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"")
    (d / "source.spm").write_bytes(b"")
    assert available("fr", "en") is False
    (d / "target.spm").write_bytes(b"")
    assert available("fr", "en") is True
    assert available("en", "fr") is False
