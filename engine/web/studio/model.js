/* Logique pure du montage : aucune dépendance au DOM, testée par `node --test`.

   Toutes les fonctions reçoivent le document (`doc` : tracks, clips, canvas…)
   et le modifient EN PLACE ; l'appelant se charge de l'historique (store.edit).

   Règles :
     - un clip est { id, track, kind, start, dur, … } ; les clips média ont
       aussi `media`, `in` (point d'entrée dans la source) et `speed` ;
     - deux clips d'une même piste ne se chevauchent jamais ;
     - la piste principale est magnétique : ses clips restent collés à partir
       de 0, sans trous (`packMain`) ;
     - `link` regroupe des clips qui bougent ensemble (vidéo + son séparé).
       Couper un groupe à l'instant t donne à ses moitiés droites un même
       nouveau lien, dérivé de (lien, t) : les partenaires restent ensemble ;
     - les sous-titres automatiques gardent, mot par mot, leur place dans le
       média source (`m`, `s`, `e`) : `reflowCaptions` les recale après
       chaque coupe ou déplacement. */

export const MIN_DUR = 0.04;
export const HOLD_GAP = 0.8;          // mot à mot : pause au-delà de laquelle le mot disparaît (comme ai.py)
export const IMAGE_DUR = 3;
export const TEXT_DUR = 3;
const EPS = 1e-4;

let counter = 0;
export function uid(prefix = "c") {
  counter = (counter + 1) % 1296;
  return prefix + Date.now().toString(36).slice(-5) + counter.toString(36).padStart(2, "0") +
         Math.random().toString(36).slice(2, 5);
}

/** Nouveau lien, le même pour tous les partenaires coupés au même instant. */
export function deriveLink(link, key) {
  if (!link) return "";
  const s = link + "@" + key;
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return "g" + (h >>> 0).toString(36);
}

export const r4 = (v) => Math.round(v * 1e4) / 1e4;
export const clipEnd = (c) => c.start + c.dur;
export const srcEnd = (c) => (c.in || 0) + c.dur * (c.speed || 1);
export const isMediaClip = (c) => c.kind === "video" || c.kind === "audio" || c.kind === "image";
const hasSource = (c) => c.kind === "video" || c.kind === "audio";

export function trackKindFor(clipKind) {
  return clipKind === "audio" ? "audio" : clipKind === "text" ? "text" : "video";
}

/* ------------------------------------------------------------------ pistes */

export const track = (doc, id) => doc.tracks.find((t) => t.id === id) || null;
export const mainTrack = (doc) =>
  doc.tracks.find((t) => t.main) || doc.tracks.find((t) => t.kind === "video") || null;
export const isMain = (doc, tid) => { const m = mainTrack(doc); return !!m && m.id === tid; };

export function trackClips(doc, tid) {
  return doc.clips.filter((c) => c.track === tid).sort((a, b) => a.start - b.start);
}

export function duration(doc) {
  return doc.clips.reduce((m, c) => Math.max(m, clipEnd(c)), 0);
}

/** Nouvelle piste, rangée comme dans CapCut : texte en haut, vidéos de
 *  superposition au-dessus de la principale, audio en bas. */
export function addTrack(doc, kind, { name, at } = {}) {
  const n = doc.tracks.filter((t) => t.kind === kind).length + 1;
  const t = {
    id: uid("t"), kind, main: false, muted: false, hidden: false, locked: false,
    name: name || ({ video: "Vidéo", audio: "Audio", text: "Texte" }[kind] + " " + n),
  };
  let pos = at;
  if (pos === undefined) {
    if (kind === "text") pos = 0;
    else if (kind === "video") {
      // au-dessus de la plus haute piste vidéo, sous les pistes texte
      const firstVideo = doc.tracks.findIndex((x) => x.kind === "video");
      pos = firstVideo < 0 ? doc.tracks.filter((x) => x.kind === "text").length : firstVideo;
    } else pos = doc.tracks.length;
  }
  doc.tracks.splice(Math.max(0, Math.min(pos, doc.tracks.length)), 0, t);
  return t;
}

export function removeTrack(doc, tid) {
  const t = track(doc, tid);
  if (!t || t.main) return false;
  const gone = new Set(doc.clips.filter((c) => c.track === tid).map((c) => c.id));
  doc.tracks = doc.tracks.filter((x) => x.id !== tid);
  if (gone.size) deleteClips(doc, gone);
  return true;
}

/** Le créneau [start, start+dur[ de la piste est-il libre ? */
export function isFree(doc, tid, start, dur, ignore = new Set()) {
  const a = start, b = start + dur;
  return !doc.clips.some((c) => c.track === tid && !ignore.has(c.id) &&
                               c.start < b - EPS && clipEnd(c) > a + EPS);
}

/** Première piste du genre voulu où le créneau est libre (sinon une nouvelle). */
export function freeTrack(doc, kind, start, dur, { ignore, skipMain = false, create = true } = {}) {
  const ign = ignore || new Set();
  const list = doc.tracks.filter((t) => t.kind === kind && !t.locked && !(skipMain && t.main));
  // vidéo de superposition : la plus proche de la principale d'abord
  const ordered = kind === "video" ? list.slice().reverse() : list;
  const found = ordered.find((t) => isFree(doc, t.id, start, dur, ign));
  return found || (create ? addTrack(doc, kind) : null);
}

/* ------------------------------------------------------------------- clips */

