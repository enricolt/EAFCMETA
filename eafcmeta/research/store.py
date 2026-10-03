"""Tabelle `proposals` e `criteria` e revisione umana (accetta/rifiuta).

Niente va in `opinions` senza conferma: accept() chiama db.upsert_opinion e sposta i criteri su opinion_id.
Le funzioni NON fanno commit (come db.py): lo fa il chiamante.
"""
import hashlib
import sqlite3

from pydantic import ValidationError

from .. import db, scoring
from . import text as T
from .sources import ResearchError

_UNSET = object()


class ProposalNotFound(ResearchError):
    pass


class ProposalStateError(ResearchError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crea le tabelle della ricerca (idempotente). Richiamata da db.connect."""
    conn.executescript(
        """CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            creator TEXT NOT NULL COLLATE NOCASE,
            stance TEXT NOT NULL CHECK (stance IN ('yes','maybe','no')),
            score REAL, reason TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'paste', excerpt TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
            ts TEXT NOT NULL,
            dedup_key TEXT NOT NULL, method TEXT NOT NULL DEFAULT 'offline',
            ambiguous INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', group_key TEXT NOT NULL DEFAULT '',
            UNIQUE(dedup_key, card_id, creator)
        );
        CREATE INDEX IF NOT EXISTS ix_proposals_status ON proposals(status, id);
        CREATE TABLE IF NOT EXISTS criteria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opinion_id INTEGER NULL,
            proposal_id INTEGER NULL,
            creator TEXT,
            role TEXT,
            criterion TEXT,
            polarity INTEGER,
            ts TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_criteria_opinion ON criteria(opinion_id);
        CREATE INDEX IF NOT EXISTS ix_criteria_proposal ON criteria(proposal_id);
        CREATE TRIGGER IF NOT EXISTS tr_proposals_del_criteria AFTER DELETE ON proposals
            BEGIN DELETE FROM criteria WHERE proposal_id = OLD.id; END;
        CREATE TRIGGER IF NOT EXISTS tr_opinions_del_criteria AFTER DELETE ON opinions
            BEGIN DELETE FROM criteria WHERE opinion_id = OLD.id; END;"""
    )


def dedup_key(url: str, text: str) -> str:
    """Chiave di deduplica: il link; per i testi incollati senza link, un'impronta del testo."""
    url = (url or "").strip()
    return url if url else "paste:" + hashlib.sha1(T.norm(text).strip().encode()).hexdigest()[:16]


def _role(conn, card_id: int) -> str:
    r = conn.execute("SELECT position FROM cards WHERE id=?", (card_id,)).fetchone()
    return scoring.role_of(r["position"], scoring.load_config()) if r else ""


def _write_criteria(conn, proposal_id: int, creator: str, role: str, crit: list) -> None:
    conn.execute("DELETE FROM criteria WHERE proposal_id=?", (proposal_id,))
    conn.executemany("INSERT INTO criteria (opinion_id, proposal_id, creator, role, criterion, polarity, ts) "
                     "VALUES (NULL,?,?,?,?,?,?)",
                     [(proposal_id, creator, role, k, int(p), db._now()) for k, p in crit])


def add_proposals(conn, proposals: list, source: str, text: str) -> tuple[list[dict], list[dict]]:
    """Salva le proposte (status 'pending'). Duplicati per (url|impronta, carta, creator) ignorati.
    Ritorna (nuove, duplicate) come dict."""
    new, dup = [], []
    for p in proposals:
        key = dedup_key(p.url, text)
        cur = conn.execute(
            "INSERT OR IGNORE INTO proposals (card_id, creator, stance, score, reason, url, source, excerpt, confidence, "
            "status, ts, dedup_key, method, ambiguous, note, group_key) VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)",
            (p.card_id, p.creator, p.stance, p.score, p.reason, p.url, source, p.excerpt, p.confidence, db._now(), key,
             p.method, int(p.ambiguous), p.note, p.group_key))
        if cur.rowcount:
            _write_criteria(conn, cur.lastrowid, p.creator, _role(conn, p.card_id), p.criteria)
            new.append(get_proposal(conn, cur.lastrowid))
        else:
            row = conn.execute("SELECT id FROM proposals WHERE dedup_key=? AND card_id=? AND creator=?",
                               (key, p.card_id, p.creator)).fetchone()
            dup.append(get_proposal(conn, row["id"]))
    return new, dup


def _row(conn, r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in r.keys() if k != "dedup_key"}
    d["ambiguous"] = bool(d["ambiguous"])
    if d["status"] == "pending":
        rows = conn.execute("SELECT criterion, polarity, role FROM criteria WHERE proposal_id=? ORDER BY id", (d["id"],))
    elif d["status"] == "accepted":
        rows = conn.execute("SELECT criterion, polarity, role FROM criteria WHERE opinion_id=(SELECT id FROM opinions "
                            "WHERE card_id=? AND creator=? COLLATE NOCASE) ORDER BY id", (d["card_id"], d["creator"]))
    else:
        rows = []
    d["criteria"] = [dict(c) for c in rows]
    card = conn.execute("SELECT name, version, position FROM cards WHERE id=?", (d["card_id"],)).fetchone()
    d["card"] = dict(card) if card else None
    return d


