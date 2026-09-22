"""Serveur web local (FastAPI) : projets, analyse, édition et export.

Lancer :   python -m engine.server
Puis ouvrir http://127.0.0.1:8765

Le flux est en deux temps, pour ne payer Whisper qu'une fois :

    /api/analyze  -> transcrit, coupe les blancs, fabrique un proxy léger
    (édition)     -> le navigateur déplace/corrige les sous-titres, gratuit
    /api/recut    -> rejoue les coupes sans retranscrire
    /api/export   -> rend la vidéo finale avec les sous-titres tels qu'édités

Les projets sont écrits sur disque (`engine.store`) : fermer le serveur ne perd
rien, et l'écran d'accueil liste les montages en cours. La mémoire ne sert que
de cache.

Tout reste local : les vidéos ne quittent jamais la machine. Le seul accès
réseau possible est le téléchargement du modèle Whisper, une fois, s'il manque.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from engine import store
from engine.core import Options, default_output
from engine.pipeline.style_presets import catalog, groups
from engine.pipeline.translate import available as translate_available
from engine.pipeline.translate import translate_captions
from engine.project import Project
from engine.timeline import api as timeline_api
from engine.tools import audio as audio_tool

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
# Installée, l'application range les projets dans le dossier de l'utilisateur :
# son dossier d'installation peut être en lecture seule (cf. app.py).
WORK_DIR = os.path.abspath(os.environ.get("MONTAGE_IA_WORK")
                           or os.path.join(HERE, "..", "work"))
os.makedirs(WORK_DIR, exist_ok=True)

app = FastAPI(title="Montage IA")


class _Static(StaticFiles):
    """Fichiers de l'interface, revalidés à chaque chargement : après une mise
    à jour de l'application, le navigateur ne doit pas garder d'anciens modules."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/web", _Static(directory=WEB_DIR), name="web")
# Le studio lit le dossier de travail au moment de chaque requête : les tests
# le déplacent en cours de route.
timeline_api.configure(lambda: WORK_DIR)
app.include_router(timeline_api.router)

# Cache des projets ouverts ; la vérité est sur disque.
PROJECTS: dict[str, Project] = {}
_TMP_PREFIXES = ("_preview_", "_cut_", "_capt_", "_caps_")


def _clean_workdir() -> None:
    """Balaie les fichiers intermédiaires laissés par une session précédente.

    Les rendus en cours au moment de l'arrêt ne servent plus à rien ; les
    proxies, vignettes et exports référencés par un projet sont conservés.
    """
    roots = [WORK_DIR]
    projects_root = store.root(WORK_DIR)
    roots += [os.path.join(projects_root, d) for d in os.listdir(projects_root)]
    for folder in roots:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.startswith(_TMP_PREFIXES):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass


_clean_workdir()

# Boîte à outils : pas de projet, juste des tâches en mémoire. Leurs fichiers
# (vidéo envoyée, résultat à télécharger) vivent dans `work/tools/<id>/` et
# sont effacés au démarrage suivant.
TOOLS_DIR = os.path.join(WORK_DIR, "tools")
TOOL_JOBS: dict[str, dict] = {}
shutil.rmtree(TOOLS_DIR, ignore_errors=True)


def _project(pid: str) -> Project:
    """Projet en cache, sinon relu depuis le disque."""
    proj = PROJECTS.get(pid)
    if proj is None:
        proj = Project.load(WORK_DIR, pid)
        if proj is None:
            raise HTTPException(404, "Projet introuvable.")
        PROJECTS[pid] = proj
    return proj


def _idle(proj: Project) -> Project:
    """Refuse une nouvelle tâche tant que la précédente tourne."""
    if proj.is_busy:
        raise HTTPException(409, "Une opération est déjà en cours sur ce projet.")
    return proj


def _spawn(fn, *args, **kwargs) -> None:
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


