/* Montage automatique : un clic, et le reel est monté.

   Le bouton « Monter la vidéo » confie tout au moteur (engine/agent/reel.py),
   qui enchaîne la méthode du montage court, sur ce PC :

     1. dérush : les prises de chaque rush, retranscrites une par une ;
        reprises, faux départs, fins redites, claquettes et ratés écartés ;
     2. histoire : l'analyse de la voix (et le modèle de langage local s'il est
        là) choisit les phrases gardées et l'accroche ;
     3. coupe : blancs et tics retirés, recadrages rythmés sur le visage,
        voix mesurée et traitée, volume normalisé à l'export ;
     4. sous-titres dans le style choisi, un mot-clé par phrase qui grossit ;
     5. scènes animées calées sur les mots : écran partagé, chiffres qui
        défilent, page « ? » sur une question, mot fort sur le visage, bandeau
        sur un écran filmé, carte « commente … » pour l'appel à l'action —
        contrôlées (rythme, lisibilité, rien sur le visage) puis dessinées ;
     6. repères sur les moments forts.

   Le studio recharge ensuite le montage : tout se retouche à la main, Ctrl+Z
   ou « Revenir en arrière » rétablit le montage d'avant en un pas. */

import { api, post } from "./api.js";
import { startPolling } from "./bin.js";
import * as M from "./model.js";
import { S, changed, edit, on, replaceDoc, saveNow, setTime } from "./store.js";
import { $, fmt, h, put, sec, section, svg, toast } from "./util.js";
import { leads } from "./panels.js";

const A = { busy: false, llm: null, before: null, last: null, poll: 0, ready: null };

const DEFAULTS = { silence: true, fillers: true, trim: true, hook: true, cold_open: false, zoom: true, texts: true,
                   captions: true, sound: true, llm: true, rhythm: "dynamic", max_duration: 0, style: "net",
                   accent: "#D40F30", cta: "", look: "clean" };
const STYLES = [["net", "Net"], ["net_accent", "Net accent"], ["pilule", "Pilule"], ["bulle", "Bulle"],
                ["encre", "Encre"]];
const LAYOUTS = { split: ["écran partagé", "écrans partagés"], face: ["visage", "visages"],
                  full: ["page pleine", "pages pleines"], face_top: ["visage en haut", "visages en haut"],
                  face_box: ["visage en fenêtre", "visages en fenêtre"], world: ["décor", "décors"] };
// raisons du dérush (engine/timeline/takes.py, engine/agent/reel.py)
const WHY = [[/^no speech/, "pas de parole"], [/^false start/, "faux départ, refait juste après"],
             [/^contained/, "redit dans la prise suivante"], [/^retake/, "reprise : la suivante le redit mieux"],
             [/said again at the start/, "fin redite au début de la prise suivante : coupée"],
             [/^slate/, "claquette avant la prise"], [/^outtake/, "raté en fin de rush"]];

const opts = () => ({ ...DEFAULTS, ...((S.doc.settings || {}).auto || {}) });
function setOpt(k, v) {
  S.doc.settings = { ...(S.doc.settings || {}), auto: { ...opts(), [k]: v } };
  changed({ reason: "settings" });
}

/** Short automatique : `{ waiting }` tant que les vidéos se préparent, puis
 *  `{ count }` quand elles sont sur la timeline et que le montage attend le clic. */
export function setReady(state) {
  const same = JSON.stringify(state) === JSON.stringify(A.ready);
  A.ready = state;
  if (!same) render();
}

export function init() {
  on("tab", ({ name }) => { if (name === "auto") { render(); refreshLLM(); } });
  on("doc", ({ reason }) => { if (!A.busy && (reason === "load" || reason === "undo" || reason === "redo")) render(); });
}

/* ================================================================ panneau */

