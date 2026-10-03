"""Aggiornamento carte e prezzi da FUT.GG riusando eafcmeta/fetch.py (crawl gentile) e collect.import_pages.

Per ogni elenco configurato legge `pages` pagine (aggiorna i prezzi delle carte note e scarica le pagine dei giocatori
mancanti, fino a `max_new_cards` per esecuzione). Una pagina che dà errore non ferma le altre; un blocco del sito
(403/429/503, robots.txt) ferma il passo e lo registra. Rete e robots sono iniettabili (test senza rete).
NON provato contro il sito vero (dal cloud la rete è bloccata): vedi HANDOFF.
"""
import urllib.robotparser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .. import fetch as F


def page_url(u: str, n: int, param: str = "page") -> str:
    """Indirizzo della pagina n (1 = quello dato) di un elenco: aggiunge/sostituisce il parametro `param`."""
    if n == 1:
        return u
    p = urlparse(u)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != param] + [(param, str(n))]
    return urlunparse(p._replace(query=urlencode(q, safe="[]")))


def page_urls(list_urls: list[str], pages: int, param: str = "page") -> list[str]:
    return [page_url(u, n, param) for u in list_urls for n in range(1, pages + 1)]


class Robots:
    """robots.txt letto UNA volta per sito (fetch.allowed lo rilegge a ogni richiesta). Illeggibile => si procede, come fetch.py."""

    def __init__(self):
        self._rp: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def ok(self, url: str) -> bool:
        u = urlparse(url)
        key = f"{u.scheme}://{u.netloc}"
        if key not in self._rp:
            rp = urllib.robotparser.RobotFileParser(f"{key}/robots.txt")
            try:
                rp.read()
            except Exception:  # noqa: BLE001
                rp = None
            self._rp[key] = rp
        rp = self._rp[key]
        return True if rp is None else rp.can_fetch(F.UA, url)


def update(conn, cfg: dict, counters: dict, fetch=None, robots=None, log=print) -> None:
    """Aggiorna `counters` (carte_nuove, prezzi_aggiornati, errori, fermato). Non alza eccezioni per i problemi del sito."""
    fc, lim = cfg["futgg"], cfg["limits"]
    fetch_raw = fetch or F.http_get
    robots_ok = robots if robots is not None else (Robots().ok if fc["check_robots"] else (lambda _u: True))

    def guarded(url: str) -> str:
        if not robots_ok(url):
            raise F.Blocked(f"robots.txt non permette {url}")
        return fetch_raw(url)

    failures = 0
    for url in page_urls(fc["list_urls"], fc["pages"], fc["page_param"]):
        left = max(0, lim["max_new_cards"] - counters["carte_nuove"])
        try:
            r = F.crawl(conn, [url], fetch=guarded, max_cards=left, delay=fc["delay_seconds"], check_robots=False,
                        log=lambda *_a, **_k: None)
        except Exception as e:  # noqa: BLE001 - HTTP 404, rete assente...: questa pagina salta, le altre proseguono
            conn.rollback()
            counters["errori"].append(f"FUT.GG {url}: {type(e).__name__}: {e}"[:250])
            failures += 1
            if failures >= 3:  # rete assente o sito irraggiungibile: non si insiste pagina dopo pagina
                counters["fermato"]["futgg"] = "3 errori di fila: sito irraggiungibile, mi fermo"
                return
            continue
        failures = 0
        counters["carte_nuove"] += r["new"]
        counters["prezzi_aggiornati"] += r["prices_updated"]
        counters["errori"] += [f"FUT.GG {e.get('file', url)}: {e.get('error', '')}"[:250] for e in r["errors"][:5]]
        log(f"[fut.gg] {url}: {r['new']} nuove, {r['prices_updated']} prezzi")
        if r["stopped"]:
            counters["fermato"]["futgg"] = f"{r['stopped']}"
            return
