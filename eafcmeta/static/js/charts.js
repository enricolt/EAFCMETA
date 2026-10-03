// Grafici SVG inline senza librerie: radar, area, dispersione, istogramma, anello.
import { esc, short, fmt, clamp, $$ } from "./util.js";
import { lbl, vkey, kind } from "./card.js";

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)"];
let uid = 0;

export function fmtTs(ts) {
  const m = String(ts).match(/^(\d{4})-(\d\d)-(\d\d)(?:[ T](\d\d):(\d\d))?/);
  if (!m) return String(ts);
  const mesi = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];
  return `${+m[3]} ${mesi[+m[2] - 1]}${m[4] ? " " + m[4] + ":" + m[5] : ""}`;
}

/* ---------- radar (1-3 serie sovrapposte) ---------- */
export function radar(series, keys, { names = k => lbl(k), size = 300 } = {}) {
  const n = keys.length;
  if (n < 3) return "";
  const C = 150, CY = 142, R = 98;
  const pt = (i, r) => [C + r * Math.sin(2 * Math.PI * i / n), CY - r * Math.cos(2 * Math.PI * i / n)];
  const ring = f => keys.map((_, i) => pt(i, R * f).map(v => v.toFixed(1)).join(",")).join(" ");
  const g = [.25, .5, .75, 1].map(f => `<polygon points="${ring(f)}" fill="none" stroke="var(--line-2)" stroke-width="${f === 1 ? 1.2 : .8}" ${f < 1 ? 'stroke-dasharray="2 3"' : ""}/>`).join("");
  const ax = keys.map((_, i) => { const [x, y] = pt(i, R); return `<line x1="${C}" y1="${CY}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--line)"/>`; }).join("");
  const ser = series.map((s, si) => {
    const pts = keys.map((k, i) => pt(i, R * clamp((s.stats[k] ?? 0) / 100, .04, 1)));
    const poly = pts.map(p => p.map(v => v.toFixed(1)).join(",")).join(" ");
    return `<g class="rd-s" style="--d:${si * 90}ms"><polygon points="${poly}" fill="${s.color}" fill-opacity="${series.length > 1 ? .16 : .26}" stroke="${s.color}" stroke-width="2.4" stroke-linejoin="round"/>
      ${pts.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3.2" fill="var(--surface)" stroke="${s.color}" stroke-width="2"/>`).join("")}</g>`;
  }).join("");
  const labs = keys.map((k, i) => {
    const [x, y] = pt(i, R + 17), a = Math.abs(x - C) < 6 ? "middle" : x > C ? "start" : "end";
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" font-size="11" font-weight="700" fill="var(--mut)" text-anchor="${a}" dominant-baseline="middle">${esc(names(k))}</text>`;
  }).join("");
  return `<svg class="radar" viewBox="0 0 300 285" style="max-width:${size}px" role="img" aria-label="Grafico radar delle statistiche">${g}${ax}${ser}${labs}</svg>`;
}

/* ---------- barre delle statistiche ---------- */
export function statBar(label, vals, colors = [null]) {
  const best = Math.max(...vals.filter(v => v != null));
  return `<div class="sbar"><span class="sbar-l">${esc(label)}</span><div class="sbar-t">${vals.map((v, i) =>
    `<div class="sbar-r${v != null && v === best && vals.length > 1 ? " best" : ""}"><i><u style="--w:${clamp((v ?? 0), 0, 99) / 99 * 100}%;${colors[i] ? `background:${colors[i]}` : ""}"></u></i><b class="num">${v ?? "–"}</b></div>`).join("")}</div></div>`;
}

