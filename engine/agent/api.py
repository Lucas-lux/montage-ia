"""Routes HTTP des agents IA (`/api/agent/...`), appelées par le serveur MCP.

Même moteur, mêmes projets que le studio : un montage fait par un agent
s'ouvre dans l'application et se retouche à la main. Les erreurs sont des
phrases pour l'agent (quoi faire ensuite), en 4xx.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Callable

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from engine import store
from engine.agent import images, preview, reference, scenes, service, visuals
from engine.timeline import api as timeline_api
from engine.timeline import model
from engine.timeline.project import TimelineProject

API_VERSION = 1
router = APIRouter(prefix="/api/agent")


def _work() -> str:
    return timeline_api._work_dir()


def _settings_path() -> str:
    return os.path.join(_work(), "agent", "settings.json")


def settings() -> dict:
    """Réglages des agents gardés par l'application (clé Pexels)."""
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def pexels_key(given: str | None = None) -> str | None:
    return given or os.environ.get("PEXELS_API_KEY") or settings().get("pexels_key") or None


def _proj(pid: str):
    return timeline_api.get(pid)


def _call(fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.AgentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except scenes.SceneError as exc:
        raise HTTPException(400, scene_error_text(exc)) from exc
    except (images.SearchError, visuals.VisualError) as exc:
        raise HTTPException(400, str(exc)) from exc


def scene_error_text(exc: Exception) -> str:
    """Message d'un plan de scènes refusé : la raison, puis chaque problème."""
    probs = getattr(exc, "problems", None) or []
    return str(exc) + ("".join(f"\n- {p}" for p in probs) if probs else "")


# ------------------------------------------------------------ généralités

@router.get("/info")
def info() -> dict:
    from engine.pipeline import fonts, llm, matting, style_presets
    from engine.timeline import animations
    from engine.timeline.project import default_export_dir  # noqa: F401 - vérifie l'import
    return {
        "api": API_VERSION, "work_dir": _work(),
        "fonts": len(fonts.bundled()), "caption_styles": len(style_presets.PRESETS),
        "title_styles": len(service.titles()), "animations": len(animations.definitions()),
        "sounds": len(timeline_api.sounds().library()), "transitions": list(model.TRANSITIONS),
        "formats": list(model.CANVAS_PRESETS), "local_llm": llm.available(),
        "subject_model": bool(matting.model_path()), "visual_renderer": visuals.find_browser(),
        "pexels": bool(pexels_key()),
    }


# ------------------------------------------- brancher un agent (Claude Code…)

NAME = "montage-ia"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def mcp_command() -> list[str]:
    """Commande qui lance le serveur MCP de cette installation."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mcp"]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [sys.executable, os.path.join(root, "app.py"), "--mcp"]


def _q(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or "\\" in arg) else arg


def _cli(name: str) -> str | None:
    exe = shutil.which(name)
    if exe:
        return exe
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".local", "bin", name + (".exe" if os.name == "nt" else "")),
                 os.path.join(os.environ.get("APPDATA", ""), "npm", name + ".cmd")):
        if os.path.isfile(cand):
            return cand
    return None


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)


def snippets() -> dict:
    cmd = mcp_command()
    line = " ".join(_q(a) for a in cmd)
    toml_args = ", ".join(json.dumps(a) for a in cmd[1:])
    return {
        "claude": f"claude mcp add --scope user {NAME} -- {line}",
        "codex": f"codex mcp add {NAME} -- {line}",
        "codex_toml": (f"[mcp_servers.{NAME}]\ncommand = {json.dumps(cmd[0])}\nargs = [{toml_args}]\n"
                       "tool_timeout_sec = 600\n"),
        "json": json.dumps({"mcpServers": {NAME: {"command": cmd[0], "args": cmd[1:]}}}, indent=2),
    }


@router.get("/connect")
def connect_info() -> dict:
    """Claude Code et Codex sont-ils là, et Montage IA y est-il déjà branché ?"""
    clients = {}
    for name in ("claude", "codex"):
        exe = _cli(name)
        registered = None
        if exe:
            try:
                r = _run([exe, "mcp", "get", NAME], 30)
                registered = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                registered = None
        clients[name] = {"found": bool(exe), "path": exe, "registered": registered}
    return {"name": NAME, "command": mcp_command(), "clients": clients, "snippets": snippets(),
            "pexels": bool(pexels_key())}


@router.post("/connect")
def connect(body: dict = Body(...)) -> dict:
    """Branche (ou débranche) Montage IA dans Claude Code ou Codex."""
    client = str(body.get("client") or "")
    remove = body.get("action") == "remove"
    exe = _cli(client) if client in ("claude", "codex") else None
    if not exe:
        raise HTTPException(404, f"{client or 'Client'} introuvable sur ce PC.")
    if client == "claude":
        args = [exe, "mcp", "remove", "--scope", "user", NAME] if remove else \
            [exe, "mcp", "add", "--scope", "user", NAME, "--", *mcp_command()]
    else:
        args = [exe, "mcp", "remove", NAME] if remove else [exe, "mcp", "add", NAME, "--", *mcp_command()]
    try:
        r = _run(args, 90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(500, f"{client} n'a pas répondu : {exc}") from exc
    out = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode != 0 and not (not remove and "already exists" in out):
        raise HTTPException(400, out[-800:] or f"{client} a renvoyé le code {r.returncode}.")
    return {"ok": True, "output": out[-800:]}


@router.get("/settings")
def get_settings() -> dict:
    key = settings().get("pexels_key") or ""
    return {"pexels": bool(key), "pexels_hint": (key[:4] + "…" + key[-3:]) if len(key) > 8 else ""}


@router.post("/settings")
def set_settings(body: dict = Body(...)) -> dict:
    data = settings()
    if "pexels_key" in body:
        key = str(body.get("pexels_key") or "").strip()
        if key:
            data["pexels_key"] = key
        else:
            data.pop("pexels_key", None)
    os.makedirs(os.path.dirname(_settings_path()), exist_ok=True)
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return get_settings()


@router.get("/catalog/{what}")
def catalog(what: str) -> dict:
    from engine.pipeline import fonts, style_presets
    from engine.timeline import animations
    if what == "caption_styles":
        items = [{"name": n, "label": p.get("label", n), "hint": p.get("hint", ""), "group": p.get("group", ""),
                  "font": style_presets.preset(n).get("font"),
                  **({"anims": {k: v["type"] for k, v in style_presets.preset_anims(n).items()}}
                     if style_presets.preset_anims(n) else {})}
                 for n, p in style_presets.PRESETS.items()]
        return {"items": items, "groups": style_presets.GROUPS}
    if what == "title_styles":
        return {"items": [{"name": t["name"], "label": t["label"], "hint": t["hint"], "group": t["group"],
                           "font": t["look"].get("font"),
                           **({"anims": {k: t["look"][k]["type"] for k in ("anim_in", "anim_out", "anim_loop")
                                         if t["look"].get(k)}} if any(t["look"].get(k) for k in
                                                                      ("anim_in", "anim_out", "anim_loop")) else {})}
                          for t in service.titles()]}
    if what == "fonts":
        cats: dict[str, list] = {}
        for f in fonts.bundled():
            cats.setdefault(f["category"], []).append(f["name"])
        return {"bundled": cats, "system": list(fonts.SYSTEM_FONTS)}
    if what == "animations":
        return {"items": [{"type": k, "kind": d["kind"], "label": d.get("label", k),
                           "for": d.get("for", ["text", "media"]),
                           **({"dur": d["dur"]} if "dur" in d else {"period": d.get("period")})}
                          for k, d in animations.definitions().items()]}
    if what == "sounds":
        from engine.tools import sfx
        cats = dict(sfx.CATEGORIES) if not isinstance(sfx.CATEGORIES, dict) else sfx.CATEGORIES
        S = timeline_api.sounds()
        return {"categories": cats, "items": [{"id": s["id"], "label": s["label"], "category": s["category"],
                                               "duration": s["params"].get("dur")} for s in S.library()],
                "mine": [{"id": s["id"], "label": s.get("label")} for s in S.mine()]}
    if what == "transitions":
        return {"items": list(model.TRANSITIONS)}
    if what == "voice_presets":
        return {"items": {"auto": "measured on each rush (level, noise, dynamics, timbre) — the default",
                          **service.VOICE_PRESETS}}
    if what == "formats":
        return {"items": {k: {"w": w, "h": h} for k, (w, h) in model.CANVAS_PRESETS.items()}}
    if what == "effects":
        return {"text_fields": {
            "font/size/color/bold/italic/upper": "typography (size in px of a 1080-wide frame)",
            "outline/outline_col": "outline width and colour", "outline2/outline2_col": "second outline",
            "shadow/shadow_col/shadow_blur": "drop shadow", "glow/glow_col": "glow (neon)",
            "extrude/extrude_col": "3D extrusion depth and colour", "color2": "gradient end colour (left→right)",
            "hollow": "outline only", "box/box_alpha": "background box (colour = outline_col)",
            "spacing": "letter spacing", "rotation": "degrees", "opacity": "0..1",
            "hl/mode": "caption highlight colour; mode word|sweep|reveal|dim|none",
            "kw/kw_scale/kw_pop": "keywords (words marked k, see add_captions keywords): colour (empty = hl), "
                                  "relative size (1.55 = the short-form look), bounce when the word appears"},
            "media_fields": {"x/y": "centre in the frame (0..1)", "scale": "1 = fill", "fit": "cover|contain",
                             "rotation": "degrees", "opacity": "0..1", "flip_h/flip_v": "mirror",
                             "border/border_col": "card border around the picture (px of a 1080-wide frame, "
                                                  "e.g. 14 white for a meme card)",
                             "filters": "{brightness, contrast, saturation, temperature} each -1..1",
                             "cutout": "remove background (subject detected)",
                             "follow": "frame follows the subject", "volume": "0..2", "muted": "bool",
                             "box": "{x, y, w, h (fractions of the frame, top-left), r (corner radius px)}: the clip "
                                    "lives in that zone (fill/fit, x/y and scale relative to it, nothing spills "
                                    "out) — split screens, a face in a rounded window"}}
    if what == "scenes":
        return scenes.HELP
    raise HTTPException(404, "Unknown catalog: caption_styles, title_styles, fonts, animations, sounds, "
                             "transitions, voice_presets, formats, effects, scenes.")


def _study_job(job: dict, path: str) -> dict:
    from engine.pipeline.captions import clean_words
    from engine.pipeline.transcribe import transcribe

    def words_of(p: str) -> list[dict]:
        raw = transcribe(p, "large-v3-turbo", "auto", "auto", None)
        return [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)} for w in clean_words(raw)]
    res = reference.study(path, _work(), words_of, lambda t: job.update(message=t))
    key = os.path.basename(os.path.dirname(res["sheets"][0])) if res.get("sheets") else ""
    return {**res, "sheet_urls": [f"/api/agent/reference/{key}/{os.path.basename(p)}" for p in res.get("sheets", [])]}


@router.post("/reference")
def reference_start(body: dict = Body(...)) -> dict:
    """Étudie une vidéo de référence (tâche : transcription comprise)."""
    from engine.timeline import jobs
    path = str(body.get("path") or "").strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(400, f"File not found: {path}")
    return {"job_id": service.start_job("reference", _study_job, path, queue=jobs.TRANSCRIBE)}


@router.get("/reference/{key}/{name}")
def reference_sheet(key: str, name: str):
    path = os.path.join(_work(), "agent", "references", os.path.basename(key), os.path.basename(name))
    if not os.path.isfile(path) or not name.endswith(".jpg"):
        raise HTTPException(404, "Unknown sheet.")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/projects")
def projects() -> dict:
    return {"projects": [p for p in store.list_projects(_work()) if p.get("kind") == "timeline"]}


@router.post("/projects")
def create(body: dict = Body(default={})) -> dict:
    fmt = str(body.get("format") or model.DEFAULT_CANVAS)
    if fmt not in model.CANVAS_PRESETS:
        raise HTTPException(400, f"Unknown format {fmt!r}: {', '.join(model.CANVAS_PRESETS)}.")
    proj = TimelineProject.create(_work(), str(body.get("name") or ""), fmt)
    timeline_api.TIMELINES[proj.id] = proj
    if body.get("language"):
        proj.apply({"settings": {"language": str(body["language"])}})
    return service.summary(proj)


# ------------------------------------------------------------ lecture

@router.get("/jobs/{jid}")
def job(jid: str) -> dict:
    return _call(service.job_view, jid)


@router.get("/{pid}")
def summary(pid: str, captions: bool = Query(False)) -> dict:
    return service.summary(_proj(pid), captions)


@router.get("/{pid}/transcript")
def transcript(pid: str, media: str = Query(""), start: float | None = Query(None), end: float | None = Query(None),
               words: bool = Query(False)) -> dict:
    return _call(service.transcript, _proj(pid), media or None, start, end, words)


@router.get("/{pid}/timeline-text")
def timeline_text(pid: str, words: bool = Query(False)) -> dict:
    out = {"sentences": _call(service.timeline_text, _proj(pid))}
    if words:
        out["words"] = _call(service.timeline_words, _proj(pid))
    return out


@router.post("/{pid}/analyze")
def analyze(pid: str, body: dict = Body(default={})) -> dict:
    proj = _proj(pid)
    if body.get("llm"):
        m = _call(service.resolve_media, proj, body.get("media"), ("video", "audio"))
        _call(service.require_transcript, m)
        jid = service.start_job("analyze", service.analyze_job, proj, m["id"], body, queue=service.queue_for("analyze"))
        return {"job_id": jid}
    return _call(service.plan, proj, body.get("media"), body)


# ------------------------------------------------------------ montage

@router.post("/{pid}/auto")
def auto(pid: str, body: dict = Body(default={})) -> dict:
    proj = _proj(pid)
    jid = service.start_job("auto_edit", service.auto_edit, proj, body)
    return {"job_id": jid}


@router.post("/{pid}/derush")
def derush(pid: str, body: dict = Body(default={})) -> dict:
    """Prises du rush et décisions proposées ; `precise` : chaque prise
    retranscrite seule (tâche de fond, sur le GPU)."""
    proj = _proj(pid)
    if body.get("precise"):
        m = _call(service.resolve_media, proj, body.get("media"), ("video", "audio"))
        _call(service.require_transcript, m)
        return {"job_id": service.start_job("derush", service.derush_job, proj, {**body, "media": m["id"]},
                                            queue=service.queue_for("derush"))}
    return _call(service.derush, proj, body)


def _scenes_job(job: dict, proj, body: dict) -> dict:
    try:
        return scenes.build(proj, body, job)
    except scenes.SceneError as exc:
        raise service.AgentError(scene_error_text(exc)) from exc


@router.get("/{pid}/scenes/last")
def scenes_last(pid: str) -> dict:
    """Le dernier plan de scènes posé (débuts, mises en page) : pour regarder chaque coupe."""
    path = os.path.join(scenes.scenes_dir(_proj(pid)), "last.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise HTTPException(404, "No scenes built yet in this project: call build_scenes.") from None


@router.post("/{pid}/scenes")
def build_scenes(pid: str, body: dict = Body(default={})) -> dict:
    """Scènes du montage (engine/agent/scenes.py). Le contrôle du plan est
    immédiat ; le rendu des zones passe par une tâche de fond."""
    proj = _proj(pid)
    if body.get("check_only"):
        return _call(scenes.build, proj, body)
    # le plan est contrôlé tout de suite : une erreur revient sans attendre de rendu
    _call(scenes.build, proj, {**body, "check_only": True})
    return {"job_id": service.start_job("scenes", _scenes_job, proj, body)}


def _edit_route(name: str, fn):
    def route(pid: str, body: dict = Body(default={})) -> dict:
        return _call(fn, _proj(pid), body)
    route.__name__ = name
    router.add_api_route("/{pid}/" + name, route, methods=["POST"])


for _name, _fn in (("edit", service.build_edit), ("captions", service.captions), ("text", service.add_text),
                   ("clip", service.add_clip), ("audio", service.add_audio), ("update", service.update_clips),
                   ("delete", service.delete), ("cut", service.cut), ("format", service.set_format),
                   ("reframe", service.reframe), ("subject/apply", service.subject_apply),
                   ("markers", service.markers), ("sound", service.optimize_sound)):
    _edit_route(_name, _fn)


@router.delete("/{pid}/captions")
def captions_off(pid: str) -> dict:
    return _call(service.remove_captions, _proj(pid))


@router.post("/{pid}/undo")
def undo(pid: str) -> dict:
    return _call(service.undo, _proj(pid))


@router.post("/{pid}/rename")
def rename(pid: str, body: dict = Body(...)) -> dict:
    return _call(service.rename, _proj(pid), str(body.get("name") or ""))


@router.post("/{pid}/subject")
def subject(pid: str, body: dict = Body(default={})) -> dict:
    """Détecte (détoure) le sujet désigné d'un clic dans un média."""
    proj = _proj(pid)
    m = _call(service.resolve_media, proj, body.get("media"), ("video", "image"))
    _call(service.require_ready, m)
    view = timeline_api.subject_pick(pid, m["id"], {"x": body.get("x", 0.5), "y": body.get("y", 0.4),
                                                    "t": body.get("t", 0)})
    return {"media": m["id"], "subject": view.get("subject")}


# ------------------------------------------------------------ médias

@router.post("/{pid}/import")
def import_media(pid: str, body: dict = Body(default={})) -> dict:
    """Fichiers locaux (`paths`, lus sur place), adresses web (`urls`) ou
    résultats de `search_images` (`refs`), téléchargés dans le projet."""
    proj = _proj(pid)
    added: list[dict] = []
    skipped: list[dict] = []
    if body.get("paths"):
        res = timeline_api.media_paths(pid, {"paths": body["paths"], "recursive": bool(body.get("recursive"))})
        added += res["added"]
        skipped += res["skipped"]
    items = [("ref", r) for r in body.get("refs") or []] + [("url", u) for u in body.get("urls") or []]
    for what, value in items:
        try:
            if what == "ref":
                r = images.result(value)
                url, title, kind = r["url"], r.get("title") or r.get("source", ""), r["kind"]
                fallback = r.get("fallback") or ""
                credit = {k: r.get(k) for k in ("source", "title", "author", "license", "license_url", "page",
                                                 "credit")}
            else:
                url, title, kind, credit, fallback = str(value), "", "", {"source": str(value)}, ""
            mid = model.new_id("m")
            path, name = images.download(url, proj.media_folder(mid), title, kind, fallback)
            proj.add_media(path, name=name, copied=True, mid=mid)
            proj.update_media(mid, credit=credit)
            added.append(service.media_brief(proj.media(mid)))
        except (images.SearchError, OSError, ValueError) as exc:
            skipped.append({"item": value, "reason": str(exc)[:300]})
    return {"added": [service.media_brief(proj.media(a["id"])) if proj.media(a["id"]) else a for a in added],
            "skipped": skipped}


@router.get("/images/search")
def image_search(q: str = Query(...), n: int = Query(12), source: str = Query("auto"),
                 orientation: str = Query(""), license: str = Query("any"), kind: str = Query("photo"),
                 pexels_key: str = Query(""), transparent: bool = Query(False)) -> dict:
    res = _call(images.search, q, _work(), n, source, orientation or None, license, kind,
                globals()["pexels_key"](pexels_key or None), transparent)
    return {**res, "sheet_url": f"/api/agent/images/{res['id']}/sheet.jpg"}


@router.post("/{pid}/capture")
def capture(pid: str, body: dict = Body(...)) -> dict:
    """Capture d'une page web (image, ou vidéo qui défile), importée dans le projet."""
    proj = _proj(pid)
    url = str(body.get("url") or "")
    cv = proj.state["canvas"]
    scroll = float(body.get("scroll") or 0)
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "source.mp4" if scroll > 0 else "source.png")
    t0 = time.time()
    info = _call(visuals.capture_page, url, out, int(body.get("width") or cv["w"]),
                 int(body.get("height") or cv["h"]), scroll, int(cv.get("fps") or 30), 2.5,
                 str(body.get("device") or ""))
    import urllib.parse
    host = urllib.parse.urlparse(url).netloc or "page"
    name = (str(body.get("name") or host).strip()[:60] or host) + os.path.splitext(out)[1]
    proj.add_media(out, name=name, copied=True, mid=mid)
    proj.update_media(mid, credit={"source": url})
    thumb = os.path.join(folder, "visual_preview.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", out, "-frames:v", "1", "-vf", "scale=-2:720", thumb],
                   capture_output=True)
    return {"media": mid, "name": name, **info, "seconds": round(time.time() - t0, 1),
            "preview_url": f"/api/agent/{pid}/visual/{mid}.jpg"}


