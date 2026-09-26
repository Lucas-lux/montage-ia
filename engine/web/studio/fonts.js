/* Polices : celles du système et celles livrées avec l'application.

   - Système : les styles d'origine utilisent des polices de Windows ; sur un
     Mac, celles qui manquent sont remplacées par l'équivalent préinstallé,
     dans l'aperçu comme à l'export (même tableau qu'engine/pipeline/fonts.py).
   - Livrées : une cinquantaine de polices libres (engine/data/fonts). Elles
     sont déclarées ici en @font-face sous leur nom complet (« Montserrat
     Black »), le nom que libass retrouve à l'export.

   `fontPicker` ouvre le sélecteur : recherche, catégories, chaque police
   écrite dans sa propre police. */

import { api } from "./api.js";
import { clamp, h } from "./util.js";

export const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");

const MAC = {
  "Segoe UI": "Helvetica Neue",
  "Segoe UI Black": "Helvetica Neue",
  "Bahnschrift": "DIN Condensed",
  "Calibri": "Helvetica Neue",
  "Candara": "Optima",
  "Corbel": "Gill Sans",
  "Cambria": "Georgia",
  "Franklin Gothic Medium": "Avenir Next Condensed",
  "Consolas": "Menlo",
  "Segoe Script": "Snell Roundhand",
  "Ink Free": "Chalkboard SE",
};

/** Polices du système proposées (Windows ; remplacées sur Mac). */
export const SYSTEM_FONTS = ["Arial", "Arial Black", "Bahnschrift", "Impact", "Segoe UI", "Segoe UI Black", "Verdana",
                            "Tahoma", "Trebuchet MS", "Franklin Gothic Medium", "Candara", "Corbel", "Calibri",
                            "Georgia", "Cambria", "Times New Roman", "Courier New", "Consolas", "Comic Sans MS",
                            "Segoe Script", "Ink Free"];

/** Catalogue des polices livrées ({ name, file, category }), leurs catégories,
 *  et pour chaque police le rapport cadratin / hauteur (voir `emScale`). */
export const F = { bundled: [], categories: {}, metrics: {}, ready: null };

/** libass règle une police de taille N pour que sa hauteur totale (métriques
 *  OS/2 « Windows ») fasse N pixels ; CSS donne N pixels au cadratin. La
 *  taille CSS qui dessine comme libass est donc N × emScale(police), et
 *  l'interligne vaut N (engine/pipeline/fonts.py, `em_scale`). */
export function emScale(name) {
  return (F.metrics[name] || {}).em || 0.87;
}

const BASE = new Map();

/** Décalage vertical (en fraction de la taille) qui met la ligne de base du
 *  navigateur là où libass la met : libass la pose à `asc` × taille du haut de
 *  la ligne (métriques « Windows »), le navigateur centre ses propres
 *  ascendante et descendante dans l'interligne. Mesuré une fois par police,
 *  quand elle est chargée ; 0 tant qu'elle ne l'est pas. */
export function baselineShift(name) {
  if (BASE.has(name)) return BASE.get(name);
  const m = F.metrics[name];
  if (!m || !document.fonts.check(`40px "${name}"`)) return 0;
  const em = 100 * m.em;                              // pour une taille de 100
  const ctx = (baselineShift.cv ||= document.createElement("canvas")).getContext("2d");
  ctx.font = `${em}px ${fontStack(name)}`;
  const tm = ctx.measureText("Hg");
  const a = tm.fontBoundingBoxAscent, d = tm.fontBoundingBoxDescent;
  if (!(a > 0)) return 0;
  const shift = (100 * m.asc - (50 + (a - d) / 2)) / 100;
  BASE.set(name, shift);
  return shift;
}

/** Pile CSS d'une police : elle-même, son équivalent Mac, puis un repli sans empattement. */
export function fontStack(name) {
  const f = name || "Arial";
  const alt = MAC[f];
  return `"${f}"${alt ? `, "${alt}"` : ""}, Arial, "Helvetica Neue", sans-serif`;
}

