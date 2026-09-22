/* Panneau Médias : import (fichiers, dossier, chemins locaux), état de
   préparation de chaque média, ajout à la timeline.

   Deux façons d'importer :
     - téléverser (bouton, glisser-déposer, dossier) : le fichier est copié
       dans le projet — le navigateur ne peut pas donner son chemin ;
     - « Par chemin » : le moteur lit le fichier là où il est, sans copie.
       C'est le bon choix pour de gros rushs. */

import { api, del, post, upload } from "./api.js";
import * as M from "./model.js";
import { S, edit, emit, on, select, setMedia, setTime } from "./store.js";
import { $, fmt, h, menu, modal, mo, naturalCompare, svg, toast } from "./util.js";

const VIDEO = ["mp4", "mov", "m4v", "mkv", "webm", "avi", "mts", "m2ts", "ts", "wmv", "flv",
               "3gp", "mpg", "mpeg", "mxf", "ogv"];
const AUDIO = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "wma", "aif", "aiff",
               "caf", "amr", "ac3"];
const IMAGE = ["jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif", "avif"];
const EXT = new Set([...VIDEO, ...AUDIO, ...IMAGE]);
const ext = (name) => (String(name).split(".").pop() || "").toLowerCase();
export const isMediaFile = (name) => EXT.has(ext(name));

const B = {
  uploads: [],          // envois en cours { key, name, size, pct }
  filter: "all",
  query: "",
  pending: [],          // médias à poser sur la timeline dès qu'ils sont prêts
  poll: 0,
  gen: 0,               // change à chaque ajout ou retrait local de média
};

/* ----------------------------------------------------------------- import */

/** Téléverse des fichiers, un par un (progression par fichier). Avec
 *  `toTimeline`, chaque média rejoint la timeline quand il est prêt. */
export async function importFiles(files, { toTimeline = false } = {}) {
  const list = [...files].filter((f) => isMediaFile(f.name))
    .sort((a, b) => naturalCompare(a.webkitRelativePath || a.name, b.webkitRelativePath || b.name));
  const ignored = files.length - list.length;
  if (!list.length) {
    toast(files.length ? "Aucun fichier vidéo, audio ou image reconnu." : "Aucun fichier.");
    return;
  }
  if (ignored) toast(`${ignored} fichier${ignored > 1 ? "s" : ""} ignoré${ignored > 1 ? "s" : ""} (type non pris en charge).`);
  const jobs = list.map((f) => ({ key: M.uid("u"), name: f.name, size: f.size, pct: 0, file: f }));
  B.uploads.push(...jobs);
  render();
  for (const job of jobs) {
    try {
      const view = await upload(
        `/api/timeline/${S.pid}/media/upload?name=${encodeURIComponent(job.name)}`, job.file,
        (p) => { job.pct = p; renderUpload(job); });
      addMediaViews([view]);
      if (toTimeline) B.pending.push(view.id);
    } catch (e) {
      toast(e.message, 4000);
    } finally {
      B.uploads = B.uploads.filter((u) => u !== job);
      render();
    }
  }
  startPolling();
}

/** Ajoute des fichiers ou dossiers locaux par leur chemin (aucune copie). */
export async function importPaths(paths, { recursive = false, toTimeline = false } = {}) {
  const res = await post(`/api/timeline/${S.pid}/media/paths`, { paths, recursive });
  addMediaViews(res.added);
  if (toTimeline) B.pending.push(...res.added.map((m) => m.id));
  if (res.skipped.length) {
    const shown = res.skipped.slice(0, 3).map((s) => `${s.path.split(/[\\/]/).pop()} : ${s.reason}`);
    toast(shown.join(" · ") + (res.skipped.length > 3 ? ` (+${res.skipped.length - 3})` : ""), 5000);
  }
  if (res.added.length) toast(`${res.added.length} média${res.added.length > 1 ? "s" : ""} ajouté${res.added.length > 1 ? "s" : ""}.`);
  startPolling();
  return res;
}

