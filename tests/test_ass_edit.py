"""Traduction WYSIWYG des sous-titres édités en .ass."""
from __future__ import annotations

import re

import pytest

from engine.pipeline.ass_edit import (_alpha, _esc, _rgb, _ts, build_ass_edited,
                                      emoji_geometry)
from engine.pipeline.style_presets import preset


@pytest.mark.parametrize("value, expected", [
    ("#FF8800", "&H0088FF&"),
    ("ff8800", "&H0088FF&"),
    ("#abc", "&HCCBBAA&"),
    ("#102030 ", "&H302010&"),
    ("#zzzzzz", "&HFFFFFF&"),
    (None, "&HFFFFFF&"),
    ("", "&HFFFFFF&"),
])
def test_rgb(value, expected):
    assert _rgb(value) == expected


def test_rgb_blanc_avant_diese():
    # Régression : un blanc devant le « # » faisait retomber la couleur sur le blanc.
    assert _rgb(" #102030") == "&H302010&"


@pytest.mark.parametrize("value, expected", [
    (0, "&H00&"), (1, "&HFF&"), (0.25, "&H40&"), (2.5, "&HFF&"), (-1, "&H00&"),
    ("0.5", "&H80&"), ("abc", "&H00&"), (None, "&H00&"),
])
def test_alpha(value, expected):
    assert _alpha(value) == expected


@pytest.mark.parametrize("t, expected", [
    (0, "0:00:00.00"), (-1.0, "0:00:00.00"), (1.234, "0:00:01.23"),
    (61.5, "0:01:01.50"), (3725.07, "1:02:05.07"), (9.996, "0:00:10.00"),
])
def test_ts(t, expected):
    assert _ts(t) == expected


@pytest.mark.parametrize("t, expected", [(59.999, "0:01:00.00"), (3599.999, "1:00:00.00")])
def test_ts_report_des_secondes(t, expected):
    # Régression : l'arrondi à 100 cs donnait 0:00:60.00 et 0:59:60.00.
    assert _ts(t) == expected


def test_esc():
    assert _esc("a{b}\\c") == "a(b)/c"
    assert _esc("ligne 1\nligne 2\r\nligne 3") == r"ligne 1\Nligne 2\Nligne 3"
    assert _esc("") == ""
    assert _esc(42) == "42"


# ------------------------------------------------------------ build_ass_edited


def caption(mode="word", **over):
    c = {**preset("classic"), "id": "c0", "start": 1.0, "end": 2.5, "mode": mode,
         "emoji": "", "words": [{"text": "salut", "start": 1.0, "end": 1.4},
                                {"text": "à", "start": 1.5, "end": 1.7},
                                {"text": "tous", "start": 1.8, "end": 2.5}]}
    c.update(over)
    return c


def dialogues(path):
    text = path.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


def test_mode_word_une_ligne_par_mot(tmp_path):
    out = tmp_path / "caps.ass"
    build_ass_edited([caption("word", hl="#00FF00")], str(out))
    lines = dialogues(out)
    assert len(lines) == 3
    # Chaque mot est surligné tour à tour, du début de la ligne à sa fin.
    assert [ln.split(",")[1:3] for ln in lines] == [
        ["0:00:01.00", "0:00:01.50"], ["0:00:01.50", "0:00:01.80"],
        ["0:00:01.80", "0:00:02.50"]]
    for k, ln in enumerate(lines):
        assert all(w in ln for w in ("salut", "à", "tous"))
        hl = re.search(r"\{\\1c&H00FF00&[^}]*\}(\w+)", ln)
        assert hl and hl.group(1) == ["salut", "à", "tous"][k]


def test_mode_sweep_une_seule_ligne_karaoke(tmp_path):
    out = tmp_path / "caps.ass"
    build_ass_edited([caption("sweep")], str(out))
    lines = dialogues(out)
    assert len(lines) == 1
    assert re.findall(r"\\kf(\d+)", lines[0]) == ["50", "30", "70"]


def test_mode_none_texte_statique(tmp_path):
    out = tmp_path / "caps.ass"
    build_ass_edited([caption("none", upper=True, box=True)], str(out))
    lines = dialogues(out)
    assert len(lines) == 1
    assert "\\kf" not in lines[0]
    assert lines[0].endswith("SALUT À TOUS")
    assert ",Box," in lines[0]


def test_lignes_ignorees(tmp_path):
    out = tmp_path / "sub" / "caps.ass"
    caps = [caption(hidden=True, emoji="🔥"),
            caption(id="vide", words=[{"text": " ", "start": 1, "end": 2}]),
            caption(id="inversee", start=3.0, end=3.0)]
    assert build_ass_edited(caps, str(out)) == []
    assert dialogues(out) == []


def test_en_tete_et_echappement(tmp_path):
    out = tmp_path / "caps.ass"
    build_ass_edited([caption("none", words=[{"text": "{\\b1}x", "start": 1, "end": 2}])],
                     str(out), width=720, height=1280)
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 720" in text and "PlayResY: 1280" in text
    assert dialogues(out)[0].endswith("(/b1)x")


def test_emojis_en_pixels_de_sortie(tmp_path):
    out = tmp_path / "caps.ass"
    caps = [
        caption("none", emoji="🔥", x=0.25, y=0.5, size=80, emoji_dx=10),
        caption("none", id="c1", start=4.0, end=5.0, emoji=" 💡 ", x=0.5, y=0.8,
                size=100, emoji_dy=-50, emoji_size=1000),
        caption("none", id="c2", start=6.0, end=6.0, emoji="💥"),
    ]
    emojis = build_ass_edited(caps, str(out), width=1080, height=1920)
    _, dy = emoji_geometry(80)
    assert emojis == [
        {"char": "🔥", "x": 280, "y": round(960 + dy), "size": 124, "start": 1.0, "end": 2.5},
        {"char": "💡", "x": 540, "y": 1486, "size": 600, "start": 4.0, "end": 5.0},
    ]
