"""Catalogo: cache delle valutazioni, GET /catalog (ordine, filtri, paginazione), facets, collezione, coerenza con /cards."""
import json

import pytest
from conftest import ST

from eafcmeta import db, scoring
from eafcmeta.catalog import evaluation
from eafcmeta.models import CardIn

API = "/api/v1"


def full_stats(boost=0):
    """Tutte le stat valide (servono per ogni posizione): quelle da attaccante di conftest, le altre a 70."""
    base = {**{k: 70 for k in scoring.load_config()["stat_keys"]}, **ST}
    return {k: max(1, min(99, v + boost)) for k, v in base.items()}


def mk(conn, name, version="Gold 86", position="ST", price=100_000, boost=0, released=None, **kw):
    stats = full_stats(boost)
    cid, _ = db.upsert_card(conn, CardIn(name=name, version=version, position=position, price=price, stats=stats,
                                         released_at=released, **kw))
    conn.commit()
    return cid


@pytest.fixture
def conn(client):
    c = db.connect()
    yield c
    c.close()


def names(r):
    return [i["name"] for i in r.json()["items"]]


def test_sort_by_release_with_undated_cards_last(client, conn):
    a = mk(conn, "Vecchia", released="2026-08-01T10:00:00Z")
    b = mk(conn, "Recente", released="2026-09-25T17:01:00Z")
    c = mk(conn, "SenzaDataA")          # senza data: in coda, per first_seen e poi per id
    d = mk(conn, "SenzaDataB")
    conn.execute("UPDATE cards SET first_seen='2026-10-01T00:00:00Z' WHERE id IN (?, ?)", (c, d))
    conn.execute("UPDATE cards SET first_seen='2026-10-02T00:00:00Z' WHERE id=?", (c,))
    conn.commit()
    r = client.get(f"{API}/catalog")
    assert names(r) == ["Recente", "Vecchia", "SenzaDataA", "SenzaDataB"] and r.json()["total"] == 4
    assert names(client.get(f"{API}/catalog?order=asc")) == ["Vecchia", "Recente", "SenzaDataB", "SenzaDataA"]
    it = r.json()["items"][0]
    assert it["released_at"] == "2026-09-25T17:01:00Z" and it["version_family"] == "Gold" and it["rating"] == 86
    assert it["first_seen"].endswith("Z") and it["in_collection"] is False and len(it["summary"]) <= 120
    assert set(it["scores"]) == {"base_score", "final_score", "pro_sentiment_score", "pro_share"}
    assert it["verdict"] in ("MUST_DO", "NEUTRAL", "AVOID") and it["meta_level"] in ("top", "meta", "playable", "below")
    assert it["cost_credits"] == 100_000 and isinstance(it["top_stats"], list) and it["opinions_count"] == 0


def test_filters_pagination_and_search(client, conn):
    mk(conn, "Vinícius Júnior", "TOTW 90", "RW", 500_000, released="2026-09-20T10:00:00Z", boost=2)
    mk(conn, "Marco Rossi", "Gold 80", "ST", 5_000, released="2026-09-10T10:00:00Z", boost=-10)
    mk(conn, "Kelly", "Team of the Week 88", "ST", 90_000, released="2026-09-12T10:00:00Z")
    mk(conn, "Zico", "Icon 91", "CAM", 3_000_000, released="2026-08-30T10:00:00Z", boost=4)
    mk(conn, "Anonimo", "Gold 75", "ST", 1_000)
    f = lambda q: client.get(f"{API}/catalog?{q}")
    assert f("q=vinicius").json()["total"] == 1 and f("q=junior%20totw").json()["total"] == 1   # senza accenti, parole in AND
    assert f("q=nessuno").json() == {"total": 0, "offset": 0, "limit": 60, "items": []}
    assert sorted(names(f("position=ST,CAM"))) == ["Anonimo", "Kelly", "Marco Rossi", "Zico"]
    assert sorted(names(f("version=TOTW"))) == ["Kelly", "Vinícius Júnior"]           # alias "Team of the Week"
    assert sorted(names(f("version=Icon,Gold"))) == ["Anonimo", "Marco Rossi", "Zico"]
    assert names(f("rating_min=88&rating_max=90&sort=rating&order=asc")) == ["Kelly", "Vinícius Júnior"]
    assert names(f("price_min=5000&price_max=90000&sort=price&order=asc")) == ["Marco Rossi", "Kelly"]
    assert sorted(names(f("released_after=2026-09-12"))) == ["Kelly", "Vinícius Júnior"]
    assert sorted(names(f("released_before=2026-09-10"))) == ["Marco Rossi", "Zico"]   # il giorno indicato e' compreso
    assert f("has_opinions=true").json()["total"] == 0 and f("has_opinions=false").json()["total"] == 5
    assert f("verdict=MUST_DO,NEUTRAL,AVOID").json()["total"] == 5
    assert f("meta=top,meta,playable,below").json()["total"] == 5
    # paginazione: total resta il totale, items la finestra
    p1, p2 = f("limit=2&offset=0").json(), f("limit=2&offset=4").json()
    assert (p1["total"], len(p1["items"]), p1["limit"]) == (5, 2, 2) and len(p2["items"]) == 1 and p2["offset"] == 4
    assert names(f("sort=name&order=asc"))[:2] == ["Anonimo", "Kelly"]
    sc = [i["scores"]["final_score"] for i in f("sort=score&order=desc").json()["items"]]
    assert sc == sorted(sc, reverse=True) and len(sc) == 5
    # parametri non validi
    for bad in ("sort=boh", "order=su", "verdict=forse", "meta=alto", "released_after=ieri", "limit=0", "limit=201", "offset=-1"):
        assert f(bad).status_code == 422, bad


