"""Montage court en un clic : toute la méthode, sans agent.

Le bouton « Monter la vidéo » du studio (et `/api/agent/{pid}/reel`) fait ce
qu'un monteur ferait avec les outils des agents, dans l'ordre :

  1. dérush : les prises de chaque rush, retranscrites une par une, avec les
     décisions proposées (reprises, faux départs, fins redites) ; en plus, le
     « ok / top / action » tout seul au début d'un rush et le raté à la fin
     (« je recommence », « c'est nul »…) s'en vont ;
  2. histoire : l'analyse habituelle (accroche, passages creux, moments forts,
     modèle de langage local s'il est là) choisit les phrases gardées ;
  3. coupe : prises et phrases gardées, blancs et tics retirés, rythme
     dynamique cadré sur le visage, voix mesurée et traitée ;
  4. sous-titres : le style choisi, un mot-clé par phrase (le chiffre, le nom,
     le mot fort), qui grossit à l'écran ;
  5. scènes : un plan déduit de ce qui est dit — écran partagé pour
     l'accroche, chiffre qui défile quand un chiffre est dit, page « ? » sur
     une question, mot fort sur le visage ou en carte, bandeau sur un écran
     filmé, carte d'appel à l'action avec le mot à commenter — contrôlé comme
     celui d'un agent (rythme, mise en page) puis dessiné (scenes.py).

Tout reste retouchable dans le studio ; le rapport dit chaque décision.
"""
from __future__ import annotations

import re
import time

from engine.agent import edit as E
from engine.agent import scene_html as SH
from engine.agent import scenes as SC
from engine.agent import service as S
from engine.pipeline import style_presets
from engine.timeline import ai, autoedit, model

DEFAULTS = dict(model.AUTO_DEFAULTS)
# un rush qui commence par un mot de claquette, ou finit sur un raté (formes de autoedit.norm)
SLATE = ("ok", "okay", "top", "action", "go", "vas y", "c est bon", "on y va", "c est parti", "attends", "bon",
         "alors", "voila", "ca tourne", "moteur")
OUTTAKE = ("je recommence", "on recommence", "je reprends", "on la refait", "je la refais", "c est nul", "putain",
           "merde", "coupez", "rate", "zut", "j ai rate", "non non")
_NUM = re.compile(r"^\+?\d[\d,.]*(%|€|\$|k|K)?$")
SCREEN_FACES = 0.25          # moins d'images avec un visage que ça : c'est un écran filmé
MIN_UNIT = 1.2               # s : une phrase plus courte rejoint la suivante dans la même scène
SCREEN_SPAN = 9.0            # s : une scène d'écran filmé regroupe les phrases jusqu'à cette durée
SCREEN_CAPTIONS = {"y": 0.87, "box": True, "outline_col": "#0A0A0A", "box_alpha": 0.25, "outline": 12}
CTA = ("commente", "commentez", "commentaire", "commentaires", "abonne", "abonnez", "abonnes", "le lien", "en description",
       "dans la description", "en bio", "subscribe", "follow", "link in", "comment below")


def _norm(s: str) -> str:
    return autoedit.norm(str(s))


def _has(text: str, phrases) -> bool:
    padded = f" {_norm(text)} "
    return any(f" {p} " in padded for p in phrases)


# --------------------------------------------------------------- dérush

def extra_drops(takes: list[dict]) -> list[tuple[int, str]]:
    """Prises à écarter en plus des décisions du dérush : la claquette seule au
    début du rush, le raté à la fin. Jamais la seule prise gardée."""
    out = []
    kept = [t for t in takes if t["keep"]]
    if len(kept) < 2:
        return out
    first, last = kept[0], kept[-1]
    txt = _norm(" ".join(w["text"] for w in first["words"]))
    if len(first["words"]) <= 3 and txt and _only_slate(txt):
        out.append((first["n"], "slate word before the first take"))
    if len(last["words"]) <= 8 and _has(" ".join(w["text"] for w in last["words"]), OUTTAKE):
        out.append((last["n"], "outtake at the end of the rush"))
    return out


def _only_slate(txt: str) -> bool:
    """Le texte n'est fait que de mots de claquette (« ok top », « bon alors »)."""
    rest = f" {txt} "
    for s in sorted(SLATE, key=len, reverse=True):
        rest = rest.replace(f" {s} ", " ")
    return not rest.strip()


def excluded(takes: list[dict]) -> list[tuple[float, float]]:
    """Plages source à ne pas monter : prises écartées, fins redites."""
    out = []
    for t in takes:
        if not t["keep"]:
            out.append((t["in"], t["out"]))
        elif t.get("trim_out"):
            out.append((t["trim_out"], t["out"]))
    return out