function addMediaViews(views) {
  if (!views.length) return;
  B.gen++;
  const list = [...S.media.values()];
  views.forEach((v) => { if (!S.media.has(v.id)) list.push(v); });
  setMedia(list);
  emit("media");
}

/** Interroge le moteur tant qu'un média se prépare.
 *  Une réponse partie AVANT un ajout ou un retrait local est périmée (elle
 *  ignorerait le média qui vient d'arriver) : on la jette et on redemande. */
function startPolling() {
  if (B.poll) return;
  const tick = async () => {
    const gen = B.gen;
    try {
      const { media, busy } = await api(`/api/timeline/${S.pid}/media`);
      if (gen === B.gen) {
        setMedia(media);
        emit("media");
        flushPending();
        const waiting = media.some((m) => m.status === "pending" || m.status === "processing");
        if (!busy && !waiting && !B.uploads.length && !transcribing()) { B.poll = 0; return; }
      }
    } catch (e) { /* le moteur redémarre ? on réessaie */ }
    B.poll = setTimeout(tick, 900);
  };
  B.poll = setTimeout(tick, 400);
}
export { startPolling };

const transcribing = () =>
  [...S.media.values()].some((m) => ["queued", "running"].includes((m.transcript || {}).status));

/** Médias déposés sur la timeline avant d'être prêts : posés dans l'ordre. */
function flushPending() {
  while (B.pending.length) {
    const m = S.media.get(B.pending[0]);
    if (!m) { B.pending.shift(); continue; }
    if (m.status === "error" || m.status === "missing") { B.pending.shift(); continue; }
    if (m.status !== "ready") return;
    B.pending.shift();
    addToTimeline(m, { quiet: true, at: M.duration(S.doc) + 1e6 });
  }
}

/* -------------------------------------------------------- vers la timeline */

/** Pose un média sur la timeline (bouton « + », double-clic, menu). */
export function addToTimeline(m, { at, quiet = false } = {}) {
  if (m.status !== "ready") {
    B.pending.push(m.id);
    if (!quiet) toast("Encore en préparation : il rejoindra la timeline dès qu'il sera prêt.");
    return;
  }
  const t = at ?? S.t;
  const added = edit((doc) => {
    const clips = M.appendMedia(doc, m, t);
    M.reflowCaptions(doc);
    return clips;
  }, "add");
  select(added.map((c) => c.id));
  if (m.kind !== "audio") setTime(M.clipEnd(added[0]), { from: "bin" });
  if (!quiet) toast(`« ${m.name} » ajouté à la timeline.`);
}

/* -------------------------------------------------------------- panneau */

function init() {
  const root = $("tab-media");
  const fileIn = h("input", { type: "file", multiple: true, class: "hidden",
                              accept: "video/*,audio/*,image/*" });
  const dirIn = h("input", { type: "file", multiple: true, class: "hidden" });
  dirIn.webkitdirectory = true;
  fileIn.onchange = () => { importFiles(fileIn.files); fileIn.value = ""; };
  dirIn.onchange = () => { importFiles(dirIn.files); dirIn.value = ""; };

  const search = h("input", { type: "text", placeholder: "Rechercher un média…",
                              oninput: (e) => { B.query = e.target.value.trim().toLowerCase(); renderGrid(); } });
  const chips = h("div.seg", { id: "binKinds" },
    [["all", "Tout"], ["video", "Vidéo"], ["audio", "Audio"], ["image", "Image"]].map(([k, label]) =>
      h("button", { class: k === B.filter ? "on" : "", dataset: { k },
                    onclick: () => { B.filter = k; renderGrid(); } }, label)));

  root.append(
    h("div.importbar", {},
      h("button.btn.primary", { onclick: () => fileIn.click(), title: "Vidéos, sons, images (copiés dans le projet)",
                                html: svg("plus", 14) + "Importer" }),
      h("button.btn", { onclick: () => dirIn.click(), title: "Tout un dossier (copié dans le projet)",
                        html: svg("folder", 14) + "Dossier" }),
      h("button.btn", { onclick: pathDialog, title: "Lire des fichiers ou dossiers sur place, sans copie",
                        html: svg("pathin", 14) + "Par chemin" })),
    h("div.binfilters", {}, search, chips),
    h("div.bin", { id: "bin" }),
    h("div.dropzone", { id: "binEmpty" },
      h("b", {}, "Dépose tes rushs ici"),
      "fichiers ou dossier entier — vidéos, sons, images",
      h("div.meta", { style: { marginTop: "8px" } },
        "Gros fichiers : « Par chemin » les lit sur place, sans les copier.")),
    fileIn, dirIn);

  initDrop();
  on("media", renderGrid);
  on("doc", ({ reason }) => { if (["add", "delete", "undo", "redo", "load"].includes(reason)) renderGrid(); });
  render();
  if (S.proj.media.some((m) => ["pending", "processing"].includes(m.status))) startPolling();
}
export { init };

