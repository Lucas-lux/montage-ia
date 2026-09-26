/* Panneaux de gauche : Texte, Sous-titres, Outils IA.

   - Texte : ajoute des titres (texte libre) à la tête de lecture.
   - Sous-titres : génère des sous-titres liés à la voix depuis les clips de la
     timeline, liste les lignes, fusionne, masque, traduit.
   - Outils IA : montage automatique (autoedit.js), transcription des médias,
     suppression des blancs (par la voix ou par le volume) et des tics de
     langage, avec aperçu avant d'appliquer. */

import { api, post } from "./api.js";
import * as autoedit from "./autoedit.js";
import { importing, setAutoTimeline, startPolling } from "./bin.js";
import { emojiGeometry } from "./captions.js";
import { I as Insp, applyLook, styleGrid } from "./inspector.js";
import { showTab } from "./main.js";
import * as M from "./model.js";
import { S, changed, edit, emit, on, select, setMedia, setTime } from "./store.js";
import { $, clamp, fmt, h, put, sec, section, svg, toast } from "./util.js";
import { textBetween, wordsOf } from "./words.js";

const X = {
  preview: null,           // [{ id, cuts }] : coupes proposées, pas encore appliquées
  scope: "main",
  method: "voice",
  noise: -35, minSil: 0.4,
  busy: false,
  capStyle: null,
  replace: true,
  translateOk: null,
};

export function init() {
  buildText();
  buildCaptions();
  buildAuto();
  on("tool", ({ name, scope }) => {
    if (name === "silence") { if (scope) X.scope = scope; showTab("auto"); renderAuto(); }
    else if (name === "captions") showTab("captions");
    else if (name === "text") addText(TITLE_STYLES[0]);
  });
  on("transcribe", ({ ids, force }) => transcribe(ids, force));
  on("media", () => { renderTranscripts(); renderCapState(); });
  on("doc", ({ light }) => {
    if (!light && X.preview) clearPreview();
    renderCapList();
    renderCapState();
  });
  on("select", renderCapList);
  on("tab", ({ name }) => { if (name === "auto") renderAuto(); if (name === "captions") renderCapList(); });
  on("presets", renderCapGen);
  on("passage", ({ entry, x, y }) => openPassage(entry, x, y));
  on("doc", () => renderPassages());
  // Arrivée depuis « Short automatique » : les vidéos importées rejoignent la
  // timeline, mais le montage attend qu'on clique sur « Monter la vidéo ».
  if (new URLSearchParams(location.hash.slice(1)).get("auto") === "1") {
    X.autoArmed = true;
    setAutoTimeline(true);
    showTab("media");
    toast("Short automatique : importe tes vidéos, puis lance le montage avec « Monter la vidéo ».", 6000);
    on("media", armedReady);
    on("doc", armedReady);
    armedReady();
  }
}

/* ======================================================== short automatique */

/** Short automatique : dès que les vidéos importées sont prêtes et posées sur
 *  la timeline, l'onglet Outils IA s'ouvre sur « Monter la vidéo ». Rien ne
 *  part sans ce clic : on peut d'abord ranger, couper ou changer les réglages. */
function armedReady() {
  if (!X.autoArmed) return;
  const vids = [...S.media.values()].filter((m) => m.kind === "video");
  const waiting = vids.filter((m) => m.status === "pending" || m.status === "processing").length;
  const main = M.mainTrack(S.doc);
  const placed = main ? M.trackClips(S.doc, main.id).filter((c) => c.kind === "video").length : 0;
  if (waiting || importing() || !placed) {
    autoedit.setReady(waiting || importing() ? { waiting: Math.max(1, waiting) } : null);
    return;
  }
  X.autoArmed = false;
  setAutoTimeline(false);
  history.replaceState(null, "", "#p=" + S.pid);
  // un short sans nom prend celui de sa première vidéo
  if (!S.doc.name || S.doc.name === "Nouveau montage") {
    const first = S.media.get(M.trackClips(S.doc, main.id).find((c) => c.kind === "video").media);
    if (first) {
      S.doc.name = first.name.replace(/\.[^.]+$/, "");
      changed({ reason: "name" });
      const input = document.getElementById("name");
      if (input) input.value = S.doc.name;
    }
  }
  autoedit.setReady({ count: placed });
  showTab("auto");
  toast(placed > 1 ? `${placed} vidéos prêtes : clique sur « Monter la vidéo » quand tu veux.`
    : "Vidéo prête : clique sur « Monter la vidéo » quand tu veux.", 6000);
}

/* ============================================================== transcription */

const voiceClips = (clips) => clips.filter((c) => !c.gone &&
  (c.kind === "audio" || (c.kind === "video" && !c.detached)) &&
  (S.media.get(c.media) || {}).has_audio && !c.muted);

const trStatus = (mid) => ((S.media.get(mid) || {}).transcript || {}).status || "none";

export async function transcribe(ids, force = false) {
  try {
    const res = await post(`/api/timeline/${S.pid}/transcribe`, { media: ids, force });
    setMedia(res.media);
    emit("media");
    startPolling();
    if (res.queued.length) toast(`Transcription lancée (${res.queued.length} média${res.queued.length > 1 ? "s" : ""}).`);
    return res;
  } catch (e) { toast("Transcription impossible : " + e.message); return null; }
}

/** Attend la fin des transcriptions demandées. Renvoie les médias en erreur. */
function waitTranscripts(ids, onTick) {
  return new Promise((resolve) => {
    const tick = () => {
      const st = ids.map(trStatus);
      if (onTick) onTick(st.filter((s) => s === "done").length, ids.length);
      if (st.every((s) => s === "done" || s === "error" || s === "none")) {
        resolve(ids.filter((id) => trStatus(id) !== "done"));
        return;
      }
      setTimeout(tick, 700);
    };
    tick();
  });
}

