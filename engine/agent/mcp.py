r"""Serveur MCP de Montage IA : les outils de l'application pour un agent IA.

Claude Code, Codex, Cursor, Claude Desktop… lancent `MontageIA.exe --mcp`
(ou `python app.py --mcp` depuis les sources) et lui parlent en JSON-RPC sur
l'entrée et la sortie standard (Model Context Protocol, transport stdio).

Ce processus est léger : bibliothèque standard seulement. Il trouve le moteur
de l'application déjà ouvert (fichier d'instance), sinon lance l'application
en arrière-plan (fenêtre cachée, qui se ferme seule quand plus personne ne
s'en sert), puis relaie chaque outil aux routes `/api/agent/...` du moteur.
Les projets sont ceux du studio : ce que l'agent monte s'ouvre dans
l'application (`open_in_app`) et se retouche à la main.

Les opérations longues (préparation, transcription, détourage, aperçu,
export) rendent la main au bout de `wait` secondes au plus (45 par défaut :
certains clients coupent un outil à 60 s) ; l'agent appelle alors `wait`.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SERVER_NAME = "montage-ia"
SERVER_VERSION = "1.0.0"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_WAIT = 45.0
MAX_WAIT = 600.0
FORMATS = ["9:16", "16:9", "1:1", "4:5", "3:4", "21:9"]

INSTRUCTIONS = """\
Montage IA edits videos on this computer (Whisper transcription, cuts, captions, titles, b-roll, sound,
subject cutout, export) inside the user's Montage IA app. Projects built here open in the app's studio
(open_in_app), where the user sees the timeline and can tweak everything by hand.

THE METHOD — seven gates, the way a short-form editor works. At each gate, show the user what you decided and
wait for their ok (answer their notes in plain words, then go on). If they asked for the finished video in one
go, do not stop — but still go through every gate and report each one at the end.

1 FRAMING. create_project(name, format="9:16", media=[absolute paths]) imports, prepares (HDR phone footage is
  converted to SDR), transcribes. view_media on three moments: where the face is, text already burned in, a 16:9
  rush that needs reframing (set_format reframe). If a reference video is given: study_reference and note what
  appears on which word, fonts, caption look, layouts, how often the picture changes. Gate: the frame.
