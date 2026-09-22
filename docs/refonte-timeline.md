# Refonte : le montage sur timeline

Objectif : monter une vidéo de bout en bout dans Montage IA, sans repasser par
CapCut. Un projet « Montage » a une vraie timeline multipiste : on y importe
ses rushs (fichiers ou dossier entier), on les place, on les coupe, on les
supprime, on sépare le son en un clic, on superpose des pistes audio, et les
outils automatiques (suppression des blancs, tics de langage, sous-titres)
travaillent directement sur cette timeline.

Le mode actuel (« Short automatique » : une vidéo → coupes + sous-titres)
reste disponible pendant toute la refonte.

Branche : `refonte-timeline`.

---

## Principes

- **Tout reste local.** Médias, proxies, transcription, rendu : rien ne quitte
  la machine.
- **Le navigateur monte, ffmpeg rend.** Chaque édition (déplacer, couper,
  diviser, séparer le son…) est instantanée : elle modifie un état JSON dans le
  navigateur, joué en direct par un lecteur multipiste. Seul l'export repasse
  par ffmpeg, en pleine résolution.
- **Proxies.** À l'import, chaque média reçoit une copie légère (≤ 960 px,
  images clés rapprochées pour des déplacements instantanés), une bande de
  vignettes et une forme d'onde. L'export, lui, relit les originaux.
- **Ce qu'on voit est ce qu'on exporte.** Cadrage, position, échelle,
  sous-titres : l'aperçu et le rendu utilisent la même géométrie.
- **Sous-titres liés à la voix.** Un sous-titre automatique garde, mot par
  mot, sa référence dans le média source. Couper, déplacer ou réordonner les
  clips le recale tout seul.
- **Chaque étape est testée.** Les tests pytest couvrent le moteur, `node
  --test` couvre la logique de timeline écrite en JavaScript.

## Modèle de données

Un projet timeline est un `project.json` de type `"kind": "timeline"` :

```text
canvas    { w, h, fps, bg }                        format de sortie (9:16, 16:9, 1:1…)
media[]   { id, name, path, kind, duration, w, h, fps, has_audio,
            status, proxy, thumbs, waveform, transcript }
tracks[]  { id, kind: video|audio|text, name, main, muted, hidden, locked }
clips[]   { id, track, kind: video|audio|image|text, start, dur,
            media, in, speed, volume, muted, detached, fade_in, fade_out,
            x, y, scale, rotation, opacity, fit, link,
            words / style (texte et sous-titres) }
settings  { max_gap, pad, words_per_line, max_chars, style, emojis, … }
```

- `start` et `dur` situent le clip sur la timeline (secondes) ; `in` est le
  point d'entrée dans le média, `speed` la vitesse. Fin dans la source :
  `in + dur × speed`.
- Ordre des pistes = ordre d'affichage, de haut en bas. Une piste vidéo plus
  haute passe devant. La piste principale est magnétique : ses clips restent
  collés, sans trous.
- Deux clips d'une même piste ne se chevauchent jamais.
- `link` regroupe les clips qui bougent ensemble : une vidéo et son son séparé,
  par exemple. On peut les dissocier.

---

## Les étapes

Chaque étape donne une application utilisable et fait l'objet d'un commit.

### 1. Fondations
- [x] Branche, feuille de route.
- [x] Les fichiers de l'interface sont servis en statique (modules JS/CSS
      séparés), et l'installeur les embarque tous.
- [x] Type de projet `timeline` : création, lecture, sauvegarde, liste des
      projets commune aux deux modes.
- [x] Normalisation côté serveur de l'état envoyé par l'éditeur (bornes,
      chevauchements, pistes manquantes).
- [x] Page « Studio » (squelette : médias, lecteur, inspecteur, timeline).

### 2. Médias
- [x] Import de fichiers, d'un dossier entier (glisser-déposer compris) ou
      par chemin local, sans copie.
- [x] Vidéo, audio, image : sonde, proxy, bande de vignettes, forme d'onde,
      avec une file de tâches et la progression par média.
- [x] Panneau Médias : vignettes, durées, état, ajout à la timeline.

### 3. Timeline
- [x] Règle, zoom (Ctrl + molette), défilement, tête de lecture.
- [x] Pistes vidéo, audio et texte : nom, muet, masquée, verrouillée, ajout,
      suppression.
- [x] Clips avec vignettes ou forme d'onde ; glisser depuis les médias.
- [x] Sélection (clic, Ctrl, Maj, rectangle), déplacement entre pistes,
      rognage par les bords, aimantation, piste principale magnétique.
- [x] Diviser (Ctrl+B), supprimer (avec fermeture du trou), dupliquer,
      copier-coller, annuler/rétablir.

### 4. Lecteur
- [x] Lecture temps réel de la timeline : clips vidéo superposés, images,
      pistes audio mixées.
- [x] Défilement image par image, lecture en boucle, raccourcis clavier.
- [x] Format du projet (9:16, 16:9, 1:1, 4:5…), couleur de fond.

### 5. Son et clips
- [ ] Séparer le son d'une vidéo en un clic (clip audio lié).
- [ ] Plusieurs pistes audio : musique, voix off, bruitages.
- [ ] Volume (jusqu'à 200 %), fondus d'entrée et de sortie, muet, vitesse.
- [ ] Transformations à la souris sur l'aperçu : position, échelle,
      rotation, opacité, remplir ou adapter, miroir.

### 6. Outils automatiques dans la timeline
- [ ] Transcription par média (file d'attente, GPU puis processeur).
- [ ] Supprimer les blancs sur la sélection ou toute la piste principale :
      par la voix (transcription) ou par le volume sonore, avec aperçu de ce
      qui sera retiré.
- [ ] Supprimer les tics de langage.
- [ ] Sous-titres automatiques sur une piste texte, liés à la voix.
- [ ] Édition des sous-titres : styles, position, taille, texte, émojis,
      fusion, division, traduction.
- [ ] Textes libres (titres).

### 7. Export
- [ ] Rendu ffmpeg de la timeline complète : pistes vidéo superposées,
      transformations, images, mixage audio, sous-titres et émojis.
- [ ] Réglages : définition, images/s, qualité, codec (H.264 / HEVC).
- [ ] Export du son seul (formats de la boîte à outils).

### 8. Intégration
- [ ] Accueil : choix entre « Montage » et « Short automatique ».
- [ ] « Short automatique » dans la timeline : import → coupes → sous-titres,
      puis retouches à la main.
- [ ] Ouvrir un ancien projet dans la timeline.
- [ ] Documentation, changelog, build de l'application.

### 9. Finitions (seconde vague)
- [ ] Transitions entre clips (fondu, fondu au noir, glissement, zoom).
- [ ] Réglages d'image (luminosité, contraste, saturation, température).
- [ ] Arrière-plan flou pour les formats qui ne remplissent pas le cadre.
- [ ] Arrêt sur image, marqueurs.
- [ ] Son : normalisation du volume, réduction du bruit.