/** S'assure que ces médias sont transcrits (lance et attend ce qui manque). */
export async function ensureTranscripts(ids, status) {
  const need = [...new Set(ids)].filter((id) => trStatus(id) !== "done");
  if (!need.length) return true;
  const fresh = need.filter((id) => !["queued", "running"].includes(trStatus(id)));
  if (fresh.length) await transcribe(fresh);
  const failed = await waitTranscripts(need, (done, total) => status &&
    status(`Transcription… ${done}/${total} média${total > 1 ? "s" : ""} (Whisper, sur ton PC)`));
  if (failed.length) {
    const m = S.media.get(failed[0]);
    toast(`Transcription impossible pour « ${m ? m.name : failed[0]} » : ${((m || {}).transcript || {}).error || "erreur"}`, 5000);
    return false;
  }
  return true;
}


/* ==================================================================== texte */

/* Styles de titre. Même vocabulaire que les sous-titres (police, contour,
   fond…) mais sans surlignage : un titre est un texte libre. `y` place le
   texte quand on le crée. */
const T = (label, hint, look, group = "base") => ({ label, hint, group, mode: "none", pop: false, bold: true,
  upper: false, shadow: 0, box: false, box_alpha: 0.25, hl: look.color || "#FFFFFF", ...look });
// polices livrées (engine/data/fonts) : déjà grasses, pas de gras ajouté
const B = (look) => ({ bold: false, ...look });
const TITLE_GROUPS = [
  { name: "base", label: "Essentiels" }, { name: "anime", label: "Animés" }, { name: "neon", label: "Néon et lueur" },
  { name: "relief", label: "Relief et 3D" }, { name: "degrade", label: "Dégradés" },
  { name: "manuscrit", label: "Manuscrits" }, { name: "retro", label: "Rétro et jeux" }, { name: "sobre", label: "Sobres" },
];
const TITLE_STYLES = [
  T("Titre", "blanc, gras, contour", { font: "Arial Black", size: 110, color: "#FFFFFF", outline_col: "#000000", outline: 6, shadow: 2, y: 0.3 }),
  T("Bandeau", "texte sur fond rouge", { font: "Arial", size: 72, color: "#FFFFFF", outline_col: "#F23A52", outline: 14, box: true, box_alpha: 0, upper: true, y: 0.72 }),
  T("Impact", "énorme, majuscules", { font: "Impact", size: 150, color: "#FFE500", outline_col: "#000000", outline: 9, upper: true, y: 0.5 }),
  T("Accroche", "Montserrat, ombre douce", B({ font: "Montserrat Black", size: 120, color: "#FFFFFF", outline_col: "#000000", outline: 6, shadow: 8, shadow_blur: 10, upper: true, y: 0.3 })),
  T("Géant", "Anton, énorme", B({ font: "Anton", size: 190, color: "#FFFFFF", outline_col: "#000000", outline: 8, upper: true, y: 0.45 })),
  T("Condensé", "haut et serré", { font: "Bahnschrift", size: 130, color: "#FFFFFF", outline_col: "#000000", outline: 5, upper: true, y: 0.35 }),
  T("Alerte", "fond jaune", { font: "Arial Black", size: 76, color: "#17181C", outline_col: "#FFE500", outline: 12, box: true, box_alpha: 0, upper: true, y: 0.2 }),
  T("Étiquette", "petit, en haut à gauche", { font: "Segoe UI Black", size: 50, color: "#FFFFFF", outline_col: "#17181C", outline: 10, box: true, box_alpha: 0.1, upper: true, x: 0.24, y: 0.1 }),
  // animés
  T("Pop", "surgit", B({ font: "Luckiest Guy", size: 130, color: "#FFE500", outline_col: "#000000", outline: 8, upper: true, y: 0.35,
    anim_in: { type: "pop", dur: 0.4 }, anim_out: { type: "zoom_out_out", dur: 0.3 } }), "anime"),
  T("Machine à écrire", "tapé lettre par lettre", B({ font: "Syne Mono", size: 80, color: "#D8FFE0", outline_col: "#0B0F0B", outline: 12, box: true, box_alpha: 0.1, y: 0.4,
    anim_in: { type: "typewriter", dur: 1.2 } }), "anime"),
  T("Chute", "tombe et rebondit", B({ font: "Bangers", size: 150, color: "#FFFFFF", outline_col: "#000000", outline: 7, upper: true, extrude: 8, extrude_col: "#F23A52", y: 0.35,
    anim_in: { type: "drop", dur: 0.8 } }), "anime"),
  T("Cinéma", "sort du flou, s'efface", B({ font: "Cinzel Bold", size: 96, color: "#F5E6C8", outline_col: "#000000", outline: 0, shadow: 6, shadow_blur: 12, spacing: 10, y: 0.5,
    anim_in: { type: "blur", dur: 1.0 }, anim_out: { type: "fade_out", dur: 0.8 } }), "anime"),
  T("Lettre à lettre", "apparaît lettre par lettre", B({ font: "Montserrat Black", size: 110, color: "#FFFFFF", outline_col: "#000000", outline: 5, glow: 16, glow_col: "#00E5FF", y: 0.4,
    anim_in: { type: "letters", dur: 1.0 } }), "anime"),
  T("Tourbillon", "arrive en tournant", B({ font: "Titan One", size: 120, color: "#FF3B81", outline_col: "#FFFFFF", outline: 7, y: 0.4,
    anim_in: { type: "spin", dur: 0.7 } }), "anime"),
  T("Battement", "bat comme un cœur", B({ font: "Lilita One", size: 130, color: "#FF2B55", outline_col: "#FFFFFF", outline: 7, y: 0.4,
    anim_loop: { type: "heartbeat", speed: 1 } }), "anime"),
  T("Flottant", "flotte doucement", B({ font: "Fredoka Bold", size: 110, color: "#FFFFFF", outline_col: "#2B6CFF", outline: 8, y: 0.3,
    anim_in: { type: "slide_up", dur: 0.5 }, anim_loop: { type: "float", speed: 1 } }), "anime"),
  // néon et lueur
  T("Néon rose", "lueur rose", B({ font: "Montserrat Black", size: 110, color: "#FFFFFF", outline_col: "#FF2BD6", outline: 3, glow: 30, glow_col: "#FF2BD6", y: 0.4 }), "neon"),
  T("Néon bleu", "lueur cyan", B({ font: "Audiowide", size: 104, color: "#FFFFFF", outline_col: "#00C8FF", outline: 3, glow: 30, glow_col: "#00C8FF", y: 0.4 }), "neon"),
  T("Enseigne", "néon qui clignote", B({ font: "Monoton", size: 110, color: "#FFE500", outline: 0, glow: 26, glow_col: "#FF9F1C", upper: true, y: 0.4,
    anim_loop: { type: "blink", speed: 0.6 } }), "neon"),
  T("Braise", "orange incandescent", B({ font: "Kanit ExtraBold", size: 120, color: "#FFE08A", outline_col: "#C21E00", outline: 4, glow: 24, glow_col: "#FF4D1A", upper: true, y: 0.4 }), "neon"),
  T("Halo", "texte lumineux", B({ font: "Poppins ExtraBold", size: 110, color: "#FFFFFF", outline: 0, glow: 34, glow_col: "#FFFFFF", y: 0.4 }), "neon"),
  // relief et 3D
  T("Pop 3D", "jaune sur relief rose", B({ font: "Bangers", size: 150, color: "#FFE500", outline_col: "#1B1B1B", outline: 6, extrude: 18, extrude_col: "#C2185B", upper: true, y: 0.4 }), "relief"),
  T("Bloc 3D", "blanc sur relief rouge", B({ font: "Russo One", size: 120, color: "#FFFFFF", outline_col: "#000000", outline: 5, extrude: 16, extrude_col: "#F23A52", y: 0.4 }), "relief"),
  T("Or massif", "doré en relief", B({ font: "Alfa Slab One", size: 120, color: "#FFE27A", color2: "#FFA000", outline_col: "#3A2800", outline: 5, extrude: 12, extrude_col: "#6B4A00", y: 0.4 }), "relief"),
  T("BD", "double contour", B({ font: "Bangers", size: 140, color: "#FFFFFF", outline_col: "#000000", outline: 7, outline2: 8, outline2_col: "#FFE500", upper: true, y: 0.4 }), "relief"),
  T("Autocollant", "contour blanc épais", B({ font: "Fredoka Bold", size: 120, color: "#17181C", outline_col: "#FFFFFF", outline: 12, shadow: 6, shadow_blur: 10, y: 0.4 }), "relief"),
  // dégradés
  T("Coucher de soleil", "corail vers doré", B({ font: "Poppins ExtraBold", size: 120, color: "#FF5F6D", color2: "#FFC371", outline_col: "#2B0A18", outline: 6, y: 0.4 }), "degrade"),
  T("Océan", "bleu profond", B({ font: "Montserrat Black", size: 120, color: "#00C6FF", color2: "#0072FF", outline_col: "#FFFFFF", outline: 6, y: 0.4 }), "degrade"),
  T("Aurore", "violet vers bleu", B({ font: "Rubik Black", size: 120, color: "#B721FF", color2: "#21D4FD", outline_col: "#14002B", outline: 5, glow: 12, glow_col: "#7C5CFF", y: 0.4 }), "degrade"),
  T("Or", "doré", { font: "Cambria", size: 100, color: "#FFD84D", outline_col: "#3A2800", outline: 5, shadow: 3, y: 0.4 }, "degrade"),
  // manuscrits
  T("Marqueur", "feutre", B({ font: "Permanent Marker", size: 110, color: "#FFFFFF", outline_col: "#000000", outline: 5, y: 0.35 }), "manuscrit"),
  T("Signature", "calligraphie", B({ font: "Great Vibes", size: 150, color: "#FFFFFF", outline_col: "#000000", outline: 2, shadow: 4, shadow_blur: 6, y: 0.4 }), "manuscrit"),
  T("Tendre", "rose, lueur douce", B({ font: "Pacifico", size: 110, color: "#FFF0F5", outline_col: "#FF3B81", outline: 5, glow: 14, glow_col: "#FF8FB1", y: 0.4 }), "manuscrit"),
  T("Craie", "écrit à la main", B({ font: "Caveat Bold", size: 130, color: "#FFFFFF", outline: 0, shadow: 5, shadow_blur: 6, y: 0.35 }), "manuscrit"),
  T("Manuscrit", "écrit à la main", { font: "Ink Free", size: 110, color: "#FFFFFF", outline_col: "#000000", outline: 5, shadow: 2, y: 0.35 }, "manuscrit"),
  // rétro et jeux
  T("Arcade", "pixels", B({ font: "Press Start 2P", size: 70, color: "#39FF14", outline_col: "#000000", outline: 6, upper: true, y: 0.4 }), "retro"),
  T("VHS", "cassette", B({ font: "VT323", size: 150, color: "#E8FFF0", outline: 0, glow: 12, glow_col: "#00FFB3", shadow: 5, shadow_col: "#FF0055", y: 0.4 }), "retro"),
  T("Glitch", "tremble et se décale", B({ font: "Rubik Glitch", size: 120, color: "#FFFFFF", outline_col: "#000000", outline: 4, shadow: 5, shadow_col: "#00E5FF", y: 0.4,
    anim_loop: { type: "glitch", speed: 1 } }), "retro"),
  T("Frisson", "horreur", B({ font: "Creepster", size: 130, color: "#B8FF3C", outline_col: "#1A0000", outline: 5, glow: 14, glow_col: "#6BFF00", y: 0.4 }), "retro"),
  T("Terminal", "monospace vert", { font: "Consolas", size: 70, color: "#00FF66", outline_col: "#0B0F0B", outline: 10, box: true, box_alpha: 0.1, y: 0.5 }, "retro"),
  // sobres
  T("Légende", "sobre, petit", { font: "Segoe UI", size: 54, color: "#FFFFFF", outline_col: "#000000", outline: 10, box: true, box_alpha: 0.35, y: 0.88 }, "sobre"),
  T("Journal", "fond blanc, texte sombre", { font: "Georgia", size: 70, color: "#17181C", outline_col: "#FFFFFF", outline: 12, box: true, box_alpha: 0.05, y: 0.2 }, "sobre"),
  T("Élégant", "serif, léger", B({ font: "DM Serif Display", size: 96, color: "#FFFFFF", outline_col: "#1A1410", outline: 2, shadow: 4, shadow_blur: 6, y: 0.4 }), "sobre"),
  T("Minimal", "fin, espacé", B({ font: "Lexend Bold", size: 72, color: "#FFFFFF", outline: 0, shadow: 4, shadow_blur: 8, spacing: 14, upper: true, y: 0.45 }), "sobre"),
  T("Contour seul", "lettres évidées", B({ font: "Bebas Neue", size: 170, color: "#FFFFFF", outline_col: "#FFFFFF", outline: 4, hollow: true, y: 0.4 }), "sobre"),
];

