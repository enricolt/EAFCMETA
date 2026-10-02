import json
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import db, scoring

app = FastAPI(title="EA FC Meta")
_conn = None


def get_conn():
    global _conn
    if _conn is None:
        _conn = db.connect()
    return _conn


Conn = Annotated[object, Depends(get_conn)]


class CardIn(BaseModel):
    name: str
    version: str = ""
    position: str
    price: int = Field(ge=0)
    is_sbc: bool = False
    stats: dict[str, int]
    playstyles: list[str] = []
    body_type: str = "Average"
    weak_foot: int = Field(3, ge=1, le=5)
    skill_moves: int = Field(3, ge=1, le=5)


class ProIn(BaseModel):
    pro_score: float = Field(ge=0, le=100)
    notes: str = ""


def _eval(card: dict, conn) -> dict:
    cfg = scoring.load_config()
    try:
        base = scoring.base_score(card, cfg)
    except ValueError as e:
        raise HTTPException(422, str(e))
    final = scoring.final_score(base, card["pro_score"], cfg)
    market = [(c["price"], scoring.final_score(scoring.base_score(c, cfg), c["pro_score"], cfg))
              for c in (db.row_to_card(r) for r in conn.execute("SELECT * FROM cards WHERE id != ? AND position = ?",
                                                                 (card["id"], card["position"])))]
    v = scoring.verdict(final, card["price"], market, cfg)
    return {"id": card["id"], "name": card["name"], "version": card["version"], "position": card["position"],
            "cost_credits": card["price"],
            "scores": {"base_score": round(base, 2), "pro_sentiment_score": card["pro_score"],
                       "final_score": round(final, 2)},
            "verdict": v["verdict"], "value_gap": v["value_gap"], "verdict_reason": v["reason"],
            "pro_notes": card["pro_notes"]}


@app.post("/api/v1/cards", status_code=201)
def add_card(card: CardIn, conn: Conn):
    if card.position not in scoring.load_config()["position_to_role"]:
        raise HTTPException(422, f"posizione non supportata: {card.position}")
    data = card.model_dump(exclude={"name", "version", "position", "price", "is_sbc"})
    cur = conn.execute("INSERT INTO cards (name, version, position, price, is_sbc, data) VALUES (?,?,?,?,?,?)",
                       (card.name, card.version, card.position, card.price, int(card.is_sbc), json.dumps(data)))
    conn.commit()
    return {"id": cur.lastrowid}


def _get(conn, card_id: int) -> dict:
    r = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "carta non trovata")
    return db.row_to_card(r)


@app.get("/api/v1/cards")
def list_cards(conn: Conn):
    cards = [db.row_to_card(r) for r in conn.execute("SELECT * FROM cards ORDER BY id DESC")]
    out = [_eval(c, conn) for c in cards]
    return sorted(out, key=lambda e: e["scores"]["final_score"], reverse=True)


@app.get("/api/v1/cards/{card_id}/eval")
def eval_card(card_id: int, conn: Conn):
    return _eval(_get(conn, card_id), conn)


@app.put("/api/v1/cards/{card_id}/pro")
def set_pro(card_id: int, body: ProIn, conn: Conn):
    _get(conn, card_id)
    conn.execute("UPDATE cards SET pro_score = ?, pro_notes = ? WHERE id = ?", (body.pro_score, body.notes, card_id))
    conn.commit()
    return _eval(_get(conn, card_id), conn)
