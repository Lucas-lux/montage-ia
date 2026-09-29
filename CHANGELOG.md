# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **One-click reel** (*Outils IA → Montage automatique → Monter la vidéo*): the
  button now runs the whole short-form method in the engine
  (`engine/agent/reel.py`, `POST /api/agent/{pid}/reel`, a background job the
  studio follows and then reloads): derush by takes (each take re-transcribed
  once; slate words before the first take and outtakes at the end dropped too),
  story and hook, cut with the *Dynamique* rhythm (new default), voice
  processing, captions in a short-form style with one keyword per sentence, and
  scenes planned from what is said — split screen for the hook, count-ups on
  numbers, a paper « ? » page on questions, the strong word on the face or a
  card, a banner over screen recordings, a « comment KEYWORD » card — checked,
  drawn, then fixed where the browser saw text on the face, overlaps, text
  outside the frame or poor contrast. New panel settings: caption style, accent
  colour (scene colours derived from it, with a darker ink when it is too light
  to read), the word to comment, scene look (clean / paper). The panel lists
  every decision (takes dropped and why, scenes, keywords, checks).
- **Agents, short-form method**: the MCP instructions and the `edit_video`
  prompt now follow seven gates, each validated by the user — framing, derush,
  captions, beats and real assets, opening, scenes → final, delivery — with the
  editor's rules (something changes every 2–4 s, the visual lands on its word,
  show the thing instead of labelling it, nothing on the face, readable muted).
- **`derush`**: the rush split into takes on its own measured levels (noise
  floor and voice, never a fixed dB; silences read on peaks, syllable edges on
  RMS, 120 Hz high-pass), take edges on the waveform, no two takes overlapping,
  each take re-transcribed on its own (`transcribe(..., clips=…)`: Whisper on a
  whole file swallows repeated sentences) and a proposal per take — retake,
  false start, take contained in the next one, end said again at the start of
  the next take. `build_edit` segments accept `takes: "2-9,11"`.
- **`build_scenes`**: the cut dressed in scenes starting on words (`"w:word"`,
  `"w:le vrai secret"` for a phrase), with layouts `face`, `split`, `face_top`,
  `face_box`, `full` and `world` (16:9, the speaker as a card moving on the
  words), and items landing on their words: cards, count-ups, stamps, badges,
  check rows, strike-throughs, circles, underlines, screenshots with
  highlights and zooms, browser captures, b-roll playing in the zone (real
  clips), photos, logos, chevrons, glass cards, statements, pills, tags, 3D
  cascades, and a *paper* look (paper, grain, tilted cards, Fraunces italic
  accent, Caveat notes). The plan is checked before anything is drawn (scenes
  on words, full coverage, no still moment over 2.2 s without a `hold`
  reason); zones are HTML pages animated with Web Animations, rendered frame by
  frame (identical frames reused) and cached; the drawn pages are checked in
  the browser (overlaps, text outside the frame, contrast, text on the face);
  captions are placed per layout. `preview(cuts=true)` shows every scene change.
