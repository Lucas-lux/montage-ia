/* Polices d'un système à l'autre (même tableau qu'engine/pipeline/fonts.py).

   Les styles utilisent des polices de Windows ; sur un Mac, celles qui
   manquent sont remplacées par l'équivalent préinstallé, dans l'aperçu comme
   à l'export. */

export const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");

const MAC = {
  "Segoe UI": "Helvetica Neue",
  "Segoe UI Black": "Helvetica Neue",
  "Bahnschrift": "DIN Condensed",
  "Calibri": "Helvetica Neue",
  "Candara": "Optima",
  "Corbel": "Gill Sans",
  "Cambria": "Georgia",
  "Franklin Gothic Medium": "Avenir Next Condensed",
  "Consolas": "Menlo",
  "Segoe Script": "Snell Roundhand",
  "Ink Free": "Chalkboard SE",
};

/** Pile CSS d'une police : elle-même, son équivalent Mac, puis un repli sans empattement. */
export function fontStack(name) {
  const f = name || "Arial";
  const alt = MAC[f];
  return `"${f}"${alt ? `, "${alt}"` : ""}, Arial, "Helvetica Neue", sans-serif`;
}
