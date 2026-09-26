/* Panneau Sons : la bibliothèque d'effets sonores et « Mes sons ».

   La bibliothèque est synthétisée par le moteur (engine/tools/sfx.py) : chaque
   son est une recette — un moteur et ses réglages. « Créer un son » ouvre le
   même synthétiseur avec ses curseurs ; on peut aussi repartir d'un son de la
   bibliothèque, importer un fichier ou s'enregistrer au micro.

   Ajouter un son au montage le copie dans le projet (un média comme un
   autre) puis le pose à la tête de lecture, ou là où on l'a glissé. */

import { api, del, post, upload } from "./api.js";
import { addMediaViews, startPolling } from "./bin.js";
import { isDesktop, pickFiles } from "./desktop.js";
import * as M from "./model.js";
import { S, edit, on, select } from "./store.js";
import { $, fmt, h, menu, modal, put, svg, toast } from "./util.js";

const SN = { data: null, cat: "all", query: "", playing: null, audio: null };

export function init() {
  on("tab", ({ name }) => { if (name === "sounds") load(); });
}

async function load(force = false) {
  if (SN.data && !force) { render(); return; }
  try { SN.data = await api("/api/sounds"); }
  catch (e) { toast("Sons indisponibles : " + e.message); return; }
  render();
}

/* --------------------------------------------------------------- écoute */

function play(s, btn) {
  if (!SN.audio) SN.audio = new Audio();
  const a = SN.audio;
  if (SN.playing === s.id && !a.paused) { a.pause(); return; }
  a.src = s.url + (s.url.includes("?") ? "&" : "?") + "v=" + (s.dur || 0);
  a.currentTime = 0;
  a.play().catch(() => {});
  SN.playing = s.id;
  document.querySelectorAll(".sndrow .play.on").forEach((b) => { b.classList.remove("on"); b.innerHTML = svg("play", 13); });
  if (btn) { btn.classList.add("on"); btn.innerHTML = svg("pause", 13); }
  a.onended = a.onpause = () => {
    if (btn) { btn.classList.remove("on"); btn.innerHTML = svg("play", 13); }
    if (SN.playing === s.id) SN.playing = null;
  };
}

/* ------------------------------------------------------- vers le montage */

/** Copie le son dans le projet, attend qu'il soit prêt, puis le pose (à `t`,
 *  sur la piste `tid` si elle convient, sinon sur une piste audio libre). */
export async function soundToTimeline(sid, { t = S.t, tid = null } = {}) {
  let view;
  try { view = await post(`/api/timeline/${S.pid}/sounds/${sid}`); }
  catch (e) { toast("Ajout impossible : " + e.message); return; }
  addMediaViews([view]);
  startPolling();
  const m = await new Promise((resolve) => {
    const tick = (n = 0) => {
      const x = S.media.get(view.id);
      if (x && x.status === "ready") return resolve(x);
      if (!x || x.status === "error" || n > 150) return resolve(null);
      setTimeout(() => tick(n + 1), 150);
    };
    tick();
  });
  if (!m) { toast("Ce son n'a pas pu être préparé."); return; }
  const added = edit((doc) => {
    const tr = tid ? M.track(doc, tid) : null;
    const target = tr && tr.kind === "audio" && !tr.locked ? tr.id
      : M.freeTrack(doc, "audio", t, m.duration, {}).id;
    return M.placeMedia(doc, m, target, t);
  }, "add");
  select(added.map((c) => c.id));
  toast(`« ${m.name.replace(/\.wav$/, "")} » ajouté à ${fmt(t)}.`);
}

/* --------------------------------------------------------------- panneau */

const ICON = { synth: "wand", import: "file", record: "mic" };

