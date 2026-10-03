// Router a hash (#/carte, #/confronta, …). Le sezioni sono registrate in sections.js:
// per aggiungerne una basta un nuovo file in views/ e una riga nel registro.
import { $, reduced } from "./util.js";
import { SECTIONS } from "./sections.js";
import { emit } from "./state.js";

let cur = null, curId = null;
export const currentId = () => curId;
export const currentView = () => cur;
export const find = id => SECTIONS.find(s => s.id === id);

export function go(id) { if (location.hash !== "#/" + id) location.hash = "#/" + id; else render(); }

export function render() {
  const id = (location.hash.match(/^#\/([a-z]+)/) || [])[1];
  const sec = find(id) || SECTIONS[0];
  if (cur?.unmount) cur.unmount();
  curId = sec.id;
  const el = $("#main");
  el.className = "page p-" + sec.id;
  el.innerHTML = "";
  cur = sec.view();
  cur.mount(el);
  if (!reduced()) { el.classList.remove("page-in"); void el.offsetWidth; el.classList.add("page-in"); }
  window.scrollTo({ top: 0 });
  emit("route", sec);
}
export function startRouter() {
  addEventListener("hashchange", render);
  render();
}
