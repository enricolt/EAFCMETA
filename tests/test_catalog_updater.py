"""Catalogo: aggiornamento a comando con un SITO FINTO (nessuna rete, nessuna attesa), servizio in background, API, config."""
import functools
import json
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from eafcmeta import api, api_catalog, db, fetch as F
from eafcmeta.auto import config as acfg, runner
from eafcmeta.catalog import __main__ as cli, config as catcfg, evaluation, updater
from eafcmeta.models import CardIn
from test_collect import gg_page

API = "/api/v1"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
ST_STATS = None


def short(n: int) -> str:
    return f"{n // 1000}K"


class Site:
    """Elenco FUT.GG ordinato per novità (pagine di `per_page` carte) e pagine giocatore, tutto in memoria."""

    def __init__(self, n_cards: int, per_page: int = 3, price=lambda i: 10_000 + 1000 * i):
        self.cards = [{"id": 100 + i, "name": f"Giocatore{chr(97 + i // 26)}{chr(97 + i % 26)}", "rating": 90 - i % 10, "price": price(i),
                       "released": f"2026-09-{28 - i // 24:02d}T{23 - i % 24:02d}:00:00Z"} for i in range(n_cards)]   # indice 0 = la piu' recente
        self.per_page, self.requests, self.fail_cards, self.on_request = per_page, [], set(), None

    def url(self, c) -> str:
        return f"https://www.fut.gg/players/{c['id']}-{c['name'].lower()}/27-{c['id']}/"

    def list_html(self, n: int) -> str:
        chunk = self.cards[(n - 1) * self.per_page:n * self.per_page]
        if not chunk:
            return "<html><head><title>fut.gg</title></head><body>fine</body></html>"
        a = "".join(f'<a href="{self.url(c)}"><div class="fc-card"><img alt="{c["name"]} - {c["rating"]} - Rare"><span>CM</span>'
                    f'<span>88.0</span><span>{short(c["price"])}</span></div></a>' for c in chunk)
        return f"<html><head><title>EA FC 27 Players - FUT.GG</title></head><body>{a}</body></html>"

    def card_html(self, c) -> str:
        d = datetime.strptime(c["released"], "%Y-%m-%dT%H:%M:%SZ")
        added = d.strftime("%b %d, %Y, %I:%M %p UTC").replace(" 0", " ")
        return gg_page(name=c["name"], rating=c["rating"], rarity="Rare", price=f"{c['price']:,}").replace(
            "</body>", f"<div>Added On</div><div>{added}</div></body>")

    def fetch(self, url: str) -> str:
        self.requests.append(url)
        if self.on_request:
            self.on_request(url)
        u = urlparse(url)
        if u.path == "/players/":
            return self.list_html(int(parse_qs(u.query).get("page", ["1"])[0]))
        for c in self.cards:
            if self.url(c) == url:
                if c["id"] in self.fail_cards:
                    raise RuntimeError("boom")
                return self.card_html(c)
        raise OSError("404")

    def lists(self):
        return [r for r in self.requests if urlparse(r).path == "/players/"]

    def card_pages(self):
        return [r for r in self.requests if urlparse(r).path != "/players/"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EAFCMETA_DB", str(tmp_path / "c.db"))
    monkeypatch.delenv("EAFCMETA_TOKEN", raising=False)
    conn = db.connect()
    yield conn
    conn.close()


def cfg_of(**over):
    c = catcfg.load()
    c.update(setup_confirmed=True, delay_seconds=0, check_robots=False, lookback_days=14)
    lim = over.pop("limits", {})
    c["limits"].update(lim)
    c.update(over)
    return c


def run(mode="new", pages=None, site=None, cfg=None, **kw):
    return updater.run_update(mode=mode, pages=pages, cfg=cfg or cfg_of(), fetch=site.fetch, sleep=lambda s: None, now=NOW, log=lambda *_: None, **kw)


def seed(conn, site, idx, price=1_000, released=True, url=True):
    c = site.cards[idx]
    from test_catalog_api import full_stats
    cid, _ = db.upsert_card(conn, CardIn(name=c["name"], version=f"Gold {c['rating']}", position="CM", price=price, stats=full_stats(),
                                         released_at=c["released"] if released else None, url=site.url(c) if url else None))
    conn.commit()
    return cid


def card_names(conn):
    return {r["name"] for r in conn.execute("SELECT name FROM cards")}


# ---------------------------------------------------------------- new

def test_new_stops_at_known_page_downloads_only_new_and_evaluates(env):
    site = Site(9)
    for i in range(3, 9):
        seed(env, site, i, url=False)      # pagine 2 e 3 gia' note (senza indirizzo: si riconoscono per nome+versione+posizione)
    r = run("new", 5, site)
    s = r["summary"]
    assert r["status"] == "ok" and (s["nuove"], s["aggiornate"]) == (3, 0) and s["pagine_elenco"] == 2
    assert len(site.lists()) == 2 and len(site.card_pages()) == 3     # si ferma alla pagina 2 (solo note): niente pagina 3
    assert card_names(env) == {c["name"] for c in site.cards}
    row = env.execute("SELECT * FROM cards WHERE name=?", (site.cards[0]["name"],)).fetchone()
    assert row["released_at"] == site.cards[0]["released"] and row["rating"] == site.cards[0]["rating"] and row["url"] == site.url(site.cards[0])
    assert s["valutate"] >= 3 and evaluation.stale_count(env, evaluation.config_hash()) == 0
    assert any("sole carte già note" in n for n in s["note"])
    last = updater.last_finished(env)
    assert last["status"] == "ok" and last["mode"] == "new" and last["summary"]["nuove"] == 3 and updater.running_row(env) is None


def test_new_updates_prices_of_known_cards_from_list_and_recent_cards_from_pages(env):
    site = Site(6, price=lambda i: 50_000)
    a, b, c = seed(env, site, 0, 1_000), seed(env, site, 1, 1_000), seed(env, site, 4, 2_000)   # a,b in pagina 1; c in pagina 2
    old = seed(env, site, 5, 3_000)
    env.execute("UPDATE cards SET released_at='2026-01-01T00:00:00Z', first_seen='2026-01-01T00:00:00Z' WHERE id=?", (old,))
    env.commit()
    r = run("new", 1, site)             # una sola pagina: le carte della pagina 2 sono recenti ma non viste
    s = r["summary"]
    assert s["nuove"] == 1 and s["prezzi"] >= 3                       # la 3a carta della pagina 1 e' nuova; a, b dall'elenco; c dalla sua pagina
    price = lambda i: env.execute("SELECT price FROM cards WHERE id=?", (i,)).fetchone()[0]
    assert price(a) == price(b) == price(c) == 50_000 and price(old) == 3_000   # la vecchia (fuori da lookback_days) non si tocca
    assert site.url(site.cards[4]) in site.requests and site.url(site.cards[5]) not in site.requests
    assert [h["price"] for h in db.history(env, a)] == [1_000, 50_000]


def test_new_with_nothing_new_makes_one_request(env):
    site = Site(3)
    for i in range(3):
        seed(env, site, i, price=10_000 + 1000 * i)
    r = run("new", 3, site)
    assert (r["summary"]["nuove"], len(site.requests)) == (0, 1) and r["status"] == "ok"


def test_new_respects_caps(env):
    site = Site(12)
    r = run("new", 4, site, cfg_of(limits={"max_new_cards": 2}))
    assert r["summary"]["nuove"] == 2 and "tetto di carte" in r["summary"]["fermato"] and r["status"] == "ok"
    site2 = Site(12)
    env.execute("DELETE FROM cards"); env.commit()
    r = run("new", 4, site2, cfg_of(limits={"max_requests": 4}))
    assert len(site2.requests) == 4 and "tetto di richieste" in r["summary"]["fermato"]
    site3 = Site(12)
    env.execute("DELETE FROM cards"); env.commit()
    r = run("new", 99, site3, cfg_of(limits={"max_pages": 2}))      # `pages` e' limitato da limits.max_pages
    assert len(site3.lists()) == 2


def test_new_cancel_stops_and_keeps_what_was_done(env):
    site = Site(9)
    flag = {"stop": False}
    site.on_request = lambda url: flag.update(stop=len(site.card_pages()) >= 2)
    r = run("new", 3, site, cancel=lambda: flag["stop"])
    assert r["status"] == "annullato" and r["summary"]["nuove"] == 2 and "annullato" in r["summary"]["fermato"]
    assert len(card_names(env)) == 2 and updater.last_finished(env)["status"] == "annullato"


def test_block_stops_step_and_is_recorded(env):
    site = Site(9)

    def block(url):
        if len(site.card_pages()) >= 2:
            raise F.Blocked("il sito ha rifiutato la richiesta (HTTP 429)")
    site.on_request = block
    r = run("new", 3, site)
    s = r["summary"]
    assert r["status"] == "parziale" and s["nuove"] == 1 and "429" in s["fermato"] and "rifiutato" in s["fermato"]
    # blocco subito: niente di fatto -> errore
    env.execute("DELETE FROM cards"); env.commit()
    site2 = Site(9)
    site2.on_request = lambda url: (_ for _ in ()).throw(F.Blocked("HTTP 403"))
    r2 = run("new", 3, site2)
    assert r2["status"] == "errore" and "403" in r2["summary"]["fermato"] and len(site2.requests) == 1


def test_futbin_block_gives_clear_message_without_workarounds(env):
    site = Site(3)
    site.on_request = lambda url: (_ for _ in ()).throw(F.Blocked("HTTP 403"))
    cfg = cfg_of(list_urls=["https://www.futbin.com/27/players"])
    r = updater.run_update("new", None, cfg=cfg, fetch=site.fetch, sleep=lambda s: None, now=NOW, log=lambda *_: None)
    assert r["status"] == "errore" and "FUTBIN" in r["summary"]["fermato"] and "aggirare" in r["summary"]["fermato"]
    assert len(site.requests) == 1                                  # nessun nuovo tentativo


def test_three_errors_in_a_row_stop_the_step(env):
    site = Site(9)
    site.fail_cards = {c["id"] for c in site.cards}
    r = run("new", 3, site)
    assert r["status"] == "parziale" and "3 errori di fila" in r["summary"]["fermato"] and len(site.card_pages()) == 3
    assert len(r["summary"]["errori"]) >= 3
    # un errore isolato non ferma niente
    env.execute("DELETE FROM cards"); env.commit()
    site2 = Site(6)
    site2.fail_cards = {site2.cards[1]["id"]}
    r2 = run("new", 3, site2)
    assert r2["summary"]["nuove"] == 5 and r2["status"] == "parziale" and not r2["summary"]["fermato"]


def test_needs_setup_when_unconfirmed_or_unrecognized(env):
    # configurazione di fabbrica: indirizzo NON confermato -> non parte, nessuna richiesta
    site = Site(3)
    default = catcfg.load()
    assert default["setup_confirmed"] is False and catcfg.needs_setup(default)
    r = updater.run_update("new", cfg={**default, "delay_seconds": 0}, fetch=site.fetch, sleep=lambda s: None, now=NOW, log=lambda *_: None)
    assert r["status"] == "errore" and r["summary"]["needs_setup"] and r["summary"]["fermato"] == "needs_setup" and not site.requests
    assert "indirizzo" in r["summary"]["messaggio"].lower()
    # confermato ma la prima pagina non ha carte riconoscibili: needs_setup con messaggio chiaro, e torna da confermare
    r2 = updater.run_update("new", cfg=cfg_of(), fetch=lambda u: "<html><head><title>fut.gg</title></head><body>ciao</body></html>",
                            sleep=lambda s: None, now=NOW, log=lambda *_: None)
    assert r2["status"] == "errore" and r2["summary"]["needs_setup"] and "riconoscibili" in r2["summary"]["messaggio"]
    assert catcfg.needs_setup(catcfg.load()) is True
    assert env.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


# ---------------------------------------------------------------- backfill, prices

def test_backfill_goes_backwards_in_tranches_and_does_not_stop_on_known(env):
    site = Site(12)                       # 4 pagine
    seed(env, site, 0)                    # una carta della pagina 1 gia' nota: backfill non si ferma per questo
    r1 = run("backfill", 2, site)
    assert r1["summary"]["nuove"] == 5 and len(site.lists()) == 2
    assert updater._get_state(env, "backfill:https://www.fut.gg/players/") == "3"
    r2 = run("backfill", 2, site)         # riparte dalla pagina 3
    assert r2["summary"]["nuove"] == 6 and any("page=3" in u for u in site.lists()) and len(card_names(env)) == 12
    r3 = run("backfill", 2, site)         # pagina 5: oltre la fine
    assert r3["summary"]["nuove"] == 0 and updater._get_state(env, "backfill:https://www.fut.gg/players/") == "fine"
    n_req = len(site.requests)
    r4 = run("backfill", 2, site)
    assert len(site.requests) == n_req and any("già percorso" in n for n in r4["summary"]["note"])
    updater.reset_backfill(env)
    assert updater._get_state(env, "backfill:https://www.fut.gg/players/") is None


def test_prices_mode_updates_only_prices(env):
    site = Site(6, price=lambda i: 77_000)
    a, b = seed(env, site, 0, 1_000), seed(env, site, 1, 1_000)
    recent = seed(env, site, 4, 1_000)               # recente ma fuori dalla pagina 1
    r = run("prices", 1, site)
    assert r["summary"]["nuove"] == 0 and r["summary"]["prezzi"] == 3 and card_names(env) == {site.cards[i]["name"] for i in (0, 1, 4)}
    assert all(env.execute("SELECT price FROM cards WHERE id=?", (i,)).fetchone()[0] == 77_000 for i in (a, b, recent))
    assert r["summary"]["valutate"] >= 3
    # price_source = list: le carte non viste nell'elenco non si scaricano
    site2 = Site(6, price=lambda i: 88_000)
    r2 = run("prices", 1, site2, cfg_of(price_source="list"))
    assert r2["summary"]["prezzi"] == 2 and site2.url(site2.cards[4]) not in site2.requests


def test_unknown_mode_and_overlap(env):
    with pytest.raises(ValueError):
        updater.run_update("boh")
    rid = updater.begin_run(env, NOW, "new")
    assert rid and updater.begin_run(env, NOW, "new") is None
    assert updater.run_update("new", cfg=cfg_of(), fetch=Site(1).fetch, now=NOW)["status"] == "saltata"
    later = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)
    assert updater.begin_run(env, later, "new")
    assert env.execute("SELECT status FROM catalog_runs WHERE id=?", (rid,)).fetchone()[0] == "interrotto"


