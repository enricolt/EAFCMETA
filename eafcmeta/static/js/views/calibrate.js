// Vista "Calibra": confronta lo score con i pareri dei creator e propone soglie e pesi.
import { $, esc, ic } from "../util.js";
import { jget, send, errFriendly } from "../api.js";
import { state, loadCards, loadMeta } from "../state.js";
import { toast, toastErr, confirmBox } from "../ui.js";
import { ring } from "../charts.js";
import { ruleName } from "./rules.js";
import { signed, num } from "../labels.js";

export default function view() {
  let root;
  const pct = x => Math.round(x * 100) + "%";
  const nm = k => state.meta.stat_names[k] || k;

  function rulesPanel(rr) {
    const ch = rr?.changes || [];
    return `<section class="panel"><h4 class="dr-sh first">Regole: ritocchi suggeriti <span class="lbl-s">(solo regole attive, a passi piccoli)</span></h4>` + (ch.length
      ? `<ul class="rlist cal-rules">${ch.map(x => `<li>${ic("rules", 15)}<span><b>${esc(ruleName(x.id))}</b><small>${x.n_hit} carte con la caratteristica, ${x.n_other} senza · differenza media nei voti dei pro ${signed(x.diff, 1)}</small></span>
          <span class="cr-ch">${x.delta.new !== x.delta.old ? `<em>effetto <s class="num">${signed(x.delta.old, 2)}</s> → <b class="num">${signed(x.delta.new, 2)}</b></em>` : ""}${x.threshold ? `<em>soglia ${esc(x.threshold.field)} <s class="num">${num(x.threshold.old, 1)}</s> → <b class="num">${num(x.threshold.new, 1)}</b></em>` : ""}</span></li>`).join("")}</ul>`
      : `<p class="note">Nessun ritocco alle regole per ora: ne servono almeno ${rr?.min_cards_rule ?? 5} carte con e ${rr?.min_cards_rule ?? 5} senza la caratteristica, con i pareri dei creator, e una differenza netta nei voti.</p>`) + `</section>`;
  }
  async function draw() {
    root.innerHTML = `<div class="skel" style="height:260px"></div>`;
    let r;
    try { r = await jget("/calibration"); } catch (e) { root.innerHTML = `<div class="empty"><h3>Impossibile caricare la calibrazione</h3><p>${esc(e.message)}</p></div>`; return; }
    let h = `<div class="cal"><section class="panel cal-intro"><div class="cal-ic">${ic("target", 28)}</div><div><h3>Calibrazione dal meta dei pro</h3>
      <p>L'app confronta il suo punteggio con i pareri dei creator che hai inserito (il voto automatico della community dei siti non conta) e propone soglie, pesi e piccoli ritocchi alle regole più vicini al loro modo di vedere il meta. Niente cambia da solo: applichi tu, e puoi sempre tornare ai valori originali.</p></div></section>`;
    if (!r.ready) {
      h += `<section class="panel cal-wait"><div class="cal-ring">${ring(r.n, r.needed, { size: 132, label: `${r.n}/${r.needed}`, sub: "carte con parere", color: "var(--acc)" })}</div>
        <div><h3>Servono più pareri</h3><p>Carte con il parere di un creator: <b class="num">${r.n} / ${r.needed}</b></p>
        <div class="prog"><u style="width:${Math.min(100, r.n / r.needed * 100)}%"></u></div>
        <div class="note warn">${ic("info", 18)}<div>${esc(r.message)}</div></div></div></section>`;
    } else {
      const c = r.current, sg = r.suggested, t = sg.thresholds || null, after = t && sg.after;
      h += `<section class="cal-kpis"><div class="kpi"><small>Accordo ora</small><b class="num">${pct(c.accuracy)}</b><span>soglia meta ${r.thresholds.meta}</span></div>
        <div class="kpi ${after ? "kpi-ok" : ""}"><small>Accordo suggerito</small><b class="num">${after ? pct(after.accuracy) : "—"}</b><span>${t ? `con soglia ${t.meta}` : "nessuna soglia migliore"}</span></div>
        <div class="kpi"><small>Correlazione</small><b class="num">${r.correlation == null ? "—" : r.correlation.toFixed(2)}</b><span>col giudizio dei pro</span></div></section>
        <section class="panel"><p class="note">Su ${r.n} carte, i creator approvano (voto medio ≥ ${r.pro_meta_at}) in modo coerente col nostro punteggio nel ${pct(c.accuracy)} dei casi. Falsi “meta”: ${c.fp} · carte che i pro approvano e noi no: ${c.fn}.</p>
        <h4 class="dr-sh">Soglie suggerite</h4>${t ? `<div class="thr">${[["Giocabile", "playable"], ["Meta", "meta"], ["Di vertice", "top"]].map(([l, k]) => `<div class="thr-i"><small>${l}</small><span><s class="num">${r.thresholds[k]}</s>${ic("right", 14)}<b class="num">${t[k]}</b></span></div>`).join("")}</div>`
          : `<div class="note warn">${ic("info", 18)}<div>${esc(sg.thresholds_message || "Per ora non propongo modifiche alle soglie.")}</div></div>`}</section>`;
      const roles = Object.entries(sg.weights);
      h += `<section class="panel"><h4 class="dr-sh first">Pesi suggeriti <span class="lbl-s">(cambi piccoli e prudenti)</span></h4>` + (roles.length ? `<div class="wgrid">${roles.map(([ro, i]) => `<div class="wcard"><div class="wcard-h"><b>${esc(ro)}</b><small>su ${i.n} carte</small></div><table class="wtab"><thead><tr><th>Statistica</th><th>Peso</th><th>Correl.</th></tr></thead><tbody>${i.changes.slice(0, 5).map(x => `<tr><td>${esc(nm(x.stat))}</td><td class="num">${x.old} → <b>${x.new}</b></td><td class="num">${x.corr}</td></tr>`).join("")}</tbody></table></div>`).join("")}</div>`
        : `<p class="note">Per i pesi servono almeno 8 carte dello stesso ruolo con pareri.</p>`) +
        `</section>` + rulesPanel(r.rules) +
        `<section class="panel"><div class="cal-apply"><label class="check ${t ? "" : "is-off"}"><input type="checkbox" id="cT" ${t ? "checked" : "disabled"}><span class="check-b">${ic("check", 14)}</span>Applica le soglie</label><label class="check"><input type="checkbox" id="cW"><span class="check-b">${ic("check", 14)}</span>Applica i pesi</label><label class="check ${(r.rules?.changes || []).length ? "" : "is-off"}"><input type="checkbox" id="cR" ${(r.rules?.changes || []).length ? "" : "disabled"}><span class="check-b">${ic("check", 14)}</span>Applica le regole</label><span class="grow"></span><button class="btn pri" id="cApply">${ic("check", 16)}Applica</button></div></section>`;
    }
    if (r.active) h += `<section class="panel cal-active"><span class="badge b-meta">${ic("shield", 13)}Calibrazione attiva</span><span class="grow"></span><button class="btn danger" id="cReset">${ic("trash", 16)}Ripristina valori originali</button></section>`;
    root.innerHTML = h + `</div>`;
    const done = async r2 => { if (!r2.ok) return toastErr(await errFriendly(r2)); toast("Calibrazione aggiornata"); await Promise.all([loadCards(), loadMeta()]); draw(); };
    $("#cApply", root)?.addEventListener("click", async () => { const body = { thresholds: $("#cT", root).checked, weights: $("#cW", root).checked, rules: $("#cR", root).checked }; if (!body.thresholds && !body.weights && !body.rules) return toast("Spunta cosa vuoi applicare", "info"); done(await send("/calibration/apply", "POST", body)); });
    $("#cReset", root)?.addEventListener("click", async () => { if (await confirmBox({ title: "Tornare ai valori originali?", text: "Soglie, pesi e ritocchi alle regole calibrati verranno rimossi.", ok: "Ripristina", danger: true })) done(await send("/calibration/reset", "POST")); });
  }
  return { mount(el) { root = el; draw(); }, unmount() { } };
}
