// Logique de timeline du studio (engine/web/studio/model.js).
// Lancer : node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";

import * as M from "../../engine/web/studio/model.js";

const VIDEO = { id: "mv", kind: "video", duration: 10, has_audio: true };
const VIDEO2 = { id: "mw", kind: "video", duration: 6, has_audio: true };
const SONG = { id: "ms", kind: "audio", duration: 30, has_audio: true };
const PHOTO = { id: "mp", kind: "image", duration: 0 };
const MEDIA = new Map([VIDEO, VIDEO2, SONG, PHOTO].map((m) => [m.id, m]));

function doc() {
  return {
    canvas: { w: 1080, h: 1920, fps: 30 },
    tracks: [
      { id: "tv1", kind: "video", name: "Vidéo", main: true },
      { id: "ta1", kind: "audio", name: "Audio 1", main: false },
    ],
    clips: [],
  };
}
const on = (d, tid) => M.trackClips(d, tid);
const spans = (d, tid) => on(d, tid).map((c) => [c.start, M.r4(c.dur)]);
const near = (a, b, eps = 1e-3) => assert.ok(Math.abs(a - b) < eps, `${a} ≉ ${b}`);

test("ajout de médias : vidéos collées sur la principale, audio à la tête de lecture", () => {
  const d = doc();
  M.appendMedia(d, VIDEO, 0);
  M.appendMedia(d, PHOTO, 99);          // au bord le plus proche = la fin
  assert.deepEqual(spans(d, "tv1"), [[0, 10], [10, 3]]);
  const [a] = M.appendMedia(d, SONG, 4);
  assert.equal(a.track, "ta1");
  assert.equal(a.start, 4);
  const [b] = M.appendMedia(d, SONG, 5);   // créneau occupé : nouvelle piste audio
  assert.notEqual(b.track, "ta1");
  assert.equal(M.track(d, b.track).kind, "audio");
});

test("insertion au milieu : la suite recule, les sons liés aussi", () => {
  const d = doc();
  M.appendMedia(d, VIDEO, 0);
  const [second] = M.appendMedia(d, VIDEO2, 10);
  const a = M.detachAudio(d, second);
  M.insertMain(d, [M.newMediaClip(PHOTO)], 9.8);       // bord le plus proche : 10
  assert.deepEqual(spans(d, "tv1"), [[0, 10], [10, 3], [13, 6]]);
  assert.equal(a.start, 13);
});

test("séparer le son : clip audio lié, vidéo muette ; rattacher l'annule", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const a = M.detachAudio(d, v);
  assert.equal(a.kind, "audio");
  assert.equal(a.track, "ta1");
  assert.deepEqual([a.start, a.dur, a.in, a.media], [v.start, v.dur, v.in, v.media]);
  assert.ok(v.detached && v.link && v.link === a.link);
  assert.equal(M.detachAudio(d, v), null);             // déjà séparé
  assert.ok(M.reattachAudio(d, v));
  assert.ok(!v.detached);
  assert.equal(d.clips.length, 1);
});

test("diviser un groupe lié : les moitiés droites restent liées entre elles", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const a = M.detachAudio(d, v);
  const rights = M.splitAt(d, 4, new Set([v.id]));
  assert.equal(rights.length, 2);                      // la vidéo ET son son
  const [rv, ra] = [rights.find((c) => c.kind === "video"), rights.find((c) => c.kind === "audio")];
  assert.equal(rv.link, ra.link);
  assert.notEqual(rv.link, v.link);
  assert.equal(v.link, a.link);
  assert.deepEqual([rv.start, rv.in, rv.dur], [4, 4, 6]);
  assert.deepEqual([v.dur, a.dur], [4, 4]);
});

test("diviser sans sélection : tout ce qui est sous la tête, sauf pistes verrouillées", () => {
  const d = doc();
  M.appendMedia(d, VIDEO, 0);
  M.appendMedia(d, SONG, 0);
  d.tracks[1].locked = true;
  const rights = M.splitAt(d, 5);
  assert.equal(rights.length, 1);
  assert.equal(rights[0].kind, "video");
  assert.equal(M.splitAt(d, 0.01).length, 0);          // trop près d'un bord
});

