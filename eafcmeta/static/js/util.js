// Utilità condivise: DOM, formattazione, storage, icone SVG inline.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmt = n => Number(n).toLocaleString("it-IT");
export const short = n => n >= 1e6 ? (n / 1e6).toFixed(n % 1e6 ? 1 : 0).replace(".", ",") + "M" : n >= 1e3 ? (n / 1e3).toFixed(n % 1e3 ? 1 : 0).replace(".", ",") + "K" : String(n);
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const finePointer = () => matchMedia("(hover: hover) and (pointer: fine)").matches;

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignora */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignora */ } },
};

export function debounce(fn, ms = 120) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Crea un elemento da una stringa HTML.
export function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

// Conteggio animato (rispetta prefers-reduced-motion).
export function countUp(el, to, { dur = 900, decimals = 0, suffix = "" } = {}) {
  const f = v => Number(v).toLocaleString("it-IT", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
  if (reduced() || !Number.isFinite(to)) { el.textContent = f(to); return; }
  const t0 = performance.now();
  const step = now => {
    const p = clamp((now - t0) / dur, 0, 1), e = 1 - Math.pow(1 - p, 3);
    el.textContent = f(to * e);
    if (p < 1) requestAnimationFrame(step); else el.textContent = f(to);
  };
  requestAnimationFrame(step);
}
export function runCounters(root = document) { $$("[data-count]", root).forEach(el => countUp(el, +el.dataset.count, { decimals: +(el.dataset.dec || 0), suffix: el.dataset.suffix || "" })); }

// Icone (viewBox 24, tratto). Uso: ic("cards", 20)
const P = {
  cards: '<rect x="4" y="5" width="12" height="16" rx="2.5"/><path d="M8 3h9.5A2.5 2.5 0 0 1 20 5.5V17"/>',
  compare: '<path d="M7 4v16M17 4v16"/><path d="M3 9l4-3 4 3M13 15l4 3 4-3"/><circle cx="7" cy="14" r="0.8"/><circle cx="17" cy="10" r="0.8"/>',
  import: '<path d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5"/><path d="M4 16v2.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V16"/>',
  calibrate: '<path d="M4 8h9M17 8h3M4 16h3M11 16h9"/><circle cx="15" cy="8" r="2.2"/><circle cx="9" cy="16" r="2.2"/>',
  rules: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4z"/><path d="M9 9h6M9 13h6"/>',
  research: '<circle cx="11" cy="11" r="6.5"/><path d="M20.5 20.5L16 16"/><path d="M8.3 11.2l1.9 1.9 3.5-3.7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6"/>',
  moon: '<path d="M20 14.2A8 8 0 0 1 9.8 4a8 8 0 1 0 10.2 10.2z"/>',
  auto: '<rect x="3" y="4.5" width="18" height="12" rx="2.5"/><path d="M8.5 20h7M12 16.5V20"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  left: '<path d="M14.5 5.5L8 12l6.5 6.5"/>',
  right: '<path d="M9.5 5.5L16 12l-6.5 6.5"/>',
  up: '<path d="M6 14.5L12 8l6 6.5"/>',
  down: '<path d="M6 9.5l6 6.5 6-6.5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M7 7l10 10M17 7L7 17"/>',
  minus: '<path d="M6 12h12"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.8"/><rect x="13" y="4" width="7" height="7" rx="1.8"/><rect x="4" y="13" width="7" height="7" rx="1.8"/><rect x="13" y="13" width="7" height="7" rx="1.8"/>',
  list: '<path d="M8.5 6.5H20M8.5 12H20M8.5 17.5H20"/><circle cx="4.5" cy="6.5" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="17.5" r="1"/>',
  table: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M3.5 15h17M9.5 10v9.5"/>',
  coin: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v9M14.6 9.6c-.5-.8-1.5-1.2-2.6-1.2-1.5 0-2.6.7-2.6 1.8 0 2.4 5.4 1.2 5.4 3.6 0 1.1-1.2 1.9-2.8 1.9-1.2 0-2.3-.5-2.8-1.4"/>',
  bolt: '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6L13 3z"/>',
  flame: '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.1 0-2.5 1.5-4.2 2.8-5.6 1-1.1 1.7-2.3 1.7-4.3 3 1.4 4.2 3.5 4.4 5.2 1-.8 1.4-1.9 1.4-3 2 1.6 3.2 4.2 3.2 7.1C19 18.4 16 21 12 21z"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0V4zM8 6H4.5c0 3 1.4 4.5 3.7 4.8M16 6h3.5c0 3-1.4 4.5-3.7 4.8M12 13v4M8.5 20h7M10 17h4"/>',
  trend: '<path d="M3.5 17l5.5-5.5 3.5 3.5 7.5-8"/><path d="M15 7h5.5v5.5"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.2M12 7.7v.1"/>',
  trash: '<path d="M4.5 7h15M10 7V4.5h4V7M6.5 7l.8 12.5a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7M10 11v6M14 11v6"/>',
  edit: '<path d="M4 20l1-4.2L16.3 4.5a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8L8.2 19 4 20z"/><path d="M14.5 6.5l3 3"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM18.5 16l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z"/>',
  more: '<circle cx="5.5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="18.5" cy="12" r="1.4"/>',
  upload: '<path d="M12 16V4m0 0L8 8m4-4l4 4"/><path d="M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V15"/>',
  file: '<path d="M6.5 3.5h7L19 9v10.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5V5a1.5 1.5 0 0 1 1.5-1.5z"/><path d="M13.5 3.5V9H19"/>',
  alert: '<path d="M12 4l9 15.5H3L12 4z"/><path d="M12 10v4.2M12 17v.1"/>',
  star: '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9l-5.2 2.8 1-5.9L3.5 9.7l5.9-.8L12 3.5z"/>',
  shield: '<path d="M12 3l7.5 2.7v5.6c0 4.4-3 8-7.5 9.7-4.5-1.7-7.5-5.3-7.5-9.7V5.7L12 3z"/><path d="M8.7 12l2.3 2.3 4.3-4.6"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  lock: '<rect x="5" y="10.5" width="14" height="9.5" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  filter: '<path d="M4 6h16l-6 7.5V19l-4 1.5v-7L4 6z"/>',
  bulb: '<path d="M9.5 18h5M10 21h4M12 3.5a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2h4.2c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3.5z"/>',
  scale: '<path d="M12 4v16M7 20h10M5 8h14M5 8l-2.5 6a3.2 3.2 0 0 0 5 0L5 8zM19 8l-2.5 6a3.2 3.2 0 0 0 5 0L19 8z"/>',
  keyboard: '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M7 10h.1M10.5 10h.1M14 10h.1M17 10h.1M7.5 14h9"/>',
  sync: '<path d="M20 11a8 8 0 0 0-14.300-4.200M4.500 3.500V8H9M4 13a8 8 0 0 0 14.300 4.200M19.500 20.500V16H15"/>',
  play: '<path d="M8 5.500v13l11-6.500-11-6.500z"/>',
  clipboard: '<rect x="5" y="4.500" width="14" height="16.500" rx="2.500"/><path d="M9 4.500V3.500h6v1M9 11h6M9 15h4"/>',
  pitch:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 5v14"/><circle cx="12" cy="12" r="2.6"/>',
};
export const ic = (n, size = 20, cls = "") =>
  `<svg class="ic ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[n] || ""}</svg>`;
