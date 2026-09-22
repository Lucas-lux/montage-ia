"""Montage automatique : phrases, scores, reprises, plan (sans modèle)."""
from __future__ import annotations

import pytest

from engine.timeline import autoedit as ae


def W(text, start, end):
    return {"text": text, "start": start, "end": end}


def words_of(script):
    """"Bonjour à tous. | Deuxième phrase ?" -> mots horodatés, 0,3 s par mot, 1 s entre phrases."""
    out, t = [], 0.0
    for sent in script.split("|"):
        toks = sent.split()
        for k, tok in enumerate(toks):
            out.append(W(tok, round(t, 2), round(t + 0.25, 2)))
            t += 0.3
        t += 1.2
    return out


def test_phrases_par_ponctuation_et_par_blanc():
    words = [W("Bonjour", 0, .3), W("à", .35, .5), W("tous.", .55, .8), W("Aujourd'hui", .9, 1.3),
             W("on", 1.35, 1.5), W("commence", 1.55, 2), W("vite", 3.5, 3.8)]
    s = ae.sentences(words)
    assert [x["text"] for x in s] == ["Bonjour à tous.", "Aujourd'hui on commence", "vite"]
    assert s[0]["start"] == 0 and s[0]["end"] == 0.8


def test_score_accroche():
    words = words_of("Bonjour à tous et bienvenue. | Pourquoi 90 % des gens échouent ? C'est le secret. | Voilà voilà.")
    s = ae.sentences(words)
    ae.score_sentences(s, None, 100)
    assert s[1]["score"] > s[0]["score"] and s[1]["score"] > s[2]["score"]
    assert ae.detect_fluff(s) == {0}


def test_salutation_avec_sujet_gardee():
    words = words_of("Bonjour à tous, aujourd'hui je vais vous parler de montage vidéo. | Salut tout le monde, "
                     "bienvenue sur la chaîne. | Le montage. | Merci et à bientôt.")
    s = ae.sentences(words)
    assert ae.detect_fluff(s) == {1, 3}


def test_ouverture_a_froid_par_les_regles():
    words = words_of("Bonjour à tous, aujourd'hui je vais vous parler de montage vidéo. | Euh c'est un sujet passionnant. | "
                     "Avec ce logiciel on gagne énormément de temps et d'argent. | Merci et à bientôt.")
    plan = ae.build_plan(words, None, 100, {"llm": False, "trim": True})
    assert plan["hook"]["sentence"] == 2 and plan["hook"]["cold_open"]
    assert plan["drop_sentences"] == [3]


def test_formule_de_fin_et_reprises():
    words = words_of("La première étape | La première étape c'est de couper les blancs. | On coupe tous les blancs. | "
                     "Merci et abonnez-vous à la chaîne.")
    s = ae.sentences(words)
    assert ae.detect_retakes(s) == {0}          # faux départ
    assert ae.detect_fluff(s) == {3}


def test_mots_cles_et_accroche_courte():
    words = words_of("Avec ce logiciel on gagne énormément de temps et d'argent tous les jours de la semaine.")
    [s] = ae.sentences(words)
    ae.score_sentences([s], None, 100)
    kw = ae.keywords(s)
    assert "temps" in kw and "argent" in kw and len(kw.split()) <= 3
    assert ae._hook_text(s).endswith("…") and len(ae._hook_text(s).split()) <= 8


def test_plan_par_regles():
    words = words_of("Salut tout le monde. | Voici 3 erreurs qui coûtent de l'argent. | La première c'est de ne pas couper. | "
                     "La deuxième c'est le son. | Pensez à vous abonner.")
    plan = ae.build_plan(words, None, 100, {"llm": False, "trim": True})
    assert not plan["llm"]
    assert plan["drop_sentences"] == [0, 4]
    assert plan["hook"]["sentence"] == 1 and "erreurs" in plan["hook"]["text"]
    assert all(t["sentence"] != 1 for t in plan["texts"])
    assert plan["highlights"] and plan["highlights"][0]["sentence"] == 1
    assert len(plan["cutpoints"]) == 5