- **Clip zones (`box`)**: a video or image can live in a zone of the frame
  (split screen, face in a rounded window): fill/fit, position and zoom are
  relative to the zone and nothing spills out — in the export (rendered at
  the zone's size, rounded-corner mask) and in the studio preview, moving and
  framing included.
- **Caption keywords**: words marked `k` grow (`kw_scale`), take their own
  colour (`kw`) and can pop in (`kw_pop`), identically in the preview and the
  export; `add_captions(keywords=[…], lexicon={…})`; **Alt + click** on a
  caption word in the studio toggles it. New *Montage court* caption styles:
  Net, Net accent, Encre, Pilule, Bulle.
- **HDR footage** (iPhone HLG, PQ, BT.2020 10-bit) is tone-mapped to SDR BT.709
  in the preview proxy and the export (zscale, or a colour-space fallback).
- Fonts: Inter Bold, Archivo Bold / ExtraBold, Fraunces Black and Black Italic
  (59 bundled); `scripts/fetch_fonts.py --new` fetches only the missing ones,
  italics supported.
- **Optimiser le son** (*Outils IA → Son*, and *Optimisé* in the inspector's
  *Voix* section): the engine measures each rush — voice level, background
  noise and signal-to-noise gap, dynamics, sibilance, timbre (mud, body),
  clipping — and sets the voice processing from it: a gain to a working level
  (so the gate and compressor thresholds fit every take), noise reduction and a
  gate calibrated on the measured noise, compression, de-esser, clarity or
  warmth, declipping (`adeclip`); background music is lowered under the voice
  and the export is normalised to −14 LUFS. On real phone rushes the old
  *Clair* preset lowered the voice/noise gap by 5–7 dB; the measured settings
  raise it by 2–13 dB, with no clipping. The automatic edit and the agents
  (`build_edit`, `auto_edit`, new `optimize_sound` tool) use it by default.
- **AI agents (MCP)**: `MontageIA.exe --mcp` (or `python app.py --mcp`) is a
  Model Context Protocol server, so Claude Code, Codex, Cursor, Claude Desktop…
  can edit a video from scratch with the app's tools: create a project, import
  and transcribe rushes, read numbered sentences (hook score, fluff and false
  starts flagged), get the app's analysis, look at source frames with a grid,
  build the main track sentence by sentence (hook first; silences and filler
  words cut; rhythm cuts and punch-in zooms framed on the face), captions,
  titles (50 styles, 54 fonts, effects, animations), free images (Openverse,
  Wikimedia Commons, Pexels photos and videos with a key) with a numbered
  contact sheet, the agent's own HTML/CSS visuals rendered to transparent PNG
  with the bundled fonts, b-roll and insets, music and the 69 sound effects,
  subject cutout / follow / framing, format change with reframing on the face,
  edits, undo, a low-resolution preview returned as a storyboard image, export,
  and `open_in_app`. 31 tools, an `edit_video` prompt, progress notifications;
  long operations return within 45 s and continue with `wait`. The server is
  stdlib-only and talks to the app's engine (`/api/agent/…`); if the app is not
  running it starts it in the background (hidden window, closed after 30 min of
  inactivity). *Boîte à outils → Agents IA* connects Claude Code or Codex in
  one click, shows the configuration for other agents and keeps an optional
  Pexels key. Guide: `docs/agents-ia.md`.
- **Agents, pro short-form look**: `study_reference` studies a video to match
  (contact sheets of its shots, median shot length, words per second, cuts on
  word starts, loudness, music) and the agent's playbook says how to reproduce
  it; `rhythm="dynamic"` cuts at clause ends and cycles framings (wide, close,
  medium, very close, slow push-ins) on the face tracked shot by shot, with
  the zoom capped by the rush resolution; editorial caption and title styles
  (Instrument Serif, bundled, and DM Serif Display); `search_images` searches
  the whole web through the browser (memes, logos, product shots; transparent
  logos and stickers); `add_visual(animated=true)` captures an HTML/CSS/JS
  animation frame by frame into a transparent video, and visuals use project
  images by id (`src="media:<id>"`); `capture_web` screenshots a page, or
  records it scrolling, in a phone or desktop layout; custom keyframe
  animations (`{type: "custom", kf: […]}`) in the studio, the export and for
  agents; `get_timeline(words=true)` gives each word's timeline time.
- **Card border** for images and videos (*Cadrage → Bordure*, `border` /
  `border_col`): a white frame around a meme, a photo or a capture, kept when
  the clip is tilted or animated.
- Animated GIFs are imported as short silent videos (they play), not as a
  still of their first frame.
- The studio reloads by itself when the project is changed elsewhere (by an
  agent): each save carries the revision it was made on and the engine refuses a
  stale one (409) instead of silently overwriting; the previous state stays in
  `Ctrl+Z`.
- Title styles are shared data (`engine/web/studio/titles.json`), used by the
  studio and by the agents.
- **Fonts**: 54 free fonts bundled (Google Fonts, SIL OFL / Apache-2.0: Anton,
  Bebas Neue, Montserrat, Poppins, Bangers, Luckiest Guy, Permanent Marker,
  Pacifico, Press Start 2P, Monoton, Playfair Display…), declared by `@font-face`
  in the editors and handed to libass (`fontsdir`) at export; a searchable font
  picker with categories, each font drawn in itself.
- **Text effects**: glow, neon, soft or coloured shadow, second outline, 3D
  extrusion, left-to-right gradient, outline only, letter spacing, italic,
  rotation, opacity — as layers of the same line, identical in the preview and
  in the export (checked side by side), with one-click effect presets.
- **Animations** (51) for texts, videos and images: in (fade, slides, zoom, pop,
  bounce, drop, elastic, spin, blur, stretch, typewriter, letter by letter, word
  by word…), out, and loops (pulse, heartbeat, float, sway, shake, blink,
  wobble, glitch, slow zoom, pan, shimmer). One JSON definition read by the
  preview (JavaScript) and the export (Python, checked equal by a test); texts
  are rendered frame by frame in the `.ass`, videos and images through
  per-frame `sendcmd` commands. *Animations* section in the inspector with
  animated cards.
- **Captions**: *Mot à mot* (one word on screen, held until the next one, also
  after cuts); new highlight modes *Apparition* (words appear as they are said)
  and *Mot actif seul* (the others dimmed); 71 caption styles (creators, animated,
  neon, 3D, gradients, handwritten, retro…) and about forty title styles.
- **Sound effects**: a « Sons » tab with a library of 69 effects synthesised by
  the engine (whooshes, pops, notifications, impacts, cartoon, risers, glitch,
  drums, comedy, ambience) — no third-party audio file — to preview and drop on
  the timeline; « Mes sons »: create a sound with the synthesiser (12 engines,
  sliders, random), import a file or record the microphone.
- **Subject and background**: click on a person in the preview to detour them
  frame by frame (MODNet, Apache-2.0, 26 MB, bundled; CPU, ~2× real time),
  then *Supprimer l'arrière-plan*, *Cadrer sur le sujet* (reframe, e.g. 16:9 →
  9:16) and *Suivre le sujet* (the frame follows them). The preview plays a
  transparent VP9 proxy; the export applies the matte at full resolution.
