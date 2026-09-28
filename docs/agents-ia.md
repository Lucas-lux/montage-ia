# Monter ses vidéos avec un agent IA (Claude Code, Codex, Cursor…)

Montage IA donne ses outils aux agents IA par le **Model Context Protocol**
(MCP). Tu donnes une vidéo à ton agent ; il la transcrit, choisit l'accroche,
garde les bonnes phrases, coupe les blancs, recadre, pose les sous-titres, les
titres, des images et des visuels, des sons, regarde le résultat et exporte —
avec le moteur de Montage IA, sur ton PC. Le projet est un projet normal du
studio : il s'ouvre dans l'application et se retouche à la main.

---

## Brancher Montage IA à son agent

### Depuis l'application (le plus simple)

*Boîte à outils → Agents IA* : les boutons **Brancher à Claude Code** et
**Brancher à Codex** ajoutent Montage IA à l'agent installé sur le PC. Pour les
autres agents, la configuration à copier est juste en dessous. Redémarre
ensuite l'agent.

### À la main

Le serveur MCP est l'application elle-même, avec l'option `--mcp` :

```
"%LOCALAPPDATA%\Programs\MontageIA\MontageIA.exe" --mcp
```

- **Claude Code** :
  `claude mcp add --scope user montage-ia -- "C:\Users\<toi>\AppData\Local\Programs\MontageIA\MontageIA.exe" --mcp`
- **Codex** : `codex mcp add montage-ia -- "C:\...\MontageIA.exe" --mcp`, ou dans
  `~/.codex/config.toml` :
  ```toml
  [mcp_servers.montage-ia]
  command = 'C:\Users\<toi>\AppData\Local\Programs\MontageIA\MontageIA.exe'
  args = ["--mcp"]
  tool_timeout_sec = 600
  ```
- **Cursor, Claude Desktop, Windsurf, VS Code…** (fichier de configuration MCP) :
  ```json
  { "mcpServers": { "montage-ia": { "command": "C:\\...\\MontageIA.exe", "args": ["--mcp"] } } }
  ```
- **Depuis les sources** : `python app.py --mcp` (même chose avec le Python du `.venv`).

Pas besoin d'ouvrir Montage IA avant : si l'application n'est pas lancée, le
serveur MCP la démarre en arrière-plan (fenêtre cachée). Elle apparaît quand
tu relances Montage IA ou quand l'agent ouvre le projet (`open_in_app`) ; si
personne ne s'en sert pendant 30 minutes, elle se ferme seule.

## Lui demander un montage

Exemples :

> Monte `C:\Vidéos\rush.mp4` en short 9:16 de 45 secondes : une accroche forte,
> les sous-titres « hype », des titres sur les idées clés, des images pour
> illustrer et quelques effets sonores. Exporte en 1080p.

> Reprends le projet « Vlog Tokyo », passe-le en 16:9 cadré sur mon visage et
> remplace les sous-titres par du mot à mot.

> Monte mes rushes de `C:\Vidéos\croque` comme `C:\Vidéos\reference.mp4` :
> même typo, même rythme, mêmes cartes et mèmes.

Avec une vidéo de référence, l'agent l'étudie d'abord (`study_reference`) :
planches de ses plans, durée médiane d'un plan, débit, coupes calées sur les
mots, niveau sonore, musique ou non — puis il reprend ce langage : sous-titres
et titres « editorial » (Instrument Serif, DM Serif Display), rythme
`dynamic`, cartes plein écran, mèmes en cartes inclinées, capture d'écran en
bandeau au-dessus de la personne.

Dans Claude Code, l'invite toute prête `/mcp__montage-ia__edit_video` pose le
cadre (source, format, durée, style).

## La méthode : sept étapes validées une à une

L'agent travaille comme un monteur de vidéos courtes, et s'arrête à chaque
étape pour ton accord (sauf si tu lui demandes la vidéo finie d'un coup : il
passe alors par toutes les étapes et te les résume à la fin).

1. **Cadrage** — projet, import (une vidéo HDR de téléphone est ramenée en
   SDR), transcription ; il regarde le rush (où est le visage, texte déjà
   incrusté, 16:9 à recadrer) et, si tu en donnes une, la vidéo de référence.
