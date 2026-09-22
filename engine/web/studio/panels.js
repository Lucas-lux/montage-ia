/* Panneaux de gauche : Texte, Sous-titres, Outils IA.

   - Texte : ajoute des titres (texte libre) à la tête de lecture.
   - Sous-titres : génère des sous-titres liés à la voix depuis les clips de la
     timeline, liste les lignes, fusionne, masque, traduit.
   - Outils IA : transcription des médias, suppression des blancs (par la voix
     ou par le volume) et des tics de langage, avec aperçu avant d'appliquer. */

import { api, post } from "./api.js";
import { startPolling } from "./bin.js";
import { emojiGeometry, stylePreview } from "./captions.js";
import { I as Insp, applyLook } from "./inspector.js";
import { showTab } from "./main.js";
import * as M from "./model.js";
import { S, changed, edit, emit, on, select, setMedia, setTime } from "./store.js";
import { $, clamp, fmt, h, put, sec, svg, toast } from "./util.js";
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
  // Arrivée depuis « Short automatique » : la première vidéo prête lance tout.
  if (new URLSearchParams(location.hash.slice(1)).get("auto") === "1") {
    X.autoArmed = true;
    showTab("media");
    toast("Short automatique : importe ta vidéo, les coupes et les sous-titres se font tout seuls.", 6000);
    on("media", armedStart);
    armedStart();
  }
}

/* ======================================================== short automatique */

function armedStart() {
  if (!X.autoArmed || X.busy) return;
  const ready = [...S.media.values()].find((m) => m.status === "ready" && m.kind === "video");
  if (!ready) return;
  X.autoArmed = false;
  history.replaceState(null, "", "#p=" + S.pid);
  if (!S.doc.clips.some((c) => c.kind === "video")) {
    edit((doc) => {
      M.appendMedia(doc, ready, 0);
      // un short sans nom prend celui de sa vidéo
      if (!doc.name || doc.name === "Nouveau montage") doc.name = ready.name.replace(/\.[^.]+$/, "");
    }, "add");
    const input = document.getElementById("name");
    if (input) input.value = S.doc.name;
  }
  autoShort();
}

/** Coupe les blancs de la piste principale puis génère les sous-titres. */
export async function autoShort() {
  if (X.busy) return;
  if (!S.doc.clips.some((c) => c.kind === "video" || c.kind === "audio")) {
    toast("Ajoute d'abord une vidéo à la timeline.");
    return;
  }
  const veil = h("div.busyveil", {}, h("div.box", {},
    h("div", { style: { fontWeight: 600, marginBottom: "6px" } }, "Short automatique"),
    h("div.meta", { id: "autoMsg" }, "Préparation…"),
    h("div.track-bar", {}, h("i", { id: "autoBar", style: { width: "10%" } }))));
  document.body.appendChild(veil);
  const say = (t, pct) => {
    const m = document.getElementById("autoMsg");
    if (m) m.textContent = t;
    if (pct !== undefined) document.getElementById("autoBar").style.width = pct + "%";
  };
  const old = { scope: X.scope, method: X.method };
  try {
    X.scope = "main";
    X.method = "voice";
    const ids = S.doc.clips.filter((c) => c.kind === "video" || c.kind === "audio").map((c) => c.media);
    say("Transcription de la voix (Whisper, sur ton PC)…", 20);
    if (!(await ensureTranscripts(ids, (t) => say(t, 35)))) return;
    say("Suppression des blancs…", 60);
    const plan = await computeCuts();
    if (plan && plan.length) {
      edit((doc) => {
        const order = plan.map((p) => ({ ...p, c: doc.clips.find((x) => x.id === p.id) }))
          .filter((p) => p.c).sort((a, b) => b.c.start - a.c.start);
        for (const p of order) M.applyCuts(doc, p.c, p.cuts);
        M.reflowCaptions(doc);
      }, "silence");
    }
    say("Sous-titres…", 80);
    await generateCaptions();
    say("Terminé", 100);
    toast("Short prêt : blancs coupés, sous-titres posés. Tout se retouche dans la timeline.", 6000);
  } finally {
    Object.assign(X, old);
    veil.remove();
    renderSilence();
  }
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
async function ensureTranscripts(ids, status) {
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

const TITLE_STYLES = [
  { label: "Titre", hint: "blanc, gras, contour", font: "Arial Black", size: 110, color: "#FFFFFF", hl: "#FFFFFF",
    outline_col: "#000000", outline: 6, shadow: 2, box: false, upper: false, y: 0.3 },
  { label: "Bandeau", hint: "texte sur fond", font: "Arial", size: 72, color: "#FFFFFF", hl: "#FFFFFF",
    outline_col: "#F23A52", outline: 14, shadow: 0, box: true, box_alpha: 0, upper: true, y: 0.72 },
  { label: "Impact", hint: "énorme, majuscules", font: "Impact", size: 150, color: "#FFE500", hl: "#FFE500",
    outline_col: "#000000", outline: 9, shadow: 0, box: false, upper: true, y: 0.5 },
  { label: "Néon", hint: "cyan lumineux", font: "Arial", size: 96, color: "#00FFFF", hl: "#00FFFF",
    outline_col: "#FF00AA", outline: 4, shadow: 6, box: false, upper: false, y: 0.4 },
  { label: "Légende", hint: "sobre, petit", font: "Segoe UI", size: 54, color: "#FFFFFF", hl: "#FFFFFF",
    outline_col: "#000000", outline: 10, shadow: 0, box: true, box_alpha: 0.35, upper: false, y: 0.88 },
  { label: "Machine à écrire", hint: "Georgia, fond clair", font: "Georgia", size: 70, color: "#17181C", hl: "#17181C",
    outline_col: "#FFFFFF", outline: 12, shadow: 0, box: true, box_alpha: 0.05, upper: false, y: 0.2 },
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
    clip.y = style.y ?? 0.5;
    doc.clips.push(clip);
    return clip;
  }, "text");
  select([c.id]);
  emit("editclip", { id: c.id, kind: "text" });
}