def get_proposal(conn, pid: int) -> dict:
    r = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    if r is None:
        raise ProposalNotFound("proposta non trovata")
    return _row(conn, r)


def list_proposals(conn, status: str | None = None) -> list[dict]:
    if status is not None and status not in ("pending", "accepted", "rejected"):
        raise ValueError("status deve essere pending, accepted o rejected")
    q, args = "SELECT * FROM proposals", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    return [_row(conn, r) for r in conn.execute(q + " ORDER BY id", args)]


def accept(conn, pid: int, stance=_UNSET, score=_UNSET, reason=_UNSET, card_id=_UNSET) -> dict:
    """Conferma una proposta (con correzioni opzionali) e la trasforma in parere. Non fa commit."""
    from ..models import OpinionIn  # import locale: models importa scoring, evita cicli all'avvio
    p = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise ProposalNotFound("proposta non trovata")
    if p["status"] != "pending":
        raise ProposalStateError(f"la proposta è già {'accettata' if p['status'] == 'accepted' else 'rifiutata'}")
    vals = {"stance": p["stance"] if stance is _UNSET else stance,
            "score": p["score"] if score is _UNSET else score,
            "reason": p["reason"] if reason is _UNSET else reason,
            "card_id": p["card_id"] if card_id is _UNSET else card_id}
    try:
        op = OpinionIn(creator=p["creator"], stance=vals["stance"], score=vals["score"], reason=vals["reason"],
                       url=p["url"])
    except ValidationError as e:
        # stessa validazione di OpinionIn (coerenza voto/scelta, limiti): messaggio chiaro, niente testo tecnico
        raise ValueError("; ".join((f"{'.'.join(map(str, x['loc']))}: " if x["loc"] else "") + x["msg"].removeprefix("Value error, ")
                                   for x in e.errors())) from None
    if conn.execute("SELECT 1 FROM cards WHERE id=?", (vals["card_id"],)).fetchone() is None:
        raise ProposalNotFound("carta non trovata")
    if vals["card_id"] != p["card_id"]:  # correzione della carta: l'eventuale proposta "gemella" (ambiguità) lascia il posto
        other = conn.execute("SELECT id, status FROM proposals WHERE dedup_key=? AND creator=? COLLATE NOCASE AND "
                             "card_id=? AND id<>?", (p["dedup_key"], p["creator"], vals["card_id"], pid)).fetchone()
        if other is not None:
            if other["status"] == "accepted":
                raise ProposalStateError("esiste già un parere accettato per quella carta dallo stesso link e creator")
            conn.execute("DELETE FROM proposals WHERE id=?", (other["id"],))
        conn.execute("UPDATE proposals SET card_id=? WHERE id=?", (vals["card_id"], pid))
    if p["ambiguous"]:  # scelta fatta dall'utente: le alternative ancora in attesa vengono chiuse
        conn.execute("UPDATE proposals SET status='rejected' WHERE status='pending' AND ambiguous=1 AND dedup_key=? "
                     "AND creator=? COLLATE NOCASE AND group_key=? AND id<>?",
                     (p["dedup_key"], p["creator"], p["group_key"], pid))
        conn.execute("DELETE FROM criteria WHERE proposal_id IN (SELECT id FROM proposals WHERE status='rejected')")
    previous = conn.execute("SELECT id FROM opinions WHERE card_id=? AND creator=? COLLATE NOCASE",
                            (vals["card_id"], p["creator"])).fetchone()
    if previous:  # il nuovo parere sostituisce il vecchio: via anche i suoi criteri
        conn.execute("DELETE FROM criteria WHERE opinion_id=?", (previous["id"],))
    db.upsert_opinion(conn, vals["card_id"], op)
    oid = conn.execute("SELECT id FROM opinions WHERE card_id=? AND creator=? COLLATE NOCASE",
                       (vals["card_id"], p["creator"])).fetchone()["id"]
    conn.execute("UPDATE criteria SET opinion_id=?, proposal_id=NULL, role=? WHERE proposal_id=?",
                 (oid, _role(conn, vals["card_id"]), pid))
    conn.execute("UPDATE proposals SET status='accepted', stance=?, score=?, reason=? WHERE id=?",
                 (op.stance, op.score, op.reason, pid))
    return {"proposal": get_proposal(conn, pid), "opinion_id": oid, "replaced": previous is not None}


def reject(conn, pid: int) -> dict:
    p = conn.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise ProposalNotFound("proposta non trovata")
    if p["status"] != "pending":
        raise ProposalStateError(f"la proposta è già {'accettata' if p['status'] == 'accepted' else 'rifiutata'}")
    conn.execute("DELETE FROM criteria WHERE proposal_id=?", (pid,))
    conn.execute("UPDATE proposals SET status='rejected' WHERE id=?", (pid,))
    return get_proposal(conn, pid)
