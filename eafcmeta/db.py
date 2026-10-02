import json
import os
import sqlite3

DB_PATH = os.environ.get("EAFCMETA_DB", "eafcmeta.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
            position TEXT NOT NULL, price INTEGER NOT NULL,
            is_sbc INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL,        -- JSON: stats, playstyles, body_type, weak_foot, skill_moves
            pro_score REAL, pro_notes TEXT NOT NULL DEFAULT ''
        )"""
    )
    return conn


def row_to_card(r: sqlite3.Row) -> dict:
    d = json.loads(r["data"])
    return {"id": r["id"], "name": r["name"], "version": r["version"], "position": r["position"],
            "price": r["price"], "is_sbc": bool(r["is_sbc"]), "pro_score": r["pro_score"],
            "pro_notes": r["pro_notes"], **d}
