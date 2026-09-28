"""Opérations de montage des agents IA (engine/agent/edit.py) : mêmes règles
que le studio (studio/model.js), sans navigateur."""
from __future__ import annotations

import pytest

from engine.agent import edit as E
from engine.timeline import model


def doc(fmt: str = "9:16") -> dict:
    return {"name": "Essai", "canvas": model.canvas_for(fmt), "tracks": model.default_tracks(), "clips": [],
            "markers": [], "settings": dict(model.SETTINGS_DEFAULTS)}


def w(text, s, e):
    return {"text": text, "start": s, "end": e}


# 0.5-2.0 : première phrase ; 2 s de blanc ; « euh » ; seconde phrase ; long blanc ; « fin »
WORDS = [w("Bonjour", 0.5, 0.9), w("tout", 1.0, 1.3), w("le", 1.35, 1.45), w("monde.", 1.5, 2.0),
         w("euh", 4.0, 4.3), w("voici", 4.4, 4.8), w("la", 4.85, 4.95), w("suite.", 5.0, 5.5),
         w("Fin.", 9.0, 9.5)]
MEDIA = {"m1": {"id": "m1", "kind": "video", "duration": 20.0, "w": 1920, "h": 1080, "has_audio": True},
         "img": {"id": "img", "kind": "image", "w": 800, "h": 800},
         "a1": {"id": "a1", "kind": "audio", "duration": 30.0, "has_audio": True}}
words_of = lambda mid: WORDS if mid == "m1" else []  # noqa: E731


def test_blancs_et_tics_memes_regles_que_le_studio():
    assert E.silence_cuts([w("a", 1, 2), w("b", 3, 4)], 5, 0.5, 0.1, 0) == [[0.0, 0.9], [2.1, 2.9], [4.1, 5]]
    cuts = E.filler_cuts([w("euh", 1, 1.2), w("du", 2, 2.1), w("coup", 2.1, 2.3), w("bon", 3, 3.2)], 0.05)
    assert [pytest.approx(c) for c in cuts] == [[0.95, 1.25], [1.95, 2.35]]
    assert E.norm("Déjà-vu !") == "dejavu"
    assert E.merge_ranges([[3, 4], [1, 2], [1.5, 3.2]]) == [[1, 4]]
    assert E.keep_ranges(0, 10, [[2, 3], [5, 6]]) == [[0, 2], [3, 5], [6, 10]]


def test_assemblage_retire_blancs_et_tics():
    d = doc()
    res = E.assemble(d, MEDIA, words_of, [{"media": "m1", "start": 0, "end": 10}], tail=0)
    main = E.main_clips(d)
    assert main and main[0]["start"] == 0
    for a, b in zip(main, main[1:]):                       # piste principale collée, sans trou
        assert b["start"] == pytest.approx(E.clip_end(a), abs=1e-3)
    # ni le long blanc (2-4 s), ni « euh » (4.0-4.3), ni le blanc avant « Fin. »
    for c in main:
        assert not (c["in"] < 3.0 < E.src_end(c))
        assert not (c["in"] < 4.15 < E.src_end(c))
        assert not (c["in"] < 7.0 < E.src_end(c))
    assert res["removed"] > 5
    assert any(c.get("gap") for c in main)                 # passages retirés restaurables dans le studio


def test_segments_dans_l_ordre_donne_accroche_en_tete():
    d = doc()
    E.assemble(d, MEDIA, words_of, [{"media": "m1", "start": 4.4, "end": 5.5, "zoom": 1.2},
                                    {"media": "m1", "start": 0.5, "end": 2.0}],
               remove_silences=False, remove_fillers=False)
    main = E.main_clips(d)
    assert [round(c["in"], 2) for c in main] == [4.4, 0.5]
    assert main[0]["scale"] == 1.2 and main[1]["start"] == pytest.approx(1.1)


def test_segment_image_et_remplacement():
    d = doc()
    E.assemble(d, MEDIA, words_of, [{"media": "m1", "start": 0, "end": 2}], remove_silences=False,
               remove_fillers=False)
    E.assemble(d, MEDIA, words_of, [{"media": "img", "duration": 2.5}], replace=False)
    main = E.main_clips(d)
    assert [c["kind"] for c in main] == ["video", "image"] and main[1]["dur"] == 2.5
    E.assemble(d, MEDIA, words_of, [{"media": "img", "duration": 1}])      # remplace tout
    assert [c["kind"] for c in E.main_clips(d)] == ["image"]