export function addText(style, text = "Ton texte") {
  const c = edit((doc) => {
    const start = M.r4(S.t), dur = M.TEXT_DUR;
    const tr = M.freeTrack(doc, "text", start, dur);
    const [esz, edy] = emojiGeometry(style.size);
    const clip = {
      id: M.uid("x"), track: tr.id, kind: "text", start, dur, auto: false,
      words: [{ text, start, end: start + dur }],
      font: "Arial", size: 90, bold: true, upper: false, color: "#FFFFFF", hl: "#FFFFFF",
      outline_col: "#000000", outline: 5, shadow: 0, box: false, box_alpha: 0.25,
      mode: "none", pop: false, x: 0.5, y: 0.5, emoji: "", emoji_size: esz, emoji_dx: 0, emoji_dy: edy,
      moved: true,
    };
    applyLook(clip, style);
    clip.mode = "none";
    clip.pop = false;
    clip.x = style.x ?? 0.5;
    clip.y = style.y ?? 0.5;
    doc.clips.push(clip);
    return clip;
  }, "text");
  select([c.id]);
  emit("editclip", { id: c.id, kind: "text" });
}

function buildText() {
  put($("tab-text"),
    h("button.btn.primary.wide", { html: svg("plus", 14) + "Ajouter un texte", onclick: () => addText(TITLE_STYLES[0]) }),
    h("div.hint", { style: { margin: "6px 0 12px" } }, "Posé à la tête de lecture. Double-clic sur l'aperçu pour l'écrire."),
    styleGrid(TITLE_STYLES, TITLE_GROUPS, { words: ["Ton", "texte"], pick: (st) => addText(st) }));
}