def test_verdict_and_meta_filters_match_items(client, conn):
    for i in range(12):  # abbastanza carte per avere un mercato nella posizione
        mk(conn, f"Mercato {i}", "Gold 80", "ST", 2_000 * (i + 1) ** 2, boost=i - 6, released=f"2026-09-{i + 1:02d}T10:00:00Z")
    items = client.get(f"{API}/catalog?limit=200").json()["items"]
    assert {i["verdict"] for i in items} >= {"NEUTRAL"}
    for v in {i["verdict"] for i in items}:
        got = client.get(f"{API}/catalog?verdict={v}&limit=200").json()
        assert got["total"] == sum(i["verdict"] == v for i in items) and all(i["verdict"] == v for i in got["items"])
    for m in {i["meta_level"] for i in items}:
        got = client.get(f"{API}/catalog?meta={m}&limit=200").json()
        assert got["total"] == sum(i["meta_level"] == m for i in items)


def test_facets(client, conn):
    mk(conn, "A", "Gold 80", "ST", 1_000, released="2026-09-01T00:00:00Z")
    mk(conn, "B", "Gold 84", "ST", 9_000, released="2026-09-05T00:00:00Z")
    mk(conn, "C", "TOTW 90", "CAM", 70_000)
    r = client.get(f"{API}/catalog/facets").json()
    assert r["total"] == 3
    assert r["positions"] == [{"value": "ST", "count": 2}, {"value": "CAM", "count": 1}]
    assert r["versions"] == [{"value": "Gold", "count": 2}, {"value": "TOTW", "count": 1}]
    assert r["rating"] == {"min": 80, "max": 90} and r["price"] == {"min": 1_000, "max": 70_000}
    assert r["released"] == {"min": "2026-09-01T00:00:00Z", "max": "2026-09-05T00:00:00Z"}
    empty = client.get(f"{API}/catalog/facets").json()["total"]
    assert empty == 3


# ---------------------------------------------------------------- cache

class Spy:
    def __init__(self, monkeypatch):
        self.calls = []
        real = evaluation.recompute
        monkeypatch.setattr(evaluation, "recompute", lambda conn, ids=None, cfg=None: (self.calls.append(None if ids is None else list(ids)), real(conn, ids, cfg))[1])


def test_reading_never_recomputes_when_cache_is_fresh(client, conn, monkeypatch):
    for i in range(5):
        mk(conn, f"C{i}", price=1_000 * (i + 1))
    spy = Spy(monkeypatch)
    client.get(f"{API}/catalog")            # prima lettura: valuta una volta le carte mancanti
    assert len(spy.calls) == 1
    for _ in range(3):
        client.get(f"{API}/catalog?sort=score")
        client.get(f"{API}/catalog/facets")
        client.get(f"{API}/catalog/status")
    assert len(spy.calls) == 1               # le letture successive non ricalcolano niente
    assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 5


