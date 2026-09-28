/* Traitement de la voix : presets, chaîne d'aperçu (Web Audio), libellés.

   Les réglages vivent dans `clip.audio_fx` (mêmes clés que VOICE_FX côté
   moteur) : intensités 0..1 (bruit, de-esser, compression, clarté, chaleur) et
   interrupteurs (coupe-bas, porte, niveau constant). L'export les rend avec
   ffmpeg (RNNoise, de-esser, compresseur, égaliseur…) ; l'aperçu en joue une
   approximation fidèle pour ce que Web Audio sait faire — coupe-bas, clarté,
   chaleur, sifflantes, compression. Bruit, porte et niveau constant ne
   s'entendent qu'à l'export. */

import { post } from "./api.js";
import { S, edit } from "./store.js";

export const KEYS = ["denoise", "lowcut", "gate", "deess", "compress", "clarity", "warmth", "level"];

export const PRESETS = [
  { name: "brut", label: "Brut", hint: "aucun traitement", fx: {} },
  { name: "clair", label: "Clair", hint: "nette et légère", fx: { lowcut: true, clarity: 0.6, compress: 0.4, deess: 0.3 } },
  { name: "voixoff", label: "Voix off", hint: "propre, présente, régulière",
    fx: { denoise: 0.7, lowcut: true, gate: true, deess: 0.4, compress: 0.6, clarity: 0.5, level: true } },
  { name: "podcast", label: "Podcast", hint: "chaude et posée",
    fx: { denoise: 0.5, lowcut: true, deess: 0.4, compress: 0.6, clarity: 0.4, warmth: 0.4, level: true } },
  { name: "radio", label: "Radio", hint: "dense, très présente",
    fx: { lowcut: true, deess: 0.4, compress: 0.9, clarity: 0.7, warmth: 0.5, level: true } },
];

export const SLIDERS = [
  ["denoise", "Réduction de bruit", "Souffle, ventilation, pièce (RNNoise, à l'export)"],
  ["clarity", "Clarté", "Moins de boue, plus de présence et d'air"],
  ["compress", "Compression", "Voix régulière, plus près du micro"],
  ["deess", "De-esser", "Adoucit les sifflantes (s, ch)"],
  ["warmth", "Chaleur", "Un peu plus de corps dans les graves"],
];
export const SWITCHES = [
  ["lowcut", "Coupe-bas", "Retire ronflement, souffle grave et pops"],
  ["gate", "Porte anti-bruit", "Silence entre les phrases (à l'export)"],
  ["level", "Niveau constant", "Même volume du début à la fin (à l'export)"],
];

/** Réglages d'un preset, prêts à poser sur un clip. */
export function withPreset(name) {
  const p = PRESETS.find((x) => x.name === name) || PRESETS[0];
  return Object.keys(p.fx).length ? { ...p.fx, preset: p.name } : {};
}

/** Réglages mesurés par « Optimiser le son » : un preset à part, propre à chaque rush. */
export const AUTO = { name: "auto", label: "Optimisé", hint: "mesuré sur ton son : niveau, bruit, dynamique, timbre" };

/** Le preset dont `fx` a exactement les valeurs, ou null (réglages personnalisés). */
export function presetOf(fx) {
  const f = fx || {};
  if (f.preset === "auto") return AUTO;
  return PRESETS.find((p) => KEYS.every((k) => norm(p.fx[k]) === norm(f[k]))) || null;
}
const norm = (v) => (typeof v === "boolean" ? (v ? 1 : 0) : Math.round((+v || 0) * 100) / 100);

/** Clé de comparaison : la chaîne d'aperçu ne se reconstruit que si elle change. */
export function fxKey(fx) {
  const f = fx || {};
  return KEYS.map((k) => norm(f[k])).join(",") + ":" + (+f.gain || 0);
}

export const active = (fx) => KEYS.some((k) => norm((fx || {})[k]) > 0);