2 ROUGH CUT. derush(media): the takes, cut on the recording's own measured silences, with a proposal per take
  (retake, false start, end said again at the next take's start); each take is re-transcribed on its own.
  Read EVERY take yourself — slate words (« ok », « top »), outtakes, a sentence said twice with other words,
  an unfinished take, a trim that removes a real repetition — keep the best version of each idea, in the order
  that tells the story, the hook first. build_edit(segments=[{media, takes:"2-9,11"}] (or start/end for part
  of a take), rhythm="dynamic", voice="auto"). Gate: the list of kept/dropped takes (why), then the cut.
3 CAPTIONS. Ask which look: net (default: words come with the voice, keywords grow), net_accent (keywords in
  colour), pilule (uppercase pill), bulle (dark pill), or the captions of their reference. Choose keywords (the
  number, the name, the idea of each line — one or two per line, not every word) and fix proper nouns with a
  lexicon. They go in build_scenes(captions={style, keywords, lexicon}) or add_captions. Gate: style + spelling.
4 BEATS AND ASSETS. The beats table in chat, one line per beat: timeline time, the words, what is on screen,
  why — including the moments you leave bare and why. Assets: the real thing first (capture_web of the real
  site or tool, the user's files, search_images for real logos, people, products → import_media), clips the
  user gives with their source; no invented mockup when the real thing exists, nothing generated unless asked.
  Gate: the table, line by line, each asset with its source.
5 OPENING. The first 3 seconds decide whether someone watches to the end: a specific reason to stay, readable
  muted, from the first frame. Propose three different openings with the same true words (problem first,
  result first, recognition first), say which you would pick and why. Gate: A / B / C.
6 SCENES → FINAL. build_scenes(scenes=[…], look, brand, captions) — catalog('scenes') lists layouts and items.
  It refuses a plan with more than 2.2 s of nothing on screen (add an item on a word, or give that scene a
  `hold` reason) or a scene that does not start on a word; then it draws the zones, frames the face in its
  window, places the captions per layout, and reports layout problems (overlaps, contrast, text on the face):
  fix them and run it again (unchanged scenes are reused). preview(cuts=true) and preview(times=[hero
  moments]) and LOOK. Three rounds of fixes at most, then open_in_app for the user to scrub. Music and sound
  effects only if they want them (add_audio; music 14-20 dB under the voice, optimize_sound). Gate: the
  preview; then export(resolution="1080p").
7 DELIVERY. Check the export (duration, the last word is whole), give its path, write the post caption and, for a
  call to action, the exact comment keyword. You never publish or send anything yourself.

CRAFT — what makes a short hold attention
- It always moves: something changes every 2-4 s; a still moment is a decision (`hold`), never an accident.
- You name it, you show it: every tool, product, number or proof said out loud gets its real visual, on its word.
- One idea per scene. Alternate layouts (never the same twice in a row); the face comes back at least every 8 s.
- Anchor to the word, not the sentence: a visual lands on the START of its word. One that lands late is worse
  than none.
- Show the thing, don't label it: the number counts up, the list gets checked, the screenshot is highlighted and
  zoomed, the wrong idea is crossed out. A box with a sentence in it is lazy editing.
- Readable with the sound off. Captions on every frame, keywords stressed. Nothing on the face.
- The call to action is the payoff: the exact keyword, big, in the accent colour. Close the promise of the hook
  before asking for anything.
- Face scenes: rhythm="dynamic" gives multi-camera framings (wide / close / very close, slow push-ins);
  max_gap 0.25-0.35 for tight jump cuts. Other tools still work around the scenes: add_text, add_visual (your own
  HTML, still or animated), add_media_clip (memes/photos as cards, border, anim_in), add_audio (whoosh, pop on an
  entrance; "bip" over a censored word).
- Sound: build_edit measures and treats the voice (voice='auto'); optimize_sound again after adding music.

RULES: segment and transcript times are SOURCE seconds; every other time is TIMELINE seconds. On-screen text in
the language spoken in the video; titles <= 5 words. catalog() gives the names of styles, fonts, animations,
transitions, sounds. Every change is saved at once (the open studio reloads) and can be reverted with undo.
Web images have an unknown licence: prefer source='free' for images the user did not ask for explicitly.
When a tool says something is still running, call wait.
"""

EDIT_PROMPT = """\
Edit this video with the Montage IA tools, from scratch, like a professional short-form editor.

Source: {source}
Format: {format}
Target length: {length}
Style / brief: {brief}
Reference to match: {reference}

Follow the seven gates of the method (see the server instructions), and stop at each one for the user's ok:
1 framing (create_project, view_media, study_reference if a reference is given) -> 2 rough cut (derush, read
every take, build_edit with takes, rhythm dynamic) -> 3 captions (ask the look; keywords and spelling) ->
4 beats table and real assets (capture_web, search_images, the user's files) -> 5 three openings, pick one ->
6 build_scenes, fix what it reports, preview(cuts=true) and hero frames, three rounds of fixes at most ->
export in 1080p -> 7 check the file, give its path, the post caption and the comment keyword.
Keep your messages short: what you decided, why, and what you need from the user.
"""


# ----------------------------------------------------------------- moteur

class EngineError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Engine:
    """Le moteur de l'application : déjà ouvert, sinon lancé en arrière-plan."""

    def __init__(self, find, launch) -> None:
        self._find = find
        self._launch = launch
        self.url: str | None = None
        self._lock = threading.Lock()
        self.launched = False

    def ensure(self) -> str:
        with self._lock:
            if self.url and self._alive(self.url):
                return self.url
            url = self._find()
            if not url:
                self._launch()
                self.launched = True
                t0 = time.time()
                while not url and time.time() - t0 < 120:
                    time.sleep(0.5)
                    url = self._find()
                if not url:
                    raise EngineError(503, "Montage IA did not start (see its log in %LOCALAPPDATA%\\MontageIA\\logs).")
            self.url = url
            return url

    @staticmethod
    def _alive(url: str) -> bool:
        try:
            with urllib.request.urlopen(url + "/api/agent/info", timeout=3) as r:
                return r.status == 200
        except OSError:
            return False

    def request(self, method: str, path: str, body=None, timeout: float = 600.0, raw: bool = False):
        url = self.ensure() + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                if raw:
                    return payload, dict(r.headers)
                return json.loads(payload or b"null")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
            raise EngineError(exc.code, str(detail or exc.reason)) from None
        except urllib.error.URLError as exc:
            self.url = None
            raise EngineError(503, f"Montage IA engine unreachable: {exc.reason}") from None

    def get(self, path: str, **q):
        q = {k: v for k, v in q.items() if v is not None and v != ""}
        return self.request("GET", path + ("?" + urllib.parse.urlencode(q, doseq=True) if q else ""))

    def post(self, path: str, body=None, timeout: float = 600.0):
        return self.request("POST", path, body if body is not None else {}, timeout)


# ------------------------------------------------------------ contexte

class Cancelled(Exception):
    pass


class Context:
    """Un appel d'outil : progression et annulation."""

    def __init__(self, server: "Server", req_id, token) -> None:
        self.server = server
        self.req_id = req_id
        self.token = token
        self.cancelled = False
        self.images: list[tuple[str, str]] = []

    def progress(self, pct: float, message: str = "") -> None:
        if self.token is None:
            return
        self.server.notify("notifications/progress", {"progressToken": self.token, "progress": round(pct, 1),
                                                      "total": 100, "message": message[:200]})

    def sleep(self, s: float) -> None:
        end = time.time() + s
        while time.time() < end:
            if self.cancelled:
                raise Cancelled()
            time.sleep(0.1)

    def image(self, data: bytes, mime: str = "image/jpeg") -> None:
        self.images.append((base64.b64encode(data).decode("ascii"), mime))


def _budget(args: dict) -> float:
    try:
        return max(0.0, min(MAX_WAIT, float(args.get("wait", DEFAULT_WAIT))))
    except (TypeError, ValueError):
        return DEFAULT_WAIT


def poll(ctx: Context, check, budget: float, every: float = 1.0):
    """Appelle `check()` jusqu'à ce qu'il renvoie autre chose que None, ou la fin du budget."""
    t0 = time.time()
    while True:
        res = check()
        if res is not None:
            return res
        if time.time() - t0 >= budget:
            return None
        ctx.sleep(every)


# ----------------------------------------------------------- mise en forme

def _t(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "?"


def fmt_media(m: dict) -> str:
    tr = m.get("transcript") or {}
    bits = [f"{m['id']} {m['kind']} \"{m['name']}\"", f"{_t(m.get('duration'))}s" if m["kind"] != "image" else "",
            f"{m.get('w')}x{m.get('h')}" if m.get("w") else "", f"status={m.get('status')}"
            + (f" {m.get('progress')}%" if m.get("progress") not in (None, 0, 100) else "")]
    if m["kind"] in ("video", "audio") and m.get("has_audio"):
        bits.append("transcript=" + (tr.get("status") or "none")
                    + (f" ({tr.get('count')} words, {tr.get('language')})" if tr.get("status") == "done" else ""))
    if m.get("subject"):
        bits.append(f"subject={m['subject'].get('status')}")
    if m.get("error"):
        bits.append(f"error: {m['error']}")
    if m["kind"] == "image" and m.get("path"):
        bits.append(f"path={m['path']}")
    return " · ".join(b for b in bits if b)


def fmt_clip(c: dict) -> str:
    head = f"{c['id']} [{_t(c['start'])}–{_t(c['end'])}]"
    if c["kind"] == "text":
        s = f"{head} text \"{c.get('text', '')[:80]}\" font={c.get('font')} size={c.get('size')} y={c.get('y')}"
    else:
        s = f"{head} {c['kind']} \"{c.get('name', '')}\""
        if "in" in c:
            s += f" src {_t(c['in'])}–{_t(c['out'])}"
        for k in ("scale", "x", "y", "rotation", "fit", "speed", "volume", "muted", "cutout", "follow", "opacity",
                  "border", "voice"):
            if k in c:
                s += f" {k}={c[k]}"
        if c.get("transition"):
            s += f" transition={c['transition']['type']}"
    for k in ("anim_in", "anim_out", "anim_loop"):
        if c.get(k):
            s += f" {k}={c[k]['type']}"
    return s


def fmt_summary(p: dict) -> str:
    cv = p["canvas"]
    out = [f"Project {p['id']} \"{p['name']}\" · {cv['w']}x{cv['h']} {cv['fps']} fps · background {cv['bg']}"
           + (" (blurred)" if cv.get("blur") else "") + f" · duration {_t(p['duration'])}s · rev {p['rev']}",
           "Media:"]
    out += [f"  {fmt_media(m)}" for m in p["media"]] or ["  (none)"]
    out.append("Tracks (top of the picture first):")
    for t in p["tracks"]:
        label = f"  {t['id']} \"{t['name']}\" {t['kind']}" + (" MAIN (magnetic)" if t["main"] else "")
        if t.get("captions"):
            c = t["captions"]
            out.append(f"{label}: {c['lines']} caption lines, style {c['style']}"
                       + (", word by word" if c.get("word_by_word") else ""))
            continue
        clips = t.get("clips") or []
        out.append(f"{label}: {len(clips)} clip(s)")
        out += [f"    {fmt_clip(c)}" for c in clips[:80]]
        if len(clips) > 80:
            out.append(f"    … {len(clips) - 80} more")
    if p.get("markers"):
        out.append("Markers: " + ", ".join(f"{_t(m['t'])}s {m['label']}" for m in p["markers"]))
    if p.get("export"):
        out.append(f"Last export: {p['export'].get('output')}")
    return "\n".join(out)


def fmt_sentences(sents: list[dict], timeline: bool = False) -> str:
    lines = []
    for s in sents:
        if timeline:
            lines.append(f"[{_t(s['t0'])}–{_t(s['t1'])}] {s['text']}  (sentence #{s['sentence']} of {s['media']})")
        else:
            flags = f" {{{', '.join(s['flags'])}}}" if s.get("flags") else ""
            lines.append(f"#{s['i']} [{_t(s['start'])}–{_t(s['end'])}] (score {s.get('score', 0):+.1f}){flags} "
                         f"{s['text']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ outils

TOOLS: dict[str, dict] = {}


def tool(name: str, title: str, description: str, props: dict, required: list[str] | None = None,
         read_only: bool = False, destructive: bool = False):
    def deco(fn):
        TOOLS[name] = {"name": name, "title": title, "description": description,
                       "inputSchema": {"type": "object", "properties": props, "required": required or [],
                                       "additionalProperties": True},
                       "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
                                       "openWorldHint": name in ("search_images", "import_media")},
                       "fn": fn}
        return fn
    return deco


P = {"type": "string", "description": "Project id (from create_project or list_projects)."}
WAIT = {"type": "number", "description": f"Seconds to wait for the operation before returning (default "
                                         f"{DEFAULT_WAIT:.0f}, max {MAX_WAIT:.0f}). If it is still running, "
                                         f"call wait."}
ANIM = {"anyOf": [{"type": "string"}, {"type": "object"}],
        "description": "Animation name (catalog('animations')) or {type, dur} ({type, speed} for loops), or your "
                       "own: {type:'custom', dur, ease, kf:[[0,{o:0,s:0.6,r:-12}],[0.7,{s:1.08}],[1,{}]]} — keyframes "
                       "at progress 0..1 with o opacity, dx/dy offset (fraction of the frame), s/sx/sy scale, r "
                       "degrees, b blur px, optional curve per keyframe (linear, outQuad, outBack, outElastic, "
                       "outBounce…). Loops: {type:'custom', period, kf} or span:true over the whole clip."}
NUM = {"type": "number"}


@tool("status", "Montage IA status",
      "Start or find the Montage IA engine and report what it can do (fonts, styles, animations, sounds, local "
      "models, image search, visual renderer) and the existing projects. Call it first.", {}, read_only=True)
def t_status(E: Engine, ctx: Context, a: dict) -> str:
    info = E.get("/api/agent/info")
    projects = E.get("/api/agent/projects")["projects"]
    pexels = "yes" if info.get("pexels") or os.environ.get("PEXELS_API_KEY") else \
        "no (a free key in Montage IA > Toolbox > AI agents, or PEXELS_API_KEY, adds stock photos and videos)"
    lines = [f"Montage IA engine: {E.url}" + (" (started in the background for you)" if E.launched else ""),
             f"Work folder: {info['work_dir']}",
             f"Library: {info['fonts']} fonts, {info['caption_styles']} caption styles, {info['title_styles']} "
             f"title styles, {info['animations']} animations, {info['sounds']} sound effects, transitions: "
             f"{', '.join(info['transitions'])}",
             f"Formats: {', '.join(info['formats'])}",
             f"Local LLM for analyze/auto_edit: {'yes' if info['local_llm'] else 'no (rules only)'} · "
             f"subject cutout model: {'yes' if info['subject_model'] else 'no'} · visual renderer: "
             f"{info['visual_renderer'] or 'none found'} · Pexels: {pexels}",
             f"Projects ({len(projects)}):"]
    lines += [f"  {p['id']} \"{p['name']}\" {p['canvas'][0]}x{p['canvas'][1]} {_t(p['duration'])}s, "
              f"{p['media']} media, updated {time.strftime('%Y-%m-%d %H:%M', time.localtime(p['updated']))}"
              for p in projects[:30]]
    return "\n".join(lines)


@tool("list_projects", "List projects", "List the Montage IA timeline projects (newest first).", {},
      read_only=True)
def t_list(E: Engine, ctx: Context, a: dict) -> str:
    ps = E.get("/api/agent/projects")["projects"]
    return "\n".join(f"{p['id']} \"{p['name']}\" {p['canvas'][0]}x{p['canvas'][1]} {_t(p['duration'])}s "
                     f"{p['media']} media" for p in ps) or "No project yet."


def _wait_media(E: Engine, ctx: Context, pid: str, budget: float, transcripts: bool) -> tuple[bool, dict]:
    """Attend la préparation des médias et, avec `transcripts`, leur transcription
    (lancée au passage pour ceux qui viennent d'être prêts)."""
    def check():
        p = E.get(f"/api/agent/{pid}")
        media = p["media"]
        busy = [m for m in media if m["status"] in ("pending", "processing")]
        if transcripts and any(m["status"] == "ready" and m.get("has_audio") and m["kind"] in ("video", "audio")
                               and (m.get("transcript") or {}).get("status", "none") == "none" for m in media):
            _transcribe(E, pid)
            p = E.get(f"/api/agent/{pid}")
            media = p["media"]
        tr = [m for m in media if (m.get("transcript") or {}).get("status") in ("queued", "running")]
        done = len(media) - len(busy)
        ctx.progress(100 * done / max(1, len(media)), f"{len(busy)} media preparing, {len(tr)} transcribing")
        if not busy and (not transcripts or not tr):
            return p
        return None
    p = poll(ctx, check, budget, 1.0)
    return (p is not None), (p or E.get(f"/api/agent/{pid}"))


def _transcribe(E: Engine, pid: str, mids: list[str] | None = None, force: bool = False) -> dict:
    return E.post(f"/api/timeline/{pid}/transcribe", {"media": mids, "force": force} if mids else {"force": force})


@tool("create_project", "Create a project",
      "Create a Montage IA project, import video/image/audio files (absolute local paths, read in place, not "
      "copied), prepare them and start the transcription of the voice (Whisper, local GPU/CPU). Returns the "
      "project summary; if preparation or transcription is still running, call wait.",
      {"name": {"type": "string", "description": "Project name (the export file is named after it)."},
       "format": {"type": "string", "enum": FORMATS, "description": "Frame format (default 9:16 vertical)."},
       "media": {"type": "array", "items": {"type": "string"}, "description": "Absolute paths of files or folders."},
       "transcribe": {"type": "boolean", "description": "Transcribe the media with speech (default true)."},
       "language": {"type": "string", "description": "Spoken language code (fr, en…); detected if omitted."},
       "wait": WAIT}, ["name"])
def t_create(E: Engine, ctx: Context, a: dict) -> str:
    p = E.post("/api/agent/projects", {"name": a.get("name"), "format": a.get("format") or "9:16",
                                       "language": a.get("language")})
    pid = p["id"]
    notes = []
    if a.get("media"):
        res = E.post(f"/api/agent/{pid}/import", {"paths": a["media"]})
        notes += [f"skipped {s.get('path') or s.get('item')}: {s['reason']}" for s in res["skipped"]]
    # une seule enveloppe de temps pour la préparation et la transcription (lancée dès qu'un média est prêt)
    done, p = _wait_media(E, ctx, pid, _budget(a), a.get("transcribe", True) is not False)
    head = f"Created project {pid}." + ("" if done else " Still preparing/transcribing: call wait(project, "
                                                        "what='transcription').")
    return "\n".join([head, *notes, fmt_summary(p)])


@tool("import_media", "Import media",
      "Add media to a project: local files or folders (`paths`, read in place), web files (`urls`: direct links "
      "to images, videos or sounds) or results of search_images (`refs`, downloaded with their credits). Then "
      "place them with add_media_clip / add_audio / build_edit.",
      {"project": P, "paths": {"type": "array", "items": {"type": "string"}},
       "urls": {"type": "array", "items": {"type": "string"}},
       "refs": {"type": "array", "items": {"type": "string"}, "description": "`ref` values from search_images."},
       "transcribe": {"type": "boolean", "description": "Transcribe new media with speech (default true)."},
       "wait": WAIT}, ["project"], destructive=False)
def t_import(E: Engine, ctx: Context, a: dict) -> str:
    pid = a["project"]
    res = E.post(f"/api/agent/{pid}/import", {k: a.get(k) for k in ("paths", "urls", "refs")})
    done, p = _wait_media(E, ctx, pid, _budget(a), False)
    if done and a.get("transcribe", True) is not False:
        _transcribe(E, pid)            # rien ne l'attend ici : get_transcript/wait diront où elle en est
    lines = [f"Added {len(res['added'])} media" + ("" if done else " (still preparing: call wait)") + ":"]
    ids = {m["id"] for m in res["added"]}
    lines += [f"  {fmt_media(m)}" for m in p["media"] if m["id"] in ids]
    lines += [f"  skipped {s.get('path') or s.get('item')}: {s['reason']}" for s in res["skipped"]]
    return "\n".join(lines)


@tool("transcribe", "Transcribe",
      "Transcribe the speech of project media (Whisper large-v3-turbo, word timings). Usually already started "
      "by create_project/import_media. `force` redoes it (e.g. after setting the language).",
      {"project": P, "media": {"type": "array", "items": {"type": "string"}}, "force": {"type": "boolean"},
       "language": {"type": "string"}, "wait": WAIT}, ["project"])
def t_transcribe(E: Engine, ctx: Context, a: dict) -> str:
    pid = a["project"]
    if a.get("language"):
        E.post(f"/api/timeline/{pid}/save", {"settings": {"language": a["language"]}})
    res = _transcribe(E, pid, a.get("media"), bool(a.get("force")))
    done, p = _wait_media(E, ctx, pid, _budget(a), True)
    lines = [("Transcription done." if done else "Transcription running: call wait(project, what='transcription').")]
    lines += [f"  {fmt_media(m)}" for m in p["media"] if m["kind"] in ("video", "audio")]
    if res.get("skipped"):
        lines.append(f"  not transcribed (no sound or not ready): {res['skipped']}")
    return "\n".join(lines)


@tool("get_transcript", "Read the transcript",
      "Numbered sentences of a media's speech with SOURCE times, a hook score (higher = catchier) and flags: "
      "fluff (greetings, intro, outro, call to subscribe) and retake (false start repeated just after). Use the "
      "sentence numbers in build_edit. `words` adds word timings (for precise cuts); narrow with start/end.",
      {"project": P, "media": {"type": "string", "description": "Media id or name (default: the only one)."},
       "start": NUM, "end": NUM, "words": {"type": "boolean"}}, ["project"], read_only=True)
def t_transcript(E: Engine, ctx: Context, a: dict) -> str:
    r = E.get(f"/api/agent/{a['project']}/transcript", media=a.get("media"), start=a.get("start"),
              end=a.get("end"), words="true" if a.get("words") else None)
    m = r["media"]
    out = [f"{m['id']} \"{m['name']}\" · {_t(m['duration'])}s · language {r['language']} · "
           f"{r['sentences_total']} sentences (source times)", fmt_sentences(r["sentences"])]
    if r.get("words"):
        out.append("Words (text start end): " + " ".join(f"{w[0]}@{_t(w[1])}-{_t(w[2])}" for w in r["words"]))
    return "\n".join(out)


@tool("view_media", "Look at a media",
      "See frames of a SOURCE video or image of the project, with a 0..1 grid (yellow lines every 0.5, labels "
      "every 0.2): what the shot shows, where the person is (x, y for subject), whether it already has text "
      "burned in, how to frame it. `times` are source seconds (default: evenly spaced).",
      {"project": P, "media": {"type": "string"}, "times": {"type": "array", "items": {"type": "number"}},
       "count": {"type": "integer", "description": "Number of frames (default 6, max 12)."},
       "columns": {"type": "integer"}}, ["project", "media"], read_only=True)
def t_view(E: Engine, ctx: Context, a: dict) -> str:
    q = urllib.parse.urlencode({"times": ",".join(str(t) for t in a.get("times") or []),
                                "count": a.get("count") or 6, "cols": a.get("columns") or 3})
    data, headers = E.request("GET", f"/api/agent/{a['project']}/media/{urllib.parse.quote(a['media'])}/frames?{q}",
                              raw=True)
    ctx.image(data)
    return (f"Frames of {headers.get('X-Media') or headers.get('x-media')} at source "
            f"{headers.get('X-Times') or headers.get('x-times')} s (grid: x from the left, y from the top, 0..1).")


@tool("analyze", "Analyze for editing",
      "The app's own editing suggestions for a transcribed media: hook sentence (and whether to move it first), "
      "sentences to drop (fluff, retakes, weakest ones to fit max_duration), highlights, short on-screen "
      "keywords, face position. A second opinion for your choices. use_local_llm asks the local Qwen model "
      "(slower).",
      {"project": P, "media": {"type": "string"}, "max_duration": {"type": "number", "description": "Target "
       "length in seconds (drops the weakest sentences)."}, "cold_open": {"type": "boolean", "description":
       "Allow moving a strong later sentence to the start."}, "use_local_llm": {"type": "boolean"}, "wait": WAIT},
      ["project"], read_only=True)
def t_analyze(E: Engine, ctx: Context, a: dict) -> str:
    body = {"media": a.get("media"), "max_duration": a.get("max_duration"), "cold_open": a.get("cold_open"),
            "llm": bool(a.get("use_local_llm"))}
    r = E.post(f"/api/agent/{a['project']}/analyze", body)
    if r.get("job_id"):
        job = _wait_job(E, ctx, r["job_id"], _budget(a))
        if job["status"] != "done":
            return _job_text(job)
        r = job["result"]
    hk = r.get("hook") or {}
    lines = [f"Analysis of {r['name']} ({'local LLM' if r['llm'] else 'rules'}); kept speech if the drops are "
             f"applied: {_t(r['kept_duration'])}s",
             f"Hook: sentence #{hk.get('sentence')} [{_t(hk.get('s'))}–{_t(hk.get('e'))}] title \"{hk.get('text')}\""
             + (" -> move it first (cold open)" if hk.get("cold_open") else "") if hk else "Hook: none found",
             f"Drop sentences: {r['drop_sentences']} (fluff {r['fluff']}, retakes {r['retakes']})",
             "Highlights: " + ("; ".join(f"#{h.get('sentence')} [{_t(h['s'])}–{_t(h['e'])}] {h.get('label', '')}"
                                         for h in r["highlights"]) or "none"),
             "On-screen keywords: " + ("; ".join(f"#{x['sentence']} \"{x['text']}\"" for x in r["texts"]) or "none"),
             "Face: " + (f"x={r['face']['x']} y={r['face']['y']} (0..1 in the source)" if r.get("face") else
                         "not detected")]
    return "\n".join(lines)


def _job_text(job: dict) -> str:
    if job["status"] == "error":
        return f"Failed: {job['message']}"
    return f"Still running ({job.get('pct', 0)}%, {job.get('message', '')}): call wait(what='job', job_id='{job['id']}')."


def _wait_job(E: Engine, ctx: Context, jid: str, budget: float) -> dict:
    def check():
        j = E.get(f"/api/agent/jobs/{jid}")
        ctx.progress(j.get("pct", 0), j.get("message", ""))
        return j if j["status"] != "running" else None
    return poll(ctx, check, budget, 1.0) or E.get(f"/api/agent/jobs/{jid}")


def _sound_lines(report: list | None) -> list[str]:
    out = []
    for e in report or []:
        if e.get("kind") == "voice":
            fx = e.get("fx") or {}
            out.append(f"  sound {e['name']}: voice {_t(e.get('speech_db'))} dBFS, noise {_t(e.get('noise_db'))} dBFS "
                       f"(gap {_t(e.get('snr'))} dB) -> gain {fx.get('gain', 0)} dB, denoise {fx.get('denoise', 0)}, "
                       f"compress {fx.get('compress', 0)}, clarity {fx.get('clarity', 0)}"
                       + (", declip" if fx.get("declip") else "") + (", gate" if fx.get("gate") else ""))
        elif e.get("kind") == "music":
            out.append(f"  sound {e['name']}: music" + (f", volume {e['volume']} under the voice"
                                                         if e.get("volume") is not None else ""))
    return out


def _edit_report(r: dict) -> str:
    lines = [f"Timeline rebuilt: {r['pieces']} shots, {_t(r['duration'])}s (removed {_t(r['removed_seconds'])}s of "
             f"silences/fillers), {r['zooms']} zoomed shots"
             + (f", {r['captions']} caption lines" if r.get("captions") else "") + f" · rev {r['rev']}"]
    lines += _sound_lines(r.get("sound"))
    if r.get("hook"):
        lines.append(f"Hook title: \"{r['hook']}\"")
    if r.get("texts"):
        lines.append(f"Keyword texts: {r['texts']}")
    lines.append("What is said, in TIMELINE time (place texts, b-roll and sounds with these times):")
    lines.append(fmt_sentences(r.get("timeline_text") or [], True))
    return "\n".join(lines)


@tool("auto_edit", "Automatic edit",
      "The app's one-click edit: transcription if needed, analysis, cuts (silences, fillers, fluff, retakes), "
      "hook title, rhythm cuts with punch-in zooms on the face, keyword texts, captions, clean voice and loudness, "
      "markers on highlights. Replaces the main track. Fast way to a first cut; refine it afterwards.",
      {"project": P, "media": {"type": "array", "items": {"type": "string"}},
       "max_duration": {"type": "number"},
       "rhythm": {"type": "string", "enum": ["none", "calm", "normal", "punchy", "dynamic"]},
       "captions": {"anyOf": [{"type": "boolean"}, {"type": "object"}], "description": "true, false or "
                    "{style, word_by_word, words_per_line}"},
       "hook": {"type": "boolean"}, "texts": {"type": "boolean"}, "cold_open": {"type": "boolean"},
       "use_local_llm": {"type": "boolean", "description": "Default true when the local model is installed."},
       "voice": {"type": "string", "enum": ["auto", "clair", "voixoff", "podcast", "radio", "brut", "none"]},
       "wait": WAIT},
      ["project"])
def t_auto(E: Engine, ctx: Context, a: dict) -> str:
    body = {k: a[k] for k in ("media", "max_duration", "rhythm", "captions", "hook", "texts", "cold_open", "voice")
            if a.get(k) is not None}
    body["llm"] = a.get("use_local_llm", True)
    jid = E.post(f"/api/agent/{a['project']}/auto", body)["job_id"]
    job = _wait_job(E, ctx, jid, _budget(a))
    if job["status"] != "done":
        return _job_text(job)
    return _edit_report(job["result"])


SEGMENT = {"type": "object", "properties": {
    "media": {"type": "string", "description": "Media id or name."},
    "takes": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "string"}],
              "description": "Take numbers from derush, in the order to play them: \"2-9,11\" (ends trimmed as "
                             "derush proposed). To keep only part of a take, give start/end instead."},
    "sentences": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "string"}],
                  "description": "Sentence numbers from get_transcript: [12] or [3,4,5] or \"3-8\"."},
    "start": {"type": "number", "description": "Source start (s), instead of sentences. start/end falling inside "
                                               "a word snap to its edge."},
    "end": {"type": "number", "description": "Source end (s)."},
    "duration": {"type": "number", "description": "For an image segment: seconds on screen."},
    "zoom": {"type": "number", "description": "Scale for this segment (1 = fill; 1.15 punch-in); disables auto zoom."},
    "x": NUM, "y": NUM, "speed": NUM,
    "transition": {"type": "string", "description": "Entry transition (catalog('transitions'))."}}}


