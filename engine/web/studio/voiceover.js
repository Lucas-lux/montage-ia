/* Voix off : enregistre le micro à la tête de lecture, comme dans CapCut.

   Choix du micro parmi ceux du PC, vumètre, compte à rebours, lecture du
   montage pendant la prise (les autres sons peuvent se taire pour ne pas
   rentrer dans le micro), puis la prise devient un clip sur une piste
   « Voix off », déjà traitée (preset choisi). La voix se règle ensuite dans
   l'inspecteur (section Voix).

   Le navigateur enregistre en PCM quand il sait (sinon Opus haut débit) ; le
   moteur convertit en wav 48 kHz mono et prépare le média comme un import. */

import { api } from "./api.js";
import { addMediaViews, startPolling } from "./bin.js";
import * as M from "./model.js";
import { P, pause, play } from "./player.js";
import { S, edit, on, select, setTime } from "./store.js";
import { $, fmt, h, modal, sec, svg, toast } from "./util.js";
import { PRESETS, withPreset } from "./voice.js";

const V = {
  open: false, stream: null, ctx: null, analyser: null, raf: 0, rec: null, chunks: [],
  state: "idle",           // idle | count | rec | upload
  t0: 0,                   // instant timeline du début de la prise
  started: 0,              // performance.now() au début de la prise
  devices: [], deviceId: "",
  playAlong: true, muteOthers: true, preset: "voixoff",
  timer: 0,
};

export function init() {
  try {
    V.deviceId = localStorage.getItem("studio.mic") || "";
    const o = JSON.parse(localStorage.getItem("studio.voiceover") || "{}");
    if (typeof o.playAlong === "boolean") V.playAlong = o.playAlong;
    if (typeof o.muteOthers === "boolean") V.muteOthers = o.muteOthers;
    if (PRESETS.some((p) => p.name === o.preset)) V.preset = o.preset;
  } catch (e) { /* rien */ }
  on("tool", ({ name }) => { if (name === "voiceover") open(); });
}

function remember() {
  try {
    localStorage.setItem("studio.mic", V.deviceId);
    localStorage.setItem("studio.voiceover", JSON.stringify({ playAlong: V.playAlong, muteOthers: V.muteOthers, preset: V.preset }));
  } catch (e) { /* rien */ }
}

/* ================================================================= panneau */

export async function open() {
  if (V.open) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast("Ce navigateur ne donne pas accès au micro : ouvre Montage IA dans Chrome, Edge, Firefox ou Safari à jour.", 6000);
    return;
  }
  V.open = true;
  if (P.playing) pause();
  V.t0 = S.t;
  const body = h("div.vo", {},
    h("div.field", {}, h("div.head", {}, h("span.label", {}, "Micro"),
        h("button.btn.sm.icon.quiet", { title: "Actualiser", html: svg("refresh", 12), onclick: () => listDevices(true) })),
      h("select", { id: "voMic", onchange: (e) => { V.deviceId = e.target.value; remember(); listen(); } })),
    h("div.vometer", {}, h("i", { id: "voLevel" })),
    h("div.meta", { id: "voHint", style: { marginTop: "6px" } }, "Parle : le vumètre doit bouger."),
    h("div.g2", { style: { marginTop: "12px" } },
      h("label.check", {}, h("input", { type: "checkbox", checked: V.playAlong,
        onchange: (e) => { V.playAlong = e.target.checked; remember(); } }), "Lire le montage pendant la prise"),
      h("label.check", {}, h("input", { type: "checkbox", checked: V.muteOthers,
        onchange: (e) => { V.muteOthers = e.target.checked; remember(); } }), "Couper les autres sons")),
    h("div.field", { style: { marginTop: "10px" } }, h("div.head", {}, h("span.label", {}, "Traitement de la voix")),
      h("select", { onchange: (e) => { V.preset = e.target.value; remember(); } },
        PRESETS.map((p) => h("option", { value: p.name, selected: p.name === V.preset }, p.label + " — " + p.hint)))),
    h("div.vorec", {},
      h("button.vobtn", { id: "voBtn", title: "Enregistrer", onclick: toggleRec }, h("i")),
      h("div", {},
        h("div.votime", { id: "voTime" }, "0:00,0"),
        h("div.meta", { id: "voWhere" }, "À la tête de lecture : " + fmt(V.t0)))),
    h("div.hint", { style: { marginTop: "8px" } },
      "3, 2, 1 puis ça enregistre ; clique à nouveau pour arrêter. La prise arrive sur une piste « Voix off », " +
      "déjà traitée ; tout se règle ensuite dans l'inspecteur (Voix)."));
  V.modal = modal({ title: "Voix off", body, width: 400, onclose: close });
  V.modal.el.classList.add("side");            // carte flottante : la vidéo reste visible
  await listen();
}

