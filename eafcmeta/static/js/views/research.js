// Vista "Ricerca pareri": incolla un testo, rivedi le proposte (accetta/correggi/rifiuta), controlla le fonti disponibili.
import { $, $$, esc, ic } from "../util.js";
import { jget, send, errFriendly } from "../api.js";
import { state, loadCards } from "../state.js";
import { toast, toastErr } from "../ui.js";
import { STANCE_SHORT, stanceBadge, critName, pct, srcLink } from "../labels.js";

const SRC_NAME = { paste: "Testo incollato", youtube: "YouTube", x: "X (Twitter)", tiktok: "TikTok", instagram: "Instagram" };
const SRC_ICON = { paste: "clipboard", youtube: "play", x: "search", tiktok: "alert", instagram: "alert" };
const TABS = [["paste", "Incolla un testo", "clipboard"], ["queue", "Da rivedere", "research"], ["sources", "Fonti", "info"]];
const confCls = c => c >= 0.75 ? "ok" : c >= 0.6 ? "warn" : "bad";
const cardLabel = c => `${c.name}${c.version ? " · " + c.version : ""} (${c.position})`;

export default function view() {
  let root, onClick, tab = "paste", queue = [], sources = null, paste = null, editing = new Set(), byId = new Map(), busy = new Set();

  const cardOptions = sel => [...state.cards].sort((a, b) => a.name.localeCompare(b.name, "it")).map(c => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(cardLabel(c))}</option>`).join("");

  /* ---------- una proposta ---------- */
  function proposal(p) {
    const done = p.status !== "pending", c = p.card, ed = editing.has(p.id) && !done;
    const crit = (p.criteria || []).map(k => `<span class="crit ${k.polarity > 0 ? "pos" : "neg"}" title="${k.polarity > 0 ? "Citato in positivo" : "Citato in negativo"}">${ic(k.polarity > 0 ? "up" : "down", 12)}${esc(critName(k.criterion))}</span>`).join("");
    const head = `<header class="pr-h"><div class="pr-card">${c ? `<button class="link-btn pr-name" data-open="${p.card_id}">${esc(c.name)}</button><small>${esc(c.version || "")} · ${esc(c.position)}</small>` : `<b>Carta #${p.card_id}</b>`}</div>
      <div class="pr-meta"><span class="pr-cr">${ic("star", 13)}${esc(p.creator)}</span>${stanceBadge(p.stance)}${p.score != null ? `<span class="op-s num" title="Voto">${p.score}</span>` : ""}</div></header>`;
    const conf = `<div class="conf ${confCls(p.confidence)}" title="Quanto l'app è sicura di aver letto bene"><span>Confidenza</span><i><u style="--w:${Math.round(p.confidence * 100)}%"></u></i><b class="num">${pct(p.confidence)}</b></div>`;
    const flags = [p.ambiguous ? `<span class="badge b-warn">${ic("alert", 13)}Carta ambigua</span>` : "", p.method ? `<span class="badge b-kind">${p.method === "offline" ? "Parole chiave" : "Modello"}</span>` : ""].join("");
    const body = `<blockquote class="pr-reason">${p.reason ? esc(p.reason) : "<i>Nessun motivo estratto.</i>"}</blockquote>
      ${p.note ? `<p class="pr-note">${ic("info", 14)}${esc(p.note)}</p>` : ""}
      ${crit ? `<div class="pr-crit"><span>Criteri citati</span>${crit}</div>` : ""}
      <div class="pr-foot">${conf}${flags}${srcLink(p.url)}</div>
      ${p.excerpt ? `<details class="more"><summary>Testo originale</summary><p class="pr-ex">${esc(p.excerpt)}</p></details>` : ""}`;
    if (done) return `<article class="pr st-${p.status}" data-pid="${p.id}">${head}${body}<div class="pr-done">${ic(p.status === "accepted" ? "check" : "x", 15)}${p.status === "accepted" ? "Accettata: ora è un parere della carta" : "Rifiutata"}</div></article>`;
    const form = ed ? `<form class="pr-edit" data-edit="${p.id}"><div class="fg2">
        <div><span class="lbl" id="se${p.id}">Scelta</span><div class="seg stance" role="radiogroup" aria-labelledby="se${p.id}">${Object.entries(STANCE_SHORT).map(([s, l]) => `<button type="button" role="radio" data-st="${s}" aria-checked="${s === p.stance}" class="sn-${s}">${l}</button>`).join("")}</div></div>
        <div><label class="lbl" for="sc${p.id}">Voto 0–100 <span class="lbl-s">(sì ≥ 70 · no ≤ 60)</span></label><input class="inp" id="sc${p.id}" type="number" min="0" max="100" step="0.5" inputmode="decimal" value="${p.score ?? ""}"></div>
        <div class="span2"><label class="lbl" for="ca${p.id}">Carta</label><select class="inp sel" id="ca${p.id}">${cardOptions(p.card_id)}</select></div>
        <div class="span2"><label class="lbl" for="re${p.id}">Motivo</label><textarea class="inp" id="re${p.id}" maxlength="800" rows="3">${esc(p.reason)}</textarea></div></div>
        <p class="form-err" role="alert" hidden></p></form>` : "";
    const dis = busy.has(p.id) ? "disabled" : "";
    return `<article class="pr${p.ambiguous ? " amb" : ""}" data-pid="${p.id}">${head}${body}${form}
      <div class="pr-act"><button class="btn danger" data-rej="${p.id}" ${dis}>${ic("x", 16)}Rifiuta</button><span class="grow"></span>
        <button class="btn" data-edit-toggle="${p.id}" aria-expanded="${ed}">${ic("edit", 16)}${ed ? "Annulla modifica" : "Correggi"}</button>
        <button class="btn pri" data-acc="${p.id}" ${dis}>${ic("check", 16)}${ed ? "Salva e accetta" : "Accetta"}</button></div></article>`;
  }
  function proposalList(items) {
    const out = [], seen = new Set();
    items.forEach(p => {
      if (p.ambiguous && p.group_key) {
        const k = `${p.creator}|${p.url}|${p.group_key}`;
        if (seen.has(k)) return; seen.add(k);
        const g = items.filter(x => x.ambiguous && x.group_key && `${x.creator}|${x.url}|${x.group_key}` === k);
        if (g.length > 1) { out.push(`<div class="amb-group"><p class="amb-t">${ic("alert", 16)}<span><b>${esc(p.creator)}</b> cita una carta che può essere più di una. Accetta quella giusta: le altre si chiudono da sole.</span></p>${g.map(proposal).join("")}</div>`); return; }
      }
      out.push(proposal(p));
    });
    return out.join("");
  }

  /* ---------- schede ---------- */
  function paintPaste() {
    const r = paste;
    let res = "";
    if (r) {
      const items = r.created.map(p => byId.get(p.id) || p), pend = items.filter(p => p.status === "pending").length;
      res = `<div class="res" aria-live="polite"><div class="res-h ${items.length ? "ok" : ""}">${ic(items.length ? "check" : "info", 18)}<b>${items.length ? `${items.length} ${items.length === 1 ? "proposta trovata" : "proposte trovate"}: controllale, nulla è ancora salvato.` : "Nessuna proposta da questo testo."}</b></div>
        <div class="rpills"><span class="rpill ${items.length ? "ok" : ""}"><b class="num">${items.length}</b>nuove</span>${r.duplicates.length ? `<span class="rpill"><b class="num">${r.duplicates.length}</b>già viste</span>` : ""}${r.unresolved.length ? `<span class="rpill bad"><b class="num">${r.unresolved.length}</b>senza carta</span>` : ""}<span class="rpill">${r.mode === "offline" ? "lettura per parole chiave" : "lettura con il modello"}</span></div>
        ${r.warnings.length ? `<ul class="rlist">${r.warnings.map(w => `<li class="warnl">${ic("info", 15)}<span>${esc(w)}</span></li>`).join("")}</ul>` : ""}
        ${r.duplicates.length ? `<p class="hint">${r.duplicates.length} ${r.duplicates.length === 1 ? "proposta era già presente" : "proposte erano già presenti"} (stesso link e stesso creator): non le ho duplicate.</p>` : ""}
        ${items.length ? `<div class="prs">${proposalList(items)}</div>` : ""}
        ${items.length && !pend ? `<p class="hint">Hai deciso tutte le proposte.</p>` : ""}</div>`;
    }
    const o = $("#pOut", root); if (o) o.innerHTML = res;
  }
  function paintQueue() {
    const q = $("#qBox", root); if (!q) return;
    q.innerHTML = queue.length ? `<div class="prs">${proposalList(queue)}</div>`
      : `<div class="empty"><h3>Nessuna proposta in attesa</h3><p>Quando incolli un testo, le proposte trovate arrivano qui. Niente diventa un parere finché non lo accetti.</p><div class="row"><button class="btn pri" data-tab="paste">${ic("clipboard", 16)}Incolla un testo</button></div></div>`;
    const n = $("#qN", root); if (n) { n.textContent = queue.length; n.hidden = !queue.length; }
  }
  function paintSources() {
    const box = $("#sBox", root); if (!box) return;
    if (!sources) { box.innerHTML = `<div class="skel" style="height:160px"></div>`; return; }
    if (sources.error) { box.innerHTML = `<div class="note warn">${ic("alert", 18)}<div>${esc(sources.error)}</div></div>`; return; }
    box.innerHTML = `<ul class="srcs">${sources.sources.map(s => {
      const auto = ["tiktok", "instagram"].includes(s.name), lab = s.available ? "Disponibile" : auto ? "Solo a mano" : "Non attiva";
      return `<li class="${s.available ? "on" : ""}"><span class="src-ic">${ic(SRC_ICON[s.name] || "info", 18)}</span><div><b>${esc(SRC_NAME[s.name] || s.name)}</b><p>${esc(s.note)}</p></div><span class="badge b-${s.available ? "ok" : "nd"}">${lab}</span></li>`;
    }).join("")}</ul><div class="note soft">${ic("info", 18)}<div>${esc(sources.note)}</div></div>
      <div class="note">${ic("sync", 18)}<div>I video YouTube dei pro vengono letti anche in automatico dalla sezione <a href="#/automatico">Automatico</a>, con i pareri sicuri salvati già come «automatici».</div></div>`;
  }
  function setTab(t) {
    tab = t;
    $$("[data-tab-b]", root).forEach(b => b.setAttribute("aria-selected", String(b.dataset.tabB === t)));
    ["paste", "queue", "sources"].forEach(k => { $("#p-" + k, root).hidden = k !== t; });
    if (t === "queue") loadQueue();
    if (t === "sources" && !sources) loadSources();
  }

  /* ---------- dati ---------- */
  async function loadQueue() {
    try { queue = await jget("/research/proposals?status=pending"); } catch (e) { $("#qBox", root).innerHTML = `<div class="note warn">${ic("alert", 18)}<div>${esc(e.message)}</div></div>`; return; }
    paintQueue();
  }
  async function loadSources() {
    try { sources = await jget("/research/sources"); } catch (e) { sources = { error: e.message }; }
    paintSources();
  }
  async function doPaste(e) {
    e.preventDefault();
    const text = $("#pText", root).value.trim(), creator = $("#pCr", root).value.trim(), url = $("#pUrl", root).value.trim(), cid = $("#pCard", root).value;
    const err = $("#pErr", root); err.hidden = true;
    const bad = m => { err.textContent = m; err.hidden = false; };
    if (!text) return bad("Incolla prima il testo da leggere.");
    if (!creator) return bad("Scrivi chi ha espresso il parere (creator).");
    if (url && !/^https?:\/\//i.test(url)) return bad("Il link deve iniziare con http:// o https://");
    const b = $("#pGo", root); b.disabled = true; b.querySelector("span").textContent = "Leggo il testo…";
    try {
      const r = await send("/research/paste", "POST", { text, creator, url, card_id: cid ? +cid : null });
      if (!r.ok) return bad(await errFriendly(r));
      const d = await r.json();
      d.created.forEach(p => byId.set(p.id, p)); paste = d; paintPaste();
      toast(d.created.length ? "Proposte pronte da rivedere" : "Nessuna proposta trovata", d.created.length ? "ok" : "info");
      $("#pOut", root).scrollIntoView({ behavior: "smooth", block: "nearest" });
    } finally { b.disabled = false; b.querySelector("span").textContent = "Cerca pareri nel testo"; }
  }
  function formValues(id) {
    const card = $(`[data-pid="${id}"]`, root), f = card?.querySelector("form.pr-edit");
    if (!f) return {};
    const st = f.querySelector('[data-st][aria-checked="true"]')?.dataset.st, sc = f.querySelector('input[type=number]').value.trim();
    return { stance: st, score: sc === "" ? null : +sc, reason: f.querySelector("textarea").value, card_id: +f.querySelector("select").value };
  }
  async function decide(id, kind) {
    const card = $(`[data-pid="${id}"]`, root);
    const body = kind === "accept" && editing.has(id) ? formValues(id) : undefined;
    busy.add(id); card?.querySelectorAll("button").forEach(b => b.disabled = true);
    try {
      const r = await send(`/research/proposals/${id}/${kind}`, "POST", body);
      if (!r.ok) {
        const m = await errFriendly(r), fe = card?.querySelector(".form-err");
        if (fe) { fe.textContent = m; fe.hidden = false; }
        toastErr(m);
        if (r.status === 409 || r.status === 404) { await loadQueue(); refreshPaste(); }
        return;
      }
      const d = await r.json();
      byId.set(id, kind === "accept" ? d.proposal : d); editing.delete(id);
      if (kind === "accept") { toast(d.replaced ? "Parere salvato (ha sostituito quello precedente dello stesso creator)" : "Parere salvato sulla carta"); loadCards(); } else toast("Proposta rifiutata", "info");
      await loadQueue(); refreshPaste();
    } finally { busy.delete(id); card?.querySelectorAll("button").forEach(b => { b.disabled = false; }); }
  }
  // dopo una decisione, anche le alternative chiuse (carta ambigua) cambiano stato: si riallineano dalla coda
  function refreshPaste() {
    if (!paste) return;
    const pend = new Set(queue.map(p => p.id));
    paste.created.forEach(p => { const cur = byId.get(p.id) || p; if (cur.status === "pending" && !pend.has(p.id)) byId.set(p.id, { ...cur, status: "rejected" }); });
    paintPaste();
  }

  return {
    mount(el) {
      root = el;
      root.innerHTML = `<div class="rs"><section class="panel cal-intro"><div class="cal-ic">${ic("research", 28)}</div><div><h3>Pareri dei pro, sempre con la tua conferma</h3>
        <p>Incolla un post, una didascalia o la trascrizione di un video: l'app riconosce le carte citate e propone «sì / dipende / no» con voto, motivo e criteri. Una proposta diventa un parere vero solo quando la accetti (puoi anche correggerla).</p></div></section>
        <section class="panel"><div class="tabs" role="tablist" aria-label="Ricerca pareri">${TABS.map(([k, l, i]) => `<button role="tab" data-tab-b="${k}" aria-selected="${k === "paste"}">${ic(i, 17)}${l}${k === "queue" ? '<em class="tab-n" id="qN" hidden>0</em>' : ""}</button>`).join("")}</div>
          <div id="p-paste"><form id="pForm" class="pform" autocomplete="off" novalidate>
            <div><label class="lbl" for="pText">Testo da leggere <span class="lbl-s">(post, didascalia, trascrizione)</span></label>
              <textarea class="inp" id="pText" rows="7" maxlength="100000" placeholder="es. Chloe Kelly è una bomba: scatto e finalizzazione da vertice, la prendo senza pensarci, voto 88."></textarea></div>
            <div class="fg2"><div><label class="lbl" for="pCr">Creator</label><input class="inp" id="pCr" list="prCr" maxlength="40" placeholder="es. Team Gullit" required><datalist id="prCr">${(state.meta?.creators || []).map(x => `<option value="${esc(x)}">`).join("")}</datalist></div>
              <div><label class="lbl" for="pUrl">Link <span class="lbl-s">(facoltativo)</span></label><input class="inp" id="pUrl" type="text" inputmode="url" maxlength="300" placeholder="https://…"></div>
              <div class="span2"><label class="lbl" for="pCard">Carta <span class="lbl-s">(facoltativa: se la scegli, il testo parla solo di lei)</span></label><select class="inp sel" id="pCard"><option value="">Riconosci le carte dal testo</option>${cardOptions()}</select></div></div>
            <p class="form-err" id="pErr" role="alert" hidden></p>
            <div class="row end"><button class="btn pri" id="pGo" type="submit">${ic("search", 16)}<span>Cerca pareri nel testo</span></button></div></form>
            <div id="pOut"></div></div>
          <div id="p-queue" hidden><p class="hint">Le proposte in attesa. Accettale così come sono, correggile (scelta, voto, motivo o carta) oppure rifiutale.</p><div id="qBox"><div class="skel" style="height:160px"></div></div></div>
          <div id="p-sources" hidden><div id="sBox"></div></div></section></div>`;
      $$("[data-tab-b]", root).forEach(b => b.onclick = () => setTab(b.dataset.tabB));
      $("#pForm", root).onsubmit = doPaste;
      onClick = e => {
        const t = e.target.closest("[data-tab]"); if (t) return setTab(t.dataset.tab);
        const a = e.target.closest("[data-acc]"); if (a) return decide(+a.dataset.acc, "accept");
        const r = e.target.closest("[data-rej]"); if (r) return decide(+r.dataset.rej, "reject");
        const ed = e.target.closest("[data-edit-toggle]");
        if (ed) { const id = +ed.dataset.editToggle; editing.has(id) ? editing.delete(id) : editing.add(id); paintQueue(); paintPaste(); return; }
        const st = e.target.closest("[data-st]");
        if (st) $$("[data-st]", st.parentElement).forEach(x => x.setAttribute("aria-checked", String(x === st)));
      };
      root.addEventListener("click", onClick);
      loadQueue().then(() => { if (queue.length && !paste) setTab("queue"); });
    },
    unmount() { root?.removeEventListener("click", onClick); },
  };
}