function rows(list, mine) {
  return list.map((s) => {
    const btn = h("button.btn.icon.quiet.play", { title: "Écouter", html: svg("play", 13) });
    btn.onclick = (e) => { e.stopPropagation(); play(s, btn); };
    const row = h("div.sndrow", {
      draggable: "true", title: "Glisser sur la timeline, ou « + » pour l'ajouter à la tête de lecture",
      ondblclick: () => soundToTimeline(s.id),
      oncontextmenu: (e) => { e.preventDefault(); rowMenu(e, s, mine); },
      ondragstart: (e) => {
        e.dataTransfer.setData("application/x-montage-sound", s.id);
        e.dataTransfer.effectAllowed = "copy";
        S.dragSound = { id: s.id, dur: s.dur || (s.params && s.params.dur) || 1, label: s.label };
      },
      ondragend: () => { S.dragSound = null; },
    }, btn,
    mine ? h("i.src", { html: svg(ICON[s.source] || "note", 12), title: { synth: "créé", import: "importé", record: "enregistré" }[s.source] }) : null,
    h("span.nm.ell", {}, s.label),
    s.dur ? h("span.dur.num", {}, s.dur < 1 ? s.dur.toFixed(1).replace(".", ",") + " s" : fmt(s.dur)) : null,
    h("button.btn.icon.quiet", { title: "Ajouter à la tête de lecture", html: svg("plus", 13),
                                 onclick: (e) => { e.stopPropagation(); soundToTimeline(s.id); } }));
    return row;
  });
}

function rowMenu(e, s, mine) {
  menu(e.clientX, e.clientY, [
    { label: "Ajouter à la tête de lecture", icon: "plus", onclick: () => soundToTimeline(s.id) },
    s.engine ? { label: mine ? "Modifier" : "Créer une variante…", icon: "wand", onclick: () => creator(s) } : null,
    mine ? { label: "Renommer…", icon: "text", onclick: () => rename(s) } : null,
    mine ? "-" : null,
    mine ? { label: "Supprimer de mes sons", icon: "trash", onclick: () => remove(s) } : null,
  ]);
}

function render() {
  const box = $("tab-sounds");
  if (!box || !SN.data) return;
  const d = SN.data;
  box.innerHTML = "";
  const search = h("input", { type: "text", placeholder: "Rechercher un son…", value: SN.query, "aria-label": "Rechercher un son" });
  search.oninput = () => { SN.query = search.value.trim().toLowerCase(); renderList(); };
  search.onkeydown = (e) => e.stopPropagation();
  const cats = [["all", "Tous"], ["mine", "Mes sons"], ...Object.entries(d.categories)];
  const chips = h("div.chips.sndcats", {}, cats.map(([k, label]) => h("button.chip" + (SN.cat === k ? ".on" : ""), {
    onclick: () => { SN.cat = k; render(); },
  }, label + (k === "mine" && d.mine.length ? ` (${d.mine.length})` : ""))));
  const tools = h("div.sndtools", {},
    h("button.btn.primary", { html: svg("wand", 14) + "Créer un son", onclick: () => creator() }),
    h("button.btn", { html: svg("file", 13) + "Importer", title: "Un fichier son (ou le son d'une vidéo) dans Mes sons",
                      onclick: importSounds }),
    h("button.btn.icon", { html: svg("mic", 14), title: "S'enregistrer au micro", onclick: () => creator(null, "mic") }));
  put(box, tools, search, chips, h("div.sndlist", { id: "sndList" }));
  renderList();
}

function renderList() {
  const box = $("sndList");
  if (!box) return;
  const d = SN.data;
  const q = SN.query;
  const match = (s) => !q || s.label.toLowerCase().includes(q);
  box.innerHTML = "";
  const mine = d.mine.filter(match);
  if (SN.cat === "all" || SN.cat === "mine") {
    if (SN.cat === "mine" || mine.length) {
      box.appendChild(h("div.sndgroup", {}, "Mes sons"));
      if (!mine.length) {
        box.appendChild(h("div.hint", { style: { padding: "4px 2px 10px" } },
          "Crée un son avec le synthétiseur, importe un fichier ou enregistre-toi : ils arrivent ici."));
      }
      rows(mine, true).forEach((r) => box.appendChild(r));
    }
  }
  for (const [k, label] of Object.entries(d.categories)) {
    if (SN.cat !== "all" && SN.cat !== k) continue;
    const list = d.library.filter((s) => s.category === k && match(s));
    if (!list.length) continue;
    box.appendChild(h("div.sndgroup", {}, label));
    rows(list, false).forEach((r) => box.appendChild(r));
  }
  if (!box.children.length) box.appendChild(h("div.hint", { style: { padding: "10px 2px" } }, "Aucun son."));
}