@tool("build_edit", "Build the edit",
      "Build (replace) the main track from ordered source segments: a hook sentence first, then the kept "
      "sentences. Inside each segment, silences longer than max_gap and filler words are cut from the word "
      "timings. rhythm cuts long shots at sentence ends and alternates punch-in zooms framed on the face "
      "(landscape rushes in a vertical frame are reframed on the face). voice cleans the voice; captions "
      "regenerates the captions. Other tracks are kept (their times do not move). Returns the new timeline "
      "text with TIMELINE times.",
      {"project": P, "segments": {"type": "array", "items": SEGMENT},
       "remove_silences": {"type": "boolean"}, "remove_fillers": {"type": "boolean"},
       "max_gap": {"type": "number", "description": "Longest pause kept between words (default 0.5 s)."},
       "rhythm": {"type": "string", "enum": ["none", "calm", "normal", "punchy", "dynamic"],
                  "description": "dynamic = a shot every 1-3 s cut at clause ends, framings cycling wide / close / "
                                 "medium / very close on the face (multi-camera feel), slow push-ins; normal = cuts "
                                 "at sentence ends, a zoom every other shot."},
       "voice": {"type": "string", "enum": ["auto", "clair", "voixoff", "podcast", "radio", "brut", "none"],
                 "description": "auto (default): measured on each rush — level, noise, dynamics, timbre."},
       "captions": {"anyOf": [{"type": "boolean"}, {"type": "object"}],
                    "description": "true or {style, word_by_word, words_per_line, y} to (re)generate captions."}},
      ["project", "segments"])