def test_rythme_coupe_aux_fins_de_phrases():
    clip = {"start": 0.0, "dur": 20.0, "in": 0.0, "speed": 1}
    pts = E.rhythm_splits(clip, [3.0, 5.5, 8.0, 12.5, 15.0], 2.0, 6.0)
    assert pts and all(a < b for a, b in zip(pts, pts[1:]))
    edges = [0.0, *pts, 20.0]
    assert all(2.0 <= b - a <= 8.0 for a, b in zip(edges, edges[1:]))
    assert set(pts) <= {3.0, 5.5, 8.0, 12.5, 15.0, 6.0, 12.0, 18.0}


def test_zooms_alternes_sauf_plans_choisis():
    d = doc("16:9")
    media = {"m1": dict(MEDIA["m1"], duration=30.0)}
    res = E.assemble(d, media, words_of, [{"media": "m1", "start": 0, "end": 30}], remove_silences=False,
                     remove_fillers=False)
    n = E.apply_rhythm(d, media, {"m1": [5.0, 10.0, 15.0, 20.0, 25.0]}, {"m1": {"x": 0.5, "y": 0.4}}, "punchy")
    scales = [c["scale"] for c in E.main_clips(d)]
    assert n >= 2 and 1.0 in scales and 1.2 in scales
    fixed = E.main_clips(d)[0]["id"]
    E.assemble(d, media, words_of, [{"media": "m1", "start": 0, "end": 30}], remove_silences=False,
               remove_fillers=False)
    first = E.main_clips(d)[0]
    E.apply_rhythm(d, media, {}, {}, "normal", skip={first["id"]})
    assert first["scale"] == 1 and fixed != first["id"] and res["pieces"]


def test_paysage_dans_un_cadre_vertical_recadre_sur_le_visage():
    cv = model.canvas_for("9:16")
    x, y = E.position({"x": 0.7, "y": 0.4}, MEDIA["m1"], cv, 1.0)
    gw = 1920 * (1920 / 1080)
    left, right = x * 1080 - gw / 2, x * 1080 + gw / 2
    assert x < 0.5 and left <= 0 <= 1080 <= right and y == pytest.approx(0.5)
    # même format que la source : le zoom garde le visage à sa place
    assert E.position(None, {"w": 1080, "h": 1920}, cv, 1.0) == (0.5, 0.5)


def test_sous_titres_suivent_les_coupes():
    d = doc()
    E.assemble(d, MEDIA, words_of, [{"media": "m1", "start": 4.3, "end": 5.6}, {"media": "m1", "start": 0.4,
                                                                                "end": 2.1}],
               remove_silences=False, remove_fillers=False)
    cap = {"id": "k1", "track": "", "kind": "text", "auto": True, "start": 0.5, "dur": 1.5,
           "words": [dict(w("Bonjour", 0.5, 0.9), m="m1", s=0.5, e=0.9),
                     dict(w("monde.", 1.5, 2.0), m="m1", s=1.5, e=2.0)]}
    gone = {"id": "k2", "track": "", "kind": "text", "auto": True, "start": 9.0, "dur": 0.5,
            "words": [dict(w("Fin.", 9.0, 9.5), m="m1", s=9.0, e=9.5)]}
    assert E.put_captions(d, [cap, gone]) == 2
    first = E.main_clips(d)[1]
    assert cap["start"] == pytest.approx(first["start"] + 0.1, abs=1e-3)
    assert gone.get("gone") and gone["words"][0].get("cut")
    assert E.captions_track(d)["name"] == "Sous-titres"


def test_couper_un_passage_decale_la_suite():
    d = doc()
    E.assemble(d, MEDIA, words_of, [{"media": "m1", "start": 0, "end": 10}], remove_silences=False,
               remove_fillers=False)
    t = E.add_text(d, "Titre", 6.0, 1.0, {"size": 90})
    d["markers"].append({"id": "k", "t": 7.0, "label": "", "color": "#FFFFFF"})
    assert E.cut_range(d, 2.0, 4.0) == 2.0
    assert sum(c["dur"] for c in E.main_clips(d)) == pytest.approx(8.0)
    assert t["start"] == 4.0 and d["markers"][0]["t"] == 5.0


def test_phrases_sur_la_timeline():
    from engine.timeline.autoedit import sentences
    d = doc()
    E.assemble(d, MEDIA, words_of, [{"media": "m1", "start": 4.3, "end": 5.6}, {"media": "m1", "start": 0.4,
                                                                                "end": 2.1}],
               remove_silences=False, remove_fillers=False)
    out = E.timeline_sentences(d, lambda mid: sentences(WORDS))
    # « euh » (4.0-4.3) est hors du premier segment : seuls les mots gardés comptent
    assert [s["text"] for s in out] == ["voici la suite.", "Bonjour tout le monde."]
    assert out[0]["t0"] < out[1]["t0"]
    assert out[1]["t0"] == pytest.approx(1.3 + 0.1, abs=0.01)


