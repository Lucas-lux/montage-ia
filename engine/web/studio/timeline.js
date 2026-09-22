/* Timeline : règle, pistes, clips, tête de lecture et tous les gestes de
   montage (sélection, déplacement, rognage, dépôt depuis les médias).

   Rendu en deux couches par piste :
     - un canevas de la largeur de la ZONE VISIBLE (collé à gauche par
       `position: sticky`) où sont dessinés fonds, vignettes et formes
       d'onde — on ne dessine jamais ce qui est hors champ ;
     - un élément DOM par clip, transparent, pour la souris : bords de
       rognage, étiquette, sélection.

   Un geste (glisser, rogner) repart à chaque mouvement de l'état d'origine
   et y rejoue l'opération : pas de dérive, et un mouvement impossible
   laisse simplement le dernier état valide.

   Mouvement, comme dans CapCut : les clips ne sautent jamais d'une place à
   l'autre, leur position affichée glisse vers leur place réelle (`disp` vers
   `targets`). Pendant un glisser, le clip tenu se détache et suit la souris
   (« flottant »), les autres s'écartent pour lui faire de la place et un
   emplacement en pointillés montre où il va se poser. */

import * as A from "./actions.js";
import { bytes } from "./api.js";
import * as M from "./model.js";
import { S, begin, cancelBegin, changed, edit, emit, end, on, select, selectNone, setTime,
         toggle } from "./store.js";
import { $, clamp, fmt, h, iconBtn, menu, rafThrottle, svg, tc, toast, typing } from "./util.js";

const HEAD_W = 168;
const ROW_H = { text: 34, video: 58, main: 72, audio: 50 };
const SNAP_PX = 8;
const PPS_MIN = 2, PPS_MAX = 600;

export const T = {
  pps: 60,              // pixels par seconde
  snap: true,
  rows: [],             // [{ tid, el, lane, canvas, h }]
  clipEls: new Map(),   // id -> élément
  rowKey: "",
  sprites: new Map(),   // url -> Image
  waves: new Map(),     // media id -> Uint8Array | "loading"
  cutPreview: [],       // [{ tid, a, b }] : ce que les outils automatiques vont retirer
  gesture: null,
  dropGhost: null,       // dépôt depuis les médias
  landing: [],           // où les clips glissés vont se poser [{ tid, t, dur }]
  disp: new Map(),       // id -> { x, w } affichés (glissent vers targets)
  targets: new Map(),    // id -> { x, w } réels
  anim: 0,
  instant: false,        // rognage, zoom : pas d'animation
  float: null,           // { ids, items } clips tenus qui suivent la souris
};

const floating = (id) => !!T.float && T.float.ids.has(id);

let scroll, inner, rulerCanvas, playhead, snapLine;

const rowHeight = (t) => (t.kind === "video" ? (t.main ? ROW_H.main : ROW_H.video) : ROW_H[t.kind]);
const viewW = () => Math.max(50, scroll.clientWidth - HEAD_W);
const contentW = () => Math.max(viewW(), (M.duration(S.doc) + 30) * T.pps, 60 * T.pps);
const timeAtX = (clientX) => {
  const r = scroll.getBoundingClientRect();
  return Math.max(0, (clientX - r.left - HEAD_W + scroll.scrollLeft) / T.pps);
};

/* ================================================================= init */

export function init() {
  scroll = $("tlscroll");
  inner = $("tlinner");
  buildTools();
  inner.innerHTML = "";

  const ruler = h("div.rl", {});
  rulerCanvas = h("canvas", {});
  ruler.appendChild(rulerCanvas);
  const corner = h("div.corner", {},
    h("button.btn.sm", { title: "Ajouter une piste", html: svg("plus", 13) + "Piste",
                         onclick: (e) => addTrackMenu(e) }));
  inner.appendChild(h("div.rulerrow", {}, corner, ruler));
  inner.appendChild(h("div", { id: "tlrows" }));
  playhead = h("div.playhead", {});
  snapLine = h("div.snapline.hidden", {});
  inner.append(playhead, snapLine, h("span.tlhint.hidden", { id: "tlHint" }));

  ruler.onpointerdown = rulerDown;
  ruler.oncontextmenu = rulerMenu;
  playhead.onpointerdown = (e) => { if (e.offsetY < 16) rulerDown(e); };
  scroll.addEventListener("scroll", rafThrottle(() => { draw(); placePlayhead(); }));
  scroll.addEventListener("wheel", onWheel, { passive: false });
  scroll.addEventListener("pointerdown", emptyDown);
  new ResizeObserver(rafThrottle(() => render())).observe(scroll);

  initDnD();
  initKeys();

  on("doc", () => render());
  on("select", () => { updateClipClasses(); draw(); });
  on("time", ({ from }) => { placePlayhead(); if (from === "play") follow(); });
  on("media", () => { render(); });
  on("cutpreview", ({ ranges }) => { T.cutPreview = ranges || []; draw(); });
  try {
    const z = +localStorage.getItem("studio.pps");
    if (z >= PPS_MIN && z <= PPS_MAX) T.pps = z;
  } catch (e) { /* stockage indisponible */ }
  render();
  requestAnimationFrame(() => { if (M.duration(S.doc)) fit(); });
}

/* ============================================================ barre d'outils */

function buildTools() {
  const bar = $("tltools");
  bar.innerHTML = "";
  const zoom = h("input", { type: "range", min: 0, max: 1000, id: "tlZoom", title: "Zoom (Ctrl + molette)" });
  zoom.oninput = () => setZoom(sliderToPps(+zoom.value));
  bar.append(
    iconBtn("split", "Diviser à la tête de lecture (Ctrl+B)", () => A.split()),
    iconBtn("trash", "Supprimer (Suppr)", () => A.remove()),
    iconBtn("copy", "Dupliquer (Ctrl+D)", () => A.duplicate()),
    h("span.vsep"),
    h("button.btn.sm.quiet", { title: "Séparer le son de la vidéo sélectionnée", html: svg("detach", 15) + "Séparer le son",
                               onclick: () => A.toggleDetach() }),
    iconBtn("unlink", "Dissocier les clips liés", () => A.unlinkSelection()),
    iconBtn("flag", "Ajouter un marqueur (M)", () => A.addMarker()),
    iconBtn("freeze", "Arrêt sur image (F)", () => A.freezeFrame()),
    h("span.vsep"),
    h("button.btn.sm.quiet", { title: "Supprimer les blancs (outils IA)", html: svg("silence", 15) + "Blancs",
                               onclick: () => emit("tool", { name: "silence" }) }),
    h("button.btn.sm.quiet", { title: "Sous-titres automatiques", html: svg("cc", 15) + "Sous-titres",
                               onclick: () => emit("tool", { name: "captions" }) }),
    h("button.btn.sm.quiet", { title: "Ajouter un texte", html: svg("text", 15) + "Texte",
                               onclick: () => emit("tool", { name: "text" }) }),
    h("div.grow"),
    h("span.meta.num", { id: "tlInfo", style: { marginRight: "8px" } }),
    h("button.btn.icon.quiet" + (T.snap ? ".on" : ""), { id: "tlSnap", title: "Aimantation (N)",
      html: svg("magnet", 16), onclick: () => toggleSnap() }),
    iconBtn("zoomout", "Dézoomer (-)", () => setZoom(T.pps / 1.4)),
    h("div.zoom", {}, zoom),
    iconBtn("zoomin", "Zoomer (+)", () => setZoom(T.pps * 1.4)),
    iconBtn("fit", "Tout voir (Maj+Z)", () => fit()),
  );
}

