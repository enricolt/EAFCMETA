// Dettaglio carta: drawer laterale (desktop) / bottom sheet (telefono).
import { $, $$, esc, fmt, ic, h, runCounters, reduced } from "./util.js";
import { jget, send, errFriendly } from "./api.js";
import { pct, signed, num, critName, srcLink } from "./labels.js";
import { state, toggleCompare, loadCards, on, MAX_COMPARE } from "./state.js";
import { toast, toastErr, confirmBox } from "./ui.js";
import { futCard, verdictBadge, metaChip, kind, KIND_NAME, vkey, VERD, priceTag } from "./card.js";
import { radar, statBar, areaChart, ring, bindCharts, SERIES, fmtTs } from "./charts.js";
import { openForm } from "./form.js";

const STANCE = { yes: "Sì, da prendere", maybe: "Dipende", no: "No, da evitare" };
const STANCE_SHORT = { yes: "Sì", maybe: "Dipende", no: "No" };
const initials = n => n.split(/[\s.\-()]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase();
const hue = n => { let x = 0; for (const ch of n) x = (x * 31 + ch.charCodeAt(0)) % 360; return x; };
const avatar = n => `<span class="av" style="--h:${hue(n)}" aria-hidden="true">${esc(initials(n))}</span>`;

let curId = null, stance = "yes";
const dlg = () => $("#dDetail");

export async function openDetail(id) {
  id = +id;
  let d;
  try { d = await jget("/cards/" + id); } catch (e) { return toastErr(e.message); }
  const first = !dlg().open, body = $("#dtBody"), y = body && curId === id ? body.scrollTop : 0;
  curId = id; stance = "yes";
  draw(d);
  if (first) dlg().showModal();
  const b = $("#dtBody"); if (b) b.scrollTop = y;
  $("#dtBody").focus({ preventScroll: true });
}

function draw(d) {
  const meta = state.meta, w = meta.role_weights[d.position] || {};
  const keys = Object.keys(w).sort((a, b) => w[b] - w[a]);
  const k = kind(d), a = d.analysis, hist = d.price_history, ids = state.navIds || [], i = ids.indexOf(d.id);
  const inCmp = state.compare.includes(d.id), v = VERD[vkey(d)];
  const prices = hist.map(x => x.price), last = prices.at(-1), first = prices[0];
  const delta = hist.length > 1 && first ? Math.round((last - first) / first * 1000) / 10 : null;
  const names = key => meta.stat_names[key] || key.replaceAll("_", " ");
  const sections = [["sec-an", "Analisi"], ["sec-st", "Statistiche"], ["sec-pr", "Prezzo"], ["sec-op", "Creator"]];

  dlg().innerHTML = `<div class="dr">
  <header class="dr-head">
    <div class="dr-pn"><button class="icon-btn" data-prev ${i <= 0 ? "disabled" : ""} aria-label="Carta precedente" title="Precedente (←)">${ic("left", 18)}</button><button class="icon-btn" data-next ${i < 0 || i >= ids.length - 1 ? "disabled" : ""} aria-label="Carta successiva" title="Successiva (→)">${ic("right", 18)}</button></div>
    <div class="dr-title"><small>${esc(d.version || KIND_NAME[k])} · ${esc(d.position)}</small><h2 id="dtTitle">${esc(d.name)}</h2></div>
    <button class="icon-btn" data-close aria-label="Chiudi" title="Chiudi (Esc)">${ic("close", 18)}</button>
  </header>
  <nav class="dr-tabs" aria-label="Sezioni della carta">${sections.map(([id, l], n) => `<button data-jump="${id}" aria-current="${n === 0}">${l}</button>`).join("")}</nav>
  <div class="dr-body" id="dtBody" tabindex="-1">
    <section class="dr-hero hb-${k}">
      <div class="dr-card tilt">${futCard(d)}</div>
      <div class="dr-meta">
        <div class="dr-badges">${verdictBadge(d)}${metaChip(d)}<span class="badge b-kind kk-${k}">${KIND_NAME[k]}</span></div>
        <div class="dr-price"><span class="num">${fmt(d.cost_credits)}</span>${ic("coin", 22)}${d.is_sbc ? '<em class="sbc">costo SBC</em>' : ""}</div>
        <p class="dr-reason">${esc(d.verdict_reason)}</p>
        <div class="dr-scores">
          ${ring(d.scores.final_score, 100, { size: 96, label: d.scores.final_score.toFixed(1).replace(".", ","), sub: "finale", color: "var(--acc)" })}
          <div class="mini"><small>Base</small><b class="num">${d.scores.base_score}</b></div>
          <div class="mini"><small>Pro</small><b class="num">${d.scores.pro_sentiment_score == null ? "–" : +(+d.scores.pro_sentiment_score).toFixed(1)}</b></div>
          <div class="mini"><small>Vs mercato</small><b class="num ${d.value_gap == null ? "" : d.value_gap >= 0 ? "pos" : "neg"}">${d.value_gap == null ? "–" : (d.value_gap > 0 ? "+" : "") + d.value_gap}</b></div>
        </div>
      </div>
    </section>

    <section class="dr-sec" id="sec-an"><h3 class="dr-h">${ic("bulb", 18)}Analisi</h3>
      <div class="ana"><div class="ana-top"><span class="badge b-lvl lv-${esc(a.meta_level)}">${esc(a.meta_label)}</span></div>
        <p class="ana-head">${esc(a.headline)}</p>
        <div class="two">
          <div class="pc pc-pro"><h4>${ic("check", 15)}Perché sì</h4><ul>${a.pros.map(x => `<li>${esc(x)}</li>`).join("") || "<li class='none'>Nessun punto di forza particolare.</li>"}</ul></div>
          <div class="pc pc-con"><h4>${ic("alert", 15)}Perché no / attenzione</h4><ul>${a.cons.map(x => `<li>${esc(x)}</li>`).join("") || "<li class='none'>Nessun difetto rilevante.</li>"}</ul></div>
        </div>
        <div class="advice">${ic("bolt", 20)}<div><small>Consiglio</small><p>${esc(a.advice)}</p></div></div>
      </div>
      ${rulesBlock(a)}
      ${a.site_signals.length ? `<h4 class="dr-sh">Segnali dei siti</h4><div class="signals">${a.site_signals.map(x => `<div class="signal">${ic("trend", 16)}<span>${esc(x)}</span></div>`).join("")}</div>` : ""}
    </section>

    <section class="dr-sec" id="sec-st"><h3 class="dr-h">${ic("target", 18)}Statistiche del ruolo ${esc(d.breakdown.role)}</h3>
      <div class="st-grid"><div>${radar([{ stats: d.card.stats, color: SERIES[0] }], keys)}</div>
      <div class="sbars">${keys.map(key => statBar(names(key), [d.card.stats[key] ?? null])).join("")}</div></div>
      <div class="formula"><span><b>${d.breakdown.stats_meta}</b>statistiche</span><i>+</i><span><b>${d.breakdown.bonus}</b>bonus</span><i>=</i><span><b>${d.scores.base_score}</b>base</span>${d.pro_missing ? "" : `<i>→</i><span><b>${d.scores.final_score}</b>${Math.round((1 - d.scores.pro_share) * 100)}% base + ${Math.round(d.scores.pro_share * 100)}% pareri</span>`}</div>
      <p class="hint">Bonus da PlayStyle, body type e 5★. Confronto con ${d.market_size} carte ${esc(d.position)}.</p>
      <div class="chips-s">${[["Piede debole", d.card.weak_foot + "★"], ["Skill moves", d.card.skill_moves + "★"], ["Body type", d.card.body_type]].map(([l, x]) => `<span class="info-chip"><small>${l}</small><b>${esc(x)}</b></span>`).join("")}${d.card.playstyles.map(p => `<span class="ps ${p.endsWith("+") ? "plus" : ""}">${esc(p)}</span>`).join("")}</div>
    </section>

    <section class="dr-sec" id="sec-pr"><h3 class="dr-h">${ic("trend", 18)}Storico del prezzo</h3>
      ${hist.length > 1 ? `<div class="pr-kpis"><div><small>Ultimo</small><b class="num">${fmt(last)}</b></div><div><small>Minimo</small><b class="num">${fmt(Math.min(...prices))}</b></div><div><small>Massimo</small><b class="num">${fmt(Math.max(...prices))}</b></div>
        <div><small>Variazione</small><b class="num ${delta >= 0 ? "pos" : "neg"}">${delta > 0 ? "+" : ""}${String(delta).replace(".", ",")}%</b></div></div>${areaChart(hist)}`
        : `<div class="note">${ic("info", 18)}<div><b>Ancora nessuno storico.</b> Si costruisce a ogni aggiornamento dei prezzi (Importa → Solo prezzi, o riimportando la carta). Prezzo registrato: ${fmt(last)} crediti${hist[0] ? " · " + esc(fmtTs(hist[0].ts)) : ""}.</div></div>`}
    </section>

    <section class="dr-sec" id="sec-op"><h3 class="dr-h">${ic("scale", 18)}Cosa dicono i creator</h3>
      <p class="osum">${esc(a.opinions.summary)}</p>
      <div class="ogs">${a.opinions.groups.map(g => `<div class="og og-${g.stance}"><div class="og-h"><b>${esc(g.label)}</b><span class="num">${g.creators.length}</span></div>${g.creators.map(c => `<div class="op">${avatar(c.name)}<div><b>${esc(c.name)}${c.score != null ? `<span class="op-s num">${c.score}</span>` : ""}${autoBadge(byCreator(d, c.name))}</b><p>${esc(c.reason) || "<i>Nessun motivo indicato.</i>"}</p>${srcLink(c.url, "Fonte")}</div></div>`).join("")}</div>`).join("")}</div>
      ${a.opinions.hint ? `<div class="note">${ic("info", 18)}<div>${esc(a.opinions.hint)}</div></div>` : ""}
      ${disagreeBlock(a)}
      ${weighBlock(d, a)}

      <h4 class="dr-sh">Pareri inseriti</h4>
      ${d.opinions.length ? `<div class="oplist">${d.opinions.map(o => `<div class="oprow">${avatar(o.creator)}<span class="op-n"><b>${esc(o.creator)}${autoBadge(o)}</b><small>${STANCE_SHORT[o.stance]}${o.score != null ? " · " + o.score : ""}${o.automatic && srcLink(o.url) ? " · " + srcLink(o.url, "fonte") : ""}</small></span><span class="op-st st-${o.stance}"></span><button class="icon-btn" data-edit-op="${o.id}" aria-label="Modifica il parere di ${esc(o.creator)}" title="Modifica">${ic("edit", 16)}</button><button class="icon-btn danger" data-del="${o.id}" data-name="${esc(o.creator)}" aria-label="Elimina il parere di ${esc(o.creator)}" title="Elimina">${ic("trash", 16)}</button></div>`).join("")}</div>`
        : `<p class="hint">Nessun parere inserito. Aggiungi cosa dicono i creator che segui.</p>`}
      <form class="opform" id="opForm" autocomplete="off">
        <div class="fg2">
          <div><label class="lbl" for="oC">Creator</label><input class="inp" type="text" id="oC" list="crList" maxlength="40" placeholder="es. Team Gullit" required><datalist id="crList">${meta.creators.map(x => `<option value="${esc(x)}">`).join("")}</datalist></div>
          <div><span class="lbl" id="oSl">Secondo lui/lei</span><div class="seg stance" role="radiogroup" aria-labelledby="oSl">${Object.entries(STANCE_SHORT).map(([s, l]) => `<button type="button" role="radio" data-stance="${s}" aria-checked="${s === "yes"}" class="sn-${s}">${l}</button>`).join("")}</div></div>
          <div><label class="lbl" for="oV">Voto 0–100 <span class="lbl-s">(sì ≥ 70 · no ≤ 60 · vuoto: sì 85, dipende 70, no 50)</span></label><input class="inp" type="number" id="oV" min="0" max="100" step="0.5" inputmode="decimal"></div>
          <div><label class="lbl" for="oU">Link al video <span class="lbl-s">(facoltativo)</span></label><input class="inp" type="text" id="oU" maxlength="300" placeholder="https://…"></div>
          <div class="span2"><label class="lbl" for="oR">Perché <span class="lbl-s">(a parole sue, in breve)</span></label><textarea class="inp" id="oR" maxlength="800" rows="2" placeholder="es. Scatto e finalizzazione top, ma lenta nei cambi di direzione"></textarea></div>
        </div>
        <p class="form-err" id="opErr" role="alert" hidden></p>
        <div class="row end"><button class="btn pri" id="bOp" type="submit">${ic("check", 16)}Salva parere</button></div>
      </form>
    </section>
    <div class="dr-spacer"></div>
  </div>
  <footer class="dr-foot">
    <button class="btn danger ghost" id="bDel">${ic("trash", 16)}<span>Elimina</span></button><span class="grow"></span>
    <button class="btn ${inCmp ? "on" : ""}" id="bCmp" aria-pressed="${inCmp}">${ic(inCmp ? "check" : "compare", 16)}<span>${inCmp ? "Nel confronto" : "Confronta"}</span></button>
    <button class="btn pri" id="bEdit">${ic("edit", 16)}<span>Modifica</span></button>
  </footer></div>`;
  dlg().setAttribute("aria-labelledby", "dtTitle");
  dlg().style.setProperty("--kc", `var(--k-${k})`);
  bindCharts(dlg());

  const body = $("#dtBody"), tabs = $$("[data-jump]", dlg());
  tabs.forEach(t => t.onclick = () => { const s = $("#" + t.dataset.jump); body.scrollTo({ top: s.offsetTop - 8, behavior: reduced() ? "auto" : "smooth" }); });
  body.addEventListener("scroll", () => {
    let cur = sections[0][0];
    sections.forEach(([id]) => { if ($("#" + id).offsetTop - 80 <= body.scrollTop) cur = id; });
    if (body.scrollTop + body.clientHeight >= body.scrollHeight - 4) cur = sections.at(-1)[0];
    tabs.forEach(t => t.setAttribute("aria-current", String(t.dataset.jump === cur)));
  }, { passive: true });

  $("[data-prev]", dlg()).onclick = () => step(-1);
  $("[data-next]", dlg()).onclick = () => step(1);
  $$("[data-stance]", dlg()).forEach(b => b.onclick = () => { stance = b.dataset.stance; $$("[data-stance]", dlg()).forEach(x => x.setAttribute("aria-checked", String(x === b))); });
  const opErr = m => { const e = $("#opErr"); e.textContent = m || ""; e.hidden = !m; };
  $$("[data-edit-op]", dlg()).forEach(b => b.onclick = () => {
    const o = d.opinions.find(x => x.id === +b.dataset.editOp); if (!o) return;
    $("#oC").value = o.creator; $("#oV").value = o.score ?? ""; $("#oR").value = o.reason || ""; $("#oU").value = o.url || ""; opErr("");
    stance = o.stance; $$("[data-stance]", dlg()).forEach(x => x.setAttribute("aria-checked", String(x.dataset.stance === stance)));
    $("#bOp").lastChild.textContent = "Aggiorna parere"; $("#opForm").scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "center" }); $("#oR").focus({ preventScroll: true });
  });
  $("#opForm").onsubmit = async e => {
    e.preventDefault(); opErr("");
    const val = $("#oV").value.trim(), btn = $("#bOp");
    if (!$("#oC").value.trim()) { $("#oC").focus(); return opErr("Scrivi il nome del creator."); }
    if (val !== "" && (stance === "yes" && +val < 70 || stance === "no" && +val > 60)) { $("#oV").focus(); return opErr("Il voto non è coerente con la scelta: per «Sì» serve almeno 70, per «No» al massimo 60."); }
    btn.disabled = true;
    const r = await send(`/cards/${d.id}/opinions`, "PUT", { creator: $("#oC").value, stance, score: val === "" ? null : +val, reason: $("#oR").value, url: $("#oU").value });
    btn.disabled = false;
    if (!r.ok) { const m = await errFriendly(r); opErr(m[0].toUpperCase() + m.slice(1)); return toastErr(m); }
    toast("Parere salvato"); await openDetail(d.id); loadCards();
  };
  $$("[data-del]", dlg()).forEach(b => b.onclick = async () => {
    if (!await confirmBox({ title: "Eliminare questo parere?", text: `Il parere di ${b.dataset.name} verrà rimosso e lo score verrà ricalcolato.`, ok: "Elimina", danger: true })) return;
    const r = await send(`/cards/${d.id}/opinions/${b.dataset.del}`, "DELETE");
    if (!r.ok) return toastErr(await errFriendly(r));
    toast("Parere eliminato"); await openDetail(d.id); loadCards();
  });
  $("#bDel").onclick = async () => {
    if (!await confirmBox({ title: `Eliminare ${d.name}?`, text: "La carta, il suo storico prezzi e i pareri verranno eliminati definitivamente.", ok: "Elimina carta", danger: true })) return;
    const r = await send("/cards/" + d.id, "DELETE");
    if (r.ok) { toast("Carta eliminata"); dlg().close(); loadCards(); } else toastErr(await errFriendly(r));
  };
  $("#bEdit").onclick = () => { dlg().close(); openForm(d); };
  $("#bCmp").onclick = () => {
    if (!toggleCompare(d.id)) return toast(`Puoi confrontare al massimo ${MAX_COMPARE} carte`, "info");
    const y = $("#dtBody").scrollTop; draw(d); $("#dtBody").scrollTop = y;
  };
}