/* ============================================================== sous-titres */

function buildCaptions() {
  put($("tab-captions"),
    h("div", { id: "capGen" }),
    h("div", { id: "capLines" }),
    h("div", { id: "capTr" }));
  renderCapGen();
}

function renderCapGen() {
  const box = $("capGen");
  if (!box) return;
  const st = S.doc.settings || {};
  X.capStyle = X.capStyle || st.style || "hype";
  const setS = (k, v) => { S.doc.settings = { ...(S.doc.settings || {}), [k]: v }; changed({ reason: "settings" }); };
  const slider = (label, value, min, max, set) => {
    const input = h("input", { type: "range", min, max, step: 1, value });
    const out = h("b", {}, String(value));
    input.oninput = () => { out.textContent = input.value; };
    input.onchange = () => set(+input.value);
    return h("div.field", {}, h("div.head", {}, h("span.label", {}, label), out), input);
  };
  const emo = h("input", { type: "checkbox", checked: st.emojis !== false });
  emo.onchange = () => setS("emojis", emo.checked);
  const rep = h("input", { type: "checkbox", checked: X.replace });
  rep.onchange = () => { X.replace = rep.checked; };
  const wbw = h("input", { type: "checkbox", checked: !!st.word_by_word });
  wbw.onchange = () => { setS("word_by_word", wbw.checked); renderCapGen(); };
  const hasAuto = S.doc.clips.some((c) => c.kind === "text" && c.auto);
  const current = Insp.presets.find((p) => p.name === X.capStyle);
  box.innerHTML = "";
  put(box,
    h("button.btn.primary.wide", { id: "capGo", html: svg("cc", 14) + (hasAuto ? "Régénérer les sous-titres" : "Générer les sous-titres"),
                                   onclick: () => generateCaptions() }),
    h("div.hint", { id: "capMsg", style: { margin: "6px 0 8px" } }, "D'après la voix des clips. Ils suivent ensuite les coupes."),
    h("label.check.wbw", { title: "Un seul mot à l'écran à la fois, affiché jusqu'au mot suivant" }, wbw,
      h("span", {}, h("b", {}, "Mot à mot"), " — un seul mot à l'écran",
        hasAuto && !!st.word_by_word !== S.doc.clips.some((c) => c.kind === "text" && c.auto && c.hold)
          ? h("span.meta", {}, " (régénère pour l'appliquer)") : null)),
    section({ title: "Style", key: "cap.style", count: current ? current.label : undefined },
      styleGrid(Insp.presets, Insp.groups, {
        same: (p) => p.name === X.capStyle,
        pick: (p) => { X.capStyle = p.name; setS("style", p.name); renderCapGen(); },
      }),
      hasAuto ? h("button.btn.sm.wide", {
        style: { marginTop: "8px" }, html: svg("refresh", 12) + "Appliquer aux sous-titres existants",
        onclick: () => {
          const look = Insp.presets.find((p) => p.name === X.capStyle);
          if (!look) return;
          edit((doc) => doc.clips.forEach((c) => {
            if (c.kind !== "text" || !c.auto) return;
            applyLook(c, look);
            if (!c.moved) { c.x = look.x; c.y = look.y; }
          }), "style");
          toast(`Style « ${look.label} » appliqué.`);
        },
      }) : null),
    section({ title: "Réglages", key: "cap.settings", open: false },
      st.word_by_word ? null : slider("Mots par ligne", st.words_per_line || 4, 1, 8, (v) => setS("words_per_line", v)),
      slider("Caractères max par ligne", st.max_chars || 18, 8, 40, (v) => setS("max_chars", v)),
      h("label.check", { style: { marginBottom: "8px" } }, emo, "Émojis sur les mots importants"),
      hasAuto ? h("label.check", {}, rep, "Remplacer les sous-titres existants") : null));
  renderCapState();
}
on("doc", ({ reason }) => { if (reason === "load" || reason === "undo" || reason === "redo") renderCapGen(); });

