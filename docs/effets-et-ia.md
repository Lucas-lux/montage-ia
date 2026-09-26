# Effets, animations, sons et détourage

Objectif : les effets de texte, animations, sons et outils de sujet qu'on
trouve dans CapCut, dans Montage IA, en local, avec le même principe que le
reste : **ce qu'on voit dans l'aperçu est ce qui sort à l'export**.

Branche : `refonte-timeline`.

---

## Principes

- **Une seule définition, deux moteurs de rendu.** Les animations sont des
  données (images clés + courbes d'accélération) lues à la fois par l'aperçu
  (JavaScript, CSS) et par l'export (Python → ffmpeg et libass). Un test
  vérifie que les deux calculent les mêmes valeurs.
- **Export image par image quand il le faut.** Un texte animé devient, pendant
  son animation, une ligne ASS par image de la vidéo (position, taille,
  rotation, flou, opacité déjà calculés) : pas d'approximation. Une vidéo ou
  une image animée reçoit des expressions ffmpeg évaluées à chaque image.
- **Polices libres, embarquées.** Des polices Google Fonts (licence SIL OFL)
  livrées avec l'application : l'aperçu les charge par `@font-face`, libass
  les lit par `fontsdir`. Même fichier des deux côtés.
- **Sons fabriqués, pas téléchargés.** La bibliothèque d'effets sonores est
  synthétisée par le moteur (aucun fichier sous licence à redistribuer) ; le
  même synthétiseur sert à créer ses propres sons.
- **Détourage local.** MODNet (Apache-2.0, 26 Mo) détoure la personne image
  par image ; un clic choisit le sujet à garder quand il y en a plusieurs.

## Les étapes

### 1. Polices
- [x] Une quarantaine de polices OFL (impact, créateurs, manuscrites, fun,
      élégantes, sobres), leurs licences, un catalogue.
- [x] Servies à l'aperçu (`@font-face`), passées à libass à l'export.
- [x] Sélecteur de police avec aperçu, par catégorie, avec recherche.

### 2. Effets de texte
- [x] Lueur, néon, ombre (couleur, flou), double contour, relief 3D,
      dégradé, contour seul, espacement, italique, rotation, opacité.
- [x] Même rendu dans l'aperçu et à l'export (tests de rendu).

### 3. Sous-titres
- [x] Mode « mot à mot » : un seul mot à l'écran, tenu jusqu'au suivant.
- [x] Nouveaux surlignages : apparition progressive, mots non dits estompés.
- [x] Une soixantaine de styles de sous-titres et de titres.

### 4. Animations
- [x] Moteur d'animation partagé (images clés, courbes, entrée / sortie / boucle).
- [x] Textes : fondu, glissements, zoom, rebond, chute, rotation, flou,
      machine à écrire, lettre à lettre, mot à mot, pulsation, scintillement, secousse…
- [x] Vidéos et images : fondu, glissements, zoom, rebond, rotation, flou,
      zoom lent (Ken Burns), flottement, pulsation…
- [x] Panneau « Animations » : entrée, sortie, boucle, durée, aperçu.

### 5. Effets sonores
- [x] Bibliothèque synthétisée par catégories (transitions, pops, impacts,
      cartoon, tension, glitch, rythme, comédie), écoute, ajout à la timeline.
- [x] « Mes sons » : importer, enregistrer au micro, créer avec le
      synthétiseur (type, hauteur, durée, brillance, écho, inversé).

### 6. Sujet et arrière-plan
- [x] Détourage MODNet image par image, choix du sujet par un clic.
- [x] « Supprimer l'arrière-plan » sur un clip (aperçu et export).
- [x] « Cadrer sur le sujet » (et suivi du sujet).

### 7. Boîte à outils
- [x] Supprimer l'arrière-plan d'une image (PNG transparent) ou d'une vidéo
      (WebM transparent, MOV ProRes 4444, ou fond de couleur).

### 8. Finitions
- [x] Documentation, changelog, licences, build, installation.
