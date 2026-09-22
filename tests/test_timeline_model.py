"""Validation de l'état envoyé par l'éditeur timeline."""
from __future__ import annotations

import pytest

from engine.timeline import model

VIDEO = {"id": "m1", "kind": "video", "duration": 10.0, "has_audio": True}
MUTE = {"id": "m2", "kind": "video", "duration": 5.0, "has_audio": False}
SONG = {"id": "m3", "kind": "audio", "duration": 60.0, "has_audio": True}
PHOTO = {"id": "m4", "kind": "image", "duration": 0.0}
MEDIA = [VIDEO, MUTE, SONG, PHOTO]


def tracks():
    return model.default_tracks() + [{"id": "tt1", "kind": "text", "name": "Texte"}]


def clip(**kw):
    base = {"id": "c1", "track": "tv1", "kind": "video", "media": "m1", "start": 0, "dur": 2,
            "in": 0}
    base.update(kw)
    return base


def norm(*clips):
    return model.normalize_clips(list(clips), model.normalize_tracks(tracks()), MEDIA)


def test_etat_neuf():
    s = model.new_state("p1", "  Mon film ", "16:9", now=12.0)
    assert s["kind"] == "timeline" and s["name"] == "Mon film"
    assert s["canvas"] == {"w": 1920, "h": 1080, "fps": 30, "bg": "#000000"}
    assert [t["kind"] for t in s["tracks"]] == ["video", "audio"]
    assert s["tracks"][0]["main"]


def test_format_inconnu_retombe_sur_9_16():
    assert model.canvas_for("n'importe") == model.canvas_for("9:16")


def test_canvas_borne_pair_et_fps_proposes():
    c = model.normalize_canvas({"w": 1081, "h": 99999, "fps": 29, "bg": "red"})
    assert c == {"w": 1080, "h": 4096, "fps": 30, "bg": "#000000"}
    assert model.normalize_canvas({"bg": "#abc"})["bg"] == "#AABBCC"


def test_pistes_sans_video_recoivent_une_piste_principale():
    out = model.normalize_tracks([{"id": "a", "kind": "audio"}, {"kind": "banane"}])
    assert [t["kind"] for t in out] == ["video", "audio"]
    assert out[0]["main"]


def test_une_seule_piste_principale():
    out = model.normalize_tracks([
        {"id": "v2", "kind": "video", "main": True},
        {"id": "v1", "kind": "video", "main": True},
        {"id": "v1", "kind": "audio", "main": True},     # id en double, audio
    ])
    assert [t["main"] for t in out] == [False, True, False]
    assert len({t["id"] for t in out}) == 3


def test_clip_valide_et_champs_par_defaut():
    [c] = norm(clip())
    assert c["kind"] == "video" and c["speed"] == 1.0 and c["volume"] == 1.0
    assert c["fit"] == "cover" and c["x"] == 0.5 and not c["detached"]


def test_clip_borne_a_la_fin_du_media():
    [c] = norm(clip(**{"in": 8.0, "dur": 5.0}))
    assert c["dur"] == pytest.approx(2.05)                # 10 s de média, marge d'arrondi
    [c] = norm(clip(**{"in": 8.0, "dur": 5.0, "speed": 2.0}))
    assert c["dur"] == pytest.approx(1.025)


def test_clips_impossibles_ecartes():
    assert norm(clip(media="inconnu")) == []
    assert norm(clip(track="inconnue")) == []
    assert norm(clip(kind="audio")) == []                        # audio sur piste vidéo
    assert norm(clip(**{"in": 20.0})) == []                      # au-delà du média
    assert norm(clip(dur=0.01)) == []
    assert norm(clip(track="ta1", kind="audio", media="m2")) == []   # vidéo muette


def test_son_d_une_video_sur_piste_audio():
    [c] = norm(clip(track="ta1", kind="audio", media="m1"))
    assert c["kind"] == "audio" and "x" not in c


def test_image_sans_limite_de_duree():
    [c] = norm(clip(kind="image", media="m4", dur=120, speed=3))
    assert c["dur"] == 120 and c["speed"] == 1.0


def test_valeurs_bornees():
    [c] = norm(clip(volume=9, speed=100, opacity=-1, fit="zoom", fade_in=50))
    assert (c["volume"], c["speed"], c["opacity"], c["fit"]) == (2.0, 16.0, 0.0, "cover")
    assert c["fade_in"] <= c["dur"]


def test_chevauchement_le_clip_pose_apres_gagne():
    a = clip(id="a", start=0, dur=4)
    b = clip(id="b", start=3, dur=2, **{"in": 5})
    out = norm(a, b)
    assert [(c["id"], c["start"], c["dur"]) for c in out] == [("a", 0, 3), ("b", 3, 2)]
    covered = clip(id="c", start=3.01, dur=1)
    assert [c["id"] for c in norm(clip(id="x", start=3, dur=0.5), covered)] == ["c"]


def test_identifiants_uniques():
    out = norm(clip(id="a"), clip(id="a", start=5))
    assert len({c["id"] for c in out}) == 2


def test_texte_nettoye():
    t = {"id": "t", "track": "tt1", "kind": "text", "start": 1, "dur": 2, "size": "90",
         "color": "vert", "mode": "clignote",
         "words": [{"text": "Salut", "start": 1, "end": 1.4, "m": "m1", "s": 3.2, "e": 3.6},
                   {"text": "  "}, "pas un mot"]}
    [c] = norm(t)
    assert c["size"] == 90 and c["color"] == "#FFFFFF" and c["mode"] == "word"
    assert c["words"] == [{"text": "Salut", "start": 1, "end": 1.4, "m": "m1", "s": 3.2, "e": 3.6}]


def test_reglages():
    s = model.normalize_settings({"max_gap": 99, "style": "inconnu", "language": "FR",
                                  "device": "tpu"})
    assert s["max_gap"] == 5.0 and s["style"] == "hype" and s["language"] == "fr"
    assert s["device"] == "auto"
    assert model.normalize_settings({})["language"] is None


def test_sauvegarde_ne_touche_pas_aux_medias():
    state = model.new_state("p", "x")
    state["media"] = [VIDEO]
    model.apply_client_state(state, {"media": [], "name": " Nouveau ", "clips": [clip()]})
    assert state["media"] == [VIDEO] and state["name"] == "Nouveau"
    assert len(state["clips"]) == 1


def test_supprimer_une_piste_emporte_ses_clips():
    state = model.new_state("p", "x")
    state["media"] = [SONG]
    model.apply_client_state(state, {"clips": [clip(track="ta1", kind="audio", media="m3")]})
    assert len(state["clips"]) == 1
    model.apply_client_state(state, {"tracks": [state["tracks"][0]]})
    assert state["clips"] == []


def test_duree():
    assert model.duration([]) == 0
    assert model.duration([{"start": 1, "dur": 2}, {"start": 0.5, "dur": 4}]) == 4.5


def test_reglages_image_et_mots_coupes_conserves():
    [c] = norm(clip(filters={"brightness": 0.3, "contrast": 0, "saturation": 5, "bidon": 1}))
    assert c["filters"] == {"brightness": 0.3, "saturation": 1.0}
    [n] = norm(clip(filters={"brightness": 0}))
    assert "filters" not in n
    t = {"id": "t", "track": "tt1", "kind": "text", "start": 0, "dur": 2, "gone": True,
         "words": [{"text": "a", "start": 0, "end": 1, "cut": True}, {"text": "b", "start": 1, "end": 2}]}
    [c] = norm(t)
    assert c["gone"] and c["words"][0]["cut"] and "cut" not in c["words"][1]
