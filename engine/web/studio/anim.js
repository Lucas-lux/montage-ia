/* Animations d'entrée, de sortie et en boucle (textes, vidéos, images).

   Jumeau d'engine/timeline/animations.py : mêmes définitions
   (animations.json), mêmes courbes, mêmes règles de combinaison. L'aperçu
   calcule ici l'état d'un clip à chaque image ; l'export le calcule en Python
   pour chaque image de la vidéo. Un test compare les deux. */

export const IDENTITY = { o: 1, dx: 0, dy: 0, s: 1, sx: 1, sy: 1, r: 0, b: 0 };
const MULT = new Set(["o", "s", "sx", "sy"]);
const KEYS = Object.keys(IDENTITY);
export const UNIT_PROPS = ["o", "b"];
const MIN_SPEED = 0.25, MAX_SPEED = 4;

/** Définitions ({ kinds, anims }), chargées par `loadAnimations`. */
export const DEFS = { kinds: {}, anims: {} };

export async function loadAnimations(fetchJson) {
  const data = await fetchJson("/web/studio/animations.json");
  DEFS.kinds = data.kinds;
  DEFS.anims = data.anims;
  return DEFS;
}
export function setDefinitions(data) {
  DEFS.kinds = data.kinds;
  DEFS.anims = data.anims;
}

/* ------------------------------------------------------------- courbes */

const C1 = 1.70158, C3 = C1 + 1, C4 = (2 * Math.PI) / 3;

function outBounce(u) {
  const n = 7.5625, d = 2.75;
  if (u < 1 / d) return n * u * u;
  if (u < 2 / d) { u -= 1.5 / d; return n * u * u + 0.75; }
  if (u < 2.5 / d) { u -= 2.25 / d; return n * u * u + 0.9375; }
  u -= 2.625 / d;
  return n * u * u + 0.984375;
}

export const EASE = {
  linear: (u) => u,
  inQuad: (u) => u * u,
  outQuad: (u) => 1 - (1 - u) * (1 - u),
  inOutQuad: (u) => (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2),
  inCubic: (u) => u ** 3,
  outCubic: (u) => 1 - (1 - u) ** 3,
  inOutCubic: (u) => (u < 0.5 ? 4 * u ** 3 : 1 - (-2 * u + 2) ** 3 / 2),
  inBack: (u) => C3 * u ** 3 - C1 * u * u,
  outBack: (u) => 1 + C3 * (u - 1) ** 3 + C1 * (u - 1) ** 2,
  outElastic: (u) => (u === 0 || u === 1 ? u : 2 ** (-10 * u) * Math.sin((u * 10 - 0.75) * C4) + 1),
  outBounce,
  inOutSine: (u) => -(Math.cos(Math.PI * u) - 1) / 2,
};

export function ease(name, u) {
  return (EASE[name] || EASE.linear)(Math.min(1, Math.max(0, u)));
}

/* --------------------------------------------------------- images clés */

/** Valeurs des propriétés à la progression `p` (0..1). */
export function sample(kf, p, defaultEase = "linear") {
  if (!kf || !kf.length) return { ...IDENTITY };
  p = Math.min(1, Math.max(0, p));
  if (p <= kf[0][0]) return { ...IDENTITY, ...kf[0][1] };
  if (p >= kf[kf.length - 1][0]) return { ...IDENTITY, ...kf[kf.length - 1][1] };
  let i = 0;
  while (!(kf[i][0] <= p && p < kf[i + 1][0])) i++;
  const a = kf[i], b = kf[i + 1];
  const span = b[0] - a[0];
  const u = span > 0 ? (p - a[0]) / span : 1;
  const e = ease(a[2] || defaultEase, u);
  const va = { ...IDENTITY, ...a[1] }, vb = { ...IDENTITY, ...b[1] };
  const out = {};
  for (const k of KEYS) out[k] = va[k] + (vb[k] - va[k]) * e;
  return out;
}

export function combine(acc, v) {
  for (const k of KEYS) {
    if (MULT.has(k)) acc[k] *= v[k];
    else acc[k] += v[k];
  }
  return acc;
}

