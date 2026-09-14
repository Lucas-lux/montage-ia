"""Traduction locale des sous-titres (Opus-MT via CTranslate2).

Tout tourne sur la machine : un petit modèle Marian (~80 Mo en int8) converti
pour ctranslate2 — la bibliothèque qui fait déjà tourner faster-whisper, donc
rien de lourd en plus. Pas de réseau, pas de clé d'API.

Traduire ligne par ligne donnerait du charabia : une ligne de sous-titre fait
quatre mots. On regroupe donc les lignes en phrases (ponctuation de fin), on
traduit la phrase entière, puis on répartit les mots traduits sur les lignes
d'origine au prorata de leur longueur. Les lignes gardent horaires, position et
look : seul le texte change, et le texte transcrit reste dans `src_words` pour
pouvoir y revenir.

Récupérer le modèle (une fois) :  python scripts/download_models.py
"""
from __future__ import annotations

import functools
import itertools
import os

_SENTENCE_END = (".", "!", "?", "…")
_CLOSERS = "\"'»”)]"


def models_root() -> str:
    """Dossier des modèles : posé par app.py une fois installée, sinon celui du dépôt."""
    env = os.environ.get("MONTAGE_IA_TRANSLATE")
    if env:
        return env
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "models", "translate")


def model_dir(source: str, target: str) -> str:
    return os.path.join(models_root(), f"opus-mt-{source}-{target}")


def available(source: str, target: str) -> bool:
    d = model_dir(source, target)
    return all(os.path.isfile(os.path.join(d, f))
               for f in ("model.bin", "source.spm", "target.spm"))


@functools.lru_cache(maxsize=4)
def _load(source: str, target: str):
    import ctranslate2
    import sentencepiece as spm

    d = model_dir(source, target)
    # CPU int8 : une vidéo courte se traduit en une ou deux secondes, inutile
    # de disputer la mémoire du GPU à Whisper.
    translator = ctranslate2.Translator(d, device="cpu", compute_type="int8")
    sp_src = spm.SentencePieceProcessor(model_file=os.path.join(d, "source.spm"))
    sp_tgt = spm.SentencePieceProcessor(model_file=os.path.join(d, "target.spm"))
    return translator, sp_src, sp_tgt


def translate_texts(texts: list[str], source: str = "fr", target: str = "en") -> list[str]:
    if not texts:
        return []
    translator, sp_src, sp_tgt = _load(source, target)
    batch = [sp_src.encode(t, out_type=str) + ["</s>"] for t in texts]
    results = translator.translate_batch(batch, beam_size=4, max_batch_size=16,
                                         max_decoding_length=256)
    return [sp_tgt.decode([tok for tok in r.hypotheses[0] if tok != "</s>"]).strip()
            for r in results]


# ------------------------------------------------------------------ lignes


def _source_words(c: dict) -> list[dict]:
    return list(c.get("src_words") or c.get("words") or [])


def _text(words: list[dict]) -> str:
    return " ".join(str(w.get("text", "")).strip() for w in words).strip()


def sentence_units(captions: list[dict], max_lines: int = 6) -> list[list[int]]:
    """Regroupe les lignes consécutives en phrases à traduire d'un bloc.

    Une phrase se ferme sur sa ponctuation finale ; faute de ponctuation, on
    coupe tous les `max_lines` pour ne pas envoyer un monologue d'un coup.
    """
    units: list[list[int]] = []
    cur: list[int] = []
    for i, c in enumerate(captions):
        cur.append(i)
        text = _text(_source_words(c)).rstrip(_CLOSERS)
        if text.endswith(_SENTENCE_END) or len(cur) >= max_lines:
            units.append(cur)
            cur = []
    if cur:
        units.append(cur)
    return units


def distribute(tokens: list[str], weights: list[float]) -> list[list[str]]:
    """Répartit les mots traduits sur n lignes, au prorata de `weights`.

    Chaque mot va à la ligne dont la part couvre son milieu ; tant qu'il reste
    assez de mots, chaque ligne en reçoit au moins un.
    """
    n = len(weights)
    out: list[list[str]] = [[] for _ in range(n)]
    if not n:
        return out
    weights = [max(1.0, float(w)) for w in weights]
    total_w = sum(weights)
    bounds = list(itertools.accumulate(w / total_w for w in weights))
    total_c = sum(len(t) + 1 for t in tokens) or 1

    i, done = 0, 0
    for k, tok in enumerate(tokens):
        mid = (done + (len(tok) + 1) / 2) / total_c
        while i < n - 1 and out[i] and mid > bounds[i]:
            i += 1
        if i < n - 1 and out[i] and len(tokens) - k <= n - 1 - i:
            i += 1
        out[i].append(tok)
        done += len(tok) + 1
    return out


def retime(tokens: list[str], start: float, end: float) -> list[dict]:
    """Cale les mots sur la durée de la ligne au prorata de leur longueur
    (même règle que `setText` dans l'éditeur), sans jamais déborder de la
    ligne : un mot au-delà de sa fin serait ignoré à l'incrustation."""
    span = max(0.0, end - start)
    total = sum(len(t) for t in tokens) or 1
    t0, out = start, []
    for tok in tokens:
        d = span * len(tok) / total
        out.append({"text": tok, "start": round(t0, 3), "end": round(t0 + d, 3)})
        t0 += d
    return out


def translate_captions(captions: list[dict], source: str = "fr",
                       target: str = "en") -> list[dict]:
    """Copies traduites des lignes : mêmes id, même ordre, texte remplacé.

    Une ligne déjà traduite repart de son texte d'origine (`src_words`) : on ne
    traduit jamais une traduction. Les lignes masquées par l'utilisateur ne
    bougent pas ; celles qu'une traduction précédente avait vidées reviennent.
    """
    caps = [dict(c) for c in captions]
    live = [c for c in caps if not c.get("hidden") or c.get("tr_hidden")]
    units = sentence_units(live)
    texts = [" ".join(_text(_source_words(live[i])) for i in unit) for unit in units]
    translated = translate_texts(texts, source, target)

    for unit, text in zip(units, translated):
        lines = [live[i] for i in unit]
        sources = [_source_words(c) for c in lines]
        parts = distribute(text.split(), [len(_text(s)) for s in sources])
        for c, src, toks in zip(lines, sources, parts):
            c["src_words"] = src
            c["lang"] = target
            if toks:
                c["words"] = retime(toks, float(c["start"]), float(c["end"]))
                if c.pop("tr_hidden", False):
                    c["hidden"] = False
            else:
                # Traduction plus courte que l'original : la ligne n'a plus rien à dire.
                c["words"] = [dict(w) for w in src]
                c["hidden"] = True
                c["tr_hidden"] = True
    return caps
