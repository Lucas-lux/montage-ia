/* Rendu des textes dans l'aperçu : mêmes couches, mêmes réglages que l'export
   (engine/pipeline/ass_edit.py), en CSS.

   Un texte est une pile (.tstack) : sous le texte principal, des copies de la
   même ligne — relief 3D, ombre douce, lueur, second contour — qui ne
   diffèrent que par la couleur, l'épaisseur du contour, le flou ou un
   décalage. Même mise en page pour toutes : elles se superposent exactement.

   Correspondances avec libass :
     - contour ASS (vers l'extérieur) = -webkit-text-stroke deux fois plus
       épais, moitié cachée sous le remplissage (paint-order) ;
     - \blur N ≈ filter: blur(N × BLUR_CSS) ;
     - l'ombre nette (\shad) = text-shadow sans flou.

   Aucune dépendance : l'éditeur short (index.html) l'importe aussi. */

// Mêmes constantes que ass_edit.py.
export const GLOW_BORD = 0.25, GLOW_BLUR = 0.5, EXTRUDE_STEP = 2, POP = 1.12, DIM = 0.4;
export const SHADOW_OP = 1 - 0x60 / 255, GLOW_OP = 1 - 0x40 / 255;
export const BLUR_CSS = 0.8;             // \blur libass -> rayon CSS (réglé à l'œil sur des rendus)

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function rgb(hex) {
  const hx = String(hex || "#FFFFFF").replace("#", "").trim();
  const full = hx.length === 3 ? hx.split("").map((x) => x + x).join("") : hx;
  const n = parseInt(full, 16);
  return Number.isNaN(n) ? [255, 255, 255] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function hexA(hex, alpha) {
  const [r, g, b] = rgb(hex);
  return `rgba(${r},${g},${b},${clamp(alpha, 0, 1)})`;
}

/** Couleur à la fraction `k` entre deux couleurs (même arrondi que ass_edit._mix). */
export function mix(c1, c2, k) {
  const a = rgb(c1), b = rgb(c2);
  const v = a.map((x, i) => Math.floor(x + (b[i] - x) * k + 0.5));
  return `rgb(${v[0]},${v[1]},${v[2]})`;
}

/** Couches d'un texte, de la plus basse à la plus haute (comme `_layers`). */
export function layers(c) {
  const bord = clamp(+c.outline || 0, 0, 60);
  const out = [];
  const depth = clamp(+c.extrude || 0, 0, 80);
  if (depth > 0) {
    const n = Math.max(1, Math.ceil(depth / EXTRUDE_STEP));
    for (let i = n; i >= 1; i--) {
      const off = (depth * i) / n;
      out.push({ kind: "extrude", dx: off, dy: off, bord, blur: 0, color: c.extrude_col, op: 1 });
    }
  }
  const shad = clamp(+c.shadow || 0, 0, 60), sblur = clamp(+c.shadow_blur || 0, 0, 60);
  if (shad > 0 && sblur > 0) out.push({ kind: "shadow", dx: shad, dy: shad, bord, blur: sblur, color: c.shadow_col, op: SHADOW_OP });
  const glow = clamp(+c.glow || 0, 0, 80);
  if (glow > 0) out.push({ kind: "glow", dx: 0, dy: 0, bord: bord + glow * GLOW_BORD, blur: glow * GLOW_BLUR, color: c.glow_col, op: GLOW_OP });
  const o2 = clamp(+c.outline2 || 0, 0, 60);
  if (o2 > 0) out.push({ kind: "outline2", dx: 0, dy: 0, bord: bord + o2, blur: 0, color: c.outline2_col, op: 1 });
  out.push({ kind: "main", dx: 0, dy: 0, bord, blur: 0 });
  return out;
}

/** État de chaque mot pour le surlignage (comme `_word_states`). */
export function wordStates(c, n, k) {
  const mode = c.mode || "word";
  const out = [];
  for (let j = 0; j < n; j++) {
    const lit = ["word", "reveal", "dim"].includes(mode) ? j === k : mode === "sweep" ? k >= 0 && j <= k : false;
    out.push({
      lit,
      f: mode === "reveal" && j > k ? 0 : mode === "dim" && j !== k ? DIM : 1,
      grow: !!c.pop && j === k && ["word", "reveal", "dim"].includes(mode),
    });
  }
  return out;
}

const CHAR = /\n|[\s\S]/g;

/** Construit la pile de couches. `words` : textes des mots ; `states` : état
 *  de chaque mot (ou null) ; `k` : pixels d'écran par pixel de sortie ;
 *  `perChar` : découper aussi en lettres (animation lettre à lettre). Les
 *  lettres ne sont séparées que si c'est utile, comme à l'export.
 *  Renvoie { root, main, units } — `units.char` / `units.word` : les
 *  éléments à animer lettre à lettre ou mot à mot (toutes couches confondues). */
export function buildText(c, k, words, states, perChar = false) {
  const root = document.createElement("span");
  root.className = "tstack";
  root.style.letterSpacing = (+c.spacing || 0) * k + "px";
  root.style.fontStyle = c.italic ? "italic" : "normal";
  const toks = words.map((w) => (c.upper ? String(w).toUpperCase() : String(w)));
  const total = toks.reduce((a, t) => a + [...t.matchAll(CHAR)].filter((m) => m[0] !== "\n").length, 0);
  const gradient = !!c.color2 && !c.hollow;
  const chars = gradient || perChar;
  const units = { char: [], word: [] };
  let main = null;
  for (const lay of layers(c)) {
    const isMain = lay.kind === "main";
    const span = document.createElement("span");
    span.className = isMain ? "txt" : "tl";
    // même marge que la boîte pour que toutes les couches se superposent
    if (c.box) {
      // libass : la boîte déborde du texte de `outline` de chaque côté
      span.style.padding = (c.outline * k) + "px";
      if (isMain) span.style.background = hexA(c.outline_col, 1 - (c.box_alpha || 0));
    }
    const strokeCol = isMain ? c.outline_col : lay.color;
    if (!(isMain && c.box) && lay.bord > 0) {
      // contour seul : trait centré (même épaisseur visible que libass)
      span.style.webkitTextStroke = (lay.bord * k * (isMain && c.hollow ? 1 : 2)) + "px " + strokeCol;
      span.style.paintOrder = "stroke fill";
    }
    if (!isMain) {
      span.style.color = lay.color;
      span.style.opacity = lay.op;
      if (lay.dx || lay.dy) span.style.transform = `translate(${lay.dx * k}px, ${lay.dy * k}px)`;
      if (lay.blur) span.style.filter = `blur(${lay.blur * k * BLUR_CSS}px)`;
    } else {
      if (c.hollow) span.style.color = "transparent";
      const shad = clamp(+c.shadow || 0, 0, 60);
      if (shad > 0 && !(+c.shadow_blur > 0)) {
        span.style.textShadow = `${shad * k}px ${shad * k}px 0 ${hexA(c.shadow_col || "#000000", SHADOW_OP)}`;
      }
    }
    let ci = 0;
    toks.forEach((tok, j) => {
      const ws = states ? states[j] : { lit: false, f: 1, grow: false };
      const w = document.createElement("w");
      if (isMain && !c.hollow) w.style.color = ws.lit ? c.hl : c.color;
      w._f = ws.f;
      if (ws.f !== 1) w.style.opacity = ws.f;
      if (ws.grow) w.style.fontSize = POP + "em";
      units.word.push({ el: w, i: j });
      if (!chars) {
        w.textContent = tok;
      } else {
        for (const m of tok.matchAll(CHAR)) {
          if (m[0] === "\n") { w.appendChild(document.createElement("br")); continue; }
          const ch = document.createElement("l");
          ch.textContent = m[0];
          if (isMain && gradient && !ws.lit) ch.style.color = mix(c.color, c.color2, ci / Math.max(1, total - 1));
          units.char.push({ el: ch, i: ci });
          w.appendChild(ch);
          ci++;
        }
      }
      span.appendChild(w);
      if (j < toks.length - 1) span.appendChild(document.createTextNode(" "));
    });
    root.appendChild(span);
    if (isMain) main = span;
  }
  root._units = units;
  return { root, main, units };
}

/** Applique un état animé (anim.js `state`) à une pile : transformation,
 *  opacité et flou d'ensemble, puis opacité et flou par lettre ou par mot. */
export function applyState(root, c, st, k, canvas, unitState) {
  const s = st.s, sx = st.sx, sy = st.sy;
  const dx = st.dx * canvas.w * k, dy = st.dy * canvas.h * k;
  const rot = (+c.rotation || 0) + st.r;
  root.style.transform = `translate(${dx}px, ${dy}px) rotate(${rot}deg) scale(${s * sx}, ${s * sy})`;
  root.style.opacity = clamp(+(c.opacity ?? 1), 0, 1) * st.o;
  root.style.filter = st.b > 0.01 ? `blur(${st.b * k * BLUR_CSS}px)` : "";
  const units = root._units;
  if (!units) return;
  for (const per of ["char", "word"]) {
    const list = unitState ? unitState(per) : null;
    for (const u of units[per]) {
      const v = list ? list[u.i] : null;
      const f = u.el._f ?? 1;                  // mot masqué ou estompé par le surlignage
      const o = f * (v ? v.o : 1);
      u.el.style.opacity = o === 1 ? "" : o;
      u.el.style.filter = v && v.b > 0.01 ? `blur(${v.b * k * BLUR_CSS}px)` : "";
    }
  }
}
