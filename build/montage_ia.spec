# -*- mode: python ; coding: utf-8 -*-
r"""Recette PyInstaller de MontageIA.exe (Windows) et de Montage IA.app (macOS).

Ce fichier n'empaquette que du Python : le moteur, l'interface et les
bibliothèques. Les gros binaires (ffmpeg, DLL CUDA, modèle Whisper) sont copiés
ensuite par `build/build.py` À CÔTÉ de l'exe — les faire passer par l'analyse
de PyInstaller coûterait de longues minutes pour rien, ce sont des blobs, pas
du code à inspecter. `app.py` les retrouve au démarrage via le PATH.

Mode « un dossier » et non « un fichier » : un exe unique de 2 Go se
décompresserait dans un dossier temporaire à chaque lancement.

Sur macOS, le dossier devient une application (BUNDLE) : pas de terminal,
icône dans le Dock et la barre des menus (macapp.py), App Nap désactivé.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
MAC = sys.platform == "darwin"
VERSION = os.environ.get("MONTAGE_IA_VERSION", "0.1.0")

# Toute l'interface : pages, modules JS et feuilles de style du studio, et
# les données du moteur (détecteur de visage YuNet).
datas = [(os.path.join(ROOT, "engine", "web"), "engine/web"),
         (os.path.join(ROOT, "engine", "data"), "engine/data")]
binaries = []
hiddenimports = [
    # Chargés paresseusement dans le moteur : PyInstaller ne peut pas les voir.
    "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
    "engine.server", "engine.project", "engine.store", "engine.pipeline.fonts",
]
if MAC:
    # Dock et barre des menus (importés dans une fonction : invisibles à l'analyse)
    hiddenimports += ["macapp", "AppKit", "Foundation", "objc", "PyObjCTools.AppHelper"]

# uvicorn/fastapi résolvent leurs protocoles par nom au démarrage.
# sentencepiece : tokenizer des modèles de traduction (engine/pipeline/translate.py).
# cv2 : détecteur de visage du montage automatique (engine/timeline/autoedit.py).
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers",
            "huggingface_hub", "uvicorn", "sentencepiece", "cv2"):
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
    # Windows : la fenêtre sert de journal et de bouton « quitter ».
    # macOS : une application sans terminal (journal dans ~/Library/Logs).
    console=not MAC,
    argv_emulation=False,
    icon=os.path.join(SPECPATH, "icon.icns" if MAC else "icon.ico") if os.path.exists(
        os.path.join(SPECPATH, "icon.icns" if MAC else "icon.ico")) else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MontageIA",
)
if MAC:
    app = BUNDLE(
        coll,
        name="Montage IA.app",
        icon=os.path.join(SPECPATH, "icon.icns"),
        bundle_identifier="io.github.lucas-lux.montage-ia",
        version=VERSION,
        info_plist={
            "CFBundleName": "Montage IA",
            "CFBundleDisplayName": "Montage IA",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.video",
            "NSHighResolutionCapable": True,
            # le moteur garde sa vitesse quand le navigateur est au premier plan
            "NSAppSleepDisabled": True,
            "NSHumanReadableCopyright": "MIT — Montage IA",
        },
    )