# ---------------------------------------------------------------- Fetcher (refactor di fetch.py)

def test_fetcher_hosts_robots_and_error_streak():
    f = F.Fetcher(lambda u: "ok", delay=0, check_robots=False, hosts=catcfg.HOSTS, sleep=lambda s: None)
    assert f.get("https://www.futbin.com/27/players") == "ok" and f.requests == 1
    assert f.get("https://evil.example/x") is None and "non consentito" in f.errors[-1]["error"]
    assert F.validate_url("https://www.futbin.com/x", catcfg.HOSTS)
    with pytest.raises(ValueError):
        F.validate_url("https://www.futbin.com/x")                  # di default solo fut.gg (comportamento di prima)
    g = F.Fetcher(lambda u: (_ for _ in ()).throw(OSError("giu")), delay=0, check_robots=False, max_consecutive_errors=2, sleep=lambda s: None)
    assert g.get("https://www.fut.gg/a") is None
    with pytest.raises(F.TooManyErrors):
        g.get("https://www.fut.gg/b")
    h = F.Fetcher(lambda u: "ok", delay=0, check_robots=True, sleep=lambda s: None, robots_fetch=lambda u, t: "User-agent: *\nDisallow: /players/")
    assert h.get("https://www.fut.gg/players/x") is None and "robots" in h.errors[0]["error"]


