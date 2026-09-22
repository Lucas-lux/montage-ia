/* Fenêtre d'export : réglages, progression, résultat.

   L'export relit le montage SAUVEGARDÉ : on enregistre d'abord. Un export
   déjà fait n'est pas refait d'office : on montre le dernier, en signalant
   s'il ne correspond plus au montage (empreinte `signature`). */

import { api, post } from "./api.js";
import * as M from "./model.js";
import { S, saveNow } from "./store.js";
import { $, fmt, h, mo, put, svg, toast } from "./util.js";

const RES = [["720p", 720, "720p"], ["1080p", 1080, "1080p"], ["1440p", 1440, "2K"], ["2160p", 2160, "4K"]];
const FPS = [24, 25, 30, 50, 60];
const QUALITY = [["low", "Légère"], ["standard", "Standard"], ["high", "Haute"]];
const Q_FACTOR = { low: 0.55, standard: 1, high: 1.7 };

const E = {
  opts: { kind: "video", resolution: "1080p", fps: 30, quality: "standard", codec: "h264",
          audio_format: "mp3", audio_quality: null, folder: "", loudness: false },
  formats: null, poll: 0, modal: null, last: null, folder: "",
};

export function init() {
  $("exportBtn").onclick = open;
  try { Object.assign(E.opts, JSON.parse(localStorage.getItem("studio.export") || "{}")); } catch (e) { /* rien */ }
}

/** Empreinte du montage : dit si le dernier export correspond encore. */
export function signature() {
  const s = JSON.stringify([S.doc.canvas, S.doc.tracks, S.doc.clips]);
  let x = 2166136261;
  for (let i = 0; i < s.length; i++) { x ^= s.charCodeAt(i); x = Math.imul(x, 16777619); }
  return (x >>> 0).toString(36);
}

const size = (res) => {
  const { w, h: hh } = S.doc.canvas;
  const target = (RES.find((r) => r[0] === res) || [0, Math.min(w, hh)])[1];
  const k = target / Math.min(w, hh);
  return [Math.round(w * k / 2) * 2, Math.round(hh * k / 2) * 2];
};

async function open() {
  if (!S.doc.clips.length) { toast("Le montage est vide : ajoute des clips avant d'exporter."); return; }
  await saveNow();
  let st;
  try { st = await api(`/api/timeline/${S.pid}/export`); }
  catch (e) { toast(e.message); return; }
  E.folder = st.folder;
  E.last = st.export;
  if (!E.opts.fps || !FPS.includes(E.opts.fps)) E.opts.fps = S.doc.canvas.fps;
  // le montage automatique demande un volume normalisé : coché d'office ici
  if ((S.doc.settings || {}).loudness) E.opts.loudness = true;
  const box = h("div", { id: "expBox" });
  E.modal = modalShell(box);
  if (st.task.status === "running") { running(); watch(); }
  else if (E.last) result(E.last);
  else settings();
}

function modalShell(body) {
  const close = () => { clearTimeout(E.poll); ov.remove(); document.removeEventListener("keydown", key, true); };
  const key = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const ov = h("div.overlay", { onpointerdown: (e) => { if (e.target === ov) close(); } },
    h("div.modal", { style: { maxWidth: "520px" } },
      h("div.mh", {}, h("h2", { id: "expTitle" }, "Exporter"),
        h("button.btn.sm.icon.quiet", { title: "Fermer", onclick: close, html: svg("close", 14) })),
      h("div.mb", {}, body)));
  document.body.appendChild(ov);
  document.addEventListener("keydown", key, true);
  return { close };
}

const title = (t) => { $("expTitle").textContent = t; };
const saveOpts = () => { try { localStorage.setItem("studio.export", JSON.stringify(E.opts)); } catch (e) { /* rien */ } };

/* -------------------------------------------------------------- réglages */