const sliderToPps = (v) => PPS_MIN * Math.pow(PPS_MAX / PPS_MIN, v / 1000);
const ppsToSlider = (p) => Math.round(1000 * Math.log(p / PPS_MIN) / Math.log(PPS_MAX / PPS_MIN));

function toggleSnap() {
  T.snap = !T.snap;
  $("tlSnap").classList.toggle("on", T.snap);
  toast(T.snap ? "Aimantation activée" : "Aimantation désactivée");
}

/** Zoom, ancré sur un instant (souris ou tête de lecture) qui ne bouge pas à l'écran. */
export function setZoom(pps, anchorT, anchorX) {
  pps = clamp(pps, PPS_MIN, PPS_MAX);
  if (anchorT === undefined) {
    anchorT = S.t;
    anchorX = clamp(S.t * T.pps - scroll.scrollLeft, 0, viewW());
  }
  T.pps = pps;
  try { localStorage.setItem("studio.pps", pps); } catch (e) { /* stockage indisponible */ }
  T.instant = true;
  render();
  T.instant = false;
  scroll.scrollLeft = Math.max(0, anchorT * pps - anchorX);
  draw();
  placePlayhead();
}

export function fit() {
  const d = M.duration(S.doc);
  if (!d) return;
  setZoom((viewW() - 40) / d, 0, 0);
  scroll.scrollLeft = 0;
}

function onWheel(e) {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    const r = scroll.getBoundingClientRect();
    const x = e.clientX - r.left - HEAD_W;
    const t = (x + scroll.scrollLeft) / T.pps;
    setZoom(T.pps * Math.pow(1.0018, -e.deltaY), t, x);
  } else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
    // défilement horizontal naturel
  } else if (!e.target.closest(".thead") && scroll.scrollHeight <= scroll.clientHeight + 2) {
    // pas de pistes à faire défiler verticalement : la molette parcourt le temps
    e.preventDefault();
    scroll.scrollLeft += e.deltaY;
  }
}

/* ================================================================= rendu */

export function render() {
  if (!scroll || !S.doc) return;
  const rowsBox = $("tlrows");
  const key = S.doc.tracks.map((t) => [t.id, t.kind, t.main, t.name, t.muted, t.hidden, t.locked].join(":")).join("|");
  if (key !== T.rowKey) {
    T.rowKey = key;
    rowsBox.innerHTML = "";
    T.rows = S.doc.tracks.map((t) => buildRow(t));
    T.rows.forEach((r) => rowsBox.appendChild(r.el));
    rowsBox.appendChild(h("div.trow.newtrack", {},
      h("div.thead", {}, h("span.meta", {}, "")),
      h("div.lane", {})));
    // les clips tenus (hors des pistes) survivent à la reconstruction
    for (const id of [...T.clipEls.keys()]) if (!floating(id)) T.clipEls.delete(id);
  }
  const w = contentW();
  inner.style.width = HEAD_W + w + "px";
  T.rows.forEach((r) => { r.lane.style.width = w + "px"; });

  // clips : réconciliation par id ; la position affichée glisse vers la vraie
  const seen = new Set();
  T.targets.clear();
  for (const c of S.doc.clips) {
    const row = T.rows.find((r) => r.tid === c.track);
    if (!row) continue;
    seen.add(c.id);
    let el = T.clipEls.get(c.id);
    if (!el) {
      el = buildClip(c);
      T.clipEls.set(c.id, el);
    }
    const tg = { x: c.start * T.pps, w: Math.max(3, c.dur * T.pps) };
    T.targets.set(c.id, tg);
    if (!T.disp.has(c.id) || T.instant) T.disp.set(c.id, { ...tg });
    if (!floating(c.id) && el.parentNode !== row.lane) row.lane.appendChild(el);
    updateClip(el, c);
  }
  for (const [id, el] of T.clipEls) {
    if (!seen.has(id)) { el.remove(); T.clipEls.delete(id); }
  }
  for (const id of [...T.disp.keys()]) if (!seen.has(id)) T.disp.delete(id);
  kick();
  renderTransitionBadges();
  renderCutMarks();
  const empty = !S.doc.clips.length;
  const hint = $("tlHint");
  if (hint) {
    hint.classList.toggle("hidden", !empty);
    hint.textContent = "Glisse des médias ici, ou clique sur « + » dans le panneau Médias.";
  }
  $("tlZoom").value = ppsToSlider(T.pps);
  const n = S.doc.clips.length;
  $("tlInfo").textContent = n ? `${n} clip${n > 1 ? "s" : ""} · ${fmt(M.duration(S.doc))}` : "";
  draw();
  placePlayhead();
}

const KIND_ICON = { video: "film", audio: "note", text: "text" };

function buildRow(t) {
  const hgt = rowHeight(t);
  const canvas = h("canvas.content", {});
  const lane = h("div.lane", { style: { height: hgt + "px" } }, canvas);
  const name = h("span.tn.ell", { title: t.main ? "Piste principale (magnétique)" : t.name,
                                  ondblclick: () => renameTrack(t, name) }, t.main ? "Principale" : t.name);
  const btns = [];
  if (t.kind !== "text") {
    btns.push(h("button.btn.sm.icon.quiet" + (t.muted ? ".on" : ""), {
      title: t.muted ? "Réactiver le son de la piste" : "Couper le son de la piste",
      html: svg(t.muted ? "mute" : "vol", 13), onclick: () => trackFlag(t, "muted") }));
  }
  if (t.kind !== "audio") {
    btns.push(h("button.btn.sm.icon.quiet" + (t.hidden ? ".on" : ""), {
      title: t.hidden ? "Afficher la piste" : "Masquer la piste",
      html: svg(t.hidden ? "eyeoff" : "eye", 13), onclick: () => trackFlag(t, "hidden") }));
  }
  btns.push(h("button.btn.sm.icon.quiet" + (t.locked ? ".on" : ""), {
    title: t.locked ? "Déverrouiller" : "Verrouiller (plus aucune modification)",
    html: svg(t.locked ? "lock" : "unlock", 13), onclick: () => trackFlag(t, "locked") }));
  const head = h("div.thead", { oncontextmenu: (e) => { e.preventDefault(); trackMenu(e, t); } },
    h("span.tk", { html: svg(t.main ? "magnet" : KIND_ICON[t.kind], 13) }), name, ...btns);
  const el = h("div.trow" + (t.main ? ".is-main" : "") + (t.locked ? ".locked" : "") +
               (t.muted ? ".muted" : "") + (t.hidden ? ".hiddentrack" : ""),
               { dataset: { tid: t.id, kind: t.kind } }, head, lane);
  return { tid: t.id, kind: t.kind, main: !!t.main, el, lane, canvas, h: hgt };
}