# ---------------------------------------------------------------- servizio in background e API

class Gate:
    """fetch che si ferma su un evento: permette di guardare lo stato 'in corso'."""

    def __init__(self, site):
        self.site, self.entered, self.release = site, threading.Event(), threading.Event()

    def __call__(self, url):
        self.entered.set()
        self.release.wait(10)
        return self.site.fetch(url)


@pytest.fixture
def svc(monkeypatch, env):
    s = updater.CatalogService()
    api.app.dependency_overrides[api_catalog.service_dep] = lambda: s
    yield s
    api.app.dependency_overrides.pop(api_catalog.service_dep, None)


def test_api_update_409_status_progress_and_cancel(client, svc, monkeypatch):
    site = Site(9)
    gate = Gate(site)
    real = updater.run_update
    monkeypatch.setattr(updater, "run_update", functools.partial(real, cfg=cfg_of(), fetch=gate, sleep=lambda s: None))
    assert client.get(f"{API}/catalog/status").json()["running"] is False
    r = client.post(f"{API}/catalog/update", json={"mode": "new", "pages": 3})
    assert r.status_code == 202 and r.json() == {"started": True}
    assert gate.entered.wait(10)
    st = client.get(f"{API}/catalog/status").json()
    assert st["running"] is True and st["progress"]["phase"] == "elenco" and st["progress"]["total"] == 3 and st["progress"]["message"]
    assert client.post(f"{API}/catalog/update", json={}).status_code == 409
    assert client.post(f"{API}/catalog/cancel").json() == {"ok": True}
    gate.release.set()
    svc.join()
    st = client.get(f"{API}/catalog/status").json()
    assert st["running"] is False and st["progress"] is None
    lu = st["last_update"]
    assert lu["status"] in ("annullato", "ok", "parziale") and set(lu["summary"]) >= {"nuove", "aggiornate", "prezzi", "valutate", "errori", "fermato"}
    assert set(st) >= {"cards", "evaluated", "newest", "oldest", "running", "progress", "last_update", "config"}
    assert set(st["config"]) == {"list_urls", "pages_per_update", "lookback_days", "needs_setup"}


