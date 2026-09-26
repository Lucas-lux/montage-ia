"""Détourage : choix du sujet, trajectoire, et export d'un clip détouré ou qui suit son sujet."""
from __future__ import annotations

import numpy as np
import pytest

from engine.pipeline import matting
from engine.pipeline import style_presets as sp
from engine.pipeline import fonts
from engine.timeline import animations, render


def _blobs():
    """Deux personnes : une à gauche, une à droite."""
    m = np.zeros((200, 300), np.float32)
    m[40:190, 20:110] = 1.0
    m[60:190, 190:280] = 0.9
    return m


def test_le_clic_choisit_le_sujet():
    m = _blobs()
    left, box = matting.keep_subject(m, (0.2, 0.5))
    assert left[100, 60] > 0.9 and left[100, 230] == 0
    assert box[0] < 0.1 and box[2] < 0.35
    right, _ = matting.keep_subject(m, (0.95, 0.1))          # hors des deux : la plus proche
    assert right[100, 230] > 0.8 and right[100, 60] == 0
    # une seule personne : tout est gardé ; personne : masque vide
    one = np.zeros((200, 300), np.float32)
    one[20:180, 100:200] = 1
    assert matting.keep_subject(one, (0.9, 0.9))[0].sum() == one.sum()
    assert matting.keep_subject(one * 0, (0.5, 0.5))[1] is None


def test_masque_resserre_et_trajectoire():
    assert matting.refine(np.array([0.1, 0.5, 0.9])).tolist() == pytest.approx([0.0, 0.5, 1.0])
    track = [(0.0, 0.2, 0.5), (1.0, 0.6, 0.5)]
    assert matting._at(track, 0.5) == pytest.approx((0.4, 0.5))
    assert matting._at(track, 9.0) == (0.6, 0.5)
    smooth = matting._smooth([(t / 10, 0.5 + t / 100, 0.5, 0.3, 0.6) for t in range(21)], 0.4)
    assert len(smooth) == 21 and all(isinstance(v, float) for v in smooth[0])


def test_styles_valides():
    names = {f["name"] for f in fonts.bundled()} | set(fonts.SYSTEM_FONTS)
    kinds = {"anim_in": "in", "anim_out": "out", "anim_loop": "loop"}
    for p in sp.catalog():
        assert p["font"] in names, p["name"]
        assert p["group"] in sp.GROUPS and p["mode"] in sp.MODES, p["name"]
        for key, kind in kinds.items():
            if key in p:
                assert animations.normalize(p[key], kind, "text"), (p["name"], key)
    assert len(sp.catalog()) >= 70


def test_suivre_le_sujet_sans_decouvrir_le_bord():
    media = {"subject": {"track": [[0.0, 0.3, 0.5, 0.2, 0.5], [2.0, 0.7, 0.5, 0.2, 0.5]]}}
    c = {"start": 0.0, "dur": 2.0, "in": 0.0, "speed": 1.0, "follow": True}
    g = {"sw": 3413.0, "sh": 1920.0, "cx": 540.0, "cy": 960.0}      # 16:9 en « remplir » dans du 9:16
    dx0, _ = render.follow_offset(c, media, g, 1080, 1920, 0.0)
    dx1, _ = render.follow_offset(c, media, g, 1080, 1920, 2.0)
    assert dx0 > 0 > dx1                         # le sujet va à droite : l'image part à gauche
    assert abs(dx0) <= (3413 - 1080) / 2 + 1e-6    # jamais au-delà du bord
    assert render.follow_offset(dict(c, follow=False), media, g, 1080, 1920, 1.0) == (0.0, 0.0)


def test_export_d_un_clip_detoure(tmp_path):
    matte = tmp_path / "matte.mp4"
    matte.write_bytes(b"x")
    media = {"m1": {"id": "m1", "kind": "video", "path": str(tmp_path / "v.mp4"), "w": 1920, "h": 1080, "fps": 30,
                    "duration": 10, "has_audio": False, "subject": {"status": "done", "matte": str(matte),
                                                                    "track": [[0, 0.5, 0.5, 0.3, 0.6]]}}}
    state = {"canvas": {"w": 1080, "h": 1920, "fps": 30, "bg": "#000000"},
             "tracks": [{"id": "tv1", "kind": "video", "main": True}],
             "clips": [{"id": "c1", "track": "tv1", "kind": "video", "media": "m1", "start": 0, "dur": 2, "in": 1,
                        "speed": 1, "x": 0.5, "y": 0.5, "scale": 1, "cutout": True, "follow": True}]}
    g = render.build(state, media, 1080, 1920, 30, str(tmp_path))
    assert str(matte) in g["inputs"] and "alphamerge" in g["graph"]
    assert "sendcmd=f=cmd_" in g["graph"]          # le suivi passe par des commandes image par image
    state["clips"][0]["cutout"] = False
    assert "alphamerge" not in render.build(state, media, 1080, 1920, 30, str(tmp_path))["graph"]