function buildClip(c) {
  const el = h("div.clip", { dataset: { id: c.id } },
    h("div.cl", {}),
    h("div.edge.l", { title: "Rogner le début" }),
    h("div.edge.r", { title: "Rogner la fin" }));
  el.onpointerdown = (e) => clipDown(e, el.dataset.id);
  el.ondblclick = () => {
    const clip = S.doc.clips.find((x) => x.id === el.dataset.id);
    if (clip) emit("editclip", { id: clip.id, kind: clip.kind });
  };
  el.oncontextmenu = (e) => { e.preventDefault(); clipMenu(e, el.dataset.id); };
  return el;
}

function clipLabel(c) {
  if (c.kind === "text") {
    const words = M.liveWords(c).map((w) => w.text).join(" ");
    return (c.auto ? svg("cc", 11) : svg("text", 11)) + `<span class="ell">${esc(words || "Texte")}</span>`;
  }
  const m = S.media.get(c.media);
  let html = svg(c.kind === "image" ? "image" : KIND_ICON[c.kind], 11) +
             `<span class="ell">${esc(m ? m.name : "Média manquant")}</span>`;
  if ((c.speed || 1) !== 1) html += `<span class="badge">${+(c.speed).toFixed(2)}×</span>`;
  if (c.muted) html += svg("mute", 11);
  if (c.kind === "video" && c.detached) html += `<span title="Son séparé">${svg("detach", 11)}</span>`;
  if (c.link) html += `<span title="Lié">${svg("link", 11)}</span>`;
  return html;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

function updateClip(el, c) {
  if (!floating(c.id)) {
    const d = T.disp.get(c.id) || { x: c.start * T.pps, w: Math.max(3, c.dur * T.pps) };
    el.style.left = d.x + "px";
    el.style.width = d.w + "px";
  }
  const cls = "clip " + c.kind + (S.sel.has(c.id) ? " sel" : "") + (c.gone ? " gone" : "") +
              (floating(c.id) ? " floating" : "");
  if (el.className !== cls) el.className = cls;
  const label = clipLabel(c);
  const cl = el.firstChild;
  if (cl._html !== label) { cl.innerHTML = label; cl._html = label; }
  el.title = clipTitle(c);
  // fondus audio : triangles aux extrémités
  el.querySelectorAll(".fade").forEach((f) => f.remove());
  if (c.fade_in > 0) el.appendChild(h("div.fade", { style: { left: 0, width: c.fade_in * T.pps + "px" } }));
  if (c.fade_out > 0) el.appendChild(h("div.fade.out", { style: { right: 0, width: c.fade_out * T.pps + "px" } }));
  el.style.display = c.gone ? "none" : "";
}

/** Un repère rouge à chaque passage retiré par la suppression des blancs. */
function renderCutMarks() {
  inner.querySelectorAll(".cutmk").forEach((b) => b.remove());
  for (const p of M.removedPassages(S.doc)) {
    const row = T.rows.find((r) => r.tid === p.track);
    if (!row) continue;
    const mk = h("div.cutmk", { title: `−${p.dur.toFixed(2).replace(".", ",")} s retirés — clic pour voir ou restaurer`,
                                style: { left: p.t * T.pps + "px" }, html: svg("cut", 10) });
    mk.onpointerdown = (e) => e.stopPropagation();
    mk.onclick = (e) => {
      e.stopPropagation();
      const b = mk.getBoundingClientRect();
      emit("passage", { entry: p, x: b.left + b.width / 2, y: b.top });
    };
    row.lane.appendChild(mk);
  }
}

/** Un losange à chaque coupe (deux clips vidéo qui se touchent) : clic pour
 *  choisir la transition. Plein quand une transition est posée. */
function renderTransitionBadges() {
  inner.querySelectorAll(".tbadge").forEach((b) => b.remove());
  for (const row of T.rows) {
    if (row.kind !== "video") continue;
    const clips = M.trackClips(S.doc, row.tid).filter((c) => c.kind === "video" || c.kind === "image");
    for (let i = 1; i < clips.length; i++) {
      const a = clips[i - 1], b = clips[i];
      if (Math.abs(M.clipEnd(a) - b.start) > 1e-3) continue;
      const tr = M.transIn(S.doc, b);
      const label = tr ? (M.TRANSITIONS.find((x) => x[0] === tr.type) || [0, tr.type])[1] + ` · ${tr.d.toFixed(1).replace(".", ",")} s` : "Ajouter une transition";
      const badge = h("div.tbadge" + (tr ? ".on" : ""), { title: label, style: { left: b.start * T.pps + "px" } });
      badge.onpointerdown = (e) => e.stopPropagation();
      badge.onclick = (e) => { e.stopPropagation(); transitionMenu(e, b); };
      if (tr) badge.style.width = badge.style.height = Math.max(12, Math.min(18, tr.d * T.pps * 0.5)) + "px";
      row.lane.appendChild(badge);
    }
  }
}

function transitionMenu(e, clip) {
  const cur = clip.trans || {};
  const d = cur.dur || 0.5;
  menu(e.clientX, e.clientY, [
    ...M.TRANSITIONS.map(([k, label]) => ({
      label: (cur.type === k ? "✓ " : "") + label, icon: "transition",
      onclick: () => A.setTransition([clip.id], k, d) })),
    "-",
    ...[0.3, 0.5, 1, 1.5, 2].map((x) => ({
      label: (cur.type && Math.abs(d - x) < 1e-3 ? "✓ " : "") + `Durée ${String(x).replace(".", ",")} s`,
      disabled: !cur.type, onclick: () => A.setTransition([clip.id], cur.type, x) })),
    "-",
    { label: "Même transition sur toutes les coupes de la piste", icon: "copy", disabled: !cur.type,
      onclick: () => A.transitionEverywhere(clip.track, cur.type, d) },
    { label: "Aucune transition", icon: "close", disabled: !cur.type, onclick: () => A.setTransition([clip.id], "") },
  ]);
}

function clipTitle(c) {
  const parts = [`${tc(c.start, S.doc.canvas.fps)} → ${tc(M.clipEnd(c), S.doc.canvas.fps)} (${fmt(c.dur)})`];
  if (c.media) {
    const m = S.media.get(c.media);
    if (m) parts.unshift(m.name);
    if (c.kind !== "image") parts.push(`source ${fmt(c.in)} → ${fmt(M.srcEnd(c))}`);
  }
  if (c.kind === "text") parts.unshift(M.liveWords(c).map((w) => w.text).join(" "));
  return parts.join("\n");
}

function updateClipClasses() {
  for (const [id, el] of T.clipEls) el.classList.toggle("sel", S.sel.has(id));
}

/* ------------------------------------------------------------ animation */

/** Fait glisser les positions affichées vers les vraies (≈ 150 ms). */
function kick() {
  if (!T.anim) T.anim = requestAnimationFrame(step);
}
function step() {
  T.anim = 0;
  let moving = false;
  for (const [id, tg] of T.targets) {
    const d = T.disp.get(id);
    if (!d) continue;
    const dx = tg.x - d.x, dw = tg.w - d.w;
    if (Math.abs(dx) < 0.5 && Math.abs(dw) < 0.5) {
      if (d.x === tg.x && d.w === tg.w) continue;
      d.x = tg.x; d.w = tg.w;
    } else {
      d.x += dx * 0.32; d.w += dw * 0.32;
      moving = true;
    }
    const el = T.clipEls.get(id);
    if (el && !floating(id)) { el.style.left = d.x + "px"; el.style.width = d.w + "px"; }
  }
  drawNow();
  if (moving) kick();
}

/* ---------------------------------------------------------------- dessin */

const draw = rafThrottle(drawNow);

function drawNow() {
  if (!scroll) return;
  const dpr = window.devicePixelRatio || 1;
  const vw = viewW();
  const sl = scroll.scrollLeft;
  drawRuler(dpr, vw, sl);
  for (const row of T.rows) {
    const cv = row.canvas;
    const W = Math.round(vw * dpr), H = Math.round(row.h * dpr);
    if (cv.width !== W || cv.height !== H) {
      cv.width = W; cv.height = H;
      cv.style.width = vw + "px"; cv.style.height = row.h + "px";
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, vw, row.h);
    const tr = M.track(S.doc, row.tid);
    for (const c of S.doc.clips) {
      if (c.track !== row.tid || c.gone || floating(c.id)) continue;
      const d = T.disp.get(c.id);
      const x = (d ? d.x : c.start * T.pps) - sl, w = d ? d.w : c.dur * T.pps;
      if (x > vw || x + w < 0) continue;
      drawClip(ctx, c, x, 3, w, row.h - 6, vw, tr);
    }
    // emplacement où les clips glissés vont se poser
    for (const g of T.landing) {
      if (g.tid !== row.tid) continue;
      const x = g.t * T.pps - sl, w = Math.max(4, g.dur * T.pps);
      if (x > vw || x + w < 0) continue;
      ctx.fillStyle = "rgba(255, 255, 255, .07)";
      roundRect(ctx, x + 0.5, 3.5, w - 1, row.h - 7, 5);
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, .85)";
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 1;
    }
    for (const p of T.cutPreview) {
      if (p.tid !== row.tid) continue;
      const x = p.a * T.pps - sl, w = (p.b - p.a) * T.pps;
      if (x > vw || x + w < 0) continue;
      ctx.fillStyle = "rgba(242, 58, 82, .55)";
      ctx.fillRect(x, 3, Math.max(1, w), row.h - 6);
      ctx.fillStyle = "rgba(255, 255, 255, .9)";
      ctx.fillRect(x, 3, 1, row.h - 6);
    }
    if (T.dropGhost && T.dropGhost.tid === row.tid) {
      const g = T.dropGhost;
      const x = g.t * T.pps - sl;
      ctx.strokeStyle = "#fff";
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(x + 0.5, 3.5, Math.max(4, g.dur * T.pps) - 1, row.h - 7);
      ctx.setLineDash([]);
    }
  }
}

