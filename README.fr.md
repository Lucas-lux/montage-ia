# Montage IA

[![tests](https://github.com/Lucas-lux/montage-ia/actions/workflows/tests.yml/badge.svg)](https://github.com/Lucas-lux/montage-ia/actions/workflows/tests.yml)
[![Licence : MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

**Un outil de montage de vidéos courtes (TikTok, Reels, Shorts) qui tourne en
local.** Tu donnes un rush face caméra ; Montage IA le transcrit, coupe les
blancs, le recadre en 9:16 et te propose des sous-titres animés que tu modifies à
la souris — puis exporte la vidéo finie. Tout se passe sur ton ordinateur : pas de
compte, pas d'envoi en ligne, pas de clé d'API.

🇬🇧 [English version](README.md)

---

## Fonctionnalités

- **Des effets comme dans CapCut** — 53 polices livrées, effets de texte
  (lueur, néon, 3D, dégradé, double contour, ombre douce…), 71 styles de
  sous-titres, sous-titres *mot à mot*, et 51 animations d'entrée, de sortie et
  en boucle pour les textes, vidéos et images. Ce qu'on voit dans l'aperçu est
  ce que l'export rend, image par image.
- **Effets sonores** — une bibliothèque de 69 effets fabriqués sur ton PC, et
  les tiens : créés avec le synthétiseur intégré, importés ou enregistrés.
- **Sujet et arrière-plan** — clique sur toi dans l'aperçu : l'arrière-plan est
  supprimé (MODNet, en local), le clip peut être recadré sur toi ou te suivre.
  La boîte à outils supprime l'arrière-plan de n'importe quelle image ou vidéo
  (PNG, WebM, ProRes 4444 transparents, ou sur une couleur).
- **Une vraie application de bureau sous Windows** — Montage IA s'ouvre dans sa
  propre fenêtre (moteur Edge WebView2 du système), sans navigateur ni console.
  Les rushs importés ou glissés depuis l'Explorateur sont lus là où ils sont :
  rien n'est copié ni envoyé, l'import est immédiat, et les proxies sont décodés
  par la carte NVIDIA quand il y en a une (environ 3× plus vite sur de la 4K de
  téléphone).
- **Studio timeline** — un éditeur multipiste à la CapCut. Importe des clips, des
  images, de la musique ou un dossier entier ; place, divise, rogne et supprime (la
  piste principale referme les trous) ; superpose des vidéos ; sépare le son d'un
  clip en un clic ; ajoute musique, voix off et titres. La suppression des blancs
  et les sous-titres automatiques travaillent directement sur la timeline, et les
  sous-titres restent calés sur la voix après chaque coupe. Export jusqu'en 4K
  (H.264 ou HEVC), ou le mixage audio seul.
- **Coupes automatiques** — supprime les blancs (seuil réglable) et, en option,
  les tics de langage (« euh », « du coup », « en fait »…).
- **Garder ce qui compte** — chaque coupe est repérée sur la timeline ; un clic
  sur le repère puis *Garder ce passage* si l'app a coupé quelque chose d'utile.
- **Éditeur de sous-titres** — déplacer, redimensionner et restyler les
  sous-titres directement sur la vidéo : 5 styles, polices, couleurs, contour,
  fond, surlignage mot à mot ou balayage karaoké, un émoji par ligne, sélection
  multiple, annuler/rétablir.
- **Traduction locale** — traduit les sous-titres français en anglais sur ta
  machine (modèle Opus-MT, ~80 Mo, environ une seconde sur processeur).
- **Ce que tu vois est ce qui est exporté** — les sous-titres sont incrustés
  exactement comme dans l'éditeur.
- **Tourne partout** — utilise le GPU NVIDIA s'il y en a un (transcription et
  encodage NVENC), sinon bascule tout seul sur le processeur.
- **Bibliothèque de projets** — tout est enregistré sur disque ; tu fermes, tu
  reprends plus tard.
- **Boîte à outils** — des utilitaires qui marchent sur n'importe quel fichier,
  sans projet. Premier outil : *Extraire le son* — récupère la bande son d'une
  vidéo en MP3 (par défaut), AAC, Opus, Ogg Vorbis, WAV ou FLAC, au débit ou à
  la profondeur de ton choix.

> **État :** jeune (v0.1). Développé et utilisé sous Windows ; macOS et Linux
> fonctionnent depuis les sources mais sont moins testés. Le français est la
> langue principale : tics de langage et traduction (français → anglais)
> supposent une vidéo en français.

## Démarrage rapide (depuis les sources)

### 1. Prérequis

| | Windows | macOS | Debian / Ubuntu |
|---|---|---|---|
| **Python 3.10 – 3.12** | `winget install Python.Python.3.12` | `brew install python@3.12` | `sudo apt install python3 python3-venv` |
| **ffmpeg** (avec libass) | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| Police émoji couleur | incluse | incluse | `sudo apt install fonts-noto-color-emoji` |

Vérifie que `ffmpeg -version` et `ffprobe -version` répondent dans un nouveau terminal.

### 2. Installation

```bash
git clone https://github.com/Lucas-lux/montage-ia.git
cd montage-ia
python -m venv .venv
# Windows :  .venv\Scripts\activate      macOS/Linux :  source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-gpu.txt   # facultatif : GPU NVIDIA (Windows/Linux)
python scripts/download_models.py     # modèle de traduction des sous-titres (~80 Mo)
```

### 3. Lancement

```bash
python app.py        # ou start.bat (Windows) / sh start.sh (macOS, Linux)
```

Sous Windows, l'application s'ouvre dans sa propre fenêtre (Edge WebView2, déjà
présent sur Windows 10 et 11) ; la fermer quitte. `python app.py --browser` (ou
`MONTAGE_IA_BROWSER=1`) passe par le navigateur. Sous macOS et Linux, le
navigateur s'ouvre sur <http://127.0.0.1:8765>. Dans tous les cas, le terminal
affiche le journal du moteur ; `Ctrl+C` dedans pour quitter.

La **première analyse télécharge le modèle Whisper** (large-v3-turbo, ~1,6 Go) —
une seule fois. Pour le récupérer d'avance : `python scripts/download_models.py --whisper`.

> **Pas de GPU ?** Ça marche, en plus lent : sur un processeur de portable, la
> transcription prend environ 3 à 4 fois la durée de la vidéo avec le modèle par
> défaut. La ligne de commande accepte un modèle plus léger (`--model small`) pour
> tester vite.

## Utiliser le studio (timeline)

*Nouveau projet → Montage* ouvre une timeline vide au format choisi (9:16, 16:9,
1:1, 4:5…). *Short automatique → Dans la timeline* fait de même et pose les vidéos
importées sur la timeline ; rien ne part tant que tu n'as pas vérifié les réglages
et cliqué sur *Monter la vidéo*.

- **Médias** — *Importer* (fichiers), *Dossier* (un dossier entier, glisser-déposer
  compris) ou *Par chemin* (chemins locaux). Dans l'application Windows, tout import
  est lu sur place, sans copie ; dans un navigateur, les fichiers importés sont
  envoyés dans le projet (le navigateur ne peut pas donner leur chemin) et *Par
  chemin* évite la copie. Chaque fichier reçoit en tâche de fond un proxy léger
  (décodé par la carte NVIDIA si possible), une bande de vignettes et une forme
  d'onde.
- **Textes** — l'onglet *Texte* propose une quarantaine de styles de titres
  (animés, néon, 3D, dégradés, manuscrits, rétro…). Dans l'inspecteur : 53
  polices avec recherche, *Effets* (lueur, second contour, 3D, ombre douce,
  dégradé, contour seul, espacement, italique, rotation, opacité, ou en un clic)
  et *Animations* (entrée, sortie, boucle — survole une carte pour la voir).
- **Animations** — les vidéos et images ont la même section *Animations*
  (fondu, glissements, zoom, rebond, tourbillon, flou, zoom lent, panoramique…).
- **Sons** — l'onglet *Sons* range les effets par catégorie : clic pour écouter,
  `+` (ou glisser) pour les poser à la tête de lecture. *Créer un son* ouvre le
  synthétiseur ; *Importer* et le bouton micro remplissent *Mes sons*.
- **Sujet** — sélectionne un clip vidéo ou image, *Sujet → Choisir le sujet*,
  clique sur la personne : après un calcul en tâche de fond (environ deux fois
  la durée du clip sur processeur), tu peux supprimer l'arrière-plan, cadrer le
  clip sur le sujet ou faire suivre le sujet par le cadre.
- **Timeline** — glisse un média sur une piste ou clique sur `+`. Glisse un clip pour
  le déplacer, ses bords pour le rogner, `Ctrl+B` pour diviser, `Q` / `W` pour
  diviser et garder la partie droite / gauche (comme dans CapCut), `Suppr` pour
  supprimer. La piste principale est magnétique ; les autres sont libres. Glisser un
  clip au-dessus de la première piste (ou sous la dernière piste audio) crée une
  piste. `Ctrl`+molette zoome, `N` coupe l'aimantation, `Alt`+clic prend un clip sans
  son son lié.
- **Son** — *Séparer le son* place le son d'une vidéo sur une piste audio, lié à elle
  (ils bougent ensemble ; *Dissocier* les sépare). Volume jusqu'à 200 %, fondus,
  vitesse (hauteur de voix conservée), muet par clip ou par piste.
- **Aperçu** — clique sur un clip dans l'aperçu pour le déplacer, l'agrandir (coins)
  ou le tourner ; l'inspecteur à droite a tous les réglages (remplir/adapter,
  position, opacité, miroir, luminosité/contraste/saturation).
- **Outils IA** — *Supprimer les blancs* sur la sélection, la piste principale ou
  tout le montage, d'après la voix (transcription, tics de langage en option) ou le
  volume sonore, avec un aperçu en rouge avant d'appliquer. *Sous-titres* les génère
  depuis la voix ; ils suivent ensuite coupes et déplacements. Styles, positions,
  émojis, fusion, division et traduction en anglais comme dans l'éditeur short.
- **Export** — définition (720p à 4K), images/s, qualité, H.264 ou HEVC (GPU NVIDIA
  si possible), ou son seul (MP3, AAC, Opus, WAV, FLAC…). Les fichiers vont dans
  `Vidéos\Montage IA` et ne s'écrasent jamais.

Tout est enregistré automatiquement ; `Ctrl+Z` / `Ctrl+Y` annulent et rétablissent
chaque modification.

## Utiliser l'éditeur short

1. **Nouveau projet** — dépose une vidéo (ou colle un chemin), choisis un style de
   sous-titres et les réglages de coupe, puis *Analyser*. La transcription est la
   seule étape lente, et elle n'a lieu qu'une fois par vidéo.
2. **Édition** — rien n'est réencodé pendant que tu modifies :
   - glisser un sous-titre pour le déplacer, son coin pour le redimensionner,
     double-clic pour corriger le texte (le calage sur la voix est conservé) ;
   - clic, `Ctrl`+clic, `Maj`+clic ou `Maj`+glisser sur la timeline pour
     sélectionner plusieurs lignes ; les réglages s'appliquent à *Toutes* les
     lignes ou à la *Sélection* ;
   - les repères rouges de la timeline montrent les passages supprimés ; un clic
     pour en garder un ; le panneau *Passages supprimés* recalcule les coupes
     avec un autre seuil de silence ;
   - *Langue → Traduire en anglais* traduit toutes les lignes (ou la sélection) ;
     *Texte original* remet la transcription ;
   - `Espace` lecture/pause, `←`/`→` navigation, `Ctrl+Z` / `Ctrl+Y`,
     `Suppr` masquer une ligne, `Ctrl+S` enregistrer (sinon c'est automatique).
3. **Export** — rend la vidéo en pleine définition, sous-titres incrustés. Un
   export terminé est conservé ; *Réexporter* ne se lance que si tu le demandes.

## Vidéos acceptées

Tout ce que ffmpeg sait lire (MP4, MOV, MKV… ; H.264, HEVC, VP9, AV1…), du 720p à
la 8K, y compris les vidéos 10 bits des téléphones.

- **Mode 9:16** (par défaut) : la sortie fait toujours 1080×1920, quelle que soit la source.
- **Format d'origine** : la sortie garde la définition de la source. Le débit suit ;
  au-delà de 4096 px (8K), NVENC passe en HEVC.
- Les exports sont en H.264 8 bits (HEVC pour la 8K), pour être lus partout.
- Les vidéos **HDR** (HLG/PQ, iPhone récents) ne sont pas encore converties en SDR :
  les couleurs peuvent paraître délavées. Exporte-les en SDR depuis le téléphone
  en attendant.
- Les grosses sources coûtent surtout du temps : décoder de la 4K/8K est plus lourd,
  et un export 8K sans GPU NVIDIA est lent.

## Ligne de commande

Pour traiter des vidéos sans l'éditeur :

```bash
python -m engine.cli "mon_rush.mp4"            # → mon_rush_short.mp4 à côté de la source
```

| Option | Défaut | Rôle |
|---|---|---|
| `-o, --output` | `<source>_short.mp4` | Fichier de sortie |
| `--model` | `large-v3-turbo` | Modèle Whisper (`small`, `medium`, …) |
| `--device` | `auto` | `auto`, `cuda` ou `cpu` |
| `--compute-type` | `auto` | `float16` sur GPU, `int8` sur processeur |
| `--language` | détectée | Langue parlée, ex. `fr` |
| `--max-gap` | `0.5` | Silence max conservé entre deux mots (s) |
| `--pad` | `0.08` | Marge gardée autour des mots (s) |
| `--no-silence` | | Ne pas couper les blancs |
| `--fillers` | | Couper aussi les tics de langage |
| `--no-vertical` | | Garder le format d'origine |
| `--encoder` | `auto` | `auto` (NVENC s'il marche, sinon libx264), `h264_nvenc`, `libx264` |
| `--words-per-line` | `4` | Mots par ligne de sous-titre |
| `--max-chars` | `18` | Caractères max par ligne |
| `--style` | `hype` | `classic`, `punch`, `hype`, `neon`, `clean` |
| `--no-subtitles` | | Pas de sous-titres incrustés |
| `--no-emojis` | | Pas d'émojis sur les mots-clés |

Les outils de la boîte à outils se lancent aussi en ligne de commande :

```bash
python -m engine.tools.audio "mon_rush.mp4"                 # → mon_rush.mp3, 192 kbit/s
python -m engine.tools.audio "mon_rush.mp4" -f flac -q 24 --rate 48000 --channels mono
```

`-f` vaut `mp3`, `m4a`, `opus`, `ogg`, `wav` ou `flac` ; `-q` est un débit en
kbit/s pour les formats compressés, une profondeur (16 ou 24 bits) pour WAV et
FLAC. Un fichier existant n'est jamais écrasé (`mon_rush (1).mp3`…).

## Installeur Windows

Pour fabriquer l'application autonome (`MontageIA.exe`, sans Python) et son installeur :

```powershell
pip install -r requirements-build.txt
winget install JRSoftware.InnoSetup
python scripts/download_models.py
python build\build.py                 # --with-model pour embarquer Whisper (+1,6 Go)
```

Tout sort dans `dist\` : le dossier portable `MontageIA\` et `MontageIA-Setup.exe`,
qui s'installe sans droits administrateur dans `%LOCALAPPDATA%\Programs\MontageIA`.
L'application embarque Python, le moteur, le ffmpeg trouvé dans le `PATH`, les
bibliothèques CUDA et le modèle de traduction. Les projets vont dans
`%LOCALAPPDATA%\MontageIA\work`. Autres options : `--app-only`, `--no-cuda`, `--clean`.

L'application n'a pas de console : elle ouvre sa propre fenêtre (Edge WebView2 ;
sans lui, elle repasse par le navigateur) et écrit son journal dans
`%LOCALAPPDATA%\MontageIA\logs\montage-ia.log`. La relancer ramène la fenêtre
ouverte au premier plan.

> C'est le ffmpeg embarqué qui fixe le pilote NVIDIA nécessaire à NVENC (ffmpeg 8.x
> demande le pilote 570+). Si NVENC ne démarre pas, l'export passe en libx264.

## Configuration

| Variable d'environnement | Défaut | Effet |
|---|---|---|
| `MONTAGE_IA_PORT` | `8765` | Premier port essayé (le suivant libre sinon) |
| `MONTAGE_IA_WORK` | `./work` (app installée : `%LOCALAPPDATA%\MontageIA\work`) | Projets, aperçus, exports |
| `MONTAGE_IA_TRANSLATE` | `./models/translate` | Dossier des modèles de traduction |
| `HF_HOME` | cache Hugging Face par défaut | Où Whisper est téléchargé |
| `MONTAGE_IA_BROWSER` | absente | `1` : sous Windows, le navigateur au lieu de la fenêtre de l'application |
| `MONTAGE_IA_GPU_PROXY` | absente | `0` : jamais de proxy décodé par la carte graphique |
| `MONTAGE_IA_DEVTOOLS` · `MONTAGE_IA_DEBUG_PORT` | absentes | Fenêtre de l'application : `1` active les outils de développement · port de débogage (Playwright via CDP) |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                 # ~250 tests (Python + JS de la timeline), quelques secondes
python -m pytest -m "not ffmpeg" # sans le test qui demande ffmpeg
```

Les tests n'ont besoin ni de GPU, ni de modèle, ni de vraie vidéo. La CI les lance
sous Linux et Windows à chaque pull request.

## Comment ça marche

```
vidéo ──ffprobe──▶ infos
      ──Whisper──▶ mots horodatés ─┬─▶ coupes (blancs, tics, manuelles) ─▶ segments gardés
                                   └─▶ lignes de sous-titres ─▶ éditeur (navigateur)
retouches = JSON dans le navigateur ──▶ enregistré dans work/projects/<id>/
export ──ffmpeg──▶ coupe + recadrage 9:16 ─▶ sous-titres .ass + émojis PNG couleur ─▶ mp4
```

- **Analyser une fois, éditer gratuitement.** Un projet ne stocke que les mots et
  les réglages ; les coupes s'en redéduisent, donc sous-titres et coupes ne peuvent
  pas se désynchroniser. Recalculer les coupes ne re-rend qu'un aperçu léger.
- **Le navigateur fait foi.** Chaque sous-titre porte sa position (normalisée
  0–1), sa taille et ses couleurs ; `engine/pipeline/ass_edit.py` traduit
  exactement ces valeurs en tags libass : l'aperçu et l'export coïncident.
- **Les émojis** sont rendus en PNG avec la police émoji couleur du système puis
  superposés par ffmpeg (libass les dessinerait en noir et blanc).

## Organisation du code

```
app.py                     point d'entrée : démarre le moteur, affiche l'interface
desktop.py                 fenêtre de l'application Windows (pywebview + WebView2) : boîtes de dialogue, chemins déposés
engine/
  server.py                FastAPI : projets, analyse, recoupe, traduction, export
  project.py               état d'édition d'une vidéo : mots, coupes, sous-titres, export
  core.py · cli.py         pipeline en une passe et sa ligne de commande
  store.py · edl.py        projets sur disque · modèles de données
  web/index.html           accueil, éditeur short et boîte à outils
  web/studio.html          studio timeline (modules dans web/studio/)
    studio/model.js        logique de timeline, pure (testée par node --test)
    studio/desktop.js      vrais chemins des fichiers dans la fenêtre de l'application (import sans copie)
    studio/timeline.js     pistes, clips, gestes · player.js aperçu temps réel
    studio/inspector.js    réglages de clip et de projet · panels.js outils IA, sous-titres
  timeline/
    project.py · model.py  projets timeline sur disque · validation de l'état de l'éditeur
    media.py · jobs.py     proxies, vignettes, formes d'onde · files de tâches
    ai.py                  transcription, silences, sous-titres depuis la timeline
    render.py              export : pistes, transformations, mixage, sous-titres
    convert.py             ouvrir un projet short dans la timeline
  pipeline/
    probe.py               ffprobe (gère les vidéos de téléphone pivotées)
    transcribe.py          faster-whisper, repli GPU → processeur
    edit.py                coupes composables : blancs, tics, manuelles, plages gardées
    captions.py            nettoyage des mots et découpage en lignes (+ .ass figé de la CLI)
    style_presets.py       styles partagés par l'éditeur et le rendu
    ass_edit.py            fichier .ass à partir des sous-titres édités
    translate.py           traduction locale (CTranslate2 + SentencePiece)
    emoji.py · emoji_overlay.py   mot-clé → émoji, PNG couleur
    render.py              ffmpeg : coupe/concat, aperçu, incrustation, repli d'encodeur
  tools/
    audio.py               boîte à outils : extraire le son d'une vidéo (aussi en CLI)
scripts/download_models.py récupère le modèle de traduction (et Whisper en option)
build/                     recettes PyInstaller + Inno Setup de l'application Windows
tests/                     suite pytest (+ tests/js, lancés par node --test)
```

## Dépannage

| Symptôme | Solution |
|---|---|
| `Driver does not support the required nvenc API version` | Ton pilote NVIDIA est plus ancien que ce que demande ton ffmpeg. L'export passe désormais en libx264 ; mets à jour le pilote pour retrouver l'encodage GPU. |
| `cublas64_12.dll is not found` / `libcublas.so.12` | Bibliothèques CUDA absentes : `pip install -r requirements-gpu.txt`. En attendant, la transcription se fait sur processeur. Sous Linux, voir la note de faster-whisper sur `LD_LIBRARY_PATH`. |
| *Traduire en anglais* est grisé | Lance `python scripts/download_models.py`. Le bouton est aussi désactivé si la vidéo n'est pas en français. |
| Émojis absents de la vidéo exportée (Linux) | Installe `fonts-noto-color-emoji`. |
| Police différente à l'export (macOS/Linux) | L'éditeur propose des polices Windows (Arial, Impact…) ; installe-les ou choisis-en une présente sur ton système. |
| `ffmpeg` introuvable | Installe-le (voir prérequis) et ouvre un nouveau terminal. |
| Un projet affiche *Analyse non terminée* | L'analyse a été interrompue (app fermée, plantage). Clique sur la carte pour la relancer : la vidéo est déjà importée. |

## Pistes

Des idées, pas des promesses — discussions et contributions bienvenues :

- recadrage dynamique qui suit la personne au lieu d'un recadrage centré ;
- détection de scènes ;
- tics de langage et traduction pour d'autres langues ;
- suggestions de clips (meilleurs moments, accroches, titres) avec un LLM local.

## Contribuer

Signalements de bugs, idées et pull requests sont les bienvenus — voir
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[MIT](LICENSE) © Montage IA contributors.
Les composants et modèles tiers gardent leur propre licence — voir
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
