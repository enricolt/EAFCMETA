import json
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def db_path() -> str:
    return os.environ.get("EAFCMETA_DB") or str(ROOT / "eafcmeta.db")


_SCHEMA_DONE: set[str] = set()  # file gia' preparati in questo processo (DDL e indice unico una volta sola)
_SCHEMA_LOCK = threading.Lock()


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path()
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    with _SCHEMA_LOCK:
        if path in _SCHEMA_DONE and (path == ":memory:" or os.path.exists(path)):
            return conn
        _prepare(conn)
        _SCHEMA_DONE.add(path)
    return conn


def _prepare(conn: sqlite3.Connection) -> None:
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
        CREATE INDEX IF NOT EXISTS ix_hist_card ON price_history(card_id, id);
        CREATE TABLE IF NOT EXISTS opinions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            creator TEXT NOT NULL COLLATE NOCASE, stance TEXT NOT NULL, score REAL,
            reason TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL,
            UNIQUE(card_id, creator)
        );"""
    )
    try:  # chiave naturale: stessa carta = stesso nome+versione+posizione (db vecchi con doppioni: si salta)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_card ON cards(name COLLATE NOCASE, version COLLATE NOCASE, position)")
    except sqlite3.IntegrityError:
        pass
    from .research.store import ensure_schema; ensure_schema(conn)  # tabelle proposals/criteria (ricerca pareri)


# Dati opzionali letti dai siti, dentro il JSON "data" (nessuna colonna in più). Carte vecchie: valgono None / [].
EXTRA_KEYS = ("height_cm", "weight_kg", "accelerate", "foot", "club", "league", "nation", "age", "chem_style_top")


def _merge_extra(old: dict, card) -> dict:
    """Unisce i dati opzionali: il nuovo vince solo se presente; i ruoli si sostituiscono per sito (FUT.GG / FUTBIN)."""
    out = {k: v if (v := getattr(card, k, None)) is not None else old.get(k) for k in EXTRA_KEYS}
    new_roles = [r.model_dump(exclude_none=True) for r in getattr(card, "roles", [])]
    if new_roles:
        sites = {r.get("site") for r in new_roles}
        out["roles"] = [r for r in old.get("roles", []) if r.get("site") not in sites] + new_roles
    else:
        out["roles"] = old.get("roles", [])
    return {k: v for k, v in out.items() if v not in (None, [])}


def row_to_card(r: sqlite3.Row) -> dict:
    d = json.loads(r["data"])
    return {"id": r["id"], "name": r["name"], "version": r["version"], "position": r["position"],
            "price": r["price"], "is_sbc": bool(r["is_sbc"]), "pro_score": r["pro_score"],
            "pro_notes": r["pro_notes"], **{k: None for k in EXTRA_KEYS}, "roles": [], **d}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _add_history(conn, card_id: int, price: int) -> None:
    conn.execute("INSERT INTO price_history (card_id, price, ts) VALUES (?,?,?)", (card_id, price, _now()))


def _words(name: str) -> set[str]:
    """Parole del nome senza accenti, maiuscole e punteggiatura: 'Vinícius Júnior' = 'vinicius junior'."""
    t = unicodedata.normalize("NFKD", name or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    return set(re.sub(r"[^\w]+", " ", t).split())


MERGE_DEFAULTS = {"min_common_stats": 5, "stat_tolerance": 2, "min_stat_share": 0.9}


def _same_stats(a: dict, b: dict, cfg: dict) -> bool:
    """Stats quasi uguali: almeno `min_stat_share` delle stat in comune entro +-`stat_tolerance` (min `min_common_stats`)."""
    common = [k for k in a if k in b]
    if len(common) < cfg["min_common_stats"]:
        return False
    close = sum(abs(a[k] - b[k]) <= cfg["stat_tolerance"] for k in common)
    return close / len(common) >= cfg["min_stat_share"]


def _norm(v) -> str:
    return " ".join(sorted(_words(v))) if isinstance(v, str) else ""


def _same_origin(new, old: dict) -> bool:
    """Stesso club/nazione, quando disponibili da entrambe le parti: tutte le coppie note devono coincidere."""
    pairs = [(_norm(getattr(new, k, None)), _norm(old.get(k))) for k in ("club", "nation")]
    pairs = [(x, y) for x, y in pairs if x and y]
    return bool(pairs) and all(x == y for x, y in pairs)


def find_card(conn, card) -> sqlite3.Row | None:
    """Stessa carta: nome+versione+posizione uguali (senza accenti/maiuscole); oppure stesso ruolo/versione e nome
    contenuto nell'altro ('Kelly' su FUTBIN = 'Chloe Kelly' su FUT.GG), ma SOLO con una prova che e' lo stesso giocatore:
    stats quasi uguali oppure stesso club/nazione. Senza prova (es. elenchi, che non hanno le stats) niente fusione,
    cosi' 'Silva' non si fonde con 'Bernardo Silva'. Il candidato deve essere uno solo."""
    row = conn.execute("SELECT id, name, price FROM cards WHERE name = ? COLLATE NOCASE AND version = ? COLLATE NOCASE "
                       "AND position = ?", (card.name, card.version, card.position)).fetchone()
    if row is not None:
        return row
    mine = _words(card.name)
    if not mine:
        return None
    rows = list(conn.execute("SELECT id, name, price, data FROM cards WHERE version = ? COLLATE NOCASE AND position = ?",
                             (card.version, card.position)))
    same = [r for r in rows if _words(r["name"]) == mine]  # stesso nome a meno di accenti/punteggiatura
    if len(same) == 1:
        return same[0]
    try:
        from . import scoring
        cfg = {**MERGE_DEFAULTS, **scoring.load_config().get("merge", {})}
    except Exception:  # noqa: BLE001 - config non leggibile: valori di default
        cfg = MERGE_DEFAULTS
    stats = getattr(card, "stats", None) or {}
    cands = []
    for r in rows:
        w = _words(r["name"])
        if not w or not (mine <= w or w <= mine):
            continue
        old = json.loads(r["data"])
        if (stats and _same_stats(stats, old.get("stats") or {}, cfg)) or _same_origin(card, old):
            cands.append(r)
    return cands[0] if len(cands) == 1 else None