const COLORS = {
  video: ["#2E3B52", "#4A5E82"], image: ["#353F4B", "#56657A"],
  audio: ["#173F31", "#2C6E55"], text: ["#3E2F61", "#6A52A3"],
};

function roundRect(ctx, x, y, w, hh, r) {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, hh, r);
  else ctx.rect(x, y, w, hh);
}

function drawClip(ctx, c, x, y, w, hh, vw, tr) {
  const [bg] = COLORS[c.kind] || COLORS.video;
  ctx.save();
  roundRect(ctx, x, y, w, hh, 5);
  ctx.fillStyle = bg;
  ctx.fill();
  ctx.clip();
  const m = c.media ? S.media.get(c.media) : null;
  if (m && (c.kind === "video" || c.kind === "image")) {
    const withWave = c.kind === "video" && m.has_audio && !c.detached && hh > 40;
    const stripH = withWave ? hh - 16 : hh;
    drawFilm(ctx, c, m, x, y, w, stripH, vw);
    if (withWave) {
      ctx.fillStyle = "rgba(0, 0, 0, .55)";
      ctx.fillRect(Math.max(x, 0), y + stripH, Math.min(w, vw), 16);
      drawWave(ctx, c, m, x, y + stripH + 1, w, 14, vw, "rgba(86, 214, 154, .85)");
    }
  } else if (m && c.kind === "audio") {
    drawWave(ctx, c, m, x, y + 14, w, hh - 16, vw, c.muted ? "rgba(160,160,160,.6)" : "#56D69A");
  }
  if (c.muted || (tr && (tr.muted && c.kind === "audio"))) {
    ctx.fillStyle = "rgba(0, 0, 0, .35)";
    ctx.fillRect(x, y, w, hh);
  }
  ctx.restore();
}

function sprite(url) {
  let img = T.sprites.get(url);
  if (!img) {
    img = new Image();
    img.onload = () => draw();
    img.src = url;
    T.sprites.set(url, img);
  }
  return img.complete && img.naturalWidth ? img : null;
}

function drawFilm(ctx, c, m, x, y, w, hh, vw) {
  const th = m.thumbs;
  if (!th || !m.urls || !m.urls.thumbs) return;
  const img = sprite(m.urls.thumbs);
  if (!img) return;
  const tw = Math.max(8, th.w * hh / th.h);
  const first = Math.max(0, Math.floor(-x / tw));
  const speed = c.speed || 1;
  for (let i = first; ; i++) {
    const px = x + i * tw;
    if (px > Math.min(vw, x + w)) break;
    let idx = 0;
    if (c.kind === "video" && th.interval > 0) {
      const s = c.in + ((i * tw + tw / 2) / T.pps) * speed;
      idx = clamp(Math.floor(s / th.interval), 0, th.count - 1);
    }
    const sx = (idx % th.cols) * th.w, sy = Math.floor(idx / th.cols) * th.h;
    ctx.drawImage(img, sx, sy, th.w, th.h, px, y, tw, hh);
  }
}

