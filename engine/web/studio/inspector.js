/* Inspecteur (panneau de droite) : propriétés de la sélection, ou du projet
   quand rien n'est sélectionné. Sections repliables : l'essentiel est ouvert,
   le reste se déplie d'un clic et s'en souvient.

   Un curseur agit en direct : au premier mouvement on mémorise l'état, puis
   chaque valeur est rejouée sur cet état d'origine (comme les gestes de la
   timeline) ; relâcher le curseur fait un seul pas d'annulation. */

import * as A from "./actions.js";
import { api } from "./api.js";
import * as M from "./model.js";
import { PRESETS } from "./main.js";
import { S, begin, changed, edit, emit, end, on } from "./store.js";
import { emojiGeometry, setText, stylePreview } from "./captions.js";
import { $, clamp, fmt, h, put, section, svg, tc, toast } from "./util.js";

export const FONTS = ["Arial", "Arial Black", "Bahnschrift", "Impact", "Segoe UI", "Segoe UI Black", "Verdana",
                      "Tahoma", "Trebuchet MS", "Franklin Gothic Medium", "Candara", "Corbel", "Calibri", "Georgia",
                      "Cambria", "Times New Roman", "Courier New", "Consolas", "Comic Sans MS", "Segoe Script",
                      "Ink Free"];
const EMOJIS = ["", "🔥", "💡", "💰", "🎯", "🚀", "🧠", "❤️", "😂", "😱", "✅", "❌", "⚡", "🤯", "👀", "🙌",
                "📈", "⏰", "🤔", "💪"];
const SPEEDS = [0.25, 0.5, 1, 1.5, 2, 4];
const FPS = [24, 25, 30, 50, 60];

export const I = { presets: [], groups: [], live: null };

export async function init() {
  on("select", render);
  on("doc", ({ reason, light }) => { if (!light && reason !== "inspector") render(); });
  on("editclip", ({ kind }) => {
    if (kind === "text") setTimeout(() => { const ta = $("insp").querySelector("textarea"); if (ta) ta.focus(); }, 0);
  });
  render();
  try {
    const res = await api("/api/styles");
    I.presets = res.styles;
    I.groups = res.groups || [];
  } catch (e) { I.presets = []; }
  render();
  emit("presets");
}

/* ------------------------------------------------------------ geste direct */

function liveApply(fn) {
  if (!I.live) { I.live = JSON.stringify(S.doc); begin(); }
  S.doc = JSON.parse(I.live);
  fn(S.doc);
  M.reflowCaptions(S.doc);
  changed({ light: true, reason: "inspector" });
}
function liveEnd() {
  if (!I.live) return;
  I.live = null;
  end("inspector");
}

/* ------------------------------------------------------------- éléments */

/** Curseur avec sa valeur éditable à droite du libellé. `apply(doc, v)` pose la valeur. */
function range({ label, value, min, max, step = 1, unit = "", apply, decimals = 0 }) {
  const input = h("input", { type: "range", min, max, step, value });
  const num = h("input", { type: "number", min, max, step, value: +(+value).toFixed(decimals),
                           "aria-label": label });
  input.oninput = () => {
    const v = +input.value;
    num.value = +v.toFixed(decimals);
    liveApply((doc) => apply(doc, v));
  };
  input.onchange = liveEnd;
  num.onchange = () => {
    const v = clamp(+num.value, +min, +max);
    if (!Number.isFinite(v)) return;
    edit((doc) => { apply(doc, v); M.reflowCaptions(doc); }, "inspector");
    input.value = v;
  };
  num.onkeydown = (e) => e.stopPropagation();
  return h("div.field", {},
    h("div.head", {}, h("span.label", {}, label), h("span.val", {}, num, unit ? h("span", {}, unit.trim()) : null)),
    h("div.numin", {}, input));
}

function seg(label, options, value, pick) {
  return h("div.field", {}, label ? h("div.head", {}, h("span.label", {}, label)) : null,
    h("div.seg", {}, options.map(([v, text]) => h("button", { class: v === value ? "on" : "", onclick: () => pick(v) }, text))));
}

