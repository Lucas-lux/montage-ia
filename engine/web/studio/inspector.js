/* Inspecteur (panneau de droite) : propriétés de la sélection, ou du projet
   quand rien n'est sélectionné. Sections repliables : l'essentiel est ouvert,
   le reste se déplie d'un clic et s'en souvient.

   Un curseur agit en direct : au premier mouvement on mémorise l'état, puis
   chaque valeur est rejouée sur cet état d'origine (comme les gestes de la
   timeline) ; relâcher le curseur fait un seul pas d'annulation. */

import * as A from "./actions.js";
import * as AN from "./anim.js";
import { api, del, post } from "./api.js";
import * as M from "./model.js";
import * as V from "./voice.js";
import { PRESETS } from "./main.js";
import { S, begin, changed, edit, emit, end, on } from "./store.js";
import { emojiGeometry, setText, stylePreview } from "./captions.js";
import { fontField } from "./fonts.js";
import { startPolling } from "./bin.js";
import { geometry } from "./player.js";
import { $, clamp, fmt, h, put, section, svg, tc, toast } from "./util.js";

const EMOJIS = ["", "🔥", "💡", "💰", "🎯", "🚀", "🧠", "❤️", "😂", "😱", "✅", "❌", "⚡", "🤯", "👀", "🙌",
                "📈", "⏰", "🤔", "💪"];
const SPEEDS = [0.25, 0.5, 1, 1.5, 2, 4];
const FPS = [24, 25, 30, 50, 60];

export const I = { presets: [], groups: [], live: null, animTab: "in", matting: null, subjectKey: "" };

export async function init() {
  on("select", render);
  // détourage en cours : l'inspecteur suit sa progression
  on("media", () => {
    const key = [...S.sel].map((id) => {
      const c = S.doc.clips.find((x) => x.id === id);
      const s = c && c.media ? (S.media.get(c.media) || {}).subject || {} : {};
      return `${s.status}:${Math.round(s.progress || 0)}`;
    }).join(",");
    if (key !== I.subjectKey) { I.subjectKey = key; render(); }
  });
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
      ["Ctrl+B", "diviser"], ["Q / W", "diviser et garder la droite / la gauche"], ["Suppr", "supprimer"], ["Ctrl+D", "dupliquer"], ["Ctrl+C / X / V", "copier / couper / coller"],
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
    out.push(animSection(visual, "media"));
    if (one) out.push(subjectSection(v, m));
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
    out.push(voiceSection(fx, each));
    out.push(section({ title: "Son", icon: "vol", key: "c.sound", count: s.muted ? "coupé" : undefined },
      range({ label: "Volume", value: Math.round((s.volume ?? 1) * 100), min: 0, max: 200, unit: " %",
              apply: (doc, x) => each(doc, (c) => { c.volume = x / 100; }) }),
      range({ label: "Fondu d'entrée", value: s.fade_in || 0, min: 0, max: +maxFade.toFixed(1), step: 0.1, decimals: 1,
              unit: " s", apply: (doc, x) => each(doc, (c) => { c.fade_in = x; }) }),
      range({ label: "Fondu de sortie", value: s.fade_out || 0, min: 0, max: +maxFade.toFixed(1), step: 0.1, decimals: 1,
              unit: " s", apply: (doc, x) => each(doc, (c) => { c.fade_out = x; }) }),
      check("Couper le son", !!s.muted, (doc, v) => each(doc, (c) => { c.muted = v; }))));
  }
  return out;
}

/** Traitement de la voix : presets, puis chaque réglage. */
function voiceSection(fx, each) {
  const preset = V.presetOf(fx);
  const setFx = (doc, patch) => each(doc, (c) => {
    const next = { ...(c.audio_fx || {}), ...patch };
    delete next.preset;
    const p = V.presetOf(next);
    if (p) next.preset = p.name;
    c.audio_fx = next;
  });
  const chips = h("div.chips", { style: { marginBottom: "10px" } }, V.PRESETS.map((p) => h("button.chip" +
    (preset && preset.name === p.name ? ".on" : ""), { title: p.hint,
    onclick: () => edit((doc) => each(doc, (c) => { c.audio_fx = V.withPreset(p.name); }), "inspector") }, p.label)));
  const label = preset ? preset.label : V.active(fx) ? "personnalisé" : undefined;
  return section({ title: "Voix", icon: "mic", key: "c.voice", open: V.active(fx), count: label },
    chips,
    V.SLIDERS.map(([k, l, hint]) => {
      const el = range({ label: l, value: Math.round((fx[k] || 0) * 100), min: 0, max: 100, unit: " %",
                         apply: (doc, x) => setFx(doc, { [k]: x / 100 }) });
      el.title = hint;
      return el;
    }),
    V.SWITCHES.map(([k, l, hint]) => {
      const el = check(l, !!fx[k], (doc, v) => setFx(doc, { [k]: v }));
      el.title = hint;
      return el;
    }),
    h("div.hint", {}, "L'aperçu joue coupe-bas, clarté, chaleur, sifflantes et compression ; " +
                      "bruit, porte et niveau constant s'entendent à l'export."));
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
              "mode", "pop", "italic", "spacing", "color2", "shadow_col", "shadow_blur", "glow", "glow_col", "outline2",
              "outline2_col", "extrude", "extrude_col", "hollow", "rotation", "opacity"];