// la tête de lecture bouge pendant que la carte est ouverte : la prise suivra
on("time", () => {
  if (!V.open || V.state !== "idle") return;
  V.t0 = S.t;
  const where = $("voWhere");
  if (where) where.textContent = "À la tête de lecture : " + fmt(V.t0);
});

function close() {
  V.open = false;
  clearTimeout(V.timer);
  cancelAnimationFrame(V.raf);
  if (V.rec && V.rec.state !== "inactive") { try { V.rec.stop(); } catch (e) { /* rien */ } }
  if (V.state === "rec") stopPlayback();
  V.state = "idle";
  stopStream();
}

function stopStream() {
  if (V.stream) V.stream.getTracks().forEach((t) => t.stop());
  V.stream = null;
  if (V.ctx) { V.ctx.close().catch(() => {}); V.ctx = null; }
  V.analyser = null;
}

/* ----------------------------------------------------------------- micro */

async function listDevices(refresh = false) {
  try { V.devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput"); }
  catch (e) { V.devices = []; }
  const sel = $("voMic");
  if (!sel) return;
  sel.innerHTML = "";
  if (!V.devices.length) {
    sel.appendChild(h("option", { value: "" }, "Aucun micro trouvé"));
    return;
  }
  if (!V.devices.some((d) => d.deviceId === V.deviceId)) V.deviceId = V.devices[0].deviceId;
  V.devices.forEach((d, i) => sel.appendChild(h("option", { value: d.deviceId, selected: d.deviceId === V.deviceId },
    d.label || `Micro ${i + 1}`)));
  if (refresh) listen();
}

/** Ouvre le micro choisi et anime le vumètre. */
async function listen() {
  stopStream();
  const hint = $("voHint");
  try {
    const audio = { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 };
    if (V.deviceId) audio.deviceId = { exact: V.deviceId };
    V.stream = await navigator.mediaDevices.getUserMedia({ audio });
  } catch (e) {
    if (hint) hint.textContent = e.name === "NotAllowedError"
      ? "Accès au micro refusé : autorise-le dans la barre d'adresse, puis actualise."
      : "Micro indisponible : " + e.message;
    await listDevices();
    return;
  }
  // les libellés ne sont connus qu'une fois le micro autorisé
  await listDevices();
  const track = V.stream.getAudioTracks()[0];
  if (track && track.getSettings().deviceId && !V.deviceId) V.deviceId = track.getSettings().deviceId;
  try {
    V.ctx = new (window.AudioContext || window.webkitAudioContext)();
    V.analyser = V.ctx.createAnalyser();
    V.analyser.fftSize = 1024;
    V.ctx.createMediaStreamSource(V.stream).connect(V.analyser);
  } catch (e) { V.analyser = null; }
  if (hint) hint.textContent = track ? `« ${track.label || "micro"} » à l'écoute. Parle : le vumètre doit bouger.` : "";
  meter();
}

function meter() {
  cancelAnimationFrame(V.raf);
  const bar = $("voLevel");
  if (!bar || !V.analyser) return;
  const buf = new Float32Array(V.analyser.fftSize);
  let peak = 0;
  const tick = () => {
    if (!V.open || !V.analyser) return;
    V.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    const db = 20 * Math.log10(rms || 1e-6);
    const level = Math.max(0, Math.min(1, (db + 50) / 50));     // −50 dB → 0, 0 dB → 1
    peak = Math.max(level, peak * 0.92);
    bar.style.width = Math.round(peak * 100) + "%";
    bar.classList.toggle("hot", peak > 0.92);
    V.raf = requestAnimationFrame(tick);
  };
  tick();
}

/* ---------------------------------------------------------------- prise */

function toggleRec() {
  if (V.state === "idle") start();
  else if (V.state === "count") { clearTimeout(V.timer); V.state = "idle"; setBtn("idle"); }
  else if (V.state === "rec") stop();
}

function setBtn(state, text) {
  const b = $("voBtn");
  if (!b) return;
  b.className = "vobtn " + state;
  b.title = state === "idle" ? "Enregistrer" : "Arrêter";
  b.innerHTML = state === "count" ? `<b>${text}</b>` : "<i></i>";
}

async function start() {
  if (!V.stream) { toast("Choisis un micro d'abord."); return; }
  V.t0 = S.t;
  const where = $("voWhere");
  if (where) where.textContent = "À la tête de lecture : " + fmt(V.t0);
  V.state = "count";
  let n = 3;
  const step = () => {
    if (V.state !== "count") return;
    if (n === 0) { record(); return; }
    setBtn("count", String(n));
    n--;
    V.timer = setTimeout(step, 700);
  };
  step();
}

function mimeType() {
  // Chrome/Edge : PCM ou Opus en webm ; Firefox : Opus en ogg ; Safari : AAC en mp4
  const list = ["audio/webm;codecs=pcm", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return list.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}

function record() {
  if (!V.stream) return;
  const mime = mimeType();
  try {
    V.rec = new MediaRecorder(V.stream, mime ? { mimeType: mime, audioBitsPerSecond: 256000 } : undefined);
  } catch (e) {
    toast("Enregistrement impossible : " + e.message, 5000);
    V.state = "idle";
    setBtn("idle");
    return;
  }
  V.chunks = [];
  V.rec.ondataavailable = (e) => { if (e.data && e.data.size) V.chunks.push(e.data); };
  V.rec.onstop = () => finish();
  V.rec.start(250);
  V.state = "rec";
  V.started = performance.now();
  setBtn("rec");
  if (V.playAlong) {
    P.silent = V.muteOthers;
    setTime(V.t0, { from: "voiceover" });
    play();
  }
  const timeEl = $("voTime");
  const tick = () => {
    if (V.state !== "rec") return;
    if (timeEl) timeEl.textContent = clock((performance.now() - V.started) / 1000);
    V.timer = setTimeout(tick, 100);
  };
  tick();
}

const clock = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")},${Math.floor((t * 10) % 10)}`;

function stopPlayback() {
  P.silent = false;
  if (P.playing) pause();
}

function stop() {
  if (V.state !== "rec") return;
  clearTimeout(V.timer);
  V.state = "upload";
  V.elapsed = (performance.now() - V.started) / 1000;
  stopPlayback();
  setTime(V.t0, { from: "voiceover" });
  try { V.rec.stop(); } catch (e) { finish(); }
}

async function finish() {
  if (V.state !== "upload") return;
  const blob = new Blob(V.chunks, { type: (V.rec && V.rec.mimeType) || "audio/webm" });
  V.chunks = [];
  if (V.elapsed < 0.3 || blob.size < 200) {
    toast("Prise trop courte.");
    V.state = "idle";
    setBtn("idle");
    return;
  }
  const hint = $("voHint");
  if (hint) hint.textContent = "Envoi de la prise…";
  const n = [...S.media.values()].filter((m) => /^Voix off/i.test(m.name)).length + 1;
  const name = `Voix off ${n}`;
  try {
    const view = await api(`/api/timeline/${S.pid}/media/record?name=${encodeURIComponent(name)}`,
      { method: "POST", body: blob, headers: { "Content-Type": "application/octet-stream" } });
    addMediaViews([view]);
    startPolling();
    if (hint) hint.textContent = "Préparation…";
    const media = await ready(view.id, V.elapsed);
    const clip = edit((doc) => placeVoice(doc, media, V.t0, V.preset), "voiceover");
    select([clip.id]);
    toast(`Voix off ajoutée (${sec(clip.dur)}), preset « ${(PRESETS.find((p) => p.name === V.preset) || PRESETS[0]).label} ». ` +
          "Réglages dans l'inspecteur, section Voix.", 5000);
    V.state = "idle";
    if (V.modal) V.modal.close();
  } catch (e) {
    toast("Voix off impossible : " + e.message, 6000);
    V.state = "idle";
    setBtn("idle");
    if (hint) hint.textContent = "";
  }
}

/** Attend que le média soit prêt (durée exacte) ; sinon, la durée mesurée. */
async function ready(mid, elapsed) {
  for (let i = 0; i < 60; i++) {
    const m = S.media.get(mid);
    if (m && m.status === "ready" && m.duration > 0) return m;
    if (m && m.status === "error") break;
    await new Promise((r) => setTimeout(r, 250));
  }
  const m = S.media.get(mid) || { id: mid, kind: "audio", has_audio: true, name: "Voix off" };
  return { ...m, duration: m.duration || elapsed };
}

/** Pose la prise sur une piste « Voix off » (libre, sinon une autre piste audio). */
export function placeVoice(doc, media, at, preset) {
  const c = M.newMediaClip(media, { start: at, kind: "audio" });
  let tr = doc.tracks.find((t) => t.kind === "audio" && t.name === "Voix off" && M.isFree(doc, t.id, c.start, c.dur));
  if (!tr) {
    const firstAudio = doc.tracks.findIndex((t) => t.kind === "audio");
    tr = doc.tracks.some((t) => t.kind === "audio" && t.name === "Voix off")
      ? M.freeTrack(doc, "audio", c.start, c.dur)
      : M.addTrack(doc, "audio", { name: "Voix off", at: firstAudio < 0 ? undefined : firstAudio });
  }
  c.track = tr.id;
  const fx = withPreset(preset);
  if (Object.keys(fx).length) c.audio_fx = fx;
  doc.clips.push(c);
  return c;
}

export const state = () => V.state;