def upsert_card(conn, card) -> tuple[int, str]:
    """Inserisce o aggiorna (chiave: nome+versione+posizione). Non fa commit. Ritorna (id, 'new'|'updated')."""
    row = find_card(conn, card)
    old = json.loads(conn.execute("SELECT data FROM cards WHERE id=?", (row["id"],)).fetchone()["data"]) if row else {}
    data = json.dumps({"stats": card.stats, "playstyles": card.playstyles, "body_type": card.body_type,
                       "weak_foot": card.weak_foot, "skill_moves": card.skill_moves,
                       "signals": {**old.get("signals", {}), **card.signals},  # i segnali dei due siti si sommano
                       **_merge_extra(old, card)})
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
    old = conn.execute("SELECT price, data FROM cards WHERE id = ?", (card_id,)).fetchone()
    signals = card.signals or (json.loads(old["data"]).get("signals", {}) if old else {})
    data = json.dumps({"stats": card.stats, "playstyles": card.playstyles, "body_type": card.body_type,
                       "weak_foot": card.weak_foot, "skill_moves": card.skill_moves, "signals": signals,
                       **_merge_extra(json.loads(old["data"]) if old else {}, card)})  # la modifica non cancella i dati dei siti
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


def opinions_by_card(conn, card_id: int | None = None) -> dict[int, list[dict]]:
    q, args = "SELECT * FROM opinions", ()
    if card_id is not None:
        q, args = q + " WHERE card_id = ?", (card_id,)
    out: dict[int, list[dict]] = {}
    for r in conn.execute(q + " ORDER BY id", args):
        out.setdefault(r["card_id"], []).append({k: r[k] for k in ("id", "creator", "stance", "score", "reason", "url", "ts")})
    return out


def upsert_opinion(conn, card_id: int, o) -> None:
    """Un solo parere per creator e carta: se esiste, lo aggiorna. Non fa commit."""
    conn.execute(
        "INSERT INTO opinions (card_id, creator, stance, score, reason, url, ts) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(card_id, creator) DO UPDATE SET stance=excluded.stance, score=excluded.score, "
        "reason=excluded.reason, url=excluded.url, ts=excluded.ts",
        (card_id, o.creator, o.stance, o.score, o.reason, o.url, _now()))