def t_build(E: Engine, ctx: Context, a: dict) -> str:
    body = {k: a[k] for k in ("segments", "remove_silences", "remove_fillers", "max_gap", "rhythm", "voice",
                              "captions") if a.get(k) is not None}
    r = E.post(f"/api/agent/{a['project']}/edit", body)
    return _edit_report(r)


def fmt_takes(r: dict) -> str:
    th = r.get("thresholds") or {}
    lines = [f"Takes of {r.get('media')} — {len(r['takes'])} takes, {r.get('method')} split "
             f"(noise {th.get('floor')} dB, voice {th.get('speech')} dB, silence below {th.get('silence')} dB)"
             + (" · transcribed take by take" if r.get("precise") else " · words from the whole-file transcript")]
    for t in r["takes"]:
        dec = "kept" if t["keep"] else "DROP"
        if t.get("trim_out"):
            dec += f", end→{_t(t['trim_out'])}"
        lines.append(f"#{t['n']:02d} [{_t(t['in'])}–{_t(t['out'])}] {dec:18} {t['text'][:110]}"
                     + (f"   ({t['why']})" if t.get("why") else ""))
    lines.append(f"Kept as proposed: {_t(r.get('kept_seconds'))}s of {_t(r.get('duration'))}s. These are PROPOSALS: "
                 "read every take like an editor (slate words like « ok / top / action », outtakes, a sentence "
                 "said twice with different words, an unfinished take, a trim that cuts a real repetition), show "
                 "the list to the user, then build_edit(segments=[{media, takes: \"2-9,11\"}] or start/end for part "
                 "of a take).")
    return "\n".join(lines)


@tool("derush", "Derush (takes)",
      "Split a talking-head rush into its TAKES the way an editor derushes: thresholds measured on the recording "
      "(noise floor and voice level, never a fixed dB), take edges on the waveform (first and last syllable "
      "kept whole), no two takes overlapping (a replayed half-word at a join is impossible), and a proposal per "
      "take: kept, dropped (retake said again just after, false start, take contained in the next one) or end "
      "trimmed (its last words are said again at the start of the next take). precise=true (default) "
      "re-transcribes every take on its own — Whisper on a whole file swallows repeated sentences — and that "
      "take-by-take transcript becomes the media's transcript. Source times.",
      {"project": P, "media": {"type": "string"}, "precise": {"type": "boolean"}, "wait": WAIT}, ["project"])
def t_derush(E: Engine, ctx: Context, a: dict) -> str:
    body = {"media": a.get("media"), "precise": a.get("precise", True) is not False}
    r = E.post(f"/api/agent/{a['project']}/derush", body)
    if r.get("job_id"):
        job = _wait_job(E, ctx, r["job_id"], _budget(a))
        if job["status"] != "done":
            return _job_text(job)
        r = job["result"]
    return fmt_takes(r)


def fmt_scenes(r: dict) -> str:
    m = r.get("metrics") or {}
    lines = [(f"Scenes built · rev {r['rev']} · {r.get('rendered', 0)} drawn, {r.get('reused', 0)} reused, "
              f"{r.get('visuals', 0)} zone visuals, {r.get('videos', 0)} b-roll clips in zones, captions placed on "
              f"{r.get('captions_placed', 0)} lines ({r.get('seconds')} s)") if r.get("rev") is not None else
             ("Scene plan OK — nothing drawn (check_only)." if r.get("ok") else "Scene plan checked (check_only)."),
             f"{m.get('scenes')} scenes over {_t(m.get('duration'))}s (average {_t(m.get('average_scene'))}s), "
             f"{m.get('events')} visual events, longest still moment {_t(m.get('max_gap'))}s."]
    for s in r.get("scenes") or []:
        lines.append(f"#{s['scene']} {s['layout']:8} [{_t(s['start'])}–{_t(s['end'])}] events "
                     f"{', '.join(_t(e) for e in s['events']) or '—'}" + (f"  hold: {s['hold']}" if s.get("hold") else "")
                     + f"\n     « {s['said']} »")
    for k, title in (("problems", "Problems"), ("layout_checks", "Seen in the drawn scenes")):
        if r.get(k):
            lines.append(f"{title}:")
            lines += [f"  - {p}" for p in r[k]]
    if r.get("notes"):
        lines.append("Notes: " + "; ".join(r["notes"]))
    if r.get("rev") is not None:
        lines.append("Next: preview(cuts=true) to look at every scene change, preview(times=[…]) on hero moments; "
                     "fix, rebuild (unchanged scenes are reused), then export.")
    return "\n".join(lines)


