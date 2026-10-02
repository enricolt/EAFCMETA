import hmac
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from . import db, importer, scoring
from .models import CardIn, ImportIn, ProIn


@asynccontextmanager
async def lifespan(_):
    scoring.load_config()  # fallisce subito se patch.json è rotto
    db.connect().close()
    yield


app = FastAPI(title="EA FC Meta", lifespan=lifespan)


def require_token(x_token: str | None = Header(default=None)):
    """Se EAFCMETA_TOKEN è impostato (es. con start.py --lan) serve l'header X-Token."""
    expected = os.environ.get("EAFCMETA_TOKEN")
    if expected and not (x_token and hmac.compare_digest(x_token, expected)):
        raise HTTPException(401, "token mancante o errato")


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_conn)


def _scored(conn) -> dict:
    cfg = scoring.load_config()
    out = {}
    for r in conn.execute("SELECT * FROM cards"):
        c = db.row_to_card(r)
        try:
            ex = scoring.explain(c, cfg)
        except ValueError:
            continue  # carta non più valida con la config corrente: ignorata
        out[c["id"]] = (c, ex, scoring.final_score(ex["base"], c["pro_score"], cfg))
    return out


def _eval(card_id: int, scored: dict) -> dict:
    cfg = scoring.load_config()
    card, ex, final = scored[card_id]
    market = [(c["price"], f) for i, (c, _, f) in scored.items() if i != card_id and c["position"] == card["position"]]
    v = scoring.verdict(final, card["price"], market, cfg)
    weights = cfg["role_weights"][ex["role"]]
    top = sorted(weights, key=lambda k: (-weights[k], -card["stats"][k]))[:4]
    return {"id": card_id, "name": card["name"], "top_stats": [{"k": k, "v": card["stats"][k]} for k in top],
            "bonus_playstyles": [p for p in card["playstyles"] if p.endswith("+")][:3], "version": card["version"], "position": card["position"],
            "is_sbc": card["is_sbc"], "cost_credits": card["price"],
            "scores": {"base_score": round(ex["base"], 2), "pro_sentiment_score": card["pro_score"],
                       "final_score": round(final, 2)},
            "pro_missing": card["pro_score"] is None, "pro_notes": card["pro_notes"],
            "verdict": v["verdict"], "value_gap": v["value_gap"], "verdict_reason": v["reason"],
            "breakdown": {"role": ex["role"], "stats_meta": ex["stats_meta"], "bonus": ex["bonus"],
                          "unknown_playstyles": ex["unknown_playstyles"]},
            "market_size": len(market)}


def _raw(card: dict) -> dict:
    return {k: card[k] for k in ("stats", "playstyles", "body_type", "weak_foot", "skill_moves")}


@router.get("/cards")
def list_cards(conn: sqlite3.Connection = Conn, position: str | None = None, q: str | None = None,
               limit: int = 500, offset: int = 0):
    scored = _scored(conn)
    out = [_eval(i, scored) for i in scored]
    if position:
        out = [e for e in out if e["position"] == position.upper()]
    if q:
        out = [e for e in out if q.lower() in f"{e['name']} {e['version']}".lower()]
    out.sort(key=lambda e: e["scores"]["final_score"], reverse=True)
    return out[max(offset, 0):max(offset, 0) + min(max(limit, 1), 1000)]


@router.post("/cards", status_code=201)
def add_card(card: CardIn, conn: sqlite3.Connection = Conn):
    cid, status = db.upsert_card(conn, card)
    conn.commit()
    return {"id": cid, "status": status}


def _exists(conn, card_id: int) -> None:
    if conn.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone() is None:
        raise HTTPException(404, "carta non trovata")


@router.get("/cards/{card_id}")
def get_card(card_id: int, conn: sqlite3.Connection = Conn):
    _exists(conn, card_id)
    scored = _scored(conn)
    if card_id not in scored:
        raise HTTPException(422, "carta non valutabile con la configurazione attuale")
    return {**_eval(card_id, scored), "card": _raw(scored[card_id][0]), "price_history": db.history(conn, card_id)}


@router.get("/cards/{card_id}/eval")
def eval_card(card_id: int, conn: sqlite3.Connection = Conn):
    return get_card(card_id, conn)


@router.put("/cards/{card_id}")
def edit_card(card_id: int, card: CardIn, conn: sqlite3.Connection = Conn):
    _exists(conn, card_id)
    try:
        db.update_card(conn, card_id, card)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(409, "esiste già una carta con lo stesso nome, versione e posizione")
    return get_card(card_id, conn)


@router.delete("/cards/{card_id}", status_code=204)
def delete_card(card_id: int, conn: sqlite3.Connection = Conn):
    _exists(conn, card_id)
    conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    conn.commit()


@router.put("/cards/{card_id}/pro")
def set_pro(card_id: int, body: ProIn, conn: sqlite3.Connection = Conn):
    _exists(conn, card_id)
    conn.execute("UPDATE cards SET pro_score=?, pro_notes=? WHERE id=?", (body.pro_score, body.notes, card_id))
    conn.commit()
    return get_card(card_id, conn)


@router.post("/import")
def import_cards(body: ImportIn, conn: sqlite3.Connection = Conn):
    return importer.import_text(conn, body.text, body.dry_run)


@router.post("/prices")
def import_prices(body: ImportIn, conn: sqlite3.Connection = Conn):
    return importer.update_prices(conn, body.text, body.dry_run)


@router.get("/import/template", response_class=PlainTextResponse)
def template():
    return importer.template_csv()


@router.get("/meta")
def meta():
    cfg = scoring.load_config()
    return {"patch_version": cfg["patch_version"], "positions": list(cfg["position_to_role"]),
            "body_types": list(cfg["body_type_bonus"]), "stat_keys": cfg["stat_keys"],
            "role_weights": {pos: cfg["role_weights"][role] for pos, role in cfg["position_to_role"].items()},
            "playstyles": list(cfg["playstyles"])}


app.include_router(router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
