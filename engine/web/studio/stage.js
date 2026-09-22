/* Aperçu : sélectionner un clip en cliquant dessus, le déplacer, l'agrandir
   et le tourner à la souris (cadre de sélection et poignées).

   Les valeurs modifiées sont celles de l'inspecteur (x, y, scale, rotation),
   dans le repère de la SORTIE : l'export les applique à l'identique. */

import * as M from "./model.js";
import { geometry } from "./player.js";
import { S, begin, cancelBegin, changed, end, on, select, selectNone } from "./store.js";
import { $, clamp, h } from "./util.js";

const SNAP = 0.012;          // aimantation au centre du cadre (fraction de l'image)

export function init() {
  const stage = $("stage");
  stage.addEventListener("pointerdown", stageDown);
  ["select", "doc", "time", "layout", "media"].forEach((ev) => on(ev, drawBox));
  drawBox();
}

/** Clips visuels affichés à l'instant courant, du plus haut au plus bas. */
function visibleClips() {
  const order = S.doc.tracks.filter((t) => t.kind === "video" && !t.hidden).map((t) => t.id);
  return S.doc.clips
    .filter((c) => (c.kind === "video" || c.kind === "image") && order.includes(c.track) &&
                   S.t >= c.start && S.t < M.clipEnd(c) && S.media.get(c.media))
    .sort((a, b) => order.indexOf(a.track) - order.indexOf(b.track));
}

/** Le point (repère de sortie) est-il dans le clip, rotation comprise ? */
function hit(c, px, py) {
  const m = S.media.get(c.media);
  const g = geometry(c, m, S.doc.canvas.w, S.doc.canvas.h);
  const a = -(c.rotation || 0) * Math.PI / 180;
  const dx = px - g.cx, dy = py - g.cy;
  const rx = dx * Math.cos(a) - dy * Math.sin(a), ry = dx * Math.sin(a) + dy * Math.cos(a);
  return Math.abs(rx) <= g.w / 2 && Math.abs(ry) <= g.h / 2;
}

function toCanvas(e) {
  const r = $("stage").getBoundingClientRect();
  return [(e.clientX - r.left) / r.width * S.doc.canvas.w, (e.clientY - r.top) / r.height * S.doc.canvas.h];
}

function stageDown(e) {
  if (e.button !== 0 || e.target.closest(".cap") || e.target.closest(".tbox")) return;
  const [px, py] = toCanvas(e);
  const c = visibleClips().find((x) => hit(x, px, py));
  if (!c) { selectNone(); return; }
  const tr = M.track(S.doc, c.track);
  if (!S.sel.has(c.id)) select(e.altKey ? [c.id] : [...M.linkedIds(S.doc, [c.id])]);
  if (tr && tr.locked) return;
  drag(e, c.id, "move");
}

/* ---------------------------------------------------------- cadre */

function target() {
  const vis = S.doc.clips.filter((c) => S.sel.has(c.id) && (c.kind === "video" || c.kind === "image"));
  if (vis.length !== 1) return null;
  const c = vis[0];
  if (!(S.t >= c.start && S.t < M.clipEnd(c))) return null;
  return S.media.get(c.media) ? c : null;
}

function drawBox() {
  const layer = $("handles");
  if (!layer) return;
  const c = target();
  if (!c) { layer.innerHTML = ""; return; }
  const m = S.media.get(c.media);
  const k = S.k || 1;
  const g = geometry(c, m, S.doc.canvas.w, S.doc.canvas.h);
  let box = layer.querySelector(".tbox");
  if (!box || box.dataset.id !== c.id) {
    layer.innerHTML = "";
    box = h("div.tbox", { dataset: { id: c.id } },
      ...["nw", "ne", "sw", "se"].map((p) => h("div.hd." + p, { dataset: { p } })),
      h("div.rot", { title: "Tourner (Maj : par pas de 15°)" }));
    box.onpointerdown = (e) => {
      e.stopPropagation();
      if (e.button !== 0) return;
      const mode = e.target.classList.contains("rot") ? "rotate" : e.target.classList.contains("hd") ? "scale" : "move";
      drag(e, box.dataset.id, mode);
    };
    layer.appendChild(box);
  }
  Object.assign(box.style, {
    left: (g.cx - g.w / 2) * k + "px", top: (g.cy - g.h / 2) * k + "px",
    width: g.w * k + "px", height: g.h * k + "px",
    transform: `rotate(${c.rotation || 0}deg)`,
  });
}

/* ---------------------------------------------------------- gestes */

function drag(e, id, mode) {
  e.preventDefault();
  const c0 = S.doc.clips.find((x) => x.id === id);
  if (!c0) return;
  const m = S.media.get(c0.media);
  const W = S.doc.canvas.w, H = S.doc.canvas.h;
  const start = { x: c0.x ?? 0.5, y: c0.y ?? 0.5, scale: c0.scale ?? 1, rot: c0.rotation || 0 };
  const g0 = geometry(c0, m, W, H);
  const [px0, py0] = toCanvas(e);
  const d0 = Math.max(4, Math.hypot(px0 - g0.cx, py0 - g0.cy));
  const a0 = Math.atan2(py0 - g0.cy, px0 - g0.cx);
  let moved = false;
  begin();
  const move = (ev) => {
    const [px, py] = toCanvas(ev);
    if (!moved && Math.hypot(px - px0, py - py0) < 3 / (S.k || 1)) return;
    moved = true;
    const c = S.doc.clips.find((x) => x.id === id);
    if (!c) return;
    let gv = false, gh = false;
    if (mode === "move") {
      let x = start.x + (px - px0) / W, y = start.y + (py - py0) / H;
      if (!ev.shiftKey) {
        if (Math.abs(x - 0.5) < SNAP) { x = 0.5; gv = true; }
        if (Math.abs(y - 0.5) < SNAP) { y = 0.5; gh = true; }
      }
      c.x = M.r4(x);
      c.y = M.r4(y);
    } else if (mode === "scale") {
      const d = Math.hypot(px - g0.cx, py - g0.cy);
      let s = start.scale * d / d0;
      if (!ev.shiftKey && Math.abs(s - 1) < 0.03) s = 1;       // retour « plein cadre » aimanté
      c.scale = M.r4(clamp(s, 0.05, 20));
    } else {
      let r = start.rot + (Math.atan2(py - g0.cy, px - g0.cx) - a0) * 180 / Math.PI;
      r = ((r + 180) % 360 + 360) % 360 - 180;
      if (ev.shiftKey) r = Math.round(r / 15) * 15;
      else for (const snap of [-180, -90, 0, 90, 180]) if (Math.abs(r - snap) < 3) r = snap;
      c.rotation = Math.round(r * 10) / 10;
    }
    $("guideV").classList.toggle("hidden", !gv);
    $("guideH").classList.toggle("hidden", !gh);
    changed({ light: true, reason: "transform" });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    $("guideV").classList.add("hidden");
    $("guideH").classList.add("hidden");
    if (moved) end("transform"); else cancelBegin();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}
