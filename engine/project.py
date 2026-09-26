r"""État d'édition d'une vidéo : ce qui vit entre l'analyse et l'export.

Principe posé par l'EDL : on analyse UNE fois (sonde + Whisper), puis tout le
montage se fait sur des données en mémoire. Déplacer un sous-titre, le
retailler, corriger un mot, changer de style — rien de tout ça ne relance l'IA
ni ffmpeg. Seul l'export final re-rend la vidéo, en pleine résolution.

La seule chose re-rendue pendant l'édition est le proxy de prévisualisation, et
uniquement quand les COUPES changent (curseur des blancs, tics de langage) —
jamais quand les sous-titres changent, puisque ceux-ci sont dessinés en HTML
par-dessus la vidéo.

Le projet est écrit sur disque (`store`) à chaque étape marquante : fermer le
serveur ne perd rien, et l'écran d'accueil peut lister les montages en cours.

Repère des sous-titres : x/y normalisés 0..1 sur la frame de sortie, origine au
centre du bloc de texte. Le proxy a le même cadrage à une échelle près, donc
les mêmes coordonnées valent pour l'aperçu et pour l'export.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict

from engine import store
from engine.core import Options
from engine.edl import KeepSegment, Word
from engine.pipeline.ass_edit import build_ass_edited, emoji_geometry
from engine.pipeline.captions import clean_words, group_indices
from engine.pipeline.edit import (clean_ranges, filler_cuts, keep_from_cuts, silence_cuts,
                                  subtract_ranges)
from engine.pipeline.emoji import emoji_for
from engine.pipeline.probe import MediaInfo, probe
from engine.pipeline.render import (burn_and_overlay, grab_thumbnail, output_size,
                                    render_cut, render_preview)
from engine.pipeline.style_presets import LOOK_FIELDS, preset
from engine.pipeline.transcribe import transcribe
from engine.pipeline.translate import available as translate_available

# Espacement mini entre deux émojis : au-delà ça fait sapin de Noël.
EMOJI_MIN_GAP = 3.0


class Project:
    """Un montage : source, mots transcrits, coupes, sous-titres, export."""

    def __init__(self, pid: str, source: str, work_dir: str, opts: Options,
                 output_path: str, name: str = "") -> None:
        self.id = pid
        self.source = os.path.abspath(source)
        self.work_dir = work_dir
        self.dir = store.project_dir(work_dir, pid, create=True)
        self.opts = opts
        self.output_path = output_path
        self.name = name or os.path.splitext(os.path.basename(self.source))[0]

        self.info: MediaInfo | None = None
        self.words: list[Word] = []          # nettoyés, timeline SOURCE
        self.cuts: list[tuple[float, float, str]] = []   # (début, fin, origine)
        self.keep: list[KeepSegment] = []
        self.out_words: list[dict] = []      # timeline SORTIE, garde l'index source
        self.captions: list[dict] = []
        self.duration = 0.0                  # durée après coupes
        self.language = opts.language or ""  # langue parlée, détectée par Whisper
        self.out_w, self.out_h = 1080, 1920

        self.preview_rev = 0
        self.preview_name = ""
        self.style = opts.style
        self.export_result: dict | None = None
        self.created = store.now()
        self.updated = self.created
        self.task: dict = {"status": "queued", "phase": "analyze", "step": 0,
                           "total": 4, "pct": 0, "message": "En file…"}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- persistance

    @property
    def preview_path(self) -> str:
        return os.path.join(self.dir, self.preview_name) if self.preview_name else ""

    @property
    def thumb_path(self) -> str:
        return os.path.join(self.dir, "thumb.jpg")

    @property
    def preview_ready(self) -> bool:
        return bool(self.preview_name) and os.path.isfile(self.preview_path)

    def save(self) -> None:
        self.updated = store.now()
        store.write_state(self.work_dir, self.id, {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "output_path": self.output_path,
            "created": self.created,
            "updated": self.updated,
            "opts": asdict(self.opts),
            "info": (asdict(self.info) if self.info else None),
            "words": [{"text": w.text, "start": w.start, "end": w.end} for w in self.words],
            "captions": self.captions,
            "language": self.language,
            "style": self.style,
            "export": self.export_result,
            "preview_name": self.preview_name,
            "preview_rev": self.preview_rev,
            "duration": round(self.duration, 3),
            "source_duration": round(self.info.duration, 3) if self.info else 0,
            "removed": round((self.info.duration - self.duration), 3) if self.info else 0,
        })

    @classmethod
    def load(cls, work_dir: str, pid: str) -> "Project | None":
        """Rouvre un projet depuis le disque, sans relancer Whisper ni ffmpeg.

        Les coupes ne sont pas stockées : elles se redéduisent des mots et des
        réglages, ce qui garantit qu'elles collent toujours aux sous-titres.
        """
        state = store.read_state(work_dir, pid)
        if not state or not state.get("words") or not state.get("info"):
            return None

        opts = Options(**{k: v for k, v in (state.get("opts") or {}).items()
                          if k in Options.__dataclass_fields__})
        proj = cls(pid, state["source"], work_dir, opts,
                   state.get("output_path") or "", state.get("name", ""))
        proj.created = state.get("created", store.now())
        proj.updated = state.get("updated", proj.created)
        proj.info = MediaInfo(**state["info"])
        proj.out_w, proj.out_h = output_size(proj.info.width, proj.info.height, opts.vertical)
        proj.words = [Word(**w) for w in state["words"]]
        proj.style = state.get("style", opts.style)
        proj.language = state.get("language") or opts.language or ""
        proj.export_result = state.get("export")
        proj.preview_name = state.get("preview_name", "")
        proj.preview_rev = state.get("preview_rev", 0)

        proj._apply_cuts()
        stored = state.get("captions")
        proj.captions = stored if stored else proj._build_captions()
        proj.task = {"status": "done", "phase": "open", "step": 0, "total": 1,
                     "pct": 100, "message": "Projet ouvert"}
        return proj

    # ------------------------------------------------------------------ tâches

    @property
    def is_busy(self) -> bool:
        return self.task.get("status") == "running"

    def _begin(self, phase: str, total: int, message: str) -> bool:
        """Réserve le projet. Faux si une autre tâche tourne déjà."""
        with self._lock:
            if self.is_busy:
                return False
            self.task = {"status": "running", "phase": phase, "step": 0,
                         "total": total, "pct": 0, "message": message}
            return True

    def _step(self, step: int, message: str) -> None:
        t = self.task
        t.update(step=step, message=message,
                 pct=int(100 * max(0, step - 1) / max(1, t["total"])))

    def _sub(self, step: int, frac: float) -> None:
        """Progression fine à l'intérieur d'une étape (rendu ffmpeg)."""
        t = self.task
        t["pct"] = int(100 * (max(0, step - 1) + min(1.0, frac)) / max(1, t["total"]))

    def _done(self, message: str, **extra) -> None:
        self.task.update(status="done", pct=100, message=message, **extra)

    def _failed(self, exc: Exception) -> None:
        self.task.update(status="error", message=str(exc))

    # ----------------------------------------------------------------- analyse

    def analyze(self) -> None:
        """Sonde + transcrit + applique les coupes + fabrique le proxy."""
        if not self._begin("analyze", 4, "Analyse de la source…"):
            return
        try:
            self._step(1, "Analyse de la source…")
            self.info = probe(self.source)
            self.out_w, self.out_h = output_size(
                self.info.width, self.info.height, self.opts.vertical)

            self._step(2, "Transcription (Whisper)… c'est la partie longue")
            meta: dict = {}
            raw = transcribe(self.source, self.opts.model, self.opts.device,
                             self.opts.compute_type, self.opts.language, info=meta)
            if not raw:
                raise RuntimeError("Aucune parole détectée : rien à monter.")
            self.language = self.opts.language or meta.get("language") or ""
            self.words = clean_words(raw)

            self._recompute(step_edit=3, step_render=4)
            self.save()
            self._done("Prêt à éditer")
        except Exception as exc:  # noqa: BLE001 - remonté tel quel au front
            self._failed(exc)

    def recut(self, *, max_gap: float, cut_silence: bool, cut_fillers: bool,
              manual: list | None = None, keep: list | None = None) -> None:
        """Rejoue les coupes SANS retranscrire (les mots sont déjà en mémoire).

        `keep` remplace la liste des passages que l'utilisateur garde malgré
        les coupes automatiques ; None la laisse telle quelle.
        """
        if not self._begin("recut", 2, "Recalcul des coupes…"):
            return
        try:
            self.opts.max_gap = float(max_gap)
            self.opts.cut_silence = bool(cut_silence)
            self.opts.cut_fillers = bool(cut_fillers)
            if manual is not None:
                self.opts.manual_cuts = manual
            if keep is not None:
                self.opts.keep_ranges = clean_ranges(keep, self.info.duration)
            self._recompute(step_edit=1, step_render=2)
            self.save()
            self._done("Coupes mises à jour")
        except Exception as exc:  # noqa: BLE001
            self._failed(exc)

    def rebuild_preview(self) -> None:
        """Refabrique le proxy manquant d'un projet rouvert (sans Whisper)."""
        if not self._begin("preview", 1, "Préparation de l'aperçu…"):
            return
        try:
            self._render_preview(1)
            self.save()
            self._done("Aperçu prêt")
        except Exception as exc:  # noqa: BLE001
            self._failed(exc)

    def _recompute(self, step_edit: int, step_render: int) -> None:
        self._step(step_edit, "Calcul des coupes…")
        self._apply_cuts()
        self.captions = self._build_captions()
        self._step(step_render, "Préparation de l'aperçu…")
        self._render_preview(step_render)

    def _apply_cuts(self) -> None:
        """Coupes -> segments gardés -> mots reprojetés. Aucun rendu ici."""
        self.cuts = self._collect_cuts()
        self.keep = keep_from_cuts(self.info.duration, [(s, e) for s, e, _ in self.cuts])
        if not self.keep:
            raise RuntimeError("Tous les segments ont été coupés : rien à garder.")
        self.out_words, self.duration = _remap(self.words, self.keep)

    def _collect_cuts(self) -> list[tuple[float, float, str]]:
        cuts: list[tuple[float, float, str]] = []
        if self.opts.cut_silence:
            cuts += [(s, e, "silence") for s, e in silence_cuts(
                self.words, self.info.duration, self.opts.max_gap, self.opts.pad)]
        if self.opts.cut_fillers:
            cuts += [(s, e, "filler") for s, e in filler_cuts(self.words)]
        cuts += [(float(s), float(e), "manual") for s, e in (self.opts.manual_cuts or [])]
        # Les passages gardés par l'utilisateur l'emportent sur tous les tools.
        keeps = [(float(s), float(e)) for s, e in (self.opts.keep_ranges or [])]
        if keeps:
            cuts = [(a, b, kind) for s, e, kind in cuts
                    for a, b in subtract_ranges([(s, e)], keeps)]
        return sorted(cuts)

    def _render_preview(self, step: int) -> None:
        old = self.preview_path
        self.preview_rev += 1
        name = f"preview_{self.preview_rev}.mp4"
        path = os.path.join(self.dir, name)
        render_preview(
            self.source, self.keep, path,
            vertical=self.opts.vertical, width=self.info.width, height=self.info.height,
            duration=self.duration, on_progress=lambda f: self._sub(step, f),
        )
        self.preview_name = name
        grab_thumbnail(path, self.thumb_path, at=min(1.0, self.duration * 0.15))
        if old and old != path:
            _try_remove(old)

    # --------------------------------------------------------------- captions

    def _build_captions(self) -> list[dict]:
        """Une ligne de sous-titre par groupe de mots, habillée par le preset."""
        look = preset(self.style)
        esz, edy = emoji_geometry(look["size"])
        texts = [w["text"] for w in self.out_words]
        groups = group_indices(texts, self.opts.words_per_line, self.opts.max_chars)

        captions: list[dict] = []
        last_emoji_t = -1e9
        for grp in groups:
            words = [self.out_words[i] for i in grp]
            start, end = words[0]["start"], words[-1]["end"]
            if end <= start:
                end = start + 0.2

            char = ""
            if self.opts.emojis and start - last_emoji_t >= EMOJI_MIN_GAP:
                for w in words:
                    char = emoji_for(w["text"]) or ""
                    if char:
                        last_emoji_t = start
                        break

            captions.append({
                "id": f"c{words[0]['i']}",
                "anchor": words[0]["i"],
                "start": round(start, 3),
                "end": round(end, 3),
                "words": [{"text": w["text"], "start": round(w["start"], 3),
                           "end": round(w["end"], 3)} for w in words],
                "emoji": char,
                "emoji_size": esz,
                "emoji_dx": 0,
                "emoji_dy": round(edy, 1),
                **look,
            })
        return captions

    def set_state(self, captions=None, style=None, name=None) -> None:
        """Sauvegarde automatique envoyée par l'éditeur."""
        if isinstance(captions, list):
            self.captions = captions
        if style:
            self.style = str(style)
        if name is not None:
            self.name = str(name).strip()[:80] or self.name
        self.save()

    # ------------------------------------------------------------------ export

    def export(self, captions: list[dict], encoder: str | None = None,
               signature: str = "") -> None:
        """Rend l'export final : coupe pleine résolution + sous-titres édités."""
        if not self._begin("export", 3, "Rendu de la vidéo…"):
            return
        try:
            enc = encoder or self.opts.encoder
            pid = f"{self.id}_{int(time.time())}"
            cut_path = os.path.join(self.dir, f"_cut_{pid}.mp4")
            ass_path = os.path.join(self.dir, f"_caps_{pid}.ass")
            out = os.path.abspath(self.output_path)
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

            usable = [c for c in captions or [] if not c.get("hidden")]

            self._step(1, "Coupe et recadrage…")
            target = out if not usable else cut_path
            render_cut(self.source, self.keep, target,
                       vertical=self.opts.vertical, encoder=enc,
                       width=self.info.width, height=self.info.height,
                       duration=self.duration, on_progress=lambda f: self._sub(1, f))

            if usable:
                self._step(2, "Sous-titres…")
                emojis = build_ass_edited(usable, ass_path, self.out_w, self.out_h)
                self._step(3, "Incrustation…")
                burn_and_overlay(cut_path, ass_path, emojis, out, encoder=enc,
                                 duration=self.duration, width=self.out_w, height=self.out_h,
                                 on_progress=lambda f: self._sub(3, f),
                                 fonts={c.get("font") for c in usable})

            for tmp in (cut_path, ass_path):
                _try_remove(tmp)

            self.export_result = {
                "output": out,
                "at": store.now(),
                "signature": str(signature or ""),
                "source_duration": round(self.info.duration, 1),
                "output_duration": round(self.duration, 1),
                "removed": round(self.info.duration - self.duration, 1),
                "segments": len(self.keep),
                "captions": len(usable),
                "size": os.path.getsize(out) if os.path.isfile(out) else 0,
            }
            self.save()
            self._done("Export terminé", result=self.export_result)
        except Exception as exc:  # noqa: BLE001
            self._failed(exc)

    # ------------------------------------------------------------ sérialisation

    def stitches(self) -> list[dict]:
        """Points de la timeline de SORTIE où un passage a été supprimé.

        C'est ce que l'éditeur affiche en marqueurs : à l'instant `t` du
        montage, `removed` secondes de la source ont sauté (et pourquoi).
        `start`/`end` situent le trou dans la source — c'est ce que l'éditeur
        renvoie pour le garder — et `text` dit ce qui y était prononcé.
        Le début et la fin de la source comptent aussi (t = 0, t = durée).
        """
        out: list[dict] = []
        acc = prev = 0.0
        spans = [(k.start, k.end) for k in self.keep]
        if self.info:
            spans.append((self.info.duration, self.info.duration))
        for start, end in spans:
            if start - prev > 0.01:
                out.append({"t": round(acc, 3), "removed": round(start - prev, 3),
                            "start": round(prev, 3), "end": round(start, 3),
                            "kind": self._cut_kind(prev, start),
                            "text": self._text_between(prev, start)})
            acc += end - start
            prev = end
        return out

    def _text_between(self, start: float, end: float) -> str:
        text = " ".join(w.text for w in self.words if start <= w.start < end)
        return text if len(text) <= 80 else text[:79] + "…"

    def _cut_kind(self, start: float, end: float) -> str:
        """Origine dominante d'un trou (les coupes peuvent se chevaucher)."""
        best, best_kind = 0.0, "silence"
        for s, e, kind in self.cuts:
            overlap = min(end, e) - max(start, s)
            if overlap > best:
                best, best_kind = overlap, kind
        return best_kind

    def to_dict(self) -> dict:
        src = self.info.duration if self.info else 0.0
        return {
            "job_id": self.id,
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "source_name": os.path.basename(self.source),
            "created": self.created,
            "updated": self.updated,
            "width": self.out_w,
            "height": self.out_h,
            "duration": round(self.duration, 3),
            "source_duration": round(src, 3),
            "removed": round(src - self.duration, 3),
            "preview": f"/api/preview/{self.id}?v={self.preview_rev}",
            "preview_ready": self.preview_ready,
            "style": self.style,
            "export": self.export_result,
            "cuts": [{"start": round(s, 3), "end": round(e, 3), "kind": k}
                     for s, e, k in self.cuts if e > s],
            "keep": [{"start": round(k.start, 3), "end": round(k.end, 3)} for k in self.keep],
            "stitches": self.stitches(),
            "kept": [{"start": s, "end": e} for s, e in (self.opts.keep_ranges or [])],
            "language": self.language,
            # Projets d'avant la détection de langue : l'app transcrit du français.
            "translate": {"available": translate_available(self.language or "fr", "en")},
            "captions": self.captions,
            "settings": {
                "max_gap": self.opts.max_gap,
                "cut_silence": self.opts.cut_silence,
                "cut_fillers": self.opts.cut_fillers,
                "words_per_line": self.opts.words_per_line,
                "max_chars": self.opts.max_chars,
                "vertical": self.opts.vertical,
                "encoder": self.opts.encoder,
            },
            "look_fields": list(LOOK_FIELDS),
        }


def _remap(words: list[Word], keep: list[KeepSegment]) -> tuple[list[dict], float]:
    """Reprojette les mots sur la timeline de sortie en gardant l'index source.

    L'index survit aux recalculs de coupes : c'est lui qui permet à l'éditeur de
    retrouver ses retouches (texte corrigé, position, taille) après un
    changement de seuil des blancs.
    """
    offsets: list[float] = []
    acc = 0.0
    for k in keep:
        offsets.append(acc)
        acc += k.end - k.start

    out: list[dict] = []
    for i, w in enumerate(words):
        for j, k in enumerate(keep):
            if k.start <= w.start < k.end:
                ns = offsets[j] + (w.start - k.start)
                ne = offsets[j] + (min(w.end, k.end) - k.start)
                out.append({"i": i, "text": w.text, "start": ns, "end": max(ns, ne)})
                break
    return out, acc


def _try_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
