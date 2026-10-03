// Vista "Importa": carte (CSV/TSV/JSON), solo prezzi, pagine salvate di FUT.GG/FUTBIN.
import { $, $$, esc, fmt, ic } from "../util.js";
import { api, errText } from "../api.js";
import { loadCards } from "../state.js";
import { toast, toastErr } from "../ui.js";
import { go } from "../router.js";

const HELP = {
  cards: "Incolla CSV (separatore ; , o tab) oppure JSON. Prima riga = intestazioni (name, version, position, price, … + le stat). Se la carta esiste già (nome+versione+posizione) viene aggiornata. Prezzi: 12.500, 12k, 1.2m.",
  pages: "Salva col browser (Ctrl+S → “Pagina web, solo HTML”) la pagina di un giocatore su FUT.GG o FUTBIN, poi scegli qui uno o più file. Leggo statistiche, prezzo (console), ruolo, body type, piede debole, skill e PlayStyle. Se la carta esiste già viene aggiornata. Se scegli una pagina elenco (FUT.GG o FUTBIN) aggiorno i prezzi delle carte che ho già.",
  prices: "Una riga per carta: nome;versione;prezzo (la versione si può omettere se il nome è unico). Cambia solo i prezzi.",
};
const TABS = [["cards", "Carte", "cards"], ["prices", "Solo prezzi", "coin"], ["pages", "Pagine salvate", "file"]];