test("supprimer sur la principale referme le trou et entraîne les sons liés", () => {
  const d = doc();
  const [v1] = M.appendMedia(d, VIDEO, 0);
  const [v2] = M.appendMedia(d, VIDEO2, 10);
  const a2 = M.detachAudio(d, v2);
  M.deleteClips(d, new Set([v1.id]));
  assert.deepEqual(spans(d, "tv1"), [[0, 6]]);
  assert.equal(a2.start, 0);
  // supprimer le son séparé laisse la vidéo muette et efface le lien
  M.deleteClips(d, new Set([a2.id]));
  assert.ok(v2.detached);
  assert.equal(v2.link, "");
});

test("rogner : borné par la source ; sur la principale le début reste et la suite suit", () => {
  const d = doc();
  const [v1] = M.appendMedia(d, VIDEO, 0);
  const [v2] = M.appendMedia(d, VIDEO2, 10);
  M.trimClip(d, v1, "r", 50, MEDIA);                   // pas au-delà des 10 s de source
  assert.equal(v1.dur, 10);
  M.trimClip(d, v1, "l", 2, MEDIA);                    // retire 2 s au début
  assert.deepEqual([v1.start, v1.in, v1.dur], [0, 2, 8]);
  assert.equal(v2.start, 8);
  M.trimClip(d, v1, "r", 6, MEDIA);
  assert.deepEqual([v1.dur, v2.start], [6, 6]);
  M.trimClip(d, v1, "l", -5, MEDIA);                   // pas avant le début de la source
  assert.deepEqual([v1.in, v1.dur], [0, 8]);
});

test("rogner un clip lié rogne son partenaire", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const a = M.detachAudio(d, v);
  M.trimClip(d, a, "l", 3, MEDIA);
  assert.deepEqual([v.start, v.in, v.dur], [0, 3, 7]);
  assert.deepEqual([a.start, a.in, a.dur], [0, 3, 7]);
  M.trimClip(d, v, "r", 5, MEDIA);
  assert.deepEqual([v.dur, a.dur], [5, 5]);
});

test("rogner hors principale : bloqué par le voisin", () => {
  const d = doc();
  const [a] = M.appendMedia(d, SONG, 0);
  a.dur = 5;
  const b = M.newMediaClip(SONG, { track: "ta1", start: 8 });
  b.dur = 5;
  d.clips.push(b);
  M.trimClip(d, a, "r", 20, MEDIA);
  assert.equal(a.dur, 8);
  M.trimClip(d, b, "l", 1, MEDIA);
  assert.equal(b.start, 8);                            // collé au voisin, pas plus loin
});

test("réordonner la principale", () => {
  const d = doc();
  const [v1] = M.appendMedia(d, VIDEO, 0);
  const [v2] = M.appendMedia(d, VIDEO2, 10);
  const a2 = M.detachAudio(d, v2);
  M.reorderMain(d, v2, 0);
  assert.deepEqual(on(d, "tv1").map((c) => c.id), [v2.id, v1.id]);
  assert.deepEqual([v2.start, v1.start, a2.start], [0, 6, 0]);
});

test("déplacement hors principale : refusé s'il chevauche", () => {
  const d = doc();
  const [a] = M.appendMedia(d, SONG, 0);
  a.dur = 4;
  const b = M.newMediaClip(SONG, { track: "ta1", start: 6 });
  b.dur = 4;
  d.clips.push(b);
  assert.ok(!M.canMove(d, new Set([a.id]), 3));
  assert.ok(M.canMove(d, new Set([a.id]), 2));
  assert.ok(!M.canMove(d, new Set([a.id]), -1));      // avant 0
  assert.ok(!M.canMove(d, new Set([a.id]), 0, new Map([["ta1", "tv1"]])));   // audio sur vidéo
});

test("coller et dupliquer", () => {
  const d = doc();
  const [v1] = M.appendMedia(d, VIDEO, 0);
  const a1 = M.detachAudio(d, v1);
  const copies = M.duplicateClips(d, new Set([v1.id, a1.id]));
  assert.equal(copies.length, 2);
  const [cv, ca] = [copies.find((c) => c.kind === "video"), copies.find((c) => c.kind === "audio")];
  assert.equal(cv.start, 10);
  assert.equal(ca.start, 10);
  assert.equal(cv.link, ca.link);
  assert.notEqual(cv.link, v1.link);
});

