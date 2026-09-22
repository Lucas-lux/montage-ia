"""Mapping mot-clé -> émoji (FR + EN), 100% local, pour mettre en avant le mot
important d'une ligne de sous-titre (un émoji bien choisi, pas un par ligne).

Les clés sont normalisées (minuscules, sans accents, alphanumérique). La
recherche tente le mot exact puis sa forme au singulier (-s).
"""
from __future__ import annotations

import re
import unicodedata

# Clés DÉJÀ normalisées (sans accent). Plusieurs synonymes -> même émoji.
EMOJI_MAP: dict[str, str] = {
    # Argent
    "argent": "💰", "money": "💰", "euro": "💰", "euros": "💰", "dollar": "💰",
    "riche": "🤑", "payer": "💸", "paye": "💸", "prix": "🏷️", "cout": "💸",
    "gratuit": "🆓", "cash": "💵", "fortune": "💰", "business": "💼", "vendre": "🤝",
    # Temps
    "temps": "⏰", "heure": "⏰", "heures": "⏰", "minute": "⏱️", "minutes": "⏱️",
    "jour": "📅", "jours": "📅", "semaine": "📅", "mois": "📅", "annee": "📅",
    "annees": "📅", "maintenant": "⏰", "rapide": "⚡", "vite": "⚡", "attendre": "⏳",
    # Feu / hype
    "feu": "🔥", "fire": "🔥", "chaud": "🔥", "hot": "🔥", "ouf": "🔥", "incroyable": "🤯",
    "fou": "🤯", "dingue": "🤯", "explosion": "💥", "bombe": "💣", "energie": "⚡",
    # Tech / dev
    "code": "💻", "coder": "💻", "coda": "💻", "dev": "💻", "developpeur": "💻",
    "developpeurs": "💻", "developpement": "💻", "programmer": "💻", "ordinateur": "💻",
    "logiciel": "💻", "app": "📱", "application": "📱", "ia": "🤖", "robot": "🤖",
    "internet": "🌐", "site": "🌐", "data": "📊", "donnees": "📊", "algorithme": "🧮",
    # Erreur / problème
    "erreur": "❌", "erreurs": "❌", "faute": "❌", "bug": "🐛", "probleme": "⚠️",
    "problemes": "⚠️", "echec": "❌", "rate": "❌", "danger": "🚨", "attention": "⚠️",
    "stop": "🛑", "non": "🚫", "jamais": "🚫", "interdit": "🚫",
    # Idée / réflexion
    "idee": "💡", "idees": "💡", "astuce": "💡", "conseil": "💡", "truc": "💡",
    "secret": "🤫", "secrets": "🤫", "cerveau": "🧠", "reflechir": "🧠", "penser": "🧠",
    "intelligent": "🧠", "comprendre": "🧠", "apprendre": "📚", "savoir": "🧠",
    # Succès / objectif
    "succes": "🏆", "gagner": "🏆", "gagne": "🏆", "victoire": "🏆", "reussir": "✅",
    "reussite": "✅", "meilleur": "🥇", "premier": "🥇", "top": "🔝", "objectif": "🎯",
    "but": "🎯", "cible": "🎯", "important": "⚠️", "cle": "🔑", "clef": "🔑",
    "parfait": "✨", "magie": "✨", "magique": "✨", "oui": "✅", "bravo": "👏",
    # Croissance
    "croissance": "📈", "augmenter": "📈", "monter": "📈", "plus": "➕", "grandir": "📈",
    "baisse": "📉", "diminuer": "📉", "moins": "➖", "resultat": "📊", "stats": "📊",
    # Corps / gens
    "fort": "💪", "puissant": "💪", "muscle": "💪", "coeur": "❤️", "amour": "❤️",
    "aimer": "❤️", "oeil": "👀", "yeux": "👀", "regarder": "👀", "voir": "👀",
    "main": "🙌", "mains": "🙌", "gens": "👥", "monde": "🌍", "equipe": "👥",
    "personne": "🙋", "client": "🤝", "clients": "🤝", "parler": "🗣️", "dire": "🗣️",
    "ecouter": "👂", "voix": "🗣️", "question": "❓", "reponse": "💬",
    # Émotions
    "rire": "😂", "drole": "😂", "marrant": "😂", "lol": "😂", "peur": "😱",
    "flippant": "😱", "triste": "😢", "pleurer": "😭", "content": "😄", "heureux": "😄",
    "colere": "😡", "enerve": "😤", "choque": "😲", "wow": "🤩",
    # Objets / vie
    "video": "🎬", "film": "🎬", "camera": "🎥", "photo": "📸", "image": "🖼️",
    "telephone": "📱", "mobile": "📱", "email": "📧", "mail": "📧", "message": "💬",
    "maison": "🏠", "immobilier": "🏠", "voiture": "🚗", "manger": "🍔", "bouffe": "🍔",
    "cafe": "☕", "eau": "💧", "soleil": "☀️", "nuit": "🌙", "terre": "🌍",
    "livre": "📚", "ecole": "🎓", "etudier": "🎓", "travail": "💼", "boulot": "💼",
    "job": "💼", "cadeau": "🎁", "musique": "🎵", "diamant": "💎", "roi": "👑",
    "fusee": "🚀", "lancer": "🚀", "boost": "🚀", "eclair": "⚡", "etoile": "⭐",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # retire accents
    return "".join(c for c in s if c.isalnum())


# Élisions françaises : « l'argent » cherche « argent ».
_ELISION = re.compile(r"^(?:[cdjlmnst]|qu|jusqu|lorsqu|puisqu)['’]", re.IGNORECASE)


def emoji_for(word: str) -> str | None:
    n = _norm(_ELISION.sub("", word.strip()))
    if not n:
        return None
    if n in EMOJI_MAP:
        return EMOJI_MAP[n]
    if n.endswith("s") and n[:-1] in EMOJI_MAP:  # pluriel simple
        return EMOJI_MAP[n[:-1]]
    return None
