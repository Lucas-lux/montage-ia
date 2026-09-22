/* Mots transcrits des médias (temps source), gardés en cache.

   Partagé par les outils IA (blancs, sous-titres) et par les repères de coupe
   de la timeline, qui disent ce qui était prononcé dans un passage retiré. */

import { api } from "./api.js";
import { S, on } from "./store.js";

const cache = new Map();          // media id -> mots
const pending = new Map();        // media id -> promesse en cours

const done = (mid) => ((S.media.get(mid) || {}).transcript || {}).status === "done";

export async function wordsOf(mid) {
  if (cache.has(mid)) return cache.get(mid);
  if (!pending.has(mid)) {
    pending.set(mid, api(`/api/timeline/${S.pid}/media/${mid}/words`)
      .then((res) => { cache.set(mid, res.words); return res.words; })
      .finally(() => pending.delete(mid)));
  }
  return pending.get(mid);
}

/** Ce qui était prononcé entre `s` et `e` (temps source), ou "" si le média
 *  n'est pas transcrit. */
export async function textBetween(mid, s, e) {
  if (!done(mid)) return "";
  try {
    const words = await wordsOf(mid);
    const t = words.filter((w) => w.end > s + 0.02 && w.start < e - 0.02).map((w) => w.text).join(" ");
    return t.length > 90 ? t.slice(0, 89) + "…" : t;
  } catch (err) { return ""; }
}

// une retranscription remplace les mots en cache
on("media", () => {
  for (const mid of [...cache.keys()]) if (!done(mid)) cache.delete(mid);
});