/* ---------- grafico ad area (storico prezzo) ---------- */
export function areaChart(hist) {
  const id = "ag" + (++uid), W = 640, H = 230, L = 52, R = 14, T = 14, B = 30;
  const p = hist.map(x => x.price), mn = Math.min(...p), mx = Math.max(...p);
  const lo = mn === mx ? mn * .9 : mn - (mx - mn) * .12, hi = mn === mx ? mx * 1.1 : mx + (mx - mn) * .12;
  const X = i => L + (hist.length === 1 ? 0 : i * (W - L - R) / (hist.length - 1));
  const Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const pts = hist.map((x, i) => [X(i), Y(x.price)]);
  const line = pts.map(q => q[0].toFixed(1) + "," + q[1].toFixed(1)).join(" ");
  const ticks = [0, .5, 1].map(f => { const v = lo + (hi - lo) * f; return `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="${L - 8}" y="${Y(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="11" fill="var(--faint)">${short(Math.round(v))}</text>`; }).join("");
  const last = pts.at(-1);
  return `<div class="chart" data-pts='${JSON.stringify(hist.map((x, i) => ({ x: pts[i][0] / W, y: pts[i][1] / H, p: x.price, t: fmtTs(x.ts) })))}'>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Andamento del prezzo: da ${fmt(p[0])} a ${fmt(p.at(-1))} crediti">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--acc)" stop-opacity=".38"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>
      ${ticks}
      <polygon fill="url(#${id})" class="ar-fill" points="${L},${H - B} ${line} ${W - R},${H - B}"/>
      <polyline class="ar-line" pathLength="1" fill="none" stroke="var(--acc)" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round" points="${line}"/>
      <circle class="ar-dot" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="5" fill="var(--surface)" stroke="var(--acc)" stroke-width="2.6"/>
      <g class="ar-hov" hidden><line y1="${T}" y2="${H - B}" stroke="var(--acc-line)"/><circle r="5.5" fill="var(--acc)" stroke="var(--surface)" stroke-width="2"/></g>
      <text x="${L}" y="${H - 8}" font-size="11" fill="var(--faint)">${esc(fmtTs(hist[0].ts))}</text>
      <text x="${W - R}" y="${H - 8}" font-size="11" fill="var(--faint)" text-anchor="end">${esc(fmtTs(hist.at(-1).ts))}</text>
    </svg><div class="ctip" hidden></div></div>`;
}

/* ---------- mappa valore: score vs prezzo (scala log) ---------- */
export function scatter(cards) {
  const W = 560, H = 270, L = 40, R = 14, T = 12, B = 30;
  const pr = cards.map(c => Math.max(1, c.cost_credits)), sc = cards.map(c => c.scores.final_score);
  const lx0 = Math.log10(Math.min(...pr)), lx1 = Math.log10(Math.max(...pr));
  const x0 = Math.floor(lx0 * 2) / 2, x1 = Math.max(Math.ceil(lx1 * 2) / 2, x0 + .5);
  const y0 = Math.floor((Math.min(...sc) - 2) / 5) * 5, y1 = Math.min(100, Math.ceil((Math.max(...sc) + 2) / 5) * 5);
  const X = v => L + (Math.log10(Math.max(1, v)) - x0) / (x1 - x0) * (W - L - R);
  const Y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const yt = []; for (let v = y0; v <= y1; v += 5) yt.push(v);
  const xt = []; for (let e = Math.ceil(x0); e <= x1; e++) xt.push(Math.pow(10, e));
  const grid = yt.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="${L - 8}" y="${Y(v)}" text-anchor="end" dominant-baseline="middle" font-size="11" fill="var(--faint)">${v}</text>`).join("") +
    xt.map(v => `<line y1="${T}" y2="${H - B}" x1="${X(v)}" x2="${X(v)}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="${X(v)}" y="${H - 10}" text-anchor="middle" font-size="11" fill="var(--faint)">${short(v)}</text>`).join("");
  const dots = [...cards].sort((a, b) => b.cost_credits - a.cost_credits).map((c, i) => {
    const k = vkey(c), col = { MUST_DO: "ok", NEUTRAL: "warn", AVOID: "bad", ND: "nd" }[k];
    return `<circle class="sc-dot" data-open="${c.id}" tabindex="0" role="button" aria-label="${esc(c.name)}: score ${Math.round(c.scores.final_score)}, ${fmt(c.cost_credits)} crediti" cx="${X(c.cost_credits).toFixed(1)}" cy="${Y(c.scores.final_score).toFixed(1)}" r="${k === "MUST_DO" ? 7 : 5.5}" fill="var(--${col}-solid)" fill-opacity=".82" stroke="var(--surface)" stroke-width="1.8" style="--d:${Math.min(i * 14, 500)}ms"
      data-tip="${esc(c.name)}|${Math.round(c.scores.final_score)} pt · ${fmt(c.cost_credits)} cr|${(x => (X(x.cost_credits) / W).toFixed(4) + "," + (Y(x.scores.final_score) / H).toFixed(4))(c)}"/>`;
  }).join("");
  return `<div class="chart sc"><svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Mappa valore: score in funzione del prezzo">${grid}
    <text x="${W - R}" y="${T + 2}" font-size="10.5" font-weight="700" fill="var(--faint)" text-anchor="end" dominant-baseline="hanging" opacity=".0">.</text>${dots}</svg><div class="ctip" hidden></div></div>`;
}

