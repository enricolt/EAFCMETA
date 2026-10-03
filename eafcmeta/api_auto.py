"""API della raccolta automatica (router incluso da api.py con una riga, protetto da require_token).

GET /api/v1/auto/status, POST /auto/run (409 se già in corso), PUT /auto/config (poche soglie sicure),
GET /auto/opinions, DELETE /auto/opinions (annulla TUTTI i pareri automatici, mai i manuali).
"""
import importlib.util
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auto import config as acfg, opinions as aop, runner, scheduler
from .auto.errors import AutoConfigError


def service_dep():
    """Servizio di pianificazione (sostituibile nei test con dependency_overrides)."""
    return scheduler.service


class ConfigIn(BaseModel):
    """Solo parametri sicuri e con limiti stretti; il resto si cambia a mano in auto.local.json."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    interval_hours: float | None = Field(None, ge=1, le=24 * 14)
    min_confidence: float | None = Field(None, ge=0.5, le=0.95)
    max_opinions_per_run: int | None = Field(None, ge=0, le=200)
    max_videos_per_run: int | None = Field(None, ge=1, le=100)
    lookback_days: int | None = Field(None, ge=1, le=60)
    request_delay_seconds: float | None = Field(None, ge=2, le=120)
    min_subscribers: int | None = Field(None, ge=0, le=10_000_000)


_WHERE = {"enabled": None, "interval_hours": None, "min_confidence": "thresholds", "min_subscribers": "thresholds",
          "max_opinions_per_run": "limits", "max_videos_per_run": "limits", "lookback_days": "youtube",
          "request_delay_seconds": "youtube"}


def editable(cfg: dict) -> dict:
    return {k: (cfg[s][k] if s else cfg[k]) for k, s in _WHERE.items()}


def build_router(require_token, get_conn) -> APIRouter:
    r = APIRouter(prefix="/api/v1/auto", dependencies=[Depends(require_token)])

    @r.get("/status")
    def status(conn: sqlite3.Connection = Depends(get_conn), svc=Depends(service_dep)):
        try:
            cfg = acfg.load()
        except AutoConfigError as e:
            raise HTTPException(500, str(e))
        last = runner.last_run(conn)
        ls = runner.last_started_epoch(conn)
        env_on = acfg.env_enabled()
        nxt = None
        if cfg["enabled"] and env_on:
            nxt = scheduler.utc_iso(svc.next_due if svc.next_due else (ls + cfg["interval_hours"] * 3600 if ls else None))
        n = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
        return {
            "enabled": cfg["enabled"], "env_enabled": env_on, "active": cfg["enabled"] and env_on,
            "scheduler_running": svc.thread_alive, "running": svc.running,
            "interval_hours": cfg["interval_hours"], "last_run": last, "next_run": nxt,
            "next_run_note": None if nxt else ("spenta" if not (cfg["enabled"] and env_on) else "alla prossima partenza dell'app"),
            "yt_dlp_installed": importlib.util.find_spec("yt_dlp") is not None,
            "counters": {"pareri_automatici": n("SELECT COUNT(*) FROM opinions WHERE auto=1"),
                         "pareri_manuali": n("SELECT COUNT(*) FROM opinions WHERE auto=0"),
                         "canali": n("SELECT COUNT(*) FROM channels"),
                         "video_elaborati": n("SELECT COUNT(*) FROM processed_items"),
                         "scarti_automatici": n("SELECT COUNT(*) FROM proposals WHERE source='auto'")},
            "channels": [dict(x, verified_auto=bool(x["verified_auto"])) for x in conn.execute(
                "SELECT name, channel_id, handle, title, subscribers, discovered_at, verified_auto FROM channels ORDER BY name")],
            "config": editable(cfg)}

    @r.post("/run", status_code=202)
    def run_now(svc=Depends(service_dep)):
        if not svc.run_async():
            raise HTTPException(409, "una raccolta automatica è già in corso")
        return {"started": True}

    @r.put("/config")
    def put_config(body: ConfigIn, svc=Depends(service_dep)):
        patch: dict = {}
        try:
            for k in body.model_fields_set:
                v = getattr(body, k)
                if v is None:
                    continue
                section = _WHERE[k]
                v = acfg.check_value(section, k, v)
                (patch.setdefault(section, {}) if section else patch)[k] = v
            if not patch:
                raise HTTPException(422, "nessun parametro da modificare")
            cfg = acfg.save_local(patch)
        except AutoConfigError as e:
            raise HTTPException(422, str(e))
        if cfg["enabled"]:
            svc.start_if_enabled()
        return {"config": editable(cfg), "enabled": cfg["enabled"]}

    @r.get("/opinions")
    def list_opinions(conn: sqlite3.Connection = Depends(get_conn)):
        return aop.list_auto(conn)

    @r.delete("/opinions")
    def undo_opinions(conn: sqlite3.Connection = Depends(get_conn)):
        return {"removed": aop.undo(conn)}

    return r