def test_cache_invalidation_on_price_opinion_and_card_change(client, conn, monkeypatch):
    ids = [mk(conn, f"C{i}", price=2_000 * (i + 1), boost=i) for i in range(6)]
    client.get(f"{API}/catalog")
    spy = Spy(monkeypatch)
    # prezzo: si rivaluta SOLO la carta toccata (verdetti della posizione rifatti, ma score delle altre intatti)
    scores = {i["id"]: i["scores"]["final_score"] for i in client.get(f"{API}/catalog").json()["items"]}
    client.put(f"{API}/cards/{ids[2]}", json={"name": "C2", "version": "Gold 86", "position": "ST", "price": 777_000, "stats": ST})
    got = client.get(f"{API}/catalog").json()["items"]
    assert [x for x in got if x["id"] == ids[2]][0]["cost_credits"] == 777_000
    assert spy.calls == [[]]                      # ricalcolo mirato (solo le obsolete), non totale
    assert conn.execute("SELECT config_hash FROM evaluations WHERE card_id=?", (ids[0],)).fetchone()[0] != ""
    assert {x["id"]: x["scores"]["final_score"] for x in got if x["id"] != ids[2]} == {k: v for k, v in scores.items() if k != ids[2]}
    client.get(f"{API}/catalog")
    assert len(spy.calls) == 1
    # parere: cambia pro_share/final_score e opinions_count della sola carta
    r = client.put(f"{API}/cards/{ids[3]}/opinions", json={"creator": "Pro", "stance": "no", "score": 40, "reason": "lenta"})
    assert r.status_code == 200
    it = [x for x in client.get(f"{API}/catalog").json()["items"] if x["id"] == ids[3]][0]
    assert it["opinions_count"] == 1 and it["scores"]["pro_share"] > 0 and it["scores"]["final_score"] < scores[ids[3]]
    assert len(spy.calls) == 2
    assert client.get(f"{API}/catalog?has_opinions=true").json()["total"] == 1
    # parere cancellato: torna senza pareri
    op = client.get(f"{API}/cards/{ids[3]}").json()["opinions"][0]["id"]
    client.delete(f"{API}/cards/{ids[3]}/opinions/{op}")
    assert client.get(f"{API}/catalog?has_opinions=true").json()["total"] == 0
    # nuova carta e carta cancellata
    new = mk(conn, "Nuova")
    assert client.get(f"{API}/catalog").json()["total"] == 7 and conn.execute("SELECT 1 FROM evaluations WHERE card_id=?", (new,)).fetchone()
    client.delete(f"{API}/cards/{new}")
    assert conn.execute("SELECT 1 FROM evaluations WHERE card_id=?", (new,)).fetchone() is None


def test_config_change_invalidates_everything(client, conn, tmp_path, monkeypatch):
    for i in range(4):
        mk(conn, f"C{i}", price=1_000 * (i + 1), boost=i)
    before = {x["id"]: x["scores"]["base_score"] for x in client.get(f"{API}/catalog").json()["items"]}
    spy = Spy(monkeypatch)
    client.get(f"{API}/catalog")
    assert spy.calls == []
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"soft_cap_start": 80}), encoding="utf-8")
    scoring.load_config.cache_clear()
    after = {x["id"]: x["scores"]["base_score"] for x in client.get(f"{API}/catalog").json()["items"]}
    assert len(spy.calls) == 1 and spy.calls[0] == []        # config diversa: tutte obsolete, un solo ricalcolo
    assert conn.execute("SELECT COUNT(DISTINCT config_hash) FROM evaluations").fetchone()[0] == 1
    assert after != before
    client.get(f"{API}/catalog")
    assert len(spy.calls) == 1


def test_rules_change_invalidates(client, conn, monkeypatch):
    from eafcmeta import rules
    mk(conn, "C0")
    client.get(f"{API}/catalog")
    h1 = evaluation.config_hash()
    st = rules.state_path()
    st.write_text(json.dumps({"learned": [], "status": {rules.load_base()["rules"][0]["id"]: "rejected"}}), encoding="utf-8")
    rules.clear_cache()
    assert evaluation.config_hash() != h1
    Spy(monkeypatch)
    client.get(f"{API}/catalog")
    assert evaluation.stale_count(conn, evaluation.config_hash()) == 0


def test_catalog_matches_cards_endpoint(client, conn):
    for i in range(15):
        mk(conn, f"M{i}", "Gold 80", "ST", 1_500 * (i + 1) ** 2, boost=i - 7)
    mk(conn, "Altro", "Icon 90", "CAM", 200_000)
    live = {c["id"]: c for c in client.get(f"{API}/cards").json()}
    cat = {c["id"]: c for c in client.get(f"{API}/catalog?limit=200").json()["items"]}
    assert set(live) == set(cat) and len(cat) == 16
    for i, c in cat.items():
        assert c["scores"] == live[i]["scores"] and c["verdict"] == live[i]["verdict"] and c["value_gap"] == live[i]["value_gap"]
        assert c["meta_level"] == live[i]["meta_level"] and c["top_stats"] == live[i]["top_stats"]