- **Toolbox — Supprimer l'arrière-plan**: image → transparent PNG or JPG on a
  colour; video → transparent WebM, ProRes 4444 MOV (alpha) or MP4 on a colour.
- Windows desktop app: Montage IA opens in its own window (pywebview + Edge
  WebView2) instead of the browser, with no console window. Native file and
  folder dialogs, and files dragged from Explorer arrive with their real paths:
  media are read in place, never copied or uploaded, so imports are instant.
  Closing the window stops the engine (with a confirmation while an export or
  an analysis runs); launching the app again brings its window to the front;
  ffmpeg processes end with the app. The log goes to
  `%LOCALAPPDATA%\MontageIA\logs`. `--browser` / `MONTAGE_IA_BROWSER=1` keeps
  the browser.
- Timeline: *Diviser et garder la droite* (`Q`) and *Diviser et garder la
  gauche* (`W`), as in CapCut — in the toolbar and the clip menu. They act on
  the selection, or on the main-track clip under the playhead; linked audio
  follows and the main track closes the gap.
- Media proxies are decoded and scaled on the NVIDIA GPU (NVDEC + `scale_cuda`)
  when possible, rotated phone videos included — about 3× faster on 4K HEVC
  footage; any refusal falls back to the CPU (`MONTAGE_IA_GPU_PROXY=0` to
  disable).

### Changed
- *Monter la vidéo* now edits on the engine side (the whole method above)
  instead of cutting in the browser; the default rhythm is *Dynamique*, and the
  *Textes à l'écran* option became *Scènes animées*.
- *Short automatique → Dans la timeline* no longer starts the automatic edit on
  its own: imported videos go on the timeline, the *Outils IA* tab opens with a
  note and a highlighted *Monter la vidéo* button, and the edit runs when you
  click it. Several videos can be imported first.

### Fixed
- Subject detection on 4K videos ran out of memory ("Invalid argument"): the
  matte is now computed at 1280 px at most (the model sees 320 px anyway) and
  scaled to the source at export.
- A word starting exactly on a cut was captioned twice (once per clip).
- Rhythm cuts (reframing on long shots) could land inside a word — after a
  clause end measured on the sound, or every N seconds when no sentence ended —
  and that word was then captioned twice; they now fall between two words.
- Cuts clipped the end of some words: Whisper often ends a word before its
  sound has died out (up to 0.3 s at the end of a sentence). Cuts now follow the
  audible end measured on the voice envelope, silence cuts keep a tail after it,
  and a segment asked to end on a word is snapped past its sound.
- In the installed app, the agents' HTML visuals, web image search and page
  captures could fail (« The browser did not start its debugging port ») and
  leave headless Edge processes behind: Edge started from the app relaunches
  itself in another process and the one launched exits at once. The engine now
  waits for the port file or the image written by the real browser, closes it
  through DevTools (`Browser.close`) and, as a last resort, stops what still
  holds its profile folder.
