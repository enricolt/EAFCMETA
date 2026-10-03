// Vista "Regole": le regole dichiarative del giudizio, l'apprendimento dai pareri e l'approvazione delle proposte.
import { $, $$, esc, ic } from "../util.js";
import { jget, send, errFriendly } from "../api.js";
import { state, loadCards } from "../state.js";
import { toast, toastErr } from "../ui.js";
import { critName, roleName, num, signed } from "../labels.js";

const TITLES = {
  "forza-stat-alte": "Punti di forza del ruolo", "debolezza-stat-basse": "Punti deboli del ruolo", "scatto-vertice": "Scatto da vertice",
  "scatto-basso-offensivi": "Scatto insufficiente per chi attacca", "difensore-lento": "Difensore lento", "playstyle-utili": "PlayStyle utili al ruolo",
  "playstyle-fuori-ruolo": "PlayStyle fuori ruolo", "playstyle-sconosciuti": "PlayStyle non riconosciuti", "body-type-reattivo": "Body type reattivo",
  "skill-5-stelle": "Skill moves a 5 stelle", "skill-poche-stelle": "Poche skill moves", "piede-debole-5": "Piede debole a 5 stelle", "piede-debole-basso": "Piede debole scarso",
};
export const ruleName = id => TITLES[id] || id.replace(/^learned-/, "").replaceAll("-", " ");
const STATUS = { active: ["Attiva", "ok", "check"], proposed: ["Proposta", "warn", "bulb"], rejected: ["Rifiutata", "nd", "x"] };
const CMP = { gte: "almeno", gt: "più di", lte: "al massimo", lt: "meno di" };
const GROUP = { in_role: "PlayStyle+ utili al ruolo", off_role: "PlayStyle+ fuori ruolo", unknown: "PlayStyle non riconosciuti" };
const stat = k => state.meta?.stat_names?.[k] || String(k).replaceAll("_", " ");

function title(r) {
  if (TITLES[r.id]) return TITLES[r.id];
  if (r.source === "learned" || r.id.startsWith("learned-")) {
    const pos = (r.effect?.score ?? 0) >= 0 && !r.con_text;
    return `${pos ? "Premiato" : "Penalizzato"}: ${critName(r.criterion)}${r.roles?.length ? ` (${r.roles.map(roleName).join(", ")})` : ""}`;
  }
  return r.id.replaceAll("-", " ");
}
function cmpText(w) {
  const k = ["gte", "gt", "lte", "lt"].find(x => x in w);
  return k ? `${CMP[k]} ${num(w[k], 1)}` : "";
}
function whenText(w) {
  const c = cmpText(w);
  switch (w.type) {
    case "stat": return `${stat(w.stat)} ${c}`;
    case "avg": return `la media di ${(w.stats || []).map(stat).join(" e ")} è ${c}`;
    case "role_top_stats": return `tra le ${w.top_n} statistiche più importanti del ruolo ce ne sono a ${num(w.gte ?? w.gt ?? 0, 0)} o più (ne cito al massimo ${w.max_items})`;
    case "role_weak_stats": return `statistiche di peso almeno ${w.min_weight} per il ruolo sono ${c}`;
    case "playstyle": return `ha il PlayStyle ${w.name}`;
    case "playstyle_group": return GROUP[w.group] || "PlayStyle";
    case "body_type": return `body type ${(w.in || []).join(", ")}`;
    case "skill_moves": return `skill moves ${c} stelle`;
    case "weak_foot": return `piede debole ${c} stelle`;
    case "height_cm": return `altezza ${c} cm`;
    case "weight_kg": return `peso ${c} kg`;
    case "price": return `prezzo ${c}`;
    default: return w.type || "";
  }
}
const fill = t => String(t || "").replace(/\{\w+[^}]*\}/g, "…");
function rolesText(r) {
  if (r.roles?.length) return r.roles.map(x => `<span class="pos-chip" title="${esc(roleName(x))}">${esc(x)}</span>`).join("");
  return r.roles_except?.length ? `<span class="rule-all">tutti i ruoli tranne ${r.roles_except.map(roleName).join(", ")}</span>` : `<span class="rule-all">tutti i ruoli</span>`;
}