def test_textes_et_plans_sur_pistes_libres():
    d = doc()
    E.put_captions(d, [])
    a = E.add_text(d, "Un", 0, 3, {"size": 100})
    b = E.add_text(d, "Deux", 1, 3, {"size": 100})
    cap = E.captions_track(d)["id"]
    assert a["track"] != b["track"] and cap not in (a["track"], b["track"])
    assert d["tracks"][0]["kind"] == "text"                # les textes passent devant
    o1 = E.add_overlay(d, MEDIA["img"], 2, 2)
    o2 = E.add_overlay(d, MEDIA["m1"], 3, 2, 5.0)
    assert o1["track"] != o2["track"] and o2["muted"] and o2["in"] == 5.0
    assert not E.is_main(d, o1["track"])
    s = E.add_audio(d, MEDIA["a1"], 1.0, 2.0, track_name="Effets sonores")
    assert E.find_track(d, s["track"])["kind"] == "audio"
    model.normalize_clips(d["clips"], model.normalize_tracks(d["tracks"]), list(MEDIA.values()))


def test_cadrage_sur_le_sujet():
    c = {"kind": "video", "in": 0.0, "dur": 4.0, "speed": 1, "fit": "cover", "scale": 1.0, "x": 0.5, "y": 0.5}
    m = {"w": 1080, "h": 1920, "subject": {"track": [[0.0, 0.3, 0.5, 0.2, 0.3], [2.0, 0.3, 0.5, 0.2, 0.3]]}}
    assert E.frame_subject(c, m, model.canvas_for("9:16"))
    assert c["scale"] > 2 and c["x"] > 0.5
    assert not E.frame_subject(dict(c), {"w": 10, "h": 10}, model.canvas_for("9:16"))


def test_regenerer_les_sous_titres_retire_ceux_des_autres_pistes():
    d = doc()
    old = {"id": "k0", "track": "", "kind": "text", "auto": True, "start": 0.0, "dur": 1.0, "words": []}
    E.put_captions(d, [old])
    other = E.add_track(d, "text", "Texte 6")
    stray = dict(old, id="k9", track=other["id"])          # une ligne rangée ailleurs par un chevauchement
    d["clips"].append(stray)
    title = E.add_text(d, "Titre", 0, 2, {"size": 90})
    E.put_captions(d, [])
    assert [c["id"] for c in d["clips"]] == [title["id"]]


def test_une_coupe_ne_tombe_jamais_au_milieu_d_un_mot():
    from engine.agent.service import snap_to_words
    ws = [w("90%,", 7.77, 8.37), w("c'est-à-dire", 8.37, 8.53), w("et", 9.73, 10.15), w("tu", 10.15, 10.61)]
    assert snap_to_words(ws, 1.10, 8.42) == (1.10, 8.37)          # « c'est-à-dire » à peine entamé : dehors
    assert snap_to_words(ws, 1.10, 8.50) == (1.10, 8.65)          # presque entier : gardé, avec un peu d'air
    assert snap_to_words(ws, 10.10, 12.0) == (10.15, 12.0)        # la fin de « et » ne passe pas
    assert snap_to_words(ws, 9.80, 12.0) == (9.69, 12.0)          # « et » gardé, attaque comprise
    assert snap_to_words(ws, 1.10, 10.61) == (1.10, 10.73)        # fin pile sur un mot : on laisse respirer
    sounding = [dict(w, cs=w["start"], ce=w["end"] + 0.25) for w in ws]
    assert snap_to_words(sounding, 1.10, 10.61)[1] == 10.98       # fin SONORE de « tu » + souffle


def test_fin_reelle_des_mots():
    """La voix d'une fin de phrase dure après la date de Whisper : on la suit
    dans l'enveloppe, sans dépasser le mot suivant."""
    import numpy as np
    from engine.timeline.sound import refine_words
    env = np.full(300, -70.0, np.float32)            # 3 s de silence (-70 dB)
    env[50:140] = -20                                # « bonjour » : 0.5-1.4 s de voix
    env[150:200] = -22                               # « à » : 1.5-2.0 s
    words = [{"text": "bonjour", "start": 0.55, "end": 1.2}, {"text": "à", "start": 1.5, "end": 1.8}]
    out = refine_words(words, env)
    assert out[0]["ce"] == pytest.approx(1.4, abs=0.02)            # prolongé jusqu'à la fin du son
    assert out[0]["cs"] == pytest.approx(0.5, abs=0.02)            # attaque avancée
    assert out[1]["ce"] == pytest.approx(2.0, abs=0.02)
    assert out[0]["start"] == 0.55 and out[0]["end"] == 1.2       # dates des sous-titres inchangées
    env2 = env.copy()
    env2[140:150] = -20                              # la voix enchaîne sur le mot suivant
    assert refine_words(words, env2)[0]["ce"] <= 1.5