2. **Dérush** — `derush` découpe le rush en **prises** sur ses propres silences
   (seuils mesurés sur l'enregistrement, jamais un nombre de dB fixe), borne
   chaque prise sur la forme d'onde (première et dernière syllabe entières),
   empêche deux prises de se chevaucher (pas de demi-mot rejoué à une
   jonction) et retranscrit chaque prise seule : Whisper sur le fichier entier
   avale volontiers une phrase redite. Il propose pour chaque prise : gardée,
   écartée (reprise, faux départ, prise contenue dans la suivante) ou
   raccourcie (sa fin est redite au début de la suivante). L'agent relit tout
   (« ok » avant la prise, ratés, phrase redite autrement…) et te montre la
   liste ; puis `build_edit` monte les prises choisies, dans l'ordre de
   l'histoire, accroche en tête.
3. **Sous-titres** — il te demande le look : *Net* (les mots arrivent avec la
   voix, les mots-clés grossissent), *Net accent* (mots-clés en couleur),
   *Pilule*, *Bulle*, ou ceux de ta référence ; il choisit les mots-clés et
   l'orthographe des noms propres.
4. **Beats et assets** — le tableau des temps forts, une ligne par passage
   (ce qui est dit, ce qui est à l'écran, pourquoi — y compris les passages
   laissés nus) ; les vraies choses d'abord : captures du vrai site, tes
   fichiers, logos et photos réels, chacun avec sa source.
5. **Ouverture** — les 3 premières secondes décident de tout : trois
   ouvertures différentes avec les mêmes mots, il t'en propose une.
6. **Scènes → final** — `build_scenes` habille la coupe (voir plus bas) ; il
   regarde l'aperçu autour de chaque changement de scène, corrige (trois tours
   au plus), puis tu regardes dans le studio avant l'export.
7. **Livraison** — contrôle du fichier, chemin, texte du post et mot-clé du
   commentaire ; il ne publie rien lui-même.

### Les scènes

`build_scenes` découpe la timeline en scènes, chacune commençant sur le mot qui
l'ouvre (`"w:le vrai secret"`), avec une mise en page :

| Mise en page | À l'écran |
|---|---|
| `face` | le visage plein cadre, avec des textes (barrés, entourés, soulignés), une étiquette, une carte d'appel à l'action |
| `split` | écran partagé : une zone graphique en haut, le visage cadré dessous |
| `face_top` | le visage en haut, une page dessous |
| `face_box` | une page pleine, le visage dans une fenêtre arrondie |
| `full` | une page pleine, sans visage (la voix continue) |
| `world` | 16:9 : un décor plein cadre, le visage en carte qui change de place sur les mots |

Dans chaque zone, des éléments qui arrivent sur leur mot : cartes, chiffres qui
défilent, tampons, lignes cochées, barrés, cercles, vraies captures avec
surlignage et zoom, plans de coupe vidéo dans la zone (de vrais clips,
retouchables), logos, photos, flèches… Deux looks : *clean* (couleurs de la
marque, cartes blanches) et *paper* (papier, grain, cartes de travers, accent en
serif italique, notes à la main). `catalog("scenes")` liste tout.

Avant de dessiner quoi que ce soit, le plan est contrôlé : chaque scène
commence sur un mot, les scènes couvrent toute la vidéo, et l'image ne reste
jamais plus de 2,2 s sans que rien ne s'y passe — sauf pause voulue (`hold`,
avec sa raison). Après le rendu, chaque zone est vérifiée dans le navigateur :
textes qui se chevauchent ou sortent du cadre, contraste trop faible, texte
posé sur le visage. Les zones déjà dessinées sont gardées : refaire les scènes
après une correction ne redessine que ce qui a changé.

