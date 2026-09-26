# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Fonts**: 53 free fonts bundled (Google Fonts, SIL OFL / Apache-2.0: Anton,
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
- *Short automatique → Dans la timeline* no longer starts the automatic edit on
  its own: imported videos go on the timeline, the *Outils IA* tab opens with a
  note and a highlighted *Monter la vidéo* button, and the edit runs when you
  click it. Several videos can be imported first.

### Fixed
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