export function newMediaClip(media, { track = "", start = 0, kind, dur, src = 0 } = {}) {
  const k = kind || (media.kind === "image" ? "image" : media.kind === "audio" ? "audio" : "video");
  const avail = media.kind === "image" ? Infinity : Math.max(MIN_DUR, (media.duration || 0) - src);
  const d = Math.min(dur || (media.kind === "image" ? IMAGE_DUR : avail), avail);
  const c = {
    id: uid("c"), track, kind: k, media: media.id, start: r4(Math.max(0, start)),
    dur: r4(d), in: r4(src), speed: 1, link: "",
  };
  if (hasSource(c)) Object.assign(c, { volume: 1, muted: false, fade_in: 0, fade_out: 0 });
  if (k === "video") c.detached = false;
  if (k === "video" || k === "image") {
    Object.assign(c, { x: 0.5, y: 0.5, scale: 1, rotation: 0, opacity: 1, fit: "cover",
                       flip_h: false, flip_v: false });
  }
  return c;
}

/** Partenaires liés d'un clip (hors lui-même). */
export const partners = (doc, c) => (c.link ? doc.clips.filter((o) => o !== c && o.link === c.link) : []);

/** Décale un clip de la piste principale et ses partenaires (qui, eux, ne sont
 *  jamais sur la principale : elle se recolle d'elle-même). */
function shiftWithPartners(doc, c, delta) {
  if (Math.abs(delta) < EPS) return;
  c.start = r4(Math.max(0, c.start + delta));
  partners(doc, c).forEach((o) => {
    if (!isMain(doc, o.track)) o.start = r4(Math.max(0, o.start + delta));
  });
}

/** Bord de clip de la piste principale le plus proche de `t`. */
export function nearestBoundary(doc, t) {
  const main = mainTrack(doc);
  let best = 0, bestD = Math.abs(t);
  for (const c of trackClips(doc, main.id)) {
    for (const p of [c.start, clipEnd(c)]) {
      const d = Math.abs(p - t);
      if (d < bestD) { best = p; bestD = d; }
    }
  }
  return best;
}

/** Recolle les clips de la piste principale à partir de 0, dans l'ordre de
 *  leurs débuts ; les partenaires liés suivent le même décalage. */
export function packMain(doc) {
  const main = mainTrack(doc);
  if (!main) return;
  let at = 0;
  for (const c of trackClips(doc, main.id)) {
    shiftWithPartners(doc, c, at - c.start);
    at = r4(at + c.dur);
  }
  fixOverlaps(doc);
}

/** Insère des clips sur la piste principale au bord le plus proche de `at`,
 *  en poussant les suivants (et leurs partenaires). */
export function insertMain(doc, clips, at) {
  const main = mainTrack(doc);
  const t = nearestBoundary(doc, at);
  const total = clips.reduce((s, c) => s + c.dur, 0);
  trackClips(doc, main.id).forEach((c) => { if (c.start >= t - EPS) shiftWithPartners(doc, c, total); });
  let cur = t;
  clips.forEach((c) => {
    c.track = main.id;
    c.start = r4(cur);
    cur += c.dur;
    doc.clips.push(c);
  });
  packMain(doc);
  return clips;
}

/** Bouton « + » du panneau Médias. Vidéo/image : piste principale, au bord le
 *  plus proche de la tête de lecture. Audio : à la tête de lecture, première
 *  piste audio libre. */
export function appendMedia(doc, media, t = 0) {
  if (media.kind === "audio") {
    const c = newMediaClip(media, { start: t, kind: "audio" });
    c.track = freeTrack(doc, "audio", c.start, c.dur).id;
    doc.clips.push(c);
    return [c];
  }
  return insertMain(doc, [newMediaClip(media)], t);
}

/** Clip déposé à un endroit précis (glisser depuis les médias). Piste
 *  principale : insertion magnétique. Ailleurs : au créneau demandé, ou sur une
 *  autre piste libre du bon genre si ça chevauche. */
export function placeMedia(doc, media, tid, start) {
  const tr = track(doc, tid);
  let kind;
  if (media.kind === "image") kind = "image";
  else if (media.kind === "audio") kind = "audio";
  else if (tr && tr.kind === "audio" && media.has_audio) kind = "audio";   // le son seul
  else kind = "video";
  const c = newMediaClip(media, { start, kind });
  const tkind = trackKindFor(kind);
  if (tr && tr.main && tkind === "video") return insertMain(doc, [c], start);
  let target = tr && tr.kind === tkind && !tr.locked && !tr.main ? tr : null;
  if (!target && tkind === "video" && !trackClips(doc, mainTrack(doc).id).length) {
    return insertMain(doc, [c], 0);           // timeline vide : la principale d'abord
  }
  if (!target || !isFree(doc, target.id, c.start, c.dur)) {
    target = freeTrack(doc, tkind, c.start, c.dur, { skipMain: true });
  }
  c.track = target.id;
  doc.clips.push(c);
  return [c];
}

/* ------------------------------------------------------------------ liens */

/** Ajoute à `ids` tous les clips liés aux clips désignés. */
export function linkedIds(doc, ids) {
  const set = new Set(ids);
  const links = new Set(doc.clips.filter((c) => set.has(c.id) && c.link).map((c) => c.link));
  doc.clips.forEach((c) => { if (c.link && links.has(c.link)) set.add(c.id); });
  return set;
}

export function unlink(doc, ids) {
  const links = new Set(doc.clips.filter((c) => ids.has(c.id) && c.link).map((c) => c.link));
  doc.clips.forEach((c) => { if (links.has(c.link)) c.link = ""; });
}

/** Sépare le son d'un clip vidéo : un clip audio lié, même place, même vitesse. */
export function detachAudio(doc, clip) {
  if (clip.kind !== "video" || clip.detached) return null;
  const link = clip.link || uid("g");
  const a = {
    id: uid("c"), track: "", kind: "audio", media: clip.media, start: clip.start, dur: clip.dur,
    in: clip.in, speed: clip.speed || 1, volume: clip.volume ?? 1, muted: !!clip.muted,
    fade_in: clip.fade_in || 0, fade_out: clip.fade_out || 0, link,
  };
  clip.link = link;
  clip.detached = true;
  a.track = freeTrack(doc, "audio", a.start, a.dur).id;
  doc.clips.push(a);
  return a;
}

