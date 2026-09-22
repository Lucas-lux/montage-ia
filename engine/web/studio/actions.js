/* Commandes de montage, partagées par la barre d'outils, le clavier, les
   menus contextuels et l'inspecteur. Chacune est un pas d'annulation. */

import { post } from "./api.js";
import { startPolling } from "./bin.js";
import * as M from "./model.js";
import { S, edit, emit, select, selectNone, selected, setMedia, setTime } from "./store.js";
import { toast } from "./util.js";

const A = { clipboard: [] };

/** Sélection étendue aux partenaires liés (vidéo + son séparé). */
export const selectionIds = () => M.linkedIds(S.doc, [...S.sel]);

/** Après toute édition qui déplace des clips : les sous-titres liés à la voix suivent. */
function reflow(doc) { M.reflowCaptions(doc); }

export function split() {
  const ids = S.sel.size ? selectionIds() : null;
  const rights = edit((doc) => {
    const r = M.splitAt(doc, S.t, ids);
    reflow(doc);
    return r;
  }, "split");
  if (!rights.length) {
    S.hist.pop(); emit("history");
    toast(ids ? "La tête de lecture n'est pas sur la sélection." : "Rien à diviser sous la tête de lecture.");
    return;
  }
  select(rights.map((c) => c.id));
}

export function remove() {
  if (!S.sel.size) return;
  const ids = selectionIds();
  const locked = S.doc.clips.filter((c) => ids.has(c.id) && (M.track(S.doc, c.track) || {}).locked);
  locked.forEach((c) => ids.delete(c.id));
  if (!ids.size) { toast("Piste verrouillée."); return; }
  edit((doc) => { M.deleteClips(doc, ids); reflow(doc); }, "delete");
  selectNone();
}

export function duplicate() {
  if (!S.sel.size) return;
  const ids = selectionIds();
  const copies = edit((doc) => { const c = M.duplicateClips(doc, ids); reflow(doc); return c; }, "duplicate");
  select(copies.map((c) => c.id));
}

export function copy() {
  if (!S.sel.size) return;
  const ids = selectionIds();
  A.clipboard = JSON.parse(JSON.stringify(S.doc.clips.filter((c) => ids.has(c.id))));
  toast(`${A.clipboard.length} clip${A.clipboard.length > 1 ? "s" : ""} copié${A.clipboard.length > 1 ? "s" : ""}.`);
}

export function cut() {
  if (!S.sel.size) return;
  copy();
  remove();
}

export function paste() {
  if (!A.clipboard.length) { toast("Rien à coller."); return; }
  // les médias retirés du projet entre-temps ne se collent pas
  const clips = A.clipboard.filter((c) => !c.media || S.media.has(c.media));
  const copies = edit((doc) => { const c = M.pasteClips(doc, clips, S.t); reflow(doc); return c; }, "paste");
  select(copies.map((c) => c.id));
}

export function selectAll() {
  select(S.doc.clips.filter((c) => !(M.track(S.doc, c.track) || {}).locked).map((c) => c.id));
}

/** Sépare le son des vidéos sélectionnées (ou le rattache s'il l'est déjà). */
export function toggleDetach(clips = selected()) {
  const videos = clips.filter((c) => c.kind === "video");
  if (!videos.length) { toast("Sélectionne un clip vidéo."); return; }
  const noSound = videos.filter((c) => !(S.media.get(c.media) || {}).has_audio);
  const todo = videos.filter((c) => !c.detached && (S.media.get(c.media) || {}).has_audio);
  if (todo.length) {
    const made = edit((doc) => {
      const out = todo.map((v) => M.detachAudio(doc, doc.clips.find((x) => x.id === v.id))).filter(Boolean);
      reflow(doc);
      return out;
    }, "detach");
    select([...todo.map((c) => c.id), ...made.map((c) => c.id)]);
    toast(`Son séparé : ${made.length} clip${made.length > 1 ? "s" : ""} audio créé${made.length > 1 ? "s" : ""}.`);
  } else if (videos.some((c) => c.detached)) {
    edit((doc) => {
      videos.forEach((v) => M.reattachAudio(doc, doc.clips.find((x) => x.id === v.id)));
      reflow(doc);
    }, "attach");
    select(videos.map((c) => c.id));
    toast("Son rattaché à la vidéo.");
  } else if (noSound.length) {
    toast("Cette vidéo n'a pas de son.");
  }
}

export function unlinkSelection() {
  const ids = selectionIds();
  if (!S.doc.clips.some((c) => ids.has(c.id) && c.link)) return;
  edit((doc) => M.unlink(doc, ids), "unlink");
  toast("Clips dissociés : ils bougent désormais séparément.");
}

/** Muet / son pour les clips sélectionnés. */
export function toggleMute() {
  const list = selected().filter((c) => c.kind === "video" || c.kind === "audio");
  if (!list.length) return;
  const mute = !list.every((c) => c.muted);
  edit((doc) => doc.clips.forEach((c) => { if (list.some((x) => x.id === c.id)) c.muted = mute; }), "mute");
}