def subtract(segs: list[dict], holes: dict[str, list[tuple[float, float]]], min_len: float = 0.3) -> list[dict]:
    """Segments {media, start, end} moins les plages écartées de leur média."""
    out = []
    for s in segs:
        if "start" not in s:
            out.append(s)
            continue
        parts = [(s["start"], s["end"])]
        for a, b in sorted(holes.get(s["media"], [])):
            nxt = []
            for x, y in parts:
                if b <= x or a >= y:
                    nxt.append((x, y))
                    continue
                if a - x >= min_len:
                    nxt.append((x, a))
                if y - b >= min_len:
                    nxt.append((b, y))
            parts = nxt
        out += [{**s, "start": round(x, 3), "end": round(y, 3)} for x, y in parts]
    return out


# ------------------------------------------------------------ mots-clés

# mots pleins qui ne portent pas une phrase (formes de autoedit.norm)
WEAK = {"venir", "viens", "vient", "aller", "faire", "fais", "dire", "mettre", "prendre", "pouvoir", "vouloir",
        "savoir", "voir", "vraiment", "juste", "attendant", "maintenant", "toujours", "souvent", "ici", "petit",
        "petite", "petits", "nouveau", "nouvelle", "permet", "permettre", "souhaite", "souhaites", "quelque",
        "quelques", "beaucoup", "besoin", "rien", "autre", "autres", "avant", "apres", "depuis", "pendant", "parce",
        "est a dire", "en fait", "du coup", "genre", "enfin", "surtout", "plutot", "d accord", "accord", "cette",
        "celui", "celle", "ceux", "leur", "leurs", "notre", "votre", "vos", "nos", "elle", "avez", "avons", "etait",
        "sont", "fait", "faut", "peut", "vais", "suis", "sais", "donc", "voila", "bref", "entre", "vers", "chez",
        "fur", "mesure", "place", "coup", "fois", "tout", "tous", "toute", "toutes", "ceci", "cela"}
# après ces mots, le mot suivant est un verbe (« tu aimes », « se construire », « n'aimes »)
_BEFORE_VERB = {"je", "j", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles", "ca", "me", "te", "se", "ne", "n",
                "m", "t", "s", "y", "lui", "va", "vas", "vais", "peux", "peut", "veux", "veut", "dois", "doit", "faut",
                "vient", "viens", "venir", "pouvez", "voulez", "allez"}
_VERB_ELISION = re.compile(r"^(?:n|j|m|t|s)['’]", re.I)
# noms de marque que la transcription écrit mal (clé : autoedit.norm)
BRANDS = {"github": "GitHub", "youtube": "YouTube", "tiktok": "TikTok", "linkedin": "LinkedIn", "chatgpt": "ChatGPT",
          "iphone": "iPhone", "ipad": "iPad", "macbook": "MacBook", "whatsapp": "WhatsApp", "openai": "OpenAI",
          "instagram": "Instagram", "netflix": "Netflix", "spotify": "Spotify", "airbnb": "Airbnb"}


def _clean(text: str) -> str:
    return autoedit._ELISION.sub("", str(text).strip(".,;:!?…«»\"'()"))


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 4 and t[-1] in "sx" else t


def topics(units: list[dict]) -> set[str]:
    """Mots (racines) dits dans au moins deux unités : le sujet de la vidéo."""
    seen: dict[str, int] = {}
    for u in units:
        for t in {_stem(_norm(_clean(w["text"]))) for w in u["words"]}:
            if len(t) >= 4 and t not in autoedit._STOP and t not in WEAK:
                seen[t] = seen.get(t, 0) + 1
    return {t for t, n in seen.items() if n >= 2}


def keyword_of(words: list[dict], topic: set[str] | None = None, least: float = 0.0) -> dict | None:
    """Le mot qui porte la phrase : un chiffre, sinon un mot fort, un nom
    propre, un mot du sujet de la vidéo, sinon le plus long des mots pleins —
    pas un adverbe en -ment ni un verbe. Le mot (avec son instant et `clean`,
    sans ponctuation ni élision), ou None (rien ne dépasse `least`)."""
    best, score = None, least
    for n, w in enumerate(words):
        raw = _clean(w["text"])
        t = _norm(raw)
        if not t or t in autoedit._STOP or t in WEAK or (len(t) < 4 and not t.isdigit()):
            continue
        pts = 1.0 + min(1.5, len(t) / 6)
        if re.search(r"\d", raw):
            pts += 3.0
        if t in autoedit._STRONG:
            pts += 2.0
        proper = raw[:1].isupper() and n > 0
        if proper:
            pts += 1.2                       # un nom propre au milieu d'une phrase
        before = _norm(_clean(words[n - 1]["text"]) if n else "")
        if not proper and (before in _BEFORE_VERB or _VERB_ELISION.match(w["text"].strip("«»\"'( "))):
            pts -= 1.0                       # un verbe conjugué : l'idée est dans le nom
        elif topic and _stem(t) in topic:
            pts += 1.0
        if t.endswith("ment") and len(t) > 6:
            pts -= 1.5
        elif re.search(r"(er|ir)$", t) and len(t) > 4 and not proper:
            pts -= 0.7                       # un infinitif : le nom d'à côté porte mieux l'idée
        if pts > score:
            best, score = {**w, "clean": raw}, pts
    return best