def test_summary_text_is_italian_and_short(client, conn):
    fast = {**ST, "acceleration": 95, "sprint_speed": 94}
    cid, _ = db.upsert_card(conn, CardIn(name="Rapida", version="Gold 90", position="ST", price=50_000, stats=fast, playstyles=["Power Shot+"]))
    conn.commit()
    s = client.get(f"{API}/catalog").json()["items"][0]["summary"]
    assert s.startswith("Scatto e velocità") and len(s) <= 120
    pace = {**ST, "acceleration": 95, "sprint_speed": 94, "finishing": 70, "shot_power": 70, "positioning": 70, "reactions": 70,
            "composure": 70, "ball_control": 70, "agility": 70}
    db.upsert_card(conn, CardIn(name="Solo scatto", version="Gold 91", position="ST", price=60_000, stats=pace))
    conn.commit()
    assert any(i["summary"].startswith("Scatto e velocità da vertice") for i in client.get(f"{API}/catalog").json()["items"])
    assert evaluation.make_summary("X" * 200, {"verdict": "MUST_DO", "value_gap": 4.1}).endswith("…")
    assert evaluation.make_summary("Scatto", {"verdict": "MUST_DO", "value_gap": 4.1}) == "Scatto · rende più del prezzo (+4,1)"
    assert evaluation.make_summary("Scatto", {"verdict": "AVOID", "value_gap": -3.0}) == "Scatto · costa più di quanto rende (-3,0)"
    assert evaluation.make_summary("Scatto", {"verdict": "NEUTRAL", "value_gap": None}) == "Scatto"


def test_unevaluable_card_is_cached_once(client, conn, monkeypatch):
    cid = mk(conn, "Rotta")
    conn.execute("UPDATE cards SET data=? WHERE id=?", (json.dumps({"stats": {"acceleration": 80}, "playstyles": [], "body_type": "Average",
                                                                     "weak_foot": 3, "skill_moves": 3, "signals": {}}), cid))
    conn.commit()
    spy = Spy(monkeypatch)
    it = client.get(f"{API}/catalog").json()["items"][0]
    assert it["scores"]["final_score"] is None and "non valutabile" in it["summary"]
    client.get(f"{API}/catalog")
    assert len(spy.calls) == 1


# ---------------------------------------------------------------- sottocampionamento

def test_sample_points_is_deterministic_and_exact_below_threshold():
    pts = [(float(p), float(p) / 10) for p in range(1, 101)]
    assert evaluation.sample_points(pts, 100) is pts and evaluation.sample_points(pts, 500) is pts
    s = evaluation.sample_points(pts, 10)
    assert len(s) == 10 and s[0] == pts[0] and s[-1] == pts[-1] and s == evaluation.sample_points(list(reversed(pts)), 10)


def test_shared_curve_exact_below_threshold_and_close_above():
    import random
    cfg = scoring.load_config()
    rnd = random.Random(3)
    items = [(i, "ST", 1_000 * (1 + i) ** 1.3, 70 + 3 * (i ** 0.5) + rnd.gauss(0, 1)) for i in range(400)]
    exact = evaluation.Markets(items, cfg, max_points=10_000)
    sampled = evaluation.Markets(items, cfg, max_points=300)
    c_exact = exact.for_card(0, "ST")[1]
    assert evaluation.Markets(items[:300], cfg, 300).for_card(0, "ST")[1] == evaluation.Markets(items[:300], cfg, 10_000).for_card(0, "ST")[1]
    c_s = sampled.for_card(0, "ST")[1]
    assert c_s != c_exact and abs(c_s[1] - c_exact[1]) < 0.15 * abs(c_exact[1])   # sopra soglia: approssimazione vicina
    assert sampled.for_card(5, "ST")[1] is sampled.for_card(9, "ST")[1]           # una curva per posizione, non per carta