/* ------------------------------------------------------------- mes sons */

async function importSounds() {
  const paths = await pickFiles("sound", true);
  if (paths === null) {
    const input = h("input", { type: "file", multiple: true, accept: "audio/*,video/*" });
    input.onchange = async () => {
      for (const f of input.files) {
        try { await upload(`/api/sounds/mine/upload?name=${encodeURIComponent(f.name)}`, f); }
        catch (e) { toast(e.message, 4000); }
      }
      SN.cat = "mine";
      load(true);
    };
    input.click();
    return;
  }
  if (!paths.length) return;
  try {
    const res = await post("/api/sounds/mine/paths", { paths });
    if (res.skipped.length) toast(res.skipped.map((s) => `${s.path.split(/[\\/]/).pop()} : ${s.reason}`).join(" · "), 5000);
    if (res.added.length) toast(`${res.added.length} son${res.added.length > 1 ? "s" : ""} ajouté${res.added.length > 1 ? "s" : ""} à Mes sons.`);
  } catch (e) { toast(e.message, 4000); }
  SN.cat = "mine";
  load(true);
}

function rename(s) {
  const input = h("input", { type: "text", value: s.label });
  modal({
    title: "Renommer le son", body: input,
    actions: [{ label: "Annuler" }, { label: "Renommer", primary: true, onclick: async () => {
      try { await api(`/api/sounds/mine/${s.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                                     body: JSON.stringify({ name: input.value }) }); }
      catch (e) { toast(e.message); return false; }
      load(true);
      return true;
    } }],
  });
}

async function remove(s) {
  if (!confirm(`Supprimer « ${s.label} » de Mes sons ?\nLes montages qui l'utilisent gardent leur copie.`)) return;
  try { await del(`/api/sounds/mine/${s.id}`); load(true); }
  catch (e) { toast(e.message); }
}

/* ------------------------------------------------------ créer un son */

/** Synthétiseur : un moteur, ses curseurs, « Écouter », « Au hasard », puis
 *  « Garder » dans Mes sons. Onglet micro : s'enregistrer. `from` : un son à
 *  reprendre (moteur et réglages). */
function creator(from = null, mode = "synth") {
  const engines = SN.data.engines;
  const st = {
    mode,
    engine: from ? from.engine : engines[0].name,
    params: from ? { ...from.params } : {},
    name: from ? (from.source ? from.label : from.label + " (variante)") : "",
    preview: null,
  };
  const body = h("div.sndmaker");
  const nameIn = h("input", { type: "text", placeholder: "Nom du son", value: st.name });
  nameIn.onkeydown = (e) => e.stopPropagation();
  let rec = null;

  const schema = () => engines.find((e) => e.name === st.engine);
  const listen = async () => {
    try {
      st.preview = await post("/api/sounds/preview", { engine: st.engine, params: st.params });
      play({ id: st.preview.id, url: st.preview.url, dur: st.preview.dur }, null);
    } catch (e) { toast(e.message); }
  };
  const randomize = () => {
    for (const p of schema().params) {
      if (p.key === "volume") continue;
      const r = Math.random();
      st.params[p.key] = p.key === "reverse" ? (r < 0.2 ? 1 : 0)
        : p.key === "echo" ? (r < 0.5 ? 0 : +(r * 0.6).toFixed(2))
        : p.key === "pitch" ? Math.round((r - 0.5) * 14)
        : +(p.min + (p.max - p.min) * Math.pow(r, 1.4)).toFixed(3);
    }
    draw();
    listen();
  };

  const draw = () => {
    body.innerHTML = "";
    const tabs = h("div.seg", { style: { marginBottom: "10px" } },
      h("button", { class: st.mode === "synth" ? "on" : "", onclick: () => { st.mode = "synth"; draw(); } }, "Synthétiseur"),
      h("button", { class: st.mode === "mic" ? "on" : "", onclick: () => { st.mode = "mic"; draw(); } }, "Micro"));
    put(body, tabs);
    if (st.mode === "mic") { drawMic(); return; }
    const kinds = h("div.chips", { style: { marginBottom: "10px" } }, engines.map((e) => h("button.chip" + (e.name === st.engine ? ".on" : ""), {
      onclick: () => { st.engine = e.name; st.params = {}; draw(); },
    }, e.label)));
    const sliders = h("div.sndparams", {}, schema().params.map((p) => {
      const val = st.params[p.key] ?? p.default;
      if (p.key === "reverse") {
        const c = h("input", { type: "checkbox", checked: val >= 0.5 });
        c.onchange = () => { st.params.reverse = c.checked ? 1 : 0; };
        return h("label.check", {}, c, p.label);
      }
      const step = p.max - p.min <= 2 ? 0.01 : p.max - p.min <= 30 ? 0.1 : 1;
      const out = h("b.num", {}, String(+(+val).toFixed(2)));
      const input = h("input", { type: "range", min: p.min, max: p.max, step, value: val });
      input.oninput = () => { st.params[p.key] = +input.value; out.textContent = String(+(+input.value).toFixed(2)); };
      input.onchange = listen;
      return h("div.field", {}, h("div.head", {}, h("span.label", {}, p.label), out), input);
    }));
    put(body, kinds, sliders,
      h("div.row", { style: { gap: "8px", margin: "10px 0" } },
        h("button.btn", { html: svg("play", 13) + "Écouter", onclick: listen }),
        h("button.btn", { html: svg("refresh", 13) + "Au hasard", onclick: randomize })),
      nameIn);
  };

  const drawMic = () => {
    const status = h("div.meta", { style: { margin: "6px 0 10px" } },
      "Enregistre un son au micro (20 s au plus) : il rejoint Mes sons.");
    const player = h("audio", { controls: true, style: { width: "100%", display: rec && rec.blob ? "" : "none" } });
    if (rec && rec.blob) player.src = URL.createObjectURL(rec.blob);
    const go = h("button.btn.primary", { html: svg("mic", 14) + (rec && rec.recorder ? "Arrêter" : "Enregistrer") });
    go.onclick = async () => {
      if (rec && rec.recorder) { rec.recorder.stop(); return; }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        const chunks = [];
        rec = { recorder, blob: null };
        recorder.ondataavailable = (ev) => chunks.push(ev.data);
        recorder.onstop = () => {
          stream.getTracks().forEach((t) => t.stop());
          rec = { recorder: null, blob: new Blob(chunks, { type: recorder.mimeType }) };
          draw();
        };
        recorder.start();
        setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, 20000);
        draw();
      } catch (e) { toast("Micro inaccessible : " + e.message, 5000); }
    };
    put(body, status, go, h("div", { style: { height: "10px" } }), player, h("div", { style: { height: "10px" } }), nameIn);
  };

  draw();
  if (mode === "synth" && from) listen();
  modal({
    title: from && from.source ? "Modifier le son" : "Créer un son",
    width: 560,
    body,
    actions: [
      { label: "Fermer" },
      { label: "Garder dans Mes sons", primary: true, onclick: async () => {
        const name = nameIn.value.trim() || (st.mode === "mic" ? "Enregistrement" : schema().label);
        try {
          if (st.mode === "mic") {
            if (!rec || !rec.blob) { toast("Enregistre d'abord un son."); return false; }
            await upload(`/api/sounds/mine/upload?name=${encodeURIComponent(name)}&source=record`, rec.blob);
          } else {
            await post("/api/sounds/mine", { name, engine: st.engine, params: st.params });
            if (from && from.source === "synth") await del(`/api/sounds/mine/${from.id}`);
          }
        } catch (e) { toast(e.message, 4000); return false; }
        toast(`« ${name} » est dans Mes sons.`);
        SN.cat = "mine";
        load(true);
        return true;
      } },
    ],
  });
}

export { load, isDesktop };