export default function view() {
  let root, data = null, filter = "all", fresh = new Set(), learnOut = "", alive = true;
  const cap = () => data.settings.max_rule_delta, capTot = () => data.settings.max_total_delta;

  function effectBox(r) {
    const s = r.effect?.score ?? 0;
    if (!s) return `<div class="rule-fx zero" title="Questa regola scrive solo il testo nel giudizio"><b class="num">0</b><small>solo testo</small></div>`;
    return `<div class="rule-fx ${s > 0 ? "up" : "down"}" title="Variazione dello score base quando la regola scatta (tetto ±${num(cap())})"><b class="num">${signed(s, 2)}</b><small>punti allo score</small></div>`;
  }
  function evidence(r) {
    const e = r.evidence || {};
    if (!e.mentions) return r.source === "manual" ? `<p class="rule-ev mut">Regola scritta a mano nel motore: non dipende dai pareri.</p>` : "";
    return `<p class="rule-ev">${ic("scale", 14)}<span><b class="num">${e.mentions}</b> menzioni da <b>${esc((e.creators || []).join(", "))}</b> · <span class="ev-pos">${e.positive ?? 0} a favore</span>, <span class="ev-neg">${e.negative ?? 0} contro</span></span></p>`;
  }
  function ruleCard(r) {
    const [sl, sc, si] = STATUS[r.status] || [r.status, "nd", "info"];
    const pro = r.pro_text ? `<li class="pro">${ic("check", 14)}<span>${esc(fill(r.pro_text))}</span></li>` : "", con = r.con_text ? `<li class="con">${ic("alert", 14)}<span>${esc(fill(r.con_text))}</span></li>` : "";
    return `<article class="rule st-${r.status}${fresh.has(r.id) ? " is-new" : ""}" data-rule="${esc(r.id)}">
      <header class="rule-h"><div class="rule-t"><h4>${esc(title(r))}</h4><div class="rule-b"><span class="badge b-${sc}">${ic(si, 13)}${sl}</span>
        <span class="badge b-kind">${r.source === "learned" ? "Appresa dai pareri" : "Manuale"}</span>${fresh.has(r.id) ? '<span class="badge b-meta">Nuova</span>' : ""}</div></div>${effectBox(r)}</header>
      <dl class="rule-d"><div><dt>Quando</dt><dd>${esc(whenText(r.when))}</dd></div><div><dt>Ruoli</dt><dd class="rule-roles">${rolesText(r)}</dd></div></dl>
      ${pro || con ? `<ul class="rule-txt">${pro}${con}</ul>` : ""}
      ${evidence(r)}
      ${r.status === "proposed" ? `<div class="rule-act"><p>Non è ancora attiva: non cambia né testi né score finché non decidi.</p><span class="grow"></span>
        <button class="btn danger" data-decide="rejected" data-id="${esc(r.id)}">${ic("x", 16)}Rifiuta</button><button class="btn pri" data-decide="active" data-id="${esc(r.id)}">${ic("check", 16)}Approva</button></div>` : ""}
    </article>`;
  }
  function kpis() {
    const c = data.counts, act = data.rules.filter(r => r.status === "active");
    const up = Math.min(capTot(), act.reduce((a, r) => a + Math.max(0, r.effect?.score || 0), 0)), dn = Math.min(capTot(), act.reduce((a, r) => a + Math.max(0, -(r.effect?.score || 0)), 0));
    const fx = up || dn ? `${up ? signed(up, 1) : "0"} / ${dn ? signed(-dn, 1) : "0"}` : "0";
    return `<section class="kpis">
      <div class="kpi kpi-ok"><small>Attive</small><b class="num">${c.active ?? 0}</b><span>contano nel giudizio</span></div>
      <div class="kpi ${c.proposed ? "kpi-gold" : ""}"><small>Proposte</small><b class="num">${c.proposed ?? 0}</b><span>aspettano la tua scelta</span></div>
      <div class="kpi"><small>Rifiutate</small><b class="num">${c.rejected ?? 0}</b><span>non tornano più</span></div>
      <div class="kpi"><small>Effetto sullo score</small><b class="num">${fx}</b><span>massimo possibile · tetto ±${num(capTot())}</span></div></section>`;
  }
  function learnResult(r) {
    const sk = r.skipped || [];
    return `<div class="res learn-res" aria-live="polite"><div class="res-h ${r.ready && r.proposals.length ? "ok" : ""}">${ic(r.ready ? (r.proposals.length ? "check" : "info") : "alert", 18)}<b>${esc(r.message)}</b></div>
      ${r.ready ? `<div class="rpills"><span class="rpill"><b class="num">${r.n_rows}</b>criteri letti dai pareri</span><span class="rpill ${r.proposals.length ? "ok" : ""}"><b class="num">${r.proposals.length}</b>proposte nuove o aggiornate</span><span class="rpill"><b class="num">${sk.length}</b>temi senza abbastanza dati</span></div>` : `<p class="hint">Servono pareri accettati dalla sezione «Ricerca pareri»: da lì l'app capisce quali criteri citano i creator (scatto, animazioni, piede debole…).</p>`}
      ${sk.length ? `<details class="more"><summary>Perché non ho proposto altro</summary><ul class="rlist">${sk.map(s => `<li>${ic("minus", 15)}<span><b>${esc(critName(s.criterion))}</b> · ${esc(roleName(s.role))} — ${esc(s.motivo)}${s.mentions != null ? ` (${s.mentions} menzioni, ${s.creators} creator)` : ""}</span></li>`).join("")}</ul></details>` : ""}</div>`;
  }
  function draw() {
    if (!alive) return;
    const order = { proposed: 0, active: 1, rejected: 2 };
    const list = [...data.rules].sort((a, b) => order[a.status] - order[b.status]).filter(r => filter === "all" || r.status === filter);
    const cnt = s => data.counts[s] ?? 0, n = data.rules.length;
    root.innerHTML = `<div class="rules">
      <section class="panel cal-intro"><div class="cal-ic">${ic("rules", 28)}</div><div><h3>Come l'app giudica una carta</h3>
        <p>Ogni regola guarda una caratteristica (una statistica, un PlayStyle, il body type, le stelle…) per certi ruoli. Se la carta la soddisfa, la regola aggiunge una frase tra i «perché sì» o i «perché no» e può spostare lo score base di pochi punti: al massimo ±${num(cap())} per regola e ±${num(capTot())} in tutto, così nessuna regola da sola ribalta il giudizio.</p></div></section>
      ${kpis()}
      <section class="panel learn"><div class="learn-h"><div><h3>${ic("bulb", 18)}Impara dai pareri</h3>
        <p>L'app rilegge i criteri citati dai creator nei pareri accettati (es. «scatto», «animazioni») e propone nuove regole quando almeno ${data.settings.learn.min_creators} creator concordano su ${data.settings.learn.min_mentions} o più menzioni.</p></div>
        <button class="btn pri" id="bLearn">${ic("sparkle", 16)}<span>Impara dai pareri</span></button></div>
        <div class="note soft">${ic("shield", 18)}<div><b>Le regole apprese non sono mai attive da sole.</b> Nascono come «proposta» e non toccano né i testi né lo score finché non premi Approva: i pareri possono essere letti male o essere di parte, e così l'ultima parola resta tua. Una proposta rifiutata non viene riproposta.</div></div>
        <div id="learnOut">${learnOut}</div></section>
      <div class="sec-h"><h3>${ic("rules", 20)}Le regole</h3><span class="sec-s">${n} in tutto</span><span class="grow"></span>
        <div class="seg" role="radiogroup" aria-label="Filtra per stato">${[["all", "Tutte", n], ["active", "Attive", cnt("active")], ["proposed", "Proposte", cnt("proposed")], ["rejected", "Rifiutate", cnt("rejected")]].map(([k, l, c]) => `<button role="radio" aria-checked="${filter === k}" data-f="${k}">${l} <span class="seg-n">${c}</span></button>`).join("")}</div></div>
      ${list.length ? `<div class="rule-grid">${list.map(ruleCard).join("")}</div>` : `<div class="empty"><h3>Nessuna regola in questo stato</h3><p>${filter === "proposed" ? "Premi «Impara dai pareri» per far proporre nuove regole dai pareri dei creator." : "Cambia filtro per vedere le altre."}</p></div>`}
    </div>`;
    $("#bLearn", root).onclick = learn;
    $$("[data-f]", root).forEach(b => b.onclick = () => { filter = b.dataset.f; draw(); });
    $$("[data-decide]", root).forEach(b => b.onclick = () => decide(b));
  }
  async function load() {
    let nd; try { nd = await jget("/rules"); } catch (e) { if (!alive) return; root.innerHTML = `<div class="empty"><h3>Impossibile caricare le regole</h3><p>${esc(e.message)}</p><button class="btn pri" id="bRetry">Riprova</button></div>`; $("#bRetry", root).onclick = load; return; }
    if (!alive) return;
    data = nd; draw();
  }
  async function learn() {
    const b = $("#bLearn", root); b.disabled = true; b.querySelector("span").textContent = "Leggo i pareri…";
    try {
      const r = await send("/rules/learn", "POST");
      if (!r.ok) return toastErr(await errFriendly(r));
      const d = await r.json();
      fresh = new Set(d.proposals.map(p => p.id)); learnOut = learnResult(d);
      toast(d.proposals.length ? `${d.proposals.length} regole proposte: approvale o rifiutale` : d.message, d.proposals.length ? "ok" : "info");
      filter = d.proposals.length ? "proposed" : filter;
      await load();
    } finally { const x = $("#bLearn", root); if (x) { x.disabled = false; x.querySelector("span").textContent = "Impara dai pareri"; } }
  }
  async function decide(btn) {
    const id = btn.dataset.id, st = btn.dataset.decide;
    $$("[data-decide]", btn.closest(".rule")).forEach(x => x.disabled = true);
    const r = await send(`/rules/${encodeURIComponent(id)}/status`, "PUT", { status: st });
    if (!r.ok) { toastErr(await errFriendly(r)); return load(); }
    fresh.delete(id);
    toast(st === "active" ? "Regola approvata: da ora conta nel giudizio" : "Regola rifiutata");
    await load();
    if (st === "active") loadCards();
  }
  return { mount(el) { root = el; root.innerHTML = `<div class="skel" style="height:260px;margin-top:8px"></div>`; load(); }, unmount() { alive = false; } };
}