function buildText() {
  const box = $("tab-text");
  put(box, 
    h("button.btn.primary.wide", { html: svg("plus", 14) + "Ajouter un texte", onclick: () => addText(TITLE_STYLES[0]) }),
    h("div.meta", { style: { margin: "8px 0 12px" } },
      "Posé à la tête de lecture pour 3 s, sur une piste texte. Double-clic sur l'aperçu pour l'écrire, coin pour l'agrandir."),
    h("div.label", { style: { marginBottom: "8px" } }, "Styles"),
    h("div", { style: { display: "grid", gap: "6px" } }, TITLE_STYLES.map((st) => h("button.tcstyle", {
      onclick: () => addText(st),
      style: { display: "flex", alignItems: "center", gap: "10px", padding: "9px 10px", borderRadius: "8px",
               background: "var(--panel-3)", textAlign: "left" },
    },
      h("span", { style: {
        width: "64px", height: "40px", borderRadius: "6px", display: "grid", placeItems: "center", flex: "0 0 auto",
        background: st.box ? st.outline_col : "#0B0C0E", color: st.color, fontFamily: `"${st.font}", Arial`,
        fontWeight: 800, fontSize: "15px", textTransform: st.upper ? "uppercase" : "none",
        WebkitTextStroke: st.box ? "" : `1px ${st.outline_col}`, textShadow: st.shadow ? `0 0 6px ${st.outline_col}` : "",
      } }, "Aa"),
      h("span.grow", {}, h("b", { style: { display: "block", fontWeight: 600 } }, st.label), h("span.meta", {}, st.hint))))));
}

/* ============================================================== sous-titres */

function buildCaptions() {
  const box = $("tab-captions");
  put(box, 
    h("div", { id: "capGen" }),
    h("div.sep"),
    h("div.row", { style: { marginBottom: "8px" } },
      h("span.label.grow", { id: "capCount" }, "Lignes"),
      h("button.btn.sm.quiet", { id: "capMerge", title: "Fusionner les lignes sélectionnées (qui se suivent)",
                                 html: svg("link", 13) + "Fusionner", onclick: mergeSelected }),
      h("button.btn.sm.quiet", { id: "capHide", title: "Masquer / réafficher la sélection", html: svg("eye", 13),
                                 onclick: toggleHideSelected })),
    h("div", { id: "capList" }),
    h("div.sep"),
    h("div", { id: "capTr" }));
  renderCapGen();
}

