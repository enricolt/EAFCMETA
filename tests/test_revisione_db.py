"""Revisione critica, area B (fusione carte) e db.connect (area F)."""
import sqlite3

from conftest import ST, card
from eafcmeta import db
from eafcmeta.models import CardIn


def _cards(client):
    return client.get("/api/v1/cards").json()


def test_partial_name_with_different_stats_is_not_merged(client):
    client.post("/api/v1/cards", json=card(name="Bernardo Silva", version="Gold 86", price=50000))
    far = {k: v - 12 for k, v in ST.items()}
    r = client.post("/api/v1/cards", json=card(name="Silva", version="Gold 86", price=9000, stats=far)).json()
    assert r["status"] == "new"
    prices = sorted(c["cost_credits"] for c in _cards(client))
    assert prices == [9000, 50000]  # il prezzo del primo non e' stato sovrascritto


def test_partial_name_with_same_stats_is_merged(client):
    client.post("/api/v1/cards", json=card(name="Chloe Kelly", version="Gold 86", price=11000))
    near = {k: v + (1 if i % 2 else -1) for i, (k, v) in enumerate(ST.items())}
    r = client.post("/api/v1/cards", json=card(name="Kelly", version="Gold 86", price=12000, stats=near)).json()
    assert r["status"] == "updated"
    assert [c["name"] for c in _cards(client)] == ["Chloe Kelly"]


def test_partial_name_with_same_club_is_merged_even_if_stats_differ(client):
    client.post("/api/v1/cards", json=card(name="Chloe Kelly", version="Gold 86", club="Arsenal"))
    far = {k: v - 12 for k, v in ST.items()}
    r = client.post("/api/v1/cards", json=card(name="Kelly", version="Gold 86", stats=far, club="arsenal")).json()
    assert r["status"] == "updated"


def test_partial_name_with_different_club_and_stats_is_not_merged(client):
    client.post("/api/v1/cards", json=card(name="Bernardo Silva", version="Gold 86", club="Man City"))
    far = {k: v - 12 for k, v in ST.items()}
    r = client.post("/api/v1/cards", json=card(name="Silva", version="Gold 86", stats=far, club="Milan")).json()
    assert r["status"] == "new"


def test_accents_and_case_are_normalized(client):
    a = client.post("/api/v1/cards", json=card(name="Vinícius Júnior", version="Gold 90")).json()
    b = client.post("/api/v1/cards", json=card(name="VINICIUS JUNIOR", version="Gold 90")).json()
    assert a["id"] == b["id"] and b["status"] == "updated"
    assert db._words("Vinícius Júnior") == db._words("Vinicius Junior") == {"vinicius", "junior"}
    assert db._words("N'Golo Kanté") == db._words("n golo kante")


def test_find_card_without_evidence_does_not_merge(tmp_path):
    conn = db.connect(str(tmp_path / "x.db"))
    db.upsert_card(conn, CardIn(**card(name="Bernardo Silva", version="Gold 86")))

    class Probe:  # come in collect.apply_list: nome/versione/ruolo, senza stats
        name, version, position = "Silva", "Gold 86", "ST"

    assert db.find_card(conn, Probe) is None


def test_connect_does_not_fail_on_db_with_duplicates(tmp_path):
    path = str(tmp_path / "dup.db")
    conn = db.connect(path)
    conn.execute("DROP INDEX IF EXISTS ux_card")
    for _ in range(2):
        conn.execute("INSERT INTO cards (name, version, position, price, data) VALUES ('A','v','ST',1,'{}')")
    conn.commit()
    conn.close()
    db._SCHEMA_DONE.clear()
    for _ in range(3):  # prima volta: indice impossibile (doppioni), poi non si riprova a ogni richiesta
        db.connect(path).close()
    assert path in db._SCHEMA_DONE
    conn = sqlite3.connect(path)
    assert conn.execute("select count(*) from cards").fetchone()[0] == 2
