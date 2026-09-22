# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

First public release.

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
  built-in rules. Per-project options, summary, *Revenir en arrière*.
- Toolbox, usable without a project. First tool: extract a video's audio as
  MP3 (default), AAC, Opus, Ogg Vorbis, WAV or FLAC, with a chosen bitrate or
  bit depth, sample rate and channels (`python -m engine.tools.audio`).
- Windows app and installer (PyInstaller + Inno Setup), bundling ffmpeg, CUDA
  libraries and the translation model.
- `scripts/download_models.py`, test suite and CI.

### Changed
- Hardware is chosen automatically: GPU when usable, CPU otherwise
  (`--device auto`, `--compute-type auto`, `--encoder auto`).

### Fixed
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