def _safe(name: str) -> str:
    """Nom de fichier utilisable sous Windows."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "video"


def _page(name: str) -> HTMLResponse:
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return _page("index.html")


@app.get("/studio", response_class=HTMLResponse)
def studio() -> HTMLResponse:
    """Éditeur timeline (le projet est désigné par `#p=<id>`)."""
    return _page("studio.html")


@app.get("/api/styles")
def styles() -> dict:
    return {"styles": catalog(), "groups": groups()}


# --------------------------------------------------------------------- projets

@app.get("/api/projects")
def projects() -> dict:
    return {"projects": store.list_projects(WORK_DIR)}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    proj = PROJECTS.get(pid)
    if proj is not None and proj.is_busy:
        raise HTTPException(409, "Une opération est en cours sur ce projet.")
    timeline_api.forget(pid)
    PROJECTS.pop(pid, None)
    if not store.delete_project(WORK_DIR, pid):
        raise HTTPException(404, "Projet introuvable.")
    return {"ok": True}


@app.get("/api/project/{pid}")
def project_state(pid: str) -> dict:
    return _project(pid).to_dict()


@app.post("/api/project/{pid}/save")
def project_save(pid: str, body: dict = Body(...)) -> dict:
    """Sauvegarde automatique de l'éditeur (sous-titres, style, nom)."""
    proj = _project(pid)
    proj.set_state(captions=body.get("captions"), style=body.get("style"),
                   name=body.get("name"))
    return {"ok": True, "updated": proj.updated}


@app.post("/api/project/{pid}/preview")
def project_preview(pid: str) -> dict:
    """Refabrique le proxy d'un projet rouvert dont l'aperçu a disparu."""
    proj = _idle(_project(pid))
    _spawn(proj.rebuild_preview)
    return {"ok": True}


@app.post("/api/project/{pid}/reanalyze")
def project_reanalyze(pid: str) -> dict:
    """Relance l'analyse d'un projet dont la transcription n'a pas abouti.

    La vidéo importée est déjà sur disque : pas besoin de la renvoyer.
    """
    proj = PROJECTS.get(pid)
    if proj is None:
        state = store.read_state(WORK_DIR, pid)
        if not state:
            raise HTTPException(404, "Projet introuvable.")
        if state.get("words"):
            raise HTTPException(400, "Ce projet est déjà analysé.")
        opts = Options(**{k: v for k, v in (state.get("opts") or {}).items()
                          if k in Options.__dataclass_fields__})
        proj = Project(pid, state.get("source") or "", WORK_DIR, opts,
                       state.get("output_path") or "", state.get("name", ""))
        proj.created = state.get("created", proj.created)
    else:
        _idle(proj)
        if proj.words:
            raise HTTPException(400, "Ce projet est déjà analysé.")
    if not os.path.isfile(proj.source):
        raise HTTPException(400, "La vidéo de ce projet a disparu : recrée le projet.")
    # Anciennes versions : "cuda" imposé par l'interface. Une nouvelle tentative
    # doit pouvoir se rabattre sur le processeur.
    if proj.opts.device == "cuda":
        proj.opts.device = "auto"
    PROJECTS[pid] = proj
    _spawn(proj.analyze)
    return {"ok": True}


@app.post("/api/project/{pid}/translate")
def project_translate(pid: str, body: dict = Body(...)) -> dict:
    """Traduit des lignes de sous-titres sur la machine (aucun appel réseau).

    Rend les lignes traduites sans rien enregistrer : l'éditeur les intègre à
    son historique (annulable) puis les sauvegarde comme n'importe quelle retouche.
    """
    proj = _project(pid)
    captions = body.get("captions")
    if not isinstance(captions, list):
        raise HTTPException(400, "`captions` doit être une liste.")
    source = proj.language or "fr"
    target = str(body.get("target") or "en")
    if source == target:
        raise HTTPException(400, "Les sous-titres sont déjà dans cette langue.")
    if not translate_available(source, target):
        raise HTTPException(400, f"Pas de modèle de traduction {source} → {target} installé.")
    try:
        return {"captions": translate_captions(captions, source, target)}
    except Exception as exc:  # noqa: BLE001 - message remonté tel quel au front
        raise HTTPException(500, f"Traduction impossible : {exc}") from exc