@router.get("/images/{sid}/sheet.jpg")
def image_sheet(sid: str):
    s = images.SEARCHES.get(sid)
    if not s or not os.path.isfile(s["sheet"]):
        raise HTTPException(404, "Unknown search.")
    return FileResponse(s["sheet"], media_type="image/jpeg")


@router.post("/{pid}/visual")
def visual(pid: str, body: dict = Body(...)) -> dict:
    """Visuel HTML/CSS ou SVG rendu en PNG, importé, et posé si `start` est donné."""
    proj = _proj(pid)
    markup = str(body.get("html") or body.get("svg") or "")
    if not markup.strip():
        raise HTTPException(400, "Give `html` (or `svg`) to draw.")
    markup = _call(service.media_refs, proj, markup)
    cv = proj.state["canvas"]
    w, h = int(body.get("width") or cv["w"]), int(body.get("height") or cv["h"])
    mid = model.new_id("m")
    folder = proj.media_folder(mid)
    transparent = body.get("transparent", True) is not False
    animated = bool(body.get("animated"))
    t0 = time.time()
    base = (str(body.get("name") or "Visuel").strip()[:60] or "Visuel")
    if animated:
        # animation sur mesure : CSS/Web Animations ou window.seek(t), capturée image par image
        length = float(body.get("animation_duration") or body.get("duration") or 2.0)
        out = os.path.join(folder, "source.mov" if transparent else "source.mp4")
        info = _call(visuals.render_animation, markup, w, h, length, out, int(body.get("fps") or cv.get("fps") or 30),
                     transparent)
        os.replace(info["sheet"], os.path.join(folder, "visual_preview.jpg"))
        name = base + (".mov" if transparent else ".mp4")
    else:
        out = os.path.join(folder, "source.png")
        _call(visuals.render, markup, w, h, out, os.path.join(_work(), "agent", "browser"), transparent)
        visuals.thumbnail(out, os.path.join(folder, "visual_preview.jpg"))
        name = base + ".png"
    proj.add_media(out, name=name, copied=True, mid=mid)
    res = {"media": mid, "name": name, "width": w, "height": h, "seconds": round(time.time() - t0, 1),
           "animated": animated, "preview_url": f"/api/agent/{pid}/visual/{mid}.jpg"}
    if body.get("start") is not None:
        _call(service._wait_ready, proj, mid, 120)
        place = {k: v for k, v in body.items() if k not in ("html", "svg", "width", "height", "name", "transparent",
                                                            "animated", "animation_duration", "fps")}
        if animated and body.get("duration") is None:
            place["duration"] = proj.media(mid).get("duration")
        if (w, h) == (cv["w"], cv["h"]):
            place.setdefault("position", "full")
        res["placed"] = _call(service.add_clip, proj, {**place, "media": mid})
    return res


