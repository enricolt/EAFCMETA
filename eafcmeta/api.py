import json
from typing import Annotated

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from . import db, scoring

app = FastAPI(title="EA FC Meta")

def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[object, Depends(get_conn)]


class CardIn(BaseModel):
    name: str
    version: str = ""
    position: str
    price: int = Field(ge=0)
    is_sbc: bool = False
    stats: dict[str, int]

    @field_validator("stats")
    @classmethod
    def _stats_range(cls, v):
        if any(not 1 <= x <= 99 for x in v.values()):
            raise ValueError("le stats devono essere tra 1 e 99")
        return v
    playstyles: list[str] = []
    body_type: str = "Average"
    weak_foot: int = Field(3, ge=1, le=5)
    skill_moves: int = Field(3, ge=1, le=5)


class ProIn(BaseModel):
    pro_score: float = Field(ge=0, le=100)
    notes: str = ""


def _eval(card: dict, scored: dict) -> dict:
    cfg = scoring.load_config()
    _, base, final = scored[card["id"]]
    market = [(c["price"], f) for i, (c, _, f) in scored.items() if i != card["id"] and c["position"] == card["position"]]
    v = scoring.verdict(final, card["price"], market, cfg)
    return {"id": card["id"], "name": card["name"], "version": card["version"], "position": card["position"],
            "cost_credits": card["price"],
            "scores": {"base_score": round(base, 2), "pro_sentiment_score": card["pro_score"],
                       "final_score": round(final, 2)},
            "verdict": v["verdict"], "value_gap": v["value_gap"], "verdict_reason": v["reason"],
            "pro_notes": card["pro_notes"]}


def _eval_all(conn) -> dict[int, dict]:
    cfg = scoring.load_config()
    scored = {}
    for r in conn.execute("SELECT * FROM cards"):
        c = db.row_to_card(r)
        try:
            base = scoring.base_score(c, cfg)
        except ValueError:
            continue  # carta non più valida con la config corrente: ignorata
        scored[c["id"]] = (c, base, scoring.final_score(base, c["pro_score"], cfg))
    return {i: _eval(c, scored) for i, (c, _, _) in scored.items()}


@app.post("/api/v1/cards", status_code=201)
def add_card(card: CardIn, conn: Conn):
    if card.position not in scoring.load_config()["position_to_role"]:
        raise HTTPException(422, f"posizione non supportata: {card.position}")
    try:
        scoring.stats_meta({"position": card.position, "stats": card.stats}, scoring.load_config())
    except ValueError as e:
        raise HTTPException(422, str(e))
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
    out = list(_eval_all(conn).values())
    return sorted(out, key=lambda e: e["scores"]["final_score"], reverse=True)


@app.get("/api/v1/cards/{card_id}/eval")
def eval_card(card_id: int, conn: Conn):
    _get(conn, card_id)
    res = _eval_all(conn).get(card_id)
    if res is None:
        raise HTTPException(422, "carta non valutabile con la configurazione attuale")
    return res


@app.put("/api/v1/cards/{card_id}/pro")
def set_pro(card_id: int, body: ProIn, conn: Conn):
    _get(conn, card_id)
    conn.execute("UPDATE cards SET pro_score = ?, pro_notes = ? WHERE id = ?", (body.pro_score, body.notes, card_id))
    conn.commit()
    return eval_card(card_id, conn)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