- Removing a filler word could bite into the next word (its margin overlapped
  it), and a short phrase kept between two cuts (« je l'ai fait. ») was
  dropped as a too-short piece; the margin now stops at the neighbouring words
  and a piece that holds a word is never merged away.
- Two instances of the app on the same projects (e.g. one started for an AI
  agent while another ran with a test folder) could overwrite each other's work:
  the instance file is now one per projects folder, and an engine rereads a
  project rewritten on disk by another process instead of saving its stale copy
  over it.
- Regenerating captions left behind the lines that an overlap had moved to
  another text track; they came back on top of the new ones.
- The *Clic* and *Tic* sound effects (30–40 ms) could not be added: too short
  to be prepared as media. Very short sounds are now padded with silence to 0.1 s.
- The preview drew texts larger than the export (libass sizes a font by its
  full height, CSS by its em square: +12 % for Arial, up to +75 % for display
  fonts) and some fonts slightly off their baseline; both now match libass.
- Splitting a clip whose silences had been removed duplicated its removed
  passages (listed twice, and restoring the wrong one could corrupt the clip).
- Audio files dropped on the timeline (or added with *Poser aussi sur la
  timeline*) were placed about 11 days after the start; they now start at 0 on
  a free audio track.

## [0.1.0] - 2026-09-22

First public release: Windows installer and macOS app (Apple Silicon).

### Added
- Local transcription with faster-whisper (large-v3-turbo), word timestamps.
- Automatic cuts: silences (adjustable threshold) and French filler words.
- *Keep this passage*: restore any automatic cut from its timeline marker.
- Browser-based caption editor: move, resize, restyle, multi-select, undo/redo,
  word-by-word or karaoke highlight, emojis.
- Local French → English caption translation (Opus-MT via CTranslate2).
- Project library stored on disk; export cached until the edit changes.
- Command-line pipeline (`python -m engine.cli`).
- Timeline studio: a CapCut-style multitrack editor (video, audio and text
  tracks). Import files, folders or local paths (read in place); proxies,
  thumbnails and waveforms prepared in the background; split, trim, move,
  ripple delete, duplicate, copy/paste, markers, snapping, undo/redo; one-click
  audio detach (linked clips), volume up to 200 %, fades, speed; transforms in
  the preview (move, scale, rotate, mirror, opacity) and image adjustments;
  real-time multitrack playback.
- AI tools on the timeline: per-media transcription queue, silence removal by
  voice or by sound level with a preview, filler words, auto captions that stay
  in sync with the voice after cuts and moves, titles, local translation, and a
  one-click « short automatique ».
- Timeline export: all tracks composited, audio mixed, captions and emojis
  burned in; 720p to 4K, H.264 or HEVC (NVENC when available), or audio only;
  cancellable. Short projects can be opened in the timeline.
- One-click automatic edit (*Outils IA → Montage automatique*): silences, filler
  words, greetings/sign-offs and false starts cut; hook title and cold open;
  rhythm cuts and alternating zooms framed on the face (YuNet); on-screen
  keywords; captions; cleaned voice and −14 LUFS loudness at export; ★ markers
  on the strongest moments with *Isoler*. Uses an optional local language model
  (Qwen3-4B-Instruct int8 via CTranslate2, `download_models.py --llm`) or
  built-in rules. Per-project options, summary, *Revenir en arrière*. The hook
  filmed first is kept as the hook (cold open only on request) and a breath is
  kept after each sentence before a cut.
- Voice-over: record any microphone of the PC on the timeline (level meter,
  countdown, montage playing under the take, other sounds muted), the take
  lands on a « Voix off » track. Voice processing on any clip with speech:
  RNNoise noise reduction, low cut, gate, de-esser, compressor, clarity, warmth,
  constant level, with presets; previewed in the browser, rendered by ffmpeg.
- Toolbox, usable without a project. First tool: extract a video's audio as
  MP3 (default), AAC, Opus, Ogg Vorbis, WAV or FLAC, with a chosen bitrate or
  bit depth, sample rate and channels (`python -m engine.tools.audio`).
- Windows app and installer (PyInstaller + Inno Setup), bundling ffmpeg, CUDA
  libraries and the translation model.
- macOS app for Apple Silicon (`.dmg`): lives in the Dock and the menu bar,
  bundles a static ffmpeg, uses VideoToolbox for exports, replaces Windows
  caption fonts with macOS equivalents; built and exercised end to end on a
  GitHub Mac runner (`build-macos` workflow, `scripts/smoke_test.py`).
- `scripts/download_models.py`, test suite and CI.

### Changed
- Hardware is chosen automatically: GPU when usable, CPU otherwise
  (`--device auto`, `--compute-type auto`, `--encoder auto`).

### Fixed
- Choosing another Whisper model, or downloading the automatic-edit language
  model, failed when large-v3-turbo was already cached (HuggingFace offline).
- Project saves could fail on Windows (« Accès refusé ») when the file was read
  at the same moment; each save now has its own temporary file and retries.
- The app could close without any message while analysing a video: CUDA
  libraries of mismatched versions (cuDNN) made CTranslate2 crash natively.
  cuDNN is now pinned to the version bundled with CTranslate2, the Windows build
  refuses a mismatch, and GPU transcription runs in a separate process — a crash
  there no longer takes the app down and the analysis continues on the CPU.
- Projects whose analysis never finished can be re-analysed from the project list.
- 10-bit sources (common for 4K phone videos) failed to export with NVENC; exports
  are now always 8-bit.
- Exports wider than 4096 px (8K in original format) switch to HEVC NVENC, and the
  bitrate scales with the resolution instead of a fixed 8 Mb/s.
- Exports no longer fail when NVENC can't start (NVIDIA driver too old for the
  bundled ffmpeg): they fall back to libx264.
- Caption timestamps like 59.999 s were written as `0:00:60.00` in the .ass file.
- Colour emojis now render with bitmap-only fonts (Noto Color Emoji, Apple Color Emoji).
- A cached Whisper model other than large-v3-turbo no longer blocks the download
  of the right one; `--with-model` bundles only that model.
