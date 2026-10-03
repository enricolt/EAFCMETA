"""Catalogo: migrazione, date di uscita, rating, famiglia di versione. Pagine ricostruite piccole (nessuna rete)."""
import sqlite3

import pytest

from eafcmeta import db, sources
from eafcmeta.catalog import dates, schema, versions
from eafcmeta.models import CardIn
from test_collect import fb_page, gg_page

LEGACY = """CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL, price INTEGER NOT NULL, is_sbc INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL,
    pro_score REAL, pro_notes TEXT NOT NULL DEFAULT '');
CREATE TABLE price_history (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, price INTEGER NOT NULL, ts TEXT NOT NULL);
CREATE TABLE opinions (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, creator TEXT NOT NULL COLLATE NOCASE,
    stance TEXT NOT NULL, score REAL, reason TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL,
    UNIQUE(card_id, creator));"""


def cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(cards)")}


def test_migration_on_legacy_db_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY)
    raw.execute("INSERT INTO cards (name, version, position, price, data) VALUES ('Vecchia', 'Gold 86', 'ST', 5000, '{}')")
    raw.execute("INSERT INTO cards (name, version, position, price, data) VALUES ('Senza numero', 'Icon', 'CM', 9000, '{}')")
    raw.execute("INSERT INTO price_history (card_id, price, ts) VALUES (1, 5000, '2026-09-01 10:30')")
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert {"released_at", "first_seen", "rating", "url"} <= cols(conn)
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM cards")}
    assert rows["Vecchia"]["rating"] == 86 and rows["Vecchia"]["released_at"] is None
    assert rows["Vecchia"]["first_seen"] == "2026-09-01T10:30:00Z"       # primo prezzo registrato
    assert rows["Senza numero"]["rating"] is None and rows["Senza numero"]["first_seen"].endswith("Z")
    before = [tuple(r) for r in conn.execute("SELECT * FROM cards ORDER BY id")]
    schema.ensure_schema(conn)
    schema.ensure_schema(conn)  # rieseguibile: niente errori e niente cambiamenti
    assert [tuple(r) for r in conn.execute("SELECT * FROM cards ORDER BY id")] == before
    names = {r[1] for r in conn.execute("PRAGMA index_list(cards)")}
    assert {"ix_cards_released", "ix_cards_rating", "ix_cards_position"} <= names
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evaluations", "catalog_runs", "catalog_state", "collection"} <= tables
    conn.close()
    assert cols(db.connect(path)) >= {"released_at"}  # seconda connessione allo stesso file


@pytest.mark.parametrize("text,expected", [
    ("Sep 25, 2026, 5:01 PM UTC", "2026-09-25T17:01:00Z"),
    ("Aug 18, 2026, 2:17 PM UTC", "2026-08-18T14:17:00Z"),
    ("Sep 15, 2026, 8:09 PM UTC", "2026-09-15T20:09:00Z"),
    ("Jan 3, 2027, 12:05 AM UTC", "2027-01-03T00:05:00Z"),
    ("Jan 3, 2027, 12:05 PM UTC", "2027-01-03T12:05:00Z"),
    ("September 25, 2026", "2026-09-25T00:00:00Z"),
    ("Release date:  2026-09-11", "2026-09-11T00:00:00Z"),
    ("2026-09-11", "2026-09-11T00:00:00Z"),
    ("2026-09-11T10:00:00Z", "2026-09-11T10:00:00Z"),
    ("2026-09-11 10:00", "2026-09-11T10:00:00Z"),
    ("", None), (None, None), ("N/A", None), ("Release date:", None), ("Feb 31, 2026, 1:00 PM UTC", None), ("Foo 3, 2026", None),
    ("Sep 25, 2026, 13:01 PM UTC", None),
])
def test_parse_release_date(text, expected):
    assert dates.parse_release_date(text) == expected