async function settings() {
  title("Exporter");
  const o = E.opts;
  const box = $("expBox");
  box.innerHTML = "";
  if (o.kind === "audio" && !E.formats) {
    try { E.formats = await api("/api/tools/audio/formats"); } catch (e) { E.formats = null; }
  }
  const seg = (label, list, value, pick) => h("div.field", {},
    h("div.head", {}, h("span.label", {}, label)),
    h("div.seg", {}, list.map(([v, l]) => h("button", { class: v === value ? "on" : "", onclick: () => { pick(v); saveOpts(); settings(); } }, l))));
  const d = M.duration(S.doc);
  const folder = h("input", { type: "text", value: o.folder || E.folder, placeholder: E.folder });
  folder.onchange = () => { o.folder = folder.value.trim() === E.folder ? "" : folder.value.trim(); saveOpts(); };
  folder.onkeydown = (e) => e.stopPropagation();

  put(box,
    seg("Type", [["video", "Vidéo MP4"], ["audio", "Son seul"]], o.kind, (v) => { o.kind = v; }),
    o.kind === "video" ? h("div", {},
      h("div.field", {}, h("div.head", {}, h("span.label", {}, "Définition")),
        h("div.chips", {}, RES.map(([key, , label]) => {
          const [w, hh] = size(key);
          return h("button.chip" + (o.resolution === key ? ".on" : ""), { title: `${w}×${hh}`,
            onclick: () => { o.resolution = key; saveOpts(); settings(); } }, `${label} · ${w}×${hh}`);
        }))),
      h("div.g2", {},
        h("div.field", {}, h("div.head", {}, h("span.label", {}, "Images par seconde")),
          (() => {
            const s = h("select", {}, FPS.map((f) => h("option", { value: f, selected: f === +o.fps }, f + " i/s")));
            s.onchange = () => { o.fps = +s.value; saveOpts(); settings(); };
            return s;
          })()),
        seg("Codec", [["h264", "H.264"], ["hevc", "HEVC"]], o.codec, (v) => { o.codec = v; })),
      seg("Qualité", QUALITY, o.quality, (v) => { o.quality = v; }),
      h("div.hint", {}, o.codec === "hevc"
        ? "HEVC : fichier environ deux fois plus léger, lu par les téléphones récents et les réseaux sociaux."
        : "H.264 : lisible partout (réseaux sociaux, montage, vieux appareils)."))
    : audioSettings(o),
    (() => {
      const box = h("input", { type: "checkbox", checked: !!o.loudness });
      box.onchange = () => { o.loudness = box.checked; saveOpts(); };
      return h("label.check", { style: { margin: "10px 0 4px" } }, box, "Normaliser le volume (−14 LUFS, niveau des réseaux sociaux)");
    })(),
    h("div.field", { style: { marginTop: "10px" } }, h("div.head", {}, h("span.label", {}, "Dossier")), folder),
    h("div.kv", {}, h("span", {}, "Durée"), h("b", {}, fmt(d))),
    o.kind === "video" ? h("div.kv", {}, h("span", {}, "Taille estimée"), h("b", {}, "≈ " + mo(estimate(d)))) : null,
    E.last ? h("button.btn.sm.quiet", { style: { marginTop: "6px" }, html: svg("film", 13) + "Voir le dernier export",
                                        onclick: () => result(E.last) }) : null,
    h("div.mf", { style: { padding: "14px 0 0" } },
      h("button.btn.primary", { onclick: start, html: svg("down", 14) + "Exporter" })));
}

function audioSettings(o) {
  if (!E.formats) return h("div.meta", {}, "Formats audio indisponibles.");
  const fmtSpec = E.formats.formats.find((f) => f.name === o.audio_format) || E.formats.formats[0];
  if (!fmtSpec.qualities.includes(o.audio_quality)) o.audio_quality = fmtSpec.default;
  return h("div", {},
    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Format")),
      h("div.chips", {}, E.formats.formats.map((f) => h("button.chip" + (f.name === fmtSpec.name ? ".on" : ""), {
        onclick: () => { o.audio_format = f.name; o.audio_quality = f.default; saveOpts(); settings(); } }, f.label)))),
    h("div.field", {}, h("div.head", {}, h("span.label", {}, fmtSpec.lossless ? "Profondeur" : "Débit")),
      h("div.chips", {}, fmtSpec.qualities.map((q) => h("button.chip" + (q === o.audio_quality ? ".on" : ""), {
        onclick: () => { o.audio_quality = q; saveOpts(); settings(); } }, q + (fmtSpec.lossless ? " bits" : " kbit/s"))))),
    h("div.hint", {}, "Le mixage de toutes les pistes (voix, musique, sons), avec volumes et fondus."));
}

function estimate(d) {
  const [w, hh] = size(E.opts.resolution);
  let mbps = Math.max(2.5, 8 * (w * hh) / (1080 * 1920)) * (Q_FACTOR[E.opts.quality] || 1);
  if (E.opts.codec === "hevc") mbps *= 0.55;
  return ((mbps + 0.19) * d / 8) * 1048576;
}

/* ------------------------------------------------------------ exécution */

