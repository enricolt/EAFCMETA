// Avvio dell'app: shell (navigazione, barra superiore, vassoio confronto), scorciatoie, caricamento dati.
import { $, $$, esc, ic, h, reduced } from "./util.js";
import { applyTheme, getTheme, themeSwitch, wireDialogs, toast, toastErr } from "./ui.js";
import { state, on, loadMeta, loadCards, toggleCompare, clearCompare, MAX_COMPARE, byId } from "./state.js";
import { SECTIONS } from "./sections.js";
import { startRouter, currentId, currentView, go, find } from "./router.js";
import { initTilt, kind } from "./card.js";
import { initDetail, openDetail } from "./detail.js";
import { openForm } from "./form.js";

applyTheme(getTheme());

const LOGO = `<svg class="logo" viewBox="0 0 40 40" aria-hidden="true"><path d="M20 3l14 4.500v12.500c0 9-6 15.500-14 18C12 35.500 6 29 6 20V7.500z" fill="url(#brandG)"/><path d="M20 3l14 4.500v12.500c0 9-6 15.500-14 18V3z" fill="#fff" opacity=".12"/><path d="M12 22.500l5-5 4 4 7-8.500" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="28" cy="13.500" r="2" fill="#fff"/></svg>`;

function buildShell() {
  const groups = [...new Set(SECTIONS.map(s => s.group))];
  $("#nav").innerHTML = `<a class="brand" href="#/carte" aria-label="EA FC Meta, vai alle carte">${LOGO}<span class="brand-t"><b>EAFC<em>META</em></b><small>Il valore delle carte</small></span></a>
    <nav class="nav-l" aria-label="Sezioni">${groups.map(g => `<div class="nav-g"><span class="nav-gl">${g}</span>${SECTIONS.filter(s => s.group === g).map(s =>
      `<a class="nav-i ${s.soon ? "is-soon" : ""}" href="#/${s.id}" data-nav="${s.id}"><span class="nav-ic">${ic(s.icon, 20)}</span><span class="nav-t">${s.label}</span>${s.soon ? '<em class="nav-b">presto</em>' : s.id === "confronta" ? '<em class="nav-b cnt" id="navCmp" hidden>0</em>' : ""}</a>`).join("")}</div>`).join("")}</nav>
    <div class="nav-f" id="navF"><div class="nav-patch" id="patch"></div><div class="nav-keys">${ic("keyboard", 15)}<span><kbd>/</kbd> cerca · <kbd>N</kbd> nuova · <kbd>←</kbd><kbd>→</kbd> sfoglia</span></div></div>`;
  $("#navF").prepend(themeSwitch("full"));

  $("#top").innerHTML = `<a class="brand brand-sm" href="#/carte" aria-label="EA FC Meta">${LOGO}</a>
    <div class="top-t"><h1 id="pgT"></h1><p id="pgS"></p></div><span class="grow"></span><div id="topTheme"></div>
    <button class="btn pri" id="bNew">${ic("plus", 18)}<span>Nuova carta</span></button>`;
  $("#topTheme").append(themeSwitch("compact"));

  const main = SECTIONS.filter(s => ["carte", "confronta", "importa", "calibra"].includes(s.id));
  $("#tabbar").innerHTML = main.map(s => `<a class="tab-i" href="#/${s.id}" data-nav="${s.id}">${ic(s.icon, 22)}<span>${s.label}</span>${s.id === "confronta" ? '<em class="nav-b cnt" id="tabCmp" hidden>0</em>' : ""}</a>`).join("") +
    `<button class="tab-i" id="bMore" aria-haspopup="dialog">${ic("more", 22)}<span>Altro</span></button>`;

  $("#dMore").innerHTML = `<div class="dlg-in"><header class="dlg-h"><div><small>Menu</small><h2>Altre sezioni</h2></div><button class="icon-btn" data-close aria-label="Chiudi">${ic("close", 18)}</button></header>
    <div class="more-l">${SECTIONS.filter(s => s.soon).map(s => `<a class="more-i" href="#/${s.id}" data-close><span class="nav-ic">${ic(s.icon, 20)}</span><span><b>${s.label}</b><small>${esc(s.sub)}</small></span><em class="nav-b">presto</em></a>`).join("")}</div>
    <div class="more-t"><span class="lbl">Tema</span><div id="moreTheme"></div></div></div>`;
  $("#moreTheme").append(themeSwitch("full"));
  $("#bMore").onclick = () => $("#dMore").showModal();
  $("#bNew").onclick = () => openForm(null);
}