const byCreator = (d, name) => d.opinions.find(o => o.creator.toLowerCase() === String(name).toLowerCase());
const autoBadge = o => o?.automatic ? `<span class="badge b-auto" title="Raccolto in automatico dai video: puoi correggerlo o eliminarlo">${ic("sync", 12)}Automatico${o.confidence != null ? ` · ${pct(o.confidence)}` : ""}</span>` : "";

// "Dove non sono d'accordo": discordanze tra creator a livello di criterio (es. scatto) per il ruolo della carta
function disagreeBlock(a) {
  const x = a.criteria_disagreements || [];
  if (!x.length) return "";
  return `<h4 class="dr-sh">Dove non sono d'accordo</h4><div class="signals">${x.map(c => `<div class="signal dis">${ic("scale", 16)}<span><b>${esc(critName(c.criterion))}.</b> ${esc(c.text)}</span></div>`).join("")}</div>`;
}

// "Come pesano": quanto i pareri contano sullo score finale e quanto pesa ciascun creator
function weighBlock(d, a) {
  const pb = d.pro_breakdown, share = d.scores.pro_share || 0;
  if (!pb || !pb.items?.length) return "";
  const base = Math.round((1 - share) * 100), pro = Math.round(share * 100);
  const items = [...pb.items].sort((x, y) => (y.influence || 0) - (x.influence || 0)), top = Math.max(...items.map(i => i.influence || 0), 0.0001);
  return `<h4 class="dr-sh">Come pesano i pareri</h4><div class="weigh">
    <p class="weigh-t"><b>I pareri pesano il ${pro}% dello score</b> e le statistiche il ${base}%.</p>
    <div class="stack weigh-bar" role="img" aria-label="${base}% statistiche, ${pro}% pareri"><i style="flex:${base};background:var(--s1)"></i><i style="flex:${Math.max(pro, 1)};background:var(--s3)"></i></div>
    <ul class="legend inline"><li><i style="background:var(--s1)"></i>Statistiche e regole<b class="num">${base}%</b></li><li><i style="background:var(--s3)"></i>Pareri dei creator<b class="num">${pro}%</b></li></ul>
    <p class="hint">${esc(a.opinions.influence || "Più creator e più recenti = più peso.")} Media pesata dei pareri: <b class="num">${num(pb.pro, 1)}</b>/100.</p>
    <ul class="wl">${items.map(i => `<li><span class="wl-n">${esc(i.creator)}${autoBadge(byCreator(d, i.creator))}</span><span class="wl-v num" title="Valore del parere">${num(i.value, 0)}</span><span class="wl-b" title="Quanto di questo parere finisce nello score finale"><u style="--w:${Math.round((i.influence || 0) / top * 100)}%"></u></span><b class="num">${num((i.influence || 0) * 100, 1)}%</b></li>`).join("")}</ul>
    <p class="hint">Ogni riga: voto del creator, peso nello score finale (peso del creator × freschezza del parere).</p></div>`;
}