function check(label, checked, apply) {
  const box = h("input", { type: "checkbox", checked });
  box.onchange = () => edit((doc) => apply(doc, box.checked), "inspector");
  return h("label.check", { style: { marginBottom: "8px" } }, box, label);
}

function color(label, value, apply) {
  const input = h("input", { type: "color", value: value || "#ffffff", title: label });
  input.oninput = () => liveApply((doc) => apply(doc, input.value));
  input.onchange = liveEnd;
  return h("div.field", {}, h("div.head", {}, h("span.label", {}, label)), input);
}

function select(label, options, value, apply) {
  const s = h("select", {}, options.map(([v, text]) => h("option", { value: v, selected: String(v) === String(value) }, text)));
  s.onchange = () => edit((doc) => apply(doc, s.value), "inspector");
  return h("div.field", {}, h("div.head", {}, h("span.label", {}, label)), s);
}

const btn = (label, icon, onclick, extra = "") =>
  h("button.btn.sm" + extra, { onclick, html: (icon ? svg(icon, 13) : "") + label });

/* ================================================================= rendu */

function render() {
  const box = $("insp");
  if (!box || !S.doc) return;
  if (I.live) return;                     // pas de reconstruction au milieu d'un geste
  box.innerHTML = "";
  const clips = S.doc.clips.filter((c) => S.sel.has(c.id));
  if (!clips.length) { $("inspTitle").textContent = "Projet"; put(box, ...projectPanel()); return; }
  const texts = clips.filter((c) => c.kind === "text");
  const media = clips.filter((c) => c.kind !== "text");
  if (texts.length && !media.length) {
    $("inspTitle").textContent = texts.length > 1 ? `${texts.length} textes` : texts[0].auto ? "Sous-titre" : "Texte";
    put(box, ...textPanel(texts));
    return;
  }
  const lead = media[0];
  const m = S.media.get(lead.media);
  $("inspTitle").textContent = media.length > 1 ? `${media.length} clips`
    : { video: "Vidéo", audio: "Audio", image: "Image" }[lead.kind];
  put(box, ...clipPanel(media, lead, m));
}

/* -------------------------------------------------------------- projet */

