"""Découpe des mots en lignes de sous-titres + génération .ass (chemin CLI).

Étapes : on nettoie d'abord les sous-mots de Whisper (élisions/ponctuation
recollées en vrais mots), on groupe par ligne (limite mots ET caractères pour
tenir dans le cadre), et on pose les tags \\kf pour le balayage karaoké.

Le groupage (`clean_words` / `group_indices`) est partagé avec l'éditeur web :
la CLI et l'interface découpent les lignes exactement de la même façon. Seul le
rendu diffère — ici un .ass figé, là-bas des sous-titres déplaçables
(cf. `ass_edit`).

Couleurs ASS &HAABBGGRR (AA=00 -> opaque). PrimaryColour = mot déjà dit,
SecondaryColour = mot actif/à venir.
"""
from __future__ import annotations

from engine.edl import Word
from engine.pipeline.emoji import emoji_for

STYLES: dict[str, dict] = {
    "classic": dict(font="Arial", size=84, primary="&H00FFFFFF", secondary="&H0000E5FF",
                    outline_col="&H00000000", back="&H64000000", bold=-1, border_style=1,
                    outline=6, shadow=2, align=2, margin_v=300, upper=False),
    "punch": dict(font="Arial", size=98, primary="&H00FFFFFF", secondary="&H0000E5FF",
                  outline_col="&H00000000", back="&H64000000", bold=-1, border_style=1,
                  outline=11, shadow=0, align=5, margin_v=0, upper=True),
    "hype": dict(font="Arial", size=86, primary="&H00FFFFFF", secondary="&H0000FF00",
                 outline_col="&H40000000", back="&H64000000", bold=-1, border_style=3,
                 outline=5, shadow=0, align=2, margin_v=320, upper=False),
    "neon": dict(font="Arial", size=88, primary="&H00FFFFFF", secondary="&H00FFFF00",
                 outline_col="&H00AA00FF", back="&H64000000", bold=-1, border_style=1,
                 outline=5, shadow=3, align=2, margin_v=300, upper=False),
    "clean": dict(font="Arial", size=62, primary="&H00FFFFFF", secondary="&H00FFFFFF",
                  outline_col="&H00000000", back="&H64000000", bold=-1, border_style=1,
                  outline=3, shadow=1, align=2, margin_v=250, upper=False),
}

_HEADER_TPL = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{secondary},{outline_col},{back},{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},{align},80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Caractères qui se recollent au mot précédent (apostrophes, ponctuation fermante).
_APOS = "'’"
_ATTACH_PUNCT = set(",.!?;:…)»")


def _header(style: str) -> str:
    return _HEADER_TPL.format(**STYLES.get(style, STYLES["classic"]))


def _ts(t: float) -> str:
    # Arrondi AVANT de découper : 59,999 s doit donner 0:01:00.00, pas 0:00:60.00.
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_words(words: list[Word]) -> list[Word]:
    """Recolle les sous-mots de Whisper en vrais mots (ex: 't' + ''enferme'
    -> 't'enferme', 'mots' + ',' -> 'mots,'). Le timing du mot fusionné couvre
    du début du premier au fin du dernier morceau.
    """
    out: list[Word] = []
    for w in words:
        raw = w.text
        token = raw.strip()
        if not token:
            continue
        if out:
            prev = out[-1]
            lead_space = raw[0].isspace()
            attach = (
                not lead_space
                or token[0] in _APOS
                or prev.text[-1] in _APOS
                or token[0] in _ATTACH_PUNCT
            )
            if attach:
                out[-1] = Word(text=prev.text + token, start=prev.start, end=w.end)
                continue
        out.append(Word(text=token, start=w.start, end=w.end))
    return out


def group_indices(texts: list[str], max_words: int, max_chars: int) -> list[list[int]]:
    """Coupe une suite de mots en lignes qui tiennent dans le cadre.

    Retourne des INDEX (pas les mots) pour que l'appelant garde le lien avec sa
    propre structure — l'éditeur a besoin de savoir de quel mot source vient
    chaque ligne.
    """
    groups: list[list[int]] = []
    cur: list[int] = []
    cur_chars = 0
    for i, text in enumerate(texts):
        wlen = len(text)
        if cur and (len(cur) >= max_words or cur_chars + 1 + wlen > max_chars):
            groups.append(cur)
            cur, cur_chars = [], 0
        cur.append(i)
        cur_chars += wlen if cur_chars == 0 else 1 + wlen
    if cur:
        groups.append(cur)
    return groups


def build_ass(
    words: list[Word],
    out_path: str,
    words_per_line: int = 4,
    max_chars: int = 18,
    style: str = "classic",
    emojis: bool = True,
    emoji_min_gap: float = 3.0,
) -> list[tuple[str, float, float]]:
    """Écrit le .ass et retourne les émojis à superposer : [(emoji, start, end)].

    L'émoji n'est PAS placé dans le texte ASS (libass ne rend pas la couleur) :
    il est overlay en image couleur au rendu. 1 émoji max par ligne.
    """
    upper = STYLES.get(style, STYLES["classic"])["upper"]
    clean = clean_words(words)
    groups = [[clean[i] for i in idx]
              for idx in group_indices([w.text for w in clean], words_per_line, max_chars)]
    events: list[str] = []
    occurrences: list[tuple[str, float, float]] = []
    last_emoji_t = -1e9  # espacement mini entre 2 émojis (sélectif, pas un par ligne)

    for group in groups:
        if not group:
            continue
        start, end = group[0].start, group[-1].end

        # Émoji : 1er mot de la ligne ayant une correspondance, espacé dans le temps.
        if emojis and start - last_emoji_t >= emoji_min_gap:
            for w in group:
                e = emoji_for(w.text)
                if e:
                    occurrences.append((e, start, end))
                    last_emoji_t = start
                    break

        text = ""
        for i, w in enumerate(group):
            token = w.text.upper() if upper else w.text
            dur_cs = max(1, int(round((w.end - w.start) * 100)))
            sep = "" if i == 0 else " "
            text += f"{sep}{{\\kf{dur_cs}}}{token}"

        events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(_header(style))
        f.write("\n".join(events) + "\n")

    return occurrences