/* ---------- istogramma degli score ---------- */
export function histogram(cards) {
  const lo = 70, hi = 100, step = 5, bins = [];
  for (let a = lo; a < hi; a += step) bins.push({ a, n: 0 });
  cards.forEach(c => { const s = c.scores.final_score; const i = clamp(Math.floor((s - lo) / step), 0, bins.length - 1); bins[i].n++; });
  const mx = Math.max(1, ...bins.map(b => b.n));
  return `<div class="hist" role="img" aria-label="Distribuzione degli score">${bins.map((b, i) => {
    const t = b.a >= 90 ? "special" : b.a >= 85 ? "gold" : b.a >= 75 ? "silver" : "bronze";
    return `<div class="hist-c"><span class="hist-n num">${b.n || ""}</span><i class="hist-b h-${t}" style="--h:${Math.max(b.n ? 6 : 2, b.n / mx * 100)}%;--d:${i * 50}ms"></i><span class="hist-l num">${b.a}${i === bins.length - 1 ? "+" : ""}</span></div>`;
  }).join("")}</div>`;
}

/* ---------- barra a segmenti (verdetti) ---------- */
export function stackBar(parts) {
  const tot = parts.reduce((s, p) => s + p.n, 0) || 1;
  return `<div class="stack" role="img" aria-label="${parts.map(p => p.n + " " + p.label).join(", ")}">${parts.filter(p => p.n).map(p => `<i style="flex:${p.n};background:var(--${p.cls}-solid)" title="${p.label}: ${p.n}"></i>`).join("")}</div>
    <ul class="legend">${parts.map(p => `<li><i style="background:var(--${p.cls}-solid)"></i>${p.label}<b class="num">${p.n}</b><span class="num">${Math.round(p.n / tot * 100)}%</span></li>`).join("")}</ul>`;
}

/* ---------- anello (punteggio / avanzamento) ---------- */
export function ring(value, max = 100, { size = 92, label = "", sub = "", color = "var(--acc)" } = {}) {
  const r = 38, c = 2 * Math.PI * r, f = clamp(value / max, 0, 1);
  return `<div class="ringw" style="width:${size}px;height:${size}px"><svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="9"/>
    <circle class="ring-v" cx="50" cy="50" r="${r}" fill="none" stroke="${color}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${(c * f).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 50 50)" style="--c:${c.toFixed(1)}"/></svg>
    <div class="ring-t"><b class="num">${label}</b>${sub ? `<small>${sub}</small>` : ""}</div></div>`;
}

/* ---------- tooltip interattivi per area e dispersione ---------- */
export function bindCharts(root = document) {
  $$(".chart", root).forEach(ch => {
    if (ch.dataset.bound) return; ch.dataset.bound = "1";
    const tip = ch.querySelector(".ctip"), svg = ch.querySelector("svg");
    const place = (fx, fy, html) => {
      tip.innerHTML = html; tip.hidden = false;
      tip.style.left = clamp(fx * 100, 12, 88) + "%"; tip.style.top = (fy * 100) + "%";
    };
    if (ch.dataset.pts) {
      const pts = JSON.parse(ch.dataset.pts), hov = ch.querySelector(".ar-hov");
      const mv = e => {
        const r = svg.getBoundingClientRect(), fx = (e.clientX - r.left) / r.width;
        let b = 0; pts.forEach((p, i) => { if (Math.abs(p.x - fx) < Math.abs(pts[b].x - fx)) b = i; });
        const p = pts[b], W = svg.viewBox.baseVal.width, H = svg.viewBox.baseVal.height;
        hov.hidden = false;
        hov.querySelector("line").setAttribute("x1", p.x * W); hov.querySelector("line").setAttribute("x2", p.x * W);
        hov.querySelector("circle").setAttribute("cx", p.x * W); hov.querySelector("circle").setAttribute("cy", p.y * H);
        place(p.x, p.y - .08, `<b class="num">${fmt(p.p)}</b><span>${esc(p.t)}</span>`);
      };
      svg.addEventListener("pointermove", mv); svg.addEventListener("pointerdown", mv);
      svg.addEventListener("pointerleave", () => { hov.hidden = true; tip.hidden = true; });
    } else {
      ch.addEventListener("pointerover", e => {
        const d = e.target.closest("[data-tip]"); if (!d) return;
        const [n, v, pos] = d.dataset.tip.split("|"), [x, y] = pos.split(",").map(Number);
        place(x, y - .1, `<b>${n}</b><span class="num">${v}</span>`);
      });
      ch.addEventListener("focusin", e => { const d = e.target.closest("[data-tip]"); if (d) { const [n, v, pos] = d.dataset.tip.split("|"), [x, y] = pos.split(",").map(Number); place(x, y - .1, `<b>${n}</b><span class="num">${v}</span>`); } });
      ch.addEventListener("pointerleave", () => { tip.hidden = true; });
      ch.addEventListener("focusout", () => { tip.hidden = true; });
    }
  });
}
