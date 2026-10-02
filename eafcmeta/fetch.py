"""Scarica da solo le pagine dei giocatori da FUT.GG, partendo da una pagina elenco (da eseguire sul TUO PC).

    python -m eafcmeta.fetch "https://www.fut.gg/players/?nation_id=[54]" --max 30

Gentile col sito: rispetta robots.txt, una richiesta ogni ~2.5 secondi, solo carte non ancora nell'app, tetto --max.
Se il sito risponde 403/429 (blocco anti-bot) si ferma e te lo dice: in quel caso salva le pagine dal browser
(Ctrl+S) e importale da “Importa → Pagine salvate”. Non testato contro il sito reale (da qui non è raggiungibile).
"""
import argparse
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

from . import collect, db, sources

UA = "Mozilla/5.0 (compatible; EAFCMETA personal tool; low-rate)"


class Blocked(Exception):
    pass


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "it,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429, 503):
            raise Blocked(f"il sito ha rifiutato la richiesta (HTTP {e.code})") from e
        raise


def allowed(url: str) -> bool:
    u = urlparse(url)
    rp = urllib.robotparser.RobotFileParser(f"{u.scheme}://{u.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:  # noqa: BLE001 - robots irraggiungibile: si procede con prudenza solo se non vieta
        return True
    return rp.can_fetch(UA, url)


def crawl(conn, list_urls: list[str], fetch=http_get, max_cards: int = 30, delay: float = 2.5,
          check_robots: bool = True, log=print) -> dict:
    """Ritorna {'new', 'updated', 'prices_updated', 'errors', 'stopped'}."""
    total = {"new": 0, "updated": 0, "prices_updated": 0, "errors": [], "stopped": None}
    todo: list[str] = []
    try:
        for lu in list_urls:
            if check_robots and not allowed(lu):
                raise Blocked(f"robots.txt non permette {lu}")
            r = collect.import_pages(conn, [(lu, fetch(lu))], dry_run=False)
            total["errors"] += r["errors"]
            for l in r["lists"]:
                total["prices_updated"] += l["prices_updated"]
                todo += [u["url"] for u in l["unknown"]]
            time.sleep(delay)
        todo = list(dict.fromkeys(todo))[:max_cards]
        for i, url in enumerate(todo, 1):
            if check_robots and not allowed(url):
                total["errors"].append({"file": url, "error": "vietato da robots.txt"})
                continue
            r = collect.import_pages(conn, [(url, fetch(url))], dry_run=False)
            total["new"] += r["new"]
            total["updated"] += r["updated"]
            total["errors"] += [{**e, "file": url} for e in r["errors"]]
            log(f"  [{i}/{len(todo)}] {url.rsplit('/players/', 1)[-1]}")
            time.sleep(delay)
    except Blocked as e:
        total["stopped"] = str(e)
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+", help="pagine elenco di FUT.GG")
    ap.add_argument("--max", type=int, default=30, help="massimo di pagine giocatore da scaricare (default 30)")
    ap.add_argument("--delay", type=float, default=2.5, help="secondi tra una richiesta e l'altra")
    a = ap.parse_args()
    r = crawl(db.connect(), a.urls, max_cards=a.max, delay=a.delay)
    print(f"{r['new']} nuove, {r['updated']} aggiornate, {r['prices_updated']} prezzi aggiornati da elenco, "
          f"{len(r['errors'])} errori")
    for e in r["errors"][:10]:
        print(f"  {e['file']}: {e['error']}")
    if r["stopped"]:
        print(f"FERMATO: {r['stopped']}. Salva le pagine dal browser (Ctrl+S) e importale dall'app.")