@app.get("/api/thumb/{pid}")
def thumbnail(pid: str):
    path = os.path.join(store.project_dir(WORK_DIR, pid), "thumb.jpg")
    if not os.path.isfile(path):
        raise HTTPException(404, "Pas de vignette.")
    return FileResponse(path, media_type="image/jpeg")


# ---------------------------------------------------------------------- montage

@app.post("/api/analyze")
async def analyze(
    file: Optional[UploadFile] = File(None),
    path: Optional[str] = Form(None),
    name: str = Form(""),
    style: str = Form("hype"),
    emojis: bool = Form(True),
    cut_silence: bool = Form(True),
    cut_fillers: bool = Form(False),
    max_gap: float = Form(0.5),
    words_per_line: int = Form(4),
    max_chars: int = Form(18),
    vertical: bool = Form(True),
    device: str = Form("auto"),
    compute_type: str = Form("auto"),
    model: str = Form("large-v3-turbo"),
    encoder: str = Form("auto"),
    language: Optional[str] = Form(None),
) -> dict:
    """Passe lourde : transcription + coupes + proxy de prévisualisation."""
    pid = uuid.uuid4().hex

    if file is not None and file.filename:
        base, ext = os.path.splitext(os.path.basename(file.filename))
        pdir = store.project_dir(WORK_DIR, pid, create=True)
        in_path = os.path.join(pdir, "source" + (ext or ".mp4"))
        with open(in_path, "wb") as out_f:
            while chunk := await file.read(1024 * 1024):
                out_f.write(chunk)
        out_path = os.path.join(pdir, _safe(base) + "_short.mp4")
        title = base
    elif path:
        in_path = path.strip().strip('"')
        if not os.path.isfile(in_path):
            raise HTTPException(400, f"Fichier introuvable : {in_path}")
        store.project_dir(WORK_DIR, pid, create=True)
        out_path = default_output(in_path)
        title = os.path.splitext(os.path.basename(in_path))[0]
    else:
        raise HTTPException(400, "Aucune vidéo fournie (fichier ou chemin).")

    opts = Options(
        model=model, device=device, compute_type=compute_type, encoder=encoder,
        max_gap=max_gap,
        language=language or None, vertical=vertical, words_per_line=words_per_line,
        max_chars=max_chars, style=style, emojis=emojis,
        cut_silence=cut_silence, cut_fillers=cut_fillers,
    )
    proj = Project(pid, in_path, WORK_DIR, opts, out_path, name.strip() or title)
    PROJECTS[pid] = proj
    proj.save()
    _spawn(proj.analyze)
    return {"job_id": pid}


@app.post("/api/recut")
def recut(body: dict = Body(...)) -> dict:
    """Rejoue les coupes à partir des mots déjà transcrits (pas de Whisper)."""
    proj = _idle(_project(str(body.get("job_id", ""))))
    keep = body.get("keep_ranges")   # absent = passages gardés inchangés
    if keep is not None and not isinstance(keep, list):
        raise HTTPException(400, "`keep_ranges` doit être une liste de [début, fin].")
    _spawn(
        proj.recut,
        max_gap=float(body.get("max_gap", proj.opts.max_gap)),
        cut_silence=bool(body.get("cut_silence", proj.opts.cut_silence)),
        cut_fillers=bool(body.get("cut_fillers", proj.opts.cut_fillers)),
        keep=keep,
    )
    return {"ok": True}


@app.post("/api/export")
def export(body: dict = Body(...)) -> dict:
    """Rend la vidéo finale avec les sous-titres exactement tels qu'édités."""
    proj = _idle(_project(str(body.get("job_id", ""))))
    captions = body.get("captions") or []
    if not isinstance(captions, list):
        raise HTTPException(400, "`captions` doit être une liste.")
    if body.get("style"):
        proj.style = str(body["style"])
    proj.captions = captions or proj.captions
    _spawn(proj.export, captions, body.get("encoder") or None,
           str(body.get("signature") or ""))
    return {"ok": True}


@app.get("/api/jobs/{pid}")
def job_status(pid: str) -> dict:
    return _project(pid).task


