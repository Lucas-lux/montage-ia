"""Montage automatique : analyse d'un rush parlé et plan de montage.

Entrée : les mots transcrits d'un média (temps source), sa forme d'onde et,
si possible, la position du visage. Sortie : un PLAN en temps source, que
l'éditeur applique lui-même sur la timeline (coupes, zooms, textes, repères) :

    hook        la phrase qui accroche + le texte à afficher au début
    drop        passages à retirer (salutations, formules de fin, reprises,
                phrases faibles quand une durée cible est demandée)
    highlights  les moments forts (plages de phrases)
    texts       mots-clés à afficher en grand, avec leur phrase
    cutpoints   fins de phrases où couper pour donner du rythme (zooms)
    face        où est le visage (0..1), pour zoomer sans couper la tête

Deux planificateurs : l'IA locale (Qwen3, `engine.pipeline.llm`) quand son
modèle est là, sinon des règles simples (mots forts, questions, chiffres,
énergie de la voix). Dans les deux cas le plan est vérifié et borné ici : le
modèle propose, ce module dispose.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata

SENTENCE_GAP = 0.9          # s de blanc qui termine une phrase
HOOK_MAX = 9.0          # s : au-delà, une phrase ne fait plus une ouverture à froid
MAX_DROP_RATIO = 0.4        # jamais plus de 40 % des phrases retirées d'office
RHYTHM_MAX = 7.0            # s : au-delà, on propose une coupe à la fin d'une phrase
LLM_WINDOW = 90             # phrases par appel au modèle

_STRONG = {
    "secret", "secrets", "erreur", "erreurs", "jamais", "toujours", "incroyable", "meilleur", "meilleure",
    "meilleurs", "pire", "pourquoi", "comment", "attention", "astuce", "astuces", "gratuit", "gratuite", "argent",
    "temps", "facile", "simple", "rapide", "vite", "important", "importante", "essentiel", "truc", "verite",
    "personne", "enorme", "fou", "dingue", "impossible", "stop", "arrete", "arretez", "regarde", "regardez",
    "imagine", "imaginez", "resultat", "resultats", "preuve", "top", "hack", "gagner", "gagne", "perdre", "perd",
    "danger", "probleme", "solution", "conseil", "conseils", "regle", "regles", "technique", "methode", "etape",
    "etapes", "cle", "ultime", "vrai", "vraiment", "faux", "gros", "grosse", "mistake", "never", "always", "best",
    "worst", "why", "how", "free", "money", "easy", "fast", "truth", "everyone", "huge", "crazy", "stop", "look",
    "result", "win", "lose", "problem", "tip", "rule", "method", "step", "secret",
}
# Racines (après `norm`) : « abonn » couvre abonne, abonnez, abonner…
_GREET = ("bonjour", "salut", "hello", "coucou", "bienvenue", "c est moi", "on se retrouve", "ca va", "hey", "yo",
          "welcome")
# annonce du sujet : pénalisée dans le score, mais elle porte souvent le sujet
_INTRO = ("aujourd hui je vais", "aujourd hui on va", "dans cette video", "today i", "in this video")
_OUTRO = ("abonn", "like", "commentaire", "a bientot", "ciao", "merci d avoir", "n hesitez pas", "partag",
          "la prochaine", "subscribe", "see you", "thanks for watching", "follow")
_ELISION = re.compile(r"^(?:[cdjlmnst]|qu|jusqu|lorsqu|puisqu)['’]", re.IGNORECASE)
_STOP = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "au", "aux", "en", "y", "on", "il", "elle",
    "ils", "elles", "je", "tu", "nous", "vous", "me", "te", "se", "ce", "ca", "cet", "cette", "ces", "mon", "ma",
    "mes", "ton", "ta", "tes", "son", "sa", "ses", "qui", "que", "quoi", "dont", "pour", "par", "avec", "sans",
    "dans", "sur", "sous", "est", "sont", "etre", "ete", "avoir", "ai", "as", "ont", "fait", "faire", "faut", "va",
    "vais", "vas", "peut", "peux", "plus", "moins", "tres", "trop", "pas", "ne", "non", "oui", "si", "mais", "donc",
    "alors", "puis", "aussi", "comme", "tout", "tous", "toute", "toutes", "bien", "encore", "deja", "ici", "la",
    "meme", "chose", "choses", "euh", "hum", "bah", "ben", "hein", "voila", "genre", "the", "a", "an", "and", "or",
    "of", "to", "in", "on", "is", "are", "be", "it", "this", "that", "i", "you", "we", "they", "my", "your",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------- phrases

def sentences(words: list[dict]) -> list[dict]:
    """Découpe les mots en phrases : ponctuation finale ou blanc de plus de SENTENCE_GAP."""
    out: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        out.append({"i": len(out), "text": " ".join(w["text"] for w in cur),
                    "start": round(float(cur[0]["start"]), 3), "end": round(float(cur[-1]["end"]), 3),
                    "words": list(cur)})
        cur.clear()

    for k, w in enumerate(words):
        if cur and float(w["start"]) - float(cur[-1]["end"]) > SENTENCE_GAP:
            flush()
        cur.append(w)
        text = str(w["text"]).rstrip("»\")")
        nxt = words[k + 1] if k + 1 < len(words) else None
        if text.endswith(("?", "!", ".", "…")) and not text.endswith(("...", "M.", "Mme.")):
            # « 3.5 » ou « etc. » ne finissent pas une phrase si la suite colle
            if nxt is None or float(nxt["start"]) - float(w["end"]) > 0.15 or not re.match(r"^[a-zé]", str(nxt["text"])):
                flush()
    flush()
    return out


def _tokens(text: str) -> list[str]:
    return [t for t in norm(text).split() if t]


def energy_of(wave: bytes | None, rate: int, s: float, e: float) -> float:
    """Niveau moyen (0..1) de la voix entre s et e, d'après la forme d'onde."""
    if not wave or rate <= 0:
        return 0.0
    a, b = max(0, int(s * rate)), min(len(wave), int(math.ceil(e * rate)))
    if b <= a:
        return 0.0
    chunk = wave[a:b]
    return sum(chunk) / (255.0 * len(chunk))


def score_sentences(sents: list[dict], wave: bytes | None, rate: int) -> None:
    """Score « accroche / moment fort » de chaque phrase, écrit dans place."""
    if not sents:
        return
    energies = [energy_of(wave, rate, s["start"], s["end"]) for s in sents]
    rates = [len(s["words"]) / max(0.3, s["end"] - s["start"]) for s in sents]

    def z(values: list[float], i: int) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        m = sum(values) / n
        sd = math.sqrt(sum((v - m) ** 2 for v in values) / n) or 1e-6
        return (values[i] - m) / sd

    for i, s in enumerate(sents):
        toks = [norm(_ELISION.sub("", w["text"])) for w in s["words"]]
        toks = [t for t in toks if t]
        n = len(toks)
        pts = 0.0
        strong = sum(1 for t in toks if t in _STRONG)
        pts += min(3.0, strong * 1.2)
        if "?" in s["text"]:
            pts += 1.5
        if "!" in s["text"]:
            pts += 0.6
        if re.search(r"\d", s["text"]):
            pts += 1.2
        if toks and toks[0] in ("regarde", "regardez", "imagine", "imaginez", "attention", "arrete", "arretez",
                                "ecoute", "ecoutez", "stop", "look", "listen"):
            pts += 1.5
        if 4 <= n <= 14:
            pts += 0.6                       # une phrase courte accroche, une tirade non
        elif n > 25:
            pts -= 0.8
        pts += 0.7 * max(-1.5, min(1.5, z(energies, i)))
        pts += 0.3 * max(-1.0, min(1.0, z(rates, i)))
        if _has(s["text"], _GREET) or _has(s["text"], _OUTRO) or _has(s["text"], _INTRO):
            pts -= 3.0
        s["score"] = round(pts, 2)
        s["energy"] = round(energies[i], 3)


def _hits(text: str, roots: tuple) -> int:
    n = norm(text)
    return sum(1 for r in roots if re.search(r"\b" + re.escape(r), n))


def _has(text: str, roots: tuple) -> bool:
    return _hits(text, roots) > 0


def detect_fluff(sents: list[dict]) -> set[int]:
    """Salutations d'ouverture et formules de fin (les 3 premières / dernières
    phrases). Une phrase qui salue puis annonce le sujet (« Bonjour, aujourd'hui
    on parle de… ») reste : seule une salutation courte, ou une phrase qui
    n'est que politesse, part."""
    out: set[int] = set()
    n = len(sents)
    for s in sents[:3]:
        k = _hits(s["text"], _GREET)
        if k and (len(s["words"]) <= 7 or k >= 2):
            out.add(s["i"])
    for s in sents[max(0, n - 3):]:
        k = _hits(s["text"], _OUTRO)
        if k and (len(s["words"]) <= 9 or k >= 2):
            out.add(s["i"])
    return out