def _phonetic(t: str) -> str:
    """Clé sonore grossière (français) : « Croc » et « Croque » se rejoignent."""
    t = _norm(t).replace(" ", "")
    for a, b in (("qu", "k"), ("ck", "k"), ("ph", "f"), ("c", "k"), ("ou", "u")):
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"(es|e|s|t|d|x)$", "", t) or t


def lexicon_of(words: list[dict], cta: str = "") -> dict[str, str]:
    """Orthographe des noms : les marques connues, et le mot de l'appel à
    l'action (souvent le nom du produit) là où la transcription l'a écrit
    autrement mais qu'il sonne pareil (« Croc » → « Croque »)."""
    lex: dict[str, str] = {}
    key = _phonetic(cta) if cta and len(_norm(cta)) >= 3 else ""
    name = cta[:1].upper() + cta[1:].lower() if cta else ""
    for w in words:
        raw = _clean(w["text"])
        t = _norm(raw)
        if t in BRANDS and raw != BRANDS[t]:
            lex[raw] = BRANDS[t]
        elif key and raw[:1].isupper() and " " not in t and t != _norm(cta) and _phonetic(t) == key:
            lex[raw] = name
    return lex


def spell(text: str, lex: dict[str, str]) -> str:
    """Le texte avec l'orthographe du lexique (mot entier)."""
    for k, v in lex.items():
        text = re.sub(rf"(?<![\w-]){re.escape(k)}(?![\w-])", lambda _m, v=v: v, text)
    return text


def number_of(words: list[dict]) -> tuple[dict, float, str, str, int] | None:
    """Premier chiffre qui vaut d'être montré : (mot, valeur, préfixe, suffixe,
    index du mot suivant le chiffre et son unité)."""
    for n, w in enumerate(words):
        raw = w["text"].strip(".,;:!?…«»\"'()").replace(" ", "")
        if not _NUM.match(raw):
            continue
        nxt = _norm(words[n + 1]["text"]) if n + 1 < len(words) else ""
        suffix, after = "", n + 1
        if raw.endswith("%") or nxt in ("%", "pourcent") or (nxt == "pour" and n + 2 < len(words)
                                                             and _norm(words[n + 2]["text"]) == "cent"):
            suffix, after = " %", n + 1 + (0 if raw.endswith("%") else 2 if nxt == "pour" else 1)
        elif raw.endswith("€") or nxt in ("euros", "euro", "€"):
            suffix, after = " €", n + 1 + (0 if raw.endswith("€") else 1)
        try:
            value = float(re.sub(r"[^\d.,]", "", raw).replace(",", "."))
        except ValueError:
            continue
        if value < 2 and not suffix:
            continue                         # « 1 bouton » : rien à compter
        return w, value, "+" if raw.startswith("+") else "", suffix, after
    return None


# --------------------------------------------------------------- scènes

_ASKS = re.compile(r"^(pourquoi|comment|combien|est ce que|qu est ce|quel|quelle|quels|quelles)\b|"
                   r"\b(sais|savais|sait|savez|savons) (toujours |jamais |plus |vraiment |)pas\b")
_ASK_WORDS = ("pourquoi", "comment", "combien", "quoi", "quel", "quelle", "quels", "quelles", "qui", "quand")
_LEAD = {"pas", "ne", "mais", "et", "donc", "alors", "je", "tu", "on", "vous", "que", "si"}


_CLAUSE = {"mais", "et", "donc", "alors", "sauf", "pourtant", "bref", "enfin", "maintenant", "du", "or"}


def clause_start(words: list[dict]) -> int:
    """Index du début de la dernière proposition : la transcription colle
    parfois deux phrases (« …kiffer Mais avec tout ça, je ne sais pas… »)."""
    start = 0
    for i in range(1, len(words)):
        txt = words[i]["text"].strip("«»\"'( ")
        if words[i - 1]["text"].rstrip()[-1:] in ".!?…" or (txt[:1].isupper() and _norm(txt) in _CLAUSE):
            start = i
    return start


def is_question(text: str, n_words: int) -> bool:
    """Une vraie question (« … ? ») ou un problème posé (« je ne sais toujours
    pas quoi manger »), assez longue pour une page."""
    return n_words >= 3 and (text.rstrip().endswith("?") or bool(_ASKS.search(_norm(text))))