function projectPanel() {
  const c = S.doc.canvas;
  const preset = PRESETS.find((p) => p.w === c.w && p.h === c.h);
  const n = S.doc.clips.filter((x) => x.kind !== "text").length;
  const texts = S.doc.clips.filter((x) => x.kind === "text").length;
  const wIn = h("input", { type: "number", min: 16, max: 4096, step: 2, value: c.w, "aria-label": "Largeur" });
  const hIn = h("input", { type: "number", min: 16, max: 4096, step: 2, value: c.h, "aria-label": "Hauteur" });
  const setWH = () => edit((doc) => {
    doc.canvas.w = clamp(Math.round(+wIn.value / 2) * 2, 16, 4096);
    doc.canvas.h = clamp(Math.round(+hIn.value / 2) * 2, 16, 4096);
  }, "canvas");
  wIn.onchange = setWH;
  hIn.onchange = setWH;
  wIn.onkeydown = hIn.onkeydown = (e) => e.stopPropagation();
  return [
    section({ title: "Format", icon: "fit", key: "p.format" },
      h("div.field", {}, h("div.chips", {}, PRESETS.map((p) => h("button.chip" + (preset === p ? ".on" : ""), {
        title: `${p.w}×${p.h}`, onclick: () => edit((doc) => { doc.canvas.w = p.w; doc.canvas.h = p.h; }, "canvas"),
      }, p.name)))),
      h("div.g2", {}, h("div.field", {}, h("div.head", {}, h("span.label", {}, "Largeur")), wIn),
                      h("div.field", {}, h("div.head", {}, h("span.label", {}, "Hauteur")), hIn)),
      select("Images par seconde", FPS.map((f) => [f, f + " i/s"]), c.fps, (doc, v) => { doc.canvas.fps = +v; })),
    section({ title: "Arrière-plan", icon: "image", key: "p.bg" },
      color("Couleur", c.bg, (doc, v) => { doc.canvas.bg = v; }),
      check("Flou de la vidéo derrière l'image", !!c.blur, (doc, v) => { doc.canvas.blur = v; }),
      h("div.hint", {}, "Visible quand un clip ne remplit pas le cadre.")),
    section({ title: "Montage", icon: "film", key: "p.stats", open: false },
      h("div.kv", {}, h("span", {}, "Durée"), h("b", {}, tc(M.duration(S.doc), c.fps))),
      h("div.kv", {}, h("span", {}, "Clips"), h("b", {}, String(n))),
      h("div.kv", {}, h("span", {}, "Textes et sous-titres"), h("b", {}, String(texts))),
      h("div.kv", {}, h("span", {}, "Médias"), h("b", {}, String(S.media.size))),
      h("div.kv", {}, h("span", {}, "Pistes"), h("b", {}, String(S.doc.tracks.length)))),
    section({ title: "Raccourcis", icon: "gear", key: "p.keys", open: false }, h("div", { html: [
      ["Espace", "lecture / pause"], ["← →", "image précédente / suivante"], ["↑ ↓", "point de montage précédent / suivant"],
      ["Ctrl+B", "diviser"], ["Suppr", "supprimer"], ["Ctrl+D", "dupliquer"], ["Ctrl+C / X / V", "copier / couper / coller"],
      ["Ctrl+Z / Y", "annuler / rétablir"], ["F", "arrêt sur image"], ["M", "marqueur"], ["N", "aimantation"],
      ["+ / −", "zoom"], ["Maj+Z", "tout voir"], ["Alt+clic", "sans les clips liés"], ["Maj (glisser)", "sans aimantation"],
    ].map(([k, v]) => `<div class="kv"><span class="kbd">${k}</span><span>${v}</span></div>`).join("") })),
  ];
}

/* ---------------------------------------------------------- clips média */