function renderCapState() {
  // le bouton change de libellé quand des sous-titres existent déjà
  const go = $("capGo");
  if (!go) return;
  const hasAuto = S.doc.clips.some((c) => c.kind === "text" && c.auto);
  go.innerHTML = svg("cc", 14) + (hasAuto ? "Régénérer les sous-titres" : "Générer les sous-titres");
}

/** Génère les sous-titres des clips avec de la voix. `quiet` : sans message
 *  (montage automatique) ; `replace` force le remplacement. Renvoie le nombre
 *  de lignes créées. */
export async function generateCaptions({ quiet = false, replace } = {}) {
  if (X.busy) return 0;
  const voice = voiceClips(S.doc.clips);
  if (!voice.length) { if (!quiet) toast("Aucun clip avec de la voix sur la timeline."); return 0; }
  const btn = $("capGo"), msg = $("capMsg");
  const say = (t) => { msg.textContent = t; };
  const doReplace = replace === undefined ? X.replace : replace;
  X.busy = true;
  btn.disabled = true;
  try {
    const ok = await ensureTranscripts(voice.map((c) => c.media), say);
    if (!ok) return 0;
    say("Création des lignes…");
    const st = S.doc.settings || {};
    const res = await post(`/api/timeline/${S.pid}/captions`, {
      clips: voiceClips(S.doc.clips),
      settings: { style: X.capStyle, words_per_line: st.words_per_line, max_chars: st.max_chars, emojis: st.emojis,
                  word_by_word: !!st.word_by_word },
    });
    if (!res.captions.length) { if (!quiet) toast("Aucune parole trouvée dans les clips."); say(""); return 0; }
    const n = edit((doc) => {
      let tr = doc.tracks.find((t) => t.kind === "text" && t.name === "Sous-titres");
      if (tr && doReplace) doc.clips = doc.clips.filter((c) => !(c.track === tr.id && c.kind === "text" && c.auto));
      if (!tr) tr = M.addTrack(doc, "text", { name: "Sous-titres" });
      res.captions.forEach((c) => { c.track = tr.id; doc.clips.push(c); });
      M.reflowCaptions(doc);
      M.fixOverlaps(doc);
      return res.captions.length;
    }, "captions");
    X.translateOk = null;
    say(`${n} lignes créées, liées à la voix.`);
    if (!quiet) toast(`${n} sous-titres générés.`);
    renderCapList();
    return n;
  } catch (e) {
    toast("Sous-titres impossibles : " + e.message, 5000);
    say("");
    return 0;
  } finally {
    X.busy = false;
    btn.disabled = false;
  }
}

const textClips = () => S.doc.clips.filter((c) => c.kind === "text" && !c.gone).sort((a, b) => a.start - b.start);

function renderCapList() {
  const box = $("capLines");
  if (!box) return;
  const caps = textClips();
  box.innerHTML = "";
  if (!caps.length) return;
  const rows = h("div", {});
  caps.forEach((c) => {
    const row = h("div.crow" + (S.sel.has(c.id) ? ".sel" : "") + (c.hidden ? ".off" : ""), {
      onclick: (e) => {
        if (e.ctrlKey || e.metaKey) { S.sel.has(c.id) ? S.sel.delete(c.id) : S.sel.add(c.id); emit("select"); return; }
        select([c.id]);
        setTime(c.start + 0.01, { from: "list" });
      },
      ondblclick: () => emit("editclip", { id: c.id, kind: "text" }),
    },
      h("span.tm.num", {}, fmt(c.start)),
      h("span.tx", {}, M.liveWords(c).map((w) => w.text).join(" ")),
      c.lang ? h("span.badge", {}, c.lang.toUpperCase()) : null,
      c.emoji ? h("span", {}, c.emoji) : null);
    rows.appendChild(row);
  });
  const tools = h("span.row", { style: { gap: "2px" }, onclick: (e) => e.stopPropagation() },
    h("button.btn.sm.icon.quiet", { title: "Fusionner les lignes sélectionnées (qui se suivent)", html: svg("link", 13), onclick: mergeSelected }),
    h("button.btn.sm.icon.quiet", { title: "Masquer / réafficher la sélection", html: svg("eye", 13), onclick: toggleHideSelected }));
  put(box, section({ title: "Lignes", key: "cap.lines", count: caps.length, extra: tools }, rows));
  renderTranslate();
}

function selectedTexts() {
  return textClips().filter((c) => S.sel.has(c.id));
}

function mergeSelected() {
  const g = selectedTexts();
  if (g.length < 2) {
    // une seule ligne : on la fusionne avec la suivante de la même piste
    const one = g[0];
    if (!one) { toast("Sélectionne des lignes à fusionner."); return; }
    const next = textClips().find((c) => c.track === one.track && c.start >= M.clipEnd(one) - 1e-3 && c.id !== one.id);
    if (!next) { toast("Pas de ligne suivante."); return; }
    g.push(next);
  }
  if (new Set(g.map((c) => c.track)).size > 1) { toast("Les lignes doivent être sur la même piste."); return; }
  edit((doc) => {
    const clips = g.map((x) => doc.clips.find((c) => c.id === x.id)).sort((a, b) => a.start - b.start);
    const first = clips[0], last = clips[clips.length - 1];
    first.words = clips.flatMap((c) => c.words || []);
    if (clips.some((c) => c.src_words)) first.src_words = clips.flatMap((c) => c.src_words || c.words || []);
    first.dur = M.r4(M.clipEnd(last) - first.start);
    first.emoji = (clips.find((c) => c.emoji) || {}).emoji || "";
    first.auto = clips.every((c) => c.auto);
    const drop = new Set(clips.slice(1).map((c) => c.id));
    // les lignes du milieu sur la même piste disparaissent aussi (plus de chevauchement)
    doc.clips = doc.clips.filter((c) => !drop.has(c.id) &&
      !(c.kind === "text" && c.track === first.track && c.id !== first.id && c.start > first.start && c.start < M.clipEnd(first)));
    M.reflowCaptions(doc);
  }, "merge");
  select([g[0].id]);
}

