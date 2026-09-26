"""Animations : définitions valides, et mêmes valeurs en Python (export) et en
JavaScript (aperçu)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from engine.timeline import animations as A

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = [i / 40 for i in range(41)]


def test_definitions_coherentes():
    defs = A.definitions()
    assert len(defs) >= 40
    for name, d in defs.items():
        assert d["kind"] in ("in", "out", "loop"), name
        assert d["label"], name
        assert d.get("ease", "linear") in A.EASE, name
        frames = d.get("kf") or d.get("ukf")
        assert frames, name
        for i, k in enumerate(frames):
            assert 0 <= k[0] <= 1 and set(k[1]) <= set(A.IDENTITY), (name, k)
            if len(k) > 2:
                assert k[2] in A.EASE, (name, k)
            if i:
                assert k[0] > frames[i - 1][0], name
        if d["kind"] == "in":            # une entrée finit au repos
            assert A.sample(d.get("kf"), 1.0) == A.IDENTITY or "ukf" in d, name
        if d["kind"] == "out":           # une sortie part du repos
            assert A.sample(d.get("kf"), 0.0) == A.IDENTITY or "ukf" in d, name


def test_courbes_aux_bornes():
    for name in A.EASE:
        assert A.ease(name, 0) == pytest.approx(0, abs=1e-9), name
        assert A.ease(name, 1) == pytest.approx(1, abs=1e-9), name
    assert A.ease("outBack", 0.8) > 1          # dépasse puis revient


def test_etat_d_un_clip():
    c = {"start": 10.0, "dur": 2.0, "anim_in": {"type": "fade", "dur": 0.5},
         "anim_out": {"type": "zoom_out_out", "dur": 0.5}}
    assert A.state(c, 10.0)["o"] == pytest.approx(0)
    assert A.state(c, 10.25)["o"] == pytest.approx(0.5)
    assert A.state(c, 11.0) == A.IDENTITY
    end = A.state(c, 12.0)
    assert end["o"] == pytest.approx(0) and end["s"] == pytest.approx(0.3)
    assert A.windows(c) == [(10.0, 10.5), (11.5, 12.0)]
    # clip trop court : entrée et sortie se partagent sa durée
    short = dict(c, dur=0.5)
    assert A.timing(short) == (pytest.approx(0.25), pytest.approx(0.25))


def test_lettre_a_lettre():
    c = {"start": 0.0, "dur": 3.0, "anim_in": {"type": "typewriter", "dur": 1.0}}
    st = A.state(c, 0.5)
    ops = [u["o"] for u in A.unit_state(st, "char", 10)]
    assert ops == [1.0] * 6 + [0.0] * 4          # la 6e lettre apparaît pile à mi-parcours
    assert A.unit_state(A.state(c, 2.0), "char", 10) == [{"o": 1.0, "b": 0.0}] * 10
    assert A.unit_state(st, "word", 3) is None


def test_normalisation():
    assert A.normalize({"type": "pop", "dur": 99}, "in", "media") == {"type": "pop", "dur": 5.0}
    assert A.normalize({"type": "pop"}, "out", "media") is None               # une entrée
    assert A.normalize({"type": "typewriter"}, "in", "media") is None         # texte seulement
    assert A.normalize({"type": "pulse", "speed": 10}, "loop", "text") == {"type": "pulse", "speed": 4.0}
    assert A.normalize("pop", "in", "text") is None


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_meme_valeurs_que_l_apercu():
    """Chaque animation, sur une grille de progressions, et des états de clips
    complets : Python et JavaScript doivent donner les mêmes nombres."""
    clips = [
        {"start": 1.0, "dur": 2.0, "anim_in": {"type": name, "dur": 0.6}} for name, d in A.definitions().items()
        if d["kind"] == "in"
    ] + [
        {"start": 1.0, "dur": 2.0, "anim_out": {"type": name, "dur": 0.6}} for name, d in A.definitions().items()
        if d["kind"] == "out"
    ] + [
        {"start": 1.0, "dur": 2.0, "anim_loop": {"type": name, "speed": 1.3}} for name, d in A.definitions().items()
        if d["kind"] == "loop"
    ]
    times = [1.0 + i * 0.037 for i in range(60)]
    script = """
import fs from "node:fs";
import * as A from "%s";
const defs = JSON.parse(fs.readFileSync(%s, "utf-8"));
A.setDefinitions(defs);
const clips = JSON.parse(fs.readFileSync(0, "utf-8"));
const out = { samples: {}, states: [] };
for (const [name, d] of Object.entries(defs.anims)) {
  out.samples[name] = %s.map((p) => A.sample(d.kf || d.ukf, p, d.ease || "linear"));
}
for (const c of clips) {
  out.states.push(%s.map((t) => {
    const st = A.state(c, t);
    return { ...st, units: undefined, chars: A.unitState(st, "char", 7), words: A.unitState(st, "word", 3) };
  }));
}
process.stdout.write(JSON.stringify(out));
""" % ("file:///" + os.path.join(ROOT, "engine", "web", "studio", "anim.js").replace("\\", "/"),
       json.dumps(A.DEFS_PATH), json.dumps(GRID), json.dumps(times))
    res = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(clips),
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    for name, d in A.definitions().items():
        for p, v in zip(GRID, js["samples"][name]):
            py = A.sample(d.get("kf") or d.get("ukf"), p, d.get("ease", "linear"))
            for k in A.IDENTITY:
                assert py[k] == pytest.approx(v[k], abs=1e-9), (name, p, k)
    for c, row in zip(clips, js["states"]):
        for t, v in zip(times, row):
            st = A.state(c, t)
            for k in A.IDENTITY:
                assert st[k] == pytest.approx(v[k], abs=1e-9), (c, t, k)
            for per, n, key in (("char", 7, "chars"), ("word", 3, "words")):
                py = A.unit_state(st, per, n)
                if py is None:
                    assert v[key] is None, (c, t)
                else:
                    assert [u["o"] for u in py] == pytest.approx([u["o"] for u in v[key]], abs=1e-9), (c, t)
                    assert [u["b"] for u in py] == pytest.approx([u["b"] for u in v[key]], abs=1e-9), (c, t)