function clipPanel(clips, lead, m) {
  const out = [];
  const one = clips.length === 1;
  const fps = S.doc.canvas.fps;
  const visual = clips.filter((c) => c.kind === "video" || c.kind === "image");
  const timed = clips.filter((c) => c.kind === "video" || c.kind === "audio");
  const sound = clips.filter((c) => (c.kind === "audio" || (c.kind === "video" && !c.detached)) &&
                                    (S.media.get(c.media) || {}).has_audio);
  const videos = clips.filter((c) => c.kind === "video" && (S.media.get(c.media) || {}).has_audio);
  const detached = videos.length > 0 && videos.every((c) => c.detached);

  out.push(h("div.sect", {},
    h("div.row", { style: { marginBottom: "8px" } },
      h("i", { html: svg(lead.kind === "image" ? "image" : lead.kind === "audio" ? "note" : "film", 14), style: { color: "var(--ink-2)" } }),
      h("span.ell", { style: { fontWeight: 620 } }, one && m ? m.name : clips.length + " clips")),
    one ? h("div.kv", {}, h("span", {}, "Sur la timeline"), h("b", {}, `${tc(lead.start, fps)} → ${tc(M.clipEnd(lead), fps)}`)) : null,
    one && lead.kind !== "image" ? h("div.kv", {}, h("span", {}, "Dans la source"), h("b", {}, `${fmt(lead.in)} → ${fmt(M.srcEnd(lead))}`)) : null,
    h("div.actions", { style: { marginTop: "6px" } },
      btn("Diviser", "split", A.split), btn("Dupliquer", "copy", A.duplicate),
      btn("Supprimer", "trash", A.remove),
      lead.kind === "video" && one ? btn("Arrêt sur image", "freeze", () => A.freezeFrame()) : null,
      videos.length ? btn(detached ? "Rattacher le son" : "Séparer le son", "detach", () => A.toggleDetach(videos)) : null,
      lead.link ? btn("Dissocier", "unlink", A.unlinkSelection) : null)));

  if (visual.length) {
    const v = visual[0];
    const ids = new Set(visual.map((c) => c.id));
    const each = (doc, fn) => doc.clips.forEach((c) => { if (ids.has(c.id)) fn(c); });
    out.push(section({ title: "Cadrage", icon: "fit", key: "c.frame" },
      h("div.row", { style: { marginBottom: "10px", justifyContent: "space-between" } },
        h("div.seg", {}, [["cover", "Remplir"], ["contain", "Adapter"]].map(([f, l]) =>
          h("button", { class: (v.fit || "cover") === f ? "on" : "",
                        onclick: () => edit((doc) => each(doc, (c) => { c.fit = f; }), "inspector") }, l))),
        h("div.row", { style: { gap: "2px" } },
          h("button.btn.sm.icon.quiet" + (v.flip_h ? ".on" : ""), { title: "Miroir horizontal", html: svg("flipH", 14),
            onclick: () => edit((doc) => each(doc, (c) => { c.flip_h = !c.flip_h; }), "inspector") }),
          h("button.btn.sm.icon.quiet" + (v.flip_v ? ".on" : ""), { title: "Miroir vertical", html: svg("flipV", 14),
            onclick: () => edit((doc) => each(doc, (c) => { c.flip_v = !c.flip_v; }), "inspector") }),
          h("button.btn.sm.icon.quiet", { title: "Pivoter de 90°", html: svg("rotate", 14),
            onclick: () => edit((doc) => each(doc, (c) => { c.rotation = (((c.rotation || 0) + 90 + 180) % 360) - 180; }), "inspector") }),
          h("button.btn.sm.icon.quiet", { title: "Réinitialiser le cadrage", html: svg("refresh", 14),
            onclick: () => edit((doc) => each(doc, (c) => {
              Object.assign(c, { x: 0.5, y: 0.5, scale: 1, rotation: 0, opacity: 1, flip_h: false, flip_v: false });
            }), "inspector") }))),
      range({ label: "Échelle", value: Math.round((v.scale ?? 1) * 100), min: 5, max: 400, unit: " %",
              apply: (doc, x) => each(doc, (c) => { c.scale = x / 100; }) }),
      range({ label: "Position X", value: Math.round((v.x ?? 0.5) * 100), min: -100, max: 200, unit: " %",
              apply: (doc, x) => each(doc, (c) => { c.x = x / 100; }) }),
      range({ label: "Position Y", value: Math.round((v.y ?? 0.5) * 100), min: -100, max: 200, unit: " %",
              apply: (doc, x) => each(doc, (c) => { c.y = x / 100; }) }),
      range({ label: "Rotation", value: v.rotation || 0, min: -180, max: 180, unit: "°",
              apply: (doc, x) => each(doc, (c) => { c.rotation = x; }) }),
      range({ label: "Opacité", value: Math.round((v.opacity ?? 1) * 100), min: 0, max: 100, unit: " %",
              apply: (doc, x) => each(doc, (c) => { c.opacity = x / 100; }) })));

    if (one) out.push(transitionSection(v));
    const f = v.filters || {};
    const active = Object.values(f).some((x) => x);
    const setF = (key, x) => (doc) => each(doc, (c) => { c.filters = { ...(c.filters || {}), [key]: x / 100 }; });
    out.push(section({ title: "Image", icon: "sliders", key: "c.image", open: false, count: active ? "réglée" : undefined },
      range({ label: "Luminosité", value: Math.round((f.brightness || 0) * 100), min: -100, max: 100,
              apply: (doc, x) => setF("brightness", x)(doc) }),
      range({ label: "Contraste", value: Math.round((f.contrast || 0) * 100), min: -100, max: 100,
              apply: (doc, x) => setF("contrast", x)(doc) }),
      range({ label: "Saturation", value: Math.round((f.saturation || 0) * 100), min: -100, max: 100,
              apply: (doc, x) => setF("saturation", x)(doc) }),
      range({ label: "Température", value: Math.round((f.temperature || 0) * 100), min: -100, max: 100,
              apply: (doc, x) => setF("temperature", x)(doc) }),
      active ? btn("Remettre à zéro", "refresh", () => edit((doc) => each(doc, (c) => { delete c.filters; }), "inspector")) : null));
  }

  if (timed.length) {
    const ids = new Set(timed.map((c) => c.id));
    const sp = timed[0].speed || 1;
    const setSp = (doc, x) => doc.clips.filter((c) => ids.has(c.id)).forEach((c) => M.setSpeed(doc, c, x));
    out.push(section({ title: "Vitesse", icon: "speed", key: "c.speed", count: sp !== 1 ? sp + "×" : undefined },
      h("div.field", {}, h("div.chips", {}, SPEEDS.map((x) => h("button.chip" + (Math.abs(x - sp) < 1e-3 ? ".on" : ""), {
        onclick: () => edit((doc) => { setSp(doc, x); M.reflowCaptions(doc); }, "inspector"),
      }, x + "×")))),
      range({ label: "Exacte", value: sp, min: 0.1, max: 4, step: 0.05, decimals: 2, unit: "×", apply: setSp })));
  }

  if (sound.length) {
    const s = sound[0];
    const ids = new Set(sound.map((c) => c.id));
    const each = (doc, fn) => doc.clips.forEach((c) => { if (ids.has(c.id)) fn(c); });
    const maxFade = Math.max(0.1, Math.min(10, Math.min(...sound.map((c) => c.dur)) / 2));
    const fx = s.audio_fx || {};
    out.push(section({ title: "Son", icon: "vol", key: "c.sound", count: s.muted ? "coupé" : undefined },
      range({ label: "Volume", value: Math.round((s.volume ?? 1) * 100), min: 0, max: 200, unit: " %",
              apply: (doc, x) => each(doc, (c) => { c.volume = x / 100; }) }),
      range({ label: "Fondu d'entrée", value: s.fade_in || 0, min: 0, max: +maxFade.toFixed(1), step: 0.1, decimals: 1,
              unit: " s", apply: (doc, x) => each(doc, (c) => { c.fade_in = x; }) }),
      range({ label: "Fondu de sortie", value: s.fade_out || 0, min: 0, max: +maxFade.toFixed(1), step: 0.1, decimals: 1,
              unit: " s", apply: (doc, x) => each(doc, (c) => { c.fade_out = x; }) }),
      check("Couper le son", !!s.muted, (doc, v) => each(doc, (c) => { c.muted = v; })),
      check("Réduire le bruit de fond", !!fx.denoise,
            (doc, v) => each(doc, (c) => { c.audio_fx = { ...(c.audio_fx || {}), denoise: v }; })),
      check("Voix plus claire", !!fx.voice,
            (doc, v) => each(doc, (c) => { c.audio_fx = { ...(c.audio_fx || {}), voice: v }; })),
      fx.denoise || fx.voice ? h("div.hint", {}, "Les effets de voix s'entendent à l'export.") : null));
  }
  return out;
}