function pathDialog() {
  const ta = h("textarea", { rows: 5, placeholder: "C:\\Users\\moi\\Vidéos\\rush.mp4\nD:\\Tournage\\jour1" });
  const rec = h("input", { type: "checkbox" });
  const tl = h("input", { type: "checkbox" });
  modal({
    title: "Ajouter par chemin",
    body: h("div", {},
      h("div.meta", { style: { marginBottom: "8px" } },
        "Un fichier ou un dossier par ligne. Les fichiers sont lus là où ils sont : ne les déplace pas pendant le montage."),
      ta,
      h("label.check", { style: { marginTop: "10px" } }, rec, "Inclure les sous-dossiers"),
      h("label.check", { style: { marginTop: "6px" } }, tl, "Poser aussi sur la timeline, dans l'ordre")),
    actions: [
      { label: "Annuler" },
      { label: "Ajouter", primary: true, onclick: async () => {
        const paths = ta.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        if (!paths.length) { toast("Colle au moins un chemin."); return false; }
        try { await importPaths(paths, { recursive: rec.checked, toTimeline: tl.checked }); }
        catch (e) { toast(e.message, 4000); return false; }
        return true;
      } },
    ],
  });
}

/* ---------------------------------------------------- glisser-déposer OS */

function initDrop() {
  let depth = 0;
  const veil = h("div.dropveil.hidden", {}, h("div", {}, "Dépose pour importer"));
  document.body.appendChild(veil);
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    depth++;
    veil.classList.remove("hidden");
  });
  window.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) veil.classList.add("hidden");
  });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    veil.classList.add("hidden");
    // Déposé sur la timeline : les médias y vont aussi, à la suite.
    const onTimeline = !!e.target.closest?.("#tl");
    const files = await filesFrom(e.dataTransfer);
    importFiles(files, { toTimeline: onTimeline });
  });
}

/** Fichiers d'un dépôt, dossiers parcourus récursivement. */
function filesFrom(dt) {
  // webkitGetAsEntry doit être appelé pendant l'évènement, avant tout await.
  const entries = [...dt.items].filter((i) => i.kind === "file")
    .map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return Promise.resolve([...dt.files]);
  const out = [];
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const f = await new Promise((res, rej) => entry.file(res, rej));
      try { Object.defineProperty(f, "webkitRelativePath", { value: prefix + f.name }); } catch (err) { /* lecture seule */ }
      out.push(f);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await walk(e, prefix + entry.name + "/");
      }
    }
  };
  return (async () => { for (const e of entries) await walk(e, ""); return out; })();
}

/* -------------------------------------------------------------- rendu */

function render() {
  renderGrid();
}

function visible() {
  return [...S.media.values()].filter((m) =>
    (B.filter === "all" || m.kind === B.filter) &&
    (!B.query || m.name.toLowerCase().includes(B.query)));
}

function usage() {
  const n = new Map();
  S.doc.clips.forEach((c) => c.media && n.set(c.media, (n.get(c.media) || 0) + 1));
  return n;
}

function renderGrid() {
  const grid = $("bin");
  if (!grid) return;
  [...$("binKinds").children].forEach((b) => b.classList.toggle("on", b.dataset.k === B.filter));
  const list = visible();
  const used = usage();
  grid.innerHTML = "";
  B.uploads.forEach((u) => grid.appendChild(uploadTile(u)));
  list.forEach((m) => grid.appendChild(tile(m, used.get(m.id) || 0)));
  $("binEmpty").classList.toggle("hidden", S.media.size > 0 || B.uploads.length > 0);
}