function renderCapGen() {
  const box = $("capGen");
  if (!box) return;
  const st = S.doc.settings || {};
  X.capStyle = X.capStyle || st.style || "hype";
  const setS = (k, v) => { S.doc.settings = { ...(S.doc.settings || {}), [k]: v }; changed({ reason: "settings" }); };
  const wpl = h("input", { type: "range", min: 1, max: 8, step: 1, value: st.words_per_line || 4 });
  const wplV = h("b", {}, String(st.words_per_line || 4));
  wpl.oninput = () => { wplV.textContent = wpl.value; };
  wpl.onchange = () => setS("words_per_line", +wpl.value);
  const mc = h("input", { type: "range", min: 8, max: 40, step: 1, value: st.max_chars || 18 });
  const mcV = h("b", {}, String(st.max_chars || 18));
  mc.oninput = () => { mcV.textContent = mc.value; };
  mc.onchange = () => setS("max_chars", +mc.value);
  const emo = h("input", { type: "checkbox", checked: st.emojis !== false });
  emo.onchange = () => setS("emojis", emo.checked);
  const rep = h("input", { type: "checkbox", checked: X.replace });
  rep.onchange = () => { X.replace = rep.checked; };
  box.innerHTML = "";
  put(box, 
    h("div.label", { style: { marginBottom: "8px" } }, "Style"),
    h("div.stylegrid", { id: "capStyles", style: { marginBottom: "12px" } },
      Insp.presets.map((p) => h("button.stylecard" + (p.name === X.capStyle ? ".on" : ""), {
        title: p.hint || "", onclick: () => { X.capStyle = p.name; setS("style", p.name); renderCapGen(); },
      }, stylePreview(p), h("span.sn", {}, h("b", {}, p.label), h("span.meta", {}, p.hint || ""))))),
    X.capStyle && S.doc.clips.some((c) => c.kind === "text" && c.auto) ? h("button.btn.sm.wide", {
      style: { marginBottom: "12px" }, html: svg("refresh", 12) + "Appliquer ce style aux sous-titres existants",
      onclick: () => {
        const look = Insp.presets.find((p) => p.name === X.capStyle);
        if (!look) return;
        edit((doc) => doc.clips.forEach((c) => {
          if (c.kind !== "text" || !c.auto) return;
          applyLook(c, look);
          if (!c.moved) { c.x = look.x; c.y = look.y; }
        }), "style");
        toast(`Style « ${look.label} » appliqué aux sous-titres.`);
      },
    }) : null,
    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Mots par ligne"), wplV), wpl),
    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Caractères max par ligne"), mcV), mc),
    h("label.check", { style: { marginBottom: "8px" } }, emo, "Émojis sur les mots importants"),
    h("label.check", { id: "capReplaceRow", style: { marginBottom: "10px" } }, rep, "Remplacer les sous-titres automatiques existants"),
    h("button.btn.primary.wide", { id: "capGo", html: svg("cc", 14) + "Générer les sous-titres", onclick: generateCaptions }),
    h("div.meta", { id: "capMsg", style: { marginTop: "8px" } },
      "D'après la voix des clips de la timeline. Les sous-titres suivent ensuite les coupes et déplacements."));
  renderCapState();
}
on("doc", ({ reason }) => { if (reason === "load" || reason === "undo" || reason === "redo") renderCapGen(); });

function renderCapState() {
  const row = $("capReplaceRow");
  if (!row) return;
  row.classList.toggle("hidden", !S.doc.clips.some((c) => c.kind === "text" && c.auto));
}

async function generateCaptions() {
  if (X.busy) return;
  const voice = voiceClips(S.doc.clips);
  if (!voice.length) { toast("Aucun clip avec de la voix sur la timeline."); return; }
  const btn = $("capGo"), msg = $("capMsg");
  const say = (t) => { msg.textContent = t; };
  X.busy = true;
  btn.disabled = true;
  try {
    const ok = await ensureTranscripts(voice.map((c) => c.media), say);
    if (!ok) return;
    say("Création des lignes…");
    const st = S.doc.settings || {};
    const res = await post(`/api/timeline/${S.pid}/captions`, {
      clips: voiceClips(S.doc.clips),
      settings: { style: X.capStyle, words_per_line: st.words_per_line, max_chars: st.max_chars, emojis: st.emojis },
    });
    if (!res.captions.length) { toast("Aucune parole trouvée dans les clips."); say(""); return; }
    const n = edit((doc) => {
      let tr = doc.tracks.find((t) => t.kind === "text" && t.name === "Sous-titres");
      if (tr && X.replace) doc.clips = doc.clips.filter((c) => !(c.track === tr.id && c.kind === "text" && c.auto));
      if (!tr) tr = M.addTrack(doc, "text", { name: "Sous-titres" });
      res.captions.forEach((c) => { c.track = tr.id; doc.clips.push(c); });
      M.reflowCaptions(doc);
      M.fixOverlaps(doc);
      return res.captions.length;
    }, "captions");
    X.translateOk = null;
    say(`${n} lignes créées, liées à la voix.`);
    toast(`${n} sous-titres générés.`);
    renderCapList();
  } catch (e) {
    toast("Sous-titres impossibles : " + e.message, 5000);
    say("");
  } finally {
    X.busy = false;
    btn.disabled = false;
  }
}

const textClips = () => S.doc.clips.filter((c) => c.kind === "text" && !c.gone).sort((a, b) => a.start - b.start);

