# -*- mode: python ; coding: utf-8 -*-
r"""Recette PyInstaller de MontageIA.exe.

Ce fichier n'empaquette que du Python : le moteur, l'interface et les
bibliothèques. Les gros binaires (ffmpeg, DLL CUDA, modèle Whisper) sont copiés
ensuite par `build/build.py` À CÔTÉ de l'exe — les faire passer par l'analyse
de PyInstaller coûterait de longues minutes pour rien, ce sont des blobs, pas
du code à inspecter. `app.py` les retrouve au démarrage via le PATH.

Mode « un dossier » et non « un fichier » : un exe unique de 2 Go se
décompresserait dans un dossier temporaire à chaque lancement.
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "engine", "web", "index.html"), "engine/web")]
binaries = []
hiddenimports = [
    # Chargés paresseusement dans le moteur : PyInstaller ne peut pas les voir.
    "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
    "engine.server", "engine.project", "engine.store",
]

# uvicorn/fastapi résolvent leurs protocoles par nom au démarrage.
# sentencepiece : tokenizer des modèles de traduction (engine/pipeline/translate.py).
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers",
            "huggingface_hub", "uvicorn", "sentencepiece"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # `nvidia` est exclu volontairement : ses DLL sont copiées dans cuda/ par
    # build.py. Le reste n'est jamais importé par l'application.
    excludes=["nvidia", "tkinter", "matplotlib", "scipy", "pandas", "pytest",
              "IPython", "notebook", "torch", "torchaudio", "transformers"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MontageIA",
    debug=False,
    strip=False,
    upx=False,
    console=True,          # la fenêtre sert de journal et de bouton « quitter »
    icon=os.path.join(SPECPATH, "icon.ico") if os.path.exists(
        os.path.join(SPECPATH, "icon.ico")) else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MontageIA",
)