export function render() {
  const box = $("autoAI");
  if (!box) return;
  const o = opts();
  const toggle = (key, label, hint) => {
    const c = h("input", { type: "checkbox", checked: !!o[key] });
    c.onchange = () => { setOpt(key, c.checked); render(); };
    return h("label.check.opt", { title: hint || "" }, c, h("span", {}, label));
  };
  const seg = (key, items) => h("div.seg", {}, items.map(([k, l]) =>
    h("button", { class: o[key] === k ? "on" : "", onclick: () => { setOpt(key, k); render(); } }, l)));
  const field = (label, el, hint) => h("div.field", { title: hint || "" },
    h("div.head", {}, h("span.label", {}, label)), el);
  const style = h("select", {}, STYLES.map(([v, l]) => h("option", { value: v, selected: o.style === v }, l)));
  style.onchange = () => setOpt("style", style.value);
  const accent = h("input", { type: "color", value: o.accent || DEFAULTS.accent, title: "Couleur d'accent" });
  accent.onchange = () => setOpt("accent", accent.value.toUpperCase());
  const cta = h("input", { type: "text", value: o.cta || "", maxLength: 40, placeholder: "ex. RECETTE (vide : aucune)" });
  cta.onchange = () => setOpt("cta", cta.value.trim().replace(/^[«"'\s]+|[»"'\s]+$/g, ""));
  box.innerHTML = "";
  put(box, section({ title: "Montage automatique", icon: "wand", key: "ai.auto" },
    readyNote(),
    h("button.btn.primary.wide.big" + (A.ready && A.ready.count ? ".attn" : ""),
      { id: "aiGo", html: svg("wand", 15) + "Monter la vidéo", onclick: run, disabled: A.busy }),
    h("div.hint", { style: { margin: "6px 0 10px" } },
      "Dérush des prises, coupe, sous-titres à mots-clés, scènes animées calées sur les mots, son : " +
      "le reel complet en un clic, sur ton PC. Tout reste modifiable, et « Revenir en arrière » rétablit " +
      "le montage d'avant."),
    h("div.g2", {},
      field("Sous-titres", style, "Style des sous-titres : les mots-clés grossissent (Net accent : en couleur)"),
      field("Accent", accent, "Couleur des scènes : chiffres, mots en avant, « ? », carte d'appel à l'action")),
    h("div.g2", { style: { marginTop: "8px" } },
      field("Mot à commenter", cta, "Appel à l'action : une carte « COMMENTE « MOT » » sur la phrase qui en parle " +
                                    "(ou à la fin)"),
      field("Scènes", seg("look", [["clean", "Clair"], ["paper", "Papier"]]),
            "Clair : fond de la marque, cartes blanches. Papier : papier, grain, titres serif")),
    h("div.optgrid", { style: { marginTop: "10px" } },
      toggle("trim", "Dérush des prises", "Chaque prise retranscrite seule ; reprises, faux départs, fins redites, " +
                                          "claquettes et passages faibles écartés"),
      toggle("silence", "Blancs", "Retire les silences entre les mots (réglages dans « Supprimer les blancs »)"),
      toggle("fillers", "Tics", "Retire les euh, du coup, en fait…"),
      toggle("cold_open", "Ouverture à froid", "Remonte en tête une phrase forte venue plus tard (à laisser décoché si tu as tourné ton accroche)"),
      toggle("texts", "Scènes animées", "Écran partagé, chiffres, mots en avant, questions, appel à l'action, " +
                                        "calés sur les mots"),
      toggle("captions", "Sous-titres", "Un mot-clé par phrase, agrandi à l'écran"),
      toggle("zoom", "Recadrages", "Le visage recadré à chaque idée (rythme ci-dessous)"),
      toggle("sound", "Son", "Mesure chaque rush et règle la voix (niveau, bruit, compression, clarté), " +
                             "baisse la musique sous la voix, normalise le volume à l'export")),
    h("div.field", { style: { marginTop: "10px" } }, h("div.head", {}, h("span.label", {}, "Rythme")),
      seg("rhythm", [["calm", "Calme"], ["normal", "Normal"], ["punchy", "Punchy"], ["dynamic", "Dynamique"]])),
    h("div.field", { style: { marginTop: "8px" } }, h("div.head", {}, h("span.label", {}, "Durée visée")),
      (() => {
        const s = h("select", {}, [[0, "Libre"], [30, "≤ 30 s"], [45, "≤ 45 s"], [60, "≤ 60 s"], [90, "≤ 90 s"]]
          .map(([v, l]) => h("option", { value: v, selected: +o.max_duration === v }, l)));
        s.onchange = () => setOpt("max_duration", +s.value);
        return s;
      })()),
    h("div", { id: "aiLLM" }),
    h("div", { id: "aiLast" })));
  renderLLM();
  renderLast();
}

/** Rien ne part tout seul : on dit où on en est, et que le clic lance tout. */
function readyNote() {
  const r = A.ready;
  if (!r) return null;
  const text = r.waiting
    ? "Préparation des vidéos… Le montage attendra ton feu vert."
    : (r.count > 1 ? `${r.count} vidéos sont sur la timeline.` : "Ta vidéo est sur la timeline.") +
      " Vérifie les réglages, puis clique sur « Monter la vidéo ».";
  return h("div.aiready", { role: "status" }, h("i", { html: svg(r.waiting ? "refresh" : "check", 13) }),
           h("span", {}, text));
}

/* --------------------------------------------------- modèle de langage */

async function refreshLLM() {
  try { A.llm = await api("/api/llm"); } catch (e) { A.llm = null; }
  A.checked = true;
  renderLLM();
  const dl = (A.llm || {}).download || {};
  clearTimeout(A.poll);
  if (dl.status === "running") A.poll = setTimeout(refreshLLM, 1500);
  else if (dl.status === "done" && !A.llm.available) A.poll = setTimeout(refreshLLM, 1500);
}

function renderLLM() {
  const box = $("aiLLM");
  if (!box) return;
  const o = opts();
  const l = A.llm, dl = (l || {}).download || {};
  box.innerHTML = "";
  if (!l) {
    put(box, h("div.llm", {}, h("span.meta", {}, A.llm === null && A.checked ? "IA locale : état inconnu." : "IA locale : vérification…")));
    return;
  }
  const use = h("input", { type: "checkbox", checked: !!o.llm });
  use.onchange = () => setOpt("llm", use.checked);
  let state;
  if (l.available) {
    state = h("div.llm.ok", {},
      h("i", { html: svg("check", 13) }),
      h("span", {}, l.loaded ? "IA locale chargée" : "IA locale prête",
        h("span.meta", {}, " · Qwen3 4B, sur ton PC")),
      h("label.check", { style: { marginLeft: "auto" } }, use, "Utiliser"));
  } else if (dl.status === "running") {
    state = h("div.llm", {},
      h("div", { style: { flex: 1 } },
        h("div.row", { style: { justifyContent: "space-between" } }, h("span", {}, "Téléchargement du modèle…"),
          h("b", {}, Math.round(dl.pct || 0) + " %")),
        h("div.track-bar", { style: { margin: "6px 0 0" } }, h("i", { style: { width: (dl.pct || 0) + "%" } }))));
  } else {
    state = h("div.llm", {},
      h("i", { html: svg("warn", 13), style: { color: "var(--warn)" } }),
      h("span", {}, "Sans le modèle, l'accroche et les textes suivent des règles simples.",
        dl.status === "error" ? h("span.err", {}, " " + (dl.message || "Téléchargement impossible.")) : null),
      h("button.btn.sm", { style: { marginLeft: "auto", flexShrink: 0 }, html: svg("down", 12) + "Télécharger (4 Go)",
        onclick: async () => {
          try { await post("/api/llm/download", {}); toast("Téléchargement lancé : le modèle se range dans le cache HuggingFace."); }
          catch (e) { toast("Téléchargement impossible : " + e.message, 5000); }
          refreshLLM();
        } }));
  }
  put(box, state);
}

/* ------------------------------------------------------- dernier passage */

const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;
const why = (text) => (WHY.find(([re]) => re.test(text || "")) || [null, text || "écartée"])[1];

/** Le rapport du dernier montage : chaque décision, pour savoir quoi retoucher. */
export function lastLines(r) {
  const out = [];
  if (r.kept_takes || r.dropped_takes) {
    out.push({ icon: "cut", text: `Dérush : ${plural(r.kept_takes, "prise")} gardée${r.kept_takes > 1 ? "s" : ""}` +
      (r.dropped_takes ? `, ${r.dropped_takes} écartée${r.dropped_takes > 1 ? "s" : ""}` : "") });
  }
  for (const t of r.takes || []) {
    out.push({ icon: t.dropped ? "trash" : "cut", sub: true,
               text: `Prise ${t.n}${t.media ? " · " + t.media : ""} — ${why(t.why)} : « ${t.text} »` });
  }
  if (r.removed > 0.05) out.push({ icon: "cut", text: `−${sec(r.removed)} retirés (blancs, tics, passages)` });
  if (r.hook) out.push({ icon: "flag", text: `Accroche : « ${r.hook} »${r.cold_open ? " — remontée en tête" : ""}` });
  if ((r.scenes || []).length) {
    const n = {};
    r.scenes.forEach((s) => { n[s.layout] = (n[s.layout] || 0) + 1; });
    out.push({ icon: "grid", text: `${plural(r.scenes.length, "scène")} : ` +
      Object.entries(n).map(([k, v]) => `${v} ${(LAYOUTS[k] || [k, k])[v > 1 ? 1 : 0]}`).join(", ") });
  }
  if (r.scenes_error) out.push({ icon: "warn", warn: true, text: "Scènes non posées : " + r.scenes_error });
  if ((r.keywords || []).length) {
    const k = r.keywords.slice(0, 10).join(", ") + (r.keywords.length > 10 ? "…" : "");
    out.push({ icon: "text", text: `Mots-clés : ${k}` });
  }
  const bits = [];
  if (r.zooms) bits.push(plural(r.zooms, "recadrage"));
  if (r.captions) bits.push(`${r.captions} sous-titres`);
  if (r.sound) bits.push("son amélioré");
  if ((r.screens || []).length) bits.push(`écran filmé : ${r.screens.join(", ")}`);
  if (bits.length) out.push({ icon: "wand", text: bits.join(" · ") });
  if ((r.checks || []).length) {
    out.push({ icon: "warn", warn: true, title: r.checks.join("\n"),
               text: `${plural(r.checks.length, "point")} de mise en page à vérifier (survole pour le détail)` });
  }
  return out;
}

function renderLast() {
  const box = $("aiLast");
  if (!box) return;
  box.innerHTML = "";
  const r = A.last;
  if (!r) return;
  const parts = lastLines(r).map((l) => h("div.row", { title: l.title || "",
    style: { gap: "8px", padding: l.sub ? "1px 0 1px 20px" : "3px 0", alignItems: "flex-start" } },
    h("i", { html: svg(l.icon, l.sub ? 11 : 13),
             style: { color: l.warn ? "var(--warn)" : "var(--ink-2)", flexShrink: 0, marginTop: "1px" } }),
    h("span", { style: { fontSize: l.sub ? "11.5px" : "12.5px", color: l.sub ? "var(--ink-2)" : "" } }, l.text)));
  const hl = h("div", {});
  (r.highlights || []).forEach((x) => {
    hl.appendChild(h("div.row.hl", { onclick: () => setTime(Math.max(0, x.t - 0.3), { from: "list" }) },
      h("span.num", { style: { color: "var(--accent)", minWidth: "44px" } }, fmt(x.t)),
      h("span.ell", { style: { flex: 1 } }, x.label),
      h("button.btn.sm.quiet", { title: "Ne garder que ce moment (Ctrl+Z pour revenir)",
        onclick: (e) => { e.stopPropagation(); isolate(x); } }, "Isoler")));
  });
  put(box, section({ title: "Dernier montage", icon: "check", key: "ai.last", count: r.llm ? "IA" : "règles" },
    parts,
    (r.highlights || []).length ? h("div", { style: { marginTop: "6px" } },
      h("div.meta", { style: { margin: "4px 0 2px" } }, "Moments forts — repères ★ sur la timeline"), hl) : null,
    h("div.actions", { style: { marginTop: "10px" } },
      h("button.btn", { html: svg("undo", 13) + "Revenir en arrière", onclick: revert }),
      h("button.btn.quiet", { html: svg("refresh", 13) + "Refaire", title: "Repart du montage d'avant avec les options actuelles",
                              onclick: () => { revert(); run(); } }))));
}

function revert() {
  if (!A.before) return;
  const old = JSON.parse(A.before);
  edit((doc) => {
    for (const k of ["tracks", "clips", "markers"]) doc[k] = old[k];
    // les options du montage automatique choisies depuis restent
    doc.settings = { ...old.settings, auto: (doc.settings || {}).auto };
  }, "autoedit");
  A.last = null;
  A.before = null;
  render();
  toast("Montage d'avant rétabli.");
}

function isolate(x) {
  // une petite marge autour du moment, sans mordre sur les plans voisins
  const at = (t) => mainClips(S.doc).find((c) => c.start <= t + 1e-3 && M.clipEnd(c) >= t - 1e-3);
  const ca = at(x.t), cb = at(x.e);
  const a = Math.max(ca ? ca.start : 0, x.t - 0.3);
  const b = Math.min(cb ? M.clipEnd(cb) : M.duration(S.doc), x.e + 0.4);
  if (b - a < 1) { toast("Moment trop court."); return; }
  edit((doc) => M.isolateRange(doc, a, b), "isolate");
  setTime(0, { from: "list" });
  toast(`Seul ce moment reste (${sec(b - a)}). Ctrl+Z pour tout retrouver.`, 4000);
}

/* ================================================================== run */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Clips à monter : ceux de la piste principale qui ont du son (un par lien). */
function targets(doc) {
  const main = M.mainTrack(doc);
  return main ? leads(M.trackClips(doc, main.id)) : [];
}
const mainClips = (doc) => M.trackClips(doc, M.mainTrack(doc).id);

/** Le moteur part de ce qui est à l'écran : les retouches en attente partent d'abord. */
async function flush() {
  for (let i = 0; i < 60 && (S.saveTimer || S.saving); i++) {
    if (!S.saving) await saveNow();
    else await sleep(150);
  }
}

/** Recharge le montage du moteur s'il a changé (un seul pas d'annulation). */
async function reloadDoc() {
  const proj = await api(`/api/timeline/${S.pid}`);
  if ((proj.rev || 0) !== S.rev) replaceDoc(proj);
  startPolling();
}

export async function run() {
  if (A.busy) return;
  const mids = [...new Set(targets(S.doc).map((c) => c.media))];
  if (!mids.length) {
    toast("Ajoute d'abord une vidéo avec de la voix sur la piste principale.");
    return;
  }
  const o = opts();
  A.busy = true;
  A.ready = null;
  render();
  const veil = h("div.busyveil", {}, h("div.box", {},
    h("div", { style: { fontWeight: 600, marginBottom: "6px" } }, "Montage du reel"),
    h("div.meta", { id: "aiMsg" }, "Préparation…"),
    h("div.track-bar", {}, h("i", { id: "aiBar", style: { width: "2%" } })),
    h("div.meta", { style: { marginTop: "8px", fontSize: "11.5px" } },
      "Tout se fait sur ton PC : la première fois, compte quelques minutes (transcription, scènes).")));
  document.body.appendChild(veil);
  const say = (t, pct) => {
    const m = $("aiMsg");
    if (m && t) m.textContent = t;
    if (pct !== undefined) $("aiBar").style.width = Math.max(2, Math.min(100, pct)) + "%";
  };
  S.engineJob = true;
  try {
    await flush();
    A.before = JSON.stringify(S.doc);
    const { job_id } = await post(`/api/agent/${S.pid}/reel`, { ...o, media: mids });
    let job;
    for (;;) {
      await sleep(700);
      job = await api(`/api/agent/jobs/${job_id}`);
      if (job.status === "error") throw new Error(job.message || "montage impossible");
      if (job.status === "done") break;
      say(job.message, job.pct || 0);
    }
    say("Chargement du montage…", 100);
    await reloadDoc();
    A.last = job.result || {};
    setTime(0, { from: "list" });
    toast("Reel monté. Tout se retouche dans la timeline ; « Revenir en arrière » rétablit le montage d'avant.", 6000);
  } catch (e) {
    toast("Montage automatique impossible : " + e.message, 7000);
    try { await reloadDoc(); } catch (err) { /* moteur arrêté : rien à recharger */ }
  } finally {
    S.engineJob = false;
    A.busy = false;
    veil.remove();
    render();
  }
}
