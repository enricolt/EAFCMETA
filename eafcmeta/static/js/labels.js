// Etichette in italiano condivise dalle viste Regole, Ricerca, Automatico e dal dettaglio carta.
import { esc, ic } from "./util.js";

export const STANCE_LONG = { yes: "Sì, da prendere", maybe: "Dipende", no: "No, da evitare" };
export const STANCE_SHORT = { yes: "Sì", maybe: "Dipende", no: "No" };
export const STANCE_CLS = { yes: "ok", maybe: "warn", no: "bad" };
export const stanceBadge = s => `<span class="badge b-${STANCE_CLS[s] || "nd"}">${ic(s === "yes" ? "check" : s === "no" ? "x" : "minus", 13)}${STANCE_SHORT[s] || esc(s)}</span>`;

// Criteri con cui i pro giudicano una carta (stesso vocabolario di config/criteria.json).
export const CRITERIA = {
  pace: "scatto e velocità", finishing: "finalizzazione e tiro", dribbling: "dribbling e agilità", passing: "passaggi e visione",
  defending: "difesa", physical: "fisico e forza", stamina: "resistenza", height: "altezza e corporatura", body_type: "body type",
  animations: "animazioni e sensazione di gioco", weak_foot: "piede debole", skill_moves: "skill moves", playstyle: "PlayStyle",
  goalkeeping: "parate", price: "prezzo",
};
export const critName = k => CRITERIA[k] || String(k || "").replaceAll("_", " ");

export const ROLE_IT = { ST: "attaccante", CF: "seconda punta", W: "esterno", CAM: "trequartista", CM: "centrocampista", CDM: "mediano",
  FB: "terzino", WB: "esterno basso", CB: "difensore centrale", GK: "portiere" };
export const roleName = r => ROLE_IT[r] || r;

export const pct = (x, d = 0) => (x * 100).toLocaleString("it-IT", { maximumFractionDigits: d }) + "%";
export const num = (x, d = 1) => Number(x).toLocaleString("it-IT", { maximumFractionDigits: d });
export const signed = (x, d = 1) => (x > 0 ? "+" : x < 0 ? "−" : "") + num(Math.abs(x), d);

// "2026-10-03 00:44" (UTC, dal server) -> "3 ott, 02:44" nell'ora locale
export function when(ts) {
  if (!ts) return "—";
  const m = String(ts).match(/^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)/);
  if (!m) return String(ts);
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]));
  return isNaN(d) ? String(ts) : d.toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
export const day = ts => {
  const m = String(ts || "").match(/^(\d{4})-(\d\d)-(\d\d)/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString("it-IT", { day: "numeric", month: "short" }) : "";
};

// Un link è mostrato solo se è http(s): niente "javascript:" anche se il dato fosse sporco.
export const safeUrl = u => /^https?:\/\//i.test(u || "") ? u : "";
export const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return "fonte"; } };
export const srcLink = (u, label = "") => safeUrl(u) ? `<a class="src" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${ic("ext", 13)}${esc(label || host(u))}</a>` : "";
