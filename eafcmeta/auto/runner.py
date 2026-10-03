"""Una esecuzione completa della raccolta automatica, registrata in `auto_runs`.

Passi (ognuno indipendente: se uno fallisce o viene fermato, gli altri proseguono):
1. carte e prezzi da FUT.GG; 2. scoperta dei canali dei pro; 3. lettura dei nuovi video e pareri sicuri.
Il blocco contro le esecuzioni sovrapposte sta nel DB (riga 'in_corso'), quindi vale anche tra l'app e la riga di comando.
"""
import json
from datetime import datetime, timedelta, timezone

from .. import db
from . import config, discovery, futgg, videos
from .errors import AutoError, StopBatch
from .ytdlp import YtDlp

STALE_HOURS = 3  # una riga 'in_corso' più vecchia è un'esecuzione interrotta (arresto improvviso)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M")


def new_counters() -> dict:
    return {"canali_scoperti": 0, "video_elaborati": 0, "pareri_salvati": 0, "pareri_aggiornati": 0, "pareri_scartati": 0,
            "pareri_protetti": 0, "carte_nuove": 0, "prezzi_aggiornati": 0, "errori": [], "fermato": {}}


def begin_run(conn, now: datetime) -> int | None:
    """Registra l'inizio. None se un'altra esecuzione è in corso (da meno di STALE_HOURS ore)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cutoff = _stamp(now - timedelta(hours=STALE_HOURS))
        conn.execute("UPDATE auto_runs SET status='interrotto', finished=? WHERE status='in_corso' AND started<?",
                     (_stamp(now), cutoff))
        if conn.execute("SELECT 1 FROM auto_runs WHERE status='in_corso'").fetchone():
            conn.rollback()
            return None
        cur = conn.execute("INSERT INTO auto_runs (started, status, summary) VALUES (?, 'in_corso', '{}')", (_stamp(now),))
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise


def is_running(conn, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    cutoff = _stamp(now - timedelta(hours=STALE_HOURS))
    return conn.execute("SELECT 1 FROM auto_runs WHERE status='in_corso' AND started>=?", (cutoff,)).fetchone() is not None


def last_run(conn) -> dict | None:
    r = conn.execute("SELECT * FROM auto_runs ORDER BY id DESC LIMIT 1").fetchone()
    return _run_row(r) if r else None


def _run_row(r) -> dict:
    try:
        summary = json.loads(r["summary"])
    except (ValueError, TypeError):
        summary = {}
    return {"id": r["id"], "started": r["started"], "finished": r["finished"], "status": r["status"], "summary": summary}


def last_started_epoch(conn) -> float | None:
    r = conn.execute("SELECT started FROM auto_runs ORDER BY id DESC LIMIT 1").fetchone()
    if r is None:
        return None
    try:
        return datetime.strptime(r["started"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _finish(conn, run_id: int, status: str, summary: dict) -> None:
    conn.execute("UPDATE auto_runs SET finished=?, status=?, summary=? WHERE id=?",
                 (_stamp(datetime.now(timezone.utc)), status, json.dumps(summary, ensure_ascii=False), run_id))
    conn.commit()


def make_yt(cfg: dict, runner=None, sleep=None) -> YtDlp:
    from ..research import config as rc
    y = cfg["youtube"]
    kw = {"sleep": sleep} if sleep else {}
    return YtDlp(runner=runner, delay=y["request_delay_seconds"], timeout=y["timeout_seconds"],
                 max_requests=cfg["limits"]["max_requests_per_run"], max_consecutive_errors=y["max_consecutive_errors"],
                 languages=y["languages"], words_per_line=y["words_per_line"], max_chars=rc.limit("max_text_chars"), **kw)


def run_once(conn_factory=db.connect, cfg: dict | None = None, yt: YtDlp | None = None, fut_fetch=None, robots=None,
             now: datetime | None = None, log=print) -> dict:
    """Esegue tutto e ritorna {'id', 'status', 'summary'}. status: ok | parziale | errore | saltata (già in corso)."""
    cfg = cfg or config.load()
    now = now or datetime.now(timezone.utc)
    conn = conn_factory()
    run_id = None
    try:
        run_id = begin_run(conn, now)
        if run_id is None:
            return {"id": None, "status": "saltata", "summary": {"motivo": "un'altra esecuzione è già in corso"}}
        c = new_counters()
        crashes, steps = 0, 0

        def step(name, enabled, fn):
            nonlocal crashes, steps
            if not enabled:
                return
            steps += 1
            try:
                fn()
            except StopBatch as e:
                c["fermato"][name] = str(e)
                log(f"[{name}] FERMATO: {e}")
            except Exception as e:  # noqa: BLE001 - un passo che crolla non deve fermare gli altri
                conn.rollback()
                crashes += 1
                c["errori"].append(f"{name}: {e}" if isinstance(e, AutoError) else f"{name}: errore interno ({type(e).__name__}: {e})")
                log(f"[{name}] ERRORE: {e}")

        step("futgg", cfg["futgg"]["enabled"], lambda: futgg.update(conn, cfg, c, fut_fetch, robots, log))
        state = {}
        if cfg["youtube"]["enabled"]:
            state["yt"] = yt or make_yt(cfg)
            creators, warns = config.creators()
            c["errori"] += warns
            step("youtube_scoperta", True, lambda: discovery.discover(conn, state["yt"], cfg, creators, c, now, log))
            if "youtube_scoperta" in c["fermato"]:  # YouTube ha detto basta: niente lettura video in questa esecuzione
                c["fermato"]["youtube_video"] = "saltato: " + c["fermato"]["youtube_scoperta"]
            else:
                step("youtube_video", True, lambda: videos.run_videos(conn, state["yt"], cfg, c, now, log=log))
            c["richieste_youtube"] = state["yt"].requests
        c["errori_totali"] = len(c["errori"])
        c["errori"] = c["errori"][:15]
        status = "errore" if steps and crashes == steps else "parziale" if (c["errori_totali"] or c["fermato"]) else "ok"
        _finish(conn, run_id, status, c)
        return {"id": run_id, "status": status, "summary": c}
    except Exception as e:  # noqa: BLE001 - mai lasciare la riga 'in_corso'
        conn.rollback()
        if run_id is not None:
            _finish(conn, run_id, "errore", {"errori": [f"{type(e).__name__}: {e}"[:300]]})
        raise
    finally:
        conn.close()
