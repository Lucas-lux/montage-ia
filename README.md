# Montage IA

[![tests](https://github.com/Lucas-lux/montage-ia/actions/workflows/tests.yml/badge.svg)](https://github.com/Lucas-lux/montage-ia/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A local-first editor for short-form videos (TikTok, Reels, Shorts).** Drop in a
raw talking-head clip; Montage IA transcribes it, cuts the silences, crops it to
9:16 and gives you animated captions you can edit with the mouse — then exports
the finished video. Everything runs on your computer: no account, no upload, no
API key.

🇫🇷 [Version française](README.fr.md)

---

## Features

- **Automatic cuts** — removes silences (adjustable threshold) and, optionally,
  French filler words (« euh », « du coup », « en fait »…).
- **Keep what matters** — every cut is marked on the timeline; click a marker
  and choose *Garder ce passage* (keep this passage) if the app cut something
  you wanted.
- **Caption editor** — drag, resize and restyle captions directly on the video:
  5 presets, fonts, colours, outline, background box, word-by-word highlight or
  karaoke sweep, one emoji per line, multi-selection, undo/redo.
- **Local translation** — translate French captions to English on your machine
  (Opus-MT model, ~80 MB, runs on CPU in about a second).
- **What you see is what you export** — captions are burned in exactly as they
  look in the editor.
- **Runs anywhere** — uses an NVIDIA GPU when available (transcription and
  NVENC encoding) and falls back to the CPU automatically otherwise.
- **Project library** — every edit is saved on disk; close the app, come back
  later.

> **Status:** early (v0.1). Developed and used on Windows; macOS and Linux work
> from source but are less tested. French is the primary language: the interface
> is in French, and filler-word detection and translation (French → English)
> assume French speech.

## Quick start (from source)

### 1. Prerequisites

| | Windows | macOS | Debian / Ubuntu |
|---|---|---|---|
| **Python 3.10 – 3.12** | `winget install Python.Python.3.12` | `brew install python@3.12` | `sudo apt install python3 python3-venv` |
| **ffmpeg** (with libass) | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| Colour emoji font | built in | built in | `sudo apt install fonts-noto-color-emoji` |

Check that `ffmpeg -version` and `ffprobe -version` work in a new terminal.

### 2. Install

```bash
git clone https://github.com/Lucas-lux/montage-ia.git
cd montage-ia
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-gpu.txt   # optional: NVIDIA GPU (Windows/Linux)
python scripts/download_models.py     # subtitle translation model (~80 MB)
```

### 3. Run

```bash
python app.py        # or start.bat (Windows) / sh start.sh (macOS, Linux)
```

Your browser opens on <http://127.0.0.1:8765>. Keep the terminal open: it is the
engine and its log. Press `Ctrl+C` there to quit.

The **first analysis downloads the Whisper model** (large-v3-turbo, ~1.6 GB) —
once. To fetch it in advance: `python scripts/download_models.py --whisper`.

> **No GPU?** It works, just slower: on a laptop CPU, transcription takes roughly
> 3–4× the video duration with the default model. The command-line tool accepts a
> smaller model (`--model small`) for faster tests.

## Using the editor

1. **New project** — drop a video (or paste a path), pick a caption style and the
   cut settings, then start the analysis. Transcription is the only slow step,
   and it runs once per video.
2. **Edit** — nothing is re-encoded while you edit:
   - drag a caption to move it, drag its corner to resize it, double-click to fix
     the text (timing stays in sync with the voice);
   - click, `Ctrl`+click, `Shift`+click or `Shift`+drag on the timeline to select
     several lines; settings apply to *Toutes* (all lines) or to the *Sélection*;
   - red markers on the timeline show removed passages; click one to keep it;
     the *Passages supprimés* panel recomputes cuts with another silence threshold;
   - *Langue → Traduire en anglais* translates all lines (or the selection);
     *Texte original* brings the transcription back;
   - `Space` play/pause, `←`/`→` navigate, `Ctrl+Z` / `Ctrl+Y` undo/redo,
     `Delete` hide a line, `Ctrl+S` save (it also saves automatically).
3. **Export** — renders the full-resolution video with the captions burned in.
   A finished export is kept; *Réexporter* only runs when you ask for it.

## Command line

For batch use, without the editor:

```bash
python -m engine.cli "my_clip.mp4"            # → my_clip_short.mp4 next to the source
```

| Option | Default | Description |
|---|---|---|
| `-o, --output` | `<source>_short.mp4` | Output file |
| `--model` | `large-v3-turbo` | Whisper model (`small`, `medium`, …) |
| `--device` | `auto` | `auto`, `cuda` or `cpu` |
| `--compute-type` | `auto` | `float16` on GPU, `int8` on CPU |
| `--language` | auto-detected | Spoken language, e.g. `fr` |
| `--max-gap` | `0.5` | Longest silence kept between two words (s) |
| `--pad` | `0.08` | Margin kept around words (s) |
| `--no-silence` | | Don't cut silences |
| `--fillers` | | Also cut French filler words |
| `--no-vertical` | | Keep the original aspect ratio |
| `--encoder` | `auto` | `auto` (NVENC if usable, else libx264), `h264_nvenc`, `libx264` |
| `--words-per-line` | `4` | Words per caption line |
| `--max-chars` | `18` | Max characters per caption line |
| `--style` | `hype` | `classic`, `punch`, `hype`, `neon`, `clean` |
| `--no-subtitles` | | Don't burn captions |
| `--no-emojis` | | No emojis on keywords |

## Windows installer

To produce a standalone app (`MontageIA.exe`, no Python needed) and its installer:

```powershell
pip install -r requirements-build.txt
winget install JRSoftware.InnoSetup
python scripts/download_models.py
python build\build.py                 # add --with-model to bundle Whisper (+1.6 GB)
```

Outputs go to `dist\`: the portable `MontageIA\` folder and `MontageIA-Setup.exe`,
which installs per-user (no admin rights) into `%LOCALAPPDATA%\Programs\MontageIA`.
The bundle includes Python, the engine, the ffmpeg found in your `PATH`, the CUDA
libraries and the translation model. Projects are stored in
`%LOCALAPPDATA%\MontageIA\work`. Other options: `--app-only`, `--no-cuda`, `--clean`.

> The bundled ffmpeg decides which NVIDIA driver NVENC needs (ffmpeg 8.x needs
> driver 570+). Exports fall back to libx264 when NVENC can't start.

## Configuration

| Environment variable | Default | Effect |
|---|---|---|
| `MONTAGE_IA_PORT` | `8765` | First port tried (the next free one is used if busy) |
| `MONTAGE_IA_WORK` | `./work` (installed app: `%LOCALAPPDATA%\MontageIA\work`) | Projects, previews, exports |
| `MONTAGE_IA_TRANSLATE` | `./models/translate` | Translation models folder |
| `HF_HOME` | Hugging Face default cache | Where Whisper is downloaded |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                 # ~120 tests, a few seconds
python -m pytest -m "not ffmpeg" # skip the test that needs ffmpeg
```

The suite needs no GPU, no model and no real video. CI runs it on Linux and
Windows for every pull request.

## How it works

```
video ──ffprobe──▶ info
      ──Whisper──▶ timestamped words ─┬─▶ cuts (silences, fillers, manual) ─▶ kept segments
                                      └─▶ caption lines ─▶ editor (browser)
editor edits = JSON in the browser  ──▶ saved to work/projects/<id>/
export ──ffmpeg──▶ cut + 9:16 crop ─▶ burn .ass captions + colour emoji PNGs ─▶ mp4
```

- **Analyse once, edit for free.** A project stores only the words and the
  settings; cuts are recomputed from them, so captions and cuts can never drift
  apart. Recomputing cuts re-renders only a light preview proxy.
- **The browser is the source of truth.** Each caption carries its position
  (normalised 0–1), size and colours; `engine/pipeline/ass_edit.py` translates
  exactly those values into libass tags, so the preview matches the export.
- **Emojis** are rendered to PNG with the system colour-emoji font and overlaid
  by ffmpeg (libass would draw them in black and white).

## Project layout

```
app.py                     desktop entry point: starts the engine, opens the browser
engine/
  server.py                FastAPI: projects, analyse, recut, translate, export
  project.py               editing state of one video: words, cuts, captions, export
  core.py · cli.py         one-shot pipeline and its command line
  store.py · edl.py        projects on disk · shared data models
  web/index.html           the whole UI (projects, editor, timeline, export)
  pipeline/
    probe.py               ffprobe (handles rotated phone videos)
    transcribe.py          faster-whisper, GPU → CPU fallback
    edit.py                composable cuts: silences, fillers, manual, kept ranges
    captions.py            word clean-up and line grouping (+ fixed .ass for the CLI)
    style_presets.py       caption presets shared by the editor and the renderer
    ass_edit.py            .ass file from the edited captions
    translate.py           local caption translation (CTranslate2 + SentencePiece)
    emoji.py · emoji_overlay.py   keyword → emoji, colour emoji PNGs
    render.py              ffmpeg: cut/concat, preview proxy, burn-in, encoder fallback
scripts/download_models.py fetch the translation (and optionally Whisper) models
build/                     PyInstaller + Inno Setup recipes for the Windows app
tests/                     pytest suite
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Driver does not support the required nvenc API version` | Your NVIDIA driver is older than your ffmpeg needs. Exports now fall back to libx264; update the driver to get GPU encoding back. |
| `cublas64_12.dll is not found` / `libcublas.so.12` | CUDA libraries missing: `pip install -r requirements-gpu.txt`. Transcription falls back to the CPU meanwhile. On Linux, see faster-whisper's notes on `LD_LIBRARY_PATH`. |
| *Traduire en anglais* is greyed out | Run `python scripts/download_models.py`. The button is also disabled when the video isn't in French. |
| Emojis missing in the exported video (Linux) | Install `fonts-noto-color-emoji`. |
| Exported captions use a different font (macOS/Linux) | The editor offers Windows fonts (Arial, Impact…); install them or pick one available on your system. |
| `ffmpeg` not found | Install it (see prerequisites) and open a new terminal. |

## Roadmap

Ideas, not promises — discussion and contributions welcome:

- dynamic reframing that follows the speaker instead of a centre crop;
- scene detection;
- filler words and translation for more languages;
- clip suggestions (best moments, hooks, titles) with a local LLM.

## Contributing

Bug reports, ideas and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Montage IA contributors.
Third-party components and models keep their own licenses — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