test("chevauchements résiduels : le clip fautif change de piste", () => {
  const d = doc();
  const a = M.newMediaClip(SONG, { track: "ta1", start: 0 });
  a.dur = 5;
  const b = M.newMediaClip(SONG, { track: "ta1", start: 3 });
  b.dur = 5;
  d.clips.push(a, b);
  M.fixOverlaps(d);
  assert.equal(a.track, "ta1");
  assert.notEqual(b.track, "ta1");
});

test("déposer une vidéo sur une piste audio n'en garde que le son", () => {
  const d = doc();
  const [c] = M.placeMedia(d, VIDEO, "ta1", 2);
  assert.equal(c.kind, "audio");
  const [v] = M.placeMedia(d, VIDEO2, "ta1", 50);        // timeline vidéo vide…
  assert.equal(v.kind, "audio");
  const [x] = M.placeMedia(d, PHOTO, "ta1", 1);          // …une image, elle, va en vidéo
  assert.equal(x.kind, "image");
  assert.equal(x.track, "tv1");
});

test("supprimer une piste emporte ses clips, jamais la principale", () => {
  const d = doc();
  M.appendMedia(d, SONG, 0);
  assert.ok(!M.removeTrack(d, "tv1"));
  assert.ok(M.removeTrack(d, "ta1"));
  assert.equal(d.clips.length, 0);
});

/* ------------------------------------------------------ blancs */

const WORDS = [
  { text: "Bonjour", start: 1.0, end: 1.5 },
  { text: "euh", start: 1.6, end: 1.9 },
  { text: "tout", start: 2.0, end: 2.3 },
  { text: "le", start: 4.0, end: 4.2 },           // 1,7 s de blanc avant
  { text: "monde", start: 4.3, end: 4.8 },
];

test("coupes : mêmes règles que le moteur Python", () => {
  const cuts = M.silenceCuts([{ start: 1, end: 2 }, { start: 3, end: 4 }], 5, 0.5, 0.1);
  assert.deepEqual(cuts.map((c) => c.map((x) => M.r4(x))), [[0, 0.9], [2.1, 2.9], [4.1, 5]]);
  assert.deepEqual(M.fillerCuts([{ text: "Du", start: 0, end: 0.2 }, { text: "coup,", start: 0.2, end: 0.5 },
                                 { text: "Euh", start: 1, end: 1.2 }], 0),
                   [[0, 0.5], [1, 1.2]]);
  assert.equal(M.norm("Élève,"), "eleve");
});

test("coupes d'un clip, en temps source", () => {
  const clip = { in: 0.5, dur: 5, speed: 1 };          // lit 0,5 → 5,5
  const cuts = M.clipCuts(clip, { words: WORDS, maxGap: 0.5, pad: 0.08 });
  assert.deepEqual(cuts, [[0.5, 0.92], [2.38, 3.92], [4.88, 5.5]]);
  const withFillers = M.clipCuts(clip, { words: WORDS, fillers: true, pad: 0.08 });
  assert.ok(withFillers.some(([a, b]) => a <= 1.55 && b >= 1.95));
  const bySound = M.clipCuts(clip, { silences: [[2.3, 4.0], [5.0, 9]], pad: 0.05 });
  assert.deepEqual(bySound, [[2.35, 3.95], [5.05, 5.5]]);
});

test("supprimer les blancs d'un clip lié : morceaux alignés, suite recollée", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const [next] = M.appendMedia(d, VIDEO2, 10);
  const a = M.detachAudio(d, v);
  const cuts = M.clipCuts(v, { words: WORDS, maxGap: 0.5, pad: 0.08 });
  const { removed, pieces } = M.applyCuts(d, v, cuts);
  const vids = on(d, "tv1").filter((c) => c.media === "mv");
  const auds = on(d, "ta1").filter((c) => c.media === "mv");
  assert.equal(pieces.length, 2);
  assert.equal(vids.length, 2);
  assert.equal(auds.length, 2);
  vids.forEach((p, i) => {
    assert.deepEqual([p.start, p.in, p.dur], [auds[i].start, auds[i].in, auds[i].dur]);
    assert.equal(p.link, auds[i].link);
  });
  assert.notEqual(vids[0].link, vids[1].link);
  near(vids[0].in, 0.92);
  near(vids[1].start, vids[0].dur);
  const kept = vids.reduce((s, c) => s + c.dur, 0);
  near(removed, 10 - kept);
  near(next.start, kept);                               // la suite a reculé
  assert.ok(!d.clips.includes(a));
});