function wave(m) {
  const cur = T.waves.get(m.id);
  if (cur && cur !== "loading") return cur;
  if (!cur && m.urls && m.urls.wave) {
    T.waves.set(m.id, "loading");
    bytes(m.urls.wave).then((b) => { T.waves.set(m.id, b); draw(); })
      .catch(() => T.waves.delete(m.id));
  }
  return null;
}

function drawWave(ctx, c, m, x, y, w, hh, vw, color) {
  const data = wave(m);
  if (!data || !m.waveform) return;
  const rate = m.waveform.rate;
  const speed = c.speed || 1;
  const gain = Math.min(1.3, 0.35 + 0.65 * (c.volume ?? 1));
  const xs = Math.max(0, Math.floor(x)), xe = Math.min(vw, Math.ceil(x + w));
  ctx.fillStyle = color;
  const mid = y + hh / 2;
  for (let px = xs; px < xe; px++) {
    const s0 = c.in + ((px - x) / T.pps) * speed;
    const s1 = s0 + speed / T.pps;
    let i0 = Math.floor(s0 * rate);
    const i1 = Math.max(i0 + 1, Math.ceil(s1 * rate));
    let peak = 0;
    for (; i0 < i1 && i0 < data.length; i0++) if (data[i0] > peak) peak = data[i0];
    if (!peak) continue;
    const a = Math.min(1, (peak / 255) * gain) * hh;
    ctx.fillRect(px, mid - a / 2, 1, Math.max(1, a));
  }
}