/** Rend le son à la vidéo : le clip audio lié du même média disparaît. */
export function reattachAudio(doc, clip) {
  if (clip.kind !== "video" || !clip.detached) return false;
  if (clip.link) {
    doc.clips = doc.clips.filter((c) => !(c.kind === "audio" && c.link === clip.link &&
                                          c.media === clip.media));
    if (!partners(doc, clip).length) clip.link = "";
  }
  clip.detached = false;
  return true;
}

/* ------------------------------------------------------------------ diviser */

/** Coupe `clip` à l'instant `t` (timeline). Renvoie le morceau de droite. */
export function splitClip(doc, clip, t) {
  if (t <= clip.start + MIN_DUR || t >= clipEnd(clip) - MIN_DUR) return null;
  const left = t - clip.start;
  const right = JSON.parse(JSON.stringify(clip));
  right.id = uid("c");
  right.start = r4(t);
  right.dur = r4(clip.dur - left);
  clip.dur = r4(left);
  if (hasSource(clip)) right.in = r4(clip.in + left * (clip.speed || 1));
  if ("fade_out" in clip) { right.fade_in = 0; clip.fade_out = 0; }   // fondus aux bouts d'origine
  delete right.trans;                  // la transition d'entrée reste au début du clip
  delete right.gap;                    // passage retiré avant le clip : à gauche,
  delete clip.tail;                    // celui d'après : à droite
  if (clip.kind === "text") {
    const words = clip.words || [];
    clip.words = words.filter((w) => w.start < t);
    right.words = words.filter((w) => w.start >= t);
    delete right.src_words;           // la traduction d'origine reste à gauche
  }
  right.link = deriveLink(clip.link, Math.round(t * 1000));
  doc.clips.push(right);
  return right;
}

/** Divise à `t` les clips visés (sinon tous ceux sous la tête de lecture, hors
 *  pistes verrouillées), partenaires liés compris. Renvoie les morceaux de
 *  droite. */
export function splitAt(doc, t, ids) {
  const base = ids && ids.size ? [...ids]
    : doc.clips.filter((c) => !(track(doc, c.track) || {}).locked).map((c) => c.id);
  const all = linkedIds(doc, base);
  return doc.clips.filter((c) => all.has(c.id) && c.start < t - MIN_DUR && clipEnd(c) > t + MIN_DUR)
    .map((c) => splitClip(doc, c, t)).filter(Boolean);
}

/** Divise à `t` et ne garde qu'un côté des clips visés (et de leurs
 *  partenaires liés), comme « Supprimer à gauche / à droite » dans CapCut.
 *  `keep` : "right" retire ce qui précède `t`, "left" ce qui suit. Sur la
 *  piste principale le trou se referme ; ailleurs le morceau gardé reste en
 *  place. Renvoie les morceaux gardés. */
export function splitKeep(doc, t, ids, keep) {
  const drop = new Set();
  const kept = [];
  const all = linkedIds(doc, [...ids]);
  for (const c of doc.clips.filter((x) => all.has(x.id) && x.start < t - MIN_DUR && clipEnd(x) > t + MIN_DUR)) {
    const right = splitClip(doc, c, t);
    if (!right) continue;
    if (keep === "right") {
      if (c.trans) right.trans = c.trans;     // la transition d'entrée suit le début gardé
      drop.add(c.id);
      kept.push(right);
    } else {
      drop.add(right.id);
      kept.push(c);
    }
  }
  if (drop.size) deleteClips(doc, drop);
  return kept;
}

/* ---------------------------------------------------------------- supprimer */

/** Supprime des clips. Sur la piste principale, le trou se referme. */
export function deleteClips(doc, ids) {
  const main = mainTrack(doc);
  const touchesMain = doc.clips.some((c) => ids.has(c.id) && main && c.track === main.id);
  doc.clips = doc.clips.filter((c) => !ids.has(c.id));
  // un lien qui ne relie plus rien s'efface (la vidéo dont on a supprimé le son
  // séparé reste muette, comme dans CapCut ; « Rattacher le son » la réveille)
  const counts = new Map();
  doc.clips.forEach((c) => c.link && counts.set(c.link, (counts.get(c.link) || 0) + 1));
  doc.clips.forEach((c) => { if (c.link && counts.get(c.link) < 2) c.link = ""; });
  if (touchesMain) packMain(doc);
}

/* ------------------------------------------------------ déplacer, rogner */

/** Rogne un clip (et ses partenaires) par un bord. `t` = position voulue du
 *  bord sur la timeline. Borné par la source, par les voisins (sauf sur la
 *  piste principale, qui se recolle) et par une durée minimale. Sur la
 *  principale, le début du clip ne bouge pas : c'est la suite qui se décale.
 *  Renvoie le décalage appliqué au bord. */
