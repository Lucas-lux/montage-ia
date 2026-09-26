r"""Presets de sous-titres — source de vérité partagée éditeur <-> rendu.

L'éditeur web affiche ces valeurs en HTML/CSS, `ass_edit` les traduit en tags
libass. Un seul dictionnaire pour les deux : ce qu'on voit dans l'éditeur est
ce qui est incrusté à l'export.

Conventions :
  * `x`/`y` sont normalisés 0..1 et désignent le CENTRE du bloc de texte
    (équivalent ASS `\an5` + `\pos`). Indépendant de la résolution.
  * `size`, `outline`, `shadow` sont en pixels de la frame de SORTIE
    (1080x1920 en vertical).
  * `outline_col` sert de couleur de contour ET de couleur de boîte quand
    `box` est vrai (c'est le comportement de libass avec BorderStyle=3).
  * `mode` : "word" = seul le mot prononcé est surligné (style shorts),
    "sweep" = surlignage progressif type karaoké, "reveal" = les mots
    apparaissent au fil de la voix, "dim" = les mots non prononcés sont
    estompés, "none" = texte statique.
  * effets (0 / "" = absents) : `glow` lueur autour du texte, `outline2`
    second contour, `extrude` relief 3D (profondeur), `shadow_blur` ombre
    douce, `color2` dégradé de gauche à droite, `hollow` texte évidé (contour
    seul), `spacing` espacement des lettres, `rotation` en degrés (sens
    horaire), `opacity`. Tous en pixels de sortie, comme `size`.
"""
from __future__ import annotations

# Valeurs communes : tout preset part de là et surcharge ce qu'il veut.
BASE: dict = {
    "font": "Arial",
    "size": 86,
    "bold": True,
    "upper": False,
    "color": "#FFFFFF",       # texte au repos
    "hl": "#00FF66",          # mot surligné
    "outline_col": "#000000",
    "outline": 5.0,
    "shadow": 0.0,
    "box": False,
    "box_alpha": 0.25,        # 0 = opaque .. 1 = invisible (utilisé si box)
    "mode": "word",
    "pop": True,              # léger zoom sur le mot actif
    "x": 0.5,
    "y": 0.82,
    # effets
    "italic": False,
    "spacing": 0.0,           # espacement des lettres
    "color2": "",             # dégradé : couleur de fin ("" = texte uni)
    "shadow_col": "#000000",
    "shadow_blur": 0.0,       # ombre douce (0 = ombre nette)
    "glow": 0.0,              # lueur : rayon
    "glow_col": "#FFFFFF",
    "outline2": 0.0,          # second contour, autour du premier
    "outline2_col": "#FFFFFF",
    "extrude": 0.0,           # relief 3D : profondeur
    "extrude_col": "#000000",
    "hollow": False,          # contour seul, intérieur transparent
    "rotation": 0.0,
    "opacity": 1.0,
}

# Surlignages possibles (champ `mode`).
MODES = ("word", "sweep", "reveal", "dim", "none")
# Champs couleur (validés comme tels ; `color2` peut être vide).
COLOR_FIELDS = ("color", "hl", "outline_col", "color2", "shadow_col", "glow_col", "outline2_col", "extrude_col")

# Groupes affichés par l'interface, dans cet ordre.
GROUPS: dict[str, str] = {
    "tendance": "Tendance", "createurs": "Créateurs", "anime": "Animés", "neon": "Néon et lueur",
    "relief": "Relief et 3D", "degrade": "Dégradés", "fond": "Sur fond", "sobre": "Sobre", "couleur": "Couleur",
    "manuscrit": "Manuscrits", "retro": "Rétro et jeux", "fun": "Fun",
}