test("supprimer les blancs hors principale : les morceaux se recollent au début du clip", () => {
  const d = doc();
  const [s] = M.appendMedia(d, SONG, 2);
  s.dur = 5;
  const cuts = [[1, 2], [3, 3.5]];
  const { removed } = M.applyCuts(d, s, cuts);
  assert.deepEqual(spans(d, "ta1"), [[2, 1], [3, 1], [4, 1.5]]);
  near(removed, 1.5);
});

/* --------------------------------------- sous-titres liés à la voix */

function captioned() {
  const d = doc();
  d.tracks.unshift({ id: "tt1", kind: "text", name: "Texte 1" });
  const [v] = M.appendMedia(d, VIDEO, 0);
  const cap = {
    id: "k1", track: "tt1", kind: "text", auto: true, start: 1, dur: 3.8,
    words: WORDS.map((w) => ({ ...w, m: "mv", s: w.start, e: w.end })),
  };
  d.clips.push(cap);
  return { d, v, cap };
}

test("sous-titres : suivent un rognage et marquent les mots coupés", () => {
  const { d, v, cap } = captioned();
  M.trimClip(d, v, "l", 1.8, MEDIA);            // coupe « Bonjour » (1,0 → 1,5)
  M.reflowCaptions(d);
  const words = cap.words.filter((w) => !w.cut).map((w) => w.text);
  assert.deepEqual(words, ["tout", "le", "monde"]);   // « euh » commence à 1,6 < 1,8
  near(cap.start, 0.2);                          // « tout » (2,0 source) → 0,2 timeline
  assert.deepEqual(M.liveWords(cap).map((w) => w.text), words);
});

test("sous-titres : suivent un déplacement et la suppression des blancs", () => {
  const { d, v, cap } = captioned();
  M.insertMain(d, [M.newMediaClip(PHOTO)], 0);   // 3 s d'image devant
  M.reflowCaptions(d);
  near(cap.start, 4);
  const cuts = M.clipCuts(on(d, "tv1")[1], { words: WORDS, maxGap: 0.5, pad: 0.08 });
  M.applyCuts(d, on(d, "tv1")[1], cuts);
  M.reflowCaptions(d);
  const le = cap.words.find((w) => w.text === "le");
  const tout = cap.words.find((w) => w.text === "tout");
  assert.ok(le.start - tout.end < 0.2);          // le blanc de 1,7 s a disparu
  assert.ok(!cap.gone);
  M.deleteClips(d, new Set(on(d, "tv1").filter((c) => c.media === "mv").map((c) => c.id)));
  M.reflowCaptions(d);
  assert.ok(cap.gone);
});

test("vitesse : même plage de source, la durée suit, la suite se recolle", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const [next] = M.appendMedia(d, VIDEO2, 10);
  const a = M.detachAudio(d, v);
  M.setSpeed(d, v, 2);
  assert.deepEqual([v.dur, v.speed, a.dur, a.speed], [5, 2, 5, 2]);
  assert.equal(next.start, 5);
  assert.equal(M.srcEnd(v), 10);
  M.setSpeed(d, v, 0.5);
  assert.deepEqual([v.dur, next.start], [20, 20]);
});

test("vitesse hors principale : rognée si elle déborde sur le voisin", () => {
  const d = doc();
  const [s] = M.appendMedia(d, SONG, 0);
  s.dur = 4;
  const b = M.newMediaClip(SONG, { track: "ta1", start: 6 });
  b.dur = 2;
  d.clips.push(b);
  M.setSpeed(d, s, 0.5);                 // voudrait 8 s, n'en a que 6
  assert.equal(s.dur, 6);
});