function toggleHideSelected() {
  const g = selectedTexts();
  if (!g.length) { toast("Sélectionne des lignes."); return; }
  const hide = !g.every((c) => c.hidden);
  edit((doc) => doc.clips.forEach((c) => { if (g.some((x) => x.id === c.id)) c.hidden = hide; }), "hide");
}

/* ------------------------------------------------------------ traduction */

async function renderTranslate() {
  const box = $("capTr");
  if (!box) return;
  const caps = textClips();
  if (!caps.length) { box.innerHTML = ""; return; }
  if (X.translateOk === null) {
    X.translateOk = "pending";
    try { X.translateOk = await api(`/api/timeline/${S.pid}/translate/available?target=en`); }
    catch (e) { X.translateOk = { available: false, source: "?" }; }
  }
  if (X.translateOk === "pending") return;
  const info = X.translateOk;
  const target = selectedTexts().length ? selectedTexts() : caps;
  box.innerHTML = "";
  put(box, section({ title: "Traduction", key: "cap.tr", open: false },
    h("div.actions", {},
      h("button.btn.sm", { disabled: !info.available || info.source === "en", html: svg("refresh", 13) + "En anglais",
                           onclick: () => translate(target) }),
      h("button.btn.sm", { disabled: !target.some((c) => c.src_words), html: svg("undo", 13) + "Texte original",
                           onclick: () => restore(target) })),
    h("div.hint", { style: { marginTop: "6px" } }, info.source === "en" ? "Déjà en anglais."
      : !info.available ? `Modèle ${info.source} → en absent (python scripts/download_models.py).`
      : `${selectedTexts().length ? "La sélection" : "Toutes les lignes"}, sur ton PC.`)));
}

async function translate(list) {
  const todo = list.filter((c) => !c.hidden);
  if (!todo.length) return;
  try {
    const res = await post(`/api/timeline/${S.pid}/translate`, { target: "en", captions: todo });
    const byId = new Map(res.captions.map((c) => [c.id, c]));
    edit((doc) => {
      doc.clips = doc.clips.map((c) => {
        const t = byId.get(c.id);
        return t ? { ...c, words: t.words, src_words: t.src_words, lang: t.lang || "en", hidden: !!t.hidden,
                     tr_hidden: !!t.tr_hidden } : c;
      });
    }, "translate");
    toast(`${byId.size} ligne${byId.size > 1 ? "s" : ""} traduite${byId.size > 1 ? "s" : ""} en anglais.`);
  } catch (e) { toast("Traduction impossible : " + e.message, 5000); }
}

function restore(list) {
  const ids = new Set(list.filter((c) => c.src_words).map((c) => c.id));
  if (!ids.size) return;
  edit((doc) => doc.clips.forEach((c) => {
    if (!ids.has(c.id)) return;
    c.words = c.src_words;
    delete c.src_words;
    delete c.lang;
    M.reflowCaptions(doc);
  }), "translate");
  toast("Texte original rétabli.");
}

/* ================================================================ outils IA */

function buildAuto() {
  put($("tab-auto"),
    h("div", { id: "autoAI" }),
    h("div", { id: "autoSil" }), h("div", { id: "autoPass" }), h("div", { id: "autoTr" }));
  renderAuto();
}

function renderAuto() {
  autoedit.render();
  renderSilence();
  renderPassages();
  renderTranscripts();
}

/* ------------------------------------------------- passages supprimés */

function passageLabel(p) {
  return `${fmt(p.t)} · −${sec(p.dur)}`;
}

/** Liste des passages retirés, chacun restaurable (et « tout restaurer »). */
function renderPassages() {
  const box = $("autoPass");
  if (!box) return;
  const list = M.removedPassages(S.doc);
  box.innerHTML = "";
  if (!list.length) return;
  const total = list.reduce((a, p) => a + p.dur, 0);
  const rows = h("div", { style: { maxHeight: "220px", overflow: "auto" } });
  list.forEach((p) => {
    const said = h("span.meta.ell", { style: { flex: 1, minWidth: 0 } }, "");
    textBetween(p.media, p.s, p.e).then((t) => { said.textContent = t ? "« " + t + " »" : "blanc"; });
    rows.appendChild(h("div.row", { style: { padding: "3px 0", cursor: "pointer" },
                                    onclick: () => setTime(Math.max(0, p.t - 1), { from: "list" }) },
      h("span.num", { style: { fontSize: "12px", minWidth: "88px", color: "var(--accent)" } }, passageLabel(p)),
      said,
      h("button.btn.sm.icon.quiet", { title: "Remettre ce passage dans le montage", html: svg("undo", 12),
        onclick: (e) => { e.stopPropagation(); restorePassage(p); } })));
  });
  const all = h("button.btn.sm.quiet", { title: "Tout restaurer", html: svg("undo", 12) + "Tout",
    onclick: (e) => {
      e.stopPropagation();
      const n = edit((doc) => { const r = M.restoreAll(doc); M.reflowCaptions(doc); return r; }, "restore");
      toast(`Tous les passages sont revenus (+${sec(n)}).`);
    } });
  put(box, section({ title: "Passages supprimés", icon: "cut", key: "ai.passages", count: `−${sec(total)}`, extra: all },
    rows, h("div.hint", { style: { marginTop: "6px" } }, "Aussi sur la timeline : les repères rouges.")));
}

function restorePassage(p) {
  const g = edit((doc) => { const r = M.restoreGap(doc, p.id, p.side); M.reflowCaptions(doc); return r; }, "restore");
  closePassage();
  toast(`Passage restauré (+${sec(g)}).`);
}