def detect_retakes(sents: list[dict]) -> set[int]:
    """Reprises : une phrase répétée (ou un faux départ) dans les 20 s qui
    suivent → on garde la dernière prise, la ou les premières sautent."""
    out: set[int] = set()
    for i, a in enumerate(sents):
        ta = _tokens(a["text"])
        if not ta:
            continue
        for b in sents[i + 1:i + 4]:
            if b["start"] - a["end"] > 20:
                break
            tb = _tokens(b["text"])
            if not tb:
                continue
            inter = len(set(ta) & set(tb))
            jacc = inter / len(set(ta) | set(tb))
            false_start = len(ta) <= 5 and tb[:min(3, len(ta))] == ta[:min(3, len(ta))] and len(tb) > len(ta)
            if jacc >= 0.6 or false_start:
                out.add(a["i"])
                break
    return out


def keywords(sent: dict, limit: int = 3) -> str:
    """Quelques mots qui résument la phrase (chiffres et mots forts d'abord)."""
    raw = [w["text"] for w in sent["words"]]
    cands: list[tuple[float, int, str]] = []
    for k, word in enumerate(raw):
        word = _ELISION.sub("", word.strip(".,;:!?…«»\"'()"))     # « d'argent » -> « argent »
        t = norm(word)
        if not t or t in _STOP or len(t) < 3 and not t.isdigit():
            continue
        pts = 1.0 + (2.0 if t in _STRONG else 0) + (2.5 if re.search(r"\d", word) else 0) + min(1.0, len(t) / 8)
        cands.append((pts, k, word))
    if not cands:
        return ""
    best = sorted(cands, key=lambda c: -c[0])[:limit]
    return " ".join(w for _, _, w in sorted(best, key=lambda c: c[1]))


