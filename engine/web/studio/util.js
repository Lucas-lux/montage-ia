/* Petits outils partagés par les modules du studio : DOM, icônes, formats. */

export const $ = (id) => document.getElementById(id);

/** Crée un élément : h("div.a.b", {title: "x", onclick}, enfants...). */
export function h(tag, attrs, ...kids) {
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el[k] = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "text") el.textContent = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return el;
}

export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const round = (v, n = 3) => Math.round(v * 10 ** n) / 10 ** n;

/* ------------------------------------------------------------------ icônes.
   Traits 20x20, même famille que l'accueil. */
export const P = {
  grid: "M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z",
  film: "M3 4h14v12H3zM7 4v12M13 4v12M3 10h14",
  tools: "M3 8h14v8H3zM7.5 8V5.5h5V8M3 11.5h14M10 10.5v2",
  back: "M12 4 6 10l6 6",
  plus: "M10 4v12M4 10h12",
  minus: "M4 10h12",
  undo: "M7.5 5 4 8.5 7.5 12M4 8.5h8a4 4 0 0 1 0 8H9",
  redo: "M12.5 5 16 8.5 12.5 12M16 8.5H8a4 4 0 0 0 0 8h3",
  down: "M10 3.5v9M6.5 9.5 10 13l3.5-3.5M4 16.5h12",
  close: "M5.5 5.5l9 9M14.5 5.5l-9 9",
  trash: "M4 6h12M8 6V4h4v2M6 6l.8 10h6.4L14 6",
  split: "M10 2.5v15M6 6 3.5 10 6 14M14 6l2.5 4-2.5 4",
  cut: "M4 4l8.5 10M16 4L7.5 14M4.5 16a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0 0 3.6zM15.5 16a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0 0 3.6z",
  copy: "M7 7h9v9H7zM4 13V4h9",
  detach: "M3 6h9v5H3zM3 14.5h14M5 17h2M10 17h2M15 17h2M15 5.5v4M13 7.5h4",
  link: "M8.5 11.5l3-3M7 9 5.5 10.5a2.5 2.5 0 0 0 3.5 3.5L10.5 13M13 11l1.5-1.5A2.5 2.5 0 0 0 11 6L9.5 7.5",
  unlink: "M7 9 5.5 10.5a2.5 2.5 0 0 0 3.5 3.5L10.5 13M13 11l1.5-1.5A2.5 2.5 0 0 0 11 6L9.5 7.5M4 4l12 12",
  magnet: "M5 4v6a5 5 0 0 0 10 0V4h-3.5v6a1.5 1.5 0 0 1-3 0V4zM5 7.5h3.5M11.5 7.5H15",
  zoomin: "M8.5 14a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11zM12.5 12.5 17 17M8.5 6v5M6 8.5h5",
  zoomout: "M8.5 14a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11zM12.5 12.5 17 17M6 8.5h5",
  fit: "M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4",
  eye: "M2.5 10S5.5 4.5 10 4.5 17.5 10 17.5 10 14.5 15.5 10 15.5 2.5 10 2.5 10zM10 12.3a2.3 2.3 0 1 0 0-4.6 2.3 2.3 0 0 0 0 4.6z",
  eyeoff: "M2.5 10S5.5 4.5 10 4.5 17.5 10 17.5 10 14.5 15.5 10 15.5 2.5 10 2.5 10zM4 4l12 12",
  vol: "M3.5 8v4h3l4 3.5v-11l-4 3.5zM13.5 7.5a3.5 3.5 0 0 1 0 5M15.5 5.5a6.5 6.5 0 0 1 0 9",
  mute: "M3.5 8v4h3l4 3.5v-11l-4 3.5zM13.5 8l4 4M17.5 8l-4 4",
  lock: "M5.5 9h9v7.5h-9zM7.5 9V6.5a2.5 2.5 0 0 1 5 0V9",
  unlock: "M5.5 9h9v7.5h-9zM7.5 9V6.5a2.5 2.5 0 0 1 5 0",
  text: "M4.5 5V3.5h11V5M10 3.5v13M7.5 16.5h5",
  cc: "M2.5 4.5h15v11h-15zM8.5 8.2a2 2 0 1 0 0 3.6M14.5 8.2a2 2 0 1 0 0 3.6",
  wand: "M4 16 13 7M11.5 5.5l1-2.5 1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1zM4.5 5.5l.5-1.5.5 1.5 1.5.5-1.5.5-.5 1.5-.5-1.5L3 6z",
  silence: "M2.5 10h2M6 7v6M9 5v10M12 8v4M15.5 9.5v1M17.5 10h0",
  play: "M6.5 4.5v11l9-5.5z",
  pause: "M7.5 4.5v11M12.5 4.5v11",
  prev: "M5 4.5v11M15.5 4.5v11l-8-5.5z",
  next: "M15 4.5v11M4.5 4.5v11l8-5.5z",
  stepb: "M12.5 5 7.5 10l5 5",
  stepf: "M7.5 5l5 5-5 5",
  loop: "M4 9.5V8a3 3 0 0 1 3-3h8l-2.5-2.5M16 10.5V12a3 3 0 0 1-3 3H5l2.5 2.5",
  full: "M3 7.5V3h4.5M12.5 3H17v4.5M17 12.5V17h-4.5M7.5 17H3v-4.5",
  note: "M8 14.5V5l8-1.5v9.5M8 14.5a2 2 0 1 1-4 0 2 2 0 0 1 4 0zM16 13a2 2 0 1 1-4 0 2 2 0 0 1 4 0z",
  image: "M3 4h14v12H3zM3 13l4-4 3 3 2.5-2.5L17 14M13 7.5h0",
  folder: "M2.5 5.5v10h15v-8H9.5L8 5.5z",
  file: "M5 2.5h6.5L15 6v11.5H5zM11 2.5V6.5h4",
  pathin: "M3 10h9M9 6.5l3.5 3.5L9 13.5M14.5 4v12",
  speed: "M3.5 13.5a6.5 6.5 0 1 1 13 0M10 13.5l3-4",
  flipH: "M10 3v14M7.5 5.5 3 14h4.5zM12.5 5.5 17 14h-4.5z",
  flipV: "M3 10h14M5.5 7.5 14 3v4.5zM5.5 12.5 14 17v-4.5z",
  rotate: "M15.5 10a5.5 5.5 0 1 1-1.6-3.9M15.5 3.5v3.5H12",
  flag: "M5 17V3.5M5 4h9l-2 3.5 2 3.5H5",
  gear: "M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4",
  warn: "M10 4l6.5 12h-13zM10 8.5v3M10 13.8v.1",
  check: "M4.5 10.5l3.5 3.5 7.5-8",
  more: "M5 10h0M10 10h0M15 10h0",
  freeze: "M10 2.5v15M3.5 6.2l13 7.6M3.5 13.8l13-7.6",
  sliders: "M4 5.5h7M14 5.5h2M4 10h2M9 10h7M4 14.5h9M16 14.5h0M12.5 4v3M7.5 8.5v3M14.5 13v3",
  transition: "M3 5h6v10H3zM11 5h6v10h-6zM8 10h4",
  refresh: "M16 10a6 6 0 1 1-1.9-4.4M16 3v3.5h-3.5",
  chev: "M6 8l4 4 4-4",
  sun: "M10 13.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7l1.3 1.3M14 14l1.3 1.3M4.7 15.3 6 14M14 6l1.3-1.3",
  moon: "M16.5 12.2A6.5 6.5 0 0 1 7.8 3.5a6.5 6.5 0 1 0 8.7 8.7z",
  search: "M8.5 14a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11zM12.5 12.5 17 17",
  upload: "M10 13V4M6.5 7.5 10 4l3.5 3.5M4 16.5h12",
  caret: "M6 8l4 4 4-4",
};