def headline_of(words: list[dict]) -> str:
    """Le titre d'une page question : à partir du mot interrogatif (« quoi
    manger ce soir »), sinon la fin de la phrase ; les deux derniers mots en
    accent (<em>)."""
    toks = [w["text"].strip(".,;:!?…«»\"()") for w in words]
    toks = [t for t in toks if t]
    start = next((i for i, t in enumerate(toks) if i > 0 and _norm(t) in _ASK_WORDS), None)
    if start is not None:
        part = toks[start:start + 7]
    elif len(toks) <= 7:
        part = toks
    else:                                    # la fin de la phrase, sans les petits mots de tête
        part = toks[-5:]
        while len(part) > 2 and _norm(part[0]) in _LEAD:
            part = part[1:]
    if len(part) >= 3:
        return " ".join(part[:-2]) + f" <em>{' '.join(part[-2:])}</em>"
    return " ".join(part)


def _upper(s: str, limit: int = 18) -> str:
    s = re.sub(r"\s+", " ", str(s).strip(".,;:!?…«»\"'()")).upper()
    return s if len(s) <= limit else (s[:limit].rsplit(" ", 1)[0] or s[:limit])


def _big_size(text: str, cap: int = 170) -> int:
    """Taille d'un mot géant qui tient dans les 1080 px de large."""
    return int(min(cap, 1000 / (0.66 * max(1, len(text)))))


def _mix(a: str, b: str, k: float) -> str:
    """Couleur `a` à k, `b` au reste (#RRGGBB)."""
    ra, rb = SC._rgb(a) or (0, 0, 0), SC._rgb(b) or (0, 0, 0)
    return "#" + "".join(f"{round(x * k + y * (1 - k)):02X}" for x, y in zip(ra, rb))


def brand_of(accent: str, look: str) -> dict:
    """Couleurs des scènes tirées de l'accent choisi : fonds doux mêlés au
    papier, et une encre d'accent assez sombre pour se lire sur le fond clair."""
    base = SH.DEFAULT_BRAND.get(look, SH.DEFAULT_BRAND["clean"])
    ink = accent
    for _ in range(12):
        r = SC.contrast(ink, base["bg"])
        if r is None or r >= 4.5:
            break
        ink = _mix(ink, "#000000", 0.82)
    brand = {"accent": accent, "soft": _mix(accent, base["bg"], 0.14), "soft2": _mix(accent, base["bg"], 0.32)}
    if ink != accent:
        brand["accent_ink"] = ink
    return brand


def _merge(u: dict, v: dict) -> None:
    u.update(t1=v["t1"], text=u["text"] + " " + v["text"], words=u["words"] + v["words"])
    u["sentences"] += v["sentences"]
    u["question"] = u.get("question") or v.get("question")


def units_of(sentences: list[dict], words: list[dict], min_len: float = MIN_UNIT) -> list[dict]:
    """Phrases de la timeline (edit.timeline_sentences) avec leurs mots,
    regroupées en unités de scène : une phrase trop courte rejoint la suivante
    (ou, devant une question ou un autre rush, la précédente). Une question
    garde sa scène à elle (`question` : ses mots)."""
    units: list[dict] = []
    for s in sentences:
        ws = [w for w in words if s["t0"] - 0.02 <= w["t0"] < s["t1"] - 0.005]
        if ws:
            qws = ws[clause_start(ws):]
            q = is_question(" ".join(w["text"] for w in qws), len(qws)) and qws[-1]["t1"] - qws[0]["t0"] >= 0.9
            units.append({"t0": s["t0"], "t1": s["t1"], "text": s["text"], "words": ws, "media": s["media"],
                          "sentences": [(s["media"], s["sentence"])], "question": qws if q else None})

    def short(u: dict) -> bool:
        return u["t1"] - u["t0"] < min_len and not u["question"]

    ahead: list[dict] = []
    for u in units:
        prev = ahead[-1] if ahead else None
        if prev and short(prev) and prev["media"] == u["media"] and not u["question"]:
            _merge(prev, u)
        else:
            ahead.append(u)
    out: list[dict] = []
    for u in ahead:
        prev = out[-1] if out else None
        if prev and short(u) and prev["media"] == u["media"] and not prev["question"]:
            _merge(prev, u)
        else:
            out.append(u)
    return out