Dans le studio, tout reste retouchable : la zone graphique est un clip de la
piste « Scènes », le visage vit dans sa fenêtre (champ `box` du clip : le
déplacer ou le zoomer reste dans la fenêtre), et **Alt + clic** sur un mot d'un
sous-titre en fait (ou n'en fait plus) un mot-clé.

## Ce que l'agent sait faire

| Outil | Ce qu'il fait |
|---|---|
| `status`, `list_projects` | trouve ou lance le moteur, dit ce qui est disponible, liste les projets |
| `create_project`, `import_media` | projet au format voulu, import des rushes (lus sur place), d'adresses web ou de résultats de recherche |
| `study_reference` | étudie une vidéo à imiter : planches de plans, rythme (durée des plans, débit, coupes sur les mots), mise en page, son |
| `transcribe`, `get_transcript` | Whisper, phrases numérotées avec leurs temps, score d'accroche, formules creuses et faux départs repérés |
| `analyze` | l'avis de Montage IA : accroche, phrases à retirer, moments forts, mots-clés, position du visage |
| `view_media` | regarde des images d'un rush avec une grille 0..1 (où est la personne, texte déjà incrusté…) |
| `derush` | les prises du rush, découpées sur ses silences mesurés, sans chevauchement, retranscrites une par une, avec une décision proposée par prise (reprise, faux départ, fin redite) |
| `build_edit` | monte la piste principale prise par prise (`takes`) ou phrase par phrase, dans l'ordre choisi (accroche en tête) ; retire blancs et tics sans entamer les mots (coupe après la fin **audible** de chaque mot) ; rythme `dynamic` : coupes en fin de proposition, cadrages qui alternent (large, serré, moyen, très serré, poussées lentes) sur le visage suivi plan par plan, zoom borné par la définition du rush ; voix mesurée et traitée ; sous-titres |
| `build_scenes` | habille la coupe en scènes calées sur les mots (écran partagé, visage habillé, page, fenêtre arrondie, décor 16:9) avec cartes, compteurs, tampons, barrés, captures… ; contrôle du rythme et de la mise en page ; sous-titres placés selon la mise en page |
| `get_timeline` | le montage tel qu'il est ; `words` donne l'instant de chaque mot pour caler visuels et sons au mot près |
| `auto_edit` | le montage automatique du studio en un appel |
| `add_captions` | sous-titres liés à la voix, 78 styles, mot à mot ; mots-clés agrandis et en couleur (`keywords`), orthographe des noms propres (`lexicon`) |
| `add_text` | titres et textes : 50 styles (dont « editorial »), 59 polices, effets, animations du catalogue ou sur mesure (images clés) |
| `search_images` | tout le web (mèmes, logos, captures, produits ; `transparent` pour les logos et stickers détourés) par le navigateur, ou seulement les images libres (Openverse, Wikimedia Commons ; Pexels photos et vidéos avec une clé) ; planche numérotée à regarder |
| `add_visual` | visuel dessiné par l'agent en HTML/CSS/JS (titre éditorial empilé, carte, compteur, liste, bandeau, pixel art…), avec les polices de l'application et les images du projet (`src="media:<id>"`) ; fixe (PNG transparent) ou **animé** (`animated`, capturé image par image en vidéo transparente) |
| `capture_web` | capture d'une page web (site, dépôt GitHub, article) au format mobile ou bureau, fixe ou en défilement comme un enregistrement d'écran |
| `add_media_clip`, `add_audio` | plans de coupe, bandeau en haut (`top_band`), incrustations, cartes avec bordure et inclinaison (les GIF animés jouent), musique, 69 effets sonores |
| `optimize_sound` | mesure le son de chaque rush et règle la voix (niveau, bruit, compression, clarté), musique sous la voix, export à −14 LUFS — déjà fait par `build_edit` et `auto_edit` |
| `subject` | détoure la personne (clic virtuel x, y), supprime l'arrière-plan, suit ou cadre le sujet |
| `set_format` | 9:16, 16:9, 1:1, 4:5… et recadrage sur le visage |
| `update_clips`, `delete_clips`, `cut_range`, `undo` | retouches, et retour en arrière |
| `preview` | rend le montage en petit (même rendu que l'export) et renvoie une planche d'images : l'agent vérifie ce qu'il a fait ; `cuts` montre une image juste avant et juste après chaque changement de scène |
| `export` | MP4 H.264 jusqu'en 4K |
| `open_in_app` | ouvre le projet dans le studio |
| `catalog` | noms des styles, polices, animations, transitions, sons ; mises en page et éléments des scènes |

Deux repères de temps : les **phrases et segments** sont en secondes dans le
rush (temps SOURCE) ; tout le reste — textes, images, sons — en secondes sur
la **timeline**. `build_edit` renvoie ce qui est dit à chaque instant de la
timeline, pour tout placer au bon moment.

## Le studio pendant ce temps

Si le projet est ouvert dans le studio, il se recharge tout seul après chaque
outil de l'agent (message « Montage mis à jour par l'agent IA ») et
**Ctrl+Z** annule la dernière modification de l'agent. Une retouche faite dans
le studio au même moment n'écrase rien : le studio recharge la version de
l'agent et garde la tienne dans Ctrl+Z.

## Images, licences, confidentialité

- Les images **du web** (`source="web"`, par défaut avec les images libres)
  ont une licence inconnue : mèmes, logos et captures de produits relèvent de
  la citation ou de l'illustration, à toi de juger selon l'usage de la vidéo ;
  `source="free"` ne garde que les images sous licence ouverte.
- Les images importées par l'agent gardent leur **crédit** (auteur, licence,
  page d'origine) dans le projet. Openverse et Wikimedia proposent des images
  sous licence Creative Commons ou du domaine public : certaines demandent de
  citer l'auteur (CC BY), d'autres interdisent l'usage commercial (NC) —
  `search_images(license="commercial")` ne garde que les licences qui
  permettent une vidéo monétisée. Les images et vidéos Pexels sont libres de
  droits (licence Pexels).
- Une **clé Pexels** gratuite (pexels.com/api) se colle dans *Boîte à outils →
  Agents IA*, ou dans la variable `PEXELS_API_KEY` du serveur MCP.
- Tes vidéos ne quittent pas ton PC : le moteur les lit sur place. Mais un
  agent **en ligne** (Claude Code, Codex…) envoie à son fournisseur ce qu'il
  lit pour travailler : la transcription, les images de `view_media` et les
  planches de `preview`. La recherche d'images interroge Bing Images (par
  Edge, sans compte), Openverse, Wikimedia ou Pexels avec les mots de la
  recherche ; `capture_web` ouvre la page demandée.