function syncRoute(sec) {
  $$("[data-nav]").forEach(a => { if (a.dataset.nav === sec.id) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  $("#pgT").textContent = sec.title; $("#pgS").textContent = sec.sub;
  document.title = `${sec.title} · EA FC Meta`;
  syncTray();
}

function syncTray() {
  const n = state.compare.length, t = $("#tray");
  ["#navCmp", "#tabCmp"].forEach(s => { const e = $(s); if (e) { e.hidden = !n; e.textContent = n; } });
  document.body.classList.toggle("has-tray", !!n && currentId() !== "confronta");
  if (!n || currentId() === "confronta") { t.hidden = true; return; }
  t.hidden = false;
  t.innerHTML = `<div class="tray-in"><div class="tray-c">${state.compare.map(id => { const c = byId(id); return c ? `<span class="tray-i"><span class="rate sm k-${kind(c)}"><b class="num">${Math.round(c.scores.final_score)}</b></span><span class="tray-n">${esc(c.name)}</span><button class="icon-btn xs" data-cmp="${c.id}" aria-label="Togli ${esc(c.name)}">${ic("close", 14)}</button></span>` : ""; }).join("")}</div>
    <span class="tray-h">${n}/${MAX_COMPARE}</span><button class="btn" id="trayClr">Svuota</button><button class="btn pri" id="trayGo" ${n < 2 ? "disabled" : ""}>${ic("compare", 16)}<span>${n < 2 ? "Scegline un'altra" : "Confronta"}</span></button></div>`;
  $("#trayClr").onclick = clearCompare;
  $("#trayGo").onclick = () => go("confronta");
}

const typing = e => /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;

function wireGlobal() {
  document.addEventListener("click", e => {
    const c = e.target.closest("[data-cmp]");
    if (c) { e.preventDefault(); if (!toggleCompare(c.dataset.cmp)) toast(`Puoi confrontare al massimo ${MAX_COMPARE} carte`, "info"); return; }
    const o = e.target.closest("[data-open]");
    if (o && !e.target.closest("dialog")) openDetail(o.dataset.open);
    else if (o && o.closest("#dDetail")) { /* nessuna azione */ }
  });
  document.addEventListener("keydown", e => {
    if (e.target.matches?.("svg [data-open]") && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDetail(e.target.dataset.open); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    if (e.key === "/" && !typing(e)) {
      e.preventDefault();
      const focus = () => currentView()?.focusSearch?.();
      if (currentId() !== "carte") { go("carte"); setTimeout(focus, 60); } else focus();
    } else if ((e.key === "n" || e.key === "N") && !typing(e)) { e.preventDefault(); openForm(null); }
    else if (e.key === "Escape" && currentId() === "confronta" && state.compare.length && !typing(e)) { /* niente */ }
  });
  on("compare", syncTray);
  on("cards", syncTray);
  on("route", syncRoute);
  on("meta", () => { $("#patch").innerHTML = `<span class="pill">patch ${esc(state.meta.patch_version)}</span>${state.meta.calibrated ? `<span class="pill ok">${ic("shield", 12)}calibrata</span>` : ""}`; });
}

(async function boot() {
  buildShell();
  wireDialogs();
  initTilt();
  initDetail();
  wireGlobal();
  try { await loadMeta(); }
  catch (e) { $("#main").innerHTML = `<div class="empty"><h3>Impossibile avviare</h3><p>${esc(e.message)}</p><button class="btn pri" onclick="location.reload()">Riprova</button></div>`; return; }
  startRouter();
  await loadCards();
  if (state.error) toastErr(state.error);
})();