export function trimClip(doc, clip, side, t, mediaById = new Map()) {
  makeManual(clip);
  const group = [clip, ...partners(doc, clip)];
  const others = (g) => trackClips(doc, g.track).filter((c) => c !== g && !group.includes(c));
  let lo = -Infinity, hi = Infinity, delta;
  if (side === "l") {
    // delta > 0 : le début avance (le clip raccourcit)
    delta = t - clip.start;
    for (const g of group) {
      hi = Math.min(hi, g.dur - MIN_DUR);
      if (hasSource(g)) lo = Math.max(lo, -g.in / (g.speed || 1));
      if (!isMain(doc, g.track)) {
        // hors piste magnétique le début bouge vraiment : pas avant 0
        lo = Math.max(lo, -g.start);
        const prevEnd = Math.max(0, ...others(g).filter((c) => clipEnd(c) <= g.start + EPS).map(clipEnd));
        lo = Math.max(lo, prevEnd - g.start);
      }
    }
    delta = Math.min(hi, Math.max(lo, delta));
    for (const g of group) {
      if (hasSource(g)) g.in = r4(Math.max(0, g.in + delta * (g.speed || 1)));
      g.start = r4(g.start + delta);
      g.dur = r4(g.dur - delta);
    }
  } else {
    // delta > 0 : la fin recule (le clip s'allonge)
    delta = t - clipEnd(clip);
    for (const g of group) {
      lo = Math.max(lo, MIN_DUR - g.dur);
      const m = mediaById.get(g.media);
      if (hasSource(g) && m && m.duration) hi = Math.min(hi, (m.duration - g.in) / (g.speed || 1) - g.dur);
      if (!isMain(doc, g.track)) {
        const next = Math.min(Infinity, ...others(g).filter((c) => c.start >= clipEnd(g) - EPS).map((c) => c.start));
        hi = Math.min(hi, next - clipEnd(g));
      }
    }
    delta = Math.max(lo, Math.min(hi, delta));
    for (const g of group) g.dur = r4(g.dur + delta);
  }
  for (const g of group) {
    if ("fade_in" in g) {
      g.fade_in = Math.min(g.fade_in || 0, g.dur);
      g.fade_out = Math.min(g.fade_out || 0, g.dur);
    }
  }
  if (group.some((g) => isMain(doc, g.track))) {
    // piste magnétique : le début reste en place, la suite se recolle
    if (side === "l") group.forEach((g) => { g.start = r4(g.start - delta); });
    packMain(doc);
  }
  return delta;
}

/** Le déplacement de clips hors piste principale est-il possible sans
 *  chevauchement ? (`trackMap` : piste d'origine -> piste de destination). */
export function canMove(doc, ids, dt, trackMap = new Map()) {
  for (const c of doc.clips.filter((x) => ids.has(x.id))) {
    const tid = trackMap.get(c.track) || c.track;
    const tr = track(doc, tid);
    if (!tr || tr.locked || tr.kind !== trackKindFor(c.kind)) return false;
    if (c.start + dt < -EPS) return false;
    if (tr.main) continue;         // la principale se réorganise d'elle-même
    if (!isFree(doc, tid, c.start + dt, c.dur, ids)) return false;
  }
  return true;
}

export function moveClips(doc, ids, dt, trackMap = new Map()) {
  doc.clips.forEach((c) => {
    if (!ids.has(c.id)) return;
    const d = Math.max(0, c.start + dt) - c.start;
    c.start = r4(c.start + d);
    c.track = trackMap.get(c.track) || c.track;
    if (c.kind === "text") {
      makeManual(c);
      (c.words || []).forEach((w) => { w.start = r4(w.start + d); w.end = r4(w.end + d); });
    }
  });
}

/** Un sous-titre recalé à la main n'est plus lié à la voix : ses mots coupés
 *  disparaissent pour de bon et il garde désormais ses horaires. */
export function makeManual(c) {
  if (c.kind !== "text" || !c.auto) return;
  c.auto = false;
  c.words = (c.words || []).filter((w) => !w.cut).map((w) => ({ text: w.text, start: w.start, end: w.end }));
}

/** Réordonne la piste principale : `clip` s'insère là où tombe son milieu. */
export function reorderMain(doc, clip, desiredStart) {
  const main = mainTrack(doc);
  const others = trackClips(doc, main.id).filter((c) => c.id !== clip.id);
  const mid = desiredStart + clip.dur / 2;
  let idx = others.findIndex((c) => mid < c.start + c.dur / 2);
  if (idx < 0) idx = others.length;
  const order = others.slice(0, idx).concat([clip], others.slice(idx));
  let at = 0;
  const targets = order.map((c) => { const s = at; at += c.dur; return s; });
  order.forEach((c, i) => shiftWithPartners(doc, c, targets[i] - c.start));
  clip.track = main.id;
  packMain(doc);
}

/** Chevauchements restants (hors principale) : le clip fautif part sur une
 *  piste libre du même genre. */
export function fixOverlaps(doc) {
  for (const t of [...doc.tracks]) {
    if (t.main) continue;
    const row = trackClips(doc, t.id);
    let lastEnd = -Infinity;
    for (const c of row) {
      if (c.start < lastEnd - EPS) {
        c.track = freeTrack(doc, t.kind, c.start, c.dur, { ignore: new Set([c.id]), skipMain: true }).id;
      } else {
        lastEnd = clipEnd(c);
      }
    }
  }
}

/* --------------------------------------------------- dupliquer, coller */

export function cloneClips(clips) {
  const links = new Map();
  return clips.map((c) => {
    const n = JSON.parse(JSON.stringify(c));
    n.id = uid("c");
    if (c.link) {
      if (!links.has(c.link)) links.set(c.link, uid("g"));
      n.link = links.get(c.link);
    }
    return n;
  });
}

/** Colle des clips copiés à partir de `t`, en gardant leurs écarts. Piste
 *  principale : insertion magnétique ; ailleurs : même piste si libre. */
export function pasteClips(doc, clips, t) {
  if (!clips.length) return [];
  const copies = cloneClips(clips);
  const main = mainTrack(doc);
  const onMain = copies.filter((c) => c.track === main.id).sort((a, b) => a.start - b.start);
  const rest = copies.filter((c) => c.track !== main.id);
  const t0 = Math.min(...copies.map((c) => c.start));
  let shift = t - t0;
  if (onMain.length) {
    const at = nearestBoundary(doc, t);
    shift = at - onMain[0].start;
    // les copies de la principale se suivent ; leurs partenaires suivent leur clip
    const starts = new Map();
    let cur = at;
    onMain.forEach((c) => { starts.set(c.link || c.id, cur - c.start); cur += c.dur; });
    rest.forEach((c) => { if (c.link && starts.has(c.link)) c._delta = starts.get(c.link); });
    insertMain(doc, onMain, at);
  }
  for (const c of rest) {
    c.start = r4(Math.max(0, c.start + (c._delta !== undefined ? c._delta : shift)));
    delete c._delta;
    const tr = track(doc, c.track);
    if (!tr || tr.locked || !isFree(doc, tr.id, c.start, c.dur)) {
      c.track = freeTrack(doc, trackKindFor(c.kind), c.start, c.dur, { skipMain: true }).id;
    }
    doc.clips.push(c);
  }
  fixOverlaps(doc);
  return copies;
}

