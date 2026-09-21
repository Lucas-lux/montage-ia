/* État du studio : le document de montage, la sélection, l'historique, la
   sauvegarde automatique.

   `S.doc` est la vérité de l'édition — pistes, clips, format, réglages. Tout
   module qui le modifie passe par `edit()` (un pas d'annulation) ou, pendant
   un glisser, par `begin()` puis `changed({light: true})` et `end()`. Chaque
   modification est annoncée par l'évènement "doc" et sauvegardée peu après.

   `S.proj` garde ce que le moteur possède : médias (proxies, vignettes,
   transcription) et tâche en cours. Il est rafraîchi par `refreshProject()`. */

import { api, post } from "./api.js";

export const S = {
  pid: "",
  proj: null,            // vue serveur (médias, tâche…)
  doc: null,             // montage édité
  media: new Map(),      // id -> média (vue serveur)
  sel: new Set(),        // clips sélectionnés
  t: 0,                  // tête de lecture (s)
  playing: false,
  hist: [],
  redo: [],
  saveTimer: 0,
  saving: false,
  savedAt: 0,
  dragDepth: 0,
  k: 1,                  // pixels écran par pixel de sortie (aperçu)
};

const bus = new EventTarget();
export const on = (type, fn) => bus.addEventListener(type, (e) => fn(e.detail || {}));
export const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));

const DOC_KEYS = ["name", "canvas", "tracks", "clips", "markers", "settings"];

export function loadDoc(proj) {
  S.proj = proj;
  S.doc = JSON.parse(JSON.stringify(Object.fromEntries(DOC_KEYS.map((k) => [k, proj[k]]))));
  setMedia(proj.media);
  S.hist.length = 0;
  S.redo.length = 0;
  S.sel.clear();
  emit("history");
}

export function setMedia(list) {
  S.media = new Map((list || []).map((m) => [m.id, m]));
  if (S.proj) S.proj.media = list || [];
}

/* ------------------------------------------------------------ historique */

function snapshotText() { return JSON.stringify(S.doc); }

/** Mémorise l'état courant comme point d'annulation. */
export function snapshot() {
  S.hist.push(snapshotText());
  if (S.hist.length > 150) S.hist.shift();
  S.redo.length = 0;
  emit("history");
}

/** Une modification atomique : un pas d'annulation, un évènement, une sauvegarde. */
export function edit(fn, reason = "edit") {
  snapshot();
  const res = fn(S.doc);
  changed({ reason });
  return res;
}

/** Glisser : un seul point d'annulation pour tout le geste. */
export function begin() {
  if (S.dragDepth++ === 0) snapshot();
}
export function end(reason = "drag") {
  S.dragDepth = Math.max(0, S.dragDepth - 1);
  if (S.dragDepth === 0) changed({ reason });
}
/** Annule un geste commencé sans rien avoir modifié. */
export function cancelBegin() {
  S.dragDepth = Math.max(0, S.dragDepth - 1);
  if (S.dragDepth === 0) { S.hist.pop(); emit("history"); }
}

export function changed(detail = {}) {
  emit("doc", detail);
  if (!detail.light) scheduleSave();
}

export function undo() {
  if (!S.hist.length) return false;
  S.redo.push(snapshotText());
  S.doc = JSON.parse(S.hist.pop());
  pruneSelection();
  emit("history");
  changed({ reason: "undo" });
  return true;
}
export function redo() {
  if (!S.redo.length) return false;
  S.hist.push(snapshotText());
  S.doc = JSON.parse(S.redo.pop());
  pruneSelection();
  emit("history");
  changed({ reason: "redo" });
  return true;
}

/* ------------------------------------------------------------ sélection */

export function pruneSelection() {
  const ids = new Set(S.doc.clips.map((c) => c.id));
  [...S.sel].forEach((id) => { if (!ids.has(id)) S.sel.delete(id); });
}
export function select(ids, { add = false } = {}) {
  if (!add) S.sel.clear();
  (Array.isArray(ids) ? ids : [ids]).forEach((id) => id && S.sel.add(id));
  emit("select");
}
export function toggle(id) {
  S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id);
  emit("select");
}
export function selectNone() {
  if (!S.sel.size) return;
  S.sel.clear();
  emit("select");
}
export const selected = () => S.doc.clips.filter((c) => S.sel.has(c.id));

/* ------------------------------------------------------ tête de lecture */

export function setTime(t, { from } = {}) {
  S.t = Math.max(0, t || 0);
  emit("time", { from });
}

/* ------------------------------------------------------ sauvegarde auto */

export function scheduleSave(delay = 700) {
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveNow, delay);
  emit("save", { state: "pending" });
}

export async function saveNow() {
  clearTimeout(S.saveTimer);
  S.saveTimer = 0;
  if (!S.pid || !S.doc) return;
  if (S.saving) { scheduleSave(300); return; }     // une à la fois, dans l'ordre
  S.saving = true;
  emit("save", { state: "saving" });
  try {
    const res = await post(`/api/timeline/${S.pid}/save`, S.doc);
    S.savedAt = res.updated;
    emit("save", { state: S.saveTimer ? "pending" : "saved" });
  } catch (e) {
    emit("save", { state: "error", message: e.message });
    scheduleSave(3000);                             // on retentera
  } finally {
    S.saving = false;
  }
}

// Fermeture de l'onglet : `fetch` n'a plus le temps d'aboutir, sendBeacon si.
window.addEventListener("beforeunload", () => {
  if (!S.saveTimer || !S.pid) return;
  navigator.sendBeacon(`/api/timeline/${S.pid}/save`,
    new Blob([JSON.stringify(S.doc)], { type: "application/json" }));
});

/* ---------------------------------------------------- données du moteur */

export async function refreshProject() {
  const proj = await api(`/api/timeline/${S.pid}`);
  S.proj.task = proj.task;
  setMedia(proj.media);
  emit("media");
  return proj;
}