function drawRuler(dpr, vw, sl) {
  const H = 26;
  const cv = rulerCanvas;
  if (cv.width !== Math.round(vw * dpr) || cv.height !== H * dpr) {
    cv.width = Math.round(vw * dpr); cv.height = H * dpr;
    cv.style.width = vw + "px"; cv.style.height = H + "px";
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, vw, H);
  const steps = [1 / 30, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
  const major = steps.find((s) => s * T.pps >= 70) || 1200;
  const minor = major / (major >= 1 && major % 5 === 0 ? 5 : 2);
  const t0 = sl / T.pps, t1 = (sl + vw) / T.pps;
  ctx.strokeStyle = "#3A3D45";
  ctx.fillStyle = "#8C9099";
  ctx.font = "10.5px Segoe UI, system-ui, sans-serif";
  ctx.beginPath();
  for (let t = Math.floor(t0 / minor) * minor; t <= t1 + minor; t += minor) {
    const x = Math.round(t * T.pps - sl) + 0.5;
    const isMajor = Math.abs(t / major - Math.round(t / major)) < 1e-6;
    ctx.moveTo(x, isMajor ? 12 : 19);
    ctx.lineTo(x, H);
    if (isMajor && t >= 0) ctx.fillText(rulerLabel(t, major), x + 4, 11);
  }
  ctx.stroke();
  for (const m of S.doc.markers || []) {
    const x = m.t * T.pps - sl;
    if (x < -6 || x > vw + 6) continue;
    ctx.fillStyle = m.color || "#F23A52";
    ctx.beginPath();
    ctx.moveTo(x - 5, 14); ctx.lineTo(x + 5, 14); ctx.lineTo(x, 22); ctx.closePath();
    ctx.fill();
  }
}

function rulerLabel(t, step) {
  if (step < 1) {
    const s = Math.floor(t % 60), m = Math.floor(t / 60);
    const f = Math.round((t - Math.floor(t)) * 100);
    return `${m}:${String(s).padStart(2, "0")}.${String(f).padStart(2, "0")}`;
  }
  return fmt(t);
}

/* ------------------------------------------------------------ tête de lecture */

function placePlayhead() {
  if (!playhead) return;
  const x = S.t * T.pps;
  playhead.style.left = HEAD_W + x + "px";
  playhead.style.display = x < scroll.scrollLeft - 1 ? "none" : "";
}

/** Pendant la lecture, la timeline suit la tête (par pages). */
function follow() {
  const x = S.t * T.pps;
  const sl = scroll.scrollLeft, vw = viewW();
  if (x > sl + vw - 20 || x < sl) scroll.scrollLeft = Math.max(0, x - 40);
}

function rulerDown(e) {
  if (e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  const seek = (ev) => setTime(snapTime(timeAtX(ev.clientX), null, ev.shiftKey), { from: "scrub" });
  seek(e);
  emit("scrub", { on: true });
  const move = (ev) => seek(ev);
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    snapLine.classList.add("hidden");
    emit("scrub", { on: false });
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function rulerMenu(e) {
  e.preventDefault();
  const t = timeAtX(e.clientX);
  const mk = (S.doc.markers || []).find((m) => Math.abs(m.t - t) * T.pps < 8);
  menu(e.clientX, e.clientY, [
    { label: "Ajouter un marqueur ici", icon: "flag", onclick: () => { setTime(t); A.addMarker(); } },
    mk ? { label: "Supprimer ce marqueur", icon: "trash", onclick: () => A.removeMarker(mk.id) } : null,
    (S.doc.markers || []).length ? { label: "Supprimer tous les marqueurs", icon: "trash",
      onclick: () => edit((doc) => { doc.markers = []; }, "marker") } : null,
  ]);
}

/* ================================================================ aimantation */

/** Points d'accroche : 0, tête de lecture, bords des clips, marqueurs. */
function snapPoints(exclude, doc = S.doc) {
  const pts = [0, S.t];
  for (const c of doc.clips) {
    if (exclude && exclude.has(c.id)) continue;
    pts.push(c.start, M.clipEnd(c));
  }
  (doc.markers || []).forEach((m) => pts.push(m.t));
  return pts;
}

/** Instant aimanté (ou tel quel si l'aimantation est coupée / Maj enfoncée). */
function snapTime(t, exclude, bypass) {
  snapLine.classList.add("hidden");
  if (!T.snap || bypass) return t;
  let best = null, bestD = SNAP_PX / T.pps;
  for (const p of snapPoints(exclude)) {
    const d = Math.abs(p - t);
    if (d < bestD) { best = p; bestD = d; }
  }
  if (best === null) return t;
  showSnap(best);
  return best;
}

/** Aimante un déplacement : le début OU la fin des clips déplacés. */
function snapDelta(dt, clips, exclude, bypass, doc = S.doc) {
  snapLine.classList.add("hidden");
  if (!T.snap || bypass) return dt;
  const pts = snapPoints(exclude, doc);
  let best = null, bestD = SNAP_PX / T.pps, at = 0;
  for (const c of clips) {
    for (const edgeT of [c.start + dt, M.clipEnd(c) + dt]) {
      for (const p of pts) {
        const d = Math.abs(p - edgeT);
        if (d < bestD) { bestD = d; best = p - edgeT; at = p; }
      }
    }
  }
  if (best === null) return dt;
  showSnap(at);
  return dt + best;
}

function showSnap(t) {
  snapLine.style.left = HEAD_W + t * T.pps + "px";
  snapLine.classList.remove("hidden");
}

/* ================================================================== gestes */

function rowAt(clientY) {
  for (const r of T.rows) {
    const b = r.el.getBoundingClientRect();
    if (clientY >= b.top && clientY < b.bottom) return r;
  }
  const first = T.rows[0], last = T.rows[T.rows.length - 1];
  if (first && clientY < first.el.getBoundingClientRect().top) return { tid: "new-top" };
  if (last && clientY >= last.el.getBoundingClientRect().bottom) return { tid: "new-bottom" };
  return null;
}

function clipDown(e, id) {
  if (e.button !== 0) return;
  e.stopPropagation();
  const clip = S.doc.clips.find((c) => c.id === id);
  if (!clip) return;
  const tr = M.track(S.doc, clip.track);
  const alone = e.altKey;                    // Alt : sans les partenaires liés
  const group = alone ? [clip.id] : [...M.linkedIds(S.doc, [clip.id])];
  const wasSel = S.sel.has(id);
  if (e.ctrlKey || e.metaKey) {
    group.forEach((g) => (wasSel ? S.sel.delete(g) : S.sel.add(g)));
    emit("select");
    return;
  }
  if (e.shiftKey) select(group, { add: true });
  else if (!wasSel) select(group);
  if (tr && tr.locked) return;

  const edge = e.target.classList.contains("edge") ? (e.target.classList.contains("l") ? "l" : "r") : null;
  if (edge) startTrim(e, clip, edge);
  else startMove(e, clip, { collapseTo: wasSel && S.sel.size > group.length && !e.shiftKey ? group : null });
}

/** Rogner un clip par un bord (et ses partenaires). */
function startTrim(e, clip, side) {
  const orig = JSON.stringify(S.doc);
  const id = clip.id;
  const x0 = e.clientX;
  let moved = false;
  T.gesture = { kind: "trim", ids: new Set([id]) };
  T.instant = true;                  // le bord suit la souris sans retard
  begin();
  const move = (ev) => {
    if (!moved && Math.abs(ev.clientX - x0) < 2) return;
    moved = true;
    const doc = JSON.parse(orig);
    const c = doc.clips.find((x) => x.id === id);
    const t = snapTime(timeAtX(ev.clientX), new Set([id]), ev.shiftKey);
    M.trimClip(doc, c, side, t, S.media);
    M.reflowCaptions(doc);
    S.doc = doc;
    changed({ light: true, reason: "trim" });
    emit("trimpreview", { id, side, t: side === "l" ? c.start : M.clipEnd(c), src: side === "l" ? c.in : M.srcEnd(c) });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    snapLine.classList.add("hidden");
    T.gesture = null;
    T.instant = false;
    emit("trimpreview", { id: null });
    if (moved) end("trim"); else cancelBegin();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/** Déplacer la sélection (et changer de piste). Piste principale : on
 *  réordonne ; ailleurs : on déplace librement tant qu'il n'y a pas de
 *  chevauchement. Glisser au-dessus de la première piste ou sous la dernière
 *  crée une nouvelle piste. */
function startMove(e, clip, { collapseTo } = {}) {
  const orig = JSON.stringify(S.doc);
  const ids = new Set(S.sel);
  const leadId = clip.id;
  const x0 = e.clientX, y0 = e.clientY;
  const lead0 = { start: clip.start, track: clip.track };
  const tkind = M.trackKindFor(clip.kind);
  const origDoc = JSON.parse(orig);
  const movingClips = origDoc.clips.filter((c) => ids.has(c.id));
  const tracksOf = new Set(movingClips.map((c) => c.track));
  let moved = false, lastValid = orig;
  T.gesture = { kind: "move", ids, moved: false };
  begin();

  const move = (ev) => {
    const dx = ev.clientX - x0, dy = ev.clientY - y0;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    if (!moved) { moved = true; T.gesture.moved = true; liftClips(ids); }
    autoScroll(ev);
    const doc = JSON.parse(orig);
    const lead = doc.clips.find((c) => c.id === leadId);
    const mainId = M.mainTrack(doc).id;
    let dt = (dx + scrollDelta()) / T.pps;
    // sur la principale, l'insertion se décide au milieu des clips : pas
    // d'aimant (les voisins bougent pour faire de la place) ; ailleurs, on
    // s'aimante aux clips tels qu'ils étaient au début du geste
    const overMain = (rowAt(ev.clientY) || {}).tid === mainId || (lead0.track === mainId && tracksOf.size === 1 &&
                     !(rowAt(ev.clientY) || {}).tid);
    if (!overMain) dt = snapDelta(dt, movingClips, ids, ev.shiftKey, origDoc);
    else snapLine.classList.add("hidden");

    // piste visée (même genre que le clip tenu)
    const row = rowAt(ev.clientY);
    let target = lead0.track;
    if (row && row.tid === "new-top" && tkind !== "audio" && tracksOf.size === 1) {
      target = M.addTrack(doc, tkind, { at: tkind === "text" ? 0 : undefined }).id;
    } else if (row && row.tid === "new-bottom" && tkind === "audio" && tracksOf.size === 1) {
      target = M.addTrack(doc, "audio").id;
    } else if (row && row.tid && !row.tid.startsWith("new") && row.kind === tkind && tracksOf.size === 1) {
      const tr = M.track(doc, row.tid);
      if (tr && !tr.locked) target = row.tid;
    }
    if (clip.kind === "audio" && target === mainId) target = lead0.track;

    let ok = true;
    if (target === mainId && (lead0.track === mainId || tkind === "video")) {
      // piste principale : réordonner (le clip tenu seulement ; ses partenaires suivent)
      if (lead0.track !== mainId) lead.track = mainId;
      M.reorderMain(doc, lead, lead0.start + dt);
    } else if (lead0.track === mainId) {
      // sortir de la principale vers une piste de superposition
      lead.track = target;
      lead.start = M.r4(Math.max(0, lead0.start + dt));
      M.packMain(doc);
      ok = M.isFree(doc, target, lead.start, lead.dur, new Set([lead.id]));
    } else {
      const map = target !== lead0.track ? new Map([[lead0.track, target]]) : new Map();
      ok = M.canMove(doc, ids, dt, map);
      if (ok) M.moveClips(doc, ids, dt, map);
    }
    if (ok) {
      M.reflowCaptions(doc);
      // pistes créées pour rien pendant le geste : on les retire
      doc.tracks = doc.tracks.filter((t) => t.main || doc.clips.some((c) => c.track === t.id) ||
                                            origDoc.tracks.some((o) => o.id === t.id));
      lastValid = JSON.stringify(doc);
      S.doc = doc;
    } else {
      S.doc = JSON.parse(lastValid);
    }
    // le clip tenu suit la souris (aimanté) ; sa place d'arrivée en pointillés
    moveFloat(dt * T.pps, dy);
    T.landing = S.doc.clips.filter((c) => ids.has(c.id)).map((c) => ({ tid: c.track, t: c.start, dur: c.dur }));
    changed({ light: true, reason: "move" });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    stopAutoScroll();
    snapLine.classList.add("hidden");
    T.gesture = null;
    T.landing = [];
    dropFloat();
    if (moved) {
      end("move");
    } else {
      cancelBegin();
      if (collapseTo) select(collapseTo);
    }
    render();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/* ------------------------------------------------------- clips « flottants » */

/** Détache les clips tenus de leur piste : ils suivent la souris au-dessus de
 *  la timeline, avec leurs vignettes, pendant que les autres s'écartent. */
function liftClips(ids) {
  const ir = inner.getBoundingClientRect();
  const items = [];
  for (const id of ids) {
    const el = T.clipEls.get(id);
    const c = S.doc.clips.find((x) => x.id === id);
    if (!el || !c || !el.isConnected) continue;
    const r = el.getBoundingClientRect();
    const item = { id, el, left: r.left - ir.left, top: r.top - ir.top };
    items.push(item);
    Object.assign(el.style, { left: item.left + "px", top: item.top + "px", width: r.width + "px",
                              height: r.height + "px", bottom: "auto" });
    // son contenu (vignettes, forme d'onde) dessiné une fois, dans le clip lui-même
    const w = Math.min(Math.round(r.width), 4000), hh = Math.round(r.height);
    const cv = h("canvas.floatpaint", { width: w, height: hh, style: { width: w + "px", height: hh + "px" } });
    const ctx = cv.getContext("2d");
    drawClip(ctx, c, 0, 0, w, hh, w, M.track(S.doc, c.track));
    el.prepend(cv);
  }
  T.float = { ids: new Set(items.map((i) => i.id)), items };
  items.forEach((i) => { inner.appendChild(i.el); i.el.classList.add("floating"); });
  draw();
}

function moveFloat(dxContent, dy) {
  if (!T.float) return;
  for (const it of T.float.items) {
    it.el.style.left = it.left + dxContent + "px";
    it.el.style.top = it.top + dy + "px";
  }
}

/** Repose les clips : ils glissent de là où on les a lâchés vers leur place. */
function dropFloat() {
  if (!T.float) return;
  for (const it of T.float.items) {
    const d = T.disp.get(it.id);
    if (d) d.x = parseFloat(it.el.style.left) - HEAD_W;
    it.el.classList.remove("floating");
    it.el.querySelectorAll(".floatpaint").forEach((cv) => cv.remove());
    Object.assign(it.el.style, { top: "", height: "", bottom: "" });
  }
  T.float = null;
}

/* défilement automatique quand on glisse près d'un bord */
let autoTimer = 0, autoVel = 0, scrollStart = null;
function scrollDelta() { return scrollStart === null ? 0 : scroll.scrollLeft - scrollStart; }
function autoScroll(ev) {
  if (scrollStart === null) scrollStart = scroll.scrollLeft;
  const r = scroll.getBoundingClientRect();
  const edge = 40;
  autoVel = ev.clientX > r.right - edge ? 14 : ev.clientX < r.left + HEAD_W + edge ? -14 : 0;
  if (autoVel && !autoTimer) {
    const step = () => {
      if (!autoVel) { autoTimer = 0; return; }
      scroll.scrollLeft += autoVel;
      autoTimer = requestAnimationFrame(step);
    };
    autoTimer = requestAnimationFrame(step);
  }
}
function stopAutoScroll() {
  autoVel = 0;
  cancelAnimationFrame(autoTimer);
  autoTimer = 0;
  scrollStart = null;
}

/** Zone vide : désélectionne, puis rectangle de sélection si on glisse. */
function emptyDown(e) {
  if (e.button !== 0) return;
  const lane = e.target.closest && e.target.closest(".lane");
  if (!lane || e.target.closest(".clip")) return;
  const add = e.ctrlKey || e.metaKey || e.shiftKey;
  const before = new Set(add ? S.sel : []);
  if (!add) selectNone();
  const ir = inner.getBoundingClientRect();
  const x0 = e.clientX - ir.left, y0 = e.clientY - ir.top;
  const box = h("div.marquee", {});
  let moved = false;
  const move = (ev) => {
    const x1 = ev.clientX - ir.left, y1 = ev.clientY - ir.top;
    if (!moved && Math.abs(x1 - x0) + Math.abs(y1 - y0) < 4) return;
    if (!moved) { moved = true; inner.appendChild(box); }
    const [l, r] = [Math.min(x0, x1), Math.max(x0, x1)];
    const [t, b] = [Math.min(y0, y1), Math.max(y0, y1)];
    Object.assign(box.style, { left: l + "px", top: t + "px", width: r - l + "px", height: b - t + "px" });
    const ta = (l - HEAD_W) / T.pps, tb = (r - HEAD_W) / T.pps;
    const ids = new Set(before);
    for (const row of T.rows) {
      const rb = row.el.getBoundingClientRect();
      const top = rb.top - ir.top, bot = rb.bottom - ir.top;
      if (bot < t || top > b) continue;
      S.doc.clips.forEach((c) => {
        if (c.track === row.tid && !c.gone && M.clipEnd(c) > ta && c.start < tb) ids.add(c.id);
      });
    }
    S.sel.clear();
    ids.forEach((id) => S.sel.add(id));
    emit("select");
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    box.remove();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/* ============================================================ glisser des médias */

function initDnD() {
  const media = () => (S.dragMedia ? S.media.get(S.dragMedia) : null);
  const target = (e) => {
    const m = media();
    if (!m) return null;
    const row = rowAt(e.clientY);
    const t = Math.max(0, timeAtX(e.clientX));
    const dur = m.kind === "image" ? M.IMAGE_DUR : m.duration;
    let tid = row && row.tid && !row.tid.startsWith("new") ? row.tid : null;
    return { m, tid, row, t: snapTime(t, null, e.shiftKey), dur };
  };
  scroll.addEventListener("dragover", (e) => {
    const tg = target(e);
    if (!tg) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    const tr = tg.tid ? M.track(S.doc, tg.tid) : null;
    const t = tr && tr.main ? M.nearestBoundary(S.doc, tg.t) : tg.t;
    T.dropGhost = tg.tid ? { tid: tg.tid, t, dur: tg.dur } : null;
    draw();
  });
  scroll.addEventListener("dragleave", (e) => {
    if (!scroll.contains(e.relatedTarget)) { T.dropGhost = null; snapLine.classList.add("hidden"); draw(); }
  });
  scroll.addEventListener("drop", (e) => {
    const tg = target(e);
    T.dropGhost = null;
    snapLine.classList.add("hidden");
    if (!tg) return;
    e.preventDefault();
    e.stopPropagation();
    const added = edit((doc) => {
      let tid = tg.tid;
      if (!tid) {
        // au-dessus des pistes : nouvelle piste ; en dessous : audio ou vidéo selon le média
        const kind = tg.m.kind === "audio" ? "audio" : "video";
        tid = tg.row && tg.row.tid === "new-top" && kind === "video" ? M.addTrack(doc, "video").id
            : kind === "audio" ? M.addTrack(doc, "audio").id : M.mainTrack(doc).id;
      }
      const clips = M.placeMedia(doc, tg.m, tid, tg.t);
      M.reflowCaptions(doc);
      return clips;
    }, "add");
    select(added.map((c) => c.id));
    S.dragMedia = null;
    draw();
  });
}

/* ================================================================== menus */

function clipMenu(e, id) {
  const clip = S.doc.clips.find((c) => c.id === id);
  if (!clip) return;
  if (!S.sel.has(id)) select([...M.linkedIds(S.doc, [id])]);
  const m = clip.media ? S.media.get(clip.media) : null;
  const under = clip.start < S.t && M.clipEnd(clip) > S.t;
  menu(e.clientX, e.clientY, [
    { label: "Diviser à la tête de lecture", icon: "split", key: "Ctrl+B", disabled: !under, onclick: A.split },
    { label: "Dupliquer", icon: "copy", key: "Ctrl+D", onclick: A.duplicate },
    { label: "Copier", icon: "copy", key: "Ctrl+C", onclick: A.copy },
    { label: "Couper", icon: "cut", key: "Ctrl+X", onclick: A.cut },
    "-",
    clip.kind === "video" && m && m.has_audio ? {
      label: clip.detached ? "Rattacher le son" : "Séparer le son", icon: "detach",
      onclick: () => A.toggleDetach([clip]) } : null,
    clip.link ? { label: "Dissocier", icon: "unlink", onclick: A.unlinkSelection } : null,
    clip.kind === "video" || clip.kind === "audio" ? {
      label: clip.muted ? "Réactiver le son" : "Couper le son", icon: clip.muted ? "vol" : "mute",
      onclick: A.toggleMute } : null,
    clip.kind === "video" || clip.kind === "audio" ? {
      label: "Supprimer les blancs de ce clip", icon: "silence",
      onclick: () => emit("tool", { name: "silence", scope: "selection" }) } : null,
    clip.kind === "video" ? { label: "Arrêt sur image (2 s)", icon: "freeze", disabled: !under,
                              onclick: () => A.freezeFrame() } : null,
    "-",
    { label: "Supprimer", icon: "trash", key: "Suppr", onclick: A.remove },
  ]);
}

function trackMenu(e, t) {
  const n = S.doc.clips.filter((c) => c.track === t.id).length;
  menu(e.clientX, e.clientY, [
    { label: "Renommer", icon: "text", onclick: () => renameTrack(t, T.rows.find((r) => r.tid === t.id).el.querySelector(".tn")) },
    { label: `Sélectionner les clips (${n})`, icon: "grid", disabled: !n,
      onclick: () => select(S.doc.clips.filter((c) => c.track === t.id).map((c) => c.id)) },
    "-",
    { label: "Nouvelle piste vidéo", icon: "film", onclick: () => newTrack("video") },
    { label: "Nouvelle piste audio", icon: "note", onclick: () => newTrack("audio") },
    { label: "Nouvelle piste texte", icon: "text", onclick: () => newTrack("text") },
    "-",
    { label: t.main ? "La piste principale ne se supprime pas" : n ? `Supprimer la piste et ses ${n} clips` : "Supprimer la piste",
      icon: "trash", disabled: t.main, onclick: () => {
        if (n && !confirm(`Supprimer « ${t.name} » et ses ${n} clips ?`)) return;
        edit((doc) => { M.removeTrack(doc, t.id); M.reflowCaptions(doc); }, "track");
      } },
  ]);
}

function addTrackMenu(e) {
  const r = e.currentTarget.getBoundingClientRect();
  menu(r.left, r.bottom + 4, [
    { label: "Piste vidéo (superposition)", icon: "film", onclick: () => newTrack("video") },
    { label: "Piste audio", icon: "note", onclick: () => newTrack("audio") },
    { label: "Piste texte", icon: "text", onclick: () => newTrack("text") },
  ]);
}

function newTrack(kind) {
  edit((doc) => M.addTrack(doc, kind), "track");
}

function trackFlag(t, flag) {
  edit((doc) => {
    const tr = M.track(doc, t.id);
    if (tr) tr[flag] = !tr[flag];
  }, "track");
}

function renameTrack(t, el) {
  if (t.main || !el) return;
  const input = h("input", { type: "text", value: t.name });
  el.textContent = "";
  el.appendChild(input);
  input.focus();
  input.select();
  const done = (ok) => {
    const v = input.value.trim();
    if (ok && v && v !== t.name) edit((doc) => { const tr = M.track(doc, t.id); if (tr) tr.name = v.slice(0, 40); }, "track");
    else render();
  };
  input.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); };
  input.onblur = () => done(true);
}

/* ================================================================= clavier */

function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (typing(e) || document.querySelector(".overlay")) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    let used = true;
    if (mod && k === "b") A.split();
    else if (!mod && k === "s" && !e.altKey) A.split();
    else if (k === "delete" || k === "backspace") A.remove();
    else if (mod && k === "d") A.duplicate();
    else if (mod && k === "c") A.copy();
    else if (mod && k === "x") A.cut();
    else if (mod && k === "v") A.paste();
    else if (mod && k === "a") A.selectAll();
    else if (k === "escape") selectNone();
    else if (k === "arrowup") A.jump(-1);
    else if (k === "arrowdown") A.jump(1);
    else if (!mod && (k === "+" || k === "=")) setZoom(T.pps * 1.4);
    else if (!mod && (k === "-" || k === "_")) setZoom(T.pps / 1.4);
    else if (!mod && e.shiftKey && k === "z") fit();
    else if (!mod && k === "m") A.addMarker();
    else if (!mod && k === "n") toggleSnap();
    else if (!mod && k === "f") A.freezeFrame();
    else used = false;
    if (used) e.preventDefault();
  });
}