/** Opacité et flou de chaque lettre (ou mot) d'une animation `per`. */
export function unitValues(d, p, n) {
  const stagger = Math.min(1, Math.max(0, d.stagger ?? 0.5));
  const loop = d.kind === "loop";
  const w = 1 - stagger;
  const out = [];
  for (let i = 0; i < n; i++) {
    const j = d.reverse ? n - 1 - i : i;
    let u;
    if (loop) u = (((p + (stagger * j) / Math.max(1, n)) % 1) + 1) % 1;
    else if (w > 1e-9) u = (p - (stagger * j) / Math.max(1, n)) / w;
    else u = p >= (stagger * j) / Math.max(1, n) - 1e-9 ? 1 : 0;
    const v = sample(d.ukf, u, d.ease || "linear");
    out.push({ o: v.o, b: v.b });
  }
  return out;
}

/* ------------------------------------------------------------- d'un clip */

function get(c, key) {
  const a = c[key];
  if (!a || typeof a !== "object") return null;
  const d = DEFS.anims[a.type];
  return d ? [d, a] : null;
}

/** Durées d'entrée et de sortie, réduites ensemble si le clip est trop court. */
export function timing(c) {
  const L = c.dur || 0;
  const ai = get(c, "anim_in"), ao = get(c, "anim_out");
  let di = ai ? (ai[1].dur || ai[0].dur || 0.5) : 0;
  let dout = ao ? (ao[1].dur || ao[0].dur || 0.5) : 0;
  if (di + dout > L && L > 0) {
    const k = L / (di + dout);
    di *= k;
    dout *= k;
  }
  return [di, dout];
}

export const hasAnim = (c) => !!(get(c, "anim_in") || get(c, "anim_out") || get(c, "anim_loop"));

/** État animé du clip à l'instant `t` : propriétés combinées et, s'il y en
 *  a, `units` = [[définition, progression]] des animations lettre à lettre. */
export function state(c, t) {
  const st = { ...IDENTITY };
  const units = [];
  const start = c.start, L = c.dur || 0;
  const [di, dout] = timing(c);
  const parts = [
    ["anim_in", di > 0 && t < start + di, di > 0 ? (t - start) / di : 1],
    ["anim_out", dout > 0 && t > start + L - dout, dout > 0 ? (t - (start + L - dout)) / dout : 0],
  ];
  for (const [key, active, p0] of parts) {
    const got = get(c, key);
    if (!got) continue;
    const d = got[0];
    const p = Math.min(1, Math.max(0, p0));
    if (d.per) units.push([d, p]);
    else if (active) combine(st, sample(d.kf, p, d.ease || "linear"));
  }
  const loop = get(c, "anim_loop");
  if (loop) {
    const [d, a] = loop;
    let p;
    if (d.span) p = L > 0 ? (t - start) / L : 0;
    else {
      const speed = Math.min(MAX_SPEED, Math.max(MIN_SPEED, a.speed || 1));
      p = ((((t - start) * speed) / (d.period || 1)) % 1 + 1) % 1;
    }
    if (d.per) units.push([d, p]);
    else combine(st, sample(d.kf, p, d.ease || "linear"));
  }
  if (units.length) st.units = units;
  return st;
}

/** Opacité et flou combinés de chaque lettre (ou mot) : null sans animation `per`. */
export function unitState(st, per, n) {
  const list = (st.units || []).filter(([d]) => d.per === per);
  if (!list.length) return null;
  const out = Array.from({ length: n }, () => ({ o: 1, b: 0 }));
  for (const [d, p] of list) {
    unitValues(d, p, n).forEach((v, i) => { out[i].o *= v.o; out[i].b += v.b; });
  }
  return out;
}

/** Animations proposées pour un genre de clip (« text » ou « media ») et un moment. */
export function choices(kind, target) {
  return Object.entries(DEFS.anims)
    .filter(([, d]) => d.kind === kind && (d.for || ["text", "media"]).includes(target))
    .map(([id, d]) => ({ id, ...d }));
}
