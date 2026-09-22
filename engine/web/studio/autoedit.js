/* Montage automatique : un clic, et le short est monté.

   Le moteur analyse la voix de chaque média (phrases, énergie, tics, faux
   départs, formules d'intro et de fin) et, si le modèle de langage local est
   là, lui demande l'accroche, les passages à retirer, les moments forts et
   les textes à poser. Le studio applique ensuite le plan sur la timeline :

     1. coupes : blancs, tics, phrases retirées ;
     2. accroche : la phrase la plus forte passe en tête si l'IA le conseille,
        et son texte s'affiche en titre ;
     3. rythme : les longs plans sont coupés aux fins de phrases et zoomés en
        alternance, cadrés sur le visage ;
     4. textes à l'écran sur les phrases fortes ;
     5. sous-titres ;
     6. son : voix nettoyée, volume normalisé à l'export ;
     7. repères sur les moments forts.

   Tout reste modifiable ensuite, et « Revenir en arrière » rétablit le
   montage d'avant en un pas. */

import { api, post } from "./api.js";
import { emojiGeometry } from "./captions.js";
import { applyLook } from "./inspector.js";
import * as M from "./model.js";
import { S, changed, edit, on, setTime } from "./store.js";
import { $, fmt, h, put, sec, section, svg, toast } from "./util.js";
import { wordsOf } from "./words.js";
import { ensureTranscripts, generateCaptions, leads } from "./panels.js";

const A = { busy: false, llm: null, before: null, last: null, poll: 0 };

const DEFAULTS = { silence: true, fillers: true, trim: true, hook: true, zoom: true, texts: true,
                   captions: true, sound: true, llm: true, rhythm: "normal", max_duration: 0 };
// rythme : longueur maximale d'un plan avant une coupe, et force des zooms
const RHYTHM = { calm: { max: 9, z: 1.1, zh: 1.22 }, normal: { max: 6, z: 1.15, zh: 1.28 },
                 punchy: { max: 4, z: 1.2, zh: 1.32 } };
const HOOK_MAX = 9;          // s : au-delà, une phrase n'est plus une accroche à déplacer
const TITLE_DUR = 3.2;       // s : durée du titre d'accroche
const MARK = "★ ";           // préfixe des repères posés ici

const HOOK_LOOK = { font: "Arial Black", size: 100, color: "#FFFFFF", outline_col: "#000000", outline: 7, shadow: 3,
                    box: false, box_alpha: 0.25, hl: "#FFFFFF", bold: true, upper: false, mode: "none", pop: false };
const TEXT_LOOK = { font: "Arial Black", size: 64, color: "#FFE500", outline_col: "#000000", outline: 6, shadow: 2,
                    box: false, box_alpha: 0.25, hl: "#FFE500", bold: true, upper: true, mode: "none", pop: false };

const opts = () => ({ ...DEFAULTS, ...((S.doc.settings || {}).auto || {}) });
function setOpt(k, v) {
  S.doc.settings = { ...(S.doc.settings || {}), auto: { ...opts(), [k]: v } };
  changed({ reason: "settings" });
}

