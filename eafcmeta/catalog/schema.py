"""Schema del catalogo: migrazione idempotente di `cards` + tabelle nuove. Richiamata da db.connect (una riga).

Colonne aggiunte a `cards` (ALTER TABLE ... ADD COLUMN, come auto/schema.py; i db esistenti non si rompono):
- released_at  testo ISO UTC ("2026-09-25T17:01:00Z"), NULL se la data e' sconosciuta;
- first_seen   quando l'app ha visto la carta la prima volta (ISO UTC); per le carte gia' presenti = primo prezzo registrato;
- rating       valutazione numerica (per le carte vecchie ricavata dal testo della versione, es. "Gold 86" -> 86);
- url          indirizzo della pagina del giocatore (serve a ritrovare la carta negli elenchi e ad aggiornarne il prezzo).

Tabelle: evaluations (cache delle valutazioni), catalog_runs (aggiornamenti), catalog_state (cursori), collection
(la scheda secondaria «La mia collezione»). Trigger: ogni modifica di una carta o di un parere segna come OBSOLETA la
sua valutazione (config_hash = ''), cosi' qualunque percorso di scrittura (import, API, automazione) la invalida.
"""
import sqlite3
from datetime import datetime, timezone

from .versions import rating_from_version

CARD_COLUMNS = (("released_at", "TEXT"), ("first_seen", "TEXT"), ("rating", "INTEGER"), ("url", "TEXT"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
    for name, decl in CARD_COLUMNS:
        if name not in have:
            try:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as e:  # un'altra connessione l'ha appena aggiunta
                if "duplicate column" not in str(e).lower():
                    raise
    conn.executescript(
        """CREATE INDEX IF NOT EXISTS ix_cards_released ON cards(released_at, first_seen);
        CREATE INDEX IF NOT EXISTS ix_cards_rating ON cards(rating);
        CREATE TABLE IF NOT EXISTS evaluations (
            card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            base REAL, final REAL, pro_share REAL, pro_score REAL,
            verdict TEXT NOT NULL DEFAULT 'NEUTRAL', value_gap REAL, verdict_reason TEXT NOT NULL DEFAULT '',
            meta_level TEXT, meta_label TEXT, summary TEXT NOT NULL DEFAULT '',
            top_stats TEXT NOT NULL DEFAULT '[]', bonus_playstyles TEXT NOT NULL DEFAULT '[]',
            opinions_count INTEGER NOT NULL DEFAULT 0, config_hash TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL DEFAULT '',
            factor TEXT NOT NULL DEFAULT '',   -- prima parte del riassunto (stats/PlayStyle): il verdetto sul prezzo si rifa' senza rivalutare la carta
            position TEXT NOT NULL DEFAULT ''  -- posizione al momento della valutazione (serve a ricalcolare il vecchio mercato se cambia)
        );
        CREATE INDEX IF NOT EXISTS ix_eval_final ON evaluations(final);
        CREATE TABLE IF NOT EXISTS catalog_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'new',
            summary TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS catalog_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS collection (
            card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            added_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
        );
        CREATE TRIGGER IF NOT EXISTS trg_eval_card_upd AFTER UPDATE OF name, version, position, price, is_sbc, data, pro_score ON cards
        BEGIN UPDATE evaluations SET config_hash = '' WHERE card_id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_eval_op_ins AFTER INSERT ON opinions
        BEGIN UPDATE evaluations SET config_hash = '' WHERE card_id = NEW.card_id; END;
        CREATE TRIGGER IF NOT EXISTS trg_eval_op_upd AFTER UPDATE ON opinions
        BEGIN UPDATE evaluations SET config_hash = '' WHERE card_id IN (OLD.card_id, NEW.card_id); END;
        CREATE TRIGGER IF NOT EXISTS trg_eval_op_del AFTER DELETE ON opinions
        BEGIN UPDATE evaluations SET config_hash = '' WHERE card_id = OLD.card_id; END;"""
    )
    _backfill(conn)


def _backfill(conn: sqlite3.Connection) -> None:
    """Riempie rating e first_seen delle carte gia' presenti (solo dove mancano: rieseguibile senza effetti)."""
    rows = conn.execute("SELECT id, version FROM cards WHERE rating IS NULL").fetchall()
    ups = [(r_, i) for i, v in ((r["id"], r["version"]) for r in rows) if (r_ := rating_from_version(v)) is not None]
    if ups:
        conn.executemany("UPDATE cards SET rating=? WHERE id=?", ups)
    if conn.execute("SELECT 1 FROM cards WHERE first_seen IS NULL LIMIT 1").fetchone():
        conn.execute("""UPDATE cards SET first_seen = COALESCE(
                (SELECT REPLACE(MIN(ts), ' ', 'T') || ':00Z' FROM price_history WHERE card_id = cards.id), ?)
            WHERE first_seen IS NULL""", (now_iso(),))
    conn.commit()