def test_api_update_runs_to_completion_and_validates_body(client, svc, monkeypatch):
    site = Site(4)
    monkeypatch.setattr(updater, "run_update", functools.partial(updater.run_update, cfg=cfg_of(), fetch=site.fetch, sleep=lambda s: None))
    assert client.post(f"{API}/catalog/update", json={"mode": "boh"}).status_code == 422
    assert client.post(f"{API}/catalog/update", json={"pages": 0}).status_code == 422
    assert client.post(f"{API}/catalog/update", json={"pages": 99}).status_code == 422
    assert client.post(f"{API}/catalog/update", json={"extra": 1}).status_code == 422
    assert client.post(f"{API}/catalog/update").status_code == 202    # corpo facoltativo: default `new`
    svc.join()
    st = client.get(f"{API}/catalog/status").json()
    assert st["cards"] == 4 and st["evaluated"] == 4 and st["last_update"]["status"] == "ok" and st["last_update"]["summary"]["nuove"] == 4
    assert st["newest"] >= st["oldest"] and client.get(f"{API}/catalog").json()["total"] == 4
    assert [i["name"] for i in client.get(f"{API}/catalog").json()["items"]][0] == site.cards[0]["name"]   # piu' recente per prima


def test_api_update_without_setup_reports_needs_setup(client, svc, monkeypatch):
    site = Site(4)
    monkeypatch.setattr(updater, "run_update", functools.partial(updater.run_update, fetch=site.fetch, sleep=lambda s: None))
    assert client.get(f"{API}/catalog/status").json()["config"]["needs_setup"] is True
    assert client.post(f"{API}/catalog/update", json={}).status_code == 202
    svc.join()
    st = client.get(f"{API}/catalog/status").json()
    assert st["last_update"]["status"] == "errore" and st["last_update"]["summary"]["needs_setup"] is True and not site.requests