def test_zoom_plafonne_selon_la_resolution():
    cv = model.canvas_for("9:16")
    assert E.zoom_cap({"w": 2160, "h": 3840}, cv) == pytest.approx(3.4)      # 4K : de la marge
    assert E.zoom_cap({"w": 720, "h": 1280}, cv) == 1.13 or E.zoom_cap({"w": 720, "h": 1280}, cv) < 1.2
    assert E.zoom_cap({"w": 480, "h": 854}, cv) == 1.0                       # déjà agrandi : pas de zoom


def test_cadrer_le_visage_au_tiers_haut():
    cv = model.canvas_for("9:16")
    m = {"w": 1080, "h": 1920}
    x, y = E.frame_on({"x": 0.62, "y": 0.35}, m, cv, 1.4)
    gw, gh = 1080 * 1.4, 1920 * 1.4
    face_x = x * 1080 + (0.62 - 0.5) * gw
    face_y = y * 1920 + (0.35 - 0.5) * gh
    assert face_x == pytest.approx(540, abs=1) and face_y == pytest.approx(0.4 * 1920, abs=1)
    # visage collé au bord : le plan reste plein (aucun bord découvert)
    x, y = E.frame_on({"x": 0.98, "y": 0.02}, m, cv, 1.4)
    assert x * 1080 - gw / 2 <= 0 and x * 1080 + gw / 2 >= 1080
    assert y * 1920 - gh / 2 <= 0


def test_rythme_dynamique():
    words = [w("Ça", 0.0, 0.2), w("paraît", 0.2, 0.5), w("illégal,", 0.5, 1.0), w("mais", 1.3, 1.5),
             w("avec", 1.5, 1.7), w("ce", 1.7, 1.8), w("logiciel,", 1.8, 2.4), w("tu", 2.4, 2.5),
             w("peux", 2.5, 2.7), w("espionner", 2.7, 3.3), w("n'importe", 3.3, 3.7), w("qui.", 3.7, 4.2)]
    pts = E.clause_points(words)
    assert pts == [1.02, 2.42, 4.22]
    d = doc()
    media = {"m1": dict(MEDIA["m1"], w=2160, h=3840, duration=12.0)}
    E.assemble(d, media, lambda mid: [], [{"media": "m1", "start": 0, "end": 12}], remove_silences=False,
               remove_fillers=False)
    n = E.apply_rhythm(d, media, {"m1": [1.02, 2.42, 4.22, 6.1, 7.5, 9.0, 10.4]},
                       lambda mid, a, b: {"x": 0.5, "y": 0.3}, "dynamic")
    shots = E.main_clips(d)
    assert len(shots) >= 5 and all(s["dur"] <= 2.8 + 1.1 + 1e-6 for s in shots)
    scales = [s["scale"] for s in shots]
    assert scales[:4] == [1.0, 1.3, 1.12, 1.42] and n >= 3
    assert shots[0].get("anim_loop", {}).get("type") == "zoom_slow"       # poussée lente sur le plan large


def test_une_courte_phrase_apres_un_tic_reste():
    """« Du coup, je l'ai fait. » : couper « du coup » n'entame pas « je », et
    la phrase qui reste (0,3 s) n'est pas jetée comme un simple souffle."""
    ws = [w("place.", 4.49, 4.89), w("Du", 5.19, 5.47), w("coup,", 5.47, 5.67), w("je", 5.67, 5.69),
          w("l'ai", 5.69, 5.81), w("fait.", 5.81, 5.94)]
    cuts = E.filler_cuts(ws, 0.05)
    assert cuts == [[pytest.approx(5.14), pytest.approx(5.67)]]
    clip = {"in": 1.7, "dur": 4.35, "speed": 1}
    got = E.clip_cuts(clip, words=ws, max_gap=0.3, pad=0.08, tail=0.12, fillers=True, min_keep=0.35)
    keep = E.keep_ranges(1.7, 6.05, got)
    assert any(x <= 5.68 and y >= 5.93 for x, y in keep)