## Comment c'est construit

```
agent (Claude Code, Codex…) ──stdio JSON-RPC──▶ MontageIA.exe --mcp   (engine/agent/mcp.py)
                                                  │ trouve l'application ouverte (instance.json)
                                                  │ ou la lance : MontageIA.exe --background
                                                  ▼
                                   moteur de l'application : /api/agent/…  (engine/agent/api.py)
                                     service.py  outils sur les projets timeline (les mêmes que le studio)
                                     edit.py     coupes, blancs, rythme, zooms, sous-titres (règles de studio/model.js)
                                     images.py   web (Bing par le navigateur), Openverse, Wikimedia, Pexels ; planches
                                     visuals.py  Edge/Chrome sans fenêtre (DevTools) : HTML/CSS -> PNG, animations
                                                 image par image, recherche d'images, captures de pages
                                     reference.py étude d'une vidéo de référence (plans, rythme, son)
                                     scenes.py   scènes : plan calé sur les mots, contrôle du rythme, visage
                                                 dans sa zone (`box`), pose sur la timeline, sous-titres placés
                                     scene_html.py pages HTML animées des zones (Web Animations, compteurs)
                                     preview.py  aperçu basse définition par le chemin de l'export, planches
                                   engine/timeline/takes.py  dérush par prises (niveaux mesurés, décisions)
```

- Le serveur MCP n'utilise que la bibliothèque standard de Python : il démarre
  en une fraction de seconde et ne charge rien de lourd ; le travail se fait
  dans le moteur de l'application, qui garde ses projets, ses files
  (transcription, détourage) et ses modèles en mémoire.
- Chaque outil qui modifie le montage l'enregistre comme une sauvegarde du
  studio (validation par `engine/timeline/model.py`) et fait avancer sa
  **révision** : le studio compare la sienne (`studio/sync.js`) pour recharger,
  et le moteur refuse une sauvegarde faite sur une révision dépassée.
- Les opérations longues (préparation, transcription, détourage, aperçu,
  export) rendent la main au bout de 45 s au plus (certains clients coupent un
  outil à 60 s) ; l'agent appelle `wait` pour la suite. La progression est
  envoyée en `notifications/progress`.
- Les styles de titres sont une donnée partagée (`web/studio/titles.json`) lue
  par le studio et par les agents.