function renderCapList() {
  const list = $("capList");
  if (!list) return;
  const caps = textClips();
  $("capCount").textContent = caps.length ? `${caps.length} ligne${caps.length > 1 ? "s" : ""}` : "Aucune ligne";
  list.innerHTML = "";
  if (!caps.length) {
    list.appendChild(h("div.meta", {}, "Les sous-titres générés et les textes apparaîtront ici."));
  }
  const frag = document.createDocumentFragment();
  caps.forEach((c) => {
    const row = h("div.crow" + (S.sel.has(c.id) ? ".sel" : "") + (c.hidden ? ".off" : ""), {
      style: { display: "flex", gap: "8px", padding: "6px 7px", borderRadius: "6px", cursor: "pointer",
               border: S.sel.has(c.id) ? "1px solid var(--ink-3)" : "1px solid transparent",
               opacity: c.hidden ? 0.5 : 1, background: S.sel.has(c.id) ? "var(--panel-3)" : "" },
      onclick: (e) => {
        if (e.ctrlKey || e.metaKey) { S.sel.has(c.id) ? S.sel.delete(c.id) : S.sel.add(c.id); emit("select"); return; }
        select([c.id]);
        setTime(c.start + 0.01, { from: "list" });
      },
      ondblclick: () => emit("editclip", { id: c.id, kind: "text" }),
    },
      h("span.meta.num", { style: { minWidth: "38px" } }, fmt(c.start)),
      h("span.grow", { style: { fontSize: "12.5px", wordBreak: "break-word",
                                textDecoration: c.hidden ? "line-through" : "" } },
        M.liveWords(c).map((w) => w.text).join(" ")),
      c.lang ? h("span.badge", {}, c.lang.toUpperCase()) : null,
      c.emoji ? h("span", {}, c.emoji) : null);
    frag.appendChild(row);
  });
  list.appendChild(frag);
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
  put(box, 
    h("div.label", { style: { marginBottom: "6px" } }, "Langue"),
    h("div.actions", {},
      h("button.btn.sm", { disabled: !info.available || info.source === "en", html: svg("refresh", 13) + "Traduire en anglais",
                           onclick: () => translate(target) }),
      h("button.btn.sm", { disabled: !target.some((c) => c.src_words), html: svg("undo", 13) + "Texte original",
                           onclick: () => restore(target) })),
    h("div.meta", { style: { marginTop: "6px" } }, info.source === "en" ? "Déjà en anglais."
      : !info.available ? `Modèle de traduction ${info.source} → en absent (python scripts/download_models.py).`
      : `${selectedTexts().length ? "Sélection" : "Toutes les lignes"} · sur ton PC, sans internet.`));
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
  $("tab-auto").append(
    h("div", {},
      h("button.btn.primary.wide", { html: svg("wand", 14) + "Short automatique en un clic", onclick: autoShort }),
      h("div.meta", { style: { marginTop: "6px" } },
        "Coupe les blancs de la piste principale puis pose les sous-titres. Chaque étape reste annulable (Ctrl+Z).")),
    h("div.sep"), h("div", { id: "autoSil" }), h("div", { id: "autoPass" }), h("div.sep"), h("div", { id: "autoTr" }));
  renderAuto();
}

