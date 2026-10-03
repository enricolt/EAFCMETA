"""Scarica da solo le pagine dei giocatori da FUT.GG, partendo da una pagina elenco (da eseguire sul TUO PC).

    python -m eafcmeta.fetch "https://www.fut.gg/players/?nation_id=[54]" --max 30

Gentile col sito: rispetta robots.txt (letto una volta), una richiesta ogni ~2.5 secondi (robots incluso), solo carte non
ancora nell'app, tetto --max. Solo https://www.fut.gg, risposte fino a 5 MB, errori per pagina senza fermare il crawl.
Se il sito risponde 403/429 (blocco anti-bot) si ferma e te lo dice: in quel caso salva le pagine dal browser
(Ctrl+S) e importale da “Importa → Pagine salvate”. Non testato contro il sito reale (da qui non è raggiungibile).
"""
import argparse
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urljoin, urlparse

from . import collect, db, sources

UA = "Mozilla/5.0 (compatible; EAFCMETA personal tool; low-rate)"
ALLOWED_HOSTS = ("fut.gg", "www.fut.gg")  # niente altro: nessun SSRF, nessun file:// o http://
MAX_BYTES = 5 * 1024 * 1024  # al massimo 5 MB per risposta
TIMEOUT = 30
ROBOTS_TIMEOUT = 10


class Blocked(Exception):
    pass


class PageTooBig(Exception):
    pass


def validate_url(url: str) -> str:
    """Solo https verso fut.gg / www.fut.gg (porta 443, niente credenziali nell'URL). ValueError se non consentito."""
    try:
        u = urlparse(url)
        host, port = (u.hostname or "").lower(), u.port
    except ValueError as e:
        raise ValueError(f"indirizzo non consentito: {url!r}") from e
    if u.scheme.lower() != "https" or host not in ALLOWED_HOSTS or port not in (None, 443) or u.username or u.password:
        raise ValueError(f"indirizzo non consentito (solo https://www.fut.gg): {url[:120]}")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Anche i reindirizzamenti devono restare su https://fut.gg."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_SafeRedirect)


def http_get(url: str) -> str:
    validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "it,en;q=0.8"})
    try:
        with _opener().open(req, timeout=TIMEOUT) as r:
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429, 503):
            raise Blocked(f"il sito ha rifiutato la richiesta (HTTP {e.code})") from e
        raise
    if len(data) > MAX_BYTES:
        raise PageTooBig(f"risposta troppo grande (oltre {MAX_BYTES // (1024 * 1024)} MB)")
    return data.decode("utf-8", errors="replace")


def _robots_fetch(url: str, timeout: float) -> str:
    with _opener().open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read(MAX_BYTES).decode("utf-8", errors="replace")


def allowed(url: str, cache: dict | None = None, robots_fetch=_robots_fetch) -> bool:
    """robots.txt letto UNA volta per host (con timeout) e tenuto in `cache`. Irraggiungibile: si procede."""
    u = urlparse(url)
    host = f"{u.scheme}://{u.netloc}"
    cache = {} if cache is None else cache
    if host not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(robots_fetch(f"{host}/robots.txt", ROBOTS_TIMEOUT).splitlines())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:  # noqa: BLE001 - robots irraggiungibile o lento: si procede con prudenza
            rp.allow_all = True
        cache[host] = rp
    return cache[host].can_fetch(UA, url)


class _Pacer:
    """Fa passare almeno `delay` secondi tra una richiesta e la successiva (robots.txt incluso), mai di piu'."""

    def __init__(self, delay: float, sleep, clock):
        self.delay, self.sleep, self.clock, self.last = delay, sleep, clock, None

    def wait(self) -> None:
        if self.last is not None:
            need = self.delay - (self.clock() - self.last)
            if need > 0:
                self.sleep(need)

    def done(self) -> None:
        self.last = self.clock()


def crawl(conn, list_urls: list[str], fetch=http_get, max_cards: int = 30, delay: float = 2.5,
          check_robots: bool = True, log=print, sleep=time.sleep, clock=time.monotonic, robots_fetch=_robots_fetch) -> dict:
    """Ritorna {'new', 'updated', 'prices_updated', 'errors', 'stopped'}. Un errore su una singola pagina (404, 500,
    timeout, rete, troppo grande, indirizzo non consentito) e' registrato e il crawl continua; solo Blocked lo ferma."""
    total = {"new": 0, "updated": 0, "prices_updated": 0, "errors": [], "stopped": None}
    pacer, robots = _Pacer(delay, sleep, clock), {}

    def paced_robots(url: str, timeout: float) -> str:
        pacer.wait()
        try:
            return robots_fetch(url, timeout)
        finally:
            pacer.done()

    def get(url: str):
        """HTML della pagina oppure None (errore gia' registrato). Solleva Blocked."""
        try:
            validate_url(url)
            if check_robots:
                if not allowed(url, robots, paced_robots):  # pacer solo se robots.txt viene davvero scaricato
                    total["errors"].append({"file": url, "error": "vietato da robots.txt"})
                    return None
            pacer.wait()
            try:
                return fetch(url)
            finally:
                pacer.done()
        except Blocked:
            raise
        except ValueError as e:  # indirizzo non consentito
            total["errors"].append({"file": url, "error": str(e)})
        except Exception as e:  # noqa: BLE001 - 404/500/timeout/rete/troppo grande: solo questa pagina
            total["errors"].append({"file": url, "error": f"pagina non scaricata ({type(e).__name__}: {e})"})
        return None

    todo: list[str] = []
    try:
        for lu in list_urls:
            html = get(lu)
            if html is None:
                continue
            r = collect.import_pages(conn, [(lu, html)], dry_run=False)
            total["errors"] += r["errors"]
            for l in r["lists"]:
                total["prices_updated"] += l["prices_updated"]
                todo += [urljoin(lu, u["url"]) for u in l["unknown"]]  # href relativi: rispetto alla pagina elenco
        todo = list(dict.fromkeys(todo))[:max_cards]
        for i, url in enumerate(todo, 1):
            html = get(url)
            if html is None:
                continue
            r = collect.import_pages(conn, [(url, html)], dry_run=False)
            total["new"] += r["new"]
            total["updated"] += r["updated"]
            total["errors"] += [{**e, "file": url} for e in r["errors"]]
            log(f"  [{i}/{len(todo)}] {url.rsplit('/players/', 1)[-1]}")
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
