/* Textes et sous-titres sur l'aperçu : rendu WYSIWYG et gestes.

   Même géométrie que libass à l'export (engine/pipeline/ass_edit.py) :
   `x`/`y` (0..1) placent le CENTRE du bloc, `size`/`outline`/`shadow` sont en
   pixels de la sortie. Le texte lui-même (couches d'effets, surlignage) est
   construit par textfx.js ; ses animations (anim.js) sont rejouées à chaque
   image sans reconstruire la page.

   Deux couches : les textes actifs à l'instant courant, et les textes
   sélectionnés hors de leur temps (en fantôme, pour les placer à l'aveugle). */

import * as AN from "./anim.js";
import { baselineShift, emScale, fontStack } from "./fonts.js";
import * as M from "./model.js";
import { applyState, buildText, hexA, wordStates } from "./textfx.js";
import { S, begin, cancelBegin, changed, end, on, select, snapshot } from "./store.js";
import { $, clamp, h } from "./util.js";

const C = { key: "", ghostKey: "" };

export function emojiGeometry(size) {
  const esz = Math.round(size * 1.55);
  return [esz, -(size * 0.75 + esz * 0.5 + 14)];
}

export { hexA };

/** Textes à afficher à l'instant t, du plus bas au plus haut. */
function activeTexts(t) {
  const order = S.doc.tracks.filter((tr) => tr.kind === "text" && !tr.hidden).map((tr) => tr.id).reverse();
  return S.doc.clips
    .filter((c) => c.kind === "text" && !c.gone && !c.hidden && t >= c.start && t < M.clipEnd(c) &&
                   order.includes(c.track))
    .sort((a, b) => order.indexOf(a.track) - order.indexOf(b.track));
}

function wordIndex(words, t) {
  for (let i = words.length - 1; i >= 0; i--) if (t >= words[i].start) return i;
  return 0;
}

export function renderCaptions(t, force) {
  const layer = $("caplayer"), ghosts = $("ghosts");
  if (!layer) return;
  const act = activeTexts(t);
  const actIds = new Set(act.map((c) => c.id));
  const sel = S.doc.clips.filter((c) => c.kind === "text" && S.sel.has(c.id) && !actIds.has(c.id) && !c.gone);
  const gk = sel.map((c) => c.id + ":" + JSON.stringify([c.x, c.y, c.size, c.words.length])).join(",") + "|" + S.k;
  if (force || gk !== C.ghostKey) {
    C.ghostKey = gk;
    ghosts.innerHTML = "";
    sel.forEach((c) => ghosts.appendChild(buildCap(c, -1, true)));
  }
  const key = act.map((c) => {
    const words = M.liveWords(c);
    return c.id + ":" + wordIndex(words, t) + ":" + (S.sel.has(c.id) ? 1 : 0) + ":" + styleKey(c);
  }).join("|") + "|" + S.k + "|" + S.sel.size;
  if (force || key !== C.key) {
    C.key = key;
    layer.innerHTML = "";
    act.forEach((c) => layer.appendChild(buildCap(c, wordIndex(M.liveWords(c), t), false)));
  }
  // animations : rejouées à chaque image sur les éléments déjà construits
  for (const el of layer.children) {
    const c = act.find((x) => x.id === el.dataset.id);
    if (c && el._root) animate(el._root, c, t);
  }
}

/** Anime un texte à l'instant t (même état que l'export, image par image). */
function animate(root, c, t) {
  const st = AN.hasAnim(c) ? AN.state(c, t) : { ...AN.IDENTITY };
  const n = { char: root._units.char.length ? Math.max(...root._units.char.map((u) => u.i)) + 1 : 0,
              word: M.liveWords(c).length };
  applyState(root, c, st, S.k || 1, S.doc.canvas, (per) => AN.unitState(st, per, n[per]));
}

/** Les lettres sont séparées seulement si une animation lettre à lettre en a besoin. */
const perChar = (c) => ["anim_in", "anim_out", "anim_loop"].some((k) =>
  c[k] && (AN.DEFS.anims[c[k].type] || {}).per === "char");

const LOOK = ["font", "size", "bold", "upper", "color", "hl", "outline_col", "outline", "shadow", "box",
              "box_alpha", "mode", "pop", "x", "y", "emoji", "emoji_size", "emoji_dx", "emoji_dy",
              "italic", "spacing", "color2", "shadow_col", "shadow_blur", "glow", "glow_col", "outline2",
              "outline2_col", "extrude", "extrude_col", "hollow"];
const styleKey = (c) => LOOK.map((f) => c[f]).join(",") + ":" + perChar(c) + ":" +
  M.liveWords(c).map((w) => w.text).join(" ");