test("transitions : valides seulement entre deux clips qui se touchent", () => {
  const d = doc();
  const [a] = M.appendMedia(d, VIDEO, 0);
  const [b] = M.appendMedia(d, VIDEO2, 10);
  b.trans = { type: "fade", dur: 9 };
  const t = M.transIn(d, b);
  assert.equal(t.prev, a);
  assert.equal(t.d, 6);                                 // bornée par le clip le plus court
  assert.deepEqual([M.extensions(d, a).post, M.extensions(d, b).pre], [3, 3]);
  const right = M.splitClip(d, b, 13);
  assert.equal(right.trans, undefined);                // la transition reste au début
  assert.ok(b.trans);
  a.trans = { type: "fade", dur: 1 };
  assert.equal(M.transIn(d, a), null);                 // rien avant le premier clip
});


test("passages supprimés : repérés, restaurés un par un, le clip se reforme", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const [next] = M.appendMedia(d, VIDEO2, 10);
  M.detachAudio(d, v);
  const cuts = M.clipCuts(v, { words: WORDS, maxGap: 0.5, pad: 0.08 });
  M.applyCuts(d, v, cuts);
  const passages = M.removedPassages(d);
  assert.deepEqual(passages.map((p) => [p.side, p.s, p.e]),
                   [["gap", 0, 0.92], ["gap", 2.38, 3.92], ["tail", 4.88, 10]]);
  assert.equal(passages.length, 3);                   // le son séparé ne compte pas en double
  const kept = next.start;
  // on restaure le blanc du milieu : +1,54 s, les deux morceaux n'en font plus qu'un
  const mid = passages[1];
  near(M.restoreGap(d, mid.id, mid.side), 1.54);
  near(next.start, kept + 1.54);
  const vids = on(d, "tv1").filter((c) => c.media === "mv");
  const auds = on(d, "ta1").filter((c) => c.media === "mv");
  assert.equal(vids.length, 1);
  assert.equal(auds.length, 1);
  assert.deepEqual([vids[0].in, auds[0].in, vids[0].dur, auds[0].dur], [0.92, 0.92, 3.96, 3.96]);
  assert.equal(vids[0].link, auds[0].link);
  // et le reste
  M.restoreAll(d);
  assert.deepEqual(M.removedPassages(d), []);
  const [whole] = on(d, "tv1").filter((c) => c.media === "mv");
  assert.deepEqual([whole.in, whole.dur, next.start], [0, 10, 10]);
});

test("couper deux fois fusionne les retraits qui se touchent", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  M.applyCuts(d, v, [[0, 1]]);
  const [p] = on(d, "tv1");
  M.applyCuts(d, p, [[1, 2]]);
  assert.deepEqual(M.removedPassages(d).map((x) => [x.s, x.e]), [[0, 2]]);
});

test("sous-titres : restaurer un blanc fait revenir ses mots", () => {
  const { d, v, cap } = captioned();
  const cuts = M.clipCuts(v, { words: WORDS, maxGap: 0.5, pad: 0.08, fillers: true });
  M.applyCuts(d, v, cuts);
  M.reflowCaptions(d);
  assert.ok(cap.words.find((w) => w.text === "euh").cut);
  M.restoreAll(d);
  M.reflowCaptions(d);
  assert.ok(!cap.words.some((w) => w.cut));
});

/* ------------------------------------------------- montage automatique */

test("coupes : une respiration après le dernier mot avant chaque coupe", () => {
  const cuts = M.silenceCuts([{ start: 1, end: 2 }, { start: 3, end: 4 }], 5, 0.5, 0.1, 0.2);
  assert.deepEqual(cuts.map((c) => c.map((x) => M.r4(x))), [[0, 0.9], [2.3, 2.9], [4.3, 5]]);
  // pas de coupe négative quand la respiration dépasse le blanc
  assert.deepEqual(M.silenceCuts([{ start: 1, end: 2 }, { start: 2.6, end: 3 }], 3.1, 0.5, 0.1, 0.4), [[0, 0.9]]);
});