function transitionSection(c) {
  const tr = M.transIn(S.doc, c);
  const adjacent = S.doc.clips.some((o) => o !== c && o.track === c.track && Math.abs(M.clipEnd(o) - c.start) < 1e-3);
  const cur = c.trans || {};
  const label = cur.type ? (M.TRANSITIONS.find((x) => x[0] === cur.type) || [])[1] : undefined;
  if (!adjacent) {
    return section({ title: "Transition", icon: "transition", key: "c.trans", open: false },
      h("div.hint", {}, "Colle ce clip à celui d'avant, sur la même piste, pour ajouter une transition."));
  }
  const maxD = Math.max(0.1, Math.min(3, c.dur, ...S.doc.clips.filter((o) => o.track === c.track &&
    Math.abs(M.clipEnd(o) - c.start) < 1e-3).map((o) => o.dur)));
  return section({ title: "Transition", icon: "transition", key: "c.trans", open: !!cur.type, count: label },
    h("div.chips", { style: { marginBottom: "10px" } },
      [["", "Aucune"], ...M.TRANSITIONS].map(([k, l]) => h("button.chip" + ((cur.type || "") === k ? ".on" : ""), {
        onclick: () => A.setTransition([c.id], k, cur.dur || 0.5) }, l))),
    cur.type ? range({ label: "Durée", value: tr ? tr.d : Math.min(cur.dur || 0.5, maxD), min: 0.1, max: +maxD.toFixed(1),
                       step: 0.1, decimals: 1, unit: " s",
                       apply: (doc, x) => { const cc = doc.clips.find((o) => o.id === c.id); if (cc && cc.trans) cc.trans.dur = x; } }) : null,
    cur.type ? btn("Sur toutes les coupes de la piste", "copy", () => A.transitionEverywhere(c.track, cur.type, cur.dur || 0.5)) : null);
}