def test_api_config_put_validation_and_persistence(client, tmp_path):
    put = lambda body: client.put(f"{API}/catalog/config", json=body)
    for bad in ({"list_urls": ["http://www.fut.gg/players/"]}, {"list_urls": ["https://evil.example/players/"]},
                {"list_urls": ["https://user:pw@www.fut.gg/players/"]}, {"list_urls": ["https://www.fut.gg:8443/players/"]},
                {"list_urls": ["https://www.fut.gg/a"] * 1 + [f"https://www.fut.gg/{i}" for i in range(6)]}, {"list_urls": [5]},
                {"pages_per_update": 0}, {"pages_per_update": 21}, {"lookback_days": 0}, {"lookback_days": 366}, {"pages_per_update": 1.5},
                {"altro": 1}):
        assert put(bad).status_code == 422, bad
    assert not (tmp_path / "catalog.local.json").exists()                 # niente salvato se non valido
    r = put({"list_urls": ["https://www.fut.gg/players/?sort=new", "https://www.futbin.com/27/players?sort=Player_Rating"], "pages_per_update": 4, "lookback_days": 7})
    assert r.status_code == 200 and r.json() == {"list_urls": ["https://www.fut.gg/players/?sort=new", "https://www.futbin.com/27/players?sort=Player_Rating"],
                                                 "pages_per_update": 4, "lookback_days": 7, "needs_setup": False}
    saved = json.loads((tmp_path / "catalog.local.json").read_text(encoding="utf-8"))
    assert saved["setup_confirmed"] is True and saved["pages_per_update"] == 4
    assert client.get(f"{API}/catalog/status").json()["config"] == r.json()
    assert put({"lookback_days": 30}).json()["lookback_days"] == 30 and put({}).status_code == 200      # modifiche parziali
    assert put({"list_urls": []}).json()["needs_setup"] is True


