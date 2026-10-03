// Accesso alle API (stesse rotte /api/v1 di sempre) + gestione della chiave LAN.
import { store } from "./util.js";
import { promptBox } from "./ui.js";

// token passato nell'URL (#token=…) dal link per gli amici in rete locale
const m = location.hash.match(/token=([^&]+)/);
if (m) { store.set("token", decodeURIComponent(m[1])); history.replaceState(null, "", location.pathname); }

// Le richieste partono una alla volta: il backend apre la connessione SQLite in un thread
// e la usa in un altro, quindi richieste simultanee possono dare errore 500.
let chain = Promise.resolve();
export function api(path, opts = {}, retry = true) {
  const run = chain.then(() => doApi(path, opts, retry));
  chain = run.catch(() => { });
  return run;
}
async function doApi(path, opts, retry) {
  const hd = { "Content-Type": "application/json" };
  const t = store.get("token");
  if (t) hd["X-Token"] = t;
  const r = await fetch("/api/v1" + path, { ...opts, headers: hd });
  if (r.status === 401 && retry) {
    const n = await promptBox({ title: "Serve la chiave d'accesso", text: "Te la dà chi ha avviato l'app in rete locale.", label: "Chiave di accesso" });
    if (n) { store.set("token", n); return doApi(path, opts, false); }
  }
  return r;
}
const FIELD = { creator: "creator", stance: "scelta", score: "voto", reason: "motivo", url: "link", text: "testo", card_id: "carta" };
export async function errText(r) {
  try {
    const d = await r.json(), x = d.detail;
    return Array.isArray(x) ? x.map(e => { const f = (e.loc || []).slice(1).map(k => FIELD[k] || k).join("."); return (f ? f + ": " : "") + String(e.msg).replace("Value error, ", ""); }).join("; ") : String(x || r.status);
  } catch { return "Errore " + r.status; }
}
export async function jget(path) {
  const r = await api(path);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}
// Messaggio in italiano per gli errori comuni (503/429/422/409/404), con il testo del server quando c'è.
const HTTP_IT = { 503: "Servizio non disponibile", 429: "Limite raggiunto", 422: "Dati non validi", 409: "Operazione non possibile ora", 404: "Non trovato", 401: "Accesso negato", 502: "Servizio esterno in errore", 500: "Errore interno" };
export async function errFriendly(r) {
  const t = await errText(r), p = HTTP_IT[r.status];
  return p && !t.toLowerCase().startsWith(p.toLowerCase()) ? `${p}: ${t}` : t;
}
export const send =(path, method, body) => api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