/* -------------------------------------------------------------- textes */

const LOOK = ["font", "size", "bold", "upper", "color", "hl", "outline_col", "outline", "shadow", "box", "box_alpha",
              "mode", "pop"];

export function applyLook(c, look) {
  LOOK.forEach((f) => { if (look[f] !== undefined) c[f] = look[f]; });
  const g = emojiGeometry(c.size);
  c.emoji_size = g[0];
  if (!c.emoji_moved) { c.emoji_dy = g[1]; c.emoji_dx = 0; }
}

/** Grille de cartes de style, par groupe. `same(p)` dit si la carte est celle en cours. */
export function styleGrid(presets, groups, { same, pick, compact = false, words } = {}) {
  const grid = h("div.stylegrid" + (compact ? ".compact" : ""));
  const order = groups.length ? groups : [{ name: "", label: "" }];
  for (const g of order) {
    const list = presets.filter((p) => !groups.length || p.group === g.name);
    if (!list.length) continue;
    if (g.label) grid.appendChild(h("div.stylegroup", {}, g.label));
    list.forEach((p) => grid.appendChild(h("button.stylecard" + (same && same(p) ? ".on" : ""), {
      title: p.hint || p.label, onclick: () => pick(p),
    }, stylePreview(p, { height: compact ? 40 : 52, words }), h("span.sn.ell", {}, p.label))));
  }
  return grid;
}