/** Fenêtre ouverte sur un repère de coupe : durée, paroles coupées, restaurer. */
async function openPassage(p, x, y) {
  closePassage();
  const said = h("div.said", {}, "…");
  const pop = h("div.cutpop", { id: "cutpop" },
    h("div.ttl", { html: svg("cut", 13) + `<span>−${sec(p.dur)} retirés ici</span>` }),
    h("div.meta", { style: { marginTop: "4px" } }, `Dans la source : ${fmt(p.s)} → ${fmt(p.e)}`),
    said,
    h("div.actions", { style: { marginTop: "10px" } },
      h("button.btn.sm", { onclick: () => { setTime(Math.max(0, p.t - 1.5), { from: "list" }); closePassage(); } },
        "Écouter la coupe"),
      h("button.btn.sm.primary", { onclick: () => restorePassage(p) }, "Restaurer")));
  document.body.appendChild(pop);
  const r = pop.getBoundingClientRect();
  pop.style.left = clamp(x - r.width / 2, 8, innerWidth - r.width - 8) + "px";
  pop.style.top = Math.max(8, y - r.height - 10) + "px";
  setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
  const t = await textBetween(p.media, p.s, p.e);
  said.textContent = t ? "« " + t + " »" : "Silence (aucune parole)";
}
function outside(e) { if (!e.target.closest("#cutpop")) closePassage(); }
function closePassage() {
  document.removeEventListener("pointerdown", outside, true);
  const el = document.getElementById("cutpop");
  if (el) el.remove();
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePassage(); });

function renderSilence() {
  const box = $("autoSil");
  if (!box) return;
  const st = S.doc.settings || {};
  const setS = (k, v) => { S.doc.settings = { ...(S.doc.settings || {}), [k]: v }; changed({ reason: "settings" }); };
  const slider = (label, value, min, max, step, show, set) => {
    const input = h("input", { type: "range", min, max, step, value });
    const out = h("b", {}, show(value));
    input.oninput = () => { out.textContent = show(+input.value); };
    input.onchange = () => { set(+input.value); clearPreview(); };
    return h("div.field", {}, h("div.head", {}, h("span.label", {}, label), out), input);
  };
  const fill = h("input", { type: "checkbox", checked: !!st.fillers });
  fill.onchange = () => { setS("fillers", fill.checked); clearPreview(); };
  box.innerHTML = "";
  put(box, section({ title: "Supprimer les blancs", icon: "silence", key: "ai.silence" },
    h("div.field", {}, h("div.seg", {}, [["selection", "Sélection"], ["main", "Piste principale"], ["all", "Tout"]].map(([k, l]) =>
      h("button", { class: X.scope === k ? "on" : "", onclick: () => { X.scope = k; clearPreview(); renderSilence(); } }, l)))),
    h("div.field", {}, h("div.seg", {}, [["voice", "Par la voix"], ["volume", "Par le volume"]].map(([k, l]) =>
      h("button", { class: X.method === k ? "on" : "", onclick: () => { X.method = k; clearPreview(); renderSilence(); } }, l)))),
    X.method === "voice" ? h("div", {},
      slider("Blanc toléré entre deux mots", st.max_gap ?? 0.5, 0.1, 2, 0.05, (v) => sec(v), (v) => setS("max_gap", v)),
      slider("Marge autour des mots", st.pad ?? 0.08, 0, 0.4, 0.01, (v) => Math.round(v * 1000) + " ms", (v) => setS("pad", v)),
      h("label.check", { style: { marginBottom: "10px" } }, fill, "Couper aussi les tics (euh, du coup…)"))
    : h("div", {},
      slider("Seuil de silence", X.noise, -60, -15, 1, (v) => v + " dB", (v) => { X.noise = v; }),
      slider("Silence minimal", X.minSil, 0.1, 2, 0.05, (v) => sec(v), (v) => { X.minSil = v; })),
    h("div.actions", {},
      h("button.btn", { id: "silPreview", html: svg("eye", 13) + "Aperçu", onclick: () => previewCuts() }),
      h("button.btn.primary", { id: "silApply", html: svg("cut", 13) + "Appliquer", onclick: () => applyCutsNow() })),
    h("div.meta", { id: "silMsg", style: { marginTop: "8px" } }, X.preview ? previewText() : "")));
}

/** Un clip par groupe lié : celui de la piste principale, sinon la vidéo. */
export function leads(clips) {
  const out = [], seen = new Set();
  const main = M.mainTrack(S.doc);
  for (const c of clips) {
    if (c.kind !== "video" && c.kind !== "audio") continue;
    if (!(S.media.get(c.media) || {}).has_audio) continue;
    const group = c.link ? S.doc.clips.filter((x) => x.link === c.link) : [c];
    const key = c.link || c.id;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(group.find((x) => x.track === main.id) || group.find((x) => x.kind === "video") || c);
  }
  return out;
}

function targets() {
  const main = M.mainTrack(S.doc);
  if (X.scope === "selection") return leads(S.doc.clips.filter((c) => S.sel.has(c.id)));
  if (X.scope === "main") return leads(S.doc.clips.filter((c) => c.track === main.id));
  return leads(S.doc.clips);
}

async function computeCuts() {
  const list = targets();
  if (!list.length) {
    toast(X.scope === "selection" ? "Sélectionne des clips avec du son." : "Aucun clip avec du son ici.");
    return null;
  }
  const say = (t) => { const m = $("silMsg"); if (m) m.textContent = t; };
  const st = S.doc.settings || {};
  if (X.method === "voice") {
    if (!(await ensureTranscripts(list.map((c) => c.media), say))) return null;
  }
  say("Analyse…");
  const out = [];
  for (const c of list) {
    let cuts;
    if (X.method === "voice") {
      cuts = M.clipCuts(c, { words: await wordsOf(c.media), maxGap: st.max_gap ?? 0.5, pad: st.pad ?? 0.08, tail: 0.1,
                             fillers: !!st.fillers });
    } else {
      const res = await api(`/api/timeline/${S.pid}/media/${c.media}/silences?noise=${X.noise}&min=${X.minSil}`);
      cuts = M.clipCuts(c, { silences: res.silences, pad: 0.05 });
    }
    if (cuts.length) out.push({ id: c.id, cuts });
  }
  return out;
}