@tool("build_scenes", "Build the scenes",
      "Dress the cut like a short-form editor: split the TIMELINE into scenes, each starting on the word that "
      "opens it, with a layout — face (speaker full frame + text overlays, tag, call-to-action card), split "
      "(screen zone on top, face below), face_top, face_box (face in a rounded window on a page), full (page, no "
      "face), world (16:9 décor, the speaker as a moving card) — and items that land on their words: cards, "
      "count-up numbers, stamps, check rows, strike-throughs, circles, underlines, real screenshots with "
      "highlights and zooms, b-roll in the zone, logos, photos, chevrons… (catalog('scenes') lists them all with "
      "their fields). Looks: clean (brand colours, white cards) or paper (paper, grain, tilted cards, serif "
      "accent, handwritten notes). The plan is checked FIRST (scenes on words, full coverage, no still moment "
      "longer than 2.2 s unless the scene has `hold`); then zones are drawn (cached), the face is framed in its "
      "window, and the captions go where each layout wants them. captions={style, keywords, lexicon} rebuilds "
      "and places the captions in the same step. check_only=true checks without drawing. Run it again after any "
      "change: unchanged scenes are reused.",
      {"project": P, "scenes": {"type": "array", "items": {"type": "object"}},
       "look": {"type": "string", "enum": ["clean", "paper"]},
       "brand": {"type": "object", "description": "{bg, ink, accent, accent_ink, muted, card} colours; font "
                                                  "overrides text/heavy/display/serif/hand."},
       "captions": {"type": "object", "description": "{style (e.g. net, net_accent, pilule, bulle, encre), "
                                                     "keywords, lexicon, words_per_line, y…}"},
       "locale": {"type": "string", "description": "Number format of count-ups (fr-FR, en-US…)."},
       "check_only": {"type": "boolean"}, "force": {"type": "boolean", "description": "Draw even if the plan "
                                                                                       "check found problems."},
       "wait": WAIT}, ["project", "scenes"])
def t_scenes(E: Engine, ctx: Context, a: dict) -> str:
    body = {k: a[k] for k in ("scenes", "look", "brand", "captions", "locale", "check_only", "force")
            if a.get(k) is not None}
    r = E.post(f"/api/agent/{a['project']}/scenes", body)
    if r.get("job_id"):
        job = _wait_job(E, ctx, r["job_id"], _budget(a))
        if job["status"] != "done":
            return _job_text(job)
        r = job["result"]
    return fmt_scenes(r)


@tool("get_timeline", "Read the timeline",
      "The project as it stands: format, media (with file paths), tracks (top of the picture first) and their "
      "clips with ids and TIMELINE times, markers. `text` adds what is said with timeline times; `words` adds "
      "every word at its timeline time (to put a visual, a sound or a title exactly on the word it illustrates); "
      "`captions` lists every caption line.",
      {"project": P, "text": {"type": "boolean"}, "words": {"type": "boolean"}, "captions": {"type": "boolean"}},
      ["project"], read_only=True)
def t_timeline(E: Engine, ctx: Context, a: dict) -> str:
    p = E.get(f"/api/agent/{a['project']}", captions="true" if a.get("captions") else None)
    out = [fmt_summary(p)]
    if a.get("text") or a.get("words"):
        r = E.get(f"/api/agent/{a['project']}/timeline-text", words="true" if a.get("words") else None)
        if a.get("text"):
            out += ["What is said (timeline time):", fmt_sentences(r["sentences"], True)]
        if a.get("words"):
            out.append("Words (timeline start): " + " ".join(f"{w['text']}@{_t(w['t0'])}" for w in r["words"]))
    return "\n".join(out)


@tool("add_captions", "Captions",
      "(Re)generate the captions from the voice on the timeline, linked to the words (they follow later cuts). "
      "style: a caption style name (catalog('caption_styles'), e.g. hype, impact, karaoke…). word_by_word shows "
      "one word at a time. y moves them (0 top … 1 bottom). remove=true deletes them.",
      {"project": P, "style": {"type": "string"}, "word_by_word": {"type": "boolean"},
       "words_per_line": {"type": "integer"}, "max_chars": {"type": "integer"}, "emojis": {"type": "boolean"},
       "y": NUM, "size": NUM, "font": {"type": "string"}, "color": {"type": "string"},
       "highlight": {"type": "string", "description": "Highlight colour of the spoken word."},
       "keywords": {"type": "array", "items": {"type": "string"},
                    "description": "Words to stress in the captions (numbers, names, the idea of each sentence): "
                                   "they grow and pop in the short-form styles (net, net_accent…). Case, accents, "
                                   "punctuation and elisions ignored (\"ingénieur\" matches « d'ingénieur, »)."},
       "lexicon": {"type": "object", "description": "Spelling fixes for the captions, one word → one word "
                                                    "({\"cloud\": \"Claude\"}); the transcript is not changed."},
       "kw": {"type": "string", "description": "Keyword colour (default: the style's)."},
       "kw_scale": NUM, "kw_pop": {"type": "boolean"},
       "uppercase": {"type": "boolean"}, "remove": {"type": "boolean"}}, ["project"])
def t_captions(E: Engine, ctx: Context, a: dict) -> str:
    pid = a["project"]
    if a.get("remove"):
        r = E.request("DELETE", f"/api/agent/{pid}/captions")
        return f"Removed {r['removed']} caption lines · rev {r['rev']}"
    body = {k: a[k] for k in ("style", "word_by_word", "words_per_line", "max_chars", "emojis", "y", "size", "font",
                              "color", "keywords", "lexicon", "kw", "kw_scale", "kw_pop") if a.get(k) is not None}
    if a.get("highlight"):
        body["hl"] = a["highlight"]
    if a.get("uppercase") is not None:
        body["upper"] = bool(a["uppercase"])
    r = E.post(f"/api/agent/{pid}/captions", body)
    return f"{r['lines']} caption lines, style {r['style']} · rev {r['rev']}"


TEXT_PROPS = {
    "project": P, "text": {"type": "string"}, "start": {"type": "number", "description": "Timeline seconds."},
    "duration": NUM, "style": {"type": "string", "description": "Title style (catalog('title_styles')) or a caption "
                                                                 "style name."},
    "x": NUM, "y": {"type": "number", "description": "Vertical centre, 0 top … 1 bottom."},
    "font": {"type": "string"}, "size": NUM, "color": {"type": "string"}, "color2": {"type": "string"},
    "outline": NUM, "outline_col": {"type": "string"}, "upper": {"type": "boolean"}, "box": {"type": "boolean"},
    "effects": {"type": "object", "description": "Any text look fields (catalog('effects')): glow, glow_col, "
                                                 "extrude, shadow_blur, spacing, italic, rotation…"},
    "anim_in": ANIM, "anim_out": ANIM, "anim_loop": ANIM, "track": {"type": "string"}}


@tool("add_text", "Add a text",
      "Put a text on screen (hook title, keyword, list item, call to action) at a TIMELINE time, with a title "
      "style and/or your own look, effects and animations. Returns the clip id.",
      TEXT_PROPS, ["project", "text", "start"])
def t_text(E: Engine, ctx: Context, a: dict) -> str:
    body = {k: v for k, v in a.items() if k not in ("project",)}
    r = E.post(f"/api/agent/{a['project']}/text", body)
    return f"Text {r['clip']} on track {r['track']} · rev {r['rev']}" + "".join("\n" + w for w in r["warnings"])


@tool("search_images", "Search images",
      "Search images to illustrate what is said: the whole web (memes, logos, product shots, screenshots, "
      "illustrations — licence unknown), Openverse and Wikimedia Commons (Creative Commons / public domain), "
      "Pexels photos and stock videos when a Pexels key is set. transparent=true finds logos, stickers and "
      "cut-out characters with a transparent background. Returns a numbered contact sheet to look at and each "
      "result's `ref`, size, source and licence. Import the chosen ones with import_media(refs=[…]). "
      "source='free' keeps only openly licensed images.",
      {"query": {"type": "string"}, "count": {"type": "integer", "description": "1-24 (default 12)."},
       "source": {"type": "string", "enum": ["auto", "free", "web", "all", "openverse", "wikimedia", "pexels"]},
       "transparent": {"type": "boolean", "description": "Transparent background (logos, stickers)."},
       "orientation": {"type": "string", "enum": ["portrait", "landscape", "square"]},
       "license": {"type": "string", "enum": ["any", "commercial"], "description": "commercial: usable in a "
                   "monetised video, modifications allowed."},
       "type": {"type": "string", "enum": ["photo", "video"], "description": "video needs a Pexels key."}},
      ["query"], read_only=True)
def t_search(E: Engine, ctx: Context, a: dict) -> str:
    r = E.get("/api/agent/images/search", q=a["query"], n=a.get("count") or 12, source=a.get("source") or "auto",
              orientation=a.get("orientation"), license=a.get("license") or "any", kind=a.get("type") or "photo",
              pexels_key=os.environ.get("PEXELS_API_KEY"), transparent="true" if a.get("transparent") else None)
    data, _ = E.request("GET", r["sheet_url"], raw=True)
    ctx.image(data)
    lines = [f"{len(r['results'])} results for \"{r['query']}\" (numbered on the image):"]
    for x in r["results"]:
        size = f" {x.get('w')}x{x.get('h')}" if x.get("w") else ""
        lines.append(f"  {x['n']}. ref={x['ref']} {x['source']} {x['kind']}{size}"
                     + (f" {x.get('duration')}s" if x.get("duration") else "")
                     + (f" · {x['site']}" if x.get("site") else f" · {x.get('title', '')[:60]} — {x.get('author', '')[:40]}")
                     + f" · {x.get('license')}")
    if r.get("errors"):
        lines.append("Unavailable sources: " + "; ".join(f"{k}: {v}" for k, v in r["errors"].items()))
    return "\n".join(lines)


PLACE = {"position": {"type": "string", "enum": ["full", "top_band", "center_band", "bottom_band", "center", "top",
                                                 "bottom", "left", "right", "top_left", "top_right", "bottom_left",
                                                 "bottom_right"],
                      "description": "full = fills the frame (default); top_band = whole picture across the "
                                     "width at the top (a screen recording above the speaker); center_band / "
                                     "bottom_band likewise; others = inset at half size."},
         "x": NUM, "y": NUM, "scale": NUM, "fit": {"type": "string", "enum": ["cover", "contain"]},
         "opacity": NUM, "rotation": NUM,
         "border": {"type": "number", "description": "Card border around the picture, px of a 1080-wide frame "
                                                     "(e.g. 14 for a meme card)."},
         "border_col": {"type": "string", "description": "Border colour, #RRGGBB (default white)."},
         "anim_in": ANIM, "anim_out": ANIM, "anim_loop": ANIM,
         "transition": {"type": "string"}}


