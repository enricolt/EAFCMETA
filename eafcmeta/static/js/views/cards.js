// Vista "Carte": hero con riepilogo, grafici, migliori affari e collezione (griglia / lista / tabella).
import { $, $$, esc, fmt, short, ic, h, store, runCounters, debounce, reduced } from "../util.js";
import { state, on, setLayout, loadCards } from "../state.js";
import { cardTile, futCard, verdictBadge, metaChip, priceTag, vkey, VERD, kind, KIND_NAME } from "../card.js";
import { scatter, histogram, stackBar, bindCharts } from "../charts.js";
import { openForm } from "../form.js";
import { go } from "../router.js";

const r1 = v => String(+(+v).toFixed(1)).replace(".", ",");
const VORD = { MUST_DO: 0, NEUTRAL: 1, ND: 2, AVOID: 3 };
const SORTS = {
  "score:-1": "Score più alto", "gap:-1": "Miglior affare", "price:1": "Prezzo più basso", "price:-1": "Prezzo più alto", "name:1": "Nome A–Z",
};
const KEYFN = {
  score: c => c.scores.final_score, base: c => c.scores.base_score, pro: c => c.scores.pro_sentiment_score ?? -1,
  price: c => c.cost_credits, gap: c => c.value_gap ?? -999, name: c => c.name.toLowerCase(), pos: c => c.position,
  verdict: c => VORD[vkey(c)],
};
let sortKey = store.get("sortKey") || "score", sortDir = +(store.get("sortDir") || -1);

export function filtered() {
  const { q, pos, verdict, metaOnly } = state.filters, qq = q.trim().toLowerCase();
  const a = state.cards.filter(c => (!pos || c.position === pos) && (!verdict || vkey(c) === verdict) &&
    (!metaOnly || c.meta_level === "top" || c.meta_level === "meta") && (!qq || (c.name + " " + c.version + " " + c.position).toLowerCase().includes(qq)));
  const f = KEYFN[sortKey] || KEYFN.score;
  a.sort((x, y) => { const p = f(x), q2 = f(y); return (p < q2 ? -1 : p > q2 ? 1 : 0) * sortDir || y.scores.final_score - x.scores.final_score; });
  return a;
}

function heroHTML() {
  const cs = state.cards, n = cs.length;
  if (!n) return `<section class="hero hero-empty">${emptyIllu()}<div class="hero-l"><span class="eyebrow">${ic("bolt", 14)}Benvenuto</span>
    <h2 class="hero-t">Costruisci la tua <em>collezione</em>.</h2>
    <p class="hero-p">Importa le carte da FUT.GG e FUTBIN, oppure aggiungile a mano: l'app calcola lo score e ti dice se il prezzo conviene.</p>
    <div class="hero-cta"><button class="btn pri lg" data-go="importa">${ic("import", 18)}Importa carte</button><button class="btn lg" data-new>${ic("plus", 18)}Nuova carta</button></div></div></section>`;
  const ok = cs.filter(c => vkey(c) === "MUST_DO").length;
  const avg = cs.reduce((s, c) => s + c.scores.final_score, 0) / n;
  const top = [...cs].sort((a, b) => b.scores.final_score - a.scores.final_score)[0];
  const deals = cs.filter(c => c.verdict === "MUST_DO" && c.value_gap != null).sort((a, b) => b.value_gap - a.value_gap);
  const spot = deals[0] || top, isDeal = !!deals[0];
  return `<section class="hero"><div class="hero-l">
    <span class="eyebrow">${ic("bolt", 14)}Patch ${esc(state.meta?.patch_version ?? "")}${state.meta?.calibrated ? " · calibrata" : ""}</span>
    <h2 class="hero-t">Prendi solo le carte che <em>valgono</em> il prezzo.</h2>
    <p class="hero-p">${n} ${n === 1 ? "carta valutata" : "carte valutate"} su statistiche, PlayStyle, body type e pareri dei creator, confrontate con il mercato.</p>
    <div class="kpis">
      <div class="kpi"><small>Carte</small><b class="num" data-count="${n}">${n}</b><span>${new Set(cs.map(c => c.position)).size} posizioni</span></div>
      <div class="kpi kpi-ok"><small>Conviene</small><b class="num" data-count="${ok}">${ok}</b><span>${Math.round(ok / n * 100)}% del totale</span></div>
      <div class="kpi"><small>Score medio</small><b class="num" data-count="${avg.toFixed(1)}" data-dec="1">${avg.toFixed(1)}</b><span>su 100</span></div>
      <div class="kpi kpi-gold"><small>Score massimo</small><b class="num" data-count="${Math.round(top.scores.final_score)}">${Math.round(top.scores.final_score)}</b><span title="${esc(top.name)}">${esc(top.name)}</span></div>
    </div></div>
    <aside class="spot tilt" data-open="${spot.id}" aria-label="${isDeal ? "Miglior affare" : "Score più alto"}: ${esc(spot.name)}">
      <span class="spot-tag">${ic(isDeal ? "flame" : "trophy", 14)}${isDeal ? "Miglior affare" : "Score più alto"}</span>
      <div class="spot-card">${futCard(spot)}</div>
      <div class="spot-i"><b>${esc(spot.name)}</b><span>${priceTag(spot.cost_credits, true)}${spot.value_gap != null ? `<em class="gap ${spot.value_gap >= 0 ? "pos" : "neg"} num">${spot.value_gap > 0 ? "+" : ""}${spot.value_gap} sul prezzo atteso</em>` : ""}</span></div>
    </aside></section>`;
}

