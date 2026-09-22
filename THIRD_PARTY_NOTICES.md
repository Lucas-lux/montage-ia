# Third-party notices

Montage IA's own code is released under the [MIT license](LICENSE). It relies on
the components below, which keep their own licenses.

## Python dependencies (installed with pip)

| Component | License |
|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT |
| [SentencePiece](https://github.com/google/sentencepiece) | Apache-2.0 |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU (HPND) |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0 |
| [tokenizers](https://github.com/huggingface/tokenizers) | Apache-2.0 |
| [OpenCV](https://github.com/opencv/opencv) (`opencv-python-headless`) | Apache-2.0 |

## Models (downloaded, not stored in this repository)

| Model | Used for | License |
|---|---|---|
| OpenAI Whisper large-v3-turbo, CTranslate2 conversion [`mobiuslabsgmbh/faster-whisper-large-v3-turbo`](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo) | transcription | MIT |
| [Opus-MT fr-en](https://huggingface.co/Helsinki-NLP/opus-mt-fr-en) by Helsinki-NLP (University of Helsinki), CTranslate2 conversion [`michaelfeil/ct2fast-opus-mt-fr-en`](https://huggingface.co/michaelfeil/ct2fast-opus-mt-fr-en) | caption translation | Apache-2.0 |
| [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) by Alibaba Cloud, CTranslate2 int8 conversion [`jncraton/Qwen3-4B-Instruct-2507-ct2-int8`](https://huggingface.co/jncraton/Qwen3-4B-Instruct-2507-ct2-int8) | automatic edit (hook, cuts, on-screen texts) | Apache-2.0 |

## Data stored in this repository

| File | Origin | License |
|---|---|---|
| `engine/data/face_detection_yunet_2023mar.onnx` | [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) face detector, from the OpenCV Zoo (Shiqi Yu et al.) | Apache-2.0 |
| `engine/data/rnnoise_sh.rnnn` | RNNoise model « somnolent-hogwash » from [rnnoise-models](https://github.com/GregorR/rnnoise-models) (Gregor Richards), used by ffmpeg's `arnndn` filter | Declared by its author as not subject to copyright ("none of this work is creative and thus none of it is subject to copyright") |

## External tools

| Tool | License | Notes |
|---|---|---|
| [FFmpeg](https://ffmpeg.org/) | LGPL-2.1+ or GPL-2.0+/GPL-3.0, depending on the build | Not included in this repository; you install it yourself. |
| Colour emoji fonts (Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji) | Their vendors' licenses | Used from the operating system, never redistributed. Emojis burned into your videos come from your system font. |

## The Windows installer built by `build/build.py`

The installer bundles more than this repository's code. **If you distribute it, you
take on the obligations of what it contains:**

- **FFmpeg** — `build.py` copies the `ffmpeg`/`ffprobe` found in your `PATH`. Common
  Windows builds (gyan.dev "full", BtbN GPL) are **GPL-3.0**: distributing them
  requires providing the corresponding source code or a written offer, per the GPL.
  Prefer an LGPL build if that is a concern.
- **NVIDIA CUDA libraries** (cuBLAS, cuDNN, CUDA runtime) — redistributed under the
  NVIDIA license terms shipped with the `nvidia-*` pip packages. Build with
  `--no-cuda` to leave them out.
- **Python and the pip packages above**, frozen by PyInstaller (GPL-2.0 with an
  exception that allows distributing the resulting executable under any license).
- **The Opus-MT model** (Apache-2.0, keep this notice) and, with `--with-model`,
  **Whisper** (MIT).
- The installer itself is compiled with [Inno Setup](https://jrsoftware.org/isinfo.php)
  (Inno Setup license, which allows distributing the installers you create).
