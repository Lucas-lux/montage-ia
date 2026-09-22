# Montage IA — éditeur de vidéos courtes qui tourne en local

**Monter un short TikTok / Reels / YouTube en quelques minutes, sans cloud ni abonnement.**
On dépose un rush face caméra : l'application le transcrit, coupe les blancs, recadre en 9:16
et propose des sous-titres animés qu'on retouche à la souris, puis exporte la vidéo finie.
Tout reste sur l'ordinateur : pas de compte, pas d'envoi en ligne, pas de clé d'API.

🔗 **Code source :** [github.com/Lucas-lux/montage-ia](https://github.com/Lucas-lux/montage-ia) · open source (MIT)

| | |
|---|---|
| **Type** | Projet personnel, application de bureau open source |
| **Plateformes** | Windows (installeur autonome), macOS et Linux depuis les sources |
| **Stack** | Python · FastAPI · faster-whisper / CTranslate2 · ffmpeg / libass · HTML/CSS/JS sans framework |
| **IA locale** | Whisper large-v3-turbo (transcription), Opus-MT (traduction) |
| **Qualité** | 120 tests automatisés, CI sur Linux et Windows |

---

## Le problème

Les outils qui montent automatiquement des vidéos courtes sont presque tous en ligne et payants.
Il faut y envoyer ses rushs, et ils limitent la personnalisation des sous-titres. Je voulais
l'inverse : un outil qui fait le travail répétitif (couper les silences, sous-titrer) en
quelques secondes, sur ma propre machine, en laissant le contrôle total sur le rendu final.

## Ce que fait l'application

- **Transcription locale au mot près** avec Whisper, sur GPU NVIDIA ou sur processeur.
- **Coupes automatiques** des silences (seuil réglable) et des tics de langage (« euh », « du coup »…).
  Chaque coupe est repérée sur la timeline et peut être annulée d'un clic.
- **Éditeur de sous-titres dans le navigateur** : déplacer, redimensionner, restyler directement
  sur la vidéo, avec 5 styles, un surlignage mot à mot ou en karaoké, des émojis, la sélection
  multiple et l'annulation.
- **Traduction français → anglais hors ligne** des sous-titres, en environ une seconde.
- **Export fidèle** : la vidéo finale est identique à l'aperçu de l'éditeur.
- **Bibliothèque de projets** enregistrée sur disque, ligne de commande pour le traitement par lots,
  et installeur Windows sans droits administrateur.

## Points techniques marquants

**Analyser une fois, éditer gratuitement.**
La transcription, seule étape coûteuse, n'a lieu qu'une fois par vidéo. Un projet ne stocke que
les mots horodatés et les réglages ; les coupes sont recalculées à partir d'eux, donc sous-titres
et montage ne peuvent jamais se désynchroniser. Pendant l'édition, rien n'est réencodé : les
sous-titres sont dessinés en HTML par-dessus un aperçu léger, et seul l'export final relance ffmpeg.

**Un rendu WYSIWYG entre le navigateur et ffmpeg.**
Chaque sous-titre porte sa position normalisée, sa taille et ses couleurs. Le serveur traduit
exactement ces valeurs en balises libass, à partir des mêmes styles que l'éditeur. libass ne
sachant dessiner les émojis qu'en noir et blanc, ceux-ci sont rendus en images couleur puis
superposés par ffmpeg dans la même passe d'encodage.

**Traduire des sous-titres de quatre mots sans perdre le sens.**
Traduire ligne par ligne produit du charabia. Les lignes sont donc regroupées en phrases, traduites
d'un bloc par un modèle Opus-MT exécuté avec CTranslate2, puis les mots traduits sont répartis sur
les lignes d'origine au prorata de leur longueur et recalés sur leurs horaires. Le texte original
est conservé : la traduction est réversible, et elle se refait seule quand le montage change.
Résultat : 102 lignes traduites en 0,7 s sur processeur.

**Fonctionner sur n'importe quel PC.**
Le matériel est détecté automatiquement, avec un repli à chaque étape. Si le GPU échoue pendant la
transcription (bibliothèques CUDA absentes, mémoire saturée), elle reprend sur le processeur.
L'encodage matériel NVENC est testé sur une image avant l'export : ffmpeg l'annonce comme disponible
même quand le pilote est trop ancien, et l'échec n'apparaissait qu'à la fin. Un vrai bug rencontré
sur ma machine, corrigé en basculant alors sur l'encodeur logiciel.

**Distribuer une application IA de 2 Go.**
L'installeur Windows (PyInstaller + Inno Setup) embarque Python, le moteur, ffmpeg, les
bibliothèques CUDA et le modèle de traduction. L'utilisateur n'a rien d'autre à installer, et
les projets survivent aux mises à jour.

## Qualité et open source

- **120 tests pytest** qui tournent sans GPU, sans modèle et sans vraie vidéo, grâce à des doublures
  pour les parties lourdes, plus un test d'intégration ffmpeg de bout en bout.
- **Intégration continue** GitHub Actions sur Linux (Python 3.10 et 3.12) et Windows.
- **Prêt pour les contributeurs** : installation en quatre commandes, script de téléchargement des
  modèles, documentation en anglais et en français, guide de contribution, inventaire des licences
  tierces.

## Stack détaillée

| Domaine | Technologies |
|---|---|
| Moteur | Python 3.10–3.12, FastAPI, Uvicorn, Pydantic |
| IA | faster-whisper (Whisper large-v3-turbo), CTranslate2, SentencePiece, Opus-MT |
| Vidéo | ffmpeg (trim/concat, recadrage, NVENC / libx264), libass, Pillow |
| Interface | HTML, CSS et JavaScript natifs, dans un seul fichier, sans étape de build |
| Distribution | PyInstaller, Inno Setup |
| Qualité | pytest, GitHub Actions |
