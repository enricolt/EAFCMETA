"""Revisione critica, area G: la curva prezzo/score si calcola una volta per posizione e per richiesta."""
import random

from conftest import ST
from eafcmeta import api, db, scoring
from eafcmeta.models import CardIn


def _fill(n, seed=3):
    rnd = random.Random(seed)
    conn = db.connect()
    for i in range(n):
        q = rnd.randint(70, 95)
        stats = {k: max(1, min(99, q + rnd.randint(-6, 6))) for k in ST}
        price = int(10_000 * 1.03 ** (q - 70) * rnd.uniform(0.6, 1.6))
        db.upsert_card(conn, CardIn(name=f"P{i}", version="Gold 80", position="ST", price=price, stats=stats))
    conn.commit()
    conn.close()


def test_large_market_fits_the_curve_once_per_request(client, monkeypatch):
    _fill(90)  # > 60 carte nella posizione: curva condivisa
    calls = []
    real = scoring.fit_curve
    monkeypatch.setattr(scoring, "fit_curve", lambda pts: calls.append(len(pts)) or real(pts))
    r = client.get("/api/v1/cards")
    assert r.status_code == 200 and len(r.json()) == 90
    assert calls == [90]  # una sola volta, non 90
    assert all(c["market_size"] == 89 for c in r.json())


def test_small_market_still_excludes_the_card_itself(client):
    _fill(20)
    cfg = scoring.load_config()
    rows = {c["id"]: c for c in client.get("/api/v1/cards").json()}
    conn = db.connect()
    scored = api._scored(conn)
    conn.close()
    for cid, (card, _, final) in list(scored.items())[:5]:
        market = [(c["price"], f) for i, (c, _, f) in scored.items() if i != cid]
        exp = scoring.verdict(final, card["price"], market, cfg)
        assert rows[cid]["verdict"] == exp["verdict"] and rows[cid]["value_gap"] == exp["value_gap"]


def test_list_and_detail_agree_on_large_markets(client):
    _fill(75)
    lst = client.get("/api/v1/cards").json()
    for c in lst[:5] + lst[-5:]:
        d = client.get(f"/api/v1/cards/{c['id']}").json()
        assert (d["verdict"], d["value_gap"], d["market_size"]) == (c["verdict"], c["value_gap"], c["market_size"])


def test_filters_keep_the_full_market(client):
    _fill(30)
    full = {c["id"]: c["value_gap"] for c in client.get("/api/v1/cards").json()}
    part = client.get("/api/v1/cards?q=P1").json()
    assert part and all(full[c["id"]] == c["value_gap"] for c in part)