# ---------------------------------------------------------------- automazione e CLI

def test_catalog_is_command_only_by_default_and_optional_in_auto(env, monkeypatch):
    base = acfg.load()
    assert base["catalog"]["enabled"] is False
    calls = []
    stub = lambda mode="new": calls.append(mode) or {"id": 1, "status": "ok", "summary": {"nuove": 2, "prezzi": 5, "errori": [], "fermato": None}}
    cfg = {**base, "youtube": {**base["youtube"], "enabled": False}, "futgg": {**base["futgg"], "enabled": False}}
    r = runner.run_once(cfg=cfg, catalog_update=stub, log=lambda *_: None)
    assert calls == [] and "catalogo" not in r["summary"]                       # spento: mai chiamato
    cfg2 = {**cfg, "catalog": {"enabled": True}}
    r2 = runner.run_once(cfg=cfg2, catalog_update=stub, log=lambda *_: None)
    assert calls == ["new"] and r2["summary"]["catalogo"]["nuove"] == 2 and r2["summary"]["carte_nuove"] == 2 and r2["status"] == "ok"
    blocked = lambda mode="new": {"id": 1, "status": "parziale", "summary": {"errori": ["x"], "fermato": "429"}}
    r3 = runner.run_once(cfg=cfg2, catalog_update=blocked, log=lambda *_: None)
    assert r3["summary"]["fermato"]["catalogo"] == "429" and r3["status"] == "parziale"
    bad = lambda mode="new": (_ for _ in ()).throw(RuntimeError("giu"))
    assert runner.run_once(cfg=cfg2, catalog_update=bad, log=lambda *_: None)["status"] == "errore"
    assert acfg.check_value("catalog", "enabled", True) is True


def test_scheduler_never_runs_catalog_by_itself(monkeypatch):
    from eafcmeta.auto import scheduler
    assert not hasattr(scheduler, "updater") and "catalog" not in open(scheduler.__file__, encoding="utf-8").read()


def test_cli_update_and_status(env, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(updater, "run_update", lambda mode="new", pages=None, **kw: seen.update(mode=mode, pages=pages) or
                        {"id": 1, "status": "ok", "summary": {"nuove": 1, "aggiornate": 0, "prezzi": 2, "valutate": 1, "richieste": 3, "errori": [], "note": []}})
    assert cli.main(["update", "--mode", "backfill", "--pages", "2"]) == 0 and seen == {"mode": "backfill", "pages": 2}
    assert "1 nuove" in capsys.readouterr().out
    monkeypatch.setattr(updater, "run_update", lambda **kw: {"id": 1, "status": "errore", "summary": {"messaggio": "da configurare", "fermato": "needs_setup"}})
    assert cli.main(["update"]) == 1 and "da configurare" in capsys.readouterr().out
    assert cli.main(["status"]) == 0 and json.loads(capsys.readouterr().out)["config"]["needs_setup"] is True
    assert cli.main(["recompute"]) == 0