def test_recompute_total_on_many_cards_is_fast_and_consistent(client, conn):
    import random, time
    rnd = random.Random(5)
    keys = scoring.load_config()["stat_keys"]
    rows = []
    for i in range(1500):
        base = rnd.randint(60, 92)
        stats = {k: max(20, min(99, int(rnd.gauss(base, 7)))) for k in keys}
        rows.append((f"P{i}", f"Gold {base}", rnd.choice(["ST", "CM", "CB", "RW"]), int(10 ** rnd.uniform(2.5, 6)), 0,
                     json.dumps({"stats": stats, "playstyles": [], "body_type": "Average", "weak_foot": 3, "skill_moves": 3, "signals": {}}),
                     "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z", base))
    conn.executemany("INSERT INTO cards (name, version, position, price, is_sbc, data, released_at, first_seen, rating) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    t = time.perf_counter()
    r = evaluation.recompute(conn)
    assert r["valutate"] == 1500 and time.perf_counter() - t < 15   # limite largo: la misura vera e' nel report
    assert client.get(f"{API}/catalog?limit=200").json()["total"] == 1500
    assert evaluation.ensure_fresh(conn) == 0


# ---------------------------------------------------------------- collezione

def test_collection_endpoints_and_stats(client, conn):
    a, b, c = mk(conn, "A", boost=5, price=1_000), mk(conn, "B", price=2_000), mk(conn, "C", boost=-20, price=3_000)
    assert client.get(f"{API}/collection").json()["stats"] == {"cards": 0, "total_value": 0, "avg_score": None, "meta_count": 0, "best": []}
    assert client.put(f"{API}/collection/{a}").json() == {"ok": True, "in_collection": True}
    assert client.put(f"{API}/collection/{a}", json={"note": "titolare"}).json()["ok"]    # idempotente
    client.put(f"{API}/collection/{c}")
    assert client.put(f"{API}/collection/9999").status_code == 404
    col = client.get(f"{API}/collection").json()
    assert col["total"] == 2 and {i["name"] for i in col["items"]} == {"A", "C"} and all(i["in_collection"] for i in col["items"])
    st = col["stats"]
    assert st["cards"] == 2 and st["total_value"] == 4_000 and st["best"][0] == a and len(st["best"]) == 2
    assert st["avg_score"] is not None and 0 <= st["meta_count"] <= 2
    assert conn.execute("SELECT note FROM collection WHERE card_id=?", (a,)).fetchone()[0] == "titolare"
    cat = {i["name"]: i["in_collection"] for i in client.get(f"{API}/catalog").json()["items"]}
    assert cat == {"A": True, "B": False, "C": True}
    assert [i["name"] for i in client.get(f"{API}/catalog?in_collection=true&sort=name&order=asc").json()["items"]] == ["A", "C"]
    assert [i["name"] for i in client.get(f"{API}/catalog?in_collection=false").json()["items"]] == ["B"]
    assert client.get(f"{API}/collection?q=zzz").json()["total"] == 0 and client.get(f"{API}/collection?q=zzz").json()["stats"]["cards"] == 2
    assert client.delete(f"{API}/collection/{a}").json() == {"ok": True, "in_collection": False}
    assert client.delete(f"{API}/collection/{a}").json()["ok"] and client.delete(f"{API}/collection/9999").json()["ok"]
    assert client.get(f"{API}/collection").json()["total"] == 1
    client.delete(f"{API}/cards/{c}")                     # la carta cancellata esce anche dalla collezione
    assert client.get(f"{API}/collection").json()["total"] == 0


def test_manual_card_enters_catalog_and_optionally_collection(client):
    r = client.post(f"{API}/cards", json={"name": "Manuale", "version": "Gold 80", "position": "ST", "price": 5000, "stats": ST})
    r2 = client.post(f"{API}/cards", json={"name": "Mia", "version": "Gold 81", "position": "ST", "price": 5000, "stats": ST, "in_collection": True})
    assert r.status_code == r2.status_code == 201
    assert client.get(f"{API}/catalog").json()["total"] == 2
    assert [i["name"] for i in client.get(f"{API}/collection").json()["items"]] == ["Mia"]
    assert [i["id"] for i in client.get(f"{API}/catalog?in_collection=true").json()["items"]] == [r2.json()["id"]]


def test_existing_routes_still_work_and_token_required(client, conn, monkeypatch):
    cid = mk(conn, "Rotta")
    assert client.get(f"{API}/cards/{cid}").json()["analysis"] and client.get(f"{API}/cards").status_code == 200
    monkeypatch.setenv("EAFCMETA_TOKEN", "segreto")
    for method, url in (("get", "/catalog"), ("get", "/catalog/facets"), ("get", "/catalog/status"), ("post", "/catalog/update"),
                        ("post", "/catalog/cancel"), ("put", "/catalog/config"), ("get", "/collection"), ("put", f"/collection/{cid}")):
        assert getattr(client, method)(API + url).status_code == 401, url
    assert client.get(f"{API}/catalog", headers={"X-Token": "segreto"}).status_code == 200
