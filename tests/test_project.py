"""État d'un montage, sans Whisper ni ffmpeg : mots posés à la main."""
from __future__ import annotations

import json

import pytest

from engine import store
from engine.core import Options
from engine.edl import Word
from engine.pipeline.probe import MediaInfo
from engine.project import Project

approx = pytest.approx

# Source de 8 s : un « euh » en tête de phrase, un long blanc, une fin muette.
WORDS = [("Bonjour", 1.0, 1.4), ("euh", 1.5, 1.9), ("tout", 2.0, 2.4), ("le", 2.5, 2.9),
         ("monde", 3.0, 3.4), ("ça", 6.0, 6.4), ("va", 6.5, 6.9)]
DURATION = 8.0


def make_project(tmp_path, **opts) -> Project:
    base = dict(cut_silence=True, cut_fillers=True, max_gap=0.5, pad=0.08, language="fr")
    proj = Project("p1", str(tmp_path / "source.mp4"), str(tmp_path / "work"),
                   Options(**{**base, **opts}), str(tmp_path / "out.mp4"))
    proj.info = MediaInfo(duration=DURATION, width=1920, height=1080, fps=30.0)
    proj.words = [Word(text=t, start=s, end=e) for t, s, e in WORDS]
    proj._apply_cuts()
    return proj


def stitch_at(proj, start):
    return next((s for s in proj.stitches() if s["start"] == approx(start)), None)


def test_coupes_et_stitches(tmp_path):
    proj = make_project(tmp_path)
    assert [(k.start, k.end) for k in proj.keep] == [
        approx((0.92, 1.45)), approx((1.95, 3.48)), approx((5.92, 6.98))]
    assert proj.duration == approx(3.12)

    st = proj.stitches()
    assert [(s["kind"], s["text"]) for s in st] == [
        ("silence", ""), ("filler", "euh"), ("silence", ""), ("silence", "")]
    assert st[0]["t"] == 0 and st[0]["start"] == 0
    assert st[-1]["t"] == approx(proj.duration) and st[-1]["end"] == DURATION
    assert sum(s["removed"] for s in st) + proj.duration == approx(DURATION)
    # Les mots coupés disparaissent de la sortie, les autres gardent leur index source.
    assert [w["i"] for w in proj.out_words] == [0, 2, 3, 4, 5, 6]


def test_stitch_contient_les_mots_coupes(tmp_path):
    proj = make_project(tmp_path, cut_silence=False, cut_fillers=False,
                        manual_cuts=[(2.45, 3.45)])
    assert proj.stitches() == [{"t": 2.45, "removed": 1.0, "start": 2.45, "end": 3.45,
                                "kind": "manual", "text": "le monde"}]


def test_keep_ranges_restaure_un_passage(tmp_path):
    proj = make_project(tmp_path)
    before = proj.duration
    assert stitch_at(proj, 1.45)["kind"] == "filler"

    proj.opts.keep_ranges = [[1.45, 1.95]]
    proj._apply_cuts()
    assert stitch_at(proj, 1.45) is None
    assert all(kind != "filler" for _, _, kind in proj.cuts)
    assert proj.duration == approx(before + 0.5)
    assert 1 in [w["i"] for w in proj.out_words]


def test_keep_range_partiel_rogne_le_blanc(tmp_path):
    proj = make_project(tmp_path, keep_ranges=[[3.0, 4.0]])
    assert proj.duration == approx(3.12 + (4.0 - 3.48))
    assert stitch_at(proj, 4.0)["end"] == approx(5.92)


def test_tout_coupe_leve_une_erreur(tmp_path):
    with pytest.raises(RuntimeError):
        make_project(tmp_path, manual_cuts=[(0.0, DURATION)])


def test_to_dict(tmp_path, fake_model):
    proj = make_project(tmp_path, keep_ranges=[[1.45, 1.95]])
    proj.captions = proj._build_captions()
    d = proj.to_dict()
    assert d["kept"] == [{"start": 1.45, "end": 1.95}]
    assert d["language"] == "fr"
    assert d["translate"] == {"available": True}
    assert (d["width"], d["height"]) == (1080, 1920)
    assert d["duration"] == approx(proj.duration, abs=1e-3)
    assert d["removed"] == approx(DURATION - proj.duration, abs=1e-3)
    assert d["stitches"] == proj.stitches()
    assert [c["id"] for c in d["captions"]] == [c["id"] for c in proj.captions]


def test_to_dict_sans_modele_et_sans_langue(tmp_path):
    proj = make_project(tmp_path, language=None)
    d = proj.to_dict()
    assert d["language"] == ""
    assert d["translate"] == {"available": False}
    assert d["kept"] == []


def test_captions_suivent_l_index_source(tmp_path):
    proj = make_project(tmp_path, words_per_line=2, max_chars=40, emojis=False)
    caps = proj._build_captions()
    assert [c["id"] for c in caps] == ["c0", "c3", "c5"]
    for c in caps:
        assert c["words"] and c["start"] < c["end"] <= proj.duration + 1e-6
        assert c["emoji"] == ""


def test_save_puis_load(tmp_path):
    proj = make_project(tmp_path, keep_ranges=[[1.45, 1.95]])
    proj.captions = proj._build_captions()
    proj.captions[0]["words"][0]["text"] = "Salut"
    proj.save()

    again = Project.load(str(tmp_path / "work"), "p1")
    assert again is not None
    assert [(k.start, k.end) for k in again.keep] == [(k.start, k.end) for k in proj.keep]
    assert again.stitches() == proj.stitches()
    assert again.captions == proj.captions
    assert again.language == "fr"
    assert again.task["status"] == "done"


def test_load_etat_ecrit_a_la_main(tmp_path):
    work = str(tmp_path / "work")
    state = {
        "id": "manuel", "name": "Démo", "source": str(tmp_path / "demo.mp4"),
        "opts": {"cut_silence": True, "cut_fillers": False, "vertical": False,
                 "champ_obsolete": 1},
        "info": {"duration": DURATION, "width": 1280, "height": 720, "fps": 25.0},
        "words": [{"text": t, "start": s, "end": e} for t, s, e in WORDS],
    }
    store.project_dir(work, "manuel", create=True)
    with open(store.state_path(work, "manuel"), "w", encoding="utf-8") as f:
        json.dump(state, f)

    proj = Project.load(work, "manuel")
    assert proj.name == "Démo"
    assert (proj.out_w, proj.out_h) == (1280, 720)
    assert proj.captions and proj.captions[0]["id"] == "c0"
    assert any(s["text"] == "" and s["start"] == approx(3.48) for s in proj.stitches())


def test_load_projet_incomplet(tmp_path):
    work = str(tmp_path / "work")
    store.write_state(work, "vide", {"id": "vide", "source": "x.mp4", "words": [],
                                     "info": {"duration": 1, "width": 1, "height": 1,
                                              "fps": 1}})
    assert Project.load(work, "vide") is None
    assert Project.load(work, "absent") is None