export function svg(name, size = 16, extra = "") {
  const d = P[name] || name;
  return `<svg viewBox="0 0 20 20" width="${size}" height="${size}" fill="none"
    stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
    ${extra}><path d="${d}"/></svg>`;
}
export const playSvg = (size = 14) =>
  `<svg viewBox="0 0 20 20" width="${size}" height="${size}" fill="currentColor"><path d="M6.5 4.5v11l9-5.5z"/></svg>`;
export const pauseSvg = (size = 14) =>
  `<svg viewBox="0 0 20 20" width="${size}" height="${size}" fill="currentColor"><path d="M6 4.5h2.6v11H6zM11.4 4.5H14v11h-2.6z"/></svg>`;

/** Bouton icône : icon("split", "Diviser (Ctrl+B)", onclick). */
export function iconBtn(name, title, onclick, cls = "") {
  return h("button.btn.icon.quiet" + (cls ? "." + cls : ""),
           { title, "aria-label": title, onclick, html: svg(name, 16) });
}

/* ------------------------------------------------------------------ formats */

/** 83.4 -> "1:23" */
export function fmt(t) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`
                 : `${m}:${String(s).padStart(2, "0")}`;
}

/** Timecode mm:ss:ii (ii = image dans la seconde), comme dans un logiciel de montage. */
export function tc(t, fps = 30) {
  t = Math.max(0, t || 0);
  const whole = Math.floor(t + 1e-6);
  const f = Math.floor((t - whole) * fps + 1e-6);
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60;
  const core = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
  return h ? `${h}:${core}` : core;
}

/** 2.35 -> "2,4 s" */
export const sec = (v) => (Math.round((v || 0) * 10) / 10).toFixed(1).replace(".", ",") + " s";
export const mo = (b) => (b / 1048576).toFixed(1).replace(".", ",") + " Mo";
export const pct = (v) => Math.round((v || 0) * 100) + " %";

/* ------------------------------------------------------------------- toast */
let toastTimer = 0;
export function toast(msg, ms = 2600) {
  let el = $("toast");
  if (!el) { el = h("div.toast", { id: "toast" }); document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), ms);
}

/** Vrai si la frappe clavier vise un champ de saisie (raccourcis à ignorer).
 *  Un curseur, une case ou un sélecteur de couleur ne gardent que leurs
 *  propres touches : Ctrl+Z ou Espace restent des raccourcis du studio. */
export function typing(e) {
  const t = e.target;
  if (t.isContentEditable) return true;
  const tag = (t.tagName || "").toLowerCase();
  if (tag === "textarea" || tag === "select") return true;
  if (tag !== "input") return false;
  if (t.type === "range") return /^(Arrow|Home$|End$|Page)/.test(e.key);
  return !["checkbox", "radio", "color", "button"].includes(t.type);
}

/** Débit limité à une exécution par image (rAF). */
export function rafThrottle(fn) {
  let queued = false, lastArgs;
  return (...args) => {
    lastArgs = args;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fn(...lastArgs); });
  };
}

/* ------------------------------------------------------ fenêtre modale */

/** modal({ title, body: élément, actions: [{ label, primary, onclick }] }).
 *  Une action qui renvoie `false` garde la fenêtre ouverte. Échap ferme. */
export function modal({ title, body, actions = [], width = 460, onclose } = {}) {
  const close = () => {
    ov.remove();
    document.removeEventListener("keydown", onKey, true);
    if (onclose) onclose();
  };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const foot = h("div.mf", {}, actions.map((a) => h("button.btn" + (a.primary ? ".primary" : ""), {
    onclick: async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try { if ((await a.onclick?.()) !== false) close(); }
      finally { btn.disabled = false; }
    },
  }, a.label)));
  const ov = h("div.overlay", { onpointerdown: (e) => { if (e.target === ov) close(); } },
    h("div.modal", { style: { maxWidth: width + "px" } },
      h("div.mh", {}, h("h2", {}, title || ""),
        h("button.btn.sm.icon.quiet", { title: "Fermer", onclick: close, html: svg("close", 14) })),
      h("div.mb", {}, body || ""),
      actions.length ? foot : null));
  document.body.appendChild(ov);
  document.addEventListener("keydown", onKey, true);
  const first = ov.querySelector("input, textarea, select");
  if (first) setTimeout(() => first.focus(), 0);
  return { close, el: ov };
}

/* ---------------------------------------------------- menu contextuel */

let openMenu = null;
export function closeMenu() {
  if (openMenu) { openMenu.remove(); openMenu = null; }
}
/** menu(x, y, [{ label, icon, key, onclick, disabled } | "-"]) */
export function menu(x, y, items) {
  closeMenu();
  const el = h("div.ctx", {}, items.filter(Boolean).map((it) => it === "-" ? h("div.cs") :
    h("button", {
      disabled: it.disabled,
      onclick: () => { closeMenu(); it.onclick?.(); },
      html: (it.icon ? svg(it.icon, 14) : `<span style="width:14px"></span>`) +
            `<span>${it.label}</span>` + (it.key ? `<span class="k">${it.key}</span>` : ""),
    })));
  document.body.appendChild(el);
  const r = el.getBoundingClientRect();
  el.style.left = clamp(x, 6, innerWidth - r.width - 6) + "px";
  el.style.top = clamp(y, 6, innerHeight - r.height - 6) + "px";
  openMenu = el;
  setTimeout(() => {
    const away = (e) => {
      if (!el.contains(e.target)) { closeMenu(); document.removeEventListener("pointerdown", away, true); }
    };
    document.addEventListener("pointerdown", away, true);
  }, 0);
  return el;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

/** Tri « humain » des noms de fichiers : clip2 avant clip10. */
export const naturalCompare = (a, b) =>
  a.localeCompare(b, "fr", { numeric: true, sensitivity: "base" });

/** Ajoute des enfants en ignorant les absents (null, false) — `append` les
 *  écrirait en toutes lettres. */
export function put(el, ...kids) {
  el.append(...kids.flat().filter((k) => k != null && k !== false));
  return el;
}

/* ------------------------------------------------------ section repliable */

let collapsedSet = null;
function collapsed() {
  if (!collapsedSet) {
    try { collapsedSet = new Set(JSON.parse(localStorage.getItem("studio.collapsed") || "[]")); }
    catch (e) { collapsedSet = new Set(); }
  }
  return collapsedSet;
}
function rememberCollapsed() {
  try { localStorage.setItem("studio.collapsed", JSON.stringify([...collapsed()])); } catch (e) { /* rien */ }
}

/** Section à titre, repliable d'un clic, dont l'état est mémorisé par `key`.
 *  `open: false` la replie la première fois. `count` s'affiche à côté du titre. */
export function section({ title, icon, key, open = true, count, extra }, ...kids) {
  // état mémorisé : « key » replié si présent ; pour une section repliée par
  // défaut, « key:o » ouverte si présent
  const set = collapsed();
  const off = key ? (open ? set.has(key) : !set.has(key + ":o")) : !open;
  const body = h("div.sbody", {}, ...kids.flat().filter((k) => k != null && k !== false));
  const el = h("div.sect" + (off ? ".collapsed" : ""), {});
  const head = h("button.stitle", {
    type: "button", "aria-expanded": off ? "false" : "true",
    onclick: () => {
      const now = el.classList.toggle("collapsed");
      head.setAttribute("aria-expanded", now ? "false" : "true");
      if (!key) return;
      if (open) { if (now) set.add(key); else set.delete(key); }
      else if (now) set.delete(key + ":o"); else set.add(key + ":o");
      rememberCollapsed();
    },
  }, icon ? h("i", { html: svg(icon, 14), style: { color: "var(--ink-2)" } }) : null,
     h("span", {}, title),
     count != null ? h("span.cnt", {}, String(count)) : null,
     extra || null,
     h("i.chev", { html: svg("chev", 14) }));
  el.append(head, body);
  return el;
}
