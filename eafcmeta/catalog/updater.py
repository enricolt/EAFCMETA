"""Aggiornamento del catalogo A COMANDO (mai periodico, salvo che l'automazione lo chiami: auto.json -> catalog.enabled).

Modi:
- `new`      legge gli elenchi «ultime uscite» dalla pagina 1 (la piu' recente), scarica le pagine dei giocatori NON ancora nel
             database e SI FERMA alla prima pagina che contiene solo carte gia' note (o al tetto di pagine/carte/richieste);
             poi aggiorna i prezzi delle carte uscite negli ultimi `lookback_days` e rivaluta (solo le carte toccate).
- `backfill` continua all'indietro: parte dal cursore salvato (`catalog_state`) e legge `pages` pagine scaricando TUTTE le
             carte mancanti (non si ferma sulle note): serve a popolare il database a tranche.
- `prices`   solo prezzi: elenchi (`pages` pagine) e, se price_source = "list+cards", pagine delle carte recenti non viste.

Gentile col sito: fetch.Fetcher (https + host consentiti, robots.txt, pausa tra le richieste, tetti). Un blocco (401/403/429/503) o
`max_consecutive_errors` errori di fila FERMANO il passo e lo registrano; nessun tentativo di aggirare blocchi (FUTBIN: errore chiaro).
Esecuzione in background (CatalogService: un thread, un lock anche nel DB), cancellabile; stato e avanzamento in `catalog_runs`.
Rete, orologio e pause sono iniettabili (i test non usano mai la rete).
NON provato contro il sito vero (dal cloud la rete e' bloccata): l'indirizzo dell'elenco va confermato dall'utente.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

from .. import collect, db, fetch as F, scoring, sources
from ..auto.futgg import page_url
from . import config as catcfg, evaluation

MODES = ("new", "backfill", "prices")
STALE_HOURS = 3  # una riga 'in_corso' piu' vecchia e' un'esecuzione interrotta
NEEDS_SETUP_MSG = ("Indirizzo dell'elenco «ultime uscite» da configurare: incolla nell'app l'indirizzo della pagina elenco "
                   "dei giocatori ordinata per novità (per esempio da FUT.GG).")
UNRECOGNIZED_MSG = ("La prima pagina dell'elenco non contiene carte riconoscibili: controlla l'indirizzo dell'elenco "
                    "(deve essere una pagina elenco giocatori di FUT.GG o FUTBIN, ordinata per novità) e incollalo di nuovo.")


class Cancelled(Exception):
    pass


class StopRun(Exception):
    """Il passo si ferma (blocco, errori di fila, tetto): il motivo va in summary['fermato']."""


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(url: str) -> str:
    return urlparse(url or "").path.rstrip("/").lower()


# ------------------------------------------------------------------ righe catalog_runs (lock nel DB)

def begin_run(conn, now: datetime, mode: str) -> int | None:
    """Registra l'inizio. None se un altro aggiornamento e' in corso (da meno di STALE_HOURS ore)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cutoff = _stamp(now - timedelta(hours=STALE_HOURS))
        conn.execute("UPDATE catalog_runs SET status='interrotto', finished=? WHERE status='in_corso' AND started<?", (_stamp(now), cutoff))
        if conn.execute("SELECT 1 FROM catalog_runs WHERE status='in_corso'").fetchone():
            conn.rollback()
            return None
        cur = conn.execute("INSERT INTO catalog_runs (started, status, mode, summary) VALUES (?, 'in_corso', ?, '{}')", (_stamp(now), mode))
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise


def _run_row(r) -> dict:
    try:
        summary = json.loads(r["summary"])
    except (ValueError, TypeError):
        summary = {}
    return {"id": r["id"], "started": r["started"], "finished": r["finished"], "status": r["status"], "mode": r["mode"], "summary": summary}


def running_row(conn, now: datetime | None = None) -> dict | None:
    cutoff = _stamp((now or datetime.now(timezone.utc)) - timedelta(hours=STALE_HOURS))
    r = conn.execute("SELECT * FROM catalog_runs WHERE status='in_corso' AND started>=? ORDER BY id DESC LIMIT 1", (cutoff,)).fetchone()
    return _run_row(r) if r else None