function insightsHTML() {
  const cs = state.cards;
  if (cs.length < 3) return "";
  const cnt = k => cs.filter(c => vkey(c) === k).length;
  return `<section class="insights">
    <div class="panel"><div class="panel-h"><h3>Mappa valore</h3><span class="panel-s">Score in funzione del prezzo · clicca un punto</span></div>${scatter(cs)}
      <ul class="legend inline"><li><i style="background:var(--ok-solid)"></i>Conviene</li><li><i style="background:var(--warn-solid)"></i>In linea</li><li><i style="background:var(--bad-solid)"></i>Evita</li><li><i style="background:var(--nd-solid)"></i>Pochi dati</li></ul></div>
    <div class="panel"><div class="panel-h"><h3>Distribuzione</h3><span class="panel-s">Score finale delle carte</span></div>${histogram(cs)}
      <div class="panel-h sub"><h3>Verdetti</h3></div>${stackBar([{ n: cnt("MUST_DO"), label: "Conviene", cls: "ok" }, { n: cnt("NEUTRAL"), label: "In linea", cls: "warn" }, { n: cnt("AVOID"), label: "Evita", cls: "bad" }, { n: cnt("ND"), label: "Pochi dati", cls: "nd" }])}</div></section>`;
}

function bestHTML() {
  const deals = state.cards.filter(c => c.value_gap != null && c.verdict === "MUST_DO").sort((a, b) => b.value_gap - a.value_gap).slice(0, 10);
  if (!deals.length) return "";
  return `<section class="best"><div class="sec-h"><h3>${ic("flame", 18)}Migliori affari</h3><span class="sec-s">Carte sopra la curva del mercato</span>
    <span class="rail-nav"><button class="icon-btn" data-rail="-1" aria-label="Scorri indietro">${ic("left", 18)}</button><button class="icon-btn" data-rail="1" aria-label="Scorri avanti">${ic("right", 18)}</button></span></div>
    <div class="rail" id="rail">${deals.map((c, i) => cardTile(c, i, { selected: state.compare.includes(c.id) })).join("")}</div></section>`;
}

function skeleton() {
  return `<div class="skel-hero skel"></div><div class="grid">${Array.from({ length: 10 }, (_, i) => `<div class="cw"><div class="skel skel-card" style="--i:${i}"></div><div class="skel skel-line"></div></div>`).join("")}</div>`;
}