const ANIM_KEYS = ["anim_in", "anim_out", "anim_loop"];

export function applyLook(c, look) {
  LOOK.forEach((f) => { if (look[f] !== undefined) c[f] = look[f]; });
  // un style animé apporte ses animations ; un style sans animation laisse celles du texte
  if (ANIM_KEYS.some((k) => look[k])) ANIM_KEYS.forEach((k) => { if (look[k]) c[k] = { ...look[k] }; else delete c[k]; });
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
      fontField("Police", t.font, (v) => edit((doc) => each(doc, (c) => { c.font = v; }), "inspector")),
      select("Surlignage", [["word", "Mot par mot"], ["sweep", "Balayage"], ["reveal", "Apparition"],
                            ["dim", "Mot actif seul"], ["none", "Aucun"]], t.mode,
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

  out.push(effectsSection(t, each));
  out.push(animSection(texts, "text"));
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

/* ------------------------------------------------------------- effets */

// Effets en un clic : ils ne touchent qu'aux champs d'effet (couleurs du texte comprises pour « Néon »).
const darker = (hex, k = 0.55) => {
  const n = parseInt(String(hex || "#FFFFFF").slice(1), 16);
  const f = (x) => Math.round(x * (1 - k)).toString(16).padStart(2, "0");
  return "#" + f((n >> 16) & 255) + f((n >> 8) & 255) + f(n & 255);
};
const NO_FX = { glow: 0, outline2: 0, extrude: 0, shadow_blur: 0, color2: "", hollow: false };
const FX_PRESETS = [
  ["Aucun", () => ({ ...NO_FX })],
  ["Néon", (t) => ({ ...NO_FX, color: "#FFFFFF", outline: 3, outline_col: t.hl && t.hl !== t.color ? t.hl : "#FF2BD6",
                     glow: 24, glow_col: t.hl && t.hl !== t.color ? t.hl : "#FF2BD6" })],
  ["Lueur", (t) => ({ ...NO_FX, glow: 18, glow_col: t.color })],
  ["Double contour", (t) => ({ ...NO_FX, outline: Math.max(4, t.outline || 0), outline2: 6,
                               outline2_col: t.outline_col === "#FFFFFF" ? "#000000" : "#FFFFFF" })],
  ["Relief 3D", (t) => ({ ...NO_FX, extrude: 12, extrude_col: darker(t.color === "#FFFFFF" ? "#F23A52" : t.color) })],
  ["Ombre douce", () => ({ ...NO_FX, shadow: 8, shadow_blur: 10, shadow_col: "#000000" })],
  ["Dégradé", (t) => ({ ...NO_FX, color2: t.hl && t.hl !== t.color ? t.hl : "#FFC371" })],
  ["Contour seul", (t) => ({ ...NO_FX, hollow: true, outline: Math.max(3, t.outline || 0),
                             outline_col: t.outline_col === "#000000" ? t.color : t.outline_col })],
];

function effectsSection(t, each) {
  const n = [t.glow, t.outline2, t.extrude, t.shadow_blur, t.color2, t.hollow, t.italic, t.spacing, t.rotation]
    .filter((x) => x).length + ((t.opacity ?? 1) < 1 ? 1 : 0);
  return section({ title: "Effets", icon: "wand", key: "t.fx", open: n > 0, count: n ? String(n) : undefined },
    h("div.chips", { style: { marginBottom: "10px" } }, FX_PRESETS.map(([label, make]) => h("button.chip", {
      onclick: () => edit((doc) => each(doc, (c) => Object.assign(c, make(c))), "effects"),
    }, label))),
    h("div.g2", {},
      range({ label: "Lueur", value: t.glow || 0, min: 0, max: 60, apply: (doc, x) => each(doc, (c) => { c.glow = x; }) }),
      color("Couleur de la lueur", t.glow_col, (doc, v) => each(doc, (c) => { c.glow_col = v; }))),
    h("div.g2", {},
      range({ label: "Second contour", value: t.outline2 || 0, min: 0, max: 30,
              apply: (doc, x) => each(doc, (c) => { c.outline2 = x; }) }),
      color("Couleur", t.outline2_col, (doc, v) => each(doc, (c) => { c.outline2_col = v; }))),
    h("div.g2", {},
      range({ label: "Relief 3D", value: t.extrude || 0, min: 0, max: 40,
              apply: (doc, x) => each(doc, (c) => { c.extrude = x; }) }),
      color("Couleur du relief", t.extrude_col, (doc, v) => each(doc, (c) => { c.extrude_col = v; }))),
    h("div.g2", {},
      range({ label: "Flou de l'ombre", value: t.shadow_blur || 0, min: 0, max: 30,
              apply: (doc, x) => each(doc, (c) => { c.shadow_blur = x; if (x && !c.shadow) c.shadow = 6; }) }),
      color("Couleur de l'ombre", t.shadow_col, (doc, v) => each(doc, (c) => { c.shadow_col = v; }))),
    h("div.g2", {},
      (() => {
        // case à part : cocher fait apparaître le choix de la couleur de fin
        const box = h("input", { type: "checkbox", checked: !!t.color2 });
        box.onchange = () => edit((doc) => each(doc, (c) => {
          c.color2 = box.checked ? (c.hl !== c.color ? c.hl : "#FFC371") : "";
        }), "effects");
        return h("label.check", { style: { marginBottom: "8px" } }, box, "Dégradé");
      })(),
      t.color2 ? color("Fin du dégradé", t.color2, (doc, v) => each(doc, (c) => { c.color2 = v; })) : h("span")),
    h("div.g2", {},
      check("Contour seul", !!t.hollow, (doc, v) => each(doc, (c) => { c.hollow = v; })),
      check("Italique", !!t.italic, (doc, v) => each(doc, (c) => { c.italic = v; }))),
    range({ label: "Espacement", value: t.spacing || 0, min: -10, max: 40, apply: (doc, x) => each(doc, (c) => { c.spacing = x; }) }),
    range({ label: "Rotation", value: t.rotation || 0, min: -180, max: 180, unit: "°",
            apply: (doc, x) => each(doc, (c) => { c.rotation = x; }) }),
    range({ label: "Opacité", value: Math.round((t.opacity ?? 1) * 100), min: 0, max: 100, unit: " %",
            apply: (doc, x) => each(doc, (c) => { c.opacity = x / 100; }) }));
}

/* ---------------------------------------------------------- animations */

const ANIM_TABS = [["in", "Entrée", "anim_in"], ["out", "Sortie", "anim_out"], ["loop", "Boucle", "anim_loop"]];

/** Animations d'entrée, de sortie et en boucle : cartes animées au survol,
 *  puis durée (ou vitesse pour une boucle). Mêmes animations à l'export. */
function animSection(clips, target) {
  const ids = new Set(clips.map((c) => c.id));
  const each = (doc, fn) => doc.clips.forEach((c) => { if (ids.has(c.id)) fn(c); });
  const lead = clips[0];
  const tab = I.animTab;
  const [, , key] = ANIM_TABS.find((x) => x[0] === tab);
  const cur = lead[key];
  const tabs = h("div.seg", { style: { marginBottom: "8px" } }, ANIM_TABS.map(([k, label, kk]) =>
    h("button", { class: k === tab ? "on" : "", onclick: () => { I.animTab = k; render(); } },
      label + (lead[kk] ? " •" : ""))));
  const set = (a) => edit((doc) => each(doc, (c) => {
    if (!a) delete c[key];
    else c[key] = tab === "loop" ? { type: a.id, speed: (c[key] && c[key].speed) || 1 }
      : { type: a.id, dur: +Math.min(a.dur || 0.5, Math.max(0.1, c.dur / 2)).toFixed(2) };
  }), "anim");
  const grid = h("div.animgrid", {}, [{ id: "", label: "Aucune" }, ...AN.choices(tab, target)].map((a) =>
    animCard(a, target, (cur ? cur.type : "") === a.id, () => set(a.id ? a : null))));
  const maxDur = Math.max(0.1, Math.min(5, Math.min(...clips.map((c) => c.dur))));
  const knob = !cur ? null : tab === "loop"
    ? range({ label: "Vitesse", value: cur.speed || 1, min: 0.25, max: 4, step: 0.05, decimals: 2, unit: "×",
              apply: (doc, x) => each(doc, (c) => { if (c[key]) c[key].speed = x; }) })
    : range({ label: "Durée", value: Math.min(cur.dur, maxDur), min: 0.1, max: +maxDur.toFixed(2), step: 0.05,
              decimals: 2, unit: " s", apply: (doc, x) => each(doc, (c) => { if (c[key]) c[key].dur = x; }) });
  const n = ANIM_KEYS.filter((k) => lead[k]).length;
  return section({ title: "Animations", icon: "speed", key: target === "text" ? "t.anim" : "c.anim",
                   open: n > 0, count: n ? String(n) : undefined }, tabs, grid, knob);
}

/** Carte d'animation : le mot « Aa » (ou une vignette) la joue au survol. */
function animCard(a, target, on, pick) {
  const d = AN.DEFS.anims[a.id];
  const sample = target === "text"
    ? h("span.animpv.txt", {}, ...[..."Aa"].map((ch) => h("l", {}, ch)))
    : h("span.animpv.media");
  const card = h("button.animcard" + (on ? ".on" : ""), { title: a.label, onclick: pick },
    h("span.animstage", {}, sample), h("span.an.ell", {}, a.label));
  if (!d) return card;
  let raf = 0, t0 = 0;
  const units = [...sample.querySelectorAll("l")];
  const frame = (now) => {
    const len = d.kind === "loop" ? (d.span ? 2 : (d.period || 1)) : (d.dur || 0.5);
    const cycle = d.kind === "loop" ? len : len + 0.6;          // une pause entre deux lectures
    const tt = ((now - t0) / 1000) % cycle;
    const p = Math.min(1, tt / len);
    const st = d.per ? { ...AN.IDENTITY } : AN.sample(d.kf, p, d.ease || "linear");
    sample.style.transform = `translate(${st.dx * 60}px, ${st.dy * 60}px) rotate(${st.r}deg) ` +
      `scale(${st.s * st.sx}, ${st.s * st.sy})`;
    sample.style.opacity = st.o;
    sample.style.filter = st.b > 0.01 ? `blur(${st.b * 0.12}px)` : "";
    if (d.per && units.length) {
      AN.unitValues(d, p, units.length).forEach((v, i) => {
        units[i].style.opacity = v.o;
        units[i].style.filter = v.b > 0.01 ? `blur(${v.b * 0.12}px)` : "";
      });
    }
    raf = requestAnimationFrame(frame);
  };
  card.onpointerenter = () => { t0 = performance.now(); cancelAnimationFrame(raf); raf = requestAnimationFrame(frame); };
  card.onpointerleave = () => {
    cancelAnimationFrame(raf);
    sample.style.transform = sample.style.opacity = sample.style.filter = "";
    units.forEach((u) => { u.style.opacity = ""; u.style.filter = ""; });
  };
  return card;
}

/* ------------------------------------------------------------------ sujet */

async function mattingInfo(force = false) {
  if (I.matting && !force) return I.matting;
  try { I.matting = await api("/api/matting"); } catch (e) { I.matting = { available: false, download: {} }; }
  return I.matting;
}

/** Détourage : choisir le sujet d'un clic, puis supprimer l'arrière-plan,
 *  cadrer le clip sur le sujet ou le suivre. */
function subjectSection(c, m) {
  if (!m || (m.kind !== "video" && m.kind !== "image")) return null;
  const sub = m.subject || {};
  const box = h("div", {});
  const ids = new Set([c.id]);
  const each = (doc, fn) => doc.clips.forEach((x) => { if (ids.has(x.id)) fn(x); });
  const pick = h("button.btn.wide", { html: svg("fit", 13) + (sub.status === "done" ? "Changer de sujet" : "Choisir le sujet"),
                                      onclick: () => pickSubject(c, m) });
  const fill = async () => {
    const info = await mattingInfo();
    box.innerHTML = "";
    if (!info.available) {
      const dl = info.download || {};
      put(box, h("div.hint", { style: { marginBottom: "8px" } },
        "Détoure une personne : supprime l'arrière-plan, cadre ou suis le sujet. Tout se calcule sur ton PC."),
      dl.status === "running" ? h("div.meta", {}, "Téléchargement du modèle…")
        : h("button.btn.wide", { html: svg("down", 13) + "Télécharger le détourage (26 Mo)", onclick: async () => {
          try { await post("/api/matting/download", {}); } catch (e) { toast(e.message); return; }
          const wait = async () => {
            const i = await mattingInfo(true);
            if (i.available) { render(); return; }
            if ((i.download || {}).status === "error") { toast("Téléchargement impossible : " + i.download.message, 5000); render(); return; }
            setTimeout(wait, 1000);
          };
          render();
          wait();
        } }),
      dl.status === "error" ? h("div.err", { style: { marginTop: "6px" } }, dl.message || "Téléchargement impossible.") : null);
      return;
    }
    if (sub.status === "queued" || sub.status === "running") {
      put(box, h("div.meta", {}, sub.status === "queued" ? "En attente…" : `Détection du sujet… ${Math.round(sub.progress || 0)} %`),
        h("div.track-bar", {}, h("i", { style: { width: (sub.progress || 0) + "%" } })));
      return;
    }
    if (sub.status === "error") put(box, h("div.err", { style: { marginBottom: "8px" } }, sub.error || "Détourage impossible."));
    if (sub.status !== "done") {
      put(box, h("div.hint", { style: { marginBottom: "8px" } }, "Clique sur le sujet (toi, par exemple) dans l'aperçu."), pick);
      return;
    }
    const cut = h("input", { type: "checkbox", checked: !!c.cutout });
    cut.onchange = () => edit((doc) => each(doc, (x) => { if (cut.checked) x.cutout = true; else delete x.cutout; }), "subject");
    const fol = h("input", { type: "checkbox", checked: !!c.follow });
    fol.onchange = () => edit((doc) => each(doc, (x) => { if (fol.checked) x.follow = true; else delete x.follow; }), "subject");
    put(box,
      h("label.check", { style: { marginBottom: "6px" } }, cut, "Supprimer l'arrière-plan"),
      m.kind === "video" ? h("label.check", { style: { marginBottom: "10px" }, title: "Le cadre suit le sujet quand il bouge" }, fol, "Suivre le sujet") : null,
      h("div.actions", {},
        h("button.btn.sm", { html: svg("fit", 13) + "Cadrer sur le sujet", onclick: () => A.frameSubject(ids, geometry) }),
        h("button.btn.sm", { html: svg("refresh", 13) + "Changer de sujet", onclick: () => pickSubject(c, m) }),
        h("button.btn.sm.quiet", { html: svg("trash", 13) + "Oublier", title: "Supprime le détourage de ce média", onclick: async () => {
          try { await del(`/api/timeline/${S.pid}/media/${m.id}/subject`); }
          catch (e) { toast(e.message); return; }
          edit((doc) => doc.clips.forEach((x) => { if (x.media === m.id) { delete x.cutout; delete x.follow; } }), "subject");
          startPolling();
        } })));
  };
  fill();
  const label = { done: "prêt", running: Math.round(sub.progress || 0) + " %", queued: "en attente", error: "erreur" }[sub.status];
  return section({ title: "Sujet", icon: "eye", key: "c.subject", open: !!sub.status, count: label }, box);
}

/** Mode « choisir le sujet » : un clic sur l'aperçu désigne le sujet à détourer. */
function pickSubject(c, m) {
  const stage = $("stage");
  const veil = h("div.pickveil", {}, h("div.pickhint", {}, "Clique sur le sujet à garder · Échap pour annuler"));
  const stop = () => { veil.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); stop(); } };
  veil.onpointerdown = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const clip = S.doc.clips.find((x) => x.id === c.id) || c;
    const pt = A.sourcePoint(clip, m, e.clientX, e.clientY, geometry);
    if (!pt) { toast("Clique sur l'image du clip."); return; }
    stop();
    const t = clip.kind === "image" ? 0
      : (clip.in || 0) + (Math.min(M.clipEnd(clip), Math.max(clip.start, S.t)) - clip.start) * (clip.speed || 1);
    try {
      await post(`/api/timeline/${S.pid}/media/${m.id}/subject`, { x: pt[0], y: pt[1], t });
      edit((doc) => doc.clips.forEach((x) => { if (x.id === c.id) x.cutout = true; }), "subject");
      toast("Détection du sujet lancée : elle tourne sur ton PC.");
      startPolling();
    } catch (e2) { toast("Détourage impossible : " + e2.message, 5000); }
  };
  stage.appendChild(veil);
  document.addEventListener("keydown", onKey, true);
}