@router.get("/{pid}/visual/{mid}.jpg")
def visual_preview(pid: str, mid: str):
    path = os.path.join(_proj(pid).media_folder(mid), "visual_preview.jpg")
    if not os.path.isfile(path):
        raise HTTPException(404, "No preview for this media.")
    return FileResponse(path, media_type="image/jpeg")


# ------------------------------------------------------ aperçu, export

@router.post("/{pid}/preview")
def preview_start(pid: str, body: dict = Body(default={})) -> dict:
    proj = _proj(pid)
    height = int(body.get("height") or 480)
    if height not in (360, 480, 720):
        raise HTTPException(400, "height: 360, 480 or 720.")
    try:
        return preview.start(proj, height, int(body.get("fps") or 15))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{pid}/preview")
def preview_status(pid: str) -> dict:
    return preview.status(_proj(pid))


@router.get("/{pid}/storyboard")
def storyboard(pid: str, times: str = Query(""), count: int = Query(9), cols: int = Query(3)):
    proj = _proj(pid)
    ts = [float(x) for x in times.replace(";", ",").split(",") if x.strip()] if times else None
    try:
        sb = preview.sheet(proj, ts, count, cols)
    except LookupError:
        raise HTTPException(409, "No up-to-date preview: call preview first (it renders the current timeline).") \
            from None
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(sb["path"], media_type="image/jpeg",
                        headers={"X-Times": ",".join(f"{t:.2f}" for t in sb["times"]), "X-Video": sb["video"],
                                 "X-Rev": str(sb["rev"]), "Cache-Control": "no-store"})