export function buildCap(c, wordIdx, ghost) {
  const k = S.k || 1;
  const words = M.liveWords(c);
  const solo = S.sel.size >= 1 && S.sel.has(c.id) &&
               [...S.sel].filter((id) => (S.doc.clips.find((x) => x.id === id) || {}).kind === "text").length === 1;
  const el = h("div.cap" + (S.sel.has(c.id) ? ".sel" : "") + (ghost ? ".ghost" : "") + (solo ? ".solo" : ""),
               { dataset: { id: c.id } });
  el.style.left = (c.x * 100) + "%";
  el.style.top = (c.y * 100) + "%";
  el.style.fontFamily = fontStack(c.font);
  el.style.fontSize = (c.size * k * emScale(c.font)) + "px";
  el.style.lineHeight = (c.size * k) + "px";          // interligne de libass : la taille elle-même
  el.style.fontWeight = c.bold ? 700 : 400;

  // mot actif agrandi comme libass (le mot prend vraiment plus de place :
  // il ne recouvre pas les espaces voisins, la ligne s'élargit un peu)
  const states = ["word", "sweep", "reveal", "dim"].includes(c.mode) ? wordStates(c, words.length, wordIdx) : null;
  const { root, main: inner } = buildText(c, k, words.map((w) => w.text), states, perChar(c));
  root.style.top = baselineShift(c.font) * c.size * k + "px";
  el.appendChild(root);
  el._root = root;
  if (ghost) animate(root, { ...c, anim_in: null, anim_out: null, anim_loop: null }, c.start);

  if (c.emoji) {
    const em = h("span.emoji", {}, c.emoji);
    em.style.fontSize = (c.emoji_size * k) + "px";
    em.style.left = `calc(50% + ${c.emoji_dx * k}px)`;
    em.style.top = (c.emoji_dy * k) + "px";
    em.onpointerdown = (e) => startDrag(e, c, "emoji");
    el.appendChild(em);
  }
  const rs = h("div.rs", {});
  rs.onpointerdown = (e) => startDrag(e, c, "resize");
  el.appendChild(rs);
  el.onpointerdown = (e) => {
    if (e.target !== el && !root.contains(e.target)) return;
    startDrag(e, c, "move");
  };
  el.ondblclick = (e) => { e.preventDefault(); e.stopPropagation(); editInline(el, inner, c); };
  return el;
}

/* -------------------------------------------------------------- gestes */

function nodeFor(id) {
  return $("caplayer").querySelector(`.cap[data-id="${id}"]`) ||
         $("ghosts").querySelector(`.cap[data-id="${id}"]`);
}

function startDrag(e, c, mode) {
  e.preventDefault();
  e.stopPropagation();
  if (!S.sel.has(c.id)) select(e.ctrlKey || e.metaKey ? [...S.sel, c.id] : [c.id]);
  const group = mode === "move"
    ? S.doc.clips.filter((x) => x.kind === "text" && S.sel.has(x.id)) : [c];
  const stage = $("stage");
  const rect = stage.getBoundingClientRect();
  const cx = rect.left + c.x * rect.width, cy = rect.top + c.y * rect.height;
  const st = {
    x: e.clientX, y: e.clientY, moved: false,
    d0: Math.max(12, Math.hypot(e.clientX - cx, e.clientY - cy)),
    items: group.map((g) => ({ c: g, x: g.x, y: g.y, size: g.size, dx: g.emoji_dx, dy: g.emoji_dy })),
  };
  begin();
  const move = (ev) => {
    const ddx = ev.clientX - st.x, ddy = ev.clientY - st.y;
    if (!st.moved && Math.abs(ddx) + Math.abs(ddy) < 3) return;
    st.moved = true;
    if (mode === "move") {
      let dx = ddx / rect.width, dy = ddy / rect.height;
      let sx = false, sy = false;
      if (st.items.length === 1 && !ev.shiftKey) {
        if (Math.abs(st.items[0].x + dx - 0.5) < 0.015) { dx = 0.5 - st.items[0].x; sx = true; }
        if (Math.abs(st.items[0].y + dy - 0.5) < 0.015) { dy = 0.5 - st.items[0].y; sy = true; }
      }
      $("guideV").classList.toggle("hidden", !sx);
      $("guideH").classList.toggle("hidden", !sy);
      st.items.forEach((it) => {
        it.c.x = M.r4(clamp(it.x + dx, -0.2, 1.2));
        it.c.y = M.r4(clamp(it.y + dy, -0.2, 1.2));
        it.c.moved = true;
        const n = nodeFor(it.c.id);
        if (n) { n.style.left = it.c.x * 100 + "%"; n.style.top = it.c.y * 100 + "%"; }
      });
    } else if (mode === "resize") {
      const it = st.items[0];
      const d = Math.hypot(ev.clientX - cx, ev.clientY - cy);
      it.c.size = Math.round(clamp(it.size * d / st.d0, 12, 400));
      const g = emojiGeometry(it.c.size);
      it.c.emoji_size = g[0];
      if (!it.c.emoji_moved) it.c.emoji_dy = g[1];
      const n = nodeFor(it.c.id);
      if (n) { n.style.fontSize = it.c.size * S.k * emScale(it.c.font) + "px"; n.style.lineHeight = it.c.size * S.k + "px"; }
    } else {
      const it = st.items[0];
      it.c.emoji_dx = M.r4(it.dx + ddx / S.k);
      it.c.emoji_dy = M.r4(it.dy + ddy / S.k);
      it.c.emoji_moved = true;
      const n = nodeFor(it.c.id);
      const em = n && n.querySelector(".emoji");
      if (em) { em.style.left = `calc(50% + ${it.c.emoji_dx * S.k}px)`; em.style.top = it.c.emoji_dy * S.k + "px"; }
    }
    changed({ light: true, reason: "caption" });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    $("guideV").classList.add("hidden");
    $("guideH").classList.add("hidden");
    if (st.moved) end("caption"); else cancelBegin();
    renderCaptions(S.t, true);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/* ------------------------------------------------------- texte corrigé */

function editInline(el, inner, c) {
  select([c.id]);
  const box = h("span.editbox", { contenteditable: "true", spellcheck: "true" },
                M.liveWords(c).map((w) => w.text).join(" "));
  box.style.color = c.color;
  inner.replaceWith(box);
  box.focus();
  document.getSelection().selectAllChildren(box);
  const commit = (ok) => {
    box.removeEventListener("blur", onBlur);
    if (ok) {
      snapshot();
      setText(c, box.innerText);
      changed({ reason: "text" });
    }
    renderCaptions(S.t, true);
  };
  const onBlur = () => commit(true);
  box.addEventListener("blur", onBlur);
  box.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter" && !ev.shiftKey && c.mode !== "none") { ev.preventDefault(); box.blur(); }
    if (ev.key === "Escape") { ev.preventDefault(); box.removeEventListener("blur", onBlur); commit(false); }
  });
}