/** Charge le catalogue et déclare les polices livrées (une seule fois). */
export function loadFonts() {
  if (F.ready) return F.ready;
  F.ready = api("/api/fonts").then((res) => {
    F.bundled = res.fonts || [];
    F.categories = res.categories || {};
    F.metrics = res.metrics || {};
    const css = F.bundled.map((f) =>
      `@font-face{font-family:"${f.name}";src:url("/fonts/${encodeURIComponent(f.file)}") format("truetype");` +
      "font-display:block}").join("\n");
    document.head.appendChild(h("style", { id: "bundledFonts" }, css));
    return F;
  }).catch(() => F);
  return F.ready;
}

/** Attend que les polices données soient prêtes à dessiner (mesures justes). */
export function fontsReady(names) {
  return Promise.all([...new Set(names)].filter(Boolean)
    .map((n) => document.fonts.load(`40px "${n}"`).catch(() => null)));
}

/* -------------------------------------------------------------- sélecteur */

let open = null;

function close() {
  if (open) { open.remove(); open = null; }
}

/** Ouvre le sélecteur sous `anchor` ; `pick(name)` reçoit la police choisie. */
export function fontPicker(anchor, current, pick) {
  close();
  const groups = [...Object.entries(F.categories).map(([k, label]) => ({ k, label })),
                  { k: "system", label: "Système" }];
  let cat = "", query = "";
  const list = h("div.fplist");
  const search = h("input", { type: "text", placeholder: "Rechercher une police…", "aria-label": "Rechercher une police" });
  const chips = h("div.seg.fpcats");
  const all = [...F.bundled.map((f) => ({ name: f.name, cat: f.category })),
               ...SYSTEM_FONTS.map((n) => ({ name: n, cat: "system" }))];

  const renderList = () => {
    list.innerHTML = "";
    const q = query.toLowerCase();
    for (const g of groups) {
      if (cat && cat !== g.k) continue;
      const items = all.filter((f) => f.cat === g.k && (!q || f.name.toLowerCase().includes(q)));
      if (!items.length) continue;
      list.appendChild(h("div.fpgroup", {}, g.label));
      items.forEach((f) => list.appendChild(h("button.fpitem" + (f.name === current ? ".on" : ""), {
        title: f.name, style: { fontFamily: fontStack(f.name) },
        onclick: () => { close(); pick(f.name); },
      }, f.name)));
    }
    if (!list.children.length) list.appendChild(h("div.hint", { style: { padding: "10px" } }, "Aucune police."));
  };
  const renderChips = () => {
    chips.innerHTML = "";
    [{ k: "", label: "Toutes" }, ...groups].forEach((g) => chips.appendChild(h("button", {
      class: g.k === cat ? "on" : "", onclick: () => { cat = g.k; renderChips(); renderList(); },
    }, g.label)));
  };
  search.oninput = () => { query = search.value.trim(); renderList(); };
  search.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Escape") close(); };

  const el = h("div.fontpop", {}, search, chips, list);
  document.body.appendChild(el);
  renderChips();
  renderList();
  const r = anchor.getBoundingClientRect();
  const box = el.getBoundingClientRect();
  el.style.left = clamp(r.left, 6, innerWidth - box.width - 6) + "px";
  el.style.top = clamp(r.bottom + 4, 6, innerHeight - box.height - 6) + "px";
  open = el;
  search.focus();
  const sel = list.querySelector(".on");
  if (sel) sel.scrollIntoView({ block: "center" });
  setTimeout(() => {
    const away = (e) => {
      if (open === el && !el.contains(e.target) && e.target !== anchor) {
        close();
        document.removeEventListener("pointerdown", away, true);
      }
    };
    document.addEventListener("pointerdown", away, true);
  }, 0);
}

/** Champ « Police » : le nom courant écrit dans sa police, ouvre le sélecteur. */
export function fontField(label, value, apply) {
  const b = h("button.fontbtn", { style: { fontFamily: fontStack(value) }, title: value }, value);
  b.onclick = () => fontPicker(b, value, apply);
  return h("div.field", {}, h("div.head", {}, h("span.label", {}, label)), b);
}
