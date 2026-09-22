/* Studio : démarrage, barre du haut, mise en page, raccourcis globaux.

   Chaque zone de l'écran est un module qui s'abonne aux évènements du store :
     "doc"     le montage a changé      "select"  la sélection a changé
     "time"    la tête de lecture a bougé  "media"   un média a progressé
     "layout"  la taille de l'aperçu a changé */

import { api } from "./api.js";
import { S, emit, on, loadDoc, undo, redo, saveNow, changed, snapshot } from "./store.js";
import { $, fmt, h, svg, toast, typing, tc } from "./util.js";

export const PRESETS = [];      // formats proposés (9:16, 16:9…), chargés au démarrage

/* ------------------------------------------------------------------ barre */

function initTopbar() {
  $("navProjects").innerHTML = svg("grid", 18);
  $("navStudio").innerHTML = svg("film", 18);
  $("navTools").innerHTML = svg("tools", 18);
  $("back").innerHTML = svg("back", 16);
  $("undo").innerHTML = svg("undo", 16);
  $("redo").innerHTML = svg("redo", 16);
  $("exportBtn").innerHTML = svg("down", 15) + "Exporter";

  $("undo").onclick = () => undo();
  $("redo").onclick = () => redo();
  on("history", () => {
    $("undo").disabled = !S.hist.length;
    $("redo").disabled = !S.redo.length;
  });

  const name = $("name");
  name.onchange = () => {
    snapshot();
    S.doc.name = name.value.trim() || "Nouveau montage";
    name.value = S.doc.name;
    document.title = S.doc.name + " · Montage IA";
    changed({ reason: "name" });
  };
  name.onkeydown = (e) => { if (e.key === "Enter") name.blur(); };

  on("save", ({ state, message }) => {
    const el = $("saveState");
    el.classList.toggle("error", state === "error");
    el.innerHTML = state === "saved" ? svg("check", 12) + "Enregistré"
      : state === "error" ? svg("warn", 12) + "Non enregistré"
      : "Enregistrement…";
    el.title = message || "";
  });
  on("doc", renderStats);
}

function renderStats() {
  const d = docDuration();
  $("stDur").innerHTML = `Durée <b>${fmt(d)}</b>`;
  $("stDur").title = tc(d, S.doc.canvas.fps) + " (min:s:image)";
  const c = S.doc.canvas;
  $("stFormat").innerHTML = `<b>${c.w}×${c.h}</b> · ${c.fps} i/s`;
}

export function docDuration() {
  return S.doc.clips.reduce((m, c) => Math.max(m, c.start + c.dur), 0);
}

/* ------------------------------------------------------------ mise en page */

function initLayout() {
  // Onglets du panneau de gauche.
  const tabs = [...$("leftTabs").children];
  tabs.forEach((t) => t.onclick = () => showTab(t.dataset.tab));

  // Séparateur timeline / zone du haut.
  const split = $("splitter");
  split.onpointerdown = (e) => {
    e.preventDefault();
    split.classList.add("on");
    const y0 = e.clientY;
    const h0 = $("tl").getBoundingClientRect().height;
    const move = (ev) => {
      const hgt = Math.max(150, Math.min(window.innerHeight - 260, h0 + (y0 - ev.clientY)));
      document.documentElement.style.setProperty("--tl-h", hgt + "px");
      fitStage();
    };
    const up = () => {
      split.classList.remove("on");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try { localStorage.setItem("studio.tlh", $("tl").getBoundingClientRect().height); } catch (err) { /* stockage indisponible */ }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  try {
    const saved = +localStorage.getItem("studio.tlh");
    if (saved > 150) document.documentElement.style.setProperty("--tl-h", saved + "px");
  } catch (err) { /* stockage indisponible */ }

  new ResizeObserver(fitStage).observe($("viewer"));
  on("doc", ({ reason }) => {
    if (reason === "canvas" || reason === "undo" || reason === "redo") fitStage();
    else $("stage").style.background = S.doc.canvas.bg;
  });
}

export function showTab(name) {
  [...$("leftTabs").children].forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  ["media", "text", "captions", "auto"].forEach((n) => $("tab-" + n).classList.toggle("hidden", n !== name));
  emit("tab", { name });
}

/** Aperçu au format du projet, le plus grand possible dans sa zone. */
export function fitStage() {
  const box = $("viewer").getBoundingClientRect();
  const { w, h: hh } = S.doc.canvas;
  const ratio = w / hh;
  let sw = box.width - 16, sh = sw / ratio;
  if (sh > box.height - 16) { sh = box.height - 16; sw = sh * ratio; }
  sw = Math.max(40, Math.floor(sw));
  sh = Math.max(40, Math.floor(sh));
  const stage = $("stage");
  stage.style.width = sw + "px";
  stage.style.height = sh + "px";
  stage.style.background = S.doc.canvas.bg;
  S.k = sh / hh;                        // pixels écran par pixel de sortie
  emit("layout");
}

/* ------------------------------------------------------------- format */

function initFormat() {
  const sel = $("fmtSel");
  const render = () => {
    const c = S.doc.canvas;
    sel.innerHTML = "";
    let matched = false;
    PRESETS.forEach((p) => {
      const on = p.w === c.w && p.h === c.h;
      matched = matched || on;
      sel.appendChild(h("option", { value: p.name, selected: on }, `${p.name} · ${p.w}×${p.h}`));
    });
    if (!matched) sel.appendChild(h("option", { value: "", selected: true }, `${c.w}×${c.h}`));
  };
  sel.onchange = () => {
    const p = PRESETS.find((x) => x.name === sel.value);
    if (!p) return;
    snapshot();
    S.doc.canvas.w = p.w;
    S.doc.canvas.h = p.h;
    changed({ reason: "canvas" });
    toast(`Format ${p.name} (${p.w}×${p.h})`);
  };
  on("doc", render);
  render();
}

/* -------------------------------------------------------------- clavier */

function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (typing(e) || document.querySelector(".overlay:not(.hidden)")) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
    else if (mod && k === "s") { e.preventDefault(); saveNow(); toast("Montage enregistré."); }
    else return;
  });
}

/* -------------------------------------------------------------- démarrage */

async function boot() {
  const pid = new URLSearchParams(location.hash.slice(1)).get("p");
  if (!pid) { location.href = "/"; return; }
  S.pid = pid;
  const [presets, proj] = await Promise.all([
    api("/api/timeline/presets"),
    api("/api/timeline/" + pid),
  ]);
  PRESETS.push(...presets.canvas);
  loadDoc(proj);

  $("name").value = S.doc.name;
  document.title = S.doc.name + " · Montage IA";
  initTopbar();
  initLayout();
  initFormat();
  initKeys();

  // Les zones de l'écran : chacune s'initialise puis écoute le store.
  const mods = await Promise.all([
    import("./bin.js"), import("./timeline.js"), import("./player.js"),
    import("./inspector.js"), import("./panels.js"), import("./exporter.js"), import("./stage.js"),
  ].map((p) => p.catch((err) => { console.error(err); return null; })));
  mods.forEach((m) => m && m.init && m.init());

  fitStage();
  emit("history");
  emit("doc", { reason: "load" });
  emit("media");
  emit("save", { state: "saved" });
}

boot().catch((e) => {
  document.body.innerHTML = "";
  document.body.appendChild(h("div.busyveil", {},
    h("div.box", {},
      h("div", { style: { fontWeight: 600, marginBottom: "8px" } }, "Montage indisponible"),
      h("div.meta", {}, e.message),
      h("a.btn", { href: "/", style: { marginTop: "14px" } }, "Retour aux projets"))));
});

