"""API della ricerca automatica dei pareri (router incluso da api.py con una riga).

POST /api/v1/research/paste, /research/youtube; GET /research/proposals; POST /research/proposals/{id}/accept|reject.
Niente finisce in `opinions` senza accettazione esplicita.
"""
import sqlite3
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .research import llm as llm_mod, pipeline, store
from .research.sources import (ConfigError, Item, MissingKeyError, NoCaptionsError, PastedText, QuotaExceededError,
                               ResearchError, YouTubeSource)


def llm_dep():
    """Client del modello (None senza ANTHROPIC_API_KEY). Sostituibile nei test con dependency_overrides."""
    return llm_mod.get_llm()


def youtube_factory_dep() -> Callable[[str], YouTubeSource]:
    """Fabbrica di YouTubeSource per creator. Sostituibile nei test con una finta."""
    return YouTubeSource.for_creator


class PasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    creator: str = Field(min_length=1, max_length=40)
    url: str = Field("", max_length=300)
    card_id: int | None = None
    mode: str = "auto"


class YouTubeIn(BaseModel):
    creator: str = Field(min_length=1, max_length=40)
    query: str = Field(min_length=1, max_length=200)
    mode: str = "auto"


class AcceptIn(BaseModel):
    stance: str | None = None
    score: float | None = None
    reason: str | None = Field(None, max_length=800)
    card_id: int | None = None


def _http(e: Exception) -> HTTPException:
    if isinstance(e, MissingKeyError):
        return HTTPException(503, str(e))
    if isinstance(e, QuotaExceededError):
        return HTTPException(429, str(e))
    if isinstance(e, (NoCaptionsError, ConfigError, ValueError)):
        return HTTPException(422, str(e))
    if isinstance(e, store.ProposalNotFound):
        return HTTPException(404, str(e))
    if isinstance(e, store.ProposalStateError):
        return HTTPException(409, str(e))
    return HTTPException(502, str(e))


def _check_url(url: str) -> None:
    if url and not url.lower().startswith(("http://", "https://")):
        raise ValueError("il link deve iniziare con http:// o https://")


def build_router(require_token, get_conn) -> APIRouter:
    r = APIRouter(prefix="/api/v1/research", dependencies=[Depends(require_token)])

    def run(conn, items, extractor, card_id=None, extra_warnings=()):
        try:
            rep = pipeline.process_items(conn, items, extractor, card_id)
            conn.commit()
        except Exception:
            conn.rollback()  # niente dati parziali
            raise
        rep["warnings"] = list(extra_warnings) + rep["warnings"]
        return rep

    @r.post("/paste")
    def paste(body: PasteIn, conn: sqlite3.Connection = Depends(get_conn), llm=Depends(llm_dep)):
        try:
            _check_url(body.url)
            extractor = pipeline.make_extractor(body.mode, llm)
            return run(conn, PastedText(body.text, body.creator, body.url).search(), extractor, body.card_id)
        except (ResearchError, ValueError) as e:
            raise _http(e)

    @r.post("/youtube")
    def youtube(body: YouTubeIn, conn: sqlite3.Connection = Depends(get_conn), llm=Depends(llm_dep),
                factory=Depends(youtube_factory_dep)):
        try:
            extractor = pipeline.make_extractor(body.mode, llm)
            src = factory(body.creator)
            items = src.search(body.query)  # senza chiave YouTube: 503 prima di qualsiasi altra cosa
            return run(conn, items, extractor, None, getattr(src, "warnings", []))
        except (ResearchError, ValueError) as e:
            raise _http(e)

    @r.get("/proposals")
    def proposals(status: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
        try:
            return store.list_proposals(conn, status)
        except ValueError as e:
            raise _http(e)

    @r.post("/proposals/{proposal_id}/accept")
    def accept(proposal_id: int, body: AcceptIn | None = None, conn: sqlite3.Connection = Depends(get_conn)):
        fields = {k: getattr(body, k) for k in (body.model_fields_set if body else ())}
        try:
            res = store.accept(conn, proposal_id, **fields)
            conn.commit()
            return res
        except (ResearchError, ValueError) as e:
            conn.rollback()
            raise _http(e)

    @r.post("/proposals/{proposal_id}/reject")
    def reject(proposal_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        try:
            res = store.reject(conn, proposal_id)
            conn.commit()
            return res
        except ResearchError as e:
            conn.rollback()
            raise _http(e)

    return r
