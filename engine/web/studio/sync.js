/* Montage modifié ailleurs : un agent IA (Claude Code, Codex… via le serveur
   MCP de Montage IA) travaille sur le même projet que le studio ouvert.

   Le studio demande régulièrement au moteur la révision du montage. Si elle a
   avancé sans lui, il recharge le montage (l'état d'avant reste dans Ctrl+Z) ;
   si ce sont les médias qui ont bougé (import, transcription, détourage), il
   relance leur suivi. Une sauvegarde refusée parce que le montage a changé
   entre-temps (409) mène au même rechargement : rien n'est écrasé. */

import { api } from "./api.js";
import { startPolling } from "./bin.js";
import { S, on, replaceDoc } from "./store.js";
import { toast } from "./util.js";

const Y = { timer: 0, media: null, busy: false };

export function init() {
  on("conflict", () => reload(true));
  schedule(1500);
}

function schedule(ms) {
  clearTimeout(Y.timer);
  Y.timer = setTimeout(tick, ms);
}

async function tick() {
  try {
    const r = await api(`/api/timeline/${S.pid}/rev`);
    // une révision plus récente, et rien d'entamé ici : on la prend (sauf pendant le
    // montage automatique, qui recharge lui-même le résultat)
    if (r.rev > S.rev && !S.saveTimer && !S.saving && !S.dragDepth && !S.engineJob) await reload(false);
    else if (Y.media !== null && r.media !== Y.media) startPolling();
    Y.media = r.media;
  } catch (e) { /* moteur arrêté ou projet supprimé : on réessaie plus tard */ }
  schedule(document.hidden ? 4000 : 1500);
}

async function reload(conflict) {
  if (Y.busy) return;
  Y.busy = true;
  try {
    replaceDoc(await api(`/api/timeline/${S.pid}`));
    startPolling();
    toast(conflict
      ? "Le montage vient d'être modifié ailleurs : ta dernière retouche est dans Ctrl+Z."
      : "Montage mis à jour par l'agent IA. Ctrl+Z pour revenir en arrière.", 3500);
  } catch (e) {
    toast("Rechargement du montage impossible : " + e.message, 4000);
  } finally {
    Y.busy = false;
  }
}