function renderAuto() {
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
  const rows = h("div", { style: { maxHeight: "220px", overflow: "auto", margin: "6px 0 8px" } });
  list.forEach((p) => {
    const said = h("span.meta.ell", { style: { flex: 1, minWidth: 0 } }, "");
    textBetween(p.media, p.s, p.e).then((t) => { said.textContent = t ? "« " + t + " »" : "blanc"; });
    rows.appendChild(h("div.row", { style: { padding: "3px 0", cursor: "pointer" },
                                    onclick: () => setTime(Math.max(0, p.t - 1), { from: "list" }) },
      h("span.num", { style: { fontSize: "12px", minWidth: "92px", color: "var(--accent)" } }, passageLabel(p)),
      said,
      h("button.btn.sm.quiet", { title: "Remettre ce passage dans le montage", html: svg("undo", 12),
        onclick: (e) => { e.stopPropagation(); restorePassage(p); } })));
  });
  put(box,
    h("div.sep"),
    h("div.row", {},
      h("span.label.grow", {}, `Passages supprimés · ${list.length} · −${sec(total)}`),
      h("button.btn.sm", { html: svg("undo", 12) + "Tout restaurer", onclick: () => {
        const n = edit((doc) => { const r = M.restoreAll(doc); M.reflowCaptions(doc); return r; }, "restore");
        toast(`Tous les passages sont revenus (+${sec(n)}).`);
      } })),
    rows,
    h("div.hint", {}, "Repères rouges sur la timeline : clic pour voir ce qui a été coupé et le restaurer."));
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
  put(box, 
    h("div.stitle", { style: { display: "flex", gap: "7px", fontWeight: 620, marginBottom: "10px" },
                      html: svg("silence", 14) + "<span>Supprimer les blancs</span>" }),
    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Où")),
      h("div.seg", {}, [["selection", "Sélection"], ["main", "Piste principale"], ["all", "Tout"]].map(([k, l]) =>
        h("button", { class: X.scope === k ? "on" : "", onclick: () => { X.scope = k; clearPreview(); renderSilence(); } }, l)))),
    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Détection")),
      h("div.seg", {}, [["voice", "Par la voix"], ["volume", "Par le volume"]].map(([k, l]) =>
        h("button", { class: X.method === k ? "on" : "", onclick: () => { X.method = k; clearPreview(); renderSilence(); } }, l)))),
    X.method === "voice" ? h("div", {},
      slider("Blanc toléré entre deux mots", st.max_gap ?? 0.5, 0.1, 2, 0.05, (v) => sec(v).replace(" s", "") + " s",
             (v) => setS("max_gap", v)),
      slider("Marge gardée autour des mots", st.pad ?? 0.08, 0, 0.4, 0.01, (v) => Math.round(v * 1000) + " ms",
             (v) => setS("pad", v)),
      h("label.check", { style: { marginBottom: "10px" } }, fill, "Couper aussi les tics (euh, du coup, en fait…)"),
      h("div.hint", { style: { marginBottom: "10px" } }, "Utilise la transcription (lancée si besoin)."))
    : h("div", {},
      slider("Seuil de silence", X.noise, -60, -15, 1, (v) => v + " dB", (v) => { X.noise = v; }),
      slider("Silence minimal", X.minSil, 0.1, 2, 0.05, (v) => sec(v), (v) => { X.minSil = v; }),
      h("div.hint", { style: { marginBottom: "10px" } }, "Pour les rushs sans parole : coupe ce qui reste sous le seuil.")),
    h("div.actions", {},
      h("button.btn", { id: "silPreview", html: svg("eye", 13) + "Aperçu", onclick: () => previewCuts() }),
      h("button.btn.primary", { id: "silApply", html: svg("cut", 13) + "Appliquer", onclick: () => applyCutsNow() })),
    h("div.meta", { id: "silMsg", style: { marginTop: "8px" } }, X.preview ? previewText() : ""));
}

/** Un clip par groupe lié : celui de la piste principale, sinon la vidéo. */
function leads(clips) {
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
      cuts = M.clipCuts(c, { words: await wordsOf(c.media), maxGap: st.max_gap ?? 0.5, pad: st.pad ?? 0.08,
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
  box.innerHTML = "";
  put(box, 
    h("div.stitle", { style: { display: "flex", gap: "7px", fontWeight: 620, marginBottom: "10px" },
                      html: svg("cc", 14) + "<span>Transcription</span>" }),
    h("div.g2", {}, h("div.field", {}, h("div.head", {}, h("span.label", {}, "Langue")), lang),
                    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Modèle")), model)),
    list.length ? h("div", {}, list.map((m) => {
      const tr = m.transcript || {};
      const label = { done: `${tr.count || 0} mots${tr.language ? " · " + tr.language : ""}`, running: "en cours…",
                      queued: "en attente…", error: "erreur", none: "à faire" }[tr.status || "none"];
      return h("div.kv", { title: tr.error || "" },
        h("span.ell", { style: { maxWidth: "150px" } }, m.name),
        h("span.row", {}, h("b", { class: tr.status === "error" ? "err" : "" }, label),
          tr.status === "running" || tr.status === "queued" ? null :
            h("button.btn.sm.quiet", { title: tr.status === "done" ? "Retranscrire" : "Transcrire",
              html: svg(tr.status === "done" ? "refresh" : "cc", 13),
              onclick: () => transcribe([m.id], tr.status === "done") })));
    })) : h("div.meta", {}, "Aucun média avec du son."),
    list.some((m) => (m.transcript || {}).status !== "done") ? h("button.btn.wide", {
      style: { marginTop: "8px" }, html: svg("cc", 13) + "Tout transcrire",
      onclick: () => transcribe(list.filter((m) => (m.transcript || {}).status !== "done").map((m) => m.id)),
    }) : null,
    h("div.hint", { style: { marginTop: "8px" } },
      "Whisper tourne sur ton PC (carte graphique si possible). La première fois, le modèle (~1,6 Go) se télécharge."));
}