export function duplicateClips(doc, ids) {
  const src = doc.clips.filter((c) => ids.has(c.id));
  if (!src.length) return [];
  return pasteClips(doc, src, Math.max(...src.map(clipEnd)));
}

/* ------------------------------------------------ sous-titres liés à la voix */

/** Clips qui portent le son d'un média, dans l'ordre de préférence. */
function voiceClips(doc) {
  const audible = doc.clips.filter((c) => c.kind === "audio" || (c.kind === "video" && !c.detached));
  return audible.concat(doc.clips.filter((c) => c.kind === "video" && c.detached));
}

/** Où tombe l'instant source `s` du média `m` sur la timeline (ou null). */
export function mapSource(clips, m, s) {
  for (const c of clips) {
    if (c.media !== m) continue;
    if (s >= c.in - EPS && s < srcEnd(c) - EPS) return { clip: c, t: c.start + (s - c.in) / (c.speed || 1) };
  }
  return null;
}

/** Recale les sous-titres automatiques sur la timeline actuelle. Un mot dont le
 *  passage a été coupé est marqué `cut` (ni affiché ni exporté) ; une ligne
 *  dont tous les mots sont coupés est masquée (`gone`). */
export function reflowCaptions(doc) {
  const clips = voiceClips(doc);
  const touched = new Set();
  for (const c of doc.clips) {
    if (c.kind !== "text" || !c.auto || !(c.words || []).length) continue;
    let lo = Infinity, hi = -Infinity;
    for (const w of c.words) {
      if (!w.m) { lo = Math.min(lo, w.start); hi = Math.max(hi, w.end); continue; }
      const hit = mapSource(clips, w.m, w.s);
      if (!hit) { w.cut = true; continue; }
      const sp = hit.clip.speed || 1;
      w.start = r4(hit.t);
      w.end = r4(Math.max(hit.t, hit.clip.start + (Math.min(w.e, srcEnd(hit.clip)) - hit.clip.in) / sp));
      delete w.cut;
      lo = Math.min(lo, w.start);
      hi = Math.max(hi, w.end);
    }
    if (lo === Infinity) { c.gone = true; continue; }
    delete c.gone;
    const start = r4(lo), dur = r4(Math.max(0.2, hi - lo));
    if (start !== c.start || dur !== c.dur) touched.add(c.track);
    c.start = start;
    c.dur = dur;
  }
  // deux lignes d'une même piste ne se chevauchent pas : la première s'arrête
  // où commence la suivante
  for (const tid of touched) {
    const row = trackClips(doc, tid).filter((c) => !c.gone);
    for (let i = 0; i + 1 < row.length; i++) {
      const a = row[i], b = row[i + 1];
      if (clipEnd(a) > b.start + EPS) a.dur = r4(Math.max(MIN_DUR, b.start - a.start));
    }
  }
  // mot à mot : un mot reste affiché jusqu'au suivant (pas de clignotement entre deux mots)
  const holds = new Set(doc.clips.filter((c) => c.kind === "text" && c.hold && !c.gone).map((c) => c.track));
  for (const tid of holds) {
    const row = trackClips(doc, tid).filter((c) => !c.gone);
    for (let i = 0; i + 1 < row.length; i++) {
      const a = row[i], b = row[i + 1];
      const gap = b.start - clipEnd(a);
      if (a.hold && gap > EPS && gap <= HOLD_GAP) a.dur = r4(b.start - a.start);
    }
  }
}

/** Mots visibles d'un sous-titre (ceux dont le passage n'a pas été coupé). */
export const liveWords = (c) => (c.words || []).filter((w) => !w.cut);

/* ------------------------------------------------ suppression des blancs */

const FILLERS = new Set(["euh", "heu", "heuh", "euhm", "hum", "hmm", "mmh", "mh",
                         "bah", "ben", "hein", "bof", "pff", "genre"]);
const MULTI_FILLERS = [["en", "fait"], ["du", "coup"], ["tu", "vois"], ["tu", "sais"],
                       ["en", "gros"], ["et", "tout"], ["je", "veux", "dire"]];

export function norm(s) {
  return String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^\p{L}\p{N}]/gu, "");
}

/** Mêmes règles que `engine/pipeline/edit.py` : blancs avant, entre et après
 *  les mots (au-delà de `maxGap`, en gardant `pad` autour des mots). Temps
 *  relatifs à la plage [0, dur]. */
/** Blancs à retirer entre des mots. `pad` reste de chaque côté d'une coupe ;
 *  `tail` s'ajoute après le dernier mot avant une coupe (la voix finit
 *  souvent après l'horodatage du mot : couper trop tôt mange la fin de la
 *  phrase). */
export function silenceCuts(words, dur, maxGap = 0.5, pad = 0.08, tail = 0) {
  if (!words.length) return [[0, dur]];
  const cuts = [];
  if (words[0].start - pad > 0) cuts.push([0, words[0].start - pad]);
  for (let i = 0; i + 1 < words.length; i++) {
    const a = words[i], b = words[i + 1];
    if (b.start - a.end > maxGap) cuts.push([a.end + pad + tail, b.start - pad]);
  }
  const last = words[words.length - 1];
  if (last.end + pad + tail < dur) cuts.push([last.end + pad + tail, dur]);
  return cuts.filter(([x, y]) => y > x);
}