async function start() {
  await saveNow();
  const o = E.opts;
  const body = o.kind === "audio"
    ? { audio_only: true, audio_format: o.audio_format, audio_quality: o.audio_quality, folder: o.folder,
        loudness: o.loudness, signature: signature() }
    : { resolution: o.resolution, fps: o.fps, quality: o.quality, codec: o.codec, folder: o.folder,
        loudness: o.loudness, signature: signature() };
  try {
    await post(`/api/timeline/${S.pid}/export`, body);
  } catch (e) { failed(e.message); return; }
  running();
  watch();
}

function running() {
  title("Export en cours");
  const box = $("expBox");
  box.innerHTML = "";
  put(box,
    h("div.meta", { id: "expMsg" }, "Démarrage…"),
    h("div.track-bar", {}, h("i", { id: "expBar" })),
    h("div.row", {}, h("span.meta.num.grow", { id: "expPct" }, "0 %"),
      h("button.btn.sm", { onclick: cancel, html: svg("close", 12) + "Annuler" })),
    h("div.hint", { style: { marginTop: "10px" } },
      "Rendu en pleine qualité depuis les fichiers d'origine. Tu peux fermer cette fenêtre : l'export continue."));
}

function watch() {
  clearTimeout(E.poll);
  const tick = async () => {
    let st;
    try { st = await api(`/api/timeline/${S.pid}/export`); } catch (e) { E.poll = setTimeout(tick, 1500); return; }
    const t = st.task;
    const bar = $("expBar");
    if (t.status === "running") {
      if (bar) {
        bar.style.width = (t.pct || 0) + "%";
        $("expPct").textContent = Math.round(t.pct || 0) + " %";
        $("expMsg").textContent = t.message || "";
      }
      E.poll = setTimeout(tick, 600);
      return;
    }
    if (!$("expBox")) {
      // fenêtre fermée pendant l'export : un mot quand c'est fini
      if (t.status === "done") toast("Export terminé : " + (st.export || {}).output, 6000);
      else if (t.status === "error") toast("Export impossible : " + t.message, 8000);
      return;
    }
    if (t.status === "done") { E.last = st.export; result(st.export); }
    else if (t.status === "cancelled") settings();
    else if (t.status === "error") failed(t.message);
  };
  tick();
}

async function cancel() {
  try { await post(`/api/timeline/${S.pid}/export/cancel`); } catch (e) { /* déjà fini */ }
  toast("Export annulé.");
}

function failed(msg) {
  title("Export impossible");
  const box = $("expBox");
  box.innerHTML = "";
  put(box,
    h("div.notice", { html: svg("warn", 15) + "<span></span>" }),
    h("div.mf", { style: { padding: "12px 0 0" } },
      h("button.btn", { onclick: settings }, "Réglages"),
      h("button.btn.primary", { onclick: start }, "Réessayer")));
  box.querySelector(".notice span").textContent = msg;
}

function result(res) {
  title(res.audio_only ? "Son exporté" : "Vidéo exportée");
  const box = $("expBox");
  box.innerHTML = "";
  const stale = res.signature && res.signature !== signature();
  const url = `/api/timeline/${S.pid}/export/file?v=${res.at || 0}`;
  put(box,
    stale ? h("div.notice", { html: svg("warn", 15) +
      "<span>Le montage a changé depuis cet export : réexporte pour appliquer tes modifications.</span>" }) : null,
    res.audio_only ? h("audio", { src: url, controls: true }) : h("video", { src: url, controls: true, preload: "metadata" }),
    h("div", { style: { marginTop: "10px" } },
      h("div.kv", {}, h("span", {}, "Durée"), h("b", {}, fmt(res.duration))),
      res.audio_only ? null : h("div.kv", {}, h("span", {}, "Définition"), h("b", {}, `${res.width}×${res.height} · ${res.fps} i/s`)),
      h("div.kv", {}, h("span", {}, "Taille"), h("b", {}, mo(res.size || 0))),
      h("div.path", {}, res.output)),
    h("div.mf", { style: { padding: "14px 0 0", flexWrap: "wrap" } },
      h("button.btn", { html: svg("folder", 14) + "Ouvrir le dossier", onclick: async () => {
        try { await post(`/api/timeline/${S.pid}/export/reveal`); } catch (e) { toast(e.message); }
      } }),
      h("a.btn", { href: url.replace("?", "?dl=1&"), download: "", html: svg("down", 14) + "Télécharger" }),
      h("button.btn" + (stale ? ".primary" : ""), { html: svg("refresh", 14) + "Réexporter", onclick: settings })));
}
