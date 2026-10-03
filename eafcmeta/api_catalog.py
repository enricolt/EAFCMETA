"""API del catalogo (contratto: docs/API_CATALOGO.md). Router incluso da api.py con una riga, protetto da require_token.

GET /catalog, /catalog/facets, /catalog/status; POST /catalog/update (202, 409 se in corso), POST /catalog/cancel,
PUT /catalog/config; scheda secondaria «La mia collezione»: GET /collection, PUT/DELETE /collection/{card_id}.
Le valutazioni vengono dalla cache (catalog/evaluation.py): la lettura non ricalcola mai tutto; se qualche riga e' obsoleta
la ricalcola una volta sola (solo quelle).
"""
import sqlite3
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .catalog import config as catcfg, evaluation, query, updater


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["new", "backfill", "prices"] = "new"
    pages: int | None = Field(None, ge=1, le=catcfg.PAGES_RANGE[1])


class ConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    list_urls: list[str] | None = None
    pages_per_update: int | None = Field(None, ge=catcfg.PAGES_RANGE[0], le=catcfg.PAGES_RANGE[1])
    lookback_days: int | None = Field(None, ge=catcfg.LOOKBACK_RANGE[0], le=catcfg.LOOKBACK_RANGE[1])


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field("", max_length=300)


def service_dep():
    """Servizio di aggiornamento (sostituibile nei test con dependency_overrides)."""
    return updater.service


def build_router(require_token, get_conn) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
    Conn = Depends(get_conn)

    def _list(conn, collection_only, sort, order, limit, offset, **filters):
        evaluation.ensure_fresh(conn)  # solo le righe obsolete, una volta
        try:
            return query.list_items(conn, filters, sort, order, limit, offset, collection_only)
        except query.QueryError as e:
            raise HTTPException(422, str(e))

    def _params(sort, order, limit, offset, q, position, version, rating_min, rating_max, price_min, price_max, verdict, meta,
                has_opinions, released_after, released_before, in_collection):
        return dict(sort=sort, order=order, limit=limit, offset=offset, q=q, position=position, version=version,
                    rating_min=rating_min, rating_max=rating_max, price_min=price_min, price_max=price_max, verdict=verdict,
                    meta=meta, has_opinions=has_opinions, released_after=released_after, released_before=released_before,
                    in_collection=in_collection)

    @router.get("/catalog")
    def catalog(conn: sqlite3.Connection = Conn, sort: str = "released", order: str = "desc",
                limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0), q: str | None = None,
                position: str | None = None, version: str | None = None,
                rating_min: int | None = None, rating_max: int | None = None, price_min: int | None = None, price_max: int | None = None,
                verdict: str | None = None, meta: str | None = None, has_opinions: bool | None = None,
                released_after: str | None = None, released_before: str | None = None, in_collection: bool | None = None):
        return _list(conn, False, **_params(sort, order, limit, offset, q, position, version, rating_min, rating_max, price_min,
                                            price_max, verdict, meta, has_opinions, released_after, released_before, in_collection))

    @router.get("/collection")
    def collection(conn: sqlite3.Connection = Conn, sort: str = "released", order: str = "desc",
                   limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0), q: str | None = None,
                   position: str | None = None, version: str | None = None,
                   rating_min: int | None = None, rating_max: int | None = None, price_min: int | None = None, price_max: int | None = None,
                   verdict: str | None = None, meta: str | None = None, has_opinions: bool | None = None,
                   released_after: str | None = None, released_before: str | None = None):
        out = _list(conn, True, **_params(sort, order, limit, offset, q, position, version, rating_min, rating_max, price_min,
                                          price_max, verdict, meta, has_opinions, released_after, released_before, None))
        return {**out, "stats": query.collection_stats(conn)}

    def _exists(conn, card_id: int) -> None:
        if conn.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone() is None:
            raise HTTPException(404, "carta non trovata")

    @router.put("/collection/{card_id}")
    def collection_add(card_id: int, body: NoteIn | None = Body(default=None), conn: sqlite3.Connection = Conn):
        _exists(conn, card_id)
        note = body.note if body else None
        conn.execute("INSERT INTO collection (card_id, added_at, note) VALUES (?, ?, ?) "
                     "ON CONFLICT(card_id) DO UPDATE SET note = COALESCE(?, note)", (card_id, db.now_iso(), note or "", note))
        conn.commit()
        return {"ok": True, "in_collection": True}

    @router.delete("/collection/{card_id}")
    def collection_remove(card_id: int, conn: sqlite3.Connection = Conn):
        conn.execute("DELETE FROM collection WHERE card_id=?", (card_id,))  # idempotente (anche se la carta non c'e')
        conn.commit()
        return {"ok": True, "in_collection": False}

    @router.get("/catalog/facets")
    def catalog_facets(conn: sqlite3.Connection = Conn):
        return query.facets(conn)

    @router.get("/catalog/status")
    def catalog_status(conn: sqlite3.Connection = Conn, svc: updater.CatalogService = Depends(service_dep)):
        cfg = catcfg.load()
        row = updater.running_row(conn)
        last = updater.last_finished(conn)
        r = conn.execute("SELECT COUNT(*) n, MIN(released_at) oldest, MAX(released_at) newest FROM cards").fetchone()
        evaluated = conn.execute("SELECT COUNT(*) FROM evaluations WHERE final IS NOT NULL").fetchone()[0]
        running = bool(row) or svc.running()
        progress = (row or {}).get("summary", {}).get("progress") if row else None
        return {"cards": r["n"], "evaluated": evaluated, "newest": r["newest"], "oldest": r["oldest"], "running": running,
                "progress": progress if running else None,
                "last_update": ({"started": last["started"], "finished": last["finished"], "status": last["status"],
                                 "mode": last["mode"], "summary": last["summary"]} if last else None),
                "config": catcfg.public(cfg)}

    @router.post("/catalog/update", status_code=202)
    def catalog_update(body: UpdateIn | None = Body(default=None), svc: updater.CatalogService = Depends(service_dep)):
        body = body or UpdateIn()
        try:
            catcfg.load()
        except catcfg.CatalogConfigError as e:
            raise HTTPException(422, str(e))
        if not svc.start(body.mode, body.pages):
            raise HTTPException(409, "un aggiornamento del catalogo è già in corso")
        return {"started": True}

    @router.post("/catalog/cancel")
    def catalog_cancel(svc: updater.CatalogService = Depends(service_dep)):
        svc.cancel()
        return {"ok": True}

    @router.put("/catalog/config")
    def catalog_config(body: ConfigIn):
        try:
            patch = catcfg.check_patch(body.model_dump(exclude_none=True))
            if patch.get("list_urls"):  # l'utente ha indicato (o ribadito) l'indirizzo: la configurazione e' confermata
                patch["setup_confirmed"] = True
            elif "list_urls" in patch:
                patch["setup_confirmed"] = False
            cfg = catcfg.save_local(patch) if patch else catcfg.load()
        except catcfg.CatalogConfigError as e:
            raise HTTPException(422, str(e))
        return catcfg.public(cfg)

    return router
