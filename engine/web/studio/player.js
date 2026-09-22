/* Lecteur : joue la timeline en direct, sans rien rendre.

   Une horloge maître (performance.now) fait avancer la tête de lecture ; à
   chaque image, chaque clip actif a son propre élément <video>, <audio> ou
   <img>, calé sur l'instant voulu de sa source :
     - un clip qui arrive dans moins de 2 s est préparé à l'avance (élément
       créé, positionné sur son point d'entrée) : pas d'image noire au raccord ;
     - un petit décalage est rattrapé en accélérant ou ralentissant à peine la
       lecture (inaudible), un gros par un saut ;
     - le son passe par Web Audio : volume jusqu'à 200 %, fondus, muet de piste.

   Géométrie : la même que l'export (engine/timeline/render.py). Un clip
   « remplir » couvre le cadre, « adapter » y tient entier ; `scale` multiplie
   cette taille, `x`/`y` placent son centre, `rotation` le tourne. */

import * as M from "./model.js";
import { buildChain, fxKey } from "./voice.js";
import { S, emit, on, setTime } from "./store.js";
import { $, clamp, h, pauseSvg, playSvg, svg, tc, typing } from "./util.js";
import { renderCaptions } from "./captions.js";

const PRELOAD = 2.0;       // s : préparation d'un clip avant son entrée
const KEEP = 6;            // s : on garde un élément inutile ce temps-là (retour arrière)
const MAX_ELEMS = 24;

export const P = {
  playing: false,
  loop: false,
  silent: false,           // voix off en cours d'enregistrement : les autres sons se taisent
  t0: 0, p0: 0,
  raf: 0,
  items: new Map(),        // clip id -> { el, wrap, media, url, kind, gain, src, last }
  ctx: null,
  scrubbing: false,
};

let vlayer;

export function init() {
  vlayer = $("vlayer");
  $("tPlay").innerHTML = playSvg(14);
  $("tStart").innerHTML = svg("prev", 15);
  $("tEnd").innerHTML = svg("next", 15);
  $("tStepB").innerHTML = svg("stepb", 15);
  $("tStepF").innerHTML = svg("stepf", 15);
  $("tLoop").innerHTML = svg("loop", 15);
  $("tFull").innerHTML = svg("full", 15);

  $("tPlay").onclick = toggle;
  $("tStart").onclick = () => seek(0);
  $("tEnd").onclick = () => seek(Math.max(0, M.duration(S.doc) - frame()));
  $("tStepB").onclick = () => step(-1);
  $("tStepF").onclick = () => step(1);
  $("tLoop").onclick = () => { P.loop = !P.loop; $("tLoop").classList.toggle("on", P.loop); };
  $("tFull").onclick = () => {
    const v = $("viewer");
    if (document.fullscreenElement) document.exitFullscreen();
    else v.requestFullscreen && v.requestFullscreen();
  };
  $("stage").addEventListener("dblclick", (e) => { if (e.target.closest(".cap")) return; toggle(); });

  on("time", ({ from }) => {
    if (from !== "play" && P.playing) {
      // la tête a été déplacée à la main pendant la lecture : on repart de là
      P.t0 = S.t;
      P.p0 = performance.now();
    }
    sync(S.t);
    clock();
  });
  on("doc", () => { sync(S.t); clock(); });
  on("media", () => sync(S.t));
  on("layout", () => sync(S.t, { layout: true }));
  on("scrub", ({ on: scrubbing }) => { P.scrubbing = scrubbing; });
  initKeys();
  sync(0);
  clock();
}

const frame = () => 1 / (S.doc.canvas.fps || 30);

/* -------------------------------------------------------------- transport */

export function play() {
  const d = M.duration(S.doc);
  if (!d) return;
  if (S.t >= d - frame() / 2) setTime(0, { from: "player" });
  audioCtx();
  P.playing = true;
  S.playing = true;
  P.t0 = S.t;
  P.p0 = performance.now();
  $("tPlay").innerHTML = pauseSvg(14);
  emit("play", { on: true });
  cancelAnimationFrame(P.raf);
  P.raf = requestAnimationFrame(loop);
}

