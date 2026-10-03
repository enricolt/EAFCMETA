// Interfaccia di servizio: toast, conferme, tema, finestre.
import { $, $$, ic, esc, store, h } from "./util.js";

/* ---------- toast ---------- */
const TOAST_ICON = { ok: "check", err: "alert", info: "info" };
export function toast(msg, kind = "ok", ms = 3200) {
  let box = $("#toasts");
  if (!box) { box = h('<div id="toasts" class="toasts" aria-live="polite" aria-atomic="false"></div>'); document.body.append(box); }
  const t = h(`<div class="toast t-${kind}" role="${kind === "err" ? "alert" : "status"}"><span class="toast-ic">${ic(TOAST_ICON[kind] || "info", 18)}</span><span class="toast-msg">${esc(msg)}</span><button class="toast-x" aria-label="Chiudi">${ic("close", 16)}</button><i class="toast-bar" style="animation-duration:${ms}ms"></i></div>`);
  const kill = () => { t.classList.add("out"); setTimeout(() => t.remove(), 220); };
  $(".toast-x", t).onclick = kill;
  box.append(t);
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(kill, ms);
}
export const toastErr = m => toast(m, "err", 5200);

/* ---------- conferma / richiesta testo (al posto di confirm/prompt nativi) ---------- */
function modal(html, { cls = "" } = {}) {
  const d = h(`<dialog class="dlg dlg-small ${cls}">${html}</dialog>`);
  document.body.append(d);
  return d;
}
export function confirmBox({ title, text = "", ok = "Conferma", danger = false }) {
  return new Promise(res => {
    const d = modal(`<form method="dialog" class="dlg-in"><div class="dlg-ic ${danger ? "bad" : ""}">${ic(danger ? "trash" : "info", 24)}</div>
      <h2 class="dlg-t">${esc(title)}</h2>${text ? `<p class="dlg-p">${esc(text)}</p>` : ""}
      <div class="dlg-act"><button class="btn" value="no" formnovalidate>Annulla</button><button class="btn ${danger ? "danger" : "pri"}" value="yes" autofocus>${esc(ok)}</button></div></form>`);
    d.addEventListener("close", () => { res(d.returnValue === "yes"); d.remove(); });
    d.addEventListener("cancel", () => { d.returnValue = "no"; });
    d.showModal();
  });
}
export function promptBox({ title, text = "", label = "", type = "password" }) {
  return new Promise(res => {
    const d = modal(`<form method="dialog" class="dlg-in"><div class="dlg-ic">${ic("lock", 24)}</div>
      <h2 class="dlg-t">${esc(title)}</h2>${text ? `<p class="dlg-p">${esc(text)}</p>` : ""}
      <label class="lbl" for="pbIn">${esc(label)}</label><input class="inp" id="pbIn" type="${type}" autocomplete="off" required>
      <div class="dlg-act"><button class="btn" value="no" formnovalidate>Annulla</button><button class="btn pri" value="yes">Continua</button></div></form>`);
    d.addEventListener("close", () => { res(d.returnValue === "yes" ? $("#pbIn", d).value.trim() : null); d.remove(); });
    d.addEventListener("cancel", () => { d.returnValue = "no"; });
    d.showModal();
    $("#pbIn", d).focus();
  });
}

/* ---------- tema Auto / Scuro / Chiaro ---------- */
const THEMES = [["auto", "Auto", "auto"], ["dark", "Scuro", "moon"], ["light", "Chiaro", "sun"]];
export const getTheme = () => store.get("theme") || "auto";
export function applyTheme(t) {
  const r = document.documentElement;
  if (t === "dark" || t === "light") r.dataset.theme = t; else delete r.dataset.theme;
  const dark = t === "dark" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  let m = $('meta[name="theme-color"]');
  if (!m) { m = document.createElement("meta"); m.name = "theme-color"; document.head.append(m); }
  m.content = dark ? "#060913" : "#eceff7";
}
export function setTheme(t) { store.set("theme", t); applyTheme(t); $$(".themesw").forEach(syncSwitch); }
function syncSwitch(sw) { $$("button", sw).forEach(b => b.setAttribute("aria-checked", String(b.dataset.theme === getTheme()))); }
export function themeSwitch(extra = "") {
  const sw = h(`<div class="seg themesw ${extra}" role="radiogroup" aria-label="Tema">${THEMES.map(([k, l, i]) =>
    `<button type="button" role="radio" data-theme="${k}" aria-checked="false" title="Tema: ${l}">${ic(i, 16)}<span>${l}</span></button>`).join("")}</div>`);
  sw.addEventListener("click", e => { const b = e.target.closest("button"); if (b) setTheme(b.dataset.theme); });
  syncSwitch(sw);
  return sw;
}
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme(getTheme()));

/* ---------- finestre ---------- */
// chiusura cliccando sullo sfondo (backdrop) o su [data-close]
export function wireDialogs(root = document) {
  root.addEventListener("click", e => {
    const c = e.target.closest("[data-close]");
    if (c) { c.closest("dialog")?.close(); return; }
    if (e.target.tagName === "DIALOG" && e.target.dataset.nobackdrop == null) {
      const r = e.target.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.target.close();
    }
  });
}
