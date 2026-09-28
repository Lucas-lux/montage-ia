"""Zone d'un clip (`box`), mots-clés des sous-titres (`k`, kw…) et vidéo HDR."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from engine.pipeline import ass_edit
from engine.pipeline.ass_edit import build_ass_edited
from engine.pipeline.style_presets import preset
from engine.timeline import media as mediatools
from engine.timeline import model, render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RED = {"id": "mr", "kind": "video", "path": "rouge.mp4", "w": 640, "h": 360, "duration": 4.0,
       "has_audio": True, "status": "ready"}


# ------------------------------------------------------------------ modèle

def test_box_tag_et_mot_cle_gardes_par_le_modele():
    st = model.new_state("p", "t", "9:16")
    st["media"] = [dict(RED)]
    body = {"clips": [
        {"id": "a", "track": "tv1", "kind": "video", "media": "mr", "start": 0, "dur": 2, "in": 0,
         "box": {"x": 0, "y": 0.4583, "w": 1, "h": 0.5417, "r": 30}, "tag": "scene"},
        {"id": "b", "track": "tv1", "kind": "video", "media": "mr", "start": 2, "dur": 1, "in": 0,
         "box": {"x": 0, "y": 0, "w": 0.001, "h": 0.5}}]}
    st = model.apply_client_state(st, body)
    a, b = st["clips"]
    assert a["box"] == {"x": 0.0, "y": 0.4583, "w": 1.0, "h": 0.5417, "r": 30.0} and a["tag"] == "scene"
    assert "box" not in b                               # zone trop petite : ignorée
    words = model._words([{"text": "six", "start": 0, "end": 0.3, "k": True}, {"text": "mois", "start": 0.3,
                                                                                 "end": 0.6}])
    assert words[0]["k"] is True and "k" not in words[1]
    t = model._text_fields({"kw": "", "kw_scale": 1.55, "kw_pop": True})
    assert t["kw"] == "" and t["kw_scale"] == 1.55 and t["kw_pop"] is True


# ------------------------------------------------------------------ export

def state(clips, canvas=None):
    return {"canvas": canvas or {"w": 360, "h": 640, "fps": 25, "bg": "#000000"},
            "tracks": [{"id": "tv1", "kind": "video", "main": True}, {"id": "ta1", "kind": "audio"}],
            "clips": clips}


def vclip(**kw):
    c = {"id": "c1", "track": "tv1", "kind": "video", "media": "mr", "start": 0.0, "dur": 2.0, "in": 0.0,
         "speed": 1.0, "x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0, "opacity": 1, "fit": "cover"}
    c.update(kw)
    return c


def test_zone_dans_le_graphe(tmp_path):
    """Le clip est rendu à la taille de sa zone, puis posé à sa place ; coins arrondis par un masque."""
    c = vclip(box={"x": 0, "y": 0.5, "w": 1, "h": 0.5})
    g = render.build(state([c]), {"mr": RED}, 360, 640, 25, str(tmp_path))["graph"]
    assert "overlay=x=0:y=320" in g
    assert "scale=" in g and "geq" not in g
    c = vclip(box={"x": 0.1, "y": 0.5, "w": 0.8, "h": 0.4, "r": 30})
    g = render.build(state([c]), {"mr": RED}, 360, 640, 25, str(tmp_path))["graph"]
    assert "geq=lum=" in g and "alphamerge" in g and "overlay=x=36:y=320" in g
    assert render.box_rect(c, 1080, 1920) == (108, 960, 864, 768, 30)


@pytest.mark.ffmpeg
def test_zone_rendue_pour_de_vrai(tmp_path):
    """Une vidéo rouge dans une zone arrondie en bas du cadre : rouge dedans, fond dehors,
    coin arrondi transparent."""
    src = tmp_path / "rouge.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=1:r=25",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    m = dict(RED, path=str(src), duration=1.0)
    c = vclip(dur=1.0, box={"x": 0.1, "y": 0.5, "w": 0.8, "h": 0.4, "r": 60})
    st = model.new_state("p", "t", "9:16")
    st.update(canvas={"w": 360, "h": 640, "fps": 25, "bg": "#0000FF", "blur": False},
              tracks=state([])["tracks"], clips=[c], media=[m])
    out = tmp_path / "out.mp4"
    render.export(st, [m], str(out))
    png = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(out), "-frames:v", "1", str(png)], check=True)
    from PIL import Image
    im = Image.open(png).convert("RGB")
    W, H = im.size
    inside = im.getpixel((W // 2, int(H * 0.7)))
    above = im.getpixel((W // 2, int(H * 0.3)))
    corner = im.getpixel((int(W * 0.1) + 2, int(H * 0.5) + 2))
    assert inside[0] > 180 and inside[2] < 80              # rouge dans la zone
    assert above[2] > 180 and above[0] < 80                # fond bleu au-dessus
    assert corner[2] > 150                                 # coin arrondi : le fond passe


def test_mot_cle_couleur_et_taille(tmp_path):
    c = {**preset("net_accent"), "start": 0.0, "end": 2.0, "mode": "none", "kw_pop": False,
         "words": [{"text": "six", "start": 0, "end": 0.5}, {"text": "mois", "start": 0.5, "end": 1.0, "k": True}]}
    out = tmp_path / "c.ass"
    build_ass_edited([c], str(out), 1080, 1920)
    txt = out.read_text(encoding="utf-8")
    body = [l for l in txt.splitlines() if l.startswith("Dialogue")][-1]
    head = body.split("mois")[0].rsplit("{", 1)[1]         # surcharges posées juste avant « mois »
    assert r"\fscx155\fscy155" in head
    assert r"\1c&H3FD2FF&" in head                         # #FFD23F en BGR
    assert r"\fscx100\fscy100" in body.split("six")[0].rsplit("{", 1)[1]


def test_mot_cle_garde_sa_couleur_meme_dit(tmp_path):
    """Mode « reveal » : le mot en cours prend `hl`, sauf un mot-clé dont le style a une couleur."""
    c = {**preset("net_accent"), "start": 0.0, "end": 1.0, "kw_pop": False, "anim_in": None,
         "words": [{"text": "trop", "start": 0, "end": 0.4}, {"text": "vite", "start": 0.4, "end": 0.9, "k": True}]}
    out = tmp_path / "c.ass"
    build_ass_edited([c], str(out), 1080, 1920)
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]
    last = lines[-1]                                       # « vite » en train d'être dit
    assert last.split("vite")[0].rsplit(r"\1c", 1)[1].startswith("&H3FD2FF&")


def test_rebond_image_par_image(tmp_path):
    c = {**preset("net"), "start": 0.0, "end": 2.0, "anim_in": None,
         "words": [{"text": "un", "start": 0, "end": 0.5}, {"text": "mot", "start": 0.5, "end": 1.0, "k": True}]}
    out = tmp_path / "c.ass"
    build_ass_edited([c], str(out), 1080, 1920, fps=25)
    n = sum(1 for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue"))
    assert n >= 7                                          # ~0,28 s image par image autour du mot
    assert ass_edit.kw_factor(c, 0.0) == pytest.approx(1.55 * ass_edit.KW_FROM)
    assert ass_edit.kw_factor(c, 1.0) == pytest.approx(1.55)
    assert max(ass_edit.kw_factor(c, t / 100) for t in range(0, 29)) > 1.55   # dépasse puis revient


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_rebond_meme_courbe_que_l_apercu():
    c = {"kw_scale": 1.55, "kw_pop": True}
    dts = [i * 0.013 - 0.05 for i in range(40)]
    script = ("import { kwFactor } from %s;\nprocess.stdout.write(JSON.stringify(%s.map((d) => kwFactor(%s, d))));"
              % (json.dumps("file:///" + os.path.join(ROOT, "engine", "web", "studio", "textfx.js").replace("\\", "/")),
                 json.dumps(dts), json.dumps(c)))
    res = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    assert [ass_edit.kw_factor(c, d) for d in dts] == pytest.approx(js, abs=1e-9)


# --------------------------------------------------------------------- HDR

def test_hdr_reconnu_et_ramene_en_sdr(tmp_path):
    assert mediatools.is_hdr({"color_transfer": "arib-std-b67"})
    assert mediatools.is_hdr({"color_primaries": "bt2020", "pix_fmt": "yuv420p10le"})
    assert not mediatools.is_hdr({"color_transfer": "bt709", "pix_fmt": "yuv420p"})
    assert mediatools.sdr_chain({}) == []
    chain = mediatools.sdr_chain({"hdr": True})
    assert chain and chain[-1] == "format=yuv420p"
    g = render.build(state([vclip()]), {"mr": dict(RED, hdr=True)}, 360, 640, 25, str(tmp_path))["graph"]
    assert chain[0] in g
    g = render.build(state([vclip()]), {"mr": RED}, 360, 640, 25, str(tmp_path))["graph"]
    assert chain[0] not in g