@app.get("/api/preview/{pid}")
def preview(pid: str):
    proj = _project(pid)
    if not proj.preview_ready:
        raise HTTPException(404, "Aperçu indisponible.")
    # no-store : le proxy est réécrit à chaque recalcul des coupes.
    return FileResponse(proj.preview_path, media_type="video/mp4",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/result/{pid}")
def job_result(pid: str):
    proj = _project(pid)
    result = proj.export_result
    if not result or not os.path.isfile(result["output"]):
        raise HTTPException(404, "Aucun export disponible.")
    return FileResponse(result["output"], media_type="video/mp4",
                        filename=os.path.basename(result["output"]))


# --------------------------------------------------------- boîte à outils

def _tool_job(tid: str) -> dict:
    job = TOOL_JOBS.get(tid)
    if job is None:
        raise HTTPException(404, "Tâche introuvable.")
    return job


def _run_extract_audio(job: dict, src: str, upload: bool, **kw) -> None:
    job.update(status="running", message="Extraction du son…")
    try:
        res = audio_tool.extract_audio(
            src, job["output"],
            on_progress=lambda p: job.update(pct=round(p * 100, 1)), **kw)
        job.update(status="done", pct=100, message="Terminé.", result=res)
    except Exception as exc:  # noqa: BLE001 - message remonté tel quel au front
        job.update(status="error", message=str(exc))
        try:
            os.remove(job["output"])
        except OSError:
            pass
    finally:
        if upload:   # la copie envoyée ne sert plus
            try:
                os.remove(src)
            except OSError:
                pass


@app.get("/api/tools/audio/formats")
def audio_formats() -> dict:
    return audio_tool.catalog()


@app.post("/api/tools/audio")
async def extract_audio(
    file: Optional[UploadFile] = File(None),
    path: Optional[str] = Form(None),
    format: str = Form(audio_tool.DEFAULT_FORMAT),
    quality: Optional[int] = Form(None),
    sample_rate: Optional[int] = Form(None),
    channels: Optional[str] = Form(None),
) -> dict:
    """Extrait la bande son d'une vidéo, hors de tout projet.

    Vidéo envoyée : le son est rangé dans `work/tools/` et se télécharge.
    Chemin local : il est écrit à côté de la vidéo, sans rien écraser.
    """
    kw = dict(fmt=format, quality=quality, sample_rate=sample_rate or None,
              channels=channels or None)
    try:
        audio_tool.encode_args(**kw)   # réglage invalide : refusé avant de copier la vidéo
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    tid = uuid.uuid4().hex
    tdir = os.path.join(TOOLS_DIR, tid)
    if file is not None and file.filename:
        os.makedirs(tdir, exist_ok=True)
        name = _safe(os.path.basename(file.filename))
        src = os.path.join(tdir, "_source_" + name)
        with open(src, "wb") as out_f:
            while chunk := await file.read(1024 * 1024):
                out_f.write(chunk)
        out = audio_tool.output_path(name, format, folder=tdir)
        upload = True
    elif path:
        src = path.strip().strip('"')
        if not os.path.isfile(src):
            raise HTTPException(400, f"Fichier introuvable : {src}")
        out = audio_tool.output_path(src, format)
        upload = False
    else:
        raise HTTPException(400, "Aucune vidéo fournie (fichier ou chemin).")

    job = {"status": "queued", "pct": 0, "message": "En attente…", "output": out}
    TOOL_JOBS[tid] = job
    _spawn(_run_extract_audio, job, src, upload, **kw)
    return {"job_id": tid}


@app.get("/api/tools/jobs/{tid}")
def tool_job_status(tid: str) -> dict:
    job = _tool_job(tid)
    return {k: job.get(k) for k in ("status", "pct", "message", "output", "result")}


@app.get("/api/tools/result/{tid}")
def tool_job_result(tid: str):
    job = _tool_job(tid)
    res = job.get("result")
    if job.get("status") != "done" or not res or not os.path.isfile(res["output"]):
        raise HTTPException(404, "Aucun résultat disponible.")
    return FileResponse(res["output"], media_type=res["mime"],
                        filename=os.path.basename(res["output"]))


def main() -> None:
    import uvicorn
    uvicorn.run("engine.server:app", host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
