# models/

Downloaded or converted models live here. Everything in this folder except this
file is git-ignored.

```
models/
  translate/
    opus-mt-fr-en/     # subtitle translation (CTranslate2, ~80 MB)
  hub/                 # optional: Whisper model bundled into the Windows installer
```

Fetch them with:

```bash
python scripts/download_models.py            # translation model
python scripts/download_models.py --whisper  # + Whisper large-v3-turbo (optional)
```

The Whisper model does not need to be here during development: faster-whisper
downloads it to the Hugging Face cache on the first analysis.