export function init() {
  on("tab", ({ name }) => { if (name === "auto") { render(); refreshLLM(); } });
  on("doc", ({ reason }) => { if (reason === "load" || reason === "undo" || reason === "redo") render(); });
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
  box.innerHTML = "";
  put(box, section({ title: "Montage automatique", icon: "wand", key: "ai.auto" },
    h("button.btn.primary.wide.big", { id: "aiGo", html: svg("wand", 15) + "Monter la vidéo", onclick: run,
                                       disabled: A.busy }),
    h("div.hint", { style: { margin: "6px 0 10px" } },
      "Blancs, tics, faux départs, accroche, zooms, textes, sous-titres, son : tout d'un coup, sur ton PC. " +
      "Chaque geste reste modifiable, et « Revenir en arrière » rétablit le montage d'avant."),
    h("div.optgrid", {},
      toggle("silence", "Blancs", "Retire les silences entre les mots (réglages dans « Supprimer les blancs »)"),
      toggle("fillers", "Tics", "Retire les euh, du coup, en fait…"),
      toggle("trim", "Passages inutiles", "Intro, fin, faux départs, phrases faibles"),
      toggle("hook", "Accroche", "Titre au début, et phrase forte remontée en tête si l'IA le conseille"),
      toggle("zoom", "Zooms", "Coupes rythmées aux fins de phrases, zooms alternés cadrés sur le visage"),
      toggle("texts", "Textes à l'écran", "Mots-clés posés sur les phrases fortes"),
      toggle("captions", "Sous-titres", "Style choisi dans l'onglet Sous-titres"),
      toggle("sound", "Son", "Voix nettoyée et niveau normalisé à l'export")),
    h("div.g2", { style: { marginTop: "10px" } },
      h("div.field", {}, h("div.head", {}, h("span.label", {}, "Rythme")),
        seg("rhythm", [["calm", "Calme"], ["normal", "Normal"], ["punchy", "Punchy"]])),
      h("div.field", {}, h("div.head", {}, h("span.label", {}, "Durée visée")),
        (() => {
          const s = h("select", {}, [[0, "Libre"], [30, "≤ 30 s"], [45, "≤ 45 s"], [60, "≤ 60 s"], [90, "≤ 90 s"]]
            .map(([v, l]) => h("option", { value: v, selected: +o.max_duration === v }, l)));
          s.onchange = () => setOpt("max_duration", +s.value);
          return s;
        })())),
    h("div", { id: "aiLLM" }),
    h("div", { id: "aiLast" })));
  renderLLM();
  renderLast();
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

function renderLast() {
  const box = $("aiLast");
  if (!box) return;
  box.innerHTML = "";
  const r = A.last;
  if (!r) return;
  const line = (icon, text) => h("div.row", { style: { gap: "8px", padding: "3px 0", alignItems: "flex-start" } },
    h("i", { html: svg(icon, 13), style: { color: "var(--ink-2)", flexShrink: 0, marginTop: "1px" } }),
    h("span", { style: { fontSize: "12.5px" } }, text));
  const parts = [];
  if (r.removed > 0.05) parts.push(line("cut", `−${sec(r.removed)} retirés` +
    (r.dropped ? ` (blancs, tics et ${r.dropped} phrase${r.dropped > 1 ? "s" : ""})` : " (blancs et tics)")));
  if (r.hook) parts.push(line("flag", `Accroche : « ${r.hook} »${r.coldOpen ? " — remontée en tête" : ""}`));
  const bits = [];
  if (r.zooms) bits.push(`${r.zooms} zoom${r.zooms > 1 ? "s" : ""}`);
  if (r.texts) bits.push(`${r.texts} texte${r.texts > 1 ? "s" : ""}`);
  if (r.captions) bits.push(`${r.captions} sous-titres`);
  if (r.sound) bits.push("son amélioré");
  if (bits.length) parts.push(line("wand", bits.join(" · ")));
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

export async function run() {
  if (A.busy) return;
  if (!targets(S.doc).length) {
    toast("Ajoute d'abord une vidéo avec de la voix sur la piste principale.");
    return;
  }
  const o = opts();
  A.busy = true;
  render();
  const veil = h("div.busyveil", {}, h("div.box", {},
    h("div", { style: { fontWeight: 600, marginBottom: "6px" } }, "Montage automatique"),
    h("div.meta", { id: "aiMsg" }, "Préparation…"),
    h("div.track-bar", {}, h("i", { id: "aiBar", style: { width: "4%" } }))));
  document.body.appendChild(veil);
  const say = (t, pct) => {
    const m = $("aiMsg");
    if (m && t) m.textContent = t;
    if (pct !== undefined) $("aiBar").style.width = pct + "%";
  };
  try {
    const mids = [...new Set(targets(S.doc).map((c) => c.media))];
    say("Transcription de la voix (Whisper, sur ton PC)…", 6);
    if (!(await ensureTranscripts(mids, (t) => say(t, 12)))) return;
    say(o.llm && A.llm && A.llm.available ? "L'IA lit la vidéo…" : "Analyse de la vidéo…", 20);
    const { plans, llm } = await fetchPlans(mids, o, say);
    const words = new Map();
    for (const mid of mids) words.set(mid, await wordsOf(mid));
    A.before = JSON.stringify(S.doc);
    say("Montage…", 72);
    const report = edit((doc) => apply(doc, plans, words, o), "autoedit");
    report.llm = llm;
    if (o.captions) {
      say("Sous-titres…", 86);
      report.captions = await generateCaptions({ quiet: true, replace: true });
    }
    say("Terminé", 100);
    A.last = report;
    setTime(0, { from: "list" });
    toast("Vidéo montée. Tout se retouche dans la timeline ; « Revenir en arrière » rétablit l'original.", 6000);
  } catch (e) {
    toast("Montage automatique impossible : " + e.message, 6000);
  } finally {
    A.busy = false;
    veil.remove();
    render();
  }
}

async function fetchPlans(mids, o, say) {
  const res = await post(`/api/timeline/${S.pid}/autoedit`,
    { media: mids, options: { llm: o.llm, trim: o.trim, max_duration: o.max_duration } });
  for (;;) {
    await sleep(600);
    const job = await api(`/api/timeline/${S.pid}/autoedit/${res.job_id}`);
    if (job.status === "error") throw new Error(job.message || "analyse impossible");
    say(job.message, 20 + (job.pct || 0) * 0.5);
    if (job.status === "done") return { plans: job.plans || {}, llm: !!res.llm };
  }
}

/* ============================================================ application */

/** Applique les plans au document (une seule étape d'annulation). */
export function apply(doc, plans, wordsBy, o) {
  const rep = { removed: 0, dropped: 0, hook: "", coldOpen: false, zooms: 0, texts: 0, captions: 0,
                sound: false, highlights: [] };
  const st = doc.settings || {};
  // un passage précédent : ses textes et repères s'effacent
  doc.clips = doc.clips.filter((c) => !(c.kind === "text" && c.ai));
  doc.markers = (doc.markers || []).filter((m) => !(m.label || "").startsWith(MARK));

  // 1. coupes, du dernier clip au premier (les coupes ne décalent pas ce qui précède)
  for (const c of targets(doc).sort((a, b) => b.start - a.start)) {
    const plan = plans[c.media];
    if (!plan) continue;
    const cutOpts = { maxGap: st.max_gap ?? 0.5, pad: st.pad ?? 0.08, fillers: !!o.fillers, extra: [] };
    const words = wordsBy.get(c.media) || [];
    if (o.silence) cutOpts.words = words;
    else if (o.fillers) {                          // les tics seuls, sans toucher aux blancs
      cutOpts.extra.push(...M.fillerCuts(words.filter((w) => w.end > c.in && w.start < M.srcEnd(c)), 0.05));
    }
    if (o.trim) {
      const inside = (plan.drop || []).filter(([s, e]) => e > c.in && s < M.srcEnd(c));
      cutOpts.extra.push(...inside);
      rep.dropped += inside.length;
    }
    const cuts = M.clipCuts(c, cutOpts);
    if (cuts.length) rep.removed += M.applyCuts(doc, c, cuts).removed;
  }

  // 2. accroche : remontée en tête si l'IA le conseille, puis titre
  const first = targets(doc)[0];
  const plan0 = first && plans[first.media];
  if (o.hook && plan0 && plan0.hook) {
    const hk = plan0.hook;
    const a = M.mapSource(mainClips(doc), first.media, hk.s + 0.02);
    const b = M.mapSource(mainClips(doc), first.media, Math.max(hk.s + 0.05, hk.e - 0.05));
    if (hk.cold_open && a && b && a.t > 0.5 && hk.e - hk.s <= HOOK_MAX) {
      const ta = Math.max(a.clip.start, a.t - 0.12), tb = Math.min(M.clipEnd(b.clip), b.t + 0.2);
      const all = () => new Set(mainClips(doc).map((c) => c.id));
      M.splitAt(doc, tb, all());
      M.splitAt(doc, ta, all());
      const ids = new Set(mainClips(doc).filter((c) => c.start >= ta - 1e-3 && M.clipEnd(c) <= tb + 1e-3).map((c) => c.id));
      if (ids.size) { M.moveToFront(doc, ids); rep.coldOpen = true; }
    }
    const text = (hk.text || plan0.title || "").trim();
    if (text) {
      addTitle(doc, { text, start: 0, dur: TITLE_DUR, look: HOOK_LOOK, y: 0.26, tag: "hook",
                      size: text.length > 40 ? 68 : text.length > 22 ? 84 : 100 });
      rep.hook = text;
    }
  }

  // 3. rythme et zooms : coupes aux fins de phrases, zoom un plan sur deux,
  //    plus fort sur les moments forts, cadré sur le visage
  if (o.zoom) {
    const R = RHYTHM[o.rhythm] || RHYTHM.normal;
    for (const c of mainClips(doc).filter((x) => x.kind === "video")) {
      const plan = plans[c.media];
      let cur = c;
      for (const t of M.rhythmSplits(c, plan ? plan.cutpoints : [], { minPiece: 2, maxPiece: R.max })) {
        const next = M.splitAt(doc, t, new Set([cur.id])).find((r) => r.track === cur.track);
        if (next) cur = next;
      }
    }
    let odd = false;
    for (const p of mainClips(doc).filter((x) => x.kind === "video" && x.fit !== "contain")) {
      const plan = plans[p.media] || {};
      const strong = (plan.highlights || []).some((x) => x.s < M.srcEnd(p) - 0.3 && x.e > p.in + 0.3);
      const scale = strong ? R.zh : odd ? R.z : 1;
      odd = !odd;
      const { x, y } = M.zoomCenter(plan.face, S.media.get(p.media), doc.canvas, scale);
      Object.assign(p, { scale, x, y });
      if (scale > 1) rep.zooms++;
    }
  }

  // 4. textes à l'écran (hors zone du titre)
  if (o.texts) {
    for (const mid of Object.keys(plans)) {
      for (const tx of plans[mid].texts || []) {
        const hit = M.mapSource(mainClips(doc), mid, tx.s + 0.02);
        if (!hit || (rep.hook && hit.t < TITLE_DUR + 0.2)) continue;
        const end = M.mapSource(mainClips(doc), mid, tx.e - 0.05);
        const dur = Math.min(2.6, Math.max(1.6, end ? end.t - hit.t : 2));
        const text = (tx.text || "").trim();
        if (!text) continue;
        if (addTitle(doc, { text, start: hit.t, dur, look: TEXT_LOOK, y: 0.2, tag: "text",
                            size: text.length > 18 ? 54 : 64, track: "Textes" })) rep.texts++;
      }
    }
  }

  // 5. son : voix nettoyée, niveau normalisé à l'export
  if (o.sound) {
    for (const c of targets(doc)) {
      for (const g of [c, ...M.partners(doc, c)]) {
        if (g.kind === "video" || g.kind === "audio") g.audio_fx = { denoise: true, voice: true };
      }
    }
    doc.settings = { ...(doc.settings || {}), loudness: true };
    rep.sound = true;
  }

  // 6. repères sur les moments forts
  for (const mid of Object.keys(plans)) {
    for (const x of plans[mid].highlights || []) {
      const hit = M.mapSource(mainClips(doc), mid, x.s + 0.02);
      if (!hit) continue;
      const end = M.mapSource(mainClips(doc), mid, x.e - 0.05);
      const label = MARK + (x.label || "Moment fort");
      doc.markers = (doc.markers || []).concat([{ id: M.uid("k"), t: M.r4(hit.t), label, color: "#F5B000" }]);
      rep.highlights.push({ t: M.r4(hit.t), e: M.r4(end ? end.t : hit.t + (x.e - x.s)), label: x.label || "Moment fort" });
    }
  }
  rep.highlights.sort((p, q) => p.t - q.t);
  M.reflowCaptions(doc);
  M.fixOverlaps(doc);
  return rep;
}

/** Texte posé par le montage automatique, sur sa propre piste. */
function addTitle(doc, { text, start, dur, look, y, tag, size, track = "Titres" }) {
  let tr = doc.tracks.find((t) => t.kind === "text" && t.name === track);
  if (!tr) tr = M.addTrack(doc, "text", { name: track });
  const s = M.r4(start), d = M.r4(dur);
  if (!M.isFree(doc, tr.id, s, d)) return null;
  const [esz, edy] = emojiGeometry(size || look.size);
  const clip = {
    id: M.uid("x"), track: tr.id, kind: "text", start: s, dur: d, auto: false, ai: tag,
    words: [{ text, start: s, end: M.r4(s + d) }],
    font: "Arial", size: 90, bold: true, upper: false, color: "#FFFFFF", hl: "#FFFFFF",
    outline_col: "#000000", outline: 5, shadow: 0, box: false, box_alpha: 0.25,
    mode: "none", pop: false, x: 0.5, y, emoji: "", emoji_size: esz, emoji_dx: 0, emoji_dy: edy, moved: true,
  };
  applyLook(clip, { ...look, size: size || look.size });
  clip.mode = "none";
  clip.pop = false;
  clip.x = 0.5;
  clip.y = y;
  doc.clips.push(clip);
  return clip;
}