export function pause() {
  if (!P.playing) return;
  P.playing = false;
  S.playing = false;
  cancelAnimationFrame(P.raf);
  for (const it of P.items.values()) if (it.el.pause) it.el.pause();
  $("tPlay").innerHTML = playSvg(14);
  emit("play", { on: false });
  sync(S.t);
}

export function toggle() { P.playing ? pause() : play(); }

export function seek(t) {
  setTime(clamp(t, 0, Math.max(0, M.duration(S.doc))), { from: "player" });
}

function step(n) {
  pause();
  const f = frame();
  seek(Math.round(S.t / f) * f + n * f);
}

function loop() {
  if (!P.playing) return;
  const d = M.duration(S.doc);
  let t = P.t0 + (performance.now() - P.p0) / 1000;
  if (t >= d) {
    if (P.loop && d > 0) {
      t = 0;
      P.t0 = 0;
      P.p0 = performance.now();
    } else {
      setTime(d, { from: "play" });
      pause();
      return;
    }
  }
  setTime(t, { from: "play" });
  P.raf = requestAnimationFrame(loop);
}

function clock() {
  const d = M.duration(S.doc), fps = S.doc.canvas.fps;
  $("tcode").innerHTML = `<b>${tc(S.t, fps)}</b> / ${tc(d, fps)}`;
  $("stageEmpty").classList.toggle("hidden", S.doc.clips.length > 0);
}

/* ------------------------------------------------------------------- son */

