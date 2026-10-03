// Vista "Calibra": confronta lo score con i pareri dei creator e propone soglie e pesi.
import { $, esc, ic } from "../util.js";
import { jget, send, errText } from "../api.js";
import { state, loadCards, loadMeta } from "../state.js";
import { toast, toastErr, confirmBox } from "../ui.js";
import { ring } from "../charts.js";

export default function view() {
  let root;
  const pct = x => Math.round(x * 100) + "%";
  const nm = k => state.meta.stat_names[k] || k;

  async function draw() {
    root.innerHTML = `<div class="skel" style="height:260px"></div>`;
    let r;
    try { r = await jget("/calibration"); } catch (e) { root.innerHTML = `<div class="empty"><h3>Impossibile caricare la calibrazione</h3><p>${esc(e.message)}</p></div>`; return; }
    let h = `<div class="cal"><section class="panel cal-intro"><div class="cal-ic">${ic("target", 28)}</div><div><h3>Calibrazione dal meta dei pro</h3>
      <p>L'app confronta il suo punteggio con i pareri dei creator che hai inserito (il voto automatico della community dei siti non conta) e propone soglie e pesi più vicini al loro modo di vedere il meta. Niente cambia da solo: applichi tu, e puoi sempre tornare ai valori originali.</p></div></section>`;
    if (!r.ready) {
      h += `<section class="panel cal-wait"><div class="cal-ring">${ring(r.n, r.needed, { size: 132, label: `${r.n}/${r.needed}`, sub: "carte con parere", color: "var(--acc)" })}</div>
        <div><h3>Servono più pareri</h3><p>Carte con il parere di un creator: <b class="num">${r.n} / ${r.needed}</b></p>
        <div class="prog"><u style="width:${Math.min(100, r.n / r.needed * 100)}%"></u></div>
        <div class="note warn">${ic("info", 18)}<div>${esc(r.message)}</div></div></div></section>`;
    } else {
      const c = r.current, sg = r.suggested, t = sg.thresholds;
      h += `<section class="cal-kpis"><div class="kpi"><small>Accordo ora</small><b class="num">${pct(c.accuracy)}</b><span>soglia meta ${r.thresholds.meta}</span></div>
        <div class="kpi kpi-ok"><small>Accordo suggerito</small><b class="num">${pct(sg.after.accuracy)}</b><span>con soglia ${t.meta}</span></div>
        <div class="kpi"><small>Correlazione</small><b class="num">${r.correlation == null ? "—" : r.correlation.toFixed(2)}</b><span>col giudizio dei pro</span></div></section>
        <section class="panel"><p class="note">Su ${r.n} carte, i creator approvano (voto medio ≥ ${r.pro_meta_at}) in modo coerente col nostro punteggio nel ${pct(c.accuracy)} dei casi. Falsi “meta”: ${c.fp} · carte che i pro approvano e noi no: ${c.fn}.</p>
        <h4 class="dr-sh">Soglie suggerite</h4><div class="thr">${[["Giocabile", "playable"], ["Meta", "meta"], ["Di vertice", "top"]].map(([l, k]) => `<div class="thr-i"><small>${l}</small><span><s class="num">${r.thresholds[k]}</s>${ic("right", 14)}<b class="num">${t[k]}</b></span></div>`).join("")}</div></section>`;
      const roles = Object.entries(sg.weights);
      h += `<section class="panel"><h4 class="dr-sh first">Pesi suggeriti <span class="lbl-s">(cambi piccoli e prudenti)</span></h4>` + (roles.length ? `<div class="wgrid">${roles.map(([ro, i]) => `<div class="wcard"><div class="wcard-h"><b>${esc(ro)}</b><small>su ${i.n} carte</small></div><table class="wtab"><thead><tr><th>Statistica</th><th>Peso</th><th>Correl.</th></tr></thead><tbody>${i.changes.slice(0, 5).map(x => `<tr><td>${esc(nm(x.stat))}</td><td class="num">${x.old} → <b>${x.new}</b></td><td class="num">${x.corr}</td></tr>`).join("")}</tbody></table></div>`).join("")}</div>`
        : `<p class="note">Per i pesi servono almeno 8 carte dello stesso ruolo con pareri.</p>`) +
        `<div class="cal-apply"><label class="check"><input type="checkbox" id="cT" checked><span class="check-b">${ic("check", 14)}</span>Applica le soglie</label><label class="check"><input type="checkbox" id="cW"><span class="check-b">${ic("check", 14)}</span>Applica i pesi</label><span class="grow"></span><button class="btn pri" id="cApply">${ic("check", 16)}Applica</button></div></section>`;
    }
    if (r.active) h += `<section class="panel cal-active"><span class="badge b-meta">${ic("shield", 13)}Calibrazione attiva</span><span class="grow"></span><button class="btn danger" id="cReset">${ic("trash", 16)}Ripristina valori originali</button></section>`;
    root.innerHTML = h + `</div>`;
    const done = async r2 => { if (!r2.ok) return toastErr(await errText(r2)); toast("Calibrazione aggiornata"); await Promise.all([loadCards(), loadMeta()]); draw(); };
    $("#cApply", root)?.addEventListener("click", async () => done(await send("/calibration/apply", "POST", { thresholds: $("#cT", root).checked, weights: $("#cW", root).checked })));
    $("#cReset", root)?.addEventListener("click", async () => { if (await confirmBox({ title: "Tornare ai valori originali?", text: "Soglie e pesi calibrati verranno rimossi.", ok: "Ripristina", danger: true })) done(await send("/calibration/reset", "POST")); });
  }
  return { mount(el) { root = el; draw(); }, unmount() { } };
}