def _with(html: str, extra: str) -> str:
    return html.replace("</body>", f"{extra}</body>")


def test_futgg_page_has_released_at_and_rating():
    d = sources.parse_page(_with(gg_page(), "<div>Added On</div><div>Sep 25, 2026, 5:01 PM UTC</div><div>Lowest BIN</div>"))
    assert d["released_at"] == "2026-09-25T17:01:00Z" and d["rating"] == 86
    assert sources.to_card(d).released_at == "2026-09-25T17:01:00Z" and sources.to_card(d).rating == 86
    assert sources.parse_page(gg_page())["released_at"] is None   # nessuna data: nessun errore


def test_futbin_page_release_date_variants():
    one_token = sources.parse_page(_with(fb_page(), "<div>Release date:  2026-09-11</div>"))
    assert one_token["released_at"] == "2026-09-11T00:00:00Z" and one_token["rating"] == 83
    split = sources.parse_page(_with(fb_page(), "<div>Release date:</div><div>2026-09-12</div>"))
    assert split["released_at"] == "2026-09-12T00:00:00Z"
    assert sources.parse_page(fb_page())["released_at"] is None


def test_upsert_sets_first_seen_released_and_keeps_precise_date(tmp_path):
    conn = db.connect(str(tmp_path / "u.db"))
    gg = sources.to_card(sources.parse_page(_with(gg_page(), "<div>Added On</div><div>Sep 25, 2026, 5:01 PM UTC</div>")))
    cid, st = db.upsert_card(conn, gg)
    row = conn.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    assert st == "new" and row["released_at"] == "2026-09-25T17:01:00Z" and row["first_seen"].endswith("Z") and row["rating"] == 86
    first_seen = row["first_seen"]
    # stessa data letta da FUTBIN (solo il giorno): non toglie l'ora precisa
    fb = gg.model_copy(update={"released_at": "2026-09-25T00:00:00Z"})
    db.upsert_card(conn, fb)
    assert conn.execute("SELECT released_at, first_seen FROM cards WHERE id=?", (cid,)).fetchone()[:] == ("2026-09-25T17:01:00Z", first_seen)
    # una data diversa la sostituisce; una pagina senza data non cancella quella nota
    db.upsert_card(conn, gg.model_copy(update={"released_at": "2026-09-26T09:00:00Z"}))
    db.upsert_card(conn, gg.model_copy(update={"released_at": None}))
    assert conn.execute("SELECT released_at FROM cards WHERE id=?", (cid,)).fetchone()[0] == "2026-09-26T09:00:00Z"


def test_cardin_validates_release_and_rating():
    base = dict(name="X", position="ST", price=1, stats={})
    from conftest import ST
    ok = CardIn(**{**base, "stats": ST, "released_at": "2026-09-25", "rating": 88})
    assert ok.released_at == "2026-09-25T00:00:00Z"
    with pytest.raises(ValueError):
        CardIn(**{**base, "stats": ST, "released_at": "ieri"})
    with pytest.raises(ValueError):
        CardIn(**{**base, "stats": ST, "rating": 120})


@pytest.mark.parametrize("version,family", [
    ("Gold 86", "Gold"), ("Silver 70", "Silver"), ("Bronze 55", "Bronze"), ("Icon 94", "Icon"), ("Hero 88", "Hero"),
    ("TOTW 89", "TOTW"), ("Team of the Week 89", "TOTW"), ("TOTY 91", "TOTY"), ("Team of the Year 91", "TOTY"),
    ("Destined for Glory 86", "Destined for Glory"), ("Rare", "Rare"), ("Icons 90", "Icon"), ("", "Altro"), ("86", "Altro"),
    ("Gold", "Gold")])
def test_version_family(version, family):
    assert versions.version_family(version) == family


def test_rating_from_version():
    assert versions.rating_from_version("Gold 86") == 86
    assert versions.rating_from_version("Icon") is None and versions.rating_from_version("Gold 5") is None