// Regole scattate su questa carta: cosa ha contato davvero, con l'effetto sullo score e le regole proposte non ancora attive
function rulesBlock(a) {
  const c = a.contributions || [], pend = a.rules_pending || [], dl = a.rules_score_delta || 0;
  if (!c.length && !pend.length) return "";
  return `<details class="more rules-d"><summary>Regole scattate su questa carta (${c.length})${dl ? ` · effetto sullo score ${signed(dl, 2)}` : ""}</summary>
    <ul class="rlist">${c.map(x => `<li class="${x.kind}">${ic(x.kind === "pro" ? "check" : x.kind === "con" ? "alert" : "info", 15)}<span>${esc(x.text || x.id)}${x.source === "learned" ? ' <span class="badge b-kind">appresa</span>' : ""}</span><em class="num">${x.delta ? signed(x.delta, 2) : "testo"}</em></li>`).join("")}</ul>
    ${a.rules_capped ? `<p class="hint">L'effetto grezzo (${signed(a.rules_score_delta_raw, 2)}) è stato limitato dal tetto totale.</p>` : ""}
    ${pend.length ? `<p class="hint"><b>${pend.length} ${pend.length === 1 ? "regola proposta scatterebbe" : "regole proposte scatterebbero"}</b> ma ${pend.length === 1 ? "non è approvata" : "non sono approvate"}: ${pend.length === 1 ? "non conta" : "non contano"} ancora (sezione Regole).</p>` : ""}</details>`;
}

function step(dir) {
  const ids = state.navIds || [], i = ids.indexOf(curId), n = ids[i + dir];
  if (n != null) openDetail(n);
}

export function initDetail() {
  dlg().addEventListener("keydown", e => {
    if (e.target.closest("input,textarea,select")) return;
    if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1);
  });
  dlg().addEventListener("close", () => { curId = null; });
}
