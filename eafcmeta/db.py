import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def db_path() -> str:
    return os.environ.get("EAFCMETA_DB") or str(ROOT / "eafcmeta.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
            position TEXT NOT NULL, price INTEGER NOT NULL,
            is_sbc INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL,        -- JSON: stats, playstyles, body_type, weak_foot, skill_moves
            pro_score REAL, pro_notes TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_cards_position ON cards(position);
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            price INTEGER NOT NULL, ts TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_hist_card ON price_history(card_id, id);"""
    )
    try:  # chiave naturale: stessa carta = stesso nome+versione+posizione (db vecchi con doppioni: si salta)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_card ON cards(name COLLATE NOCASE, version COLLATE NOCASE, position)")
    except sqlite3.IntegrityError:
        pass
    return conn


def row_to_card(r: sqlite3.Row) -> dict:
    d = json.loads(r["data"])
    return {"id": r["id"], "name": r["name"], "version": r["version"], "position": r["position"],
            "price": r["price"], "is_sbc": bool(r["is_sbc"]), "pro_score": r["pro_score"],
            "pro_notes": r["pro_notes"], **d}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _add_history(conn, card_id: int, price: int) -> None:
    conn.execute("INSERT INTO price_history (card_id, price, ts) VALUES (?,?,?)", (card_id, price, _now()))


def _words(name: str) -> set[str]:
    return set(name.lower().replace(".", " ").replace("-", " ").split())


def find_card(conn, card) -> sqlite3.Row | None:
    """Stessa carta: nome+versione+posizione uguali; oppure stesso ruolo/versione e nome contenuto nell'altro
    ('Kelly' su FUTBIN = 'Chloe Kelly' su FUT.GG), purché il candidato sia uno solo."""
    row = conn.execute("SELECT id, name, price FROM cards WHERE name = ? COLLATE NOCASE AND version = ? COLLATE NOCASE "
                       "AND position = ?", (card.name, card.version, card.position)).fetchone()
    if row is not None:
        return row
    mine = _words(card.name)
    cands = [r for r in conn.execute("SELECT id, name, price FROM cards WHERE version = ? COLLATE NOCASE AND position = ?",
                                     (card.version, card.position))
             if mine and _words(r["name"]) and (mine <= _words(r["name"]) or _words(r["name"]) <= mine)]
    return cands[0] if len(cands) == 1 else None


def upsert_card(conn, card) -> tuple[int, str]:
    """Inserisce o aggiorna (chiave: nome+versione+posizione). Non fa commit. Ritorna (id, 'new'|'updated')."""
    data = json.dumps({"stats": card.stats, "playstyles": card.playstyles, "body_type": card.body_type,
                       "weak_foot": card.weak_foot, "skill_moves": card.skill_moves})
    row = find_card(conn, card)
    if row is None:
        cur = conn.execute("INSERT INTO cards (name, version, position, price, is_sbc, data) VALUES (?,?,?,?,?,?)",
                           (card.name, card.version, card.position, card.price, int(card.is_sbc), data))
        _add_history(conn, cur.lastrowid, card.price)
        return cur.lastrowid, "new"
    name = card.name if len(card.name) > len(row["name"]) else row["name"]  # tiene il nome più completo
    conn.execute("UPDATE cards SET name=?, price=?, is_sbc=?, data=? WHERE id=?",
                 (name, card.price, int(card.is_sbc), data, row["id"]))
    if row["price"] != card.price:
        _add_history(conn, row["id"], card.price)
    return row["id"], "updated"


def update_card(conn, card_id: int, card) -> None:
    """Modifica completa di una carta esistente (può cambiare anche nome/versione/posizione)."""
    data = json.dumps({"stats": card.stats, "playstyles": card.playstyles, "body_type": card.body_type,
                       "weak_foot": card.weak_foot, "skill_moves": card.skill_moves})
    old = conn.execute("SELECT price FROM cards WHERE id = ?", (card_id,)).fetchone()
    conn.execute("UPDATE cards SET name=?, version=?, position=?, price=?, is_sbc=?, data=? WHERE id=?",
                 (card.name, card.version, card.position, card.price, int(card.is_sbc), data, card_id))
    if old and old["price"] != card.price:
        _add_history(conn, card_id, card.price)


def set_price(conn, card_id: int, price: int) -> None:
    old = conn.execute("SELECT price FROM cards WHERE id = ?", (card_id,)).fetchone()
    conn.execute("UPDATE cards SET price=? WHERE id=?", (price, card_id))
    if old and old["price"] != price:
        _add_history(conn, card_id, price)


def history(conn, card_id: int, limit: int = 12) -> list[dict]:
    rows = conn.execute("SELECT price, ts FROM price_history WHERE card_id=? ORDER BY id DESC LIMIT ?", (card_id, limit))
    return [dict(r) for r in rows][::-1]
