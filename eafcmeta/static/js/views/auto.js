// Vista "Automatico": raccolta automatica dei pareri (stato, esecuzione, soglie sicure, pareri raccolti, annullamento).
import { $, $$, esc, ic, fmt } from "../util.js";
import { jget, send, errFriendly } from "../api.js";
import { loadCards } from "../state.js";
import { toast, toastErr, confirmBox } from "../ui.js";
import { stanceBadge, pct, when, day, srcLink, num } from "../labels.js";

const COUNTERS = [["video_elaborati", "Video letti"], ["pareri_salvati", "Pareri salvati"], ["pareri_aggiornati", "Pareri aggiornati"], ["pareri_scartati", "Scartati perché incerti"],
  ["pareri_protetti", "Protetti (manuali)"], ["canali_scoperti", "Canali scoperti"], ["carte_nuove", "Carte nuove"], ["prezzi_aggiornati", "Prezzi aggiornati"]];
const RUN = { ok: ["Riuscita", "ok"], parziale: ["Parziale", "warn"], errore: ["Con errori", "bad"], in_corso: ["In corso", "nd"], interrotto: ["Interrotta", "nd"], saltata: ["Saltata", "nd"] };
const FIELDS = [
  ["interval_hours", "Ogni quante ore", "ore", 1, 336, 1, "Distanza tra due raccolte."],
  ["min_confidence", "Confidenza minima", "", 0.5, 0.95, 0.05, "Sotto questa soglia un parere non viene salvato."],
  ["max_opinions_per_run", "Pareri per esecuzione", "al massimo", 0, 200, 1, "Tetto di pareri nuovi a ogni giro."],
  ["max_videos_per_run", "Video per esecuzione", "al massimo", 1, 100, 1, "Tetto di video letti a ogni giro."],
  ["lookback_days", "Guarda indietro di", "giorni", 1, 60, 1, "Solo i video più recenti di così."],
  ["request_delay_seconds", "Pausa tra le richieste", "secondi", 2, 120, 1, "Più è alta, più si va piano con YouTube."],
  ["min_subscribers", "Iscritti minimi del canale", "iscritti", 0, 10000000, 1000, "Ignora i canali più piccoli."],
];
const MAIN_FIELDS = 4;