function previewText() {
  const clips = new Map(S.doc.clips.map((c) => [c.id, c]));
  let total = 0, n = 0;
  X.preview.forEach(({ id, cuts }) => {
    const c = clips.get(id);
    if (!c) return;
    cuts.forEach(([a, b]) => { total += (b - a) / (c.speed || 1); n++; });
  });
  return n ? `${n} passage${n > 1 ? "s" : ""} à retirer · −${sec(total)} (en rouge sur la timeline)`
           : "Rien à retirer avec ces réglages.";
}

async function previewCuts() {
  if (X.busy) return;
  X.busy = true;
  try {
    const res = await computeCuts();
    if (!res) return;
    X.preview = res;
    const ranges = [];
    res.forEach(({ id, cuts }) => {
      const c = S.doc.clips.find((x) => x.id === id);
      if (!c) return;
      const group = [c, ...M.partners(S.doc, c)];
      cuts.forEach(([a, b]) => {
        const ta = c.start + (a - c.in) / (c.speed || 1), tb = c.start + (b - c.in) / (c.speed || 1);
        group.forEach((g) => ranges.push({ tid: g.track, a: ta, b: tb }));
      });
    });
    emit("cutpreview", { ranges });
    const m = $("silMsg");
    if (m) m.textContent = previewText();
  } catch (e) {
    toast("Analyse impossible : " + e.message, 5000);
  } finally { X.busy = false; }
}

function clearPreview() {
  if (!X.preview) return;
  X.preview = null;
  emit("cutpreview", { ranges: [] });
  const m = $("silMsg");
  if (m) m.textContent = "";
}

async function applyCutsNow() {
  if (X.busy) return;
  let plan = X.preview;
  if (!plan) {
    X.busy = true;
    try { plan = await computeCuts(); } catch (e) { toast("Analyse impossible : " + e.message, 5000); }
    X.busy = false;
    if (!plan) return;
  }
  if (!plan.length) { toast("Rien à retirer avec ces réglages."); return; }
  const removed = edit((doc) => {
    let total = 0;
    // du plus tard au plus tôt : les coupes ne décalent pas les clips pas encore traités
    const order = plan.map((p) => ({ ...p, c: doc.clips.find((x) => x.id === p.id) }))
      .filter((p) => p.c).sort((a, b) => b.c.start - a.c.start);
    for (const p of order) total += M.applyCuts(doc, p.c, p.cuts).removed;
    M.reflowCaptions(doc);
    return total;
  }, "silence");
  X.preview = null;
  emit("cutpreview", { ranges: [] });
  toast(`Blancs supprimés : −${sec(removed)}. Chaque coupe a son repère rouge sur la timeline (clic pour restaurer).`, 5000);
  const m = $("silMsg");
  if (m) m.textContent = `−${sec(removed)} retirés.`;
}

/* --------------------------------------------------- panneau transcription */

function renderTranscripts() {
  const box = $("autoTr");
  if (!box) return;
  const st = S.doc.settings || {};
  const list = [...S.media.values()].filter((m) => m.status === "ready" && m.has_audio);
  const setS = (k, v) => { S.doc.settings = { ...(S.doc.settings || {}), [k]: v }; changed({ reason: "settings" }); };
  const lang = h("select", {}, [["", "Détectée"], ["fr", "Français"], ["en", "Anglais"], ["es", "Espagnol"],
                                ["de", "Allemand"], ["it", "Italien"], ["pt", "Portugais"]].map(([v, l]) =>
    h("option", { value: v, selected: (st.language || "") === v }, l)));
  lang.onchange = () => setS("language", lang.value || null);
  const model = h("select", {}, [["large-v3-turbo", "Précis (large-v3-turbo)"], ["medium", "Moyen"], ["small", "Rapide (small)"]]
    .map(([v, l]) => h("option", { value: v, selected: (st.model || "large-v3-turbo") === v }, l)));
  model.onchange = () => setS("model", model.value);
  const done = list.filter((m) => (m.transcript || {}).status === "done").length;
  const todo = list.filter((m) => (m.transcript || {}).status !== "done");
  box.innerHTML = "";
  put(box, section({ title: "Transcription", icon: "cc", key: "ai.transcribe", open: false,
                     count: list.length ? `${done}/${list.length}` : undefined },
    h("div.g2", {}, h("div.field", {}, h("div.head", {}, h("span.label", {}, "Langue")), lang),
                    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Modèle")), model)),
    list.length ? h("div", {}, list.map((m) => {
      const tr = m.transcript || {};
      const label = { done: `${tr.count || 0} mots${tr.language ? " · " + tr.language : ""}`, running: "en cours…",
                      queued: "en attente…", error: "erreur", none: "à faire" }[tr.status || "none"];
      return h("div.kv", { title: tr.error || "" },
        h("span.ell", { style: { maxWidth: "150px" } }, m.name),
        h("span.row", { style: { gap: "2px" } }, h("b", { class: tr.status === "error" ? "err" : "" }, label),
          tr.status === "running" || tr.status === "queued" ? null :
            h("button.btn.sm.icon.quiet", { title: tr.status === "done" ? "Retranscrire" : "Transcrire",
              html: svg(tr.status === "done" ? "refresh" : "cc", 13),
              onclick: () => transcribe([m.id], tr.status === "done") })));
    })) : h("div.hint", {}, "Aucun média avec du son."),
    todo.length ? h("button.btn.sm.wide", { style: { marginTop: "8px" }, html: svg("cc", 13) + "Tout transcrire",
      onclick: () => transcribe(todo.map((m) => m.id)) }) : null,
    h("div.hint", { style: { marginTop: "8px" } }, "Whisper, sur ton PC. Le modèle se télécharge une fois (~1,6 Go).")));
}