def plan_scenes(units: list[dict], W: int, H: int, o: dict, screens: set[str]) -> list[dict]:
    """Plan de scènes (format de build_scenes, temps de timeline) déduit de ce
    qui est dit. `units` : units_of(), chacune avec `kw` (keyword_of), `llm`
    (texte court proposé par le modèle de langage, ou "") et `topic`."""
    tall = H / W >= 1.7                      # l'écran partagé est fait pour le vertical
    cta_word = str(o.get("cta") or "").strip().strip("«»\"' ")
    cta_i = None
    if cta_word and units:
        # la phrase qui appelle à l'action, dans la fin de la vidéo ; sinon la dernière
        tail = max(1, int(len(units) * 0.6))
        cta_i = next((i for i in range(tail, len(units)) if _has(units[i]["text"], CTA)), len(units) - 1)
    ink = o.get("accent_ink") or o.get("accent") or "#D40F30"
    scenes: list[dict] = []
    prev = None
    for i, u in enumerate(units):
        words = u["words"]
        first = words[0]["t0"]
        kw = u.get("kw")
        at_kw = (kw or words[0])["t0"]
        label = (u.get("llm") or (kw or {}).get("clean") or "").strip()
        num = number_of(words)
        screen = u["media"] in screens
        s: dict = {"start": 0 if i == 0 else round(first, 3)}
        if i == cta_i:
            s.update(layout="face", hold="call to action",
                     cta={"kicker": "COMMENTE", "keyword": f"« <em>{cta_word.upper()}</em> »",
                          "at": round(min(first + 0.3, max(first, u["t1"] - 0.3)), 3)})
            if screen:
                s["captions"] = dict(SCREEN_CAPTIONS)
        elif i > 0 and u.get("question"):
            q = u["question"]
            asked = " ".join(w["text"] for w in q).rstrip().endswith("?")
            s["start"] = round(q[0]["t0"], 3)  # la page arrive avec la question, pas avant
            s.update(layout="full", look="paper", items=[
                {"type": "kicker", "left": "LA QUESTION" if asked else "LE PROBLÈME", "right": "",
                 "y": 120 if tall else 60, "at": q[0]["t0"]},
                {"type": "headline", "text": headline_of(q), "y": 250 if tall else 130, "size": 120 if tall else 90,
                 "at": q[0]["t0"]},
                {"type": "big", "text": "?", "y": 640 if tall else 380, "size": 560 if tall else 360, "color": ink,
                 "at": q[-1]["t0"]}])
        elif screen:
            # un écran filmé se montre lui-même : un bandeau en haut, les sous-titres en pastille en bas
            s.update(layout="face", hold="the screen recording plays", captions=dict(SCREEN_CAPTIONS))
            if num:
                w, value, prefix, suffix, after = num
                rest = " ".join(x["text"].strip(".,;:!?…") for x in words[after:after + 4])
                s["items"] = [{"type": "glass", "dark": True, "value": value, "prefix": prefix, "suffix": suffix,
                               "count": 0.5, "sub": rest[:28], "x": 60, "y": 40, "w": 470, "at": w["t0"]}]
            elif label:
                s["items"] = [{"type": "pill", "text": label[:28][:1].upper() + label[1:28], "x": 60, "y": 110,
                               "at": at_kw, "arrow": "↓"}]
        elif tall and (i == 0 or num or prev != "split"):
            items = []
            if num:
                w, value, prefix, suffix, after = num
                rest = " ".join(x["text"] for x in words[after:after + 3])
                items.append({"type": "big", "value": value, "prefix": prefix, "suffix": suffix,
                              "decimals": 0 if value == int(value) else 1, "label": _upper(rest, 28),
                              "y": 230, "size": 180, "count": 0.6, "at": w["t0"]})
                shown = {**w, "clean": _clean(w["text"])}
            else:
                big = _upper(kw["clean"], 14) if kw else ""
                if label and _norm(label) != _norm(big):
                    items.append({"type": "line", "text": _upper(label, 30), "x": "center", "y": 150, "size": 40,
                                  "heavy": True, "tracking": ".16em", "at": first})
                if big:
                    items.append({"type": "big", "text": big, "y": 260, "size": _big_size(big), "at": kw["t0"]})
                shown = kw
            # la phrase dure : un deuxième temps sur le mot fort suivant, tamponné
            later = [w for w in words if w["t0"] > (shown or words[0])["t0"] + 1.2]
            k2 = keyword_of(later, u.get("topic"), least=2.6) if later else None   # seulement un mot qui compte
            if k2 and (not shown or _norm(k2["clean"]) != _norm(shown["clean"])):
                items.append({"type": "stamp", "text": _upper(k2["clean"], 16), "x": 250, "y": 560, "at": k2["t0"]})
            if not items:
                items.append({"type": "line", "text": _upper(u["text"], 28), "x": "center", "y": 360, "size": 44,
                              "heavy": True, "at": first})
            s.update(layout="split", items=items)
            if i == 0:
                s["enter"] = "none"          # la zone est là dès la première image,
                if items[0]["type"] == "line":
                    items[0]["at"] = 0.0     # et sa ligne d'accroche aussi (la miniature)
        else:
            s.update(layout="face", hold="the speaker carries it")
            if label:
                s["items"] = [{"type": "text", "text": _upper(label, 22), "y": 1150 if tall else round(H * 0.6),
                               "size": 110 if tall else 90, "at": at_kw}]
        s["_t1"], s["_screen"] = u["t1"], screen and s["layout"] == "face" and i != cta_i
        scenes.append(s)
        prev = s["layout"]
    return group_screens(scenes)