export default function view() {
  let root, mode = "cards", files = [];

  function setMode(m) {
    mode = m;
    $$("[data-mode]", root).forEach(b => b.setAttribute("aria-selected", String(b.dataset.mode === m)));
    $("#impText", root).hidden = m === "pages"; $("#impFiles", root).hidden = m !== "pages"; $("#bTpl", root).hidden = m !== "cards";
    $("#impHelp", root).textContent = HELP[m];
    $("#impText", root).placeholder = m === "cards" ? "name;version;position;price;…" : "Mbappé;TOTS;185k";
    $("#impRes", root).innerHTML = ""; $("#impRes", root).hidden = true;
  }
  const pill = (n, l, c) => `<span class="rpill ${c}"><b class="num">${n}</b>${l}</span>`;
  function showResult(d, dry, pages) {
    const res = $("#impRes", root), errs = d.errors || [], ls = d.lists || [], cs = d.cards || [];
    const head = errs.length && !pages ? `Ci sono ${errs.length} errori: non è stato salvato nulla.` : dry ? "Anteprima: premi “Importa” per salvare." : "Importazione completata.";
    const items = [
      ...ls.map(l => `<li>${ic("list", 15)}<span><b>${esc(l.file)}</b>: ${l.total} giocatori — ${l.prices_updated} prezzi aggiornati, ${l.unknown.length} non ancora nell'app (servono le loro pagine)${l.unsupported ? `, ${l.unsupported} non supportati (portieri)` : ""}.</span></li>`),
      ...cs.map(c => `<li class="${c.status}">${ic(c.status === "new" ? "plus" : "trend", 15)}<span><b>${esc(c.name)}</b> · ${esc(c.version)} (${esc(c.position)}) — ${fmt(c.price)} crediti</span><em>${c.status === "new" ? "nuova" : "aggiornata"}</em></li>`),
      ...errs.slice(0, 50).map(x => `<li class="err">${ic("alert", 15)}<span>${x.file ? `<b>${esc(x.file)}</b>: ` : x.row ? `Riga ${x.row}: ` : ""}${esc(x.error)}</span></li>`),
    ];
    const pills = mode === "prices" ? pill(d.updated ?? 0, "prezzi da aggiornare", "") : pill(d.new ?? 0, "nuove", "ok") + pill(d.updated ?? 0, "aggiornate", "") + (errs.length ? pill(errs.length, pages ? "pagine non lette" : "errori", "bad") : "");
    res.hidden = false;
    res.innerHTML = `<div class="res-h ${errs.length && !pages ? "bad" : "ok"}">${ic(errs.length && !pages ? "alert" : "check", 18)}<b>${head}</b></div><div class="rpills">${pills}</div>${items.length ? `<ul class="rlist">${items.join("")}</ul>` : ""}`;
  }
  async function run(dry) {
    const btns = $$("#bPrev, #bDo", root); btns.forEach(b => b.disabled = true);
    try {
      let r, pages = mode === "pages";
      if (pages) {
        if (!files.length) return toast("Scegli prima uno o più file", "info");
        const ps = await Promise.all(files.map(async f => ({ name: f.name, html: await f.text() })));
        r = await api("/import/pages", { method: "POST", body: JSON.stringify({ pages: ps, dry_run: dry }) });
      } else r = await api(mode === "cards" ? "/import" : "/prices", { method: "POST", body: JSON.stringify({ text: $("#impText", root).value, dry_run: dry }) });
      if (!r.ok) return toastErr(await errText(r));
      const d = await r.json();
      showResult(d, dry, pages);
      if (d.saved) {
        toast("Importazione completata"); await loadCards();
        if (!(d.errors || []).length) { if (pages) { files = []; paintFiles(); } else $("#impText", root).value = ""; setTimeout(() => go("carte"), 700); }
      }
    } finally { btns.forEach(b => b.disabled = false); }
  }
  function paintFiles() {
    const l = $("#fileList", root);
    l.innerHTML = files.map((f, i) => `<li>${ic("file", 15)}<span>${esc(f.name)}</span><small class="num">${Math.max(1, Math.round(f.size / 1024))} KB</small><button class="icon-btn" data-rmf="${i}" aria-label="Rimuovi ${esc(f.name)}">${ic("close", 14)}</button></li>`).join("");
    $("#dzTxt", root).innerHTML = files.length ? `<b>${files.length} ${files.length === 1 ? "file scelto" : "file scelti"}</b><span>Trascina altri file o clicca per cambiare</span>` : `<b>Trascina qui le pagine salvate</b><span>oppure clicca per scegliere i file .html</span>`;
  }
  const addFiles = fl => { files = [...files, ...[...fl].filter(f => !files.some(x => x.name === f.name && x.size === f.size))]; paintFiles(); };

  return {
    mount(el) {
      root = el;
      root.innerHTML = `<div class="imp"><section class="panel imp-main">
        <div class="tabs" role="tablist" aria-label="Tipo di importazione">${TABS.map(([k, l, i]) => `<button role="tab" data-mode="${k}" id="t${k[0].toUpperCase() + k.slice(1)}" aria-selected="${k === "cards"}">${ic(i, 17)}${l}</button>`).join("")}</div>
        <p class="note" id="impHelp"></p>
        <textarea class="inp mono" id="impText" spellcheck="false" aria-label="Dati da importare" rows="11"></textarea>
        <div id="impFiles" hidden><label class="dz" id="dz" for="impFile"><span class="dz-ic">${ic("upload", 28)}</span><span class="dz-t" id="dzTxt"></span></label>
          <input type="file" id="impFile" class="sr-only" accept=".html,.htm" multiple aria-label="Pagine salvate"><ul class="flist" id="fileList"></ul></div>
        <div id="impRes" class="res" hidden aria-live="polite"></div>
        <div class="row imp-act"><button class="btn" id="bTpl">${ic("download", 16)}Modello CSV</button><span class="grow"></span><button class="btn" id="bPrev">${ic("search", 16)}Anteprima</button><button class="btn pri" id="bDo">${ic("import", 16)}Importa</button></div>
      </section>
      <aside class="panel imp-side"><h3>Come si fa</h3><ol class="steps">
        <li><b>Scegli la fonte</b><span>Incolla una tabella, oppure salva le pagine dei giocatori da FUT.GG o FUTBIN.</span></li>
        <li><b>Guarda l'anteprima</b><span>Vedi cosa viene creato o aggiornato prima di salvare. Se c'è un errore non viene salvato nulla.</span></li>
        <li><b>Importa</b><span>Le carte già presenti si aggiornano e lo storico prezzi si allunga.</span></li></ol>
        <div class="note soft">${ic("shield", 18)}<div>Nessuna connessione ai siti: leggi solo i file che scegli tu, sul tuo computer.</div></div></aside></div>`;
      // l'icona "download" non è nel set: ripiego sulla freccia verso il basso
      $("#bTpl", root).innerHTML = `${ic("down", 16)}Modello CSV`;
      setMode("cards"); paintFiles();
      $$("[data-mode]", root).forEach(b => b.onclick = () => setMode(b.dataset.mode));
      $("#bPrev", root).onclick = () => run(true); $("#bDo", root).onclick = () => run(false);
      $("#bTpl", root).onclick = async () => { const r = await api("/import/template"); $("#impText", root).value = await r.text(); };
      $("#impFile", root).onchange = e => { addFiles(e.target.files); e.target.value = ""; };
      const dz = $("#dz", root);
      ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); }));
      ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); }));
      dz.addEventListener("drop", e => addFiles(e.dataTransfer.files));
      $("#fileList", root).addEventListener("click", e => { const b = e.target.closest("[data-rmf]"); if (b) { files.splice(+b.dataset.rmf, 1); paintFiles(); } });
    },
    unmount() { },
  };
}
