"""Pareri AUTOMATICI: decisione di sicurezza, salvataggio protetto, scarti tracciati, annullamento in blocco.

Regole (tutte in config/auto.json -> thresholds):
- una proposta diventa parere solo se: carta NON ambigua, carta riconosciuta con certezza >= min_card_confidence,
  confidenza >= min_confidence (per i video senza trascrizione la confidenza è già dimezzata a monte),
  motivo non vuoto, voto coerente con sì/dipende/no (validazione di OpinionIn);
- un parere per creator e carta: un parere MANUALE (auto=0) non si tocca mai; un parere automatico si aggiorna solo con
  un video più recente (o di pari data);
- le proposte non sicure NON vanno in `opinions`: restano in `proposals` come 'rejected' con nota "scartata_auto: motivo".
"""
import sqlite3

from pydantic import ValidationError

from .. import db
from ..models import OpinionIn
from ..research import store

SAVED, UPDATED, PROTECTED, OLDER = "salvato", "aggiornato", "protetto", "piu_vecchio"


def decide(p, card_conf: float, th: dict) -> tuple[OpinionIn | None, str]:
    """(OpinionIn, '') se la proposta è sicura, altrimenti (None, motivo dello scarto)."""
    if p.ambiguous:
        return None, "carta ambigua (più carte possibili per la stessa menzione)"
    if card_conf < th["min_card_confidence"]:
        return None, f"carta riconosciuta con poca certezza ({card_conf:.2f} < {th['min_card_confidence']:.2f})"
    if p.confidence < th["min_confidence"]:
        return None, f"confidenza {p.confidence:.2f} sotto la soglia {th['min_confidence']:.2f}"
    if not (p.reason or "").strip():
        return None, "nessun motivo estratto"
    try:
        op = OpinionIn(creator=p.creator, stance=p.stance, score=p.score, reason=p.reason, url=p.url)
    except ValidationError as e:
        why = "; ".join(x["msg"].removeprefix("Value error, ") for x in e.errors())
        return None, f"parere non valido: {why}"
    return op, ""


def save_auto(conn: sqlite3.Connection, card_id: int, op: OpinionIn, confidence: float, src_date: str,
              criteria: list) -> str:
    """Salva un parere automatico rispettando la protezione dei manuali. Non fa commit. Ritorna SAVED/UPDATED/PROTECTED/OLDER."""
    row = conn.execute("SELECT id, auto, src_date FROM opinions WHERE card_id=? AND creator=? COLLATE NOCASE",
                       (card_id, op.creator)).fetchone()
    if row is None:
        cur = conn.execute("INSERT INTO opinions (card_id, creator, stance, score, reason, url, ts, auto, confidence, src_date) "
                           "VALUES (?,?,?,?,?,?,?,1,?,?)", (card_id, op.creator, op.stance, op.score, op.reason, op.url,
                                                           db._now(), confidence, src_date))
        oid, result = cur.lastrowid, SAVED
    elif not row["auto"]:
        return PROTECTED
    elif src_date and row["src_date"] and src_date < row["src_date"]:
        return OLDER
    else:
        conn.execute("UPDATE opinions SET stance=?, score=?, reason=?, url=?, ts=?, confidence=?, src_date=? WHERE id=?",
                     (op.stance, op.score, op.reason, op.url, db._now(), confidence, src_date, row["id"]))
        oid, result = row["id"], UPDATED
    conn.execute("DELETE FROM criteria WHERE opinion_id=?", (oid,))
    role = store._role(conn, card_id)
    conn.executemany("INSERT INTO criteria (opinion_id, proposal_id, creator, role, criterion, polarity, ts) "
                     "VALUES (?,NULL,?,?,?,?,?)", [(oid, op.creator, role, k, int(pol), db._now()) for k, pol in criteria])
    return result


def record_discarded(conn: sqlite3.Connection, items: list, text_hint: str = "") -> int:
    """items: [(proposta, motivo)]. Le salva in `proposals` come 'rejected' (source 'auto', nota 'scartata_auto: ...')."""
    n = 0
    for p, why in items:
        p.note = f"scartata_auto: {why}"
        new, _dup = store.add_proposals(conn, [p], "auto", text_hint)
        for r in new:
            conn.execute("UPDATE proposals SET status='rejected' WHERE id=?", (r["id"],))
            conn.execute("DELETE FROM criteria WHERE proposal_id=?", (r["id"],))
            n += 1
    return n


def forget_discarded(conn: sqlite3.Connection, url: str) -> None:
    """Prima di rielaborare un video (es. è comparsa la trascrizione) si tolgono i vecchi scarti automatici dello stesso link."""
    conn.execute("DELETE FROM proposals WHERE dedup_key=? AND source='auto' AND status='rejected'", (url,))


def undo(conn: sqlite3.Connection) -> int:
    """Annulla TUTTI i pareri automatici (mai i manuali). Fa commit. I criteri collegati cadono con un trigger.
    I video già letti restano 'elaborati': non vengono rimessi al prossimo giro."""
    n = conn.execute("DELETE FROM opinions WHERE auto=1").rowcount
    conn.commit()
    return n


def list_auto(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT o.id, o.card_id, c.name AS card_name, c.version AS card_version, c.position AS card_position, "
        "o.creator, o.stance, o.score, o.reason, o.url, o.confidence, o.src_date, o.ts FROM opinions o "
        "JOIN cards c ON c.id=o.card_id WHERE o.auto=1 ORDER BY o.id DESC")
    return [dict(r) for r in rows]