function audioCtx() {
  if (!P.ctx) {
    try { P.ctx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch (e) { P.ctx = null; }
  }
  if (P.ctx && P.ctx.state === "suspended") P.ctx.resume();
  return P.ctx;
}

/** Relie un élément média à Web Audio (une seule fois par élément). */
function route(it) {
  if (it.gain || !P.ctx) return;
  try {
    it.src = P.ctx.createMediaElementSource(it.el);
    it.gain = P.ctx.createGain();
    it.src.connect(it.gain).connect(P.ctx.destination);
    it.fxKey = "";
  } catch (e) { /* déjà relié ou refusé : volume natif */ }
}

/** Traitement de la voix du clip dans l'aperçu (reconstruit quand il change). */
function applyFx(it, c) {
  if (!it.gain || !P.ctx) return;
  const key = fxKey(c.audio_fx);
  if (key === it.fxKey) return;
  it.fxKey = key;
  try {
    it.gain.disconnect();
    if (it.fx) it.fx.nodes.forEach((n) => n.disconnect());
    it.fx = buildChain(P.ctx, c.audio_fx);
    if (it.fx) { it.gain.connect(it.fx.first); it.fx.last.connect(P.ctx.destination); }
    else it.gain.connect(P.ctx.destination);
  } catch (e) { it.fx = null; }
}

/** Gain d'un clip à l'instant t : volume, fondus, muet du clip et de la piste. */
export function gainAt(c, t, tr) {
  if (c.muted || (tr && tr.muted) || P.silent) return 0;
  let g = c.volume ?? 1;
  const local = t - c.start;
  if (c.fade_in > 0 && local < c.fade_in) g *= clamp(local / c.fade_in, 0, 1);
  const left = M.clipEnd(c) - t;
  if (c.fade_out > 0 && left < c.fade_out) g *= clamp(left / c.fade_out, 0, 1);
  return g;
}

/* --------------------------------------------------------- synchronisation */

/** Clips vidéo/image visibles, du plus bas au plus haut (ordre d'empilement). */
function visualOrder() {
  const vt = S.doc.tracks.filter((t) => t.kind === "video");
  return vt.slice().reverse();                  // la piste du bas d'abord
}

function audible(c) {
  const m = S.media.get(c.media);
  if (!m || !m.has_audio) return false;
  if (c.kind === "audio") return true;
  return c.kind === "video" && !c.detached;
}

export function sync(t, { layout = false } = {}) {
  if (!S.doc) return;
  const now = performance.now();
  const wanted = new Set();
  const order = visualOrder();
  const z = new Map(order.map((tr, i) => [tr.id, i + 1]));

  const blurSrc = [];
  for (const c of S.doc.clips) {
    if (!c.media || c.gone) continue;
    const m = S.media.get(c.media);
    if (!m || m.status !== "ready" || !m.urls || !m.urls.proxy) continue;
    const end = M.clipEnd(c);
    const visual = c.kind === "video" || c.kind === "image";
    // une transition prolonge l'image d'un clip d'une demi-durée de chaque côté
    const ext = visual ? M.extensions(S.doc, c) : { pre: 0, post: 0 };
    const seen = t >= c.start - ext.pre && t < end + ext.post;       // image visible
    const active = t >= c.start && t < end;                          // son audible
    const soon = !seen && c.start - ext.pre > t && c.start - ext.pre - t < PRELOAD;
    if (!seen && !soon) continue;
    const tr = M.track(S.doc, c.track);
    if (!visual && !audible(c)) continue;
    wanted.add(c.id);
    const it = item(c, m);
    it.last = now;
    const show = seen && visual && !(tr && tr.hidden);
    if (visual) {
      const fx = show ? transitionFx(c, ext, t) : null;
      it.wrap.style.visibility = show && !(fx && fx.hide) ? "visible" : "hidden";
      it.wrap.style.zIndex = (z.get(c.track) || 1) * 2 + (fx && fx.top ? 1 : 0);
      if (show || layout || !it.placed) place(it, c, m, fx);
      if (show && tr && tr.main && S.doc.canvas.blur && !(fx && fx.hide)) blurSrc.push({ it, c, m });
    }
    if (c.kind === "image") continue;
    const el = it.el;
    const dur = m.duration || Infinity;
    const target = seen ? clamp(c.in + (t - c.start) * (c.speed || 1), 0, Math.max(0, dur - 0.04)) : c.in - ext.pre * (c.speed || 1);
    const hearMe = active && audible(c) && P.playing;
    const g = hearMe ? gainAt(c, t, tr) : 0;
    if (it.gain) {
      el.muted = false;
      it.gain.gain.value = g;
    } else {
      el.volume = clamp(g, 0, 1);
      el.muted = g === 0;
    }
    if (seen && P.playing) {
      if (P.ctx && hearMe) { route(it); applyFx(it, c); }
      const speed = c.speed || 1;
      if (el.paused) {
        if (Math.abs(el.currentTime - target) > 0.05) el.currentTime = target;
        el.playbackRate = speed;
        el.play().catch(() => {});
        it.startedAt = now;
      } else {
        const drift = el.currentTime - target;
        // juste après le départ, le retard de démarrage se rattrape d'un saut
        const fresh = now - (it.startedAt || 0) < 1200;
        if (Math.abs(drift) > (fresh ? 0.08 : 0.35)) {
          el.currentTime = target;
          el.playbackRate = speed;
        } else {
          // rattrapage en douceur (±8 %) : inaudible, sans saut d'image
          el.playbackRate = speed * clamp(1 - drift * 0.8, 0.92, 1.08);
        }
      }
    } else {
      if (!el.paused) el.pause();
      const tol = seen ? 0.5 / (S.doc.canvas.fps || 30) : 0.05;
      if (Math.abs(el.currentTime - target) > tol && el.readyState >= 1) {
        if (!it.seeking) {
          it.seeking = true;
          el.currentTime = target;
        } else {
          it.pendingSeek = target;       // on redemandera à la fin du saut en cours
        }
      }
    }
  }

  // éléments inutiles : en pause tout de suite, supprimés un peu plus tard
  for (const [id, it] of P.items) {
    if (wanted.has(id)) continue;
    if (it.el.pause && !it.el.paused) it.el.pause();
    if (it.wrap) it.wrap.style.visibility = "hidden";
    if (now - it.last > KEEP * 1000 || P.items.size > MAX_ELEMS) destroy(id, it);
  }
  drawBlur(blurSrc);
  renderCaptions(t);
}

/* ------------------------------------------------------------ transitions */

/** Effet d'une transition sur un clip à l'instant t (null : aucun).
 *  Rôle « in » : le clip arrive ; « out » : il part vers le suivant. */
function transitionFx(c, ext, t) {
  let role = null, p = 0, type = "";
  if (ext.tin && t < c.start + ext.tin.d / 2) {
    role = "in"; type = ext.tin.type;
    p = (t - (c.start - ext.tin.d / 2)) / ext.tin.d;
  } else if (ext.tout && t >= M.clipEnd(c) - ext.tout.d / 2) {
    role = "out"; type = ext.tout.type;
    p = (t - (M.clipEnd(c) - ext.tout.d / 2)) / ext.tout.d;
  }
  if (!role) return null;
  p = clamp(p, 0, 1);
  const W = S.doc.canvas.w * (S.k || 1);
  const fx = { top: role === "in" };
  switch (type) {
    case "fadeblack":
      if (role === "out") { if (p < 0.5) fx.bright = 1 - 2 * p; else fx.hide = true; }
      else if (p < 0.5) fx.hide = true; else fx.bright = 2 * p - 1;
      break;
    case "fadewhite":
      if (role === "out") { if (p < 0.5) fx.white = 2 * p; else fx.hide = true; }
      else if (p < 0.5) fx.hide = true; else fx.white = 2 - 2 * p;
      break;
    case "slideleft":
      fx.tx = role === "in" ? (1 - p) * W : -p * W;
      break;
    case "slideright":
      fx.tx = role === "in" ? -(1 - p) * W : p * W;
      break;
    case "wipeleft":
      if (role === "in") fx.clip = `inset(0 0 0 ${((1 - p) * 100).toFixed(2)}%)`;
      break;
    case "circleopen":
      if (role === "in") fx.clip = `circle(${(p * 75).toFixed(2)}% at 50% 50%)`;
      break;
    case "zoomin":
      if (role === "out") { fx.zoom = 1 + p * 0.6; fx.op = 1 - p; } else fx.op = p;
      break;
    default:                            // fondu enchaîné, dissolution
      if (role === "in") fx.op = p;
  }
  return fx;
}

/* ----------------------------------------------------- arrière-plan flou */

let blurCanvas = null;
/** Derrière la piste principale : sa propre image, agrandie et floutée. Tracée
 *  depuis l'élément déjà décodé (pas de second décodage). */
function drawBlur(list) {
  if (!S.doc.canvas.blur) {
    if (blurCanvas) blurCanvas.style.display = "none";
    return;
  }
  if (!blurCanvas) {
    blurCanvas = h("canvas", { style: { position: "absolute", inset: "0", width: "100%", height: "100%",
                                        filter: "blur(14px) brightness(0.94)", transform: "scale(1.08)",
                                        zIndex: 0, pointerEvents: "none" } });
    vlayer.prepend(blurCanvas);
  }
  blurCanvas.style.display = "";
  const cw = Math.max(16, Math.round(S.doc.canvas.w / 10)), ch = Math.max(16, Math.round(S.doc.canvas.h / 10));
  if (blurCanvas.width !== cw || blurCanvas.height !== ch) { blurCanvas.width = cw; blurCanvas.height = ch; }
  const ctx = blurCanvas.getContext("2d");
  ctx.clearRect(0, 0, cw, ch);
  for (const { it, m } of list) {
    const el = it.el;
    const iw = el.videoWidth || el.naturalWidth || m.w, ih = el.videoHeight || el.naturalHeight || m.h;
    if (!iw || !ih || (el.readyState !== undefined && el.readyState < 2 && el.tagName === "VIDEO")) continue;
    const k = Math.max(cw / iw, ch / ih);
    try { ctx.drawImage(el, (cw - iw * k) / 2, (ch - ih * k) / 2, iw * k, ih * k); } catch (e) { /* image pas prête */ }
  }
}

function item(c, m) {
  let it = P.items.get(c.id);
  const url = m.urls.proxy;
  if (it && it.url === url && it.kind === c.kind) return it;
  if (it) destroy(c.id, it);
  let el, wrap = null;
  if (c.kind === "image") {
    el = h("img", { src: url, alt: "", draggable: "false" });
  } else if (c.kind === "video") {
    el = h("video", { src: url, preload: "auto", playsinline: true, muted: true });
    el.muted = true;
  } else {
    el = h("audio", { src: url, preload: "auto" });
  }
  if (c.kind !== "audio") {
    wrap = h("div.vitem", {}, el);
    vlayer.appendChild(wrap);
  }
  it = { el, wrap, url, kind: c.kind, media: m.id, gain: null, src: null, last: 0, placed: false,
         seeking: false, pendingSeek: null };
  if (el.tagName !== "IMG") {
    el.preservesPitch = true;
    el.addEventListener("seeked", () => {
      it.seeking = false;
      if (it.pendingSeek !== null) {
        const t2 = it.pendingSeek;
        it.pendingSeek = null;
        if (Math.abs(el.currentTime - t2) > 0.02) { it.seeking = true; el.currentTime = t2; }
      }
    });
    el.addEventListener("loadedmetadata", () => sync(S.t));
    // l'arrière-plan flou se redessine quand l'image demandée est là
    el.addEventListener("seeked", () => { if (S.doc.canvas.blur && !P.playing) sync(S.t); });
  }
  P.items.set(c.id, it);
  return it;
}

function destroy(id, it) {
  if (it.el.pause) {
    it.el.pause();
    it.el.removeAttribute("src");
    try { it.el.load(); } catch (e) { /* déjà libéré */ }
  }
  if (it.src) try { it.src.disconnect(); } catch (e) { /* déjà déconnecté */ }
  if (it.wrap) it.wrap.remove();
  P.items.delete(id);
}

/** Taille et place d'un clip visuel dans l'aperçu (mêmes règles que l'export). */
export function geometry(c, m, W, H) {
  const w = m.w || W, hh = m.h || H;
  const base = c.fit === "contain" ? Math.min(W / w, H / hh) : Math.max(W / w, H / hh);
  const s = base * (c.scale ?? 1);
  return { w: w * s, h: hh * s, cx: (c.x ?? 0.5) * W, cy: (c.y ?? 0.5) * H };
}

function place(it, c, m, fx) {
  const k = S.k || 1;
  const { w: W, h: H } = S.doc.canvas;
  const g = geometry(c, m, W, H);
  const st = it.wrap.style;
  st.width = g.w * k + "px";
  st.height = g.h * k + "px";
  st.left = (g.cx - g.w / 2) * k + "px";
  st.top = (g.cy - g.h / 2) * k + "px";
  st.opacity = (c.opacity ?? 1) * (fx && fx.op !== undefined ? fx.op : 1);
  const move = fx && fx.tx ? `translateX(${fx.tx.toFixed(1)}px) ` : "";
  const zoom = fx && fx.zoom ? ` scale(${fx.zoom.toFixed(3)})` : "";
  st.transform = `${move}rotate(${c.rotation || 0}deg)${zoom} scale(${c.flip_h ? -1 : 1}, ${c.flip_v ? -1 : 1})`;
  let filter = cssFilter(c);
  if (fx && fx.bright !== undefined) filter += ` brightness(${fx.bright.toFixed(3)})`;
  if (fx && fx.white) filter += ` brightness(${(1 + 3 * fx.white).toFixed(3)}) saturate(${(1 - fx.white).toFixed(3)})`;
  st.filter = filter.trim();
  st.clipPath = fx && fx.clip ? fx.clip : "";
  it.placed = true;
}

/** Réglages d'image en CSS (approximation fidèle de `eq` côté ffmpeg). */
function cssFilter(c) {
  const f = c.filters;
  if (!f) return "";
  const parts = [];
  if (f.brightness) parts.push(`brightness(${1 + f.brightness * 0.6})`);
  if (f.contrast) parts.push(`contrast(${1 + f.contrast})`);
  if (f.saturation) parts.push(`saturate(${Math.max(0, 1 + f.saturation)})`);
  // température : chaud = sépia léger, froid = teinte vers le bleu
  if (f.temperature > 0) parts.push(`sepia(${(f.temperature * 0.35).toFixed(3)})`);
  if (f.temperature < 0) parts.push(`hue-rotate(${(f.temperature * 18).toFixed(1)}deg) saturate(1.05)`);
  return parts.join(" ");
}

/* --------------------------------------------------------------- clavier */

function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (typing(e) || document.querySelector(".overlay")) return;
    const k = e.key;
    let used = true;
    if (k === " " || e.code === "Space") toggle();
    else if (k === "ArrowLeft") { if (e.shiftKey) { pause(); seek(S.t - 1); } else step(-1); }
    else if (k === "ArrowRight") { if (e.shiftKey) { pause(); seek(S.t + 1); } else step(1); }
    else if (k === "Home") seek(0);
    else if (k === "End") seek(M.duration(S.doc));
    else if (k.toLowerCase() === "l" && !e.ctrlKey && !e.metaKey) { P.loop = !P.loop; $("tLoop").classList.toggle("on", P.loop); }
    else used = false;
    if (used) e.preventDefault();
  });
}