function textPanel(texts) {
  const t = texts[0];
  const ids = new Set(texts.map((c) => c.id));
  const each = (doc, fn) => doc.clips.forEach((c) => { if (ids.has(c.id)) fn(c); });
  const out = [];
  const one = texts.length === 1;
  const fps = S.doc.canvas.fps;

  if (one) {
    const ta = h("textarea", { rows: t.mode === "none" ? 3 : 2, spellcheck: "true", "aria-label": "Texte" },
                 M.liveWords(t).map((w) => w.text).join(" "));
    ta.onkeydown = (e) => e.stopPropagation();
    ta.onchange = () => edit((doc) => { const c = doc.clips.find((x) => x.id === t.id); if (c) setText(c, ta.value); }, "text");
    out.push(h("div.sect", {}, ta,
      h("div.kv", { style: { marginTop: "6px" } }, h("span", {}, "Affiché"), h("b", {}, `${tc(t.start, fps)} → ${tc(M.clipEnd(t), fps)}`)),
      t.auto ? h("div.hint", {}, "Lié à la voix : il suit les coupes de la vidéo.") : null,
      h("div.actions", { style: { marginTop: "8px" } },
        btn("Diviser", "split", A.split), btn("Dupliquer", "copy", A.duplicate), btn("Supprimer", "trash", A.remove))));
  } else {
    out.push(h("div.sect", {}, h("div.hint", {}, `${texts.length} textes sélectionnés : les réglages s'appliquent à tous.`),
      h("div.actions", { style: { marginTop: "8px" } }, btn("Supprimer", "trash", A.remove))));
  }

  if (I.presets.length) {
    const same = (p) => LOOK.every((f) => p[f] === undefined || p[f] === t[f]);
    out.push(section({ title: "Style", icon: "sliders", key: "t.style", open: false },
      styleGrid(I.presets, I.groups, { same, compact: true, words: ["Aa", "Bb"],
        pick: (p) => edit((doc) => each(doc, (c) => applyLook(c, p)), "inspector") })));
  }
  out.push(section({ title: "Apparence", icon: "text", key: "t.look" },
    h("div.g2", {},
      select("Police", FONTS.map((f) => [f, f]), t.font, (doc, v) => each(doc, (c) => { c.font = v; })),
      select("Surlignage", [["word", "Mot par mot"], ["sweep", "Balayage"], ["none", "Aucun"]], t.mode,
             (doc, v) => each(doc, (c) => { c.mode = v; }))),
    range({ label: "Taille", value: t.size, min: 12, max: 320, step: 2,
            apply: (doc, x) => each(doc, (c) => { c.size = x; const g = emojiGeometry(x); c.emoji_size = g[0];
                                                   if (!c.emoji_moved) c.emoji_dy = g[1]; }) }),
    h("div.g3", {},
      color("Texte", t.color, (doc, v) => each(doc, (c) => { c.color = v; })),
      color("Surligné", t.hl, (doc, v) => each(doc, (c) => { c.hl = v; })),
      color(t.box ? "Fond" : "Contour", t.outline_col, (doc, v) => each(doc, (c) => { c.outline_col = v; }))),
    range({ label: t.box ? "Marge du fond" : "Contour", value: t.outline, min: 0, max: 20,
            apply: (doc, x) => each(doc, (c) => { c.outline = x; }) }),
    t.box ? range({ label: "Opacité du fond", value: Math.round((1 - (t.box_alpha || 0)) * 100), min: 0, max: 100, unit: " %",
                    apply: (doc, x) => each(doc, (c) => { c.box_alpha = 1 - x / 100; }) }) : null,
    range({ label: "Ombre", value: t.shadow || 0, min: 0, max: 12,
            apply: (doc, x) => each(doc, (c) => { c.shadow = x; }) }),
    h("div.g2", {},
      check("Fond derrière le texte", !!t.box, (doc, v) => each(doc, (c) => { c.box = v; })),
      check("Majuscules", !!t.upper, (doc, v) => each(doc, (c) => { c.upper = v; }))),
    h("div.g2", {},
      check("Gras", t.bold !== false, (doc, v) => each(doc, (c) => { c.bold = v; })),
      check("Zoom du mot actif", !!t.pop, (doc, v) => each(doc, (c) => { c.pop = v; })))));

  out.push(section({ title: "Position", icon: "fit", key: "t.pos" },
    range({ label: "Position X", value: Math.round(t.x * 100), min: 0, max: 100, unit: " %",
            apply: (doc, x) => each(doc, (c) => { c.x = x / 100; c.moved = true; }) }),
    range({ label: "Position Y", value: Math.round(t.y * 100), min: 0, max: 100, unit: " %",
            apply: (doc, x) => each(doc, (c) => { c.y = x / 100; c.moved = true; }) })));

  if (one) {
    out.push(section({ title: "Émoji", icon: "note", key: "t.emoji", open: false, count: t.emoji || undefined },
      h("div.chips", {}, EMOJIS.map((e) => h("button.chip" + ((t.emoji || "") === e ? ".on" : ""), {
        title: e || "aucun", onclick: () => edit((doc) => each(doc, (c) => {
          c.emoji = e;
          const g = emojiGeometry(c.size);
          c.emoji_size = g[0];
          if (!c.emoji_moved) { c.emoji_dx = 0; c.emoji_dy = g[1]; }
        }), "inspector"),
      }, e || "—")))));
  }
  const trackIds = new Set(texts.map((c) => c.track));
  out.push(h("div.sect", {}, h("button.btn.wide", {
    html: svg("copy", 13) + "Appliquer ce style à toute la piste",
    onclick: () => {
      edit((doc) => doc.clips.forEach((c) => {
        if (c.kind === "text" && trackIds.has(c.track) && !ids.has(c.id)) {
          applyLook(c, t);
          if (!c.moved) { c.x = t.x; c.y = t.y; }
        }
      }), "inspector");
      toast("Style appliqué à la piste.");
    },
  })));
  return out;
}
