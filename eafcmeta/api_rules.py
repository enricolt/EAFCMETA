"""API delle regole dichiarative (router incluso da api.py): elenco, approvazione/rifiuto, apprendimento dai pro."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from . import db, rules, scoring


def _auth(x_token: str | None = Header(default=None)):
    """Stessa autenticazione del resto dell'API (api.require_token); import tardivo per evitare il giro di import."""
    from . import api
    api.require_token(x_token)


def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


router = APIRouter(prefix="/api/v1", dependencies=[Depends(_auth)])


def criteria_notes(conn, role: str, cfg: dict) -> list[dict]:
    """Discordanze tra creator a livello di criterio, per il dettaglio carta (vuoto se mancano i dati)."""
    try:
        return rules.criteria_disagreements(conn, role, cfg)
    except sqlite3.Error:
        return []


class StatusIn(BaseModel):
    status: str


@router.get("/rules")
def get_rules():
    cfg = scoring.load_config()
    items = rules.list_rules(cfg)
    counts = {s: sum(r["status"] == s for r in items) for s in rules.STATUSES}
    return {"settings": rules.resolve(cfg)["settings"], "counts": counts, "rules": items}


@router.put("/rules/{rule_id}/status")
def put_status(rule_id: str, body: StatusIn):
    try:
        return rules.set_status(rule_id, body.status, scoring.load_config())
    except KeyError:
        raise HTTPException(404, "regola non trovata")
    except ValueError as e:
        raise HTTPException(409 if "proposte" in str(e) else 422, str(e))


@router.post("/rules/learn")
def post_learn(conn: sqlite3.Connection = Depends(_conn)):
    return rules.learn(conn, scoring.load_config())
