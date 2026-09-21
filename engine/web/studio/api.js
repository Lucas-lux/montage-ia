/* Appels au moteur local. */

export async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = "Erreur " + r.status;
    try { detail = (await r.json()).detail || detail; } catch (e) { /* pas du JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}

export const post = (url, body) => api(url, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

export const del = (url) => api(url, { method: "DELETE" });

/** Envoi de fichiers avec progression (fetch ne sait pas la donner). */
export function upload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total, e.loaded, e.total);
    };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* réponse vide */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error((data && data.detail) || "Erreur " + xhr.status));
    };
    xhr.onerror = () => reject(new Error("Envoi interrompu (serveur injoignable)."));
    xhr.send(formData);
  });
}

/** Charge un binaire (forme d'onde). */
export async function bytes(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error("Erreur " + r.status);
  return new Uint8Array(await r.arrayBuffer());
}
