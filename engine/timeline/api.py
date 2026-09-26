"""Routes HTTP des projets timeline (`/api/timeline/...`).

Branchées par `engine.server`, qui leur fournit le dossier de travail via
`configure` : les tests déplacent ce dossier en cours de route.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from typing import Callable

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from engine.pipeline.translate import available as translate_available
from engine.pipeline.translate import translate_captions
from engine.pipeline import llm
from engine.timeline import ai, autoedit, jobs
from engine.timeline import media as mediatools
from engine.timeline import model
from engine.timeline.project import TimelineProject
from engine.tools import sfx

router = APIRouter()

# Cache des projets ouverts ; la vérité est sur disque.
TIMELINES: dict[str, TimelineProject] = {}
_work_dir: Callable[[], str] = lambda: ""   # noqa: E731 - remplacé par configure()


def configure(work_dir: Callable[[], str]) -> None:
    global _work_dir
    _work_dir = work_dir


def get(pid: str) -> TimelineProject:
    """Projet en cache, sinon relu depuis le disque."""
    proj = TIMELINES.get(pid)
    if proj is None or proj.work_dir != _work_dir():
        proj = TimelineProject.load(_work_dir(), pid)
        if proj is None:
            raise HTTPException(404, "Montage introuvable.")
        TIMELINES[pid] = proj
    return proj


def forget(pid: str) -> None:
    """Avant suppression du projet : refuse si un export tourne, sinon oublie-le."""
    proj = TIMELINES.get(pid)
    if proj is None:
        return
    if proj.is_busy:
        raise HTTPException(409, "Une opération est en cours sur ce projet.")
    proj.deleted = True
    TIMELINES.pop(pid, None)


@router.get("/api/timeline/presets")
def presets() -> dict:
    return {
        "canvas": [{"name": k, "w": w, "h": h} for k, (w, h) in model.CANVAS_PRESETS.items()],
        "fps": list(model.FPS_CHOICES),
        "default": model.DEFAULT_CANVAS,
    }


@router.post("/api/timeline")
def create(body: dict = Body(default={})) -> dict:
    """Nouveau montage vide, au format demandé (9:16 par défaut)."""
    preset = str(body.get("canvas") or model.DEFAULT_CANVAS)
    if preset not in model.CANVAS_PRESETS:
        raise HTTPException(400, f"Format inconnu : {preset}")
    proj = TimelineProject.create(_work_dir(), str(body.get("name") or ""), preset)
    TIMELINES[proj.id] = proj
    return {"id": proj.id}


@router.get("/api/timeline/{pid}")
def state(pid: str) -> dict:
    return get(pid).to_dict()


@router.post("/api/timeline/{pid}/save")
def save(pid: str, body: dict = Body(...)) -> dict:
    """Sauvegarde automatique de l'éditeur : pistes, clips, format, réglages, nom."""
    proj = get(pid)
    proj.apply(body)
    return {"ok": True, "updated": proj.state["updated"],
            "duration": model.duration(proj.state["clips"])}


# ------------------------------------------------------------------- médias

MAX_IMPORT = 500          # fichiers par import de dossier