/** Remplace le texte d'un texte ou d'un sous-titre en gardant son calage.
 *  Même nombre de mots : chaque mot garde ses horaires (et sa place dans la
 *  source). Sinon la durée est répartie au prorata de la longueur des mots —
 *  en temps SOURCE pour un sous-titre lié à la voix, qui le reste. */
export function setText(c, text) {
  const raw = String(text).replace(/\r/g, "");
  if (c.mode === "none" && c.words.length <= 1) {
    // texte libre : un seul bloc, retours à la ligne compris
    const t = raw.trim();
    c.hidden = !t;
    c.words = t ? [{ text: t, start: c.start, end: M.clipEnd(c) }] : [];
    return;
  }
  const toks = raw.trim().split(/\s+/).filter(Boolean);
  const live = M.liveWords(c);
  if (!toks.length) { c.hidden = true; return; }
  c.hidden = false;
  if (toks.length === live.length) {
    toks.forEach((t, i) => { live[i].text = t; });
    return;
  }
  const total = toks.reduce((a, t) => a + t.length, 0) || 1;
  const anchored = c.auto && live.length && live.every((w) => w.m);
  if (anchored) {
    const m = live[0].m, s0 = live[0].s, s1 = live[live.length - 1].e;
    let at = s0;
    c.words = toks.map((tok) => {
      const d = (s1 - s0) * tok.length / total;
      const w = { text: tok, start: 0, end: 0, m, s: M.r4(at), e: M.r4(at + d) };
      at += d;
      return w;
    });
    M.reflowCaptions(S.doc);
    return;
  }
  const t0 = live.length ? live[0].start : c.start;
  const t1 = live.length ? live[live.length - 1].end : M.clipEnd(c);
  const span = Math.max(0.2, t1 - t0);
  let at = t0;
  c.words = toks.map((tok) => {
    const d = span * tok.length / total;
    const w = { text: tok, start: M.r4(at), end: M.r4(at + d) };
    at += d;
    return w;
  });
}

/** Aperçu miniature d'un style (mêmes règles que sur la vidéo) : la carte
 *  qu'on clique pour le choisir. */
export function stylePreview(look, { words = ["Ton", "texte", "ici"], height = 58 } = {}) {
  const px = clamp((look.size || 86) * 0.2, 12, 19);
  const k = px / (look.size || 86);
  const states = ["word", "sweep", "reveal", "dim"].includes(look.mode) ? wordStates(look, words.length, 1) : null;
  const { root } = buildText(look, k, words, states);
  applyState(root, look, { ...AN.IDENTITY }, k, { w: 1, h: 1 }, null);
  return h("div.stylepv", {
    style: { height: height + "px", fontFamily: fontStack(look.font), fontSize: px * emScale(look.font) + "px",
             lineHeight: px + "px", fontWeight: look.bold === false ? 400 : 700 },
  }, root);
}

on("select", () => renderCaptions(S.t, true));
on("layout", () => renderCaptions(S.t, true));
// une police vient d'arriver : mesures (ligne de base) et rendu à refaire
document.fonts.addEventListener("loadingdone", () => { if (S.doc) renderCaptions(S.t, true); });
