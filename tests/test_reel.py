"""Montage court en un clic (engine/agent/reel.py) : les décisions, sans rendu."""
from __future__ import annotations

from engine.agent import reel as R
from engine.agent import scenes as SC
from engine.timeline import model


def words_of(text: str, t0: float = 0.0, step: float = 0.4) -> list[dict]:
    return [{"text": w, "t0": round(t0 + i * step, 3), "t1": round(t0 + i * step + 0.3, 3)}
            for i, w in enumerate(text.split())]


def take(n, text, keep=True, a=0.0, b=1.0, **kw):
    return {"n": n, "text": text, "words": [{"text": w} for w in text.split()], "keep": keep, "in": a, "out": b,
            "why": "", **kw}


def test_claquette_et_rate_en_plus_du_derush():
    takes = [take(1, "Ok top", a=0, b=1), take(2, "Voici mon astuce du jour", a=1, b=4),
             take(3, "Et voilà le résultat", a=4, b=6), take(4, "Non je recommence", a=6, b=7)]
    assert R.extra_drops(takes) == [(1, "slate word before the first take"), (4, "outtake at the end of the rush")]
    # une vraie phrase qui commence par « ok » reste ; la seule prise gardée aussi
    assert R.extra_drops([take(1, "Ok alors voici le secret"), take(2, "Et la suite")]) == []
    assert R.extra_drops([take(1, "Ok"), take(2, "Voici", keep=False)]) == []


def test_plages_ecartees_retirees_des_segments():
    takes = [take(1, "a", a=0, b=2), take(2, "b", keep=False, a=2, b=4), take(3, "c", a=4, b=8, trim_out=7.2)]
    holes = {"m1": R.excluded(takes)}
    assert holes["m1"] == [(2, 4), (7.2, 8)]
    segs = R.subtract([{"media": "m1", "start": 0.5, "end": 7.9, "zoom": 1.1}, {"media": "m2", "start": 0, "end": 3},
                       {"media": "img", "duration": 2}], holes)
    assert segs == [{"media": "m1", "start": 0.5, "end": 2, "zoom": 1.1}, {"media": "m1", "start": 4, "end": 7.2, "zoom": 1.1},
                    {"media": "m2", "start": 0, "end": 3}, {"media": "img", "duration": 2}]
    # un reste trop court ne fait pas un plan
    assert R.subtract([{"media": "m1", "start": 1.9, "end": 4.1}], holes) == []


def test_mot_cle_et_chiffres():
    assert R.keyword_of(words_of("Et donc là tu vas gagner du temps"))["clean"] == "temps"   # pas l'infinitif
    assert R.keyword_of(words_of("Tu vas venir swipe ce que tu aimes"))["clean"] == "swipe"
    assert R.keyword_of(words_of("ça a enregistré automatiquement dans ton livre"), {"livre"})["clean"] == "livre"
    assert R.keyword_of(words_of("J'ai testé 12 applications cette semaine"))["clean"] == "12"
    assert R.keyword_of(words_of("On a ouvert à Marseille hier"))["clean"] == "Marseille"
    assert R.keyword_of(words_of("et donc euh voilà")) is None
    w, value, prefix, suffix, after = R.number_of(words_of("plus de 90% des gens abandonnent"))
    assert (w["text"], value, suffix) == ("90%", 90.0, " %") and after == 3
    assert R.number_of(words_of("ça coûte 40 euros par mois"))[1:4] == (40.0, "", " €")
    assert R.number_of(words_of("à peine 12 pour cent y arrivent"))[3:] == (" %", 5)
    assert R.number_of(words_of("un seul bouton : 1 bouton")) is None


def test_unites_de_scene():
    sents = [{"t0": 0.0, "t1": 0.6, "text": "Stop.", "media": "m1", "sentence": 0},
             {"t0": 0.8, "t1": 3.0, "text": "Tu perds ton temps.", "media": "m1", "sentence": 1},
             {"t0": 3.2, "t1": 6.0, "text": "Voici la solution.", "media": "m1", "sentence": 2}]
    words = words_of("Stop.", 0.0) + words_of("Tu perds ton temps.", 0.8) + words_of("Voici la solution.", 3.2)
    units = R.units_of(sents, words)
    assert [u["text"] for u in units] == ["Stop. Tu perds ton temps.", "Voici la solution."]
    assert units[0]["sentences"] == [("m1", 0), ("m1", 1)] and len(units[0]["words"]) == 5