@tool("add_visual", "Draw a visual (still or animated)",
      "Draw your own graphic in HTML/CSS/JS (or SVG): title card, stacked editorial title, numbered list, big "
      "number, quote, lower third, chart, arrow, sticker, meme card, pixel art… Rendered at the frame size "
      "(1080x1920 for 9:16) with a transparent background by a headless browser; the app's fonts are available "
      "by name (font-family:'Instrument Serif', 'DM Serif Display', 'Anton', 'Montserrat Black'…) and the "
      "project's images by id (<img src=\"media:m1234abcd\"> — a meme, logo or photo turned into a card). "
      "animated=true captures an ANIMATION frame by frame into a transparent video: use CSS @keyframes / "
      "transitions with delays (they are frozen at each instant), or define window.seek = t => {…} to draw time "
      "t yourself (canvas, counters, typing). animation_duration = its length (≤ 20 s). Give `start` (timeline "
      "s) to place it at once. Returns preview frames.",
      {"project": P, "html": {"type": "string", "description": "HTML fragment or document; body is the frame, "
                                                              "position elements absolutely."},
       "svg": {"type": "string"}, "name": {"type": "string"}, "width": {"type": "integer"},
       "height": {"type": "integer"}, "transparent": {"type": "boolean"},
       "animated": {"type": "boolean"}, "animation_duration": NUM, "fps": {"type": "integer"},
       "start": NUM, "duration": NUM, **PLACE},
      ["project"])
def t_visual(E: Engine, ctx: Context, a: dict) -> str:
    body = {k: v for k, v in a.items() if k != "project"}
    r = E.post(f"/api/agent/{a['project']}/visual", body, timeout=600)
    data, _ = E.request("GET", r["preview_url"], raw=True)
    ctx.image(data)
    s = (f"{'Animated visual' if r.get('animated') else 'Visual'} {r['media']} \"{r['name']}\" "
         f"{r['width']}x{r['height']} (drawn in {r['seconds']}s)")
    if r.get("placed"):
        p = r["placed"]
        s += f", placed as clip {p['clip']} [{_t(p['start'])}–{_t(p['end'])}] on track {p['track']}"
    else:
        s += ". Place it with add_media_clip(media=…)."
    return s


@tool("capture_web", "Capture a web page",
      "Screenshot a web page (the app, website, GitHub repo, article, tweet you talk about) at the frame size, "
      "or with scroll=N seconds a smooth scrolling VIDEO of it, like a screen recording. The result is a project "
      "media to place with add_media_clip (e.g. position top_band above the speaker).",
      {"project": P, "url": {"type": "string"}, "scroll": {"type": "number", "description": "Scroll duration "
                                                                                             "(s); 0 = still image."},
       "device": {"type": "string", "enum": ["phone", "desktop"], "description": "phone (mobile site, readable "
                  "in a vertical frame — default for 9:16) or desktop (1440 px wide layout, e.g. width 1080 "
                  "height 700 for a top band)."},
       "width": {"type": "integer"}, "height": {"type": "integer", "description": "Default: the frame size."},
       "name": {"type": "string"}}, ["project", "url"], read_only=False)