function emptyIllu() {
  return `<svg class="illu" viewBox="0 0 320 240" aria-hidden="true"><defs><linearGradient id="il1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--acc)"/><stop offset="1" stop-color="var(--cyan)"/></linearGradient>
    <linearGradient id="il2" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff3b0"/><stop offset="1" stop-color="#d9a52a"/></linearGradient></defs>
    <ellipse cx="160" cy="214" rx="104" ry="12" fill="var(--line)"/>
    <g transform="rotate(-14 120 120)"><rect x="60" y="48" width="104" height="146" rx="14" fill="var(--surface-3)" stroke="var(--line-2)"/><rect x="72" y="60" width="80" height="6" rx="3" fill="var(--line-2)"/></g>
    <g transform="rotate(12 210 120)"><rect x="160" y="40" width="104" height="146" rx="14" fill="var(--surface-2)" stroke="var(--line-2)"/><circle cx="212" cy="90" r="22" fill="var(--line-2)"/><rect x="180" y="132" width="64" height="8" rx="4" fill="var(--line-2)"/><rect x="190" y="150" width="44" height="6" rx="3" fill="var(--line)"/></g>
    <g transform="translate(110 60)"><rect width="104" height="146" rx="14" fill="url(#il2)" stroke="#fff" stroke-opacity=".6" stroke-width="2"/><circle cx="52" cy="58" r="22" fill="#6b4a05" fill-opacity=".25"/><path d="M26 104c0-14 12-22 26-22s26 8 26 22" fill="#6b4a05" fill-opacity=".25"/><rect x="22" y="116" width="60" height="8" rx="4" fill="#6b4a05" fill-opacity=".35"/><text x="18" y="34" font-size="26" font-weight="900" fill="#4a3300">?</text></g>
    <circle cx="258" cy="62" r="18" fill="url(#il1)"/><path d="M251 62h14M258 55v14" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>`;
}

function emptyResults() {
  return `<div class="empty">${emptyIllu()}<h3>${state.cards.length ? "Nessuna carta corrisponde" : "Ancora nessuna carta"}</h3>
    <p>${state.cards.length ? "Prova a cambiare la ricerca o a togliere qualche filtro." : "Aggiungine una a mano oppure importa quelle salvate da FUT.GG e FUTBIN."}</p>
    <div class="row">${state.cards.length ? `<button class="btn pri" data-reset>${ic("x", 16)}Azzera i filtri</button>` : `<button class="btn pri" data-go="importa">${ic("import", 16)}Importa</button><button class="btn" data-new>${ic("plus", 16)}Nuova carta</button>`}</div></div>`;
}

function listRow(c, i) {
  const k = kind(c), sel = state.compare.includes(c.id), s = c.scores.final_score;
  return `<div class="lrow" data-id="${c.id}" style="--i:${Math.min(i, 18)}">
    <button class="lrow-main" data-open="${c.id}" aria-label="${esc(c.name)}, ${esc(c.version)}, score ${Math.round(s)}">
      <span class="rate k-${k}"><b class="num">${Math.round(s)}</b><i>${esc(c.position)}</i></span>
      <span class="lrow-n"><b>${esc(c.name)}</b><small>${esc(c.version || KIND_NAME[k])}${c.is_sbc ? " · SBC" : ""}</small></span>
      <span class="lrow-bar" aria-hidden="true"><u style="--w:${Math.min(100, s)}%"></u></span>
      <span class="lrow-p">${priceTag(c.cost_credits, true)}${c.value_gap != null ? `<em class="gap ${c.value_gap >= 0 ? "pos" : "neg"} num">${c.value_gap > 0 ? "+" : ""}${c.value_gap}</em>` : ""}</span>
      <span class="lrow-v">${verdictBadge(c)}${metaChip(c, true)}</span>
    </button>
    <button class="cw-sel sm" data-cmp="${c.id}" aria-pressed="${sel}" aria-label="${sel ? "Togli dal confronto" : "Aggiungi al confronto"}: ${esc(c.name)}">${ic(sel ? "check" : "plus", 16)}</button></div>`;
}

