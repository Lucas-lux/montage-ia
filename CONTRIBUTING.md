# Contributing to Montage IA

Thanks for your interest! Bug reports, ideas and pull requests are all welcome.
French or English — both are fine in issues, pull requests and code comments.

## Set up a development environment

Follow the *Quick start* in the [README](README.md), then install the test tools:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

You don't need a GPU: transcription and encoding fall back to the CPU. To test the
editor without waiting for a long transcription, use a short video (10–20 s of
someone talking).

## Before opening a pull request

- `python -m pytest` passes (CI runs it on Linux and Windows).
- If you touched the editor (`engine/web/index.html`) or the render, try the change
  in the app: analyse a short clip, edit, export, and watch the result.
- Keep pull requests focused: one fix or one feature at a time is much easier to
  review.
- Discuss new heavy dependencies (ML frameworks, large models) in an issue first:
  the app has to stay installable on an ordinary PC and runnable offline.

## Code style

- Match the surrounding code: naming, comment density, and French docstrings in
  the existing modules.
- Comments explain *why* (a constraint, a pitfall) rather than restating the code.
- The UI is a single dependency-free HTML file — no build step, no framework.

## Where things live

| You want to… | Look at |
|---|---|
| add a cut tool (e.g. remove repeated takes) | `engine/pipeline/edit.py` — tools return intervals to cut on the source timeline; `Project._collect_cuts` composes them |
| add or tweak a caption style | `engine/pipeline/style_presets.py` (editor and export share it) |
| change what the export burns in | `engine/pipeline/ass_edit.py` and `render.burn_and_overlay` |
| add an API endpoint | `engine/server.py`; state changes go through `engine/project.py` |
| add a translation language | `engine/pipeline/translate.py` + `scripts/download_models.py` |
| change the Windows build | `build/build.py`, `build/montage_ia.spec`, `build/installer.iss` |

Principles worth keeping:

- **Analyse once.** Whisper runs once per video; everything else works on the
  stored words. Cuts are recomputed from words and settings, never stored.
- **The editor is the source of truth for captions.** The server translates the
  captions it receives into libass tags without reinterpreting them, so what the
  editor shows is what gets exported.
- **Nothing leaves the machine.** No telemetry, no cloud API.

## Tests

Tests live in `tests/` and must run without GPU, models or real videos. Use
`monkeypatch` to stand in for heavy parts (Whisper, the translation model). The one
test that needs ffmpeg is marked `@pytest.mark.ffmpeg` and skipped when ffmpeg is
missing.

## Reporting bugs

Use the *Bug report* issue template. The console window of Montage IA prints the
engine log — the relevant lines usually point straight at the problem. Please
remove anything personal (file paths, video names) before posting.