def group_screens(scenes: list[dict], span: float = SCREEN_SPAN) -> list[dict]:
    """Les phrases d'un écran filmé qui se suivent font une seule scène (jusqu'à
    `span` s) : ses bandeaux se relaient (chacun part quand le suivant arrive)
    et un chiffre dit en fin de phrase a le temps de défiler."""
    out: list[dict] = []
    for s in scenes:
        g = out[-1] if out else None
        if g and g.get("_screen") and s.get("_screen") and s["_t1"] - g["start"] <= span:
            g["items"] = (g.get("items") or []) + (s.get("items") or [])
            g["_t1"] = s["_t1"]
            continue
        out.append(s)
    for s in out:
        items = sorted(s.get("items") or [], key=lambda it: it["at"])
        for a, b in zip(items, items[1:]):
            a.setdefault("out_at", b["at"])
        if items:
            s["items"] = items
        s.pop("_t1", None)
        s.pop("_screen", None)
    return out


def _hold_problems(body: dict, problems: list[str]) -> bool:
    """Un creux signalé par le contrôle du rythme : la scène tient le plan
    (la voix porte). Vrai si quelque chose a changé."""
    changed = False
    for p in problems:
        m = re.search(r"\(scene (\d+)\)", p)
        if m and 0 < int(m.group(1)) <= len(body["scenes"]):
            sc = body["scenes"][int(m.group(1)) - 1]
            if not sc.get("hold"):
                sc["hold"] = "short still moment, the voice carries it"
                changed = True
    return changed


def _fix_layout(body: dict, checks: list[str]) -> bool:
    """Ce que le navigateur a vu sur les zones (scenes.page_problems), corrigé
    comme un monteur le ferait : le texte posé sur le visage ou qui en
    chevauche un autre s'en va, celui qui déborde rapetisse, celui qu'on lit
    mal reprend l'encre. Vrai si le plan a changé."""
    changed = False
    for c in checks:
        m = re.match(r"scene (\d+) \(\w+\): (.*)", c)
        if not m or not 0 < int(m.group(1)) <= len(body["scenes"]):
            continue
        sc, msg = body["scenes"][int(m.group(1)) - 1], m.group(2)
        items = sc.get("items") or []
        if "speaker's face" in msg:
            keep = [it for it in items if it.get("type") != "text"]
        elif "overlap" in msg:
            keep = [it for it in items if it.get("type") != "stamp"]
            keep = keep if len(keep) < len(items) else items[:1]
        elif "outside the frame" in msg:
            keep = []
            for it in items:
                it = dict(it)
                if it.get("size"):
                    it["size"] = round(float(it["size"]) * 0.75)
                if it.get("type") == "card" and len(str(it.get("title") or "")) > 24:
                    it["title"] = str(it["title"])[:24].rsplit(" ", 1)[0]
                keep.append(it)
        elif "hard to read" in msg:
            keep = [{k: v for k, v in it.items() if k != "color"} for it in items]
        else:
            continue
        if keep != items:
            sc["items"] = keep
            sc.setdefault("hold", "the voice carries it")
            changed = True
    return changed


# ------------------------------------------------------------------ run

class _SubJob(dict):
    """Progression d'une étape (0→100) ramenée dans [lo, hi] de la tâche ;
    `say(message)` : le message à montrer (None = garder celui de la tâche)."""

    def __init__(self, job: dict, lo: float, hi: float, say=None) -> None:
        super().__init__(message="", pct=0)
        self.job, self.lo, self.hi, self.say = job, lo, hi, say

    def update(self, *a, **k) -> None:
        super().update(*a, **k)
        self.job.update(pct=int(self.lo + (self.hi - self.lo) * float(self.get("pct") or 0) / 100))
        msg = self.say(str(self.get("message") or "")) if self.say else None
        if msg:
            self.job.update(message=msg)


def _drawing(msg: str) -> str | None:
    m = re.search(r"\((\d+)/(\d+)\)", msg)
    return f"Dessin des scènes ({m.group(1)}/{m.group(2)})…" if m else None


def _screens(proj, media: list[dict]) -> set[str]:
    """Rushs où l'on voit rarement un visage : un écran filmé (capture, appli)."""
    out = set()
    for m in media:
        track = S.face_track_of(proj, m)
        if len(track) < SCREEN_FACES * 2 * max(1.0, float(m.get("duration") or 0)):
            out.add(m["id"])
    return out