/** Chaîne Web Audio de l'aperçu, ou null si rien n'est à entendre. */
export function buildChain(ctx, fx) {
  const f = fx || {};
  const nodes = [];
  const biquad = (type, frequency, gain = 0, Q = 1) => {
    const n = ctx.createBiquadFilter();
    n.type = type;
    n.frequency.value = frequency;
    n.gain.value = gain;
    n.Q.value = Q;
    nodes.push(n);
  };
  const gain = +f.gain || 0;
  if (Math.abs(gain) >= 0.1) {                 // niveau de travail mesuré (« Optimiser le son »)
    const g = ctx.createGain();
    g.gain.value = Math.pow(10, gain / 20);
    nodes.push(g);
  }
  if (f.lowcut) biquad("highpass", 80, 0, 0.7);
  const cl = +f.clarity || 0;
  if (cl > 0) {
    biquad("peaking", 220, -3 * cl, 1.1);
    biquad("peaking", 3000, 4 * cl, 1.2);
    biquad("highshelf", 7000, 2 * cl);
  }
  const wa = +f.warmth || 0;
  if (wa > 0) biquad("lowshelf", 180, 4 * wa);
  const de = +f.deess || 0;
  if (de > 0) biquad("peaking", 6500, -5 * de, 2);
  const co = +f.compress || 0;
  if (co > 0) {
    const c = ctx.createDynamicsCompressor();
    c.threshold.value = -12 - 10 * co;
    c.ratio.value = 1 + 4 * co;
    c.attack.value = 0.008;
    c.release.value = 0.15;
    c.knee.value = 4;
    nodes.push(c);
    const makeup = ctx.createGain();
    makeup.gain.value = 1 + 2.5 * co;
    nodes.push(makeup);
  }
  if (!nodes.length) return null;
  for (let i = 0; i + 1 < nodes.length; i++) nodes[i].connect(nodes[i + 1]);
  return { first: nodes[0], last: nodes[nodes.length - 1], nodes };
}

/* ------------------------------------------------------ optimiser le son

   Le moteur mesure le son de chaque rush (niveau de la voix, bruit de fond,
   dynamique, sifflantes, timbre, saturation ; niveau des musiques) et renvoie
   les réglages : voix au niveau de travail, bruit réduit à sa mesure, porte
   calée sur le bruit, compression, de-esser, clarté ; musique de fond sous la
   voix ; export à -14 LUFS (engine/timeline/sound.py). */

/** Plan du moteur pour les clips donnés (ceux du montage par défaut). */
export async function soundPlan(clips = S.doc.clips) {
  return post(`/api/timeline/${S.pid}/sound/optimize`, { clips });
}

/** Mesure puis règle le son (clips `ids`, sinon tout le montage) : une étape d'annulation. */
export async function optimize(ids = null) {
  const plan = await soundPlan(ids ? S.doc.clips.filter((c) => ids.has(c.id)) : S.doc.clips);
  const n = edit((doc) => applyPlan(doc, plan, ids), "sound");
  return { plan, n };
}

/** Pose un plan sur un document (voix par média : un clip coupé depuis garde son réglage). */
export function applyPlan(doc, plan, ids = null) {
  let n = 0;
  for (const c of doc.clips) {
    if (ids && !ids.has(c.id)) continue;
    const fx = (c.kind === "video" || c.kind === "audio") ? plan.voice_media[c.media] : null;
    if (fx && !(c.kind === "video" && c.detached)) { c.audio_fx = { ...fx }; n++; }
    if (plan.volume[c.id] !== undefined) { c.volume = plan.volume[c.id]; n++; }
  }
  if (plan.loudness) doc.settings = { ...(doc.settings || {}), loudness: true };
  return n;
}

/** Une ligne par rush mesuré, pour le compte rendu. */
export function describe(entry) {
  if (entry.error) return `${entry.name} : son illisible (${entry.error})`;
  if (entry.kind === "music") {
    return `${entry.name} : musique${entry.volume !== undefined ? `, baissée à ${Math.round(entry.volume * 100)} % sous la voix` : ""}`;
  }
  const fx = entry.fx || {};
  const bits = [];
  const g = fx.gain || 0;
  bits.push(Math.abs(g) >= 1 ? `voix ${g > 0 ? "remontée" : "baissée"} de ${Math.abs(g)} dB` : "voix au bon niveau");
  const snr = entry.snr;
  bits.push(snr >= 42 ? "très peu de bruit" : snr >= 32 ? `bruit léger (réduit ${Math.round((fx.denoise || 0) * 100)} %)`
    : `bruit de fond marqué (réduit ${Math.round((fx.denoise || 0) * 100)} %${fx.gate ? ", porte" : ""})`);
  if (fx.declip) bits.push("saturation réparée");
  if (fx.deess) bits.push(`sifflantes adoucies ${Math.round(fx.deess * 100)} %`);
  bits.push(`compression ${Math.round((fx.compress || 0) * 100)} %`);
  if (fx.clarity >= 0.6) bits.push("voix sourde : clarté renforcée");
  if (fx.warmth) bits.push("voix fine : chaleur ajoutée");
  if (fx.level) bits.push("niveau régulé");
  return `${entry.name} : ${bits.join(", ")}`;
}