export function fillerCuts(words, pad = 0.05) {
  const n = words.map((w) => norm(w.text));
  const cuts = [];
  for (let i = 0; i < words.length;) {
    const phrase = MULTI_FILLERS.find((p) => p.every((x, k) => n[i + k] === x));
    if (phrase) {
      cuts.push([words[i].start - pad, words[i + phrase.length - 1].end + pad]);
      i += phrase.length;
      continue;
    }
    if (FILLERS.has(n[i])) cuts.push([words[i].start - pad, words[i].end + pad]);
    i++;
  }
  return cuts;
}

export function mergeRanges(ranges) {
  const iv = ranges.filter(([a, b]) => b > a).map(([a, b]) => [Math.max(0, a), b])
    .sort((x, y) => x[0] - y[0]);
  const out = [];
  for (const [a, b] of iv) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

/** Plages à retirer d'un clip, EN TEMPS SOURCE, d'après les mots du média
 *  (`words`) ou les silences mesurés au volume (`silences`). Un bout gardé de
 *  moins de `minKeep` entre deux coupes part aussi ; une coupe de moins de
 *  `minCut` est ignorée (on ne découpe pas pour trois images). */
export function clipCuts(clip, { words, silences, extra, maxGap = 0.5, pad = 0.08, tail = 0, fillers = false,
                                  minKeep = 0.1, minCut = 0.12 } = {}) {
  const a = clip.in, b = srcEnd(clip);
  let cuts = [];
  if (extra) cuts = extra.map(([x, y]) => [x, y]);          // plages imposées (montage automatique)
  if (words) {
    const inside = words.filter((w) => w.end > a && w.start < b)
      .map((w) => ({ text: w.text, start: Math.max(0, w.start - a), end: Math.min(b - a, w.end - a) }));
    let found = silenceCuts(inside, b - a, maxGap, pad, tail);
    if (fillers) found = found.concat(fillerCuts(inside));
    cuts = cuts.concat(found.map(([x, y]) => [x + a, y + a]));
  } else if (silences) {
    cuts = cuts.concat(silences.filter(([x, y]) => y > a && x < b)
      .map(([x, y]) => [x + pad, y - pad]));
  }
  cuts = mergeRanges(cuts.map(([x, y]) => [Math.max(a, x), Math.min(b, y)]));
  const merged = [];
  for (const c of cuts) {
    const prev = merged[merged.length - 1];
    if (prev && c[0] - prev[1] < minKeep) prev[1] = c[1];
    else merged.push([...c]);
  }
  if (merged.length && merged[0][0] - a < minKeep) merged[0][0] = a;
  const last = merged[merged.length - 1];
  if (last && b - last[1] < minKeep) last[1] = b;
  return merged.filter(([x, y]) => y - x >= minCut).map(([x, y]) => [r4(x), r4(y)]);
}

/** Complément de coupes (temps source) dans [a, b]. */
function keepRanges(a, b, cuts) {
  const out = [];
  let cur = a;
  for (const [x, y] of mergeRanges(cuts)) {
    if (x > cur + EPS) out.push([cur, Math.min(x, b)]);
    cur = Math.max(cur, y);
  }
  if (b > cur + EPS) out.push([cur, b]);
  return out.filter(([x, y]) => y - x >= MIN_DUR);
}

/** Applique des coupes (temps source du clip) : le clip et ses partenaires
 *  liés sont remplacés par les morceaux gardés, recollés à partir de son
 *  début. Sur la piste principale, la suite se recolle aussi. Renvoie le temps
 *  retiré (timeline) et les nouveaux clips.
 *
 *  Chaque morceau retient ce qui a été retiré juste avant lui (`gap`, en temps
 *  source) et le dernier ce qui l'a été après lui (`tail`) : c'est ce qui
 *  permet d'afficher chaque coupe et de la restaurer (`restoreGap`). */
export function applyCuts(doc, clip, cuts) {
  if (!cuts.length) return { removed: 0, pieces: [clip] };
  const speed = clip.speed || 1;
  const keep = keepRanges(clip.in, srcEnd(clip), cuts);
  const group = [clip, ...partners(doc, clip)];
  const gone = new Set(group.map((g) => g.id));
  doc.clips = doc.clips.filter((c) => !gone.has(c.id));
  const pieces = [];
  let removed = clip.dur;
  let t = clip.start;
  keep.forEach(([x, y], i) => {
    const dur = (y - x) / speed;
    const link = group.length > 1 ? deriveLink(clip.link || clip.id, "k" + i) : "";
    // instant (timeline d'origine) où le clip lisait `x`
    const at = clip.start + (x - clip.in) / speed;
    // ce qui a été retiré juste avant ce morceau (et après le dernier)
    const before = i === 0 ? clip.in : keep[i - 1][1];
    let gap = x - before > EPS ? { s: r4(before), e: r4(x) } : null;
    if (i === 0 && clip.gap && Math.abs(clip.gap.e - clip.in) < 1e-3) {
      gap = { s: clip.gap.s, e: gap ? gap.e : clip.gap.e };        // coupes qui se touchent : une seule
    }
    let tail = null;
    if (i === keep.length - 1) {
      const end = srcEnd(clip);
      tail = end - y > EPS ? { s: r4(y), e: r4(end) } : null;
      if (clip.tail && Math.abs(clip.tail.s - end) < 1e-3) tail = { s: tail ? tail.s : clip.tail.s, e: clip.tail.e };
    }
    for (const g of group) {
      // chaque partenaire garde ce qu'il jouait à ce même instant
      const p = JSON.parse(JSON.stringify(g));
      p.id = uid("c");
      p.start = r4(t);
      p.dur = r4(dur);
      if (hasSource(g)) p.in = r4(Math.max(0, g.in + (at - g.start) * (g.speed || 1)));
      p.link = link;
      if ("fade_in" in p) {
        p.fade_in = i === 0 ? g.fade_in || 0 : 0;
        p.fade_out = i === keep.length - 1 ? g.fade_out || 0 : 0;
      }
      if (i > 0) delete p.trans;
      // le retrait, dans la source de ce partenaire (même décalage que le clip)
      const shift = (g.in || 0) - clip.in;
      delete p.gap;
      delete p.tail;
      if (gap) p.gap = { s: r4(gap.s + shift), e: r4(gap.e + shift) };
      if (tail) p.tail = { s: r4(tail.s + shift), e: r4(tail.e + shift) };
      doc.clips.push(p);
      if (g === clip) pieces.push(p);
    }
    t += dur;
    removed -= dur;
  });
  if (isMain(doc, clip.track)) packMain(doc);
  else fixOverlaps(doc);
  return { removed: r4(removed), pieces };
}

/* ------------------------------------------------- montage automatique */

/** Fait passer des clips de la piste principale en tête du montage (accroche
 *  déplacée au début), dans leur ordre actuel ; le reste suit. */
export function moveToFront(doc, ids) {
  const main = mainTrack(doc);
  if (!main) return [];
  const row = trackClips(doc, main.id);
  const front = row.filter((c) => ids.has(c.id));
  if (!front.length) return [];
  const rest = row.filter((c) => !ids.has(c.id));
  let at = 0;
  for (const c of front.concat(rest)) {
    shiftWithPartners(doc, c, at - c.start);
    at = r4(at + c.dur);
  }
  packMain(doc);
  return front;
}

/** Ne garde que l'intervalle [a, b] de la timeline : tout le reste part, ce
 *  qui reste recule à 0. Les sous-titres liés à la voix sont recalés. */
export function isolateRange(doc, a, b) {
  const all = new Set(doc.clips.map((c) => c.id));
  splitAt(doc, b, all);
  splitAt(doc, a, new Set(doc.clips.map((c) => c.id)));
  doc.clips = doc.clips.filter((c) => c.start >= a - EPS && clipEnd(c) <= b + EPS);
  for (const c of doc.clips) c.start = r4(Math.max(0, c.start - a));
  packMain(doc);
  reflowCaptions(doc);
  return r4(b - a);
}

/** Centre (x, y) à donner à un clip « remplir » zoomé de `scale` pour que le
 *  visage, en `face` (0..1 dans l'image source), reste où il était à l'échelle
 *  1. Sans visage : un léger recentrage vers le haut (la tête est rarement au
 *  milieu d'un plan vertical). */
export function zoomCenter(face, media, canvas, scale) {
  const W = canvas.w || 1080, H = canvas.h || 1920;
  const w = (media && media.w) || W, h = (media && media.h) || H;
  const base = Math.max(W / w, H / h);
  let X = 0.5, Y = 0.42;
  if (face) {
    X = 0.5 + (face.x - 0.5) * (w * base / W);
    Y = 0.5 + (face.y - 0.5) * (h * base / H);
  }
  // le visage est parfois hors du cadre vertical rogné : on reste dans l'image
  X = Math.min(0.9, Math.max(0.1, X));
  Y = Math.min(0.9, Math.max(0.1, Y));
  return { x: r4(0.5 + (X - 0.5) * (1 - scale)), y: r4(0.5 + (Y - 0.5) * (1 - scale)) };
}

/** Instants (timeline) où couper un clip pour le rythmer : de préférence aux
 *  fins de phrases (`cutpoints`, temps source), sinon tous les `maxPiece` s.
 *  Aucun morceau plus court que `minPiece`. */
export function rhythmSplits(clip, cutpoints, { minPiece = 2, maxPiece = 6 } = {}) {
  const sp = clip.speed || 1;
  const end = clipEnd(clip);
  const pts = (cutpoints || []).map((s) => clip.start + (s - clip.in) / sp)
    .filter((t) => t > clip.start + minPiece && t < end - minPiece).sort((a, b) => a - b);
  const out = [];
  let cur = clip.start;
  while (end - cur > maxPiece + minPiece) {
    const window = pts.filter((t) => t >= cur + minPiece && t <= cur + maxPiece);
    const t = window.length ? window[window.length - 1] : r4(cur + maxPiece);
    out.push(r4(t));
    cur = t;
  }
  return out;
}

/* ------------------------------------------------------------------ vitesse */

/** Change la vitesse d'un clip (et de ses partenaires) en gardant la même
 *  plage de source : la durée sur la timeline s'allonge ou raccourcit. Sur la
 *  principale, la suite se recolle ; ailleurs, un clip qui déborderait sur son
 *  voisin est rogné à la place disponible. */
export function setSpeed(doc, clip, speed) {
  speed = Math.min(16, Math.max(0.1, speed));
  const group = [clip, ...partners(doc, clip)].filter(hasSource);
  for (const g of group) {
    const src = g.dur * (g.speed || 1);
    g.speed = r4(speed);
    let dur = src / speed;
    if (!isMain(doc, g.track)) {
      const next = Math.min(Infinity, ...trackClips(doc, g.track)
        .filter((c) => c !== g && !group.includes(c) && c.start >= clipEnd(g) - EPS).map((c) => c.start));
      dur = Math.min(dur, next - g.start);
    }
    g.dur = r4(Math.max(MIN_DUR, dur));
    if ("fade_in" in g) {
      g.fade_in = Math.min(g.fade_in || 0, g.dur);
      g.fade_out = Math.min(g.fade_out || 0, g.dur);
    }
  }
  if (group.some((g) => isMain(doc, g.track))) packMain(doc);
}

/* -------------------------------------------------------------- transitions */

export const TRANSITIONS = [
  ["fade", "Fondu enchaîné"], ["fadeblack", "Fondu au noir"], ["fadewhite", "Fondu au blanc"],
  ["dissolve", "Dissolution"], ["slideleft", "Glissement ←"], ["slideright", "Glissement →"],
  ["wipeleft", "Volet"], ["circleopen", "Cercle"], ["zoomin", "Zoom"],
];

/** Transition d'entrée effective d'un clip : le clip précédent de sa piste doit
 *  se terminer exactement à son début. Durée bornée par les deux clips. */
export function transIn(doc, c) {
  if (!c.trans || (c.kind !== "video" && c.kind !== "image")) return null;
  const prev = doc.clips.find((o) => o !== c && o.track === c.track && (o.kind === "video" || o.kind === "image") &&
                                     Math.abs(clipEnd(o) - c.start) < 1e-3);
  if (!prev) return null;
  const d = Math.min(c.trans.dur || 0.5, prev.dur, c.dur);
  return d >= 0.05 ? { prev, type: c.trans.type, d } : null;
}

/** Prolongations de lecture d'un clip dues aux transitions (secondes). */
export function extensions(doc, c) {
  const tin = transIn(doc, c);
  const next = doc.clips.find((o) => o !== c && o.track === c.track && Math.abs(o.start - clipEnd(c)) < 1e-3);
  const tout = next ? transIn(doc, next) : null;
  return { pre: tin ? tin.d / 2 : 0, post: tout ? tout.d / 2 : 0, tin, tout, next };
}

/* ------------------------------------------------- passages supprimés */

/** Les passages retirés par la suppression des blancs, un par coupe (le son
 *  séparé d'une vidéo n'en fait pas une deuxième). `t` situe la coupe sur la
 *  timeline, `dur` est la durée retirée. */
export function removedPassages(doc) {
  const out = [];
  const seen = new Set();
  const main = mainTrack(doc);
  for (const c of doc.clips) {
    if (!c.gap && !c.tail) continue;
    const group = [c, ...partners(doc, c)];
    const lead = group.find((g) => main && g.track === main.id) || group.find((g) => g.kind === "video") || c;
    if (lead !== c) continue;
    for (const side of ["gap", "tail"]) {
      const r = c[side];
      if (!r || r.e - r.s < MIN_DUR) continue;
      const key = c.id + side;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ id: c.id, side, media: c.media, track: c.track, s: r.s, e: r.e,
                 t: side === "gap" ? c.start : clipEnd(c), dur: r4((r.e - r.s) / (c.speed || 1)) });
    }
  }
  return out.sort((a, b) => a.t - b.t);
}

