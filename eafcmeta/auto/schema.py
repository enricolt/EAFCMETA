"""Schema della raccolta automatica: migrazione idempotente di `opinions` + tabelle nuove.

Richiamata da db.connect (una riga). Sicura da rieseguire: ogni ALTER è preceduto da un controllo e un eventuale
"duplicate column" (due connessioni in gara) viene ignorato.
"""
import sqlite3

OPINION_COLUMNS = (
    ("auto", "INTEGER NOT NULL DEFAULT 0"),    # 1 = salvato dalla raccolta automatica (rimovibile in blocco)
    ("confidence", "REAL"),                    # confidenza dell'estrazione (solo pareri automatici)
    ("src_date", "TEXT NOT NULL DEFAULT ''"),  # data del video di origine (AAAA-MM-GG): decide quale parere auto è più recente
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(opinions)")}
    for name, decl in OPINION_COLUMNS:
        if name not in have:
            try:
                conn.execute(f"ALTER TABLE opinions ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as e:  # un'altra connessione l'ha appena aggiunta
                if "duplicate column" not in str(e).lower():
                    raise
    conn.executescript(
        """CREATE INDEX IF NOT EXISTS ix_opinions_auto ON opinions(auto);
        CREATE TABLE IF NOT EXISTS channels (
            name TEXT PRIMARY KEY COLLATE NOCASE,
            channel_id TEXT NOT NULL DEFAULT '',
            handle TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            subscribers INTEGER,
            discovered_at TEXT NOT NULL,
            verified_auto INTEGER NOT NULL DEFAULT 0   -- 1 = trovato e verificato dalla scoperta automatica
        );
        CREATE TABLE IF NOT EXISTS channel_misses (
            name TEXT PRIMARY KEY COLLATE NOCASE,
            ts TEXT NOT NULL, reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS processed_items (
            url TEXT PRIMARY KEY,
            creator TEXT NOT NULL DEFAULT '',
            ts TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS auto_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started TEXT NOT NULL,
            finished TEXT,
            status TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '{}'
        );"""
    )