test("coupes : des plages imposées se mêlent aux blancs", () => {
  const v = { start: 0, dur: 10, in: 0, speed: 1 };
  const cuts = M.clipCuts(v, { words: WORDS, maxGap: 0.5, pad: 0.08, extra: [[3, 6], [5.5, 7]] });
  assert.ok(cuts.some(([a, b]) => a <= 3 && b >= 7));          // fusionnées avec les blancs voisins
  assert.deepEqual(M.clipCuts(v, { extra: [[1, 1.05]] }), []);   // trop courte pour valoir une coupe
});

test("accroche en tête : les clips passent devant, la suite recule", () => {
  const d = doc();
  const [v] = M.appendMedia(d, VIDEO, 0);
  const [w] = M.appendMedia(d, VIDEO2, 99);
  const right = M.splitAt(d, 4, new Set([v.id]));
  const [hook] = M.splitAt(d, 7, new Set(right.map((c) => c.id)));
  assert.deepEqual(spans(d, "tv1"), [[0, 4], [4, 3], [7, 3], [10, 6]]);
  M.moveToFront(d, new Set([right[0].id]));
  assert.deepEqual(on(d, "tv1").map((c) => [c.start, c.in]), [[0, 4], [3, 0], [7, 7], [10, 0]]);
  assert.equal(on(d, "tv1")[0].id, right[0].id);
  assert.equal(on(d, "tv1")[3].id, w.id);
  assert.ok(hook);
});

test("isoler un moment : tout le reste part, les sous-titres suivent", () => {
  const { d, v, cap } = captioned();
  M.appendMedia(d, VIDEO2, 99);
  assert.ok(v);
  const kept = M.isolateRange(d, 2, 5);
  assert.equal(kept, 3);
  assert.deepEqual(spans(d, "tv1"), [[0, 3]]);
  assert.equal(on(d, "tv1")[0].in, 2);
  M.reflowCaptions(d);
  assert.ok(cap.words.filter((w) => !w.cut).every((w) => w.start >= 0 && w.end <= 3.001));
});

test("zoom : le visage ne bouge pas, et reste dans le cadre", () => {
  const canvas = { w: 1080, h: 1920 }, media = { w: 1920, h: 1080 };
  // visage au tiers gauche d'une vidéo 16:9 recadrée en 9:16 : à l'échelle 1 il
  // est à X = 0.5 + (1/3 - 0.5) * (1920 * (1920/1080) / 1080) ≈ -0.03 → borné à 0.1
  const c = M.zoomCenter({ x: 1 / 3, y: 0.5, w: 0.2 }, media, canvas, 1.2);
  near(c.x, 0.5 + (0.1 - 0.5) * (1 - 1.2));
  near(c.y, 0.5);
  // portrait, visage un peu haut : le centre descend pour que la tête reste en place
  const p = M.zoomCenter({ x: 0.5, y: 0.35, w: 0.2 }, { w: 1080, h: 1920 }, canvas, 1.15);
  near(p.x, 0.5);
  near(p.y, 0.5 + (0.35 - 0.5) * (1 - 1.15));
  // sans visage : léger recentrage vers le haut
  assert.ok(M.zoomCenter(null, media, canvas, 1.2).y > 0.5);
});

test("rythme : coupes aux fins de phrases, sinon régulières, jamais de bout trop court", () => {
  const c = { start: 10, dur: 20, in: 5, speed: 1 };
  // fins de phrases (source) : 9, 12.5, 16, 24, 26 → timeline 14, 17.5, 21, 29, 31
  const t = M.rhythmSplits(c, [9, 12.5, 16, 24, 26], { minPiece: 2, maxPiece: 6 });
  assert.deepEqual(t, [14, 17.5, 21, 27]);      // 21 → 29 trop loin : coupe régulière à 27
  assert.deepEqual(M.rhythmSplits({ start: 0, dur: 7, in: 0, speed: 1 }, [3], { minPiece: 2, maxPiece: 6 }), []);
  const sped = M.rhythmSplits({ start: 0, dur: 10, in: 0, speed: 2 }, [8], { minPiece: 2, maxPiece: 6 });
  assert.deepEqual(sped, [4]);                   // 8 s de source à ×2 = 4 s de timeline
});
