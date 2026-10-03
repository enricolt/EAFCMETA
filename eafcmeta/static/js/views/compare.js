// Vista "Confronta": 2-3 carte affiancate con radar sovrapposti, barre delle stats e verdetto.
import { $, $$, esc, fmt, ic, debounce } from "../util.js";
import { jget } from "../api.js";
import { state, on, toggleCompare, clearCompare, MAX_COMPARE, byId } from "../state.js";
import { futCard, verdictBadge, metaChip, priceTag, kind, vkey, VERD } from "../card.js";
import { radar, statBar, SERIES, bindCharts } from "../charts.js";
import { toast } from "../ui.js";
import { go } from "../router.js";

const cache = new Map();
on("cards", () => cache.clear());

export default function view() {
  let root, offs = [], q = "", seq = 0;

  const slot = (c, i) => c
    ? `<div class="slot on" style="--sc:${SERIES[i]}"><i class="slot-dot"></i><span class="rate sm k-${kind(c)}"><b class="num">${Math.round(c.scores.final_score)}</b></span><span class="slot-n"><b>${esc(c.name)}</b><small>${esc(c.version)} · ${esc(c.position)}</small></span><button class="icon-btn" data-rm="${c.id}" aria-label="Togli ${esc(c.name)} dal confronto">${ic("close", 16)}</button></div>`
    : `<div class="slot"><span class="slot-plus">${ic("plus", 18)}</span><span class="slot-n"><b>Aggiungi una carta</b><small>${i < 2 ? "obbligatoria" : "facoltativa"}</small></span></div>`;

  function picker() {
    const qq = q.trim().toLowerCase();
    const a = state.cards.filter(c => !qq || (c.name + " " + c.version + " " + c.position).toLowerCase().includes(qq)).sort((x, y) => y.scores.final_score - x.scores.final_score).slice(0, 40);
    const full = state.compare.length >= MAX_COMPARE;
    return a.map(c => { const s = state.compare.includes(c.id); return `<button class="prow ${s ? "on" : ""}" data-pick="${c.id}" ${!s && full ? "disabled" : ""} aria-pressed="${s}">
      <span class="rate sm k-${kind(c)}"><b class="num">${Math.round(c.scores.final_score)}</b></span><span class="prow-n"><b>${esc(c.name)}</b><small>${esc(c.version)}</small></span><span class="pos-chip">${esc(c.position)}</span>${priceTag(c.cost_credits)}<span class="prow-a">${ic(s ? "check" : "plus", 16)}</span></button>`; }).join("") || `<p class="hint pad">Nessuna carta trovata.</p>`;
  }

  function best(vals, hi = true) { const v = vals.filter(x => x != null); if (v.length < 2) return -1; const t = hi ? Math.max(...v) : Math.min(...v); return v.filter(x => x === t).length === v.length ? -1 : vals.indexOf(t); }

  function compareHTML(ds) {
    const n = ds.length, meta = state.meta, w = d => meta.role_weights[d.position] || {};
    // statistiche: unione delle più importanti di ogni ruolo
    const score = {};
    ds.forEach(d => Object.entries(w(d)).forEach(([k, v]) => { score[k] = (score[k] || 0) + v; }));
    const keys = Object.keys(score).sort((a, b) => score[b] - score[a]).slice(0, 12);
    const radarKeys = keys.slice(0, 8);
    const colors = ds.map((_, i) => SERIES[i]);
    const nm = k => meta.stat_names[k] || k.replaceAll("_", " ");
    const rows = [
      ["Score finale", ds.map(d => d.scores.final_score), v => v.toFixed(1).replace(".", ","), true],
      ["Score base", ds.map(d => d.scores.base_score), v => String(v).replace(".", ","), true],
      ["Parere dei pro", ds.map(d => d.scores.pro_sentiment_score), v => v == null ? "–" : String(+(+v).toFixed(1)).replace(".", ","), true],
      ["Prezzo", ds.map(d => d.cost_credits), v => fmt(v), false],
      ["Vs mercato", ds.map(d => d.value_gap), v => v == null ? "–" : (v > 0 ? "+" : "") + v, true],
    ];
    const bs = best(ds.map(d => d.scores.final_score)), bv = best(ds.map(d => d.value_gap));
    const sum = [];
    if (bs >= 0) sum.push(`<b>${esc(ds[bs].name)}</b> ha lo score più alto (${ds[bs].scores.final_score.toFixed(1).replace(".", ",")}).`);
    if (bv >= 0) sum.push(`<b>${esc(ds[bv].name)}</b> è il migliore rispetto al prezzo (${ds[bv].value_gap > 0 ? "+" : ""}${ds[bv].value_gap} sul prezzo atteso).`);
    const cheap = best(ds.map(d => d.cost_credits), false);
    if (cheap >= 0 && cheap !== bs) sum.push(`<b>${esc(ds[cheap].name)}</b> costa meno (${fmt(ds[cheap].cost_credits)} crediti).`);
    const cols = `style="--n:${n}"`;
    return `<div class="cmp-scroll"><div class="cmp-grid" ${cols}>
      ${ds.map((d, i) => `<div class="cmp-col" style="--sc:${SERIES[i]}"><div class="cmp-card tilt" data-open="${d.id}">${futCard(d)}</div><div class="cmp-id"><i></i><b>${esc(d.name)}</b><small>${esc(d.version)} · ${esc(d.position)}</small></div><div class="cmp-v">${verdictBadge(d)}${metaChip(d)}</div></div>`).join("")}
    </div>
    <div class="cmp-grid rows" ${cols}>${rows.map(([l, vals, f, hi]) => { const b = best(vals, hi); return `<div class="cmp-rl">${l}</div>${vals.map((v, i) => `<div class="cmp-c ${i === b ? "best" : ""}" style="--sc:${SERIES[i]}"><b class="num">${f(v)}</b>${i === b ? `<span class="crown" title="Migliore">${ic("trophy", 13)}</span>` : ""}</div>`).join("")}`; }).join("")}
      <div class="cmp-rl">Verdetto</div>${ds.map(d => `<div class="cmp-c" title="${esc(d.verdict_reason)}">${verdictBadge(d)}</div>`).join("")}</div></div>
    ${sum.length ? `<div class="cmp-sum">${ic("bulb", 20)}<p>${sum.join(" ")}</p></div>` : ""}
    <div class="cmp-two"><div class="panel"><div class="panel-h"><h3>Radar sovrapposto</h3><span class="panel-s">Le ${radarKeys.length} statistiche più pesanti</span></div>
        ${radar(ds.map((d, i) => ({ stats: d.card.stats, color: colors[i] })), radarKeys, { size: 360 })}
        <ul class="legend inline center">${ds.map((d, i) => `<li><i style="background:${colors[i]}"></i>${esc(d.name)}</li>`).join("")}</ul></div>
      <div class="panel"><div class="panel-h"><h3>Statistiche a confronto</h3><span class="panel-s">La migliore è evidenziata</span></div>
        <div class="sbars">${keys.map(k => statBar(nm(k), ds.map(d => d.card.stats[k] ?? null), colors)).join("")}</div></div></div>
    <div class="panel"><div class="panel-h"><h3>Analisi di ciascuna carta</h3></div><div class="cmp-grid ana-g" ${cols}>${ds.map((d, i) => `<div class="cmp-an" style="--sc:${SERIES[i]}"><b class="cmp-an-h"><i></i>${esc(d.name)}</b><span class="badge b-lvl lv-${esc(d.analysis.meta_level)}">${esc(d.analysis.meta_label)}</span><p class="ana-head sm">${esc(d.analysis.headline)}</p>
        <ul class="mini-l pro">${d.analysis.pros.slice(0, 3).map(x => `<li>${esc(x)}</li>`).join("")}</ul><ul class="mini-l con">${d.analysis.cons.slice(0, 3).map(x => `<li>${esc(x)}</li>`).join("")}</ul>
        <div class="advice sm">${ic("bolt", 16)}<div><p>${esc(d.analysis.advice)}</p></div></div></div>`).join("")}</div></div>`;
  }

  async function draw() {
    const my = ++seq, ids = state.compare;
    const slots = $("#slots", root), out = $("#cmpOut", root);
    slots.innerHTML = Array.from({ length: MAX_COMPARE }, (_, i) => slot(byId(ids[i]), i)).join("");
    $("#clearCmp", root).hidden = !ids.length;
    $("#pickList", root).innerHTML = picker();
    if (ids.length < 2) $("#pickBox", root).open = true;
    if (ids.length < 2) {
      out.innerHTML = `<div class="empty cmp-empty"><svg class="illu" viewBox="0 0 320 200" aria-hidden="true"><defs><linearGradient id="cg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--s1)"/><stop offset="1" stop-color="var(--s2)"/></linearGradient></defs>
        <g fill="none" stroke="var(--line-2)"><polygon points="160,30 232,70 232,140 160,176 88,140 88,70"/><polygon points="160,62 202,86 202,126 160,148 118,126 118,86"/><path d="M160 30v146M88 70l144 70M232 70L88 140"/></g>
        <polygon points="160,48 220,80 214,132 160,166 110,128 104,84" fill="url(#cg)" fill-opacity=".28" stroke="var(--s1)" stroke-width="2.5" stroke-linejoin="round"/>
        <polygon points="160,70 196,92 206,124 160,136 124,118 126,90" fill="var(--s3)" fill-opacity=".2" stroke="var(--s3)" stroke-width="2.5" stroke-linejoin="round"/></svg>
        <h3>${ids.length ? "Aggiungi un'altra carta" : "Scegli le carte da confrontare"}</h3><p>Seleziona da 2 a 3 carte qui sotto, oppure usa il pulsante <b>+</b> sulle carte nella collezione. Vedrai radar sovrapposti, statistiche affiancate e verdetto.</p></div>`;
      return;
    }
    out.innerHTML = `<div class="cmp-loading"><div class="skel" style="height:320px"></div></div>`;
    let ds;
    try { ds = await Promise.all(ids.map(async id => cache.get(id) || (cache.set(id, await jget("/cards/" + id)), cache.get(id)))); }
    catch (e) { if (my === seq) out.innerHTML = `<div class="empty"><h3>Impossibile caricare il confronto</h3><p>${esc(e.message)}</p></div>`; return; }
    if (my !== seq) return;
    out.innerHTML = compareHTML(ds);
    bindCharts(out);
  }

  return {
    mount(el) {
      root = el;
      root.innerHTML = `<section class="cmp-top"><div class="slots" id="slots"></div><button class="link-btn" id="clearCmp" hidden>${ic("x", 14)}Svuota</button></section>
        <details class="pick" id="pickBox"><summary>${ic("plus", 16)}<span>Aggiungi o cambia carte</span><i>${ic("down", 16)}</i></summary>
          <div class="pick-b"><div class="sbox"><span class="sbox-i">${ic("search", 18)}</span><input class="inp" type="search" id="pq" placeholder="Cerca tra le tue carte…" aria-label="Cerca carta da confrontare" autocomplete="off"></div><div class="pick-l" id="pickList"></div></div></details>
        <div id="cmpOut"></div>`;
      draw();
      $("#pq", root).addEventListener("input", debounce(e => { q = e.target.value; $("#pickList", root).innerHTML = picker(); }, 80));
      root.addEventListener("click", e => {
        let b;
        if ((b = e.target.closest("[data-rm]"))) toggleCompare(b.dataset.rm);
        else if ((b = e.target.closest("[data-pick]"))) { if (!toggleCompare(b.dataset.pick)) toast(`Puoi confrontare al massimo ${MAX_COMPARE} carte`, "info"); }
        else if (e.target.closest("#clearCmp")) clearCompare();
      });
      offs.push(on("compare", draw), on("cards", draw));
    },
    unmount() { offs.forEach(f => f()); offs = []; seq++; },
  };
}