const COLS = [["name", "Carta"], ["pos", "Pos."], ["score", "Score", 1], ["base", "Base", 1], ["pro", "Pro", 1], ["price", "Prezzo", 1], ["gap", "Vs mercato", 1], ["verdict", "Verdetto"]];
function table(a) {
  const th = COLS.map(([k, l, r]) => `<th scope="col" class="${r ? "r" : ""}" aria-sort="${sortKey === k ? (sortDir > 0 ? "ascending" : "descending") : "none"}"><button data-sort="${k}">${l}<span class="sort-i">${sortKey === k ? ic(sortDir > 0 ? "up" : "down", 13) : ""}</span></button></th>`).join("");
  const rows = a.map(c => {
    const k = kind(c), sel = state.compare.includes(c.id), s = c.scores.final_score;
    return `<tr data-id="${c.id}"><td class="c-sel"><button class="cw-sel sm" data-cmp="${c.id}" aria-pressed="${sel}" aria-label="${sel ? "Togli dal confronto" : "Aggiungi al confronto"}: ${esc(c.name)}">${ic(sel ? "check" : "plus", 16)}</button></td>
      <td><button class="tname" data-open="${c.id}"><span class="rate sm k-${k}"><b class="num">${Math.round(s)}</b></span><span><b>${esc(c.name)}</b><small>${esc(c.version)}${c.is_sbc ? " · SBC" : ""}</small></span></button></td>
      <td><span class="pos-chip">${esc(c.position)}</span></td>
      <td class="r"><span class="tbar"><u style="--w:${Math.min(100, s)}%"></u></span><b class="num">${s.toFixed(1)}</b></td>
      <td class="r num">${r1(c.scores.base_score)}</td><td class="r num">${c.scores.pro_sentiment_score == null ? "–" : r1(c.scores.pro_sentiment_score)}</td>
      <td class="r">${priceTag(c.cost_credits, true)}</td>
      <td class="r">${c.value_gap != null ? `<em class="gap ${c.value_gap >= 0 ? "pos" : "neg"} num">${c.value_gap > 0 ? "+" : ""}${c.value_gap}</em>` : "<span class='mut'>–</span>"}</td>
      <td>${verdictBadge(c)}${metaChip(c, true)}</td></tr>`;
  }).join("");
  return `<div class="tbl-w"><table class="tbl"><thead><tr><th class="c-sel"><span class="sr-only">Confronta</span></th>${th}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

export default function view() {
  let root, offs = [], lastKey = "";
  const resultsEl = () => $("#results", root);

  function renderResults(animate = false) {
    const a = filtered();
    state.navIds = a.map(c => c.id);
    const el = resultsEl(); if (!el) return;
    const L = state.layout;
    el.className = "results lay-" + L + (animate ? " enter" : "");
    el.innerHTML = !a.length ? emptyResults() : L === "table" ? table(a)
      : L === "list" ? `<div class="list">${a.map(listRow).join("")}</div>`
      : `<div class="grid">${a.map((c, i) => cardTile(c, i, { selected: state.compare.includes(c.id) })).join("")}</div>`;
    $("#count", root).innerHTML = `<b class="num">${a.length}</b> ${a.length === 1 ? "carta" : "carte"}${a.length !== state.cards.length ? ` di ${state.cards.length}` : ""}`;
    $("#reset", root).hidden = !(state.filters.q || state.filters.pos || state.filters.verdict || state.filters.metaOnly);
    $("#sort", root).hidden = L === "table";
    $$("[data-layout]", root).forEach(b => b.setAttribute("aria-pressed", String(b.dataset.layout === L)));
    if (animate && !reduced()) setTimeout(() => el.classList.remove("enter"), 1400);
  }

  function chips() {
    const cs = state.cards, f = state.filters;
    const pos = [...new Set(cs.map(c => c.position))].sort();
    const cnt = p => cs.filter(c => c.position === p).length;
    $("#chipsPos", root).innerHTML = [["", "Tutte", cs.length], ...pos.map(p => [p, p, cnt(p)])].map(([p, l, n]) =>
      `<button class="chip" data-pos="${p}" aria-pressed="${f.pos === p}">${l}<span class="num">${n}</span></button>`).join("");
    const vc = k => cs.filter(c => vkey(c) === k).length;
    $("#chipsV", root).innerHTML = [["MUST_DO", "check"], ["NEUTRAL", "minus"], ["AVOID", "x"], ["ND", "info"]].map(([k, i]) =>
      `<button class="chip v-${VERD[k].cls}" data-verdict="${k}" aria-pressed="${f.verdict === k}">${ic(i, 13)}${VERD[k].label}<span class="num">${vc(k)}</span></button>`).join("") +
      `<button class="chip v-meta" data-meta aria-pressed="${f.metaOnly}">${ic("star", 13)}Solo meta</button>`;
  }
  function syncChips() {
    const f = state.filters;
    $$("[data-pos]", root).forEach(b => b.setAttribute("aria-pressed", String(b.dataset.pos === f.pos)));
    $$("[data-verdict]", root).forEach(b => b.setAttribute("aria-pressed", String(b.dataset.verdict === f.verdict)));
    $$("[data-meta]", root).forEach(b => b.setAttribute("aria-pressed", String(f.metaOnly)));
  }

  function build() {
    const top = $("#top", root);
    if (!state.loaded) { top.innerHTML = skeleton(); $("#coll", root).hidden = true; return; }
    $("#coll", root).hidden = false;
    if (state.error) { top.innerHTML = `<div class="empty"><h3>Impossibile caricare le carte</h3><p>${esc(state.error)}</p><button class="btn pri" data-retry>${ic("trend", 16)}Riprova</button></div>`; $("#coll", root).hidden = true; return; }
    top.innerHTML = heroHTML() + insightsHTML() + bestHTML();
    runCounters(top); bindCharts(top);
    chips(); renderResults(true);
    $("#coll", root).hidden = !state.cards.length;
  }

  return {
    mount(el) {
      root = el;
      const f = state.filters;
      root.innerHTML = `<div id="top"></div>
      <section class="coll" id="coll"><div class="sec-h"><h3>${ic("cards", 18)}Collezione</h3><span class="sec-s" id="count"></span></div>
        <div class="toolbar">
          <div class="sbox"><span class="sbox-i">${ic("search", 18)}</span><input class="inp" type="search" id="q" placeholder="Cerca giocatore, versione o ruolo…" aria-label="Cerca giocatore" autocomplete="off" value="${esc(f.q)}"><kbd class="sbox-k" aria-hidden="true">/</kbd></div>
          <label class="sel-w"><span class="sr-only">Ordina per</span><select class="inp sel" id="sort">${Object.entries(SORTS).map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
          <div class="seg" role="group" aria-label="Vista"><button data-layout="grid" aria-pressed="true" title="Griglia di carte">${ic("grid", 17)}<span>Carte</span></button><button data-layout="list" aria-pressed="false" title="Elenco compatto">${ic("list", 17)}<span>Elenco</span></button><button data-layout="table" aria-pressed="false" title="Tabella ordinabile">${ic("table", 17)}<span>Tabella</span></button></div>
        </div>
        <div class="chiprow"><div class="chips" id="chipsPos" role="group" aria-label="Filtra per posizione"></div></div>
        <div class="chiprow"><div class="chips" id="chipsV" role="group" aria-label="Filtra per verdetto"></div><button class="link-btn" id="reset" hidden>${ic("x", 14)}Azzera filtri</button></div>
        <div class="results" id="results" aria-live="polite"></div></section>`;
      $("#sort", root).value = `${sortKey}:${sortDir}` in SORTS ? `${sortKey}:${sortDir}` : "score:-1";
      build();

      const q = $("#q", root);
      q.addEventListener("input", debounce(() => { state.filters.q = q.value; renderResults(); }, 90));
      q.addEventListener("keydown", e => { if (e.key === "Escape") { if (q.value) { q.value = ""; state.filters.q = ""; renderResults(); } else q.blur(); } if (e.key === "ArrowDown") { e.preventDefault(); $(".cw-open, .lrow-main, .tname", root)?.focus(); } });
      $("#sort", root).onchange = e => { [sortKey, sortDir] = e.target.value.split(":"); sortDir = +sortDir; store.set("sortKey", sortKey); store.set("sortDir", sortDir); renderResults(true); };
      root.addEventListener("click", e => {
        const t = e.target;
        let b;
        if ((b = t.closest("[data-pos]"))) { state.filters.pos = b.dataset.pos; syncChips(); renderResults(true); }
        else if ((b = t.closest("[data-verdict]"))) { state.filters.verdict = state.filters.verdict === b.dataset.verdict ? "" : b.dataset.verdict; syncChips(); renderResults(true); }
        else if (t.closest("[data-meta]")) { state.filters.metaOnly = !state.filters.metaOnly; syncChips(); renderResults(true); }
        else if (t.closest("#reset, [data-reset]")) { state.filters = { q: "", pos: "", verdict: "", metaOnly: false }; q.value = ""; syncChips(); renderResults(true); }
        else if ((b = t.closest("[data-layout]"))) { setLayout(b.dataset.layout); renderResults(true); }
        else if ((b = t.closest("[data-sort]"))) { const k = b.dataset.sort; if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = ["name", "pos", "verdict", "price"].includes(k) ? 1 : -1; } store.set("sortKey", sortKey); store.set("sortDir", sortDir); renderResults(); $(`[data-sort="${k}"]`, root)?.focus(); }
        else if ((b = t.closest("[data-go]"))) go(b.dataset.go);
        else if (t.closest("[data-new]")) openForm(null);
        else if (t.closest("[data-retry]")) loadCards();
        else if ((b = t.closest("[data-rail]"))) { const r = $("#rail", root); r.scrollBy({ left: +b.dataset.rail * r.clientWidth * .8, behavior: reduced() ? "auto" : "smooth" }); }
      });
      offs.push(on("cards", () => { lastKey = ""; build(); }));
      offs.push(on("layout", () => { }));
      offs.push(on("compare", () => {
        $$("[data-cmp]", root).forEach(b => { const s = state.compare.includes(+b.dataset.cmp); b.setAttribute("aria-pressed", String(s)); b.innerHTML = ic(s ? "check" : "plus", 16); });
      }));
      // navigazione con le frecce tra le carte
      root.addEventListener("keydown", e => {
        if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
        const cur = e.target.closest?.("[data-open]"); if (!cur || cur.closest(".rail,.spot")) return;
        const all = $$("#results [data-open]", root).filter(x => x.offsetParent);
        const i = all.indexOf(cur); if (i < 0) return;
        const cr = cur.getBoundingClientRect(), cx = cr.left + cr.width / 2, cy = cr.top + cr.height / 2;
        let nx = null;
        if (e.key === "ArrowLeft") nx = all[i - 1]; else if (e.key === "ArrowRight") nx = all[i + 1];
        else {
          const dir = e.key === "ArrowDown" ? 1 : -1;
          const cand = all.filter(x => { const r = x.getBoundingClientRect(); return (r.top + r.height / 2 - cy) * dir > cr.height * .4; });
          let best = 1e9;
          cand.forEach(x => { const r = x.getBoundingClientRect(), d = Math.abs(r.top + r.height / 2 - cy) * 3 + Math.abs(r.left + r.width / 2 - cx); if (d < best) { best = d; nx = x; } });
        }
        if (nx) { e.preventDefault(); nx.focus(); nx.scrollIntoView({ block: "nearest", behavior: reduced() ? "auto" : "smooth" }); }
      });
    },
    unmount() { offs.forEach(f => f()); offs = []; },
    focusSearch() { const q = $("#q", root); q?.focus(); q?.select(); },
  };
}