def _units(*texts, media="m1", gap=0.4):
    out, t = [], 0.0
    for i, text in enumerate(texts):
        ws = words_of(text, t)
        out.append({"t0": t, "t1": ws[-1]["t1"], "text": text, "words": ws, "media": media,
                    "sentences": [(media, i)], "kw": R.keyword_of(ws), "llm": "", "topic": set(),
                    "question": ws if R.is_question(text, len(ws)) else None})
        t = ws[-1]["t1"] + gap
    return out


TEXTS = ("Tinder pour tes repas, voilà mon idée", "Plus de 90% des gens ne savent pas quoi manger",
         "Du coup j'ai codé une application toute simple", "Tu swipes et elle apprend tes goûts",
         "Mais pourquoi personne ne l'avait fait avant ?", "Commente CROQUE et je t'envoie le lien")


def test_plan_des_scenes_vertical():
    units = _units(*TEXTS)
    o = {**model.AUTO_DEFAULTS, "cta": "croque", "accent": "#D40F30"}
    scenes = R.plan_scenes(units, 1080, 1920, o, set())
    layouts = [s["layout"] for s in scenes]
    assert layouts[0] == "split" and scenes[0]["start"] == 0 and scenes[0]["enter"] == "none"
    big = next(it for it in scenes[1]["items"] if it["type"] == "big")
    assert big["value"] == 90 and big["suffix"] == " %" and big["at"] == units[1]["words"][2]["t0"]
    q = scenes[4]
    assert q["layout"] == "full" and q["look"] == "paper" and q["items"][-1]["text"] == "?"
    cta = scenes[5]
    assert cta["layout"] == "face" and "CROQUE" in cta["cta"]["keyword"] and cta["hold"]
    # l'image change à chaque idée : visage et écran partagé alternent
    assert layouts == ["split", "split", "face", "split", "full", "face"]
    assert scenes[2]["items"][0]["type"] == "text" and scenes[2]["items"][0]["y"] >= 1000
    # chaque scène commence sur un mot et chaque élément apparaît sur un mot de sa scène
    starts = {w["t0"] for u in units for w in u["words"]}
    for s in scenes[1:]:
        assert s["start"] in starts
    # le plan passe le contrôle des scènes (sur les mots de la timeline)
    words = [w for u in units for w in u["words"]]
    clock = SC.Clock(words, 30, units[-1]["t1"] + 0.5)
    body = {"scenes": scenes, "look": "clean", "brand": R.brand_of("#D40F30", "clean")}
    pl = SC.plan(body, clock, 1080, 1920)
    assert not pl["problems"]
    pages = SC.build_pages(pl, clock, 1080, 1920, body)
    assert [p["has_page"] for p in pages][:2] == [True, True]


def test_ecran_filme_et_format_paysage():
    units = _units(*TEXTS[:2], "Et voilà le résultat")
    screen = R.plan_scenes(units, 1080, 1920, dict(model.AUTO_DEFAULTS), {"m1"})
    # les phrases de l'écran filmé se regroupent : une scène, des bandeaux qui se relaient
    assert len(screen) == 1 and screen[0]["layout"] == "face" and screen[0]["hold"]
    assert screen[0]["captions"]["box"] and screen[0]["captions"]["y"] == 0.87
    items = screen[0]["items"]
    assert [it["type"] for it in items] == ["pill", "glass", "pill"] and items[0]["y"] == 110
    assert items[0]["out_at"] == items[1]["at"] and items[1]["out_at"] == items[2]["at"] and "out_at" not in items[2]
    long = R.plan_scenes(_units(*TEXTS[:3], gap=4.0), 1080, 1920, dict(model.AUTO_DEFAULTS), {"m1"})
    assert len(long) == 3                    # au-delà de SCREEN_SPAN, une nouvelle scène
    wide = R.plan_scenes(units, 1920, 1080, dict(model.AUTO_DEFAULTS), set())
    assert "split" not in {s["layout"] for s in wide}
    clock = SC.Clock([w for u in units for w in u["words"]], 30, units[-1]["t1"] + 0.5)
    assert not SC.plan({"scenes": wide}, clock, 1920, 1080)["problems"]


def test_couleurs_tirees_de_l_accent():
    red = R.brand_of("#D40F30", "clean")
    assert "accent_ink" not in red and red["soft"].startswith("#")
    yellow = R.brand_of("#FFD23F", "clean")
    assert SC.contrast(yellow["accent_ink"], "#F3F1EE") >= 4.5
    assert R._mix("#FFFFFF", "#000000", 0.5) == "#808080"