function uploadTile(u) {
  return h("div.tile", { id: "up-" + u.key },
    h("div.th", {}, h("span", { html: svg("down", 22) }),
      h("div.state", {}, `Envoi… ${Math.round(u.pct * 100)} %`),
      h("div.prog", {}, h("i", { style: { width: Math.round(u.pct * 100) + "%" } }))),
    h("div.nm.ell", { title: u.name }, u.name));
}
function renderUpload(u) {
  const el = document.getElementById("up-" + u.key);
  if (!el) return;
  el.querySelector(".state").textContent = `Envoi… ${Math.round(u.pct * 100)} % · ${mo(u.size)}`;
  el.querySelector(".prog > i").style.width = Math.round(u.pct * 100) + "%";
}

const KIND_ICON = { video: "film", audio: "note", image: "image" };

function tile(m, used) {
  const ready = m.status === "ready";
  const th = h("div.th", {});
  if (m.urls && m.urls.poster) th.appendChild(h("img", { src: m.urls.poster, alt: "", draggable: "false" }));
  else th.appendChild(h("span", { html: svg(KIND_ICON[m.kind] || "file", 26) }));
  th.appendChild(h("span.kind", { html: svg(KIND_ICON[m.kind] || "file", 11) }));
  if (m.kind !== "image" && m.duration) th.appendChild(h("span.dur.num", {}, fmt(m.duration)));
  if (ready) {
    th.appendChild(h("button.add", {
      title: "Ajouter à la timeline", html: svg("plus", 14),
      onclick: (e) => { e.stopPropagation(); addToTimeline(m); },
    }));
  }
  const badges = h("div.badges");
  const tr = (m.transcript || {}).status;
  if (tr === "done") badges.appendChild(h("span.badge.ok", { title: "Transcrit : prêt pour les sous-titres et les blancs" }, "TXT"));
  else if (tr === "running" || tr === "queued") badges.appendChild(h("span.badge.run", { title: "Transcription en cours" }, "TXT…"));
  if (used) badges.appendChild(h("span.badge", { title: `Utilisé ${used} fois dans la timeline` }, "×" + used));
  th.appendChild(badges);

  if (m.status === "pending" || m.status === "processing") {
    th.appendChild(h("div.state", {}, m.status === "pending" ? "En attente…" : `Préparation… ${Math.round(m.progress || 0)} %`));
    th.appendChild(h("div.prog", {}, h("i", { style: { width: (m.progress || 0) + "%" } })));
  } else if (m.status === "error") {
    th.appendChild(h("div.state", { title: m.error }, h("span.err", {}, "Illisible"), h("br"), (m.error || "").slice(0, 60)));
  } else if (m.status === "missing") {
    th.appendChild(h("div.state", { title: m.path }, h("span.err", {}, "Fichier introuvable"), h("br"), "clic droit : relier"));
  }

  const el = h("div.tile", {
    dataset: { id: m.id }, draggable: ready ? "true" : "false",
    title: `${m.name}\n${describe(m)}`,
    ondblclick: () => ready && addToTimeline(m),
    oncontextmenu: (e) => { e.preventDefault(); tileMenu(e, m); },
    ondragstart: (e) => {
      e.dataTransfer.setData("application/x-montage-media", m.id);
      e.dataTransfer.effectAllowed = "copy";
      S.dragMedia = m.id;
      emit("mediadrag", { id: m.id, on: true });
    },
    ondragend: () => { S.dragMedia = null; emit("mediadrag", { id: m.id, on: false }); },
  }, th, h("div.nm.ell", {}, m.name));
  if (ready && m.thumbs && m.thumbs.count > 1 && m.urls.thumbs) scrubber(th, m);
  return el;
}