/** Deux morceaux qui se suivent dans la source ET sur la timeline redeviennent
 *  un seul clip (partenaires liés compris). */
function mergeInto(doc, a, b) {
  if (!a || !b || a.media !== b.media || a.track !== b.track || a.kind !== b.kind) return false;
  if ((a.speed || 1) !== (b.speed || 1)) return false;
  if (Math.abs(clipEnd(a) - b.start) > 1e-3 || Math.abs(srcEnd(a) - (b.in || 0)) > 1e-3) return false;
  a.dur = r4(a.dur + b.dur);
  if (b.tail) a.tail = b.tail; else delete a.tail;
  if ("fade_out" in b) a.fade_out = b.fade_out;
  doc.clips = doc.clips.filter((c) => c !== b);
  return true;
}

function mergePair(doc, a, b) {
  if (!a || !b) return;
  const pa = partners(doc, a), pb = partners(doc, b);
  if (!mergeInto(doc, a, b)) return;
  for (const q of pb) {
    const mate = pa.find((x) => x.track === q.track && x.media === q.media);
    if (mate && mergeInto(doc, mate, q)) continue;
    q.link = a.link;                       // partenaire orphelin : il suit le clip fusionné
  }
}

/** Remet dans le montage un passage retiré (`side` : "gap" avant le clip,
 *  "tail" après). Le clip s'allonge d'autant, la suite recule, et les deux
 *  morceaux redeviennent un seul clip si rien ne les sépare plus. Renvoie la
 *  durée remise (timeline). */