def test_corrections_de_mise_en_page():
    body = {"scenes": [
        {"layout": "face", "items": [{"type": "text", "text": "WOW", "y": 1150}]},
        {"layout": "split", "items": [{"type": "big", "text": "90", "size": 170}, {"type": "stamp", "text": "VU"}]},
        {"layout": "split", "items": [{"type": "card", "title": "Une application vraiment toute simple", "w": 960}]}]}
    checks = ["scene 1 (face): a text sits on the speaker's face — move it lower (y) or to the side.",
              "scene 2 (split): texts overlap (« VU ») — give each its own place.",
              "scene 3 (split): text « Une » goes outside the frame — move it or make it smaller."]
    assert R._fix_layout(body, checks)
    s1, s2, s3 = body["scenes"]
    assert s1["items"] == [] and s1["hold"]
    assert [it["type"] for it in s2["items"]] == ["big"]
    assert len(s3["items"][0]["title"]) <= 24
    assert not R._fix_layout(body, ["scene 9 (face): whatever"])
    held = {"scenes": [{"layout": "split"}, {"layout": "face"}]}
    assert R._hold_problems(held, ["Nothing happens on screen for 4.0 s from 1.00 s (scene 2): add…"])
    assert held["scenes"][1]["hold"] and not held["scenes"][0].get("hold")


def test_options_du_montage_automatique():
    a = model.normalize_auto({"rhythm": "dynamic", "style": "pilule", "accent": "#d40f30", "cta": "  « croque » ",
                              "look": "paper"})
    assert (a["rhythm"], a["style"], a["accent"], a["look"]) == ("dynamic", "pilule", "#D40F30", "paper")
    assert a["cta"] == "« croque »"
    d = model.normalize_auto({"style": "inconnu", "accent": "rouge", "look": "néon", "rhythm": "turbo"})
    assert (d["style"], d["accent"], d["look"], d["rhythm"]) == ("net", "#D40F30", "clean", "dynamic")


def test_questions_et_problemes_poses():
    assert R.is_question("Mais pourquoi personne ne l'avait fait avant ?", 8)
    assert R.is_question("Mais avec tout ça, je ne sais toujours pas quoi manger ce soir.", 12)
    assert not R.is_question("J'ai la solution avec je mange quoi ce soir.", 9)
    assert not R.is_question("Ah ?", 1)
    ws = words_of("Mais avec tout ça, je ne sais toujours pas quoi manger ce soir.")
    assert R.headline_of(ws) == "quoi manger <em>ce soir</em>"
    assert R.headline_of(words_of("Tu veux gagner du temps ?")) == "Tu veux gagner <em>du temps</em>"


def test_unites_une_question_garde_sa_scene():
    def sent(i, t0, t1, text, media="m1"):
        return {"t0": t0, "t1": t1, "text": text, "media": media, "sentence": i}
    sents = [sent(0, 0.0, 3.0, "Hier soir j'ai rêvé de Tinder.", "a"), sent(1, 3.2, 3.9, "Je l'ai fait.", "a"),
             sent(2, 4.2, 7.0, "Tu swipes ce que tu aimes."), sent(3, 7.2, 7.9, "Ultime vraiment."),
             sent(4, 8.1, 11.0, "Je ne sais toujours pas quoi manger ce soir.")]
    words = [w for s in sents for w in words_of(s["text"], s["t0"], (s["t1"] - s["t0"]) / len(s["text"].split()))]
    units = R.units_of(sents, words)
    assert [u["text"] for u in units] == ["Hier soir j'ai rêvé de Tinder. Je l'ai fait.",
                                          "Tu swipes ce que tu aimes. Ultime vraiment.",
                                          "Je ne sais toujours pas quoi manger ce soir."]
    assert units[2]["question"] and not units[0]["question"]


def test_orthographe_des_noms():
    ws = words_of("Au fur et à mesure Croc apprend tes goûts, le code est sur Github.")
    lex = R.lexicon_of(ws, "CROQUE")
    assert lex == {"Croc": "Croque", "Github": "GitHub"}
    assert R.spell("Croc apprend, Croc-Monsieur", lex) == "Croque apprend, Croc-Monsieur"
    assert R.lexicon_of(words_of("Je croque une pomme"), "croque") == {}
    assert R.topics([{"words": words_of("ton livre de recettes")}, {"words": words_of("les recettes du livre")},
                     {"words": words_of("rien à voir")}]) == {"livre", "recette"}