def t_capture(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/capture", {k: v for k, v in a.items() if k != "project"}, timeout=300)
    data, _ = E.request("GET", r["preview_url"], raw=True)
    ctx.image(data)
    return (f"Captured {r['media']} «{r['name']}» ({r['kind']}, {r['width']}x{r['height']}"
            + (f", {r['duration']}s scrolling {r['scroll_px']}px" if r["kind"] == "video" else "")
            + f", {r['seconds']}s). Place it with add_media_clip.")


@tool("add_media_clip", "Place an image or video",
      "Place an image or video of the project above the main track at a TIMELINE time: b-roll covering the "
      "speaker (position full), an inset (top, bottom_right…), a logo. Video b-roll is muted unless keep_audio. "
      "Animations and transitions from the catalog; cutout=true removes the background (after subject).",
      {"project": P, "media": {"type": "string"}, "start": NUM, "duration": NUM,
       "in": {"type": "number", "description": "Source start inside a video (s)."},
       "keep_audio": {"type": "boolean"}, "volume": NUM, "cutout": {"type": "boolean"}, "track": {"type": "string"},
       **PLACE}, ["project", "media", "start"])
def t_clip(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/clip", {k: v for k, v in a.items() if k != "project"})
    return f"Clip {r['clip']} [{_t(r['start'])}–{_t(r['end'])}] on track {r['track']} · rev {r['rev']}"


@tool("add_audio", "Add music or a sound",
      "Add a sound effect from the library (`sound`, ids in catalog('sounds')), or the sound of a project media "
      "(`media`: music file, voice-over) at a TIMELINE time. Music: volume 0.1-0.2, fade_in/fade_out, "
      "loop_to_end repeats it to the end of the edit.",
      {"project": P, "sound": {"type": "string"}, "media": {"type": "string"}, "start": NUM, "duration": NUM,
       "in": NUM, "volume": {"type": "number", "description": "0..2 (1 = unchanged)."}, "fade_in": NUM,
       "fade_out": NUM, "loop_to_end": {"type": "boolean"}, "track": {"type": "string"}}, ["project", "start"])
def t_audio(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/audio", {k: v for k, v in a.items() if k != "project"})
    return f"Audio clip {r['clip']} [{_t(r['start'])}–{_t(r['end'])}] on track {r['track']} · rev {r['rev']}"


@tool("optimize_sound", "Optimize the sound",
      "Measure the sound of every rush on the timeline (voice level, background noise, dynamics, sibilance, "
      "timbre, clipping; music level) and set the voice processing from it: working level, noise reduction and "
      "gate from the measured noise, compression, de-esser, clarity/warmth, declipping; background music lowered "
      "under the voice; export loudness -14 LUFS. build_edit and auto_edit already do it (voice='auto'); call it "
      "again after adding music or voice-overs.",
      {"project": P, "clips": {"type": "array", "items": {"type": "string"},
                               "description": "Only these clip ids (default: the whole timeline)."}}, ["project"])
def t_sound(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/sound", {"clips": a.get("clips")})
    return "\n".join([f"Sound optimized on {r['clips']} clip(s), export at -14 LUFS · rev {r['rev']}",
                      *_sound_lines(r["report"])])


@tool("update_clips", "Change clips",
      "Change clips by id (from get_timeline): start/duration (not on the magnetic main track), in, speed, text, "
      "style, look fields, effects, position/x/y/scale/fit/opacity/rotation/border/border_col, "
      "volume/muted/fade_in/fade_out, "
      "cutout/follow, transition, anim_in/anim_out/anim_loop, hidden, track.",
      {"project": P, "clips": {"type": "array", "items": {"type": "object"},
                               "description": "[{id, …fields}]"}}, ["project", "clips"])
def t_update(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/update", {"clips": a["clips"]})
    return f"Updated {r['updated']} clip(s) · rev {r['rev']}" + "".join("\n" + n for n in r["notes"])


@tool("delete_clips", "Delete clips",
      "Delete clips by id (the main track closes the gap).", {"project": P, "ids": {"type": "array",
                                                                                   "items": {"type": "string"}}},
      ["project", "ids"], destructive=True)
def t_delete(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/delete", {"ids": a["ids"]})
    return f"Deleted {r['deleted']} clip(s) · rev {r['rev']}"


@tool("cut_range", "Cut a range",
      "Remove the TIMELINE interval [start, end] on every track; what follows moves back (captions follow).",
      {"project": P, "start": NUM, "end": NUM}, ["project", "start", "end"], destructive=True)
def t_cut(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/cut", {"start": a["start"], "end": a["end"]})
    return f"Removed {_t(r['removed'])}s, timeline now {_t(r['duration'])}s · rev {r['rev']}"


@tool("set_format", "Format and reframing",
      "Change the frame format (9:16, 16:9, 1:1, 4:5…), fps, background colour or blurred background, and "
      "reframe the main shots on the face (reframe=face) or centre them.",
      {"project": P, "format": {"type": "string", "enum": FORMATS}, "fps": {"type": "integer"},
       "background": {"type": "string"}, "blur_background": {"type": "boolean"},
       "reframe": {"type": "string", "enum": ["face", "center", "none"]}}, ["project"])
def t_format(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/format", {k: v for k, v in a.items() if k != "project"})
    cv = r["canvas"]
    return f"Frame {cv['w']}x{cv['h']} {cv['fps']} fps, {r['reframed']} shot(s) reframed · rev {r['rev']}"


@tool("subject", "Subject: cutout, follow, frame",
      "Detect the person (or subject) at point x,y (0..1 in the source picture) at source time t, frame by "
      "frame (local MODNet model, about 2x the media duration on CPU), then on its clips: remove_background "
      "(the frame background or a colour shows behind), follow (the frame follows the subject), frame (zoom "
      "and centre on the subject). Call again with the same options after wait if it was still running.",
      {"project": P, "media": {"type": "string"}, "x": NUM, "y": NUM, "t": NUM,
       "remove_background": {"type": "boolean"}, "follow": {"type": "boolean"}, "frame": {"type": "boolean"},
       "background": {"type": "string", "description": "Frame background colour behind the cut-out subject."},
       "blur_background": {"type": "boolean"}, "clips": {"type": "array", "items": {"type": "string"}},
       "wait": WAIT}, ["project", "media"])
def t_subject(E: Engine, ctx: Context, a: dict) -> str:
    pid = a["project"]
    p = E.get(f"/api/agent/{pid}")
    m = next((x for x in p["media"] if x["id"] == a["media"] or x["name"] == a["media"]
              or a["media"].lower() in x["name"].lower()), None)
    sub0 = (m or {}).get("subject") or {}
    st = sub0.get("status")
    same = st in ("done", "queued", "running") and (a.get("x") is None or (
        abs(float(a["x"]) - float(sub0.get("x", -9))) < 0.03 and abs(float(a.get("y", 0.4)) - float(sub0.get("y", -9)))
        < 0.03))
    if not same:
        r = E.post(f"/api/agent/{pid}/subject", {"media": a["media"], "x": a.get("x", 0.5), "y": a.get("y", 0.4),
                                                 "t": a.get("t", 0)})
        mid = r["media"]
    else:
        mid = m["id"]

    def check():
        mm = next(x for x in E.get(f"/api/agent/{pid}")["media"] if x["id"] == mid)
        sub = mm.get("subject") or {}
        ctx.progress(float(sub.get("progress") or 0), "subject detection")
        return sub if sub.get("status") in ("done", "error") else None
    sub = poll(ctx, check, _budget(a), 1.5)
    if not sub:
        return ("Subject detection running: call wait(project, what='subject', media) then subject again with "
                "the same options but without x/y.")
    if sub["status"] == "error":
        return f"Subject detection failed: {sub.get('error')}"
    opts = {k: a[k] for k in ("remove_background", "follow", "frame", "background", "blur_background", "clips")
            if a.get(k) is not None}
    if not opts:
        return f"Subject detected on {mid}. Apply it with subject(remove_background/follow/frame)."
    r = E.post(f"/api/agent/{pid}/subject/apply", {"media": mid, **opts})
    return f"Subject applied to {len(r['clips'])} clip(s) ({r['framed']} framed) · rev {r['rev']}"


@tool("catalog", "Catalog",
      "Names you can use: caption_styles, title_styles, fonts, animations (in/out/loop, text/media), sounds "
      "(sound effect ids by category), transitions, voice_presets, formats, effects (text and media fields), "
      "scenes (layouts and items of build_scenes, with their fields).",
      {"what": {"type": "string", "enum": ["caption_styles", "title_styles", "fonts", "animations", "sounds",
                                           "transitions", "voice_presets", "formats", "effects", "scenes"]}},
      ["what"], read_only=True)
def t_catalog(E: Engine, ctx: Context, a: dict) -> str:
    what = a["what"]
    r = E.get(f"/api/agent/catalog/{what}")
    if what == "scenes":
        out = [r["how"], "", "LAYOUTS"] + [f"- {k}: {v}" for k, v in r["layouts"].items()]
        out += ["", "SCENE FIELDS"] + [f"- {k}: {v}" for k, v in r["scene_fields"].items()]
        out += ["", "ITEMS (type: fields)"] + [f"- {k}: {v}" for k, v in r["items"].items()]
        out += ["", "COMMON: " + r["common"], "LOOK: " + r["look"], "BRAND: " + r["brand"],
                "CAPTIONS: " + r["captions"]]
        return "\n".join(out)
    if what in ("caption_styles", "title_styles"):
        return "\n".join(f"{x['name']}: {x['label']} — {x['hint']} [{x['group']}] font {x.get('font')}"
                         + (f" anims {x['anims']}" if x.get("anims") else "") for x in r["items"])
    if what == "fonts":
        return "\n".join([f"{k}: {', '.join(v)}" for k, v in r["bundled"].items()]
                         + [f"system: {', '.join(r['system'])}"])
    if what == "animations":
        rows = {}
        for x in r["items"]:
            rows.setdefault(x["kind"], []).append(f"{x['type']}" + ("" if x["for"] == ["text", "media"] else
                                                                    f"({'/'.join(x['for'])} only)"))
        return "\n".join(f"{k}: {', '.join(v)}" for k, v in rows.items())
    if what == "sounds":
        rows = {}
        for s in r["items"]:
            rows.setdefault(s["category"], []).append(f"{s['id']} ({s['label']})")
        mine = r.get("mine") or []
        return "\n".join([f"{r['categories'].get(k, k)}: {', '.join(v)}" for k, v in rows.items()]
                         + ([f"user's own sounds: {', '.join(m['id'] for m in mine)}"] if mine else []))
    return json.dumps(r, ensure_ascii=False, indent=1)


@tool("preview", "Preview (storyboard)",
      "Render the current timeline small (same pipeline as the export, cached per revision) and return a "
      "storyboard image of frames at `times` (timeline seconds) or `count` evenly spaced frames. Look at it to "
      "check framing, texts, captions and visuals before exporting. cuts=true shows a frame just before and just "
      "after every scene change of build_scenes (look for: a cropped face, text over the mouth or the captions, "
      "a black or empty frame, a doubled title). Also returns the preview MP4 path.",
      {"project": P, "times": {"type": "array", "items": {"type": "number"}}, "count": {"type": "integer"},
       "cuts": {"type": "boolean"}, "columns": {"type": "integer"},
       "height": {"type": "integer", "enum": [360, 480, 720]}, "wait": WAIT},
      ["project"], read_only=True)
def t_preview(E: Engine, ctx: Context, a: dict) -> str:
    pid = a["project"]
    if a.get("cuts") and not a.get("times"):
        try:
            starts = [s for s in E.get(f"/api/agent/{pid}/scenes/last").get("starts") or [] if s > 0.05]
        except EngineError:
            starts = []
        if starts:
            step = max(1, -(-len(starts) // 12))           # 24 images au plus
            a = {**a, "times": [round(t + d, 2) for s in starts[::step] for t, d in ((s, -0.1), (s, 0.1))],
                 "columns": a.get("columns") or 4}
    st = E.post(f"/api/agent/{pid}/preview", {"height": a.get("height") or 480})

    def check():
        s = E.get(f"/api/agent/{pid}/preview")
        ctx.progress(s.get("pct", 0), "rendering the preview")
        return s if s.get("status") in ("done", "error") else None
    st = st if st.get("status") == "done" else poll(ctx, check, _budget(a), 1.0)
    if not st:
        return "Preview still rendering: call preview again (it continues where it is)."
    if st["status"] == "error":
        return f"Preview failed: {st.get('message')}"
    times = ",".join(str(t) for t in a.get("times") or [])
    data, headers = E.request("GET", f"/api/agent/{pid}/storyboard?" + urllib.parse.urlencode(
        {"times": times, "count": a.get("count") or 9, "cols": a.get("columns") or 3}), raw=True)
    ctx.image(data)
    return (f"Storyboard of rev {headers.get('X-Rev') or headers.get('x-rev')} at "
            f"{headers.get('X-Times') or headers.get('x-times')} s. Preview video: "
            f"{headers.get('X-Video') or headers.get('x-video')}")


@tool("export", "Export",
      "Render the final video (MP4 H.264, AAC) with everything on the timeline. Returns the file path when done "
      "(call wait(what='export') if still rendering).",
      {"project": P, "resolution": {"type": "string", "enum": ["720p", "1080p", "1440p", "2160p"]},
       "fps": {"type": "integer"}, "quality": {"type": "string", "enum": ["low", "standard", "high"]},
       "folder": {"type": "string", "description": "Output folder (default: the user's Videos/Montage IA)."},
       "wait": WAIT}, ["project"])
def t_export(E: Engine, ctx: Context, a: dict) -> str:
    pid = a["project"]
    E.post(f"/api/agent/{pid}/export", {k: v for k, v in a.items() if k not in ("project", "wait")})
    return _export_wait(E, ctx, pid, _budget(a))


def _export_wait(E: Engine, ctx: Context, pid: str, budget: float) -> str:
    def check():
        s = E.get(f"/api/timeline/{pid}/export")
        ctx.progress(s["task"].get("pct", 0), s["task"].get("message", ""))
        return s if s["task"].get("status") != "running" else None
    s = poll(ctx, check, budget, 1.0)
    if not s:
        return "Export running: call wait(project, what='export')."
    if s["task"]["status"] != "done":
        return f"Export {s['task']['status']}: {s['task'].get('message')}"
    ex = s.get("export") or {}
    return (f"Exported: {ex.get('output')} ({ex.get('width')}x{ex.get('height')}, {_t(ex.get('duration'))}s, "
            f"{round((ex.get('size') or 0) / 1e6, 1)} MB)")


def _reference_text(E: Engine, ctx: Context, r: dict) -> str:
    for url in r.get("sheet_urls") or []:
        data, _ = E.request("GET", url, raw=True)
        ctx.image(data)
    s, sp, ld, v = r.get("shots") or {}, r.get("speech") or {}, r.get("loudness") or {}, r.get("voice") or {}
    lines = [f"Reference {r['width']}x{r['height']} {r['fps']} fps, {r['duration']}s — contact sheets above "
             f"(one frame every ~{round(r['duration'] / max(1, 3 * 20), 1)} s, time in the corner).",
             f"Picture changes: {s.get('count')} shots, median {s.get('median')}s, mean {s.get('mean')}s "
             f"(shortest {s.get('shortest')}s, longest {s.get('longest')}s); changes at "
             + ", ".join(f"{c}" for c in (r.get("cuts") or [])[:60])]
    if sp:
        lines.append(f"Speech: {sp['words_per_second']} words/s, gaps between words median {sp['gap_median']}s, "
                     f"p90 {sp['gap_p90']}s, {sp['gaps_over_300ms']} pauses > 0.3 s; "
                     f"{r.get('cuts_on_word_starts', 0)}/{len(r.get('cuts') or [])} picture changes land on a word "
                     f"start (visuals synced to the words).")
    if ld:
        lines.append(f"Sound: {ld.get('lufs')} LUFS, loudness range {ld.get('lra')} LU, peak {ld.get('peak')} dBFS"
                     + (f"; level between words {v.get('gap_level_db')} dBFS"
                        + (" -> music or room tone under the voice" if v.get("music_or_room_under_voice") else
                           " -> silence between words") if v else ""))
    if r.get("text"):
        lines.append("Transcript: " + r["text"][:3000])
    lines.append("To match it: read the typography, layouts (full frame, screen band at the top, full-screen "
                 "cards), how often the picture changes and what appears on which word, then use the same "
                 "fonts/styles (catalog), rhythm='dynamic' if shots are short, visuals timed on keywords.")
    return "\n".join(lines)


@tool("study_reference", "Study a reference video",
      "Analyse a video whose editing you must match (a creator the user likes): contact sheets of its frames "
      "(look at them: fonts, caption style and position, layouts, graphics, memes, colours), shot rhythm, speech "
      "pace and pauses kept, whether visuals change on word starts, loudness and music under the voice, and "
      "its transcript. Takes about the transcription time of the video.",
      {"path": {"type": "string", "description": "Absolute path of the reference video."}, "wait": WAIT},
      ["path"], read_only=True)
def t_reference(E: Engine, ctx: Context, a: dict) -> str:
    jid = E.post("/api/agent/reference", {"path": a["path"]})["job_id"]
    job = _wait_job(E, ctx, jid, _budget(a))
    if job["status"] != "done":
        return _job_text(job)
    return _reference_text(E, ctx, job["result"])


@tool("wait", "Wait",
      "Wait for a running operation: media (preparation), transcription, subject, preview, export or job "
      "(job_id from auto_edit/analyze). Returns its state.",
      {"project": P, "what": {"type": "string", "enum": ["media", "transcription", "subject", "preview", "export",
                                                          "job"]},
       "job_id": {"type": "string"}, "media": {"type": "string"}, "wait": WAIT}, ["what"], read_only=True)
def t_wait(E: Engine, ctx: Context, a: dict) -> str:
    what, pid, budget = a["what"], a.get("project"), _budget(a)
    if what == "job":
        job = _wait_job(E, ctx, a["job_id"], budget)
        if job["status"] == "done" and isinstance(job.get("result"), dict) and "timeline_text" in job["result"]:
            return _edit_report(job["result"])
        if job["status"] == "done" and isinstance(job.get("result"), dict) and "sheet_urls" in job["result"]:
            return _reference_text(E, ctx, job["result"])
        if job["status"] == "done" and isinstance(job.get("result"), dict) and "takes" in job["result"]:
            return fmt_takes(job["result"])
        if job["status"] == "done" and isinstance(job.get("result"), dict) and "metrics" in job["result"]:
            return fmt_scenes(job["result"])
        return f"Job {job['status']}" + (f": {job['message']}" if job.get("message") else "")
    if not pid:
        return "Give the project."
    if what in ("media", "transcription"):
        done, p = _wait_media(E, ctx, pid, budget, what == "transcription")
        return ("Ready." if done else "Still running: call wait again.") + "\n" + "\n".join(
            f"  {fmt_media(m)}" for m in p["media"])
    if what == "subject":
        def check():
            ms = [m for m in E.get(f"/api/agent/{pid}")["media"] if not a.get("media") or m["id"] == a["media"]
                  or m["name"] == a["media"]]
            subs = [m.get("subject") or {} for m in ms if m.get("subject")]
            return subs if subs and all(s.get("status") in ("done", "error") for s in subs) else None
        subs = poll(ctx, check, budget, 1.5)
        return ("Subject ready: " + ", ".join(s.get("status", "") for s in subs)) if subs else \
            "Subject detection still running: call wait again."
    if what == "preview":
        return t_preview(E, ctx, {"project": pid, "wait": budget})
    if what == "export":
        return _export_wait(E, ctx, pid, budget)
    return f"Unknown: {what}"


@tool("undo", "Undo", "Revert the last change made by an agent tool on this project (repeatable).",
      {"project": P}, ["project"], destructive=True)
def t_undo(E: Engine, ctx: Context, a: dict) -> str:
    r = E.post(f"/api/agent/{a['project']}/undo")
    return f"Undid {r['undone']} · rev {r['rev']} ({r['remaining']} more undo step(s))"


@tool("open_in_app", "Open in Montage IA",
      "Show the project in the Montage IA studio window (timeline, preview, all manual tools), for the user to "
      "watch or tweak. The studio reloads by itself when you change the project.",
      {"project": P}, ["project"], read_only=True)
def t_open(E: Engine, ctx: Context, a: dict) -> str:
    path = f"/studio#p={a['project']}"
    try:
        E.post("/api/app/open", {"path": path})
        return "The project is open in the Montage IA window."
    except EngineError:
        import webbrowser
        webbrowser.open(E.ensure() + path)
        return f"Opened in the browser: {E.url}{path}"


PROMPTS = {
    "edit_video": {
        "name": "edit_video", "title": "Edit a video with Montage IA",
        "description": "Edit a video from scratch (hook, cuts, captions, titles, b-roll, sound, export).",
        "arguments": [
            {"name": "source", "description": "Absolute path of the video (or folder of rushes).", "required": True},
            {"name": "format", "description": "9:16 (default), 16:9, 1:1, 4:5", "required": False},
            {"name": "length", "description": "Target length, e.g. 45 s (default: as needed)", "required": False},
            {"name": "brief", "description": "Tone, audience, style wishes", "required": False},
            {"name": "reference", "description": "A video whose editing style to match (absolute path)",
             "required": False}]},
}


# ------------------------------------------------------------- protocole

class Server:
    def __init__(self, engine: Engine, rfile, wfile) -> None:
        self.engine = engine
        self.rfile = rfile
        self.wfile = wfile
        self.wlock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.calls: dict = {}

    def send(self, msg: dict) -> None:
        data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.wlock:
            self.wfile.write(data)
            self.wfile.flush()

    def notify(self, method: str, params: dict) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def reply(self, rid, result=None, error=None) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result
        self.send(msg)

    def run(self) -> None:
        for raw in iter(self.rfile.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except ValueError:
                self.reply(None, error={"code": -32700, "message": "Parse error"})
                continue
            for m in (msg if isinstance(msg, list) else [msg]):
                try:
                    self.handle(m)
                except Exception as exc:  # noqa: BLE001 - un message fautif n'arrête pas le serveur
                    traceback.print_exc()
                    if isinstance(m, dict) and "id" in m:
                        self.reply(m["id"], error={"code": -32603, "message": str(exc)})
        # entrée fermée : les attentes en cours s'arrêtent, les réponses partent si le client écoute encore
        for ctx in list(self.calls.values()):
            ctx.cancelled = True
        self.pool.shutdown(wait=True, cancel_futures=True)

    def handle(self, m: dict) -> None:
        method, rid, params = m.get("method"), m.get("id"), m.get("params") or {}
        if method is None:
            return                                        # réponse du client : rien à faire
        if method == "initialize":
            asked = params.get("protocolVersion")
            self.reply(rid, {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False},
                                 "logging": {}},
                "serverInfo": {"name": SERVER_NAME, "title": "Montage IA", "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS})
        elif method == "ping":
            self.reply(rid, {})
        elif method == "tools/list":
            self.reply(rid, {"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS.values()]})
        elif method == "tools/call":
            ctx = Context(self, rid, (params.get("_meta") or {}).get("progressToken"))
            self.calls[rid] = ctx
            self.pool.submit(self.call, rid, params.get("name"), params.get("arguments") or {}, ctx)
        elif method == "prompts/list":
            self.reply(rid, {"prompts": list(PROMPTS.values())})
        elif method == "prompts/get":
            args = params.get("arguments") or {}
            if params.get("name") not in PROMPTS:
                self.reply(rid, error={"code": -32602, "message": f"Unknown prompt {params.get('name')}"})
                return
            text = EDIT_PROMPT.format(source=args.get("source") or "(ask me)", format=args.get("format") or "9:16",
                                      reference=args.get("reference") or "none (use your own taste)",
                                      length=args.get("length") or "as short as the content allows",
                                      brief=args.get("brief") or "dynamic, clear, engaging")
            self.reply(rid, {"description": PROMPTS["edit_video"]["description"],
                             "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
        elif method in ("resources/list",):
            self.reply(rid, {"resources": []})
        elif method == "resources/templates/list":
            self.reply(rid, {"resourceTemplates": []})
        elif method == "logging/setLevel":
            self.reply(rid, {})
        elif method == "notifications/cancelled":
            ctx = self.calls.get(params.get("requestId"))
            if ctx:
                ctx.cancelled = True
        elif method.startswith("notifications/"):
            return
        elif rid is not None:
            self.reply(rid, error={"code": -32601, "message": f"Method not found: {method}"})

    def call(self, rid, name: str, args: dict, ctx: Context) -> None:
        t = TOOLS.get(name)
        try:
            if not t:
                raise EngineError(404, f"Unknown tool {name}")
            text = t["fn"](self.engine, ctx, args)
            content = [{"type": "text", "text": text}]
            content += [{"type": "image", "data": d, "mimeType": mt} for d, mt in ctx.images]
            result = {"content": content, "isError": False}
        except Cancelled:
            result = {"content": [{"type": "text", "text": "Cancelled."}], "isError": True}
        except EngineError as exc:
            result = {"content": [{"type": "text", "text": f"Error ({exc.status}): {exc}"}], "isError": True}
        except Exception as exc:  # noqa: BLE001 - l'agent lit l'erreur et corrige son appel
            traceback.print_exc()
            result = {"content": [{"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}], "isError": True}
        finally:
            self.calls.pop(rid, None)
        if not ctx.cancelled:
            try:
                self.reply(rid, result)
            except OSError:
                pass                    # client parti


def _std_streams(streamless: bool):
    """Flux binaires d'entrée et de sortie. Un exe fenêtré (sans console) n'a
    pas de sys.stdout Python, mais reçoit bien les tubes du client : on les
    reprend alors au niveau du système."""
    if not streamless and sys.stdin is not None and hasattr(sys.stdin, "buffer"):
        return sys.stdin.buffer, sys.stdout.buffer
    if os.name == "nt":
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = ctypes.c_void_p
        fin = msvcrt.open_osfhandle(k32.GetStdHandle(-10), os.O_RDONLY | os.O_BINARY)
        fout = msvcrt.open_osfhandle(k32.GetStdHandle(-11), os.O_BINARY)
        return os.fdopen(fin, "rb", buffering=0), os.fdopen(fout, "wb", buffering=0)
    return os.fdopen(0, "rb", buffering=0), os.fdopen(1, "wb", buffering=0)


def serve(find, launch, streamless: bool = False, log: str | None = None) -> None:
    """Point d'entrée : sert le protocole jusqu'à la fermeture de l'entrée."""
    rfile, wfile = _std_streams(streamless)
    # plus rien ne doit écrire sur la sortie du protocole : les traces vont au journal
    sink = open(log, "a", encoding="utf-8", buffering=1) if log else sys.stderr
    if sink is None or not hasattr(sink, "write"):
        sink = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sink
    if streamless or sys.stderr is None:
        sys.stderr = sink
    Server(Engine(find, launch), rfile, wfile).run()
    os._exit(0)                  # le client a fermé : les attentes en cours n'ont plus de destinataire