def reel(job: dict, proj, body: dict) -> dict:
    """Le montage complet (voir la docstring du module). `body` : les options
    du montage automatique (model.AUTO_DEFAULTS) et `media` (ids, dans l'ordre
    à monter ; par défaut toutes les vidéos avec du son)."""
    t_start = time.time()
    o = {**DEFAULTS, **{k: v for k, v in body.items() if v is not None}}
    o.update(model.normalize_auto(o))

    def say(msg: str, pct: float) -> None:
        job.update(message=msg, pct=int(pct))

    media = [m for m in proj.state["media"] if m["kind"] == "video" and m.get("has_audio")]
    if body.get("media"):
        refs = body["media"] if isinstance(body["media"], list) else [body["media"]]
        media = [S.resolve_media(proj, r, ("video",)) for r in refs]
        media = [m for i, m in enumerate(media) if m.get("has_audio") and m not in media[:i]]
    if not media:
        raise S.AgentError("Ajoute d'abord une vidéo où l'on parle : le montage part de la voix.")
    for m in media:
        S.require_ready(m)
    # 0. la voix en texte (Whisper, sur ce PC)
    say("Transcription de la voix…", 2)
    for m in media:
        if (m.get("transcript") or {}).get("status") != "done":
            ai.queue_transcription(proj, m["id"])
    t0 = time.time()
    while any((proj.media(m["id"]).get("transcript") or {}).get("status") in ("queued", "running") for m in media):
        if time.time() - t0 > 3600:
            raise S.AgentError("La transcription prend trop de temps.")
        time.sleep(0.5)
    media = [proj.media(m["id"]) for m in media]
    for m in media:
        S.require_transcript(m)
    report: dict = {"takes": [], "kept_takes": 0, "dropped_takes": 0}
    holes: dict[str, list] = {}
    # 1. dérush : les prises, chacune retranscrite seule (une seule fois par rush)
    if o["trim"]:
        for n, m in enumerate(media):
            say(f"Dérush de « {m['name']} » : les prises…", 5 + 25 * n / len(media))
            sub = _SubJob(job, 5 + 25 * n / len(media), 5 + 25 * (n + 1) / len(media))
            try:
                if (m.get("transcript") or {}).get("by_takes"):
                    res = S.derush(proj, {"media": m["id"]})
                else:
                    res = S.derush_job(sub, proj, {"media": m["id"]})
            except S.AgentError:
                continue                     # pas de prise mesurable : l'analyse fera sans
            for num, why in extra_drops(res["takes"]):
                t = next(x for x in res["takes"] if x["n"] == num)
                t["keep"], t["why"] = False, why
            holes[m["id"]] = excluded(res["takes"])
            for t in res["takes"]:
                if not t["keep"] or t.get("trim_out"):
                    report["takes"].append({"media": m["name"], "n": t["n"], "text": t["text"][:90],
                                            "why": t.get("why") or "", "dropped": not t["keep"]})
            report["kept_takes"] += sum(1 for t in res["takes"] if t["keep"])
            report["dropped_takes"] += sum(1 for t in res["takes"] if not t["keep"])
    # 2. l'histoire : l'analyse choisit les phrases et l'accroche
    from engine.timeline.api import media_plan
    popts = {"llm": o["llm"], "trim": o["trim"], "cold_open": o["cold_open"],
             "max_duration": float(o["max_duration"] or 0)}
    plans = {}
    for n, m in enumerate(media):
        say(f"Analyse de « {m['name']} »…", 32 + 16 * n / len(media))
        plans[m["id"]] = media_plan(proj, m["id"], popts, lambda t: job.update(message=t))
    W_ = S.Words(proj)
    segments: list[dict] = []
    hook: list[dict] = []
    for m in media:
        p = plans[m["id"]]
        drop = set(p.get("drop_sentences") or [])
        kept = [s["i"] for s in W_.sentences(m["id"]) if s["i"] not in drop]
        hk = p.get("hook") or {}
        if o["hook"] and hk.get("cold_open") and hk.get("sentence") in kept and not hook:
            hook = subtract(S.expand_segment(proj, W_, {"media": m["id"], "sentences": [hk["sentence"]]}), holes)
            if hook:
                kept.remove(hk["sentence"])
        if kept:
            segments += S.expand_segment(proj, W_, {"media": m["id"], "sentences": kept})
    segments = hook + subtract(segments, holes)
    if not segments:
        raise S.AgentError("Il ne reste rien à monter après le dérush (on ne parle pas dans ces vidéos ?).")
    first_plan = plans[media[0]["id"]]
    report.update(hook=((first_plan.get("hook") or {}).get("text") or first_plan.get("title") or "").strip(),
                  cold_open=bool(hook), llm=any(p.get("llm") for p in plans.values()))
    # 3. la coupe, le rythme, la voix
    say("Montage de la coupe…", 50)
    rhythm = o["rhythm"] if o["zoom"] else "none"
    with proj.lock:
        doc = S.doc_of(proj)
        doc["clips"] = [c for c in doc["clips"] if not (c["kind"] == "text" and c.get("ai")) and c.get("tag") != SC.TAG]
        rep = S.assemble_doc(proj, doc, W_, segments,
                             {"remove_silences": o["silence"], "remove_fillers": o["fillers"], "rhythm": rhythm,
                              "voice": "auto" if o["sound"] else "none", "loudness": o["sound"], "captions": False},
                             {mid: p.get("highlights") or [] for mid, p in plans.items()})
        doc["markers"] = [mk for mk in doc.get("markers") or [] if not mk["label"].startswith("★ ")]
        clips = E.voice_clips(doc)
        highlights = []
        for mid, p in plans.items():
            for x in p.get("highlights") or []:
                hit = E.map_source(clips, mid, x["s"] + 0.02)
                if hit:
                    end = E.map_source(clips, mid, x["e"] - 0.05)
                    label = x.get("label") or "Moment fort"
                    doc["markers"].append({"id": model.new_id("k"), "t": E.r4(hit[1]), "label": "★ " + label,
                                           "color": "#F5B000"})
                    highlights.append({"t": E.r4(hit[1]), "e": E.r4(end[1] if end else hit[1] + x["e"] - x["s"]),
                                       "label": label})
        doc["settings"]["auto"] = {k: o[k] for k in model.AUTO_DEFAULTS}
        S.commit(proj, doc, "reel_cut")
    report.update(removed=rep["removed_seconds"], zooms=rep["zooms"], sound=o["sound"],
                  highlights=sorted(highlights, key=lambda x: x["t"]))
    # 4. un mot-clé par unité de sens, 5. les scènes
    doc = S.doc_of(proj)
    units = units_of(E.timeline_sentences(doc, W_.sentences), E.timeline_words(doc, W_.words))
    llm_text = {(mid, x.get("sentence")): (x.get("text") or "").strip() for mid, p in plans.items() if p.get("llm")
                for x in p.get("texts") or []}
    topic = topics(units)
    lex = lexicon_of([w for u in units for w in u["words"]], str(o.get("cta") or "").strip("«»\"' "))
    for u in units:
        u["topic"] = topic
        u["kw"] = keyword_of(u["words"], topic)
        if u["kw"]:
            u["kw"]["clean"] = spell(u["kw"]["clean"], lex)
        u["llm"] = spell(next((llm_text[k] for k in u["sentences"] if llm_text.get(k)), ""), lex)
    if units and report["llm"] and report["hook"] and not units[0]["llm"]:
        units[0]["llm"] = spell(report["hook"], lex)   # l'accroche proposée par le modèle ouvre la vidéo
    keywords = list(dict.fromkeys(u["kw"]["clean"] for u in units if u.get("kw")))
    style = o["style"] if o["style"] in style_presets.PRESETS else "net"
    caps = {"style": style, "words_per_line": 3, "keywords": keywords, "lexicon": lex, "emojis": False}         if o["captions"] else None
    report["lexicon"] = lex
    screens = _screens(proj, media)
    report.update(keywords=keywords, style=style if caps else None,
                  screens=[m["name"] for m in media if m["id"] in screens], scenes=[], checks=[], captions=0)
    done = False
    if o["texts"] and units:
        cv = doc["canvas"]
        brand = brand_of(o["accent"], o["look"])
        o["accent_ink"] = brand.get("accent_ink", o["accent"])
        sbody = {"scenes": plan_scenes(units, cv["w"], cv["h"], o, screens), "look": o["look"],
                 "brand": brand, "locale": "fr-FR"}
        if caps:
            sbody["captions"] = caps
        say("Plan des scènes…", 56)
        try:
            for _ in range(3):
                try:
                    SC.build(proj, {**sbody, "check_only": True})
                    break
                except SC.SceneError as exc:
                    if not exc.problems:
                        raise
                    if not _hold_problems(sbody, exc.problems):
                        sbody["force"] = True        # le reste se corrige à la main dans le studio
                        break
            say("Dessin des scènes…", 60)
            res = SC.build(proj, sbody, _SubJob(job, 60, 92, _drawing))
            if res.get("layout_checks") and _fix_layout(sbody, res["layout_checks"]):
                say("Retouche des scènes…", 93)          # seules les scènes corrigées sont redessinées
                res = SC.build(proj, {**sbody, "force": True}, _SubJob(job, 93, 98, _drawing))
            report.update(scenes=[{"n": s["scene"], "layout": s["layout"], "start": s["start"], "end": s["end"],
                                   "said": s["said"][:70]} for s in res["scenes"]],
                          checks=res.get("layout_checks") or [], captions=res.get("captions_placed", 0),
                          metrics=res.get("metrics") or {})
            done = True
        except SC.SceneError as exc:
            report["scenes_error"] = str(exc) + "".join(f" — {p}" for p in exc.problems[:3])
    if caps and not done:
        say("Sous-titres…", 95)
        report["captions"] = S.captions(proj, caps)["lines"]
    report.update(duration=model.duration(proj.state["clips"]), rev=proj.rev, seconds=round(time.time() - t_start, 1))
    say("Terminé", 100)
    return report