@router.get("/{pid}/media/{mid}/frames")
def media_frames(pid: str, mid: str, times: str = Query(""), count: int = Query(6), cols: int = Query(3)):
    """Images d'un média source, avec une grille 0..1 (où est la personne ?)."""
    proj = _proj(pid)
    m = _call(service.resolve_media, proj, mid, ("video", "image"))
    _call(service.require_ready, m)
    ts = [float(x) for x in times.replace(";", ",").split(",") if x.strip()] if times else None
    try:
        sb = preview.source_sheet(proj, m, ts, count, cols)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(sb["path"], media_type="image/jpeg",
                        headers={"X-Times": ",".join(f"{t:.2f}" for t in sb["times"]), "X-Media": m["id"],
                                 "Cache-Control": "no-store"})


@router.post("/{pid}/export")
def export(pid: str, body: dict = Body(default={})) -> dict:
    proj = _proj(pid)
    opts = {"resolution": body.get("resolution") or "1080p", "fps": body.get("fps"),
            "quality": body.get("quality") or "standard", "codec": body.get("codec") or "h264",
            "loudness": body.get("loudness", (proj.state.get("settings") or {}).get("loudness", False)),
            "folder": body.get("folder") or ""}
    if opts["resolution"] in ("360p", "480p"):
        raise HTTPException(400, "Export resolution: 720p, 1080p, 1440p or 2160p.")
    return timeline_api.export_start(pid, opts)