export function restoreGap(doc, id, side = "gap") {
  const p = doc.clips.find((c) => c.id === id);
  const r = p && p[side];
  if (!r) return 0;
  const speed = p.speed || 1;
  const len = r.e - r.s, g = len / speed;
  const group = [p, ...partners(doc, p)];
  const onMain = group.some((m) => isMain(doc, m.track));
  if (!onMain) {
    // hors piste magnétique : ce qui suit sur les pistes du groupe recule
    for (const m of group) {
      const from = side === "gap" ? m.start : clipEnd(m);
      doc.clips.forEach((o) => {
        if (!group.includes(o) && o.track === m.track && o.start >= from - EPS) o.start = r4(o.start + g);
      });
    }
  }
  for (const m of group) {
    if (side === "gap") m.in = r4(Math.max(0, (m.in || 0) - len));
    m.dur = r4(m.dur + g);
    delete m[side];
  }
  if (onMain) packMain(doc);
  else fixOverlaps(doc);
  // les deux morceaux ne font plus qu'un
  if (side === "gap") {
    const prev = doc.clips.find((c) => c !== p && c.track === p.track && Math.abs(clipEnd(c) - p.start) < 1e-3);
    if (prev) mergePair(doc, prev, p);
  } else {
    const next = doc.clips.find((c) => c !== p && c.track === p.track && Math.abs(c.start - clipEnd(p)) < 1e-3);
    if (next) mergePair(doc, p, next);
  }
  return r4(g);
}

/** Restaure tous les passages retirés (d'un clip, ou de tout le montage). */
export function restoreAll(doc, ids) {
  let total = 0;
  for (let guard = 0; guard < 2000; guard++) {
    const next = removedPassages(doc).filter((x) => !ids || ids.has(x.id)).pop();
    if (!next) break;
    total += restoreGap(doc, next.id, next.side);
  }
  return r4(total);
}
