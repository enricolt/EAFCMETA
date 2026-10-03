// Stato condiviso dell'app con un piccolo sistema di eventi.
import { jget } from "./api.js";
import { store } from "./util.js";

const L = new Map();
export const on = (ev, fn) => { (L.get(ev) || L.set(ev, new Set()).get(ev)).add(fn); return () => L.get(ev).delete(fn); };
export const emit = (ev, d) => L.get(ev)?.forEach(fn => fn(d));

export const state = {
  meta: null,
  cards: [],
  loaded: false,
  error: null,
  compare: [],          // id delle carte scelte per il confronto (max 3)
  filters: { q: "", pos: "", verdict: "", metaOnly: false },
  layout: store.get("layout") || "grid",         // grid | list | table
  sort: store.get("sort") || "score",
  tableSort: { key: "score", dir: -1 },
};
export const MAX_COMPARE = 3;

export async function loadMeta() { state.meta = await jget("/meta"); emit("meta"); return state.meta; }
export async function loadCards() {
  try { state.cards = await jget("/cards"); state.error = null; }
  catch (e) { state.error = e.message; state.cards = []; }
  state.loaded = true;
  // toglie dal confronto le carte che non esistono più
  state.compare = state.compare.filter(id => state.cards.some(c => c.id === id));
  emit("cards");
}
export const byId = id => state.cards.find(c => c.id === +id);

export function toggleCompare(id) {
  id = +id;
  const i = state.compare.indexOf(id);
  if (i >= 0) state.compare.splice(i, 1);
  else if (state.compare.length >= MAX_COMPARE) return false;
  else state.compare.push(id);
  emit("compare");
  return true;
}
export function clearCompare() { state.compare = []; emit("compare"); }
export function setLayout(l) { state.layout = l; store.set("layout", l); emit("layout"); }