def _safe_name(name: str) -> str:
    """Nom de fichier utilisable sous Windows (le chemin envoyé est ignoré)."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")[:180] or "media"


def _view(proj: TimelineProject, entry: dict) -> dict:
    return proj._media_view(copy.deepcopy(entry))


@router.get("/api/timeline/{pid}/media")
def media_list(pid: str) -> dict:
    """État des médias (interrogé par l'éditeur tant qu'une préparation tourne)."""
    proj = get(pid)
    return {"media": proj.media_views(), "busy": proj.media_busy}


@router.post("/api/timeline/{pid}/media/upload")
async def media_upload(pid: str, request: Request, name: str = Query(...)) -> dict:
    """Téléverse UN fichier, envoyé tel quel dans le corps de la requête.

    Pas de formulaire multipart : le fichier est écrit une seule fois, au fil
    de l'envoi, directement à sa place (un rush de plusieurs Go ne transite
    pas par un fichier temporaire).
    """
    proj = get(pid)
    clean = _safe_name(name)
    if not mediatools.is_media(clean):
        raise HTTPException(400, f"Type de fichier non pris en charge : {clean}")
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "source" + os.path.splitext(clean)[1].lower())
    part = dest + ".part"
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if os.path.getsize(part) == 0:
            raise ValueError("fichier vide")
        os.replace(part, dest)
    except Exception as exc:  # noqa: BLE001 - envoi coupé, disque plein…
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"Envoi de {clean} interrompu ({exc}).") from exc
    return _view(proj, proj.add_media(dest, name=clean, copied=True, mid=mid))


@router.post("/api/timeline/{pid}/media/record")
async def media_record(pid: str, request: Request, name: str = Query("Voix off")) -> dict:
    """Voix off enregistrée dans l'éditeur (webm/opus ou wav du navigateur) :
    convertie en wav 48 kHz mono, puis ajoutée aux médias comme un fichier."""
    proj = get(pid)
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    raw = os.path.join(folder, "recording.bin")
    dest = os.path.join(folder, "source.wav")
    try:
        with open(raw, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if os.path.getsize(raw) < 100:
            raise ValueError("enregistrement vide")
        res = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw, "-vn",
                              "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", dest],
                             capture_output=True, text=True, timeout=600)
        if res.returncode != 0 or not os.path.isfile(dest):
            raise ValueError((res.stderr or "conversion impossible").strip()[-200:])
    except Exception as exc:  # noqa: BLE001 - envoi coupé, ffmpeg absent…
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"Voix off impossible ({exc}).") from exc
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    clean = _safe_name(name).strip() or "Voix off"
    if not clean.lower().endswith(".wav"):
        clean += ".wav"
    return _view(proj, proj.add_media(dest, name=clean, copied=True, mid=mid))


@router.post("/api/timeline/{pid}/media/paths")
def media_paths(pid: str, body: dict = Body(...)) -> dict:
    """Ajoute des fichiers ou des dossiers LOCAUX, lus sur place (aucune copie)."""
    proj = get(pid)
    raw = body.get("paths")
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise HTTPException(400, "`paths` doit être une liste de chemins.")
    recursive = bool(body.get("recursive"))
    known = {os.path.normcase(os.path.abspath(m["path"])) for m in proj.state["media"]}
    added: list[dict] = []
    skipped: list[dict] = []
    for item in raw:
        path = str(item or "").strip().strip('"').strip()
        if not path:
            continue
        if os.path.isdir(path):
            files = mediatools.scan_folder(path, recursive)
            if not files:
                skipped.append({"path": path, "reason": "aucun média dans ce dossier"})
        elif os.path.isfile(path):
            files = [path] if mediatools.is_media(path) else []
            if not files:
                skipped.append({"path": path, "reason": "type de fichier non pris en charge"})
        else:
            skipped.append({"path": path, "reason": "introuvable"})
            continue
        for f in files:
            key = os.path.normcase(os.path.abspath(f))
            if key in known:
                skipped.append({"path": f, "reason": "déjà dans le projet"})
                continue
            if len(added) >= MAX_IMPORT:
                skipped.append({"path": f, "reason": f"plus de {MAX_IMPORT} fichiers"})
                continue
            known.add(key)
            added.append(_view(proj, proj.add_media(f)))
    return {"added": added, "skipped": skipped}


@router.delete("/api/timeline/{pid}/media/{mid}")
def media_delete(pid: str, mid: str) -> dict:
    """Retire un média du projet (et les clips qui l'utilisent)."""
    if not get(pid).remove_media(mid):
        raise HTTPException(404, "Média introuvable.")
    return {"ok": True}


@router.post("/api/timeline/{pid}/media/{mid}/retry")
def media_retry(pid: str, mid: str) -> dict:
    proj = get(pid)
    if not proj.update_media(mid, status="pending", progress=0, error=""):
        raise HTTPException(404, "Média introuvable.")
    proj.queue_media(mid)
    return {"ok": True}


@router.post("/api/timeline/{pid}/media/{mid}/relink")
def media_relink(pid: str, mid: str, body: dict = Body(...)) -> dict:
    """Retrouve un fichier déplacé : même média, nouveau chemin."""
    proj = get(pid)
    path = str(body.get("path") or "").strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(400, f"Fichier introuvable : {path}")
    if not proj.update_media(mid, path=os.path.abspath(path), copied=False,
                             status="pending", progress=0, error=""):
        raise HTTPException(404, "Média introuvable.")
    proj.queue_media(mid)
    return {"ok": True}


# ------------------------------------------------------------ effets sonores
# Bibliothèque synthétisée et « Mes sons » : communs à tous les projets, dans
# le dossier de travail (engine/tools/sfx.py).

_SOUNDS: dict[str, sfx.Sounds] = {}


def sounds() -> sfx.Sounds:
    root = os.path.join(_work_dir(), "sounds")
    if root not in _SOUNDS:
        _SOUNDS[root] = sfx.Sounds(root)
    return _SOUNDS[root]


def _sound_view(it: dict) -> dict:
    return {**it, "url": f"/api/sounds/{it['id']}/audio"}


@router.get("/api/sounds")
def sound_list() -> dict:
    S = sounds()
    return {**sfx.catalog(), "library": [_sound_view(x) for x in S.library()],
            "mine": [_sound_view(x) for x in S.mine()]}


@router.get("/api/sounds/{sid}/audio")
def sound_audio(sid: str):
    try:
        path = sounds().file(sid)
    except KeyError:
        raise HTTPException(404, "Son introuvable.") from None
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-cache"})


@router.post("/api/sounds/preview")
def sound_preview(body: dict = Body(...)) -> dict:
    """Son créé (moteur + réglages), à écouter avant de l'enregistrer."""
    try:
        return _sound_view(sounds().preview(str(body.get("engine") or ""), body.get("params") or {}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/sounds/mine")
def sound_save(body: dict = Body(...)) -> dict:
    try:
        return _sound_view(sounds().save_synth(body.get("name"), str(body.get("engine") or ""),
                                               body.get("params") or {}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/sounds/mine/upload")
async def sound_upload(request: Request, name: str = Query("Mon son"), source: str = Query("import")) -> dict:
    """Son importé (fichier envoyé) ou enregistré au micro : converti en WAV."""
    tmp = os.path.join(sounds().tmp_dir, "upload_" + model.new_id("u"))
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if os.path.getsize(tmp) < 100:
            raise ValueError("fichier vide")
        entry = sounds().save_file(os.path.splitext(_safe_name(name))[0], tmp,
                                   "record" if source == "record" else "import")
    except ValueError as exc:
        raise HTTPException(400, f"Son illisible ({exc}).") from exc
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return _sound_view(entry)


@router.post("/api/sounds/mine/paths")
def sound_paths(body: dict = Body(...)) -> dict:
    """Sons importés par leur chemin (application de bureau)."""
    added, skipped = [], []
    for raw in (body.get("paths") or [])[:50]:
        path = str(raw or "").strip().strip('"')
        if not os.path.isfile(path):
            skipped.append({"path": path, "reason": "introuvable"})
            continue
        try:
            added.append(_sound_view(sounds().save_file(os.path.splitext(os.path.basename(path))[0], path)))
        except ValueError as exc:
            skipped.append({"path": path, "reason": str(exc)[:120]})
    return {"added": added, "skipped": skipped}


@router.patch("/api/sounds/mine/{sid}")
def sound_rename(sid: str, body: dict = Body(...)) -> dict:
    it = sounds().rename(sid, body.get("name"))
    if not it:
        raise HTTPException(404, "Son introuvable.")
    return _sound_view(it)


@router.delete("/api/sounds/mine/{sid}")
def sound_delete(sid: str) -> dict:
    if not sounds().remove(sid):
        raise HTTPException(404, "Son introuvable.")
    return {"ok": True}


@router.post("/api/timeline/{pid}/sounds/{sid}")
def sound_to_project(pid: str, sid: str) -> dict:
    """Copie un effet sonore dans le projet : il devient un média comme un autre."""
    proj = get(pid)
    mid = model.new_id("m")
    try:
        dest, name = sounds().copy_to(sid, proj.media_folder(mid))
    except KeyError:
        raise HTTPException(404, "Son introuvable.") from None
    return _view(proj, proj.add_media(dest, name=name, copied=True, mid=mid))


# --------------------------------------------------------- outils automatiques

@router.post("/api/timeline/{pid}/transcribe")
def transcribe_media(pid: str, body: dict = Body(default={})) -> dict:
    """Met des médias en file de transcription (Whisper, un à la fois)."""
    proj = get(pid)
    ids = body.get("media") or [m["id"] for m in proj.state["media"]]
    queued, skipped = [], []
    for mid in ids:
        (queued if ai.queue_transcription(proj, str(mid), bool(body.get("force"))) else skipped).append(mid)
    return {"queued": queued, "skipped": skipped, "media": proj.media_views()}


@router.get("/api/timeline/{pid}/media/{mid}/words")
def media_words(pid: str, mid: str) -> dict:
    proj = get(pid)
    m = proj.media(mid)
    if m is None:
        raise HTTPException(404, "Média introuvable.")
    if (m.get("transcript") or {}).get("status") != "done":
        raise HTTPException(409, "Ce média n'est pas encore transcrit.")
    return {"words": ai.load_words(proj, mid), "language": (m.get("transcript") or {}).get("language", "")}


@router.get("/api/timeline/{pid}/media/{mid}/silences")
def media_silences(pid: str, mid: str, noise: float = Query(-35.0),
                   min_dur: float = Query(0.4, alias="min")) -> dict:
    """Silences mesurés au volume (pour les passages sans parole)."""
    proj = get(pid)
    if proj.media(mid) is None:
        raise HTTPException(404, "Média introuvable.")
    try:
        return {"silences": ai.silences(proj, mid, max(-90.0, min(noise, -5.0)),
                                        max(0.05, min(min_dur, 10.0)))}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


def _voice_input(clips: list, media: dict) -> list[dict]:
    """Clips envoyés par l'éditeur, réduits à ce qu'il faut pour sous-titrer.

    Pas de validation par piste : l'éditeur a pu créer une piste qui n'est pas
    encore sauvegardée.
    """
    out = []
    for c in clips:
        if not isinstance(c, dict) or c.get("media") not in media or c.get("kind") not in ("video", "audio"):
            continue
        try:
            out.append({"id": str(c.get("id") or ""), "kind": c["kind"], "media": c["media"],
                        "track": str(c.get("track") or ""), "start": max(0.0, float(c["start"])),
                        "dur": max(0.0, float(c["dur"])), "in": max(0.0, float(c.get("in") or 0)),
                        "speed": min(16.0, max(0.1, float(c.get("speed") or 1))),
                        "muted": bool(c.get("muted")), "detached": bool(c.get("detached"))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


@router.post("/api/timeline/{pid}/captions")
def make_captions(pid: str, body: dict = Body(...)) -> dict:
    """Sous-titres automatiques pour les clips envoyés (ceux de la timeline).

    Les médias qui portent la voix doivent être transcrits : sinon 409, avec
    la liste de ceux qui manquent (l'éditeur lance leur transcription).
    """
    proj = get(pid)
    clips = body.get("clips")
    if not isinstance(clips, list):
        raise HTTPException(400, "`clips` doit être une liste.")
    media = {m["id"]: m for m in proj.state["media"]}
    clips = _voice_input(clips, media)
    voice = ai._voice_clips(clips, media)
    missing = sorted({c["media"] for c in voice
                      if (media[c["media"]].get("transcript") or {}).get("status") != "done"})
    if missing:
        raise HTTPException(409, {"message": "Transcription nécessaire.", "missing": missing})
    cache: dict[str, list] = {}

    def words_of(mid: str) -> list:
        if mid not in cache:
            cache[mid] = ai.load_words(proj, mid)
        return cache[mid]

    settings = {**proj.state.get("settings", {}), **(body.get("settings") or {})}
    caps = ai.build_captions(clips, media, words_of, settings)
    return {"captions": caps, "language": ai.language_of(proj, sorted({c["media"] for c in voice}))}


@router.post("/api/timeline/{pid}/translate")
def translate(pid: str, body: dict = Body(...)) -> dict:
    """Traduit des textes sur la machine (Opus-MT), sans rien enregistrer."""
    proj = get(pid)
    captions = body.get("captions")
    if not isinstance(captions, list):
        raise HTTPException(400, "`captions` doit être une liste.")
    mids = sorted({w.get("m") for c in captions for w in (c.get("words") or []) if w.get("m")})
    source = str(body.get("source") or ai.language_of(proj, mids) or "fr")
    target = str(body.get("target") or "en")
    if source == target:
        raise HTTPException(400, "Les textes sont déjà dans cette langue.")
    if not translate_available(source, target):
        raise HTTPException(400, f"Pas de modèle de traduction {source} → {target} installé.")
    # Les clips de la timeline ont `start`/`dur` ; la traduction attend `end`,
    # et ne doit pas voir les mots dont le passage a été coupé.
    caps = []
    for c in captions:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        c["end"] = float(c.get("start") or 0) + float(c.get("dur") or 0)
        c["words"] = [w for w in c.get("words") or [] if not w.get("cut")]
        caps.append(c)
    try:
        out = translate_captions(caps, source, target)
        for c in out:
            c.pop("end", None)
        return {"captions": out, "source": source}
    except Exception as exc:  # noqa: BLE001 - message remonté tel quel
        raise HTTPException(500, f"Traduction impossible : {exc}") from exc


@router.get("/api/timeline/{pid}/translate/available")
def translate_ready(pid: str, target: str = Query("en")) -> dict:
    proj = get(pid)
    source = ai.language_of(proj, [m["id"] for m in proj.state["media"]])
    return {"source": source, "target": target, "available": translate_available(source, target)}


# --------------------------------------------------------- montage automatique

AUTOEDIT_JOBS: dict[str, dict] = {}
LLM_DL: dict = {"status": "idle", "pct": 0, "message": ""}


@router.get("/api/llm")
def llm_status() -> dict:
    """État du modèle de langage local (présent, chargé) et du téléchargement."""
    return {**llm.info(), "download": dict(LLM_DL), "size_gb": 4.0}


@router.post("/api/llm/download")
def llm_download() -> dict:
    """Télécharge le modèle (~4 Go) en tâche de fond, une seule fois."""
    if llm.available():
        return {"ok": True, "done": True}
    if LLM_DL["status"] == "running":
        return {"ok": True}
    LLM_DL.update(status="running", pct=0, message="Téléchargement…")

    def run() -> None:
        import threading
        stop = threading.Event()

        def watch() -> None:
            # progression estimée d'après la taille des fichiers déjà écrits
            from huggingface_hub import constants
            folder = os.path.join(constants.HF_HUB_CACHE, "models--" + llm._env_repo().replace("/", "--"), "blobs")
            while not stop.wait(1.0):
                try:
                    got = sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder))
                except OSError:
                    got = 0
                LLM_DL["pct"] = round(min(99.0, 100.0 * got / 4.05e9), 1)
        threading.Thread(target=watch, daemon=True).start()
        try:
            llm.download()
            LLM_DL.update(status="done", pct=100, message="Modèle prêt.")
        except Exception as exc:  # noqa: BLE001 - réseau, disque
            LLM_DL.update(status="error", message=str(exc)[:300])
        finally:
            stop.set()
    _spawn(run)
    return {"ok": True}


def _autoedit_job(proj: TimelineProject, jid: str, mids: list[str], opts: dict) -> None:
    job = AUTOEDIT_JOBS[jid]
    try:
        plans: dict[str, dict] = {}
        for n, mid in enumerate(mids):
            m = proj.media(mid)
            if not m:
                continue
            job.update(message=f"Analyse de « {m['name']} »…", pct=round(100 * n / max(1, len(mids))))
            folder = proj.media_folder(mid)
            words_file = ai.words_path(proj, mid)
            key = autoedit.cache_key(words_file, opts)
            cache = os.path.join(folder, f"autoedit_{key}.json")
            plan = None
            try:
                with open(cache, encoding="utf-8") as f:
                    plan = json.load(f)
            except (OSError, ValueError):
                pass
            if plan is None:
                words = ai.load_words(proj, mid)
                wave, rate = None, 100
                try:
                    with open(os.path.join(folder, "wave.bin"), "rb") as f:
                        wave = f.read()
                    rate = int((m.get("waveform") or {}).get("rate") or 100)
                except OSError:
                    pass
                if opts.get("llm", True) and llm.available():
                    job["message"] = f"L'IA prépare le montage de « {m['name']} »…"
                lang = (m.get("transcript") or {}).get("language") or "fr"
                plan = autoedit.build_plan(words, wave, rate, opts, lang)
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump(plan, f, ensure_ascii=False)
            plan["face"] = _face(proj, m)
            plans[mid] = plan
        job.update(status="done", pct=100, message="Plan prêt.", plans=plans)
    except Exception as exc:  # noqa: BLE001 - affiché dans l'éditeur
        job.update(status="error", message=str(exc)[:600])


def _face(proj: TimelineProject, m: dict) -> dict | None:
    """Position du visage dans un média vidéo (calculée une fois sur le proxy)."""
    if m.get("kind") != "video":
        return None
    cache = os.path.join(proj.media_folder(m["id"]), "face.json")
    try:
        with open(cache, encoding="utf-8") as f:
            return json.load(f).get("face")
    except (OSError, ValueError):
        pass
    proxy = os.path.join(proj.media_folder(m["id"]), m.get("proxy_file") or "")
    face = autoedit.face_anchor(proxy) if os.path.isfile(proxy) else None
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump({"face": face}, f)
    except (OSError, TypeError, ValueError):
        pass
    return face


@router.post("/api/timeline/{pid}/autoedit")
def autoedit_start(pid: str, body: dict = Body(...)) -> dict:
    """Plan de montage automatique pour des médias transcrits (tâche de fond)."""
    proj = get(pid)
    mids = [str(x) for x in (body.get("media") or []) if proj.media(str(x))]
    if not mids:
        raise HTTPException(400, "Aucun média à monter.")
    missing = [mid for mid in mids if (proj.media(mid).get("transcript") or {}).get("status") != "done"]
    if missing:
        raise HTTPException(409, {"message": "Transcription nécessaire.", "missing": missing})
    opts = body.get("options") or {}
    opts = {"llm": bool(opts.get("llm", True)), "trim": bool(opts.get("trim", True)),
            "cold_open": bool(opts.get("cold_open", False)),
            "max_duration": float(opts.get("max_duration") or 0)}
    jid = model.new_id("j")
    AUTOEDIT_JOBS[jid] = {"status": "running", "pct": 0, "message": "En attente…", "plans": None}
    jobs.TRANSCRIBE.submit(("autoedit", jid), _autoedit_job, proj, jid, mids, opts)
    return {"job_id": jid, "llm": opts["llm"] and llm.available()}


@router.get("/api/timeline/{pid}/autoedit/{jid}")
def autoedit_status(pid: str, jid: str) -> dict:
    get(pid)
    job = AUTOEDIT_JOBS.get(jid)
    if job is None:
        raise HTTPException(404, "Tâche introuvable.")
    return job


# -------------------------------------------------------------------- export

def _spawn(fn, *args) -> None:
    import threading
    threading.Thread(target=fn, args=args, daemon=True).start()


@router.post("/api/timeline/{pid}/export")
def export_start(pid: str, body: dict = Body(default={})) -> dict:
    """Lance l'export (l'éditeur a sauvegardé juste avant)."""
    from engine.timeline import render
    from engine.tools import audio as audio_tool
    proj = get(pid)
    if not proj.state["clips"]:
        raise HTTPException(400, "Le montage est vide : rien à exporter.")
    res = body.get("resolution")
    if res and res not in render.RESOLUTIONS:
        raise HTTPException(400, f"Définition inconnue : {res}")
    if body.get("audio_only"):
        try:
            audio_tool.encode_args(str(body.get("audio_format") or "mp3"), body.get("audio_quality"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    folder = str(body.get("folder") or "").strip().strip('"')
    if folder and not os.path.isdir(os.path.dirname(os.path.abspath(folder))):
        raise HTTPException(400, f"Dossier introuvable : {folder}")
    if not proj.reserve_export():
        raise HTTPException(409, "Un export est déjà en cours.")
    _spawn(proj.export, dict(body))
    return {"ok": True}


@router.get("/api/timeline/{pid}/export")
def export_status(pid: str) -> dict:
    from engine.timeline.project import default_export_dir
    proj = get(pid)
    return {"task": dict(proj.task), "export": proj.state.get("export"),
            "folder": default_export_dir(proj, create=False)}


@router.post("/api/timeline/{pid}/export/cancel")
def export_cancel(pid: str) -> dict:
    return {"ok": get(pid).cancel_export()}


@router.get("/api/timeline/{pid}/export/file")
def export_file(pid: str):
    proj = get(pid)
    res = proj.state.get("export") or {}
    path = res.get("output") or ""
    if not os.path.isfile(path):
        raise HTTPException(404, "Aucun export disponible.")
    return FileResponse(path, filename=os.path.basename(path))


@router.post("/api/timeline/{pid}/export/reveal")
def export_reveal(pid: str) -> dict:
    """Ouvre le dossier de l'export dans l'explorateur, le fichier sélectionné."""
    import subprocess
    import sys
    proj = get(pid)
    path = (proj.state.get("export") or {}).get("output") or ""
    if not os.path.isfile(path):
        raise HTTPException(404, "Aucun export disponible.")
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError as exc:
        raise HTTPException(500, f"Impossible d'ouvrir le dossier : {exc}") from exc
    return {"ok": True}


# Route générique en DERNIER : elle avalerait /words et /silences.
_FILES = {
    "thumbs": ("thumbs.jpg", "image/jpeg"),
    "poster": ("poster.jpg", "image/jpeg"),
    "wave": ("wave.bin", "application/octet-stream"),
}
_PROXY_TYPES = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".jpg": "image/jpeg"}


# ---------------------------------------------------------- sujet et arrière-plan

@router.get("/api/matting")
def matting_info() -> dict:
    from engine.pipeline import matting
    return matting.info()


@router.post("/api/matting/download")
def matting_download() -> dict:
    from engine.pipeline import matting
    matting.download_async()
    return matting.info()


@router.post("/api/timeline/{pid}/media/{mid}/subject")
def subject_pick(pid: str, mid: str, body: dict = Body(...)) -> dict:
    """Détoure le sujet désigné d'un clic (x, y dans l'image, 0..1 ; t, temps source)."""
    from engine.pipeline import matting
    from engine.timeline.project import detect_subject
    proj = get(pid)
    m = proj.media(mid)
    if m is None:
        raise HTTPException(404, "Média introuvable.")
    if m.get("kind") not in ("video", "image") or m.get("status") != "ready":
        raise HTTPException(400, "Ce média ne peut pas être détouré.")
    if not matting.model_path():
        raise HTTPException(409, "Modèle de détourage absent : télécharge-le d'abord.")
    x = model._num(body.get("x"), 0.5, 0.0, 1.0)
    y = model._num(body.get("y"), 0.5, 0.0, 1.0)
    t = model._num(body.get("t"), 0.0, 0.0)
    rev = int((m.get("subject") or {}).get("rev") or 0)
    proj.update_media(mid, subject={"status": "queued", "progress": 0, "x": x, "y": y, "t": t, "rev": rev})
    jobs.SUBJECT.submit((pid, mid, x, y, t), detect_subject, proj, mid, x, y, t)
    return _view(proj, proj.media(mid))


@router.delete("/api/timeline/{pid}/media/{mid}/subject")
def subject_forget(pid: str, mid: str) -> dict:
    proj = get(pid)
    m = proj.media(mid)
    if m is None:
        raise HTTPException(404, "Média introuvable.")
    for name in ("matte.mp4", "cutout.webm", "matte.png", "cutout.png"):
        try:
            os.remove(os.path.join(proj.media_folder(mid), name))
        except OSError:
            pass
    proj.update_media(mid, subject=None)
    return _view(proj, proj.media(mid))


@router.get("/api/timeline/{pid}/media/{mid}/{what}")
def media_file(pid: str, mid: str, what: str):
    """Fichiers dérivés d'un média. L'URL porte sa révision : cache permanent."""
    proj = get(pid)
    m = proj.media(mid)
    if m is None:
        raise HTTPException(404, "Média introuvable.")
    if what == "cutout":
        name = "cutout.png" if m.get("kind") == "image" else "cutout.webm"
        mime = "image/png" if m.get("kind") == "image" else "video/webm"
    elif what == "proxy":
        name = m.get("proxy_file") or ""
        mime = _PROXY_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
    elif what in _FILES:
        name, mime = _FILES[what]
    else:
        raise HTTPException(404, "Fichier inconnu.")
    path = os.path.join(proj.media_folder(mid), name)
    if not name or not os.path.isfile(path):
        raise HTTPException(404, "Pas encore prêt.")
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.post("/api/timeline/from-short/{pid}")
def from_short(pid: str) -> dict:
    """Ouvre un projet short dans la timeline (nouveau projet, l'ancien est gardé)."""
    from engine.timeline import convert
    try:
        tl = convert.from_short(_work_dir(), pid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    TIMELINES[tl.id] = tl
    return {"id": tl.id}


@router.post("/api/timeline/{pid}/freeze")
def freeze_frame(pid: str, body: dict = Body(...)) -> dict:
    """Arrêt sur image : l'image exacte d'un média à l'instant `at`, en nouveau média."""
    import subprocess
    proj = get(pid)
    m = proj.media(str(body.get("media") or ""))
    if m is None or m.get("kind") != "video" or m.get("status") != "ready":
        raise HTTPException(400, "Choisis un clip vidéo prêt.")
    try:
        at = max(0.0, min(float(body.get("at") or 0.0), float(m.get("duration") or 0.0) - 0.04))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Instant invalide.") from exc
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "source.jpg")
    res = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}",
                          "-i", m["path"], "-frames:v", "1", "-q:v", "2", out], capture_output=True)
    if res.returncode != 0 or not os.path.isfile(out):
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, "Image impossible à extraire.")
    base = os.path.splitext(m["name"])[0]
    return _view(proj, proj.add_media(out, name=f"{base} — arrêt {at:.2f} s.jpg", copied=True, mid=mid))