def test_plan_sans_retrait_et_duree_cible():
    words = words_of("Salut. | Une phrase forte : 3 secrets. | Une phrase banale sans grand intérêt. | "
                     "Encore une autre phrase banale. | Et à bientôt.")
    plan = ae.build_plan(words, None, 100, {"llm": False, "trim": False})
    assert plan["drop"] == []
    plan = ae.build_plan(words, None, 100, {"llm": False, "trim": True, "max_duration": 2.0})
    assert 1 not in plan["drop_sentences"] and len(plan["drop_sentences"]) >= 3


def test_energie_forme_d_onde():
    wave = bytes([200] * 100 + [20] * 100)
    assert ae.energy_of(wave, 100, 0, 1) == pytest.approx(200 / 255)
    assert ae.energy_of(wave, 100, 1, 2) == pytest.approx(20 / 255)
    assert ae.energy_of(None, 100, 0, 1) == 0


def test_plan_ia_valide_et_borne(monkeypatch):
    from engine.pipeline import llm
    words = words_of("Salut. | Regarde ce secret. | Deux. | Trois. | Quatre. | Cinq. | Six.")
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "titre": "Un titre", "hook": {"phrase": "1", "texte": "Le secret", "ouverture": True},
        "supprimer": [0, 2, 3, 4, 5, 6, 99], "moments_forts": [{"de": 1, "a": 2, "pourquoi": "fort"}, {"de": 5, "a": 1}],
        "textes": [{"phrase": 1, "texte": "SECRET"}, {"phrase": 3, "texte": "bien trop long pour un texte à l'écran"}]})
    plan = ae.build_plan(words, None, 100, {"llm": True, "trim": True})
    assert plan["llm"] and plan["title"] == "Un titre"
    assert plan["hook"]["text"] == "Le secret" and plan["hook"]["cold_open"]
    assert len(plan["drop_sentences"]) <= 0.4 * 7 + 1e-9    # garde-fou
    assert plan["highlights"] == [{"s": pytest.approx(words_of("Salut. | Regarde")[1]["start"]), "e": plan["highlights"][0]["e"],
                                   "sentence": 1, "label": "fort", "score": plan["highlights"][0]["score"]}]
    assert [t["text"] for t in plan["texts"]] == ["SECRET"]
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: None)
    assert not ae.build_plan(words, None, 100, {"llm": True})["llm"]     # réponse illisible : règles


def test_visage_sans_opencv_ni_video(tmp_path):
    assert ae.face_anchor(str(tmp_path / "rien.mp4")) is None


def test_visage_median_en_flottants_json(monkeypatch, tmp_path):
    """Un faux OpenCV : le visage médian des images, en flottants Python (JSON)."""
    import json
    import sys
    import types
    np = pytest.importorskip("numpy")

    class Cap:
        def __init__(self, path):
            self.i = 0
        def get(self, prop):
            return {7: 80, 3: 640, 4: 360}[prop]
        def set(self, prop, v):
            self.i = v
        def read(self):
            # visage centré en (320, 180), largeur 128, sauf une image sans rien
            if self.i == 5:
                return False, None
            return True, np.zeros((360, 640, 3), dtype=np.uint8)
        def release(self):
            pass

    class Det:
        def detect(self, frame):
            return 1, np.array([[256, 116, 128, 128, 0.9]], dtype=np.float32)

    fake = types.SimpleNamespace(CAP_PROP_FRAME_COUNT=7, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
                                 CAP_PROP_POS_FRAMES=1, VideoCapture=Cap,
                                 FaceDetectorYN_create=lambda *a: Det())
    monkeypatch.setitem(sys.modules, "cv2", fake)
    face = ae.face_anchor(str(tmp_path / "x.mp4"))
    assert face == {"x": 0.5, "y": 0.5, "w": 0.2, "samples": 7}     # une image illisible sur huit
    assert all(type(v) in (float, int) for v in face.values())
    json.dumps(face)