# ---------------------------------------------------------------- analyse

def analyze(words: list[dict], wave: bytes | None = None, rate: int = 100) -> dict:
    sents = sentences(words)
    score_sentences(sents, wave, rate)
    return {"sentences": [{k: v for k, v in s.items() if k != "words"} | {"n": len(s["words"])} for s in sents],
            "fluff": sorted(detect_fluff(sents)), "retakes": sorted(detect_retakes(sents)),
            "_sents": sents}


# -------------------------------------------------------------------- plan

def _ranges(sents: list[dict], idx: set[int]) -> list[list[float]]:
    return [[s["start"], s["end"]] for s in sents if s["i"] in idx]


def heuristic_plan(analysis: dict, opts: dict) -> dict:
    sents = analysis["_sents"]
    if not sents:
        return {"hook": None, "drop": [], "highlights": [], "texts": [], "cutpoints": [], "title": "", "llm": False}
    drop: set[int] = set()
    if opts.get("trim", True):
        drop |= set(analysis["fluff"]) | set(analysis["retakes"])
    kept = [s for s in sents if s["i"] not in drop]
    ranked = sorted(kept, key=lambda s: -s.get("score", 0))

    hook = None
    if kept:
        # la phrase la plus forte du premier tiers ; si une phrase bien plus
        # forte arrive plus tard et tient en quelques secondes, elle passe en
        # tête (ouverture à froid)
        early = [s for s in kept[:max(1, len(kept) // 3)]]
        best = max(early, key=lambda s: s.get("score", 0))
        top = ranked[0]
        cold = (top is not best and top.get("score", 0) >= 2.5 and best.get("score", 0) < 1.0
                and top["end"] - top["start"] <= HOOK_MAX)
        if cold:
            best = top
        hook = {"sentence": best["i"], "s": best["start"], "e": best["end"],
                "text": _hook_text(best), "cold_open": cold}

    highlights = []
    for s in ranked[:3]:
        if s.get("score", 0) >= 1.5:
            highlights.append({"s": s["start"], "e": s["end"], "label": "Moment fort", "score": s["score"],
                               "sentence": s["i"]})
    texts = []
    for s in ranked[:4]:
        if s.get("score", 0) < 1.0 or (hook and s["i"] == hook["sentence"]):
            continue
        kw = keywords(s)
        if kw:
            texts.append({"sentence": s["i"], "s": s["start"], "e": s["end"], "text": kw})
    return {"hook": hook, "drop": _ranges(sents, drop), "drop_sentences": sorted(drop),
            "highlights": sorted(highlights, key=lambda x: x["s"]), "texts": sorted(texts, key=lambda x: x["s"]),
            "cutpoints": _cutpoints(sents), "title": _hook_text(ranked[0]) if ranked else "", "llm": False}


def _hook_text(sent: dict, max_words: int = 7) -> str:
    """Accroche courte tirée d'une phrase : ses premiers mots forts, en clair."""
    raw = [w["text"].strip(".,;:!?…«»\"'()") for w in sent["words"]]
    raw = [w for w in raw if w]
    if len(raw) <= max_words:
        return " ".join(raw)
    # coupe au dernier mot fort dans la limite, sinon à la limite
    cut = max_words
    for k in range(max_words, 2, -1):
        if norm(raw[k - 1]) in _STRONG or re.search(r"\d", raw[k - 1]):
            cut = k
            break
    return " ".join(raw[:cut]) + "…"


def _cutpoints(sents: list[dict]) -> list[float]:
    """Fins de phrases utilisables pour rythmer (zoom) les longs passages."""
    return [s["end"] for s in sents if s["end"] - s["start"] >= 1.0]


def llm_plan(analysis: dict, opts: dict, language: str = "fr") -> dict | None:
    """Plan proposé par le modèle local, ou None (modèle absent, réponse illisible)."""
    from engine.pipeline import llm
    if not llm.available():
        return None
    sents = analysis["_sents"]
    if not sents:
        return None
    base = heuristic_plan(analysis, opts)
    hook = None
    drop: set[int] = set(base["drop_sentences"])
    highlights: list[dict] = []
    texts: list[dict] = []
    title = ""
    for w0 in range(0, len(sents), LLM_WINDOW):
        window = sents[w0:w0 + LLM_WINDOW]
        res = _ask(window, first=(w0 == 0), language=language)
        if res is None:
            if w0 == 0:
                return None
            continue
        valid = {s["i"] for s in window}
        if w0 == 0:
            title = str(res.get("titre") or "")[:60]
            hk = res.get("hook") or {}
            si = _int(hk.get("phrase"))
            if si in valid and str(hk.get("texte") or "").strip():
                s = sents[si]
                hook = {"sentence": si, "s": s["start"], "e": s["end"], "text": str(hk["texte"]).strip()[:48],
                        "cold_open": bool(hk.get("ouverture"))}
        for x in res.get("supprimer") or []:
            si = _int(x)
            if si in valid:
                drop.add(si)
        for m in res.get("moments_forts") or []:
            a, b = _int((m or {}).get("de")), _int((m or {}).get("a"))
            if a in valid and b in valid and b >= a and b - a <= 12:
                highlights.append({"s": sents[a]["start"], "e": sents[b]["end"], "sentence": a,
                                   "label": str(m.get("pourquoi") or "Moment fort")[:60],
                                   "score": max(s.get("score", 0) for s in sents[a:b + 1])})
        for t in res.get("textes") or []:
            si = _int((t or {}).get("phrase"))
            txt = str((t or {}).get("texte") or "").strip()
            if si in valid and 1 <= len(txt.split()) <= 6:
                s = sents[si]
                texts.append({"sentence": si, "s": s["start"], "e": s["end"], "text": txt[:40]})
    if hook and hook["sentence"] in drop:
        drop.discard(hook["sentence"])
    # garde-fou : jamais plus de MAX_DROP_RATIO des phrases
    if len(drop) > MAX_DROP_RATIO * len(sents):
        keep_scores = sorted(drop, key=lambda i: -sents[i].get("score", 0))
        while len(drop) > MAX_DROP_RATIO * len(sents) and keep_scores:
            drop.discard(keep_scores.pop(0))
    if not opts.get("trim", True):
        drop = set()
    return {"hook": hook or base["hook"], "drop": _ranges(sents, drop), "drop_sentences": sorted(drop),
            "highlights": sorted(highlights, key=lambda x: x["s"])[:6] or base["highlights"],
            "texts": sorted(texts, key=lambda x: x["s"])[:6] or base["texts"],
            "cutpoints": base["cutpoints"], "title": title or base["title"], "llm": True}


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _ask(window: list[dict], first: bool, language: str) -> dict | None:
    from engine.pipeline import llm
    lines = "\n".join(f"[{s['i']}] {s['start']:.1f}-{s['end']:.1f} : {s['text']}" for s in window)
    lang = "français" if (language or "fr").startswith("fr") else "la langue de la vidéo"
    system = ("Tu es un monteur vidéo spécialisé dans les shorts (TikTok, Reels, Shorts). "
              f"Tu réponds uniquement par un objet JSON valide, en {lang}, sans commentaire.")
    hook_part = ('''  "titre": "titre court du short (max 8 mots)",
  "hook": {"phrase": <numéro de la phrase la plus concrète et surprenante : un bénéfice, un chiffre, une question, une affirmation forte ; jamais une transition ni une opinion vague>, "texte": "accroche à afficher à l'écran au tout début, 3 à 7 mots, la promesse ou le bénéfice concret", "ouverture": <true si cette phrase se comprend seule et peut ouvrir la vidéo, sinon false>},
''' if first else "")
    user = f"""Transcription d'une vidéo face caméra, phrase par phrase, avec les secondes :

{lines}

Prépare le montage d'un short efficace. Réponds avec cet objet JSON :
{{
{hook_part}  "supprimer": [<numéros des phrases à retirer : salutations, remplissage, hésitations, répétitions, formules de fin>],
  "moments_forts": [{{"de": <numéro>, "a": <numéro>, "pourquoi": "raison en quelques mots"}}],
  "textes": [{{"phrase": <numéro>, "texte": "mot ou expression clé à afficher en grand, 1 à 4 mots"}}]
}}
Règles : 2 à 5 textes, sur les phrases les plus fortes, jamais deux sur la même phrase ; 1 à 3 moments forts, chacun sur des phrases consécutives ; ne supprime pas plus du tiers des phrases ; garde le fil de l'histoire."""
    try:
        return llm.chat_json(system, user, 900)
    except Exception:  # noqa: BLE001 - modèle en panne : les règles prennent le relais
        return None


def trim_to_duration(plan: dict, analysis: dict, target: float) -> None:
    """Retire les phrases les plus faibles jusqu'à tenir dans `target` secondes
    (hors accroche et moments forts), en plus des retraits déjà prévus."""
    sents = analysis["_sents"]
    dropped = set(plan.get("drop_sentences") or [])
    protected = {plan["hook"]["sentence"]} if plan.get("hook") else set()
    for hl in plan.get("highlights") or []:
        for s in sents:
            if s["start"] >= hl["s"] - 1e-3 and s["end"] <= hl["e"] + 1e-3:
                protected.add(s["i"])
    kept = [s for s in sents if s["i"] not in dropped]
    total = sum(s["end"] - s["start"] for s in kept)
    for s in sorted(kept, key=lambda x: x.get("score", 0)):
        if total <= target:
            break
        if s["i"] in protected:
            continue
        dropped.add(s["i"])
        total -= s["end"] - s["start"]
    plan["drop_sentences"] = sorted(dropped)
    plan["drop"] = _ranges(sents, dropped)
    plan["texts"] = [t for t in plan.get("texts") or [] if t["sentence"] not in dropped]


def build_plan(words: list[dict], wave: bytes | None, rate: int, opts: dict, language: str = "fr") -> dict:
    """Le plan complet d'un média : IA locale si possible, règles sinon."""
    analysis = analyze(words, wave, rate)
    plan = None
    if opts.get("llm", True):
        plan = llm_plan(analysis, opts, language)
    if plan is None:
        plan = heuristic_plan(analysis, opts)
    target = float(opts.get("max_duration") or 0)
    if target > 0:
        trim_to_duration(plan, analysis, target)
    plan["sentences"] = analysis["sentences"]
    plan["fluff"] = analysis["fluff"]
    plan["retakes"] = analysis["retakes"]
    return plan


# ------------------------------------------------------------------ visage

def face_anchor(video_path: str, samples: int = 8) -> dict | None:
    """Position moyenne du visage (0..1) dans une vidéo, ou None sans visage
    (ou sans OpenCV). Sert à zoomer sans sortir la tête du cadre."""
    try:
        import cv2
    except ImportError:
        return None
    model = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                         "face_detection_yunet_2023mar.onnx")
    if not os.path.isfile(model) or not hasattr(cv2, "FaceDetectorYN_create"):
        return None
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)   # OpenCV 5 : avertissement à chaque image
    except AttributeError:
        pass
    cap = cv2.VideoCapture(video_path)
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if n <= 0 or w <= 0 or h <= 0:
            return None
        det = cv2.FaceDetectorYN_create(model, "", (w, h), 0.6, 0.3, 500)
        hits: list[tuple[float, float, float]] = []
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (i + 0.5) / samples))
            ok, frame = cap.read()
            if not ok:
                continue
            _, faces = det.detect(frame)
            if faces is None or not len(faces):
                continue
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
            hits.append((float(x + fw / 2) / w, float(y + fh / 2) / h, float(fw) / w))
    except Exception:  # noqa: BLE001 - vidéo illisible par OpenCV
        return None
    finally:
        cap.release()
    if len(hits) < max(1, samples // 4):
        return None
    xs, ys, ws = (sorted(float(v[k]) for v in hits) for k in range(3))
    mid = len(hits) // 2
    # des flottants Python : le résultat part en JSON (cache et réponse)
    return {"x": round(xs[mid], 3), "y": round(ys[mid], 3), "w": round(ws[mid], 3), "samples": len(hits)}


def cache_key(words_path: str, opts: dict) -> str:
    try:
        stamp = f"{os.path.getmtime(words_path):.0f}:{os.path.getsize(words_path)}"
    except OSError:
        stamp = "0"
    keyed = {k: opts.get(k) for k in ("llm", "trim", "max_duration")}
    return hashlib.sha1((stamp + json.dumps(keyed, sort_keys=True)).encode()).hexdigest()[:12]