# Polices : présentes sur tout Windows 10/11 ; ailleurs, libass en choisit une
# proche. `featured` : proposés sur l'écran d'import du mode short.
PRESETS: dict[str, dict] = {
    # ---- tendance
    "hype": {
        "label": "Hype", "hint": "vert + boîte (recommandé)", "group": "tendance", "featured": True,
        "hl": "#00FF66", "box": True, "box_alpha": 0.25, "outline": 5.0,
    },
    "impact": {
        "label": "Impact", "hint": "majuscules, jaune", "group": "tendance", "featured": True,
        "font": "Impact", "size": 96, "upper": True, "hl": "#FFE500", "outline": 8.0,
    },
    "box": {
        "label": "Boîte", "hint": "texte sur fond noir", "group": "tendance", "featured": True,
        "font": "Segoe UI Black", "size": 80, "box": True, "box_alpha": 0.2, "hl": "#FFE500",
        "outline": 9.0, "pop": False,
    },
    "pop": {
        "label": "Pop", "hint": "rose, dynamique", "group": "tendance",
        "font": "Arial Black", "size": 84, "upper": True, "hl": "#FF3B81", "outline_col": "#2B0A18",
        "outline": 7.0,
    },
    "duo": {
        "label": "Duo", "hint": "cyan, condensé", "group": "tendance",
        "font": "Bahnschrift", "size": 92, "upper": True, "hl": "#00E5FF", "outline_col": "#0B1B2B",
        "outline": 6.0, "shadow": 2.0,
    },
    "gras": {
        "label": "Gras", "hint": "épais, jaune", "group": "tendance",
        "font": "Segoe UI Black", "size": 88, "hl": "#FFE500", "outline": 8.0,
    },
    # ---- sur fond
    "ruban": {
        "label": "Ruban", "hint": "bandeau rouge", "group": "fond",
        "size": 74, "upper": True, "box": True, "box_alpha": 0.0, "outline_col": "#F23A52",
        "hl": "#FFE500", "outline": 12.0, "pop": False,
    },
    "journal": {
        "label": "Journal", "hint": "fond blanc, texte sombre", "group": "fond",
        "font": "Segoe UI", "size": 68, "box": True, "box_alpha": 0.05, "outline_col": "#FFFFFF",
        "color": "#17181C", "hl": "#D82C43", "outline": 12.0, "pop": False,
    },
    "machine": {
        "label": "Machine", "hint": "terminal, balayage", "group": "fond",
        "font": "Consolas", "size": 70, "box": True, "box_alpha": 0.1, "outline_col": "#0B0F0B",
        "color": "#D8FFE0", "hl": "#00FF66", "outline": 10.0, "mode": "sweep", "pop": False,
    },
    "ardoise": {
        "label": "Ardoise", "hint": "fond bleu nuit", "group": "fond",
        "font": "Corbel", "size": 76, "box": True, "box_alpha": 0.15, "outline_col": "#1E2A3A",
        "color": "#F5F7FA", "hl": "#FFC93C", "outline": 11.0,
    },
    "contraste": {
        "label": "Contraste", "hint": "sombre, contour blanc", "group": "fond",
        "font": "Arial Black", "size": 84, "color": "#17181C", "hl": "#F23A52", "outline_col": "#FFFFFF",
        "outline": 7.0,
    },
    # ---- sobre
    "clean": {
        "label": "Clean", "hint": "sobre / pro", "group": "sobre", "featured": True,
        "size": 62, "hl": "#FFFFFF", "outline": 3.0, "shadow": 1.0, "y": 0.85,
        "mode": "sweep", "pop": False,
    },
    "minimal": {
        "label": "Minimal", "hint": "petit, sans surlignage", "group": "sobre",
        "font": "Segoe UI", "size": 56, "hl": "#FFFFFF", "outline": 3.0, "shadow": 1.0, "y": 0.86,
        "mode": "none", "pop": False,
    },
    "elegant": {
        "label": "Élégant", "hint": "serif, balayage", "group": "sobre",
        "font": "Georgia", "size": 72, "bold": False, "hl": "#F5D0A9", "outline_col": "#1A1410",
        "outline": 2.0, "shadow": 2.0, "mode": "sweep", "pop": False, "y": 0.84,
    },
    "ombre": {
        "label": "Ombre", "hint": "sans contour, ombre portée", "group": "sobre",
        "font": "Trebuchet MS", "size": 84, "hl": "#FFFFFF", "outline": 0.0, "shadow": 6.0,
    },
    "haut": {
        "label": "Haut", "hint": "en haut de l'image", "group": "sobre",
        "size": 78, "box": True, "box_alpha": 0.3, "hl": "#00FF66", "y": 0.14,
    },
    # ---- couleur
    "classic": {
        "label": "Classic", "hint": "blanc + jaune", "group": "couleur", "featured": True,
        "hl": "#FFE500", "outline": 6.0, "shadow": 2.0,
    },
    "punch": {
        "label": "Punch", "hint": "ÉNORME majuscules", "group": "couleur", "featured": True,
        "size": 98, "upper": True, "hl": "#FFE500", "outline": 11.0, "y": 0.5,
    },
    "neon": {
        "label": "Neon", "hint": "cyan / magenta", "group": "couleur", "featured": True,
        "size": 88, "hl": "#00FFFF", "outline_col": "#FF00AA", "shadow": 3.0,
    },
    "karaoke": {
        "label": "Karaoké", "hint": "balayage jaune", "group": "couleur", "featured": True,
        "size": 82, "hl": "#FFE500", "mode": "sweep", "pop": False,
    },
    "fluo": {
        "label": "Fluo", "hint": "vert fluo", "group": "couleur",
        "font": "Arial Black", "size": 86, "upper": True, "color": "#C6FF00", "hl": "#FFFFFF",
        "outline_col": "#1B2600", "outline": 6.0,
    },
    "or": {
        "label": "Or", "hint": "doré", "group": "couleur",
        "font": "Cambria", "size": 84, "color": "#FFD84D", "hl": "#FFFFFF", "outline_col": "#3A2800",
        "outline": 5.0, "shadow": 3.0,
    },
    "glace": {
        "label": "Glace", "hint": "bleu glacé", "group": "couleur",
        "font": "Bahnschrift", "size": 88, "hl": "#7FD8FF", "outline_col": "#0B3D91", "outline": 6.0,
    },
    "feu": {
        "label": "Feu", "hint": "orange et rouge", "group": "couleur",
        "font": "Impact", "size": 92, "upper": True, "color": "#FFE08A", "hl": "#FF4D1A",
        "outline_col": "#3A0A00", "outline": 7.0, "shadow": 2.0,
    },
    # ---- fun
    "retro": {
        "label": "Rétro", "hint": "chaud, années 70", "group": "fun",
        "font": "Georgia", "size": 84, "color": "#FFF3D6", "hl": "#FF9F1C", "outline_col": "#4A1F00",
        "outline": 6.0, "shadow": 4.0,
    },
    "bulle": {
        "label": "Bulle", "hint": "bande dessinée", "group": "fun",
        "font": "Comic Sans MS", "size": 80, "hl": "#FF6BD6", "outline_col": "#3B0F33", "outline": 7.0,
    },
    "manuscrit": {
        "label": "Manuscrit", "hint": "écrit à la main", "group": "fun",
        "font": "Ink Free", "size": 96, "hl": "#FFE500", "outline": 5.0, "shadow": 2.0,
    },
    "script": {
        "label": "Cursive", "hint": "calligraphie", "group": "fun",
        "font": "Segoe Script", "size": 80, "hl": "#F5D0A9", "outline": 4.0, "shadow": 3.0,
        "mode": "sweep", "pop": False,
    },
    # ---- créateurs (polices livrées : engine/data/fonts ; `bold` faux, elles sont déjà grasses)
    "beast": {
        "label": "Beast", "hint": "blanc, contour épais, vert", "group": "createurs",
        "font": "Luckiest Guy", "bold": False, "size": 96, "upper": True, "hl": "#50FF00", "outline": 8.0,
        "shadow": 4.0,
    },
    "hormozi": {
        "label": "Punchline", "hint": "Montserrat, mot clé jaune", "group": "createurs",
        "font": "Montserrat Black", "bold": False, "size": 88, "upper": True, "hl": "#FFE500", "outline": 6.0,
        "shadow": 6.0, "shadow_blur": 8.0, "y": 0.74,
    },
    "focus": {
        "label": "Focus", "hint": "le mot dit ressort, les autres s'estompent", "group": "createurs",
        "font": "Inter ExtraBold", "bold": False, "size": 76, "hl": "#FFFFFF", "outline": 0.0,
        "shadow": 5.0, "shadow_blur": 9.0, "mode": "dim", "pop": False,
    },
    "reveal": {
        "label": "Apparition", "hint": "les mots s'affichent au fil de la voix", "group": "createurs",
        "font": "Montserrat Black", "bold": False, "size": 86, "hl": "#00E5FF", "outline": 6.0, "mode": "reveal",
    },
    "podcast": {
        "label": "Podcast", "hint": "Poppins, violet", "group": "createurs",
        "font": "Poppins ExtraBold", "bold": False, "size": 80, "hl": "#A78BFA", "outline": 5.0,
        "glow": 10.0, "glow_col": "#7C3AED",
    },
    "jaune3d": {
        "label": "Mot jaune", "hint": "majuscules, relief noir", "group": "createurs",
        "font": "Archivo Black", "bold": False, "size": 84, "upper": True, "hl": "#FFE500", "outline": 6.0,
        "extrude": 5.0, "extrude_col": "#000000",
    },
    "geant": {
        "label": "Géant", "hint": "énorme, au centre (idéal en mot à mot)", "group": "createurs",
        "font": "Anton", "bold": False, "size": 150, "upper": True, "hl": "#FF3B81", "outline": 8.0, "y": 0.5,
    },
    "unmot": {
        "label": "Un mot", "hint": "jaune en relief (idéal en mot à mot)", "group": "createurs",
        "font": "Bebas Neue", "bold": False, "size": 170, "upper": True, "color": "#FFE500", "hl": "#FFFFFF",
        "outline": 6.0, "extrude": 8.0, "extrude_col": "#B33A00", "y": 0.5,
    },
    # ---- animés : chaque ligne entre en scène
    "pop_in": {
        "label": "Pop", "hint": "chaque ligne surgit", "group": "anime",
        "font": "Montserrat Black", "bold": False, "size": 86, "hl": "#FFE500", "outline": 6.0,
        "anim_in": {"type": "pop", "dur": 0.25},
    },
    "rebond": {
        "label": "Rebond", "hint": "chaque ligne rebondit", "group": "anime",
        "font": "Luckiest Guy", "bold": False, "size": 92, "upper": True, "hl": "#00E5FF", "outline": 7.0,
        "anim_in": {"type": "bounce", "dur": 0.45},
    },
    "ecrire": {
        "label": "Machine", "hint": "tapé lettre par lettre", "group": "anime",
        "font": "Syne Mono", "bold": False, "size": 74, "box": True, "box_alpha": 0.1, "outline_col": "#0B0F0B",
        "color": "#D8FFE0", "hl": "#39FF14", "outline": 10.0, "pop": False,
        "anim_in": {"type": "typewriter", "dur": 0.6},
    },
    "glisse": {
        "label": "Glissé", "hint": "monte doucement, s'efface", "group": "anime",
        "font": "Poppins ExtraBold", "bold": False, "size": 82, "hl": "#FF9F1C", "outline": 5.0,
        "anim_in": {"type": "slide_up", "dur": 0.25}, "anim_out": {"type": "fade_out", "dur": 0.15},
    },
    "impactzoom": {
        "label": "Impact", "hint": "arrive de loin", "group": "anime",
        "font": "Anton", "bold": False, "size": 110, "upper": True, "hl": "#FFE500", "outline": 7.0,
        "anim_in": {"type": "zoom_out", "dur": 0.25},
    },
    "pulse": {
        "label": "Pulsation", "hint": "bat au rythme", "group": "anime",
        "font": "Titan One", "bold": False, "size": 88, "hl": "#FF3B81", "outline": 7.0,
        "anim_loop": {"type": "pulse", "speed": 1.6},
    },
    "flou": {
        "label": "Mise au point", "hint": "sort du flou", "group": "anime",
        "font": "Lexend Bold", "bold": False, "size": 80, "hl": "#FFFFFF", "outline": 0.0, "shadow": 5.0,
        "shadow_blur": 8.0, "mode": "dim", "pop": False, "anim_in": {"type": "blur", "dur": 0.3},
    },
    # ---- néon et lueur
    "neon_rose": {
        "label": "Néon rose", "hint": "lueur rose", "group": "neon",
        "font": "Montserrat Black", "bold": False, "size": 86, "color": "#FFFFFF", "hl": "#FFD6F5",
        "outline_col": "#FF2BD6", "outline": 3.0, "glow": 26.0, "glow_col": "#FF2BD6", "mode": "dim", "pop": False,
    },
    "neon_bleu": {
        "label": "Néon bleu", "hint": "lueur cyan", "group": "neon",
        "font": "Audiowide", "bold": False, "size": 80, "color": "#FFFFFF", "hl": "#AEF3FF",
        "outline_col": "#00C8FF", "outline": 3.0, "glow": 26.0, "glow_col": "#00C8FF",
    },
    "neon_vert": {
        "label": "Néon vert", "hint": "lueur verte", "group": "neon",
        "font": "Righteous", "bold": False, "size": 86, "color": "#E9FFE0", "hl": "#FFFFFF",
        "outline_col": "#39FF14", "outline": 3.0, "glow": 24.0, "glow_col": "#39FF14",
    },
    "halo": {
        "label": "Halo", "hint": "texte lumineux", "group": "neon",
        "font": "Poppins ExtraBold", "bold": False, "size": 82, "color": "#FFFFFF", "hl": "#FFE500",
        "outline": 0.0, "glow": 30.0, "glow_col": "#FFFFFF",
    },
    "braise": {
        "label": "Braise", "hint": "orange incandescent", "group": "neon",
        "font": "Kanit ExtraBold", "bold": False, "size": 88, "upper": True, "color": "#FFE08A", "hl": "#FFFFFF",
        "outline_col": "#C21E00", "outline": 4.0, "glow": 22.0, "glow_col": "#FF4D1A",
    },
    "cyber": {
        "label": "Cyber", "hint": "dégradé cyan / magenta", "group": "neon",
        "font": "Audiowide", "bold": False, "size": 80, "color": "#00E5FF", "color2": "#FF00AA", "hl": "#FFFFFF",
        "outline_col": "#12002B", "outline": 4.0, "glow": 14.0, "glow_col": "#7C5CFF",
    },
    # ---- relief et 3D
    "pop3d": {
        "label": "Pop 3D", "hint": "jaune sur relief rose", "group": "relief",
        "font": "Bangers", "bold": False, "size": 110, "upper": True, "color": "#FFE500", "hl": "#FFFFFF",
        "outline_col": "#1B1B1B", "outline": 5.0, "extrude": 14.0, "extrude_col": "#C2185B",
    },
    "bloc3d": {
        "label": "Bloc 3D", "hint": "blanc sur relief rouge", "group": "relief",
        "font": "Russo One", "bold": False, "size": 84, "hl": "#FFE500", "outline": 4.0,
        "extrude": 12.0, "extrude_col": "#F23A52",
    },
    "ormassif": {
        "label": "Or massif", "hint": "doré en relief", "group": "relief",
        "font": "Alfa Slab One", "bold": False, "size": 86, "color": "#FFE27A", "color2": "#FFA000",
        "hl": "#FFFFFF", "outline_col": "#3A2800", "outline": 4.0, "extrude": 10.0, "extrude_col": "#6B4A00",
    },
    "bonbon": {
        "label": "Bonbon", "hint": "rose acidulé", "group": "relief",
        "font": "Titan One", "bold": False, "size": 88, "color": "#FFFFFF", "hl": "#FFE500",
        "outline_col": "#FF3B81", "outline": 6.0, "extrude": 10.0, "extrude_col": "#9C1B4F",
    },
    "bd": {
        "label": "BD", "hint": "double contour jaune", "group": "relief",
        "font": "Bangers", "bold": False, "size": 104, "upper": True, "color": "#FFFFFF", "hl": "#FF3B30",
        "outline": 6.0, "outline2": 6.0, "outline2_col": "#FFE500",
    },
    "autocollant": {
        "label": "Autocollant", "hint": "contour blanc épais", "group": "relief",
        "font": "Fredoka Bold", "bold": False, "size": 88, "color": "#17181C", "hl": "#F23A52",
        "outline_col": "#FFFFFF", "outline": 9.0, "shadow": 5.0, "shadow_blur": 8.0,
    },
    # ---- dégradés
    "sunset": {
        "label": "Coucher de soleil", "hint": "corail vers doré", "group": "degrade",
        "font": "Poppins ExtraBold", "bold": False, "size": 86, "color": "#FF5F6D", "color2": "#FFC371",
        "hl": "#FFFFFF", "outline_col": "#2B0A18", "outline": 5.0,
    },
    "ocean": {
        "label": "Océan", "hint": "bleu profond", "group": "degrade",
        "font": "Montserrat Black", "bold": False, "size": 86, "color": "#00C6FF", "color2": "#0072FF",
        "hl": "#FFFFFF", "outline_col": "#001A33", "outline": 5.0,
    },
    "menthe": {
        "label": "Menthe", "hint": "vert frais", "group": "degrade",
        "font": "Nunito Black", "bold": False, "size": 88, "color": "#43E97B", "color2": "#38F9D7",
        "hl": "#FFFFFF", "outline_col": "#002B1A", "outline": 5.0,
    },
    "aurore": {
        "label": "Aurore", "hint": "violet vers bleu", "group": "degrade",
        "font": "Rubik Black", "bold": False, "size": 86, "color": "#B721FF", "color2": "#21D4FD",
        "hl": "#FFFFFF", "outline_col": "#14002B", "outline": 5.0,
    },
    "champagne": {
        "label": "Champagne", "hint": "or, élégant", "group": "degrade",
        "font": "Playfair Display ExtraBold", "bold": False, "size": 84, "color": "#F7E08A", "color2": "#C8942B",
        "hl": "#FFFFFF", "outline_col": "#2B1A00", "outline": 3.0, "shadow": 4.0, "mode": "sweep", "pop": False,
    },
    # ---- manuscrits
    "marqueur": {
        "label": "Marqueur", "hint": "feutre", "group": "manuscrit",
        "font": "Permanent Marker", "bold": False, "size": 90, "hl": "#FFE500", "outline": 5.0,
    },
    "craie": {
        "label": "Craie", "hint": "écrit à la main, ombre douce", "group": "manuscrit",
        "font": "Caveat Bold", "bold": False, "size": 108, "hl": "#FFE500", "outline": 0.0, "shadow": 5.0,
        "shadow_blur": 6.0,
    },
    "tendre": {
        "label": "Tendre", "hint": "rose, lueur douce", "group": "manuscrit",
        "font": "Pacifico", "bold": False, "size": 84, "color": "#FFF0F5", "hl": "#FFFFFF",
        "outline_col": "#FF3B81", "outline": 5.0, "glow": 12.0, "glow_col": "#FF8FB1", "mode": "sweep", "pop": False,
    },
    "signature": {
        "label": "Signature", "hint": "calligraphie", "group": "manuscrit",
        "font": "Great Vibes", "bold": False, "size": 120, "hl": "#F5D0A9", "outline": 3.0, "shadow": 3.0,
        "mode": "sweep", "pop": False,
    },
    "carnet": {
        "label": "Carnet", "hint": "sur un post-it jaune", "group": "manuscrit",
        "font": "Kalam Bold", "bold": False, "size": 72, "box": True, "box_alpha": 0.0, "outline_col": "#FFE500",
        "color": "#17181C", "hl": "#D82C43", "outline": 12.0, "pop": False,
    },
    # ---- rétro et jeux
    "arcade": {
        "label": "Arcade", "hint": "pixels, jeu vidéo", "group": "retro",
        "font": "Press Start 2P", "bold": False, "size": 54, "upper": True, "hl": "#39FF14", "outline": 5.0,
    },
    "vhs": {
        "label": "VHS", "hint": "cassette, décalage rouge", "group": "retro",
        "font": "VT323", "bold": False, "size": 112, "color": "#E8FFF0", "hl": "#00FFB3", "outline": 0.0,
        "glow": 10.0, "glow_col": "#00FFB3", "shadow": 4.0, "shadow_col": "#FF0055",
    },
    "annees80": {
        "label": "Années 80", "hint": "néon rose", "group": "retro",
        "font": "Monoton", "bold": False, "size": 86, "upper": True, "color": "#FF2BD6", "hl": "#FFFFFF",
        "outline": 0.0, "glow": 20.0, "glow_col": "#FF2BD6",
    },
    "glitch": {
        "label": "Glitch", "hint": "tremble et se décale", "group": "retro",
        "font": "Rubik Glitch", "bold": False, "size": 92, "hl": "#00E5FF", "outline": 4.0, "shadow": 4.0,
        "shadow_col": "#00E5FF", "anim_loop": {"type": "glitch", "speed": 1.0},
    },
    "frisson": {
        "label": "Frisson", "hint": "horreur, vert acide", "group": "retro",
        "font": "Creepster", "bold": False, "size": 100, "color": "#B8FF3C", "hl": "#FFFFFF",
        "outline_col": "#1A0000", "outline": 5.0, "glow": 12.0, "glow_col": "#6BFF00",
    },
    "western": {
        "label": "Western", "hint": "sable et bois", "group": "retro",
        "font": "Alfa Slab One", "bold": False, "size": 84, "upper": True, "color": "#F5D0A9", "hl": "#FFFFFF",
        "outline_col": "#3A1A00", "outline": 5.0, "extrude": 6.0, "extrude_col": "#6B3A1A",
    },
}