function describe(m) {
  if (m.status !== "ready") return m.status === "error" ? m.error : "";
  const parts = [];
  if (m.kind !== "audio") parts.push(`${m.w}×${m.h}`);
  if (m.kind === "video") parts.push(`${Math.round(m.fps)} i/s`, m.has_audio ? "avec son" : "sans son");
  if (m.duration && m.kind !== "image") parts.push(fmt(m.duration));
  parts.push(m.copied ? "copié dans le projet" : "lu sur place");
  return parts.join(" · ");
}

/** Survol de la vignette : elle défile dans le rush (planche de vignettes). */
function scrubber(th, m) {
  const img = th.querySelector("img");
  const t = m.thumbs;
  th.onpointermove = (e) => {
    const r = th.getBoundingClientRect();
    const i = Math.min(t.count - 1, Math.floor(((e.clientX - r.left) / r.width) * t.count));
    const col = i % t.cols, row = Math.floor(i / t.cols);
    th.style.backgroundImage = `url("${m.urls.thumbs}")`;
    th.style.backgroundSize = `${t.cols * 100}% ${t.rows * 100}%`;
    th.style.backgroundPosition = `${t.cols > 1 ? (col / (t.cols - 1)) * 100 : 0}% ${t.rows > 1 ? (row / (t.rows - 1)) * 100 : 0}%`;
    if (img) img.style.opacity = 0;
  };
  th.onpointerleave = () => {
    th.style.backgroundImage = "";
    if (img) img.style.opacity = 1;
  };
}

function tileMenu(e, m) {
  const used = usage().get(m.id) || 0;
  menu(e.clientX, e.clientY, [
    { label: "Ajouter à la timeline", icon: "plus", disabled: m.status !== "ready", onclick: () => addToTimeline(m) },
    m.kind !== "image" && m.has_audio !== false ? {
      label: (m.transcript || {}).status === "done" ? "Retranscrire" : "Transcrire (Whisper)", icon: "cc",
      disabled: m.status !== "ready", onclick: () => emit("transcribe", { ids: [m.id], force: (m.transcript || {}).status === "done" }),
    } : null,
    "-",
    m.status === "error" ? { label: "Relancer la préparation", icon: "refresh", onclick: () => retry(m) } : null,
    m.status === "missing" || !m.copied ? { label: "Relier à un autre fichier…", icon: "link", onclick: () => relink(m) } : null,
    { label: "Copier le chemin", icon: "copy", onclick: () => copy(m.path) },
    "-",
    { label: used ? `Retirer du projet (et ses ${used} clip${used > 1 ? "s" : ""})` : "Retirer du projet",
      icon: "trash", onclick: () => remove(m, used) },
  ]);
}

async function retry(m) {
  try { await post(`/api/timeline/${S.pid}/media/${m.id}/retry`); startPolling(); }
  catch (e) { toast(e.message); }
}

function relink(m) {
  const input = h("input", { type: "text", value: m.path });
  modal({
    title: "Relier le média",
    body: h("div", {}, h("div.meta", { style: { marginBottom: "8px" } },
      `Nouveau chemin de « ${m.name} ». Les clips de la timeline sont conservés.`), input),
    actions: [{ label: "Annuler" }, { label: "Relier", primary: true, onclick: async () => {
      try { await post(`/api/timeline/${S.pid}/media/${m.id}/relink`, { path: input.value }); startPolling(); }
      catch (e) { toast(e.message); return false; }
      return true;
    } }],
  });
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Chemin copié."); }
  catch (e) { toast(text, 5000); }
}

async function remove(m, used) {
  if (used && !confirm(`Retirer « ${m.name} » ?\nSes ${used} clip${used > 1 ? "s" : ""} disparaîtront de la timeline.`)) return;
  if (used) {
    edit((doc) => {
      M.deleteClips(doc, new Set(doc.clips.filter((c) => c.media === m.id).map((c) => c.id)));
      M.reflowCaptions(doc);
    }, "delete");
  }
  try {
    await del(`/api/timeline/${S.pid}/media/${m.id}`);
    B.gen++;
    const list = [...S.media.values()].filter((x) => x.id !== m.id);
    setMedia(list);
    emit("media");
    toast(`« ${m.name} » retiré du projet.`);
  } catch (e) { toast(e.message); }
}
