"""Modèle de langage local (Qwen3, via CTranslate2) pour le montage automatique.

Même moteur que Whisper et la traduction : aucun service en ligne, aucune clé.
Le modèle (~4 Go, Apache 2.0) est rangé dans le cache HuggingFace, comme
Whisper, et téléchargé une fois par `scripts/download_models.py --llm` ou depuis
l'application. Sans lui, le montage automatique marche quand même, avec des
règles simples à la place de l'IA pour le choix du hook et des textes.

Un seul modèle chargé à la fois, et déchargé après quelques minutes sans
usage : la mémoire de la carte graphique sert aussi à Whisper et à l'export.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

# Qwen3-4B-Instruct-2507 converti en int8 pour CTranslate2 (jncraton, Apache 2.0).
REPO = "jncraton/Qwen3-4B-Instruct-2507-ct2-int8"
REPO_LIGHT = "jncraton/Qwen3-1.7B-ct2-int8"
_FILES = ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"]

_lock = threading.Lock()
_state: dict = {"gen": None, "tok": None, "path": "", "used": 0.0, "device": ""}
IDLE_UNLOAD = 300.0          # s sans usage avant de libérer la mémoire


def _env_repo() -> str:
    return os.environ.get("MONTAGE_IA_LLM") or REPO


def model_path(repo: str | None = None) -> str | None:
    """Dossier du modèle s'il est déjà téléchargé (cache HuggingFace), sinon None."""
    from huggingface_hub import snapshot_download
    try:
        path = snapshot_download(repo or _env_repo(), local_files_only=True, allow_patterns=_FILES)
    except Exception:  # noqa: BLE001 - absent du cache, ou cache inaccessible
        return None
    return path if all(os.path.isfile(os.path.join(path, f)) for f in ("model.bin", "tokenizer.json")) else None


def available() -> bool:
    return model_path() is not None


def download(repo: str | None = None, progress=None) -> str:
    """Télécharge le modèle dans le cache HuggingFace (une fois).

    L'application passe HuggingFace hors-ligne quand Whisper est déjà là
    (`HF_HUB_OFFLINE`, voir app.py) : le téléchargement demandé ici doit
    passer quand même."""
    from huggingface_hub import constants, snapshot_download
    offline = constants.HF_HUB_OFFLINE
    constants.HF_HUB_OFFLINE = False
    try:
        return snapshot_download(repo or _env_repo(), allow_patterns=_FILES)
    finally:
        constants.HF_HUB_OFFLINE = offline


def _load() -> tuple:
    import ctranslate2
    from tokenizers import Tokenizer
    from engine.pipeline.transcribe import _register_cuda_dll_dirs, resolve_device

    path = model_path()
    if not path:
        raise RuntimeError("Modèle de langage absent : lance `python scripts/download_models.py --llm`.")
    with _lock:
        if _state["gen"] is not None and _state["path"] == path:
            _state["used"] = time.monotonic()
            return _state["gen"], _state["tok"]
        _register_cuda_dll_dirs()
        device = resolve_device("auto")
        compute = "int8_float16" if device == "cuda" else "int8"
        try:
            gen = ctranslate2.Generator(path, device=device, compute_type=compute)
        except Exception:  # noqa: BLE001 - GPU trop petit ou DLL manquante : processeur
            device, compute = "cpu", "int8"
            gen = ctranslate2.Generator(path, device=device, compute_type=compute)
        tok = Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
        _state.update(gen=gen, tok=tok, path=path, used=time.monotonic(), device=device)
        threading.Thread(target=_unloader, daemon=True).start()
        return gen, tok


def _unloader() -> None:
    while True:
        time.sleep(30)
        with _lock:
            if _state["gen"] is None:
                return
            if time.monotonic() - _state["used"] > IDLE_UNLOAD:
                _state.update(gen=None, tok=None, path="")
                return


def unload() -> None:
    with _lock:
        _state.update(gen=None, tok=None, path="")


def chat(system: str, user: str, max_tokens: int = 900) -> str:
    """Une réponse du modèle (format ChatML de Qwen), sans réflexion affichée."""
    gen, tok = _load()
    prompt = (f"<|im_start|>system\n{system}<|im_end|>\n"
              f"<|im_start|>user\n{user}<|im_end|>\n"
              f"<|im_start|>assistant\n")
    ids = tok.encode(prompt, add_special_tokens=False).ids
    tokens = [tok.id_to_token(i) for i in ids]
    with _lock:
        _state["used"] = time.monotonic()
        res = gen.generate_batch([tokens], max_length=max_tokens, sampling_topk=1,
                                 include_prompt_in_result=False, end_token="<|im_end|>",
                                 repetition_penalty=1.05)
    out_ids = [tok.token_to_id(t) for t in res[0].sequences[0]]
    text = tok.decode([i for i in out_ids if i is not None], skip_special_tokens=True)
    # Qwen3 hybride : retire une éventuelle réflexion <think>…</think>
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def chat_json(system: str, user: str, max_tokens: int = 900) -> dict | None:
    """Comme `chat`, en attendant un objet JSON ; None si le modèle divague."""
    text = chat(system, user, max_tokens)
    return extract_json(text)


def extract_json(text: str) -> dict | None:
    """Premier objet JSON complet trouvé dans `text` (les modèles emballent
    parfois leur réponse de ```json … ```)."""
    text = text.replace("```json", "```").replace("```", "")
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def info() -> dict:
    return {"repo": _env_repo(), "available": available(), "loaded": _state["gen"] is not None,
            "device": _state["device"]}
