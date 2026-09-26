/* Application de bureau (Windows) : la fenêtre native donne les vrais chemins
   des fichiers, choisis ou glissés depuis l'Explorateur. Importer ne copie
   alors plus rien : le moteur lit les rushs sur place (voir desktop.py).

   Dans un navigateur, rien de tout ça : l'import reste un envoi. */

/** Vrai dans la fenêtre de l'application (moteur WebView2). */
export const isDesktop = !!(window.chrome && window.chrome.webview);

/** Fonctions de la fenêtre (`pick_files`, `pick_folder`), ou null. */
export function desktopApi() {
  const api = () => (window.pywebview && window.pywebview.api && window.pywebview.api.pick_files
    ? window.pywebview.api : null);
  if (!isDesktop || api()) return Promise.resolve(isDesktop ? api() : null);
  return new Promise((resolve) => {
    const done = () => resolve(api());
    window.addEventListener("pywebviewready", done, { once: true });
    setTimeout(done, 4000);
  });
}

/** Boîte de dialogue de Windows : chemins choisis ([] si annulé), null hors application. */
export async function pickFiles(kind = "media", multiple = true) {
  const api = await desktopApi();
  return api ? api.pick_files(kind, multiple) : null;
}

/** Dossier choisi ("" si annulé), null hors application. */
export async function pickFolder() {
  const api = await desktopApi();
  return api ? api.pick_folder() : null;
}

/* ------------------------------------------------------ glisser-déposer */

let waiting = null;

/** À appeler PENDANT l'évènement `drop` : la fenêtre renvoie aussitôt les
 *  chemins des fichiers déposés, remis à `onPaths(paths)`. S'ils n'arrivent
 *  pas (cas rare), `fallback()` reprend l'envoi classique. */
export function awaitDroppedPaths(onPaths, fallback) {
  if (waiting) clearTimeout(waiting.timer);
  const w = { onPaths, timer: setTimeout(() => { if (waiting === w) { waiting = null; fallback(); } }, 2500) };
  waiting = w;
}

window.addEventListener("montage:paths", (e) => {
  const w = waiting;
  if (!w) return;
  waiting = null;
  clearTimeout(w.timer);
  w.onPaths(e.detail || []);
});
