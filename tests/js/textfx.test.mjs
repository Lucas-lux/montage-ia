// Mots-clés des sous-titres dans l'aperçu (engine/web/studio/textfx.js) : mêmes
// règles que l'export (engine/pipeline/ass_edit.py).
import { test } from "node:test";
import assert from "node:assert/strict";

import { KW_FROM, kwFactor, wordStates } from "../../engine/web/studio/textfx.js";

test("rebond d'un mot-clé : de KW_FROM à kw_scale, en dépassant un peu", () => {
  const c = { kw_scale: 1.5, kw_pop: true };
  assert.equal(kwFactor(c, 0), 1.5 * KW_FROM);
  assert.equal(kwFactor(c, 5), 1.5);
  assert.ok(Math.max(...Array.from({ length: 28 }, (_, i) => kwFactor(c, i / 100))) > 1.5);
  assert.equal(kwFactor({ kw_scale: 1.5 }, 0), 1.5);          // sans rebond : tout de suite à sa taille
});

test("états des mots : mot-clé repéré, taille du moment", () => {
  const c = { mode: "reveal", pop: false, kw_scale: 1.55, kw_pop: false };
  const words = [{ text: "trop", start: 0 }, { text: "vite", start: 0.4, k: true }];
  const st = wordStates(c, 2, 1, words, 0.5);
  assert.equal(st[0].kw, false);
  assert.equal(st[1].kw, true);
  assert.equal(st[1].ks, 1.55);
  assert.equal(st[1].lit, true);
});