# Champs qui décrivent l'apparence (le reste — label/hint/group — est pour l'UI).
LOOK_FIELDS: tuple[str, ...] = tuple(BASE)
# Un style peut aussi animer ses lignes (engine/timeline/animations.py).
ANIM_FIELDS: tuple[str, ...] = ("anim_in", "anim_out", "anim_loop")


def preset_anims(name: str) -> dict:
    """Animations d'un style (entrée, sortie, boucle), s'il en a."""
    p = PRESETS.get(name, {})
    return {k: dict(p[k]) for k in ANIM_FIELDS if isinstance(p.get(k), dict)}


def preset(name: str) -> dict:
    """Preset complet (BASE + surcharges), sans les champs d'UI."""
    p = {**BASE, **PRESETS.get(name, {})}
    return {k: p[k] for k in LOOK_FIELDS}


def catalog() -> list[dict]:
    """Liste envoyée au front : nom, libellé, description + apparence."""
    return [
        {"name": name, "label": p.get("label", name), "hint": p.get("hint", ""),
         "group": p.get("group", "tendance"), "featured": bool(p.get("featured")),
         **preset(name), **preset_anims(name)}
        for name, p in PRESETS.items()
    ]


def groups() -> list[dict]:
    return [{"name": k, "label": v} for k, v in GROUPS.items()]
