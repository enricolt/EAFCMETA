"""Revisione critica, area D: fetch.py (URL, SSRF, dimensioni, errori per pagina, robots, ritmo). Nessuna rete."""
import io
import urllib.error

import pytest

from eafcmeta import db, fetch
from test_collect import gg_page, list_page

LIST = "https://www.fut.gg/players/?page=1"


def _crawl(conn, pages, **kw):
    seen = []

    def fake(u):
        seen.append(u)
        v = pages[u]
        if isinstance(v, Exception):
            raise v
        return v

    kw.setdefault("delay", 0)
    kw.setdefault("check_robots", False)
    kw.setdefault("log", lambda *_: None)
    return fetch.crawl(conn, [LIST], fetch=fake, **kw), seen


def _relative_list():
    html = list_page([("Pelé", 95, "Base Icon", "CM", "6.8M"), ("Zico", 91, "Base Icon", "CM", "4.9M")])
    return html.replace("https://www.fut.gg/players/", "/players/")  # href relativi


def test_relative_hrefs_are_resolved_against_the_list_url(tmp_path):
    pages = {LIST: _relative_list(),
             "https://www.fut.gg/players/1-pelé/27-1/": gg_page(name="Pelé", rating=95, rarity="Base Icon"),
             "https://www.fut.gg/players/2-zico/27-2/": gg_page(name="Zico", rating=91, rarity="Base Icon")}
    r, seen = _crawl(db.connect(str(tmp_path / "a.db")), pages)
    assert r["new"] == 2 and r["stopped"] is None
    assert all(u.startswith("https://www.fut.gg/") for u in seen)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://www.fut.gg/players/", "https://evil.example/players/",
                                 "https://www.fut.gg.evil.com/x", "https://user@www.fut.gg@evil.com/x",
                                 "https://127.0.0.1/x", "ftp://www.fut.gg/x", "https://www.fut.gg:8443/x"])
def test_only_https_futgg_is_accepted(url, tmp_path):
    with pytest.raises(ValueError):
        fetch.validate_url(url)
    calls = []
    r = fetch.crawl(db.connect(str(tmp_path / "b.db")), [url], fetch=lambda u: calls.append(u) or "", delay=0,
                    check_robots=False, log=lambda *_: None)
    assert calls == [] and r["errors"] and "non consentito" in r["errors"][0]["error"]


def test_good_urls_validate():
    for u in ("https://www.fut.gg/players/1-x/", "https://fut.gg/players/", "HTTPS://WWW.FUT.GG/players/"):
        assert fetch.validate_url(u)


def test_malicious_href_in_list_is_not_followed(tmp_path):
    html = list_page([("Pelé", 95, "Base Icon", "CM", "6.8M")]).replace("https://www.fut.gg/players/", "https://evil.example/players/")
    r, seen = _crawl(db.connect(str(tmp_path / "c.db")), {LIST: html})
    assert seen == [LIST] and any("non consentito" in e["error"] for e in r["errors"])


def test_single_page_errors_do_not_stop_the_crawl(tmp_path):
    html = list_page([("A", 90, "Base Icon", "CM", "1M"), ("B", 90, "Base Icon", "CM", "1M"), ("C", 90, "Base Icon", "CM", "1M"),
                      ("D", 90, "Base Icon", "CM", "1M")])
    pages = {LIST: html,
             "https://www.fut.gg/players/1-a/27-1/": urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO()),
             "https://www.fut.gg/players/2-b/27-2/": urllib.error.URLError("dns"),
             "https://www.fut.gg/players/3-c/27-3/": TimeoutError("lento"),
             "https://www.fut.gg/players/4-d/27-4/": gg_page(name="D", rating=90, rarity="Base Icon")}
    r, _ = _crawl(db.connect(str(tmp_path / "d.db")), pages)
    assert r["new"] == 1 and r["stopped"] is None and len(r["errors"]) == 3


def test_blocked_still_stops(tmp_path):
    html = list_page([("A", 90, "Base Icon", "CM", "1M"), ("B", 90, "Base Icon", "CM", "1M")])
    pages = {LIST: html, "https://www.fut.gg/players/1-a/27-1/": fetch.Blocked("HTTP 429")}
    r, seen = _crawl(db.connect(str(tmp_path / "e.db")), pages)
    assert r["stopped"] == "HTTP 429" and len(seen) == 2


def test_http_get_reads_at_most_5mb_and_validates(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class Opener:
        def open(self, req, timeout=None):
            return Resp(b"x" * (fetch.MAX_BYTES + 10))

    monkeypatch.setattr(fetch, "_opener", lambda: Opener())
    with pytest.raises(fetch.PageTooBig):
        fetch.http_get("https://www.fut.gg/players/")
    with pytest.raises(ValueError):
        fetch.http_get("file:///etc/passwd")


def test_http_get_blocked_codes(monkeypatch):
    class Opener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO())

    monkeypatch.setattr(fetch, "_opener", lambda: Opener())
    with pytest.raises(fetch.Blocked):
        fetch.http_get("https://www.fut.gg/players/")


def test_robots_read_once_per_host_with_timeout():
    calls = []

    def robots_fetch(url, timeout):
        calls.append((url, timeout))
        return "User-agent: *\nDisallow: /private/\n"

    cache: dict = {}
    assert fetch.allowed("https://www.fut.gg/players/1", cache=cache, robots_fetch=robots_fetch)
    assert fetch.allowed("https://www.fut.gg/players/2", cache=cache, robots_fetch=robots_fetch)
    assert not fetch.allowed("https://www.fut.gg/private/x", cache=cache, robots_fetch=robots_fetch)
    assert len(calls) == 1 and calls[0][1] and calls[0][1] <= 15


def test_robots_unreachable_is_cached_too():
    calls = []

    def robots_fetch(url, timeout):
        calls.append(url)
        raise TimeoutError("lento")

    cache: dict = {}
    for i in range(3):
        assert fetch.allowed(f"https://www.fut.gg/players/{i}", cache=cache, robots_fetch=robots_fetch)
    assert len(calls) == 1


def test_real_pace_equals_declared_delay(tmp_path):
    html = list_page([("A", 90, "Base Icon", "CM", "1M"), ("B", 90, "Base Icon", "CM", "1M")])
    pages = {LIST: html, "https://www.fut.gg/players/1-a/27-1/": gg_page(name="A", rating=90, rarity="Base Icon"),
             "https://www.fut.gg/players/2-b/27-2/": gg_page(name="B", rating=90, rarity="Base Icon")}
    now = [0.0]
    stamps = []

    def fake(u):
        stamps.append(now[0])
        return pages[u]

    def sleep(s):
        now[0] += s

    robots_calls = []

    def robots_fetch(url, timeout):
        stamps.append(now[0])
        robots_calls.append(url)
        return ""

    r = fetch.crawl(db.connect(str(tmp_path / "f.db")), [LIST], fetch=fake, delay=2.5, check_robots=True,
                    log=lambda *_: None, sleep=sleep, clock=lambda: now[0], robots_fetch=robots_fetch)
    assert r["new"] == 2 and len(robots_calls) == 1
    assert len(stamps) == 4  # robots + elenco + 2 pagine
    assert all(b - a >= 2.5 - 1e-9 for a, b in zip(stamps, stamps[1:]))  # ogni richiesta (robots incluso) e' distanziata
    assert now[0] <= 2.5 * 3 + 1e-9  # e non piu' del dichiarato: nessuna attesa dopo l'ultima