/* -------------------------------------------------------------- navigation */

/** Points de montage (débuts et fins de clips) triés. */
export function editPoints() {
  const pts = new Set([0]);
  S.doc.clips.forEach((c) => { pts.add(M.r4(c.start)); pts.add(M.r4(M.clipEnd(c))); });
  return [...pts].sort((a, b) => a - b);
}

export function jump(dir) {
  const pts = editPoints();
  const t = S.t;
  const next = dir > 0 ? pts.find((p) => p > t + 1e-3) : [...pts].reverse().find((p) => p < t - 1e-3);
  if (next !== undefined) setTime(next, { from: "jump" });
}

/* ---------------------------------------------------------------- marqueurs */

export function addMarker() {
  const t = M.r4(S.t);
  if ((S.doc.markers || []).some((m) => Math.abs(m.t - t) < 0.05)) return;
  edit((doc) => {
    doc.markers = (doc.markers || []).concat([{ id: M.uid("k"), t, label: "", color: "#F23A52" }])
      .sort((a, b) => a.t - b.t);
  }, "marker");
}

export function removeMarker(id) {
  edit((doc) => { doc.markers = (doc.markers || []).filter((m) => m.id !== id); }, "marker");
}

/* ------------------------------------------------------------ arrêt sur image */

/** Fige l'image sous la tête de lecture : le clip vidéo est coupé et une image
 *  fixe de 2 s (extraite de l'original, pleine définition) s'intercale. */
export async function freezeFrame(dur = 2) {
  const sel = selected().filter((c) => c.kind === "video");
  const c = sel.find((x) => x.start < S.t && M.clipEnd(x) > S.t) ||
            S.doc.clips.find((x) => x.kind === "video" && M.isMain(S.doc, x.track) && x.start <= S.t && M.clipEnd(x) > S.t);
  if (!c) { toast("Place la tête de lecture sur un clip vidéo."); return; }
  const at = c.in + (S.t - c.start) * (c.speed || 1);
  let view;
  try { view = await post(`/api/timeline/${S.pid}/freeze`, { media: c.media, at }); }
  catch (e) { toast("Arrêt sur image impossible : " + e.message); return; }
  setMedia([...S.media.values(), view]);
  emit("media");
  startPolling();
  toast("Arrêt sur image…");
  const t = S.t;
  const ready = await new Promise((resolve) => {
    const tick = (n = 0) => {
      const m = S.media.get(view.id);
      if (m && m.status === "ready") return resolve(m);
      if (!m || m.status === "error" || n > 100) return resolve(null);
      setTimeout(() => tick(n + 1), 300);
    };
    tick();
  });
  if (!ready) { toast("Arrêt sur image impossible."); return; }
  const img = edit((doc) => {
    const clip = doc.clips.find((x) => x.id === c.id);
    if (!clip) return null;
    const cut = t > clip.start + M.MIN_DUR && t < M.clipEnd(clip) - M.MIN_DUR;
    const piece = M.newMediaClip(ready, { dur });
    Object.assign(piece, { x: clip.x, y: clip.y, scale: clip.scale, rotation: clip.rotation, fit: clip.fit,
                           flip_h: clip.flip_h, flip_v: clip.flip_v, opacity: clip.opacity });
    if (M.isMain(doc, clip.track)) {
      if (cut) M.splitAt(doc, t, new Set([clip.id]));
      M.insertMain(doc, [piece], t);
    } else {
      piece.start = M.r4(t);
      piece.track = M.freeTrack(doc, "video", t, dur, { skipMain: true }).id;
      doc.clips.push(piece);
    }
    M.reflowCaptions(doc);
    return piece;
  }, "freeze");
  if (img) { select([img.id]); toast("Arrêt sur image ajouté (2 s)."); }
}

/* -------------------------------------------------------------- transitions */

/** Pose (ou retire, type vide) la transition d'entrée des clips donnés. */
export function setTransition(ids, type, dur) {
  edit((doc) => doc.clips.forEach((c) => {
    if (!ids.includes(c.id) || (c.kind !== "video" && c.kind !== "image")) return;
    if (!type) delete c.trans;
    else c.trans = { type, dur: M.r4(dur ?? (c.trans && c.trans.dur) ?? 0.5) };
  }), "transition");
}

/** Même transition sur toutes les coupes (clips qui se touchent) d'une piste. */
export function transitionEverywhere(trackId, type, dur) {
  const ids = S.doc.clips.filter((c) => c.track === trackId && S.doc.clips.some((o) =>
    o !== c && o.track === trackId && Math.abs(M.clipEnd(o) - c.start) < 1e-3)).map((c) => c.id);
  if (!ids.length) { toast("Aucune coupe sur cette piste."); return; }
  setTransition(ids, type, dur);
  toast(type ? `Transition posée sur ${ids.length} coupe${ids.length > 1 ? "s" : ""}.` : "Transitions retirées.");
}