export default function view() {
  let root, st = null, ops = [], timer = 0, waiting = false, prevRun = null, t0 = 0, alive = true;

  const running = () => waiting || !!st?.running;

  function runSummary(lr) {
    if (!lr) return `<p class="hint">Nessuna raccolta eseguita finora. Premi «Esegui ora» per provare (serve la rete).</p>`;
    const [lbl, cls] = RUN[lr.status] || [lr.status, "nd"], s = lr.summary || {};
    const chips = COUNTERS.filter(([k]) => s[k] != null).map(([k, l]) => `<span class="rpill ${s[k] && ["pareri_salvati", "pareri_aggiornati"].includes(k) ? "ok" : ""}"><b class="num">${s[k]}</b>${l}</span>`).join("");
    const errs = s.errori || [], stop = Object.entries(s.fermato || {});
    return `<div class="au-run"><div class="row"><span class="badge b-${cls}">${lbl}</span><span class="mut">iniziata ${esc(when(lr.started))}${lr.finished ? ` · finita ${esc(when(lr.finished))}` : ""}</span></div>
      ${s.motivo ? `<p class="hint">${esc(s.motivo)}</p>` : ""}${chips ? `<div class="rpills">${chips}</div>` : ""}
      ${stop.length ? `<div class="note warn">${ic("alert", 18)}<div><b>Fermata prima della fine</b><br>${stop.map(([k, v]) => `${esc(k.replaceAll("_", " "))}: ${esc(v)}`).join("<br>")}</div></div>` : ""}
      ${errs.length ? `<details class="more"><summary>${s.errori_totali || errs.length} ${(s.errori_totali || errs.length) === 1 ? "problema" : "problemi"} durante la raccolta</summary><ul class="rlist">${errs.map(e => `<li class="err">${ic("alert", 15)}<span>${esc(e)}</span></li>`).join("")}</ul></details>` : ""}</div>`;
  }

  function top() {
    const c = st.config, on = c.enabled, r = running();
    const state = !on ? ["Spenta", "nd", "Non parte da sola. Puoi comunque lanciarla a mano."] : !st.env_enabled ? ["Accesa ma bloccata", "warn", "L'app è stata avviata con la raccolta automatica disattivata (EAFCMETA_AUTO=0): da sola non parte."] : ["Attiva", "ok", st.next_run ? `Prossima raccolta: ${when(st.next_run)}` : (st.next_run_note ? `Prossima raccolta: ${st.next_run_note}` : "")];
    return `<section class="panel au-top"><div class="au-sw"><label class="switch"><input type="checkbox" id="swOn" role="switch" ${on ? "checked" : ""} ${r ? "disabled" : ""} aria-describedby="swH"><span class="switch-t"><i></i></span>
        <span class="switch-l"><b>Raccolta automatica</b><small id="swH">${esc(state[2])}</small></span></label><span class="badge b-${state[1]}">${state[0]}</span></div>
      <div class="au-go">${r ? `<div class="au-prog" role="status" aria-live="polite"><span class="pulse"></span><div><b>Raccolta in corso…</b><small>Può durare qualche minuto. Puoi uscire da questa pagina: va avanti da sola.</small></div><i class="ind"><u></u></i></div>`
        : `<button class="btn pri" id="bRun">${ic("play", 16)}Esegui ora</button>`}</div></section>`;
  }
  function kpis() {
    const k = st.counters;
    return `<section class="kpis k5"><div class="kpi kpi-ok"><small>Pareri automatici</small><b class="num">${k.pareri_automatici}</b><span>tolti con un clic</span></div>
      <div class="kpi"><small>Pareri tuoi</small><b class="num">${k.pareri_manuali}</b><span>mai toccati dall'automatico</span></div>
      <div class="kpi"><small>Canali dei pro</small><b class="num">${k.canali}</b><span>YouTube scoperti</span></div>
      <div class="kpi"><small>Video letti</small><b class="num">${k.video_elaborati}</b><span>in tutto</span></div>
      <div class="kpi"><small>Scartati</small><b class="num">${k.scarti_automatici}</b><span>pareri incerti, non salvati</span></div></section>`;
  }
  function cautions() {
    return `<section class="panel au-warn"><h3>${ic("shield", 18)}Cautele</h3><ul class="chk">
      <li>${ic("alert", 16)}<span><b>Zona grigia dei termini di YouTube.</b> I sottotitoli non ufficiali si leggono senza l'API di YouTube, e i suoi termini non lo permettono in modo esplicito. L'app va piano (pause tra le richieste, tetti per giro) e legge solo video pubblici; decidi tu se accenderla.</span></li>
      <li>${ic("check", 16)}<span><b>I pareri incerti non vengono salvati.</b> Carta ambigua, confidenza sotto la soglia o nessun motivo chiaro: la proposta è scartata e finisce solo nel conteggio degli scarti.</span></li>
      <li>${ic("check", 16)}<span><b>I tuoi pareri non si toccano.</b> Un parere inserito a mano non viene mai sovrascritto da uno automatico.</span></li>
      <li>${ic("check", 16)}<span><b>Si spegne tutto.</b> L'interruttore ferma la raccolta, e «Annulla tutti gli automatici» (più sotto) toglie ciò che ha raccolto.</span></li></ul>
      ${st.yt_dlp_installed ? "" : `<div class="note warn">${ic("info", 18)}<div><b>yt-dlp non è installato:</b> senza non si possono leggere i sottotitoli dei video, quindi la raccolta dei pareri da YouTube non funziona.</div></div>`}</section>`;
  }
  function settings() {
    const c = st.config;
    const f = ([k, l, u, mn, mx, step, hint]) => `<div class="cf"><label class="lbl" for="cf_${k}">${l}${u ? ` <span class="lbl-s">(${u})</span>` : ""}</label>
      ${k === "min_confidence" ? `<div class="cf-r"><input type="range" id="cf_${k}" data-k="${k}" min="${mn}" max="${mx}" step="${step}" value="${c[k]}"><output class="num" for="cf_${k}">${pct(c[k])}</output></div>`
      : `<input class="inp" type="number" id="cf_${k}" data-k="${k}" min="${mn}" max="${mx}" step="${step}" value="${c[k]}" inputmode="decimal">`}<small>${hint}</small></div>`;
    return `<section class="panel"><div class="panel-h"><h3>Soglie sicure</h3><span class="panel-s">le altre si cambiano a mano nel file di configurazione</span></div>
      <form id="cfg" novalidate><div class="cf-g">${FIELDS.slice(0, MAIN_FIELDS).map(f).join("")}</div>
        <details class="more"><summary>Altre soglie</summary><div class="cf-g">${FIELDS.slice(MAIN_FIELDS).map(f).join("")}</div></details>
        <p class="form-err" id="cfgErr" role="alert" hidden></p><div class="row end"><button class="btn pri" id="cfgSave" type="submit" disabled>${ic("check", 16)}Salva soglie</button></div></form></section>`;
  }
  function channels() {
    const ch = st.channels;
    return `<section class="panel"><div class="panel-h"><h3>Canali dei pro</h3><span class="panel-s">${ch.length} ${ch.length === 1 ? "canale" : "canali"}</span></div>
      ${ch.length ? `<ul class="chl">${ch.map(x => `<li><span class="av" style="--h:${[...x.name].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 0)}" aria-hidden="true">${esc(x.name.split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase())}</span><div><b>${esc(x.name)}</b><small>${esc(x.title || x.handle || x.channel_id)}${x.subscribers != null ? ` · ${fmt(x.subscribers)} iscritti` : ""}${x.discovered_at ? ` · trovato ${esc(day(x.discovered_at))}` : ""}</small></div>${x.verified_auto ? `<span class="badge b-ok">${ic("shield", 12)}Verificato</span>` : `<span class="badge b-nd">Da verificare</span>`}</li>`).join("")}</ul>`
        : `<p class="hint">Nessun canale scoperto. Alla prima raccolta l'app cerca su YouTube i canali dei creator che segui (serve la rete).</p>`}</section>`;
  }
  function opinions() {
    return `<section class="panel"><div class="panel-h"><h3>Pareri raccolti in automatico</h3><span class="panel-s">${ops.length} ${ops.length === 1 ? "parere" : "pareri"}</span><span class="grow"></span>
        <button class="btn danger" id="bUndo" ${ops.length ? "" : "disabled"}>${ic("trash", 16)}Annulla tutti gli automatici</button></div>
      ${ops.length ? `<ul class="aops">${ops.map(o => `<li><div class="aop-h"><button class="link-btn aop-c" data-open="${o.card_id}">${esc(o.card_name)}</button><small>${esc(o.card_version || "")} · ${esc(o.card_position)}</small></div>
        <div class="aop-m"><span class="pr-cr">${ic("star", 13)}${esc(o.creator)}</span>${stanceBadge(o.stance)}${o.score != null ? `<span class="op-s num">${o.score}</span>` : ""}
          <span class="conf ${o.confidence >= 0.75 ? "ok" : o.confidence >= 0.6 ? "warn" : "bad"}" title="Confidenza della lettura"><span>Confidenza</span><i><u style="--w:${Math.round((o.confidence || 0) * 100)}%"></u></i><b class="num">${pct(o.confidence || 0)}</b></span>${srcLink(o.url, "Video")}</div>
        <p class="aop-r">${esc(o.reason)}</p></li>`).join("")}</ul>`
        : `<div class="empty slim"><h3>Ancora nessun parere automatico</h3><p>Quando la raccolta salva un parere sicuro, lo trovi qui con la sua fonte. I tuoi pareri non compaiono in questo elenco.</p></div>`}</section>`;
  }

  function draw() {
    if (!alive) return;
    root.innerHTML = `<div class="au">${top()}${kpis()}${cautions()}<div class="au-2"><section class="panel"><div class="panel-h"><h3>Ultima raccolta</h3>${st.next_run ? `<span class="panel-s">prossima: ${esc(when(st.next_run))}</span>` : ""}</div>${runSummary(st.last_run)}<p class="hint">Ogni ${num(st.interval_hours, 1)} ore${st.scheduler_running ? " · controllo attivo" : ""}.</p></section>${settings()}</div>${channels()}${opinions()}</div>`;
    bindTop();
    $("#bUndo", root)?.addEventListener("click", undo);
    const f = $("#cfg", root);
    f.addEventListener("input", e => { const i = e.target; if (i.type === "range") i.nextElementSibling.textContent = pct(+i.value); $("#cfgSave", root).disabled = !changed().length; });
    f.addEventListener("submit", saveCfg);
  }

  function bindTop() {
    $("#swOn", root)?.addEventListener("change", toggle);
    $("#bRun", root)?.addEventListener("click", runNow);
  }
  // durante la raccolta si ridisegna solo l'intestazione: le soglie in modifica restano come sono
  function paintTop() {
    const old = $(".au-top", root); if (!old) return draw();
    old.outerHTML = top(); bindTop();
    const lr = $(".au-2 .panel", root); if (lr) lr.innerHTML = `<div class="panel-h"><h3>Ultima raccolta</h3>${st.next_run ? `<span class="panel-s">prossima: ${esc(when(st.next_run))}</span>` : ""}</div>${runSummary(st.last_run)}<p class="hint">Ogni ${num(st.interval_hours, 1)} ore${st.scheduler_running ? " · controllo attivo" : ""}.</p>`;
  }
  const changed = () => $$("#cfg [data-k]", root).filter(i => i.value !== "" && +i.value !== +st.config[i.dataset.k]);

  async function saveCfg(e) {
    e.preventDefault();
    const err = $("#cfgErr", root); err.hidden = true;
    const body = {};
    for (const i of changed()) {
      const v = +i.value, [, l, , mn, mx] = FIELDS.find(x => x[0] === i.dataset.k);
      if (!Number.isFinite(v) || v < mn || v > mx) { err.textContent = `«${l}»: serve un valore tra ${num(mn, 2)} e ${num(mx, 2)}.`; err.hidden = false; i.focus(); return; }
      body[i.dataset.k] = v;
    }
    if (!Object.keys(body).length) return;
    const r = await send("/auto/config", "PUT", body);
    if (!r.ok) { err.textContent = await errFriendly(r); err.hidden = false; return; }
    toast("Soglie salvate"); await refresh();
  }
  async function toggle(e) {
    const want = e.target.checked;
    if (want) {
      const ok = await confirmBox({ title: "Accendere la raccolta automatica?", text: "Ogni tanto l'app leggerà i sottotitoli dei video YouTube dei pro e salverà da sola i pareri sicuri. I sottotitoli non ufficiali sono in una zona grigia dei termini di YouTube. Puoi spegnerla e annullare tutto in qualsiasi momento.", ok: "Accendi" });
      if (!ok) { e.target.checked = false; return; }
    }
    const r = await send("/auto/config", "PUT", { enabled: want });
    if (!r.ok) { toastErr(await errFriendly(r)); e.target.checked = !want; return; }
    toast(want ? "Raccolta automatica accesa" : "Raccolta automatica spenta", want ? "ok" : "info"); await refresh();
  }
  async function runNow() {
    const r = await send("/auto/run", "POST");
    if (r.status === 409) { toast("Una raccolta è già in corso: ne seguo l'avanzamento", "info"); await refresh(); return poll(); }
    if (!r.ok) return toastErr(await errFriendly(r));
    waiting = true; prevRun = st.last_run?.id ?? null; t0 = Date.now(); paintTop(); poll();
  }
  async function undo() {
    if (!await confirmBox({ title: "Annullare tutti i pareri automatici?", text: `Verranno eliminati ${ops.length} pareri raccolti in automatico e gli score si ricalcolano. I tuoi pareri non si toccano. I video già letti non vengono riletti.`, ok: "Annulla tutti", danger: true })) return;
    const r = await send("/auto/opinions", "DELETE");
    if (!r.ok) return toastErr(await errFriendly(r));
    const d = await r.json();
    toast(`${d.removed} ${d.removed === 1 ? "parere automatico rimosso" : "pareri automatici rimossi"}`, "info");
    await refresh(); loadCards();
  }

  // polling gentile: ogni 3 s mentre una raccolta è in corso, poi si ferma
  function poll() {
    clearTimeout(timer);
    if (!alive || !running()) return;
    timer = setTimeout(async () => {
      let s; try { s = await jget("/auto/status"); } catch { return poll(); }
      st = s;
      const done = !s.running && (!waiting || s.last_run?.id !== prevRun || Date.now() - t0 > 9000);
      if (done) {
        const was = waiting; waiting = false;
        await load(true);
        if (was) { const lr = s.last_run; toast(lr ? `Raccolta finita: ${(RUN[lr.status] || [lr.status])[0].toLowerCase()}` : "Raccolta finita", lr?.status === "errore" ? "err" : "ok"); loadCards(); }
        return;
      }
      paintTop(); poll();
    }, 3000);
  }
  async function refresh() { await load(true); }
  async function load(quiet) {
    try { [st, ops] = await Promise.all([jget("/auto/status"), jget("/auto/opinions")]); }
    catch (e) { if (alive) root.innerHTML = `<div class="empty"><h3>Impossibile leggere lo stato</h3><p>${esc(e.message)}</p><button class="btn pri" id="bRetry">Riprova</button></div>`; $("#bRetry", root)?.addEventListener("click", () => load()); return; }
    draw();
    if (st.running) poll();
  }
  return { mount(el) { root = el; root.innerHTML = `<div class="skel" style="height:260px;margin-top:8px"></div>`; load(); }, unmount() { alive = false; clearTimeout(timer); } };
}