def last_finished(conn) -> dict | None:
    r = conn.execute("SELECT * FROM catalog_runs WHERE status!='in_corso' ORDER BY id DESC LIMIT 1").fetchone()
    return _run_row(r) if r else None


def _get_state(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM catalog_state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def _set_state(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO catalog_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def reset_backfill(conn) -> None:
    conn.execute("DELETE FROM catalog_state WHERE key LIKE 'backfill:%'")
    conn.commit()


# ------------------------------------------------------------------ contesto di un'esecuzione

class _Known:
    """Carte gia' nel database, per indirizzo (percorso) oppure per nome+versione+posizione (db.find_card, come collect.apply_list)."""

    def __init__(self, conn):
        self.conn = conn
        self.by_path = {_path(r["url"]): r["id"] for r in conn.execute("SELECT id, url FROM cards WHERE url IS NOT NULL AND url != ''")}

    def lookup(self, e: dict) -> int | None:
        cid = self.by_path.get(_path(e["url"]))
        if cid is not None:
            return cid
        row = db.find_card(self.conn, SimpleNamespace(name=e["name"], version=e["version"], position=e["position"]))
        return row["id"] if row else None


class _Ctx:
    def __init__(self, conn, cfg, fetcher, summary, run_id, cancel, log, progress_cb):
        self.conn, self.cfg, self.fetcher, self.summary, self.run_id = conn, cfg, fetcher, summary, run_id
        self.cancel, self.log, self._progress_cb = cancel, log, progress_cb
        self.lim = cfg["limits"]
        self.known = _Known(conn)
        self.seen: set[str] = set()      # pagine giocatore gia' scaricate in questa esecuzione
        self.verified: set[int] = set()  # carte il cui prezzo e' stato letto in questa esecuzione
        self.supported = set(scoring.load_config()["position_to_role"])

    def progress(self, phase: str, done: int, total: int, message: str) -> None:
        self.summary["progress"] = {"phase": phase, "done": done, "total": total, "message": message}
        self._progress_cb()

    def get(self, url: str):
        """HTML o None (errore registrato). Solleva Cancelled / StopRun (blocco, errori di fila, tetto richieste)."""
        if self.cancel():
            raise Cancelled()
        if self.fetcher.requests >= self.lim["max_requests"]:
            raise StopRun(f"tetto di richieste raggiunto ({self.lim['max_requests']})")
        try:
            return self.fetcher.get(url)
        except F.Blocked as e:
            if catcfg.host_of(url).endswith("futbin.com"):
                raise StopRun(f"FUTBIN ha rifiutato la richiesta ({e}): non provo ad aggirare il blocco. Salva le pagine dal browser "
                              "(Ctrl+S) e importale da Strumenti → Importa, oppure usa un elenco di FUT.GG.") from e
            raise StopRun(f"il sito ha rifiutato la richiesta ({e}): mi fermo. Riprova più tardi, oppure salva le pagine dal browser "
                          "(Ctrl+S) e importale da Strumenti → Importa.") from e
        except F.TooManyErrors as e:
            raise StopRun(f"{e} (sito irraggiungibile o indirizzo sbagliato)") from e

    # --- scritture sul database
    def apply_known(self, cid: int, e: dict) -> None:
        self.verified.add(cid)
        row = self.conn.execute("SELECT price FROM cards WHERE id=?", (cid,)).fetchone()
        if e["price"] is not None and row is not None and e["price"] != row["price"]:
            db.set_price(self.conn, cid, e["price"])
            self.summary["prezzi"] += 1
        self.conn.execute("UPDATE cards SET rating=COALESCE(rating, ?), url=CASE WHEN url IS NULL OR url='' THEN ? ELSE url END WHERE id=?",
                          (e.get("rating"), e["url"], cid))
        self.conn.commit()

    def import_card_page(self, url: str, html: str, entry: dict | None = None, count: bool = True) -> dict:
        """Importa una pagina giocatore (collect.import_pages) e conta nuove/aggiornate/errori (count=False: solo per i prezzi, che
        si contano a parte). Ritorna il risultato."""
        r = collect.import_pages(self.conn, [(url, html)], dry_run=False)
        if count:
            self.summary["nuove"] += r["new"]
            self.summary["aggiornate"] += r["updated"]
        for e in r["errors"]:
            self.summary["errori"].append(f"{url}: {e['error']}")
        for c in r["cards"]:
            row = db.find_card(self.conn, SimpleNamespace(name=c["name"], version=c["version"], position=c["position"]))
            if row is not None:
                self.verified.add(row["id"])
                if entry is not None:  # l'indirizzo dell'elenco e' quello che si ritrovera' nelle prossime letture
                    self.conn.execute("UPDATE cards SET url=? WHERE id=?", (entry["url"], row["id"]))
                    self.known.by_path[_path(entry["url"])] = row["id"]
        self.conn.commit()
        return r


# ------------------------------------------------------------------ scansione degli elenchi

def _abs(list_url: str, href: str) -> str:
    return urljoin(list_url, href)


def _scan(ctx: _Ctx, list_url: str, first: int, last: int, download: bool, stop_on_known: bool, mode: str) -> str | None:
    """Legge le pagine first..last di un elenco. Ritorna il motivo dell'arresto: 'note' (pagina di sole carte note), 'fine'
    (oltre l'ultima pagina), 'setup' (prima pagina non riconosciuta), 'tetto' (tetto carte nuove) oppure None (pagine finite)."""
    cfg, s = ctx.cfg, ctx.summary
    for n in range(first, last + 1):
        if ctx.cancel():
            raise Cancelled()
        ctx.progress("elenco", n - first, last - first + 1, f"Leggo la pagina {n} dell'elenco…")
        html = ctx.get(page_url(list_url, n, cfg["page_param"]))
        if html is None:
            continue
        try:
            entries = sources.parse_list(html)
        except sources.PageError:
            if n == 1:
                s["needs_setup"], s["messaggio"] = True, UNRECOGNIZED_MSG
                return "setup"
            return "fine"
        s["pagine_elenco"] += 1
        unknown = []
        for e in entries:
            if e["position"] not in ctx.supported:
                s["non_supportate"] += 1
                continue
            e["url"] = _abs(list_url, e["url"])
            cid = ctx.known.lookup(e)
            if cid is not None:
                ctx.apply_known(cid, e)
            else:
                unknown.append(e)
        if download:
            todo = [e for e in unknown if _path(e["url"]) not in ctx.seen]
            for i, e in enumerate(todo, 1):
                if s["nuove"] + s["aggiornate"] >= ctx.lim["max_new_cards"]:
                    s["fermato"] = f"tetto di carte per aggiornamento raggiunto ({ctx.lim['max_new_cards']})"
                    return "tetto"
                ctx.progress("pagine", i, len(todo), f"Leggo le pagine dei giocatori nuovi ({i}/{len(todo)})…")
                ctx.seen.add(_path(e["url"]))
                page = ctx.get(e["url"])
                if page is not None:
                    ctx.import_card_page(e["url"], page, e)
        if stop_on_known and not unknown:
            return "note"
    return None


def _scan_lists(ctx: _Ctx, urls: list[str], mode: str, pages: int, download: bool, stop_on_known: bool) -> None:
    for u in urls:
        if mode == "backfill":
            key = f"backfill:{u}"
            cur = _get_state(ctx.conn, key)
            if cur == "fine":
                ctx.summary["note"].append(f"{u}: già percorso fino in fondo (nessuna carta più vecchia da cercare)")
                continue
            first = int(cur) if cur and cur.isdigit() else 1
        else:
            first = 1
        why = _scan(ctx, u, first, first + pages - 1, download, stop_on_known, mode)
        if why == "setup":
            return
        if why == "note":
            ctx.summary["note"].append(f"{u}: fermato alla pagina di sole carte già note")
        if mode == "backfill" and why != "tetto":
            _set_state(ctx.conn, f"backfill:{u}", "fine" if why == "fine" else str(first + pages))
        elif mode == "backfill":  # tetto: il cursore resta all'inizio della tranche (le carte note si saltano alla prossima)
            _set_state(ctx.conn, f"backfill:{u}", str(first))
        if why == "tetto":
            return


# ------------------------------------------------------------------ prezzi delle carte recenti

def _prices_from_cards(ctx: _Ctx, now: datetime) -> None:
    cfg, s = ctx.cfg, ctx.summary
    if cfg["price_source"] != "list+cards":
        return
    cutoff = _stamp(now - timedelta(days=cfg["lookback_days"]))
    rows = ctx.conn.execute("SELECT id, url, price FROM cards WHERE COALESCE(released_at, first_seen) >= ? AND url IS NOT NULL AND url != '' "
                            "ORDER BY COALESCE(released_at, first_seen) DESC, id DESC", (cutoff,)).fetchall()
    todo = [r for r in rows if r["id"] not in ctx.verified][:ctx.lim["max_price_pages"]]
    for i, r in enumerate(todo, 1):
        ctx.progress("prezzi", i, len(todo), f"Aggiorno i prezzi delle carte recenti ({i}/{len(todo)})…")
        html = ctx.get(r["url"])
        if html is None:
            continue
        ctx.import_card_page(r["url"], html, count=False)
        if ctx.conn.execute("SELECT price FROM cards WHERE id=?", (r["id"],)).fetchone()["price"] != r["price"]:
            s["prezzi"] += 1


# ------------------------------------------------------------------ esecuzione

def new_summary(mode: str) -> dict:
    return {"modo": mode, "nuove": 0, "aggiornate": 0, "prezzi": 0, "valutate": 0, "errori": [], "fermato": None,
            "pagine_elenco": 0, "richieste": 0, "non_supportate": 0, "needs_setup": False, "messaggio": None, "note": []}


def run_update(mode: str = "new", pages: int | None = None, conn_factory=db.connect, cfg: dict | None = None, fetch=None,
               robots_fetch=None, sleep=time.sleep, clock=time.monotonic, now: datetime | None = None,
               cancel=lambda: False, log=print) -> dict:
    """Esegue un aggiornamento (in modo sincrono) e ritorna {'id', 'status', 'summary'}.
    status: ok | parziale | errore | annullato | saltata (un altro aggiornamento e' gia' in corso)."""
    if mode not in MODES:
        raise ValueError(f"modo sconosciuto: {mode} (validi: {', '.join(MODES)})")
    cfg = cfg or catcfg.load()
    now = now or datetime.now(timezone.utc)
    conn = conn_factory()
    run_id = None
    try:
        run_id = begin_run(conn, now, mode)
        if run_id is None:
            return {"id": None, "status": "saltata", "summary": {"motivo": "un altro aggiornamento del catalogo è già in corso"}}
        summary = new_summary(mode)

        def save(status: str | None = None) -> None:
            body = {k: (v[:15] if k == "errori" else v) for k, v in summary.items() if k != "progress" or status is None}
            if status is None:
                conn.execute("UPDATE catalog_runs SET summary=? WHERE id=?", (json.dumps(body, ensure_ascii=False), run_id))
            else:
                conn.execute("UPDATE catalog_runs SET summary=?, status=?, finished=? WHERE id=?",
                             (json.dumps(body, ensure_ascii=False), status, _stamp(datetime.now(timezone.utc)), run_id))
            conn.commit()

        kw = {} if robots_fetch is None else {"robots_fetch": robots_fetch}
        fetcher = F.Fetcher(fetch or (lambda u: F.http_get(u, catcfg.HOSTS)), cfg["delay_seconds"], cfg["check_robots"], sleep, clock,
                            hosts=catcfg.HOSTS, max_consecutive_errors=cfg["limits"]["max_consecutive_errors"], **kw)
        ctx = _Ctx(conn, cfg, fetcher, summary, run_id, cancel, log, save)
        status = _execute(ctx, mode, pages, now)
        summary["richieste"] = fetcher.requests
        summary["errori"] += [f"{e['file']}: {e['error']}" for e in fetcher.errors]
        summary["errori_totali"] = len(summary["errori"])
        summary.pop("progress", None)
        if status != "annullato":
            status = _final_status(summary, status)
        save(status)
        return {"id": run_id, "status": status, "summary": {**summary, "errori": summary["errori"][:15]}}
    except Exception as e:  # noqa: BLE001 - mai lasciare la riga 'in_corso'
        conn.rollback()
        if run_id is not None:
            conn.execute("UPDATE catalog_runs SET status='errore', finished=?, summary=? WHERE id=?",
                         (_stamp(datetime.now(timezone.utc)), json.dumps({"errori": [f"{type(e).__name__}: {e}"[:300]]}, ensure_ascii=False), run_id))
            conn.commit()
        raise
    finally:
        conn.close()


def _final_status(summary: dict, status: str) -> str:
    if summary["needs_setup"]:
        return "errore"
    done = summary["nuove"] + summary["aggiornate"] + summary["prezzi"] + summary["pagine_elenco"]
    broke = status == "bloccato"
    if (broke or summary["errori"]) and not done:
        return "errore"
    return "parziale" if (broke or summary["errori"]) else "ok"


def _execute(ctx: _Ctx, mode: str, pages: int | None, now: datetime) -> str:
    """Ritorna 'ok', 'bloccato' (blocco/errori di fila/tetto richieste) o 'annullato'. Riempie ctx.summary."""
    cfg, s = ctx.cfg, ctx.summary
    status = "ok"
    pages = max(1, min(pages or cfg["pages_per_update"], ctx.lim["max_pages"]))
    urls = cfg["list_urls"]
    use_lists = bool(urls) and not catcfg.needs_setup(cfg)
    try:
        if mode in ("new", "backfill") and not use_lists:
            s["needs_setup"], s["messaggio"], s["fermato"] = True, NEEDS_SETUP_MSG, "needs_setup"
            return "ok"
        try:
            if use_lists:
                _scan_lists(ctx, urls, mode, pages, download=mode != "prices", stop_on_known=mode == "new")
            if s["needs_setup"]:
                catcfg.save_local({"setup_confirmed": False})  # l'interfaccia riporta l'utente alla configurazione
                s["fermato"] = "needs_setup"
                return "ok"
            if mode in ("new", "prices"):
                _prices_from_cards(ctx, now)
        except StopRun as e:
            s["fermato"] = str(e)
            status = "bloccato" if "tetto" not in str(e) else "ok"
        ctx.progress("valutazione", 0, 1, "Rivaluto le carte aggiornate…")
        s["valutate"] = evaluation.ensure_fresh(ctx.conn)
    except Cancelled:
        s["fermato"] = "annullato dall'utente"
        return "annullato"
    return status


# ------------------------------------------------------------------ servizio in background

class CatalogService:
    """Un solo aggiornamento alla volta (lock in memoria + riga 'in_corso' nel DB, quindi anche tra app e riga di comando)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def running(self) -> bool:
        if self._lock.locked():
            return True
        conn = db.connect()
        try:
            return running_row(conn) is not None
        finally:
            conn.close()

    def start(self, mode: str = "new", pages: int | None = None, runner=None, **deps) -> bool:
        """Avvia in background. False se un aggiornamento e' gia' in corso (l'API risponde 409)."""
        if not self._lock.acquire(blocking=False):
            return False
        if self.running_in_db():
            self._lock.release()
            return False
        self._cancel.clear()
        run = runner or run_update

        def work():
            try:
                run(mode=mode, pages=pages, cancel=self._cancel.is_set, **deps)
            except Exception as e:  # noqa: BLE001 - il thread non deve morire in silenzio
                print(f"[catalogo] aggiornamento fallito: {type(e).__name__}: {e}")
            finally:
                self._lock.release()

        self._thread = threading.Thread(target=work, name="eafcmeta-catalog", daemon=True)
        self._thread.start()
        return True

    @staticmethod
    def running_in_db() -> bool:
        conn = db.connect()
        try:
            return running_row(conn) is not None
        finally:
            conn.close()

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float = 10.0) -> None:
        t = self._thread
        if t:
            t.join(timeout)


service = CatalogService()
