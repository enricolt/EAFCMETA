// Finestra "Nuova carta / Modifica carta".
import { $, $$, esc, ic, h } from "./util.js";
import { send, errText } from "./api.js";
import { state, loadCards } from "./state.js";
import { toast } from "./ui.js";

let editing = null, dlg = null;

function build() {
  dlg = h(`<dialog class="dlg dlg-form" id="dForm" aria-labelledby="fTitle"><form id="form" class="dlg-in wide" novalidate>
  <header class="dlg-h"><div><small id="fKicker">Carta</small><h2 id="fTitle">Nuova carta</h2></div><button type="button" class="icon-btn" data-close aria-label="Chiudi">${ic("close", 18)}</button></header>
  <div class="dlg-b">
    <div class="fg2">
      <div class="span2"><label class="lbl" for="fName">Nome</label><input class="inp" type="text" id="fName" required maxlength="64" autocomplete="off"></div>
      <div><label class="lbl" for="fVer">Versione</label><input class="inp" type="text" id="fVer" maxlength="64" placeholder="es. TOTS" autocomplete="off"></div>
      <div><label class="lbl" for="fPos">Posizione</label><select class="inp sel" id="fPos"></select></div>
      <div><label class="lbl" for="fPrice">Prezzo (crediti)</label><input class="inp" type="text" inputmode="numeric" id="fPrice" required placeholder="es. 25.000 o 25k" autocomplete="off"></div>
      <div><label class="lbl" for="fBody">Body type</label><select class="inp sel" id="fBody"></select></div>
      <div><label class="lbl" for="fWF">Piede debole ★</label><input class="inp" type="number" id="fWF" min="1" max="5" value="3" inputmode="numeric"></div>
      <div><label class="lbl" for="fSM">Skill moves ★</label><input class="inp" type="number" id="fSM" min="1" max="5" value="3" inputmode="numeric"></div>
      <div class="span2"><label class="lbl" for="fPS">PlayStyle <span class="lbl-s">(separati da ;)</span></label><input class="inp" type="text" id="fPS" list="psList" placeholder="Rapid+; Finesse Shot+" autocomplete="off"><datalist id="psList"></datalist></div>
      <label class="check span2"><input type="checkbox" id="fSBC"><span class="check-b">${ic("check", 14)}</span>Il prezzo è il costo di una SBC o di un'evoluzione</label>
    </div>
    <h3 class="dlg-sh">Statistiche in-game <small id="fRole"></small></h3>
    <div class="fg3" id="fStats"></div>
    <p class="form-err" id="fErr" role="alert" hidden></p>
  </div>
  <footer class="dlg-f"><button type="button" class="btn" data-close>Annulla</button><button class="btn pri" id="fSave" type="submit">${ic("check", 16)}Salva</button></footer>
  </form></dialog>`);
  document.body.append(dlg);
  $("#fPos").onchange = statInputs;
  $("#form").onsubmit = submit;
  $("#fStats").addEventListener("input", e => { const i = e.target.closest("input"); if (i) paint(i); });
}

function paint(i) { const v = Math.max(0, Math.min(99, +i.value || 0)); i.closest(".sf").style.setProperty("--w", v / 99 * 100 + "%"); }

function statInputs() {
  const pos = $("#fPos").value, w = state.meta.role_weights[pos] || {}, old = {};
  $$("#fStats input").forEach(i => { if (i.value) old[i.dataset.k] = i.value; });
  $("#fStats").innerHTML = Object.keys(w).map(k => `<div class="sf"><label class="lbl" for="s_${k}">${esc(state.meta.stat_names[k] || k.replaceAll("_", " "))}</label><input class="inp" type="number" id="s_${k}" data-k="${k}" min="1" max="99" inputmode="numeric" required value="${old[k] ?? ""}"><i class="sf-bar"></i></div>`).join("");
  $$("#fStats input").forEach(paint);
  $("#fRole").textContent = "solo quelle del ruolo";
}

export function openForm(c) {
  if (!dlg) build();
  const m = state.meta;
  $("#fPos").innerHTML = m.positions.map(p => `<option>${p}</option>`).join("");
  $("#fBody").innerHTML = m.body_types.map(p => `<option>${p}</option>`).join("");
  $("#psList").innerHTML = m.playstyles.map(p => `<option value="${esc(p)}">`).join("");
  editing = c ? c.id : null;
  $("#fTitle").textContent = c ? "Modifica carta" : "Nuova carta";
  $("#fKicker").textContent = c ? c.name : "Aggiungi alla collezione";
  $("#fErr").hidden = true;
  const cd = c ? c.card : {};
  $("#fName").value = c?.name || ""; $("#fVer").value = c?.version || ""; $("#fPos").value = c?.position || m.positions[0];
  $("#fPrice").value = c ? c.cost_credits : ""; $("#fBody").value = cd.body_type || "Average"; $("#fWF").value = cd.weak_foot || 3; $("#fSM").value = cd.skill_moves || 3;
  $("#fPS").value = (cd.playstyles || []).join("; "); $("#fSBC").checked = !!c?.is_sbc;
  $("#fStats").innerHTML = ""; statInputs();
  if (c) for (const [k, v] of Object.entries(cd.stats || {})) { const i = $("#s_" + k); if (i) { i.value = v; paint(i); } }
  dlg.showModal();
  setTimeout(() => $("#fName").focus(), 30);
}

export function parsePrice(v) {
  let s = v.trim().toLowerCase().replace(/\s/g, ""), m = 1;
  if (s.endsWith("k")) { m = 1e3; s = s.slice(0, -1); } else if (s.endsWith("m")) { m = 1e6; s = s.slice(0, -1); }
  if (m > 1) return Math.round(parseFloat(s.replace(",", ".")) * m);
  return parseInt(s.replace(/[.,]/g, ""), 10);
}
function showErr(t) { const e = $("#fErr"); e.textContent = t; e.hidden = false; }

async function submit(e) {
  e.preventDefault();
  if (!$("#fName").value.trim()) { $("#fName").focus(); return showErr("Scrivi il nome del giocatore"); }
  const price = parsePrice($("#fPrice").value);
  if (!Number.isFinite(price)) { $("#fPrice").focus(); return showErr("Prezzo non valido"); }
  const stats = {}; let miss = null;
  $$("#fStats input").forEach(i => { if (i.value) stats[i.dataset.k] = +i.value; else if (!miss) miss = i; });
  if (miss) { miss.focus(); return showErr("Completa tutte le statistiche del ruolo"); }
  const body = { name: $("#fName").value, version: $("#fVer").value, position: $("#fPos").value, price, is_sbc: $("#fSBC").checked, stats,
    playstyles: $("#fPS").value.split(";").map(s => s.trim()).filter(Boolean), body_type: $("#fBody").value, weak_foot: +$("#fWF").value, skill_moves: +$("#fSM").value };
  const btn = $("#fSave"); btn.disabled = true;
  const r = await send(editing ? "/cards/" + editing : "/cards", editing ? "PUT" : "POST", body);
  btn.disabled = false;
  if (!r.ok) return showErr(await errText(r));
  const d = await r.json();
  toast(d.status === "updated" ? "Carta già presente: aggiornata" : "Carta salvata");
  dlg.close(); loadCards();
}
