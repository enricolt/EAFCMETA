// Carte in stile FUT: rarità, markup, badge di verdetto, interazione 3D.
import { esc, ic, short, fmt, finePointer, reduced } from "./util.js";

export const VERD = {
  MUST_DO: { icon: "check", label: "Conviene", cls: "ok" },
  NEUTRAL: { icon: "minus", label: "In linea", cls: "warn" },
  AVOID: { icon: "x", label: "Evita", cls: "bad" },
  ND: { icon: "info", label: "Pochi dati", cls: "nd" },
};
export const vkey = c => c.value_gap == null ? "ND" : c.verdict;

const LBL = { acceleration: "ACC", sprint_speed: "VEL", agility: "AGI", balance: "BAL", reactions: "REA", ball_control: "CTR", dribbling: "DRI", composure: "CMP", positioning: "POS", finishing: "FIN",
  shot_power: "POT", long_shots: "TLU", volleys: "VOL", curve: "EFF", fk_accuracy: "PUN", penalties: "RIG", vision: "VIS", crossing: "CRO", short_passing: "PAS", long_passing: "PLU",
  heading_accuracy: "TST", defensive_awareness: "DIF", standing_tackle: "CON", sliding_tackle: "SCI", interceptions: "INT", aggression: "AGG", jumping: "SAL", stamina: "RES", strength: "FOR",
  gk_diving: "TUF", gk_handling: "PRE", gk_kicking: "RIN", gk_positioning: "POR", gk_reflexes: "RIF" };
export const lbl = k => LBL[k] || k.slice(0, 3).toUpperCase();

export const KIND_NAME = { bronze: "Bronzo", silver: "Argento", gold: "Oro", special: "Speciale", icon: "Icona" };
// Rarità visiva: dalla versione, altrimenti dallo score finale.
export function kind(c) {
  const v = (c.version || "").toLowerCase().trim(), s = c.scores.final_score;
  if (/\bicon/.test(v)) return "icon";
  if (/^bronze/.test(v)) return "bronze";
  if (/^silver/.test(v)) return "silver";
  if (/^(gold|rare|common)/.test(v)) return "gold";
  if (v && !/^(esempio|standard|base)/.test(v)) return "special";
  return s >= 93 ? "special" : s >= 85 ? "gold" : s >= 75 ? "silver" : "bronze";
}

export function verdictBadge(c, { icon = true } = {}) {
  const k = vkey(c), v = VERD[k];
  return `<span class="badge b-${v.cls}" title="${esc(c.verdict_reason)}">${icon ? ic(v.icon, 13) : ""}${v.label}</span>`;
}
export const metaChip = c => (c.meta_level === "top" || c.meta_level === "meta") ? `<span class="badge b-meta" title="${esc(c.meta_label)}">${ic("star", 12)}Meta</span>` : "";
export const priceTag = (n, full = false) => `<span class="price num">${ic("coin", 15)}${full ? fmt(n) : short(n)}</span>`;

// Solo il disegno della carta (per griglia, dettaglio, confronto).
export function futCard(c, { cls = "" } = {}) {
  const k = kind(c), s = Math.round(c.scores.final_score), v = VERD[vkey(c)];
  const len = c.name.length, nl = len > 17 ? " nm-xxs" : len > 14 ? " nm-xs" : len > 11 ? " nm-sm" : "";
  return `<div class="fcwrap ${cls}"><div class="fc k-${k}"><div class="face">
    <i class="tex"></i><i class="holo"></i><i class="rays"></i>
    <svg class="sil" viewBox="0 0 100 100" aria-hidden="true"><use href="#sil"/></svg>
    <div class="rt"><b>${s}</b><i>${esc(c.position)}</i>${c.is_sbc ? "<em>SBC</em>" : k === "icon" ? "<em>ICON</em>" : ""}</div>
    <div class="bot"><div class="nm${nl}" title="${esc(c.name)}">${esc(c.name)}</div><div class="vr">${esc(c.version || " ")}</div>
      <div class="st">${c.top_stats.map(t => `<div><b>${t.v}</b><span>${lbl(t.k)}</span></div>`).join("")}</div></div>
    <i class="glare"></i></div></div>
    <span class="vdot v-${v.cls}" title="${esc(v.label)}">${ic(v.icon, 14)}</span></div>`;
}

// Tile completo (carta + prezzo + verdetto) con pulsante di confronto.
export function cardTile(c, i = 0, { selected = false } = {}) {
  const v = VERD[vkey(c)];
  return `<article class="cw" data-id="${c.id}" style="--i:${Math.min(i, 18)}">
    <button class="cw-open" data-open="${c.id}" aria-label="${esc(c.name)}${c.version ? ", " + esc(c.version) : ""}, ${esc(c.position)}, score ${Math.round(c.scores.final_score)}, ${v.label}, ${fmt(c.cost_credits)} crediti">
      ${futCard(c)}
      <span class="under">${priceTag(c.cost_credits)}${verdictBadge(c)}${metaChip(c)}</span>
    </button>
    <button class="cw-sel" data-cmp="${c.id}" aria-pressed="${selected}" aria-label="${selected ? "Togli dal confronto" : "Aggiungi al confronto"}: ${esc(c.name)}" title="Confronta">${ic(selected ? "check" : "plus", 16)}</button>
  </article>`;
}

// Inclinazione 3D + riflesso che segue il puntatore (solo mouse, no reduced-motion).
export function initTilt(root = document) {
  if (!finePointer() || reduced()) return;
  let raf = 0, cur = null;
  root.addEventListener("pointermove", e => {
    const el = e.target.closest?.(".cw-open, .tilt");
    if (cur && cur !== el) reset(cur);
    cur = el;
    if (!el) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const w = el.querySelector(".fcwrap"); if (!w) return;
      const r = w.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      const t = el.closest(".cw") || el;
      t.style.setProperty("--ry", ((x - .5) * 16).toFixed(2) + "deg");
      t.style.setProperty("--rx", ((.5 - y) * 14).toFixed(2) + "deg");
      t.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
      t.style.setProperty("--my", (y * 100).toFixed(1) + "%");
      t.style.setProperty("--tilt", "1");
    });
  }, { passive: true });
  const reset = el => { const t = el.closest(".cw") || el; ["--ry", "--rx", "--tilt"].forEach(p => t.style.removeProperty(p)); };
  root.addEventListener("pointerout", e => { if (cur && !cur.contains(e.relatedTarget)) { reset(cur); cur = null; } });
}
