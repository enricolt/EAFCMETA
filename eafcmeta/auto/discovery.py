"""Scoperta automatica dei canali YouTube dei pro. Mai scelte a caso: un canale si accetta SOLO se

1. il titolo del canale somiglia al nome (similarità >= soglia in config),
2. ha almeno `min_subscribers` iscritti (se gli iscritti non sono leggibili: rifiutato),
3. tra gli ultimi video ce ne sono almeno `min_fc_videos` che parlano di FC/FIFA.

Altrimenti il nome finisce in `channel_misses` (non si riprova prima di `discovery_retry_days`).
I canali accettati stanno in `channels` e non si cercano più.
"""
import difflib
import re
from datetime import datetime, timedelta, timezone

from ..research import text as T
from .errors import YtVideoError

FC_RE = re.compile(r"\b(ea\s*fc|fc\s?\d{2}|fifa|fut|ultimate team|toty|tots|totw|sbc|rtg|road to glory|pack opening|icon)\b", re.I)
_FILLER = {"official", "ufficiale", "tv", "yt", "channel", "canale", "gaming", "esports", "esport", "fc", "fifa", "ea",
           "clips", "team", "the"}


def _tokens(s: str) -> list[str]:
    return [w for w, _, _ in T.words(T.norm(s))]


def name_similarity(name: str, title: str) -> float:
    """0..1. Ignora maiuscole, accenti e parole 'riempitivo' (official, tv, gaming, team...) su entrambi i lati."""
    a, b = _tokens(name), _tokens(title)
    a2, b2 = [t for t in a if t not in _FILLER] or a, [t for t in b if t not in _FILLER] or b
    sa, sb = "".join(a2), "".join(b2)
    if not sa or not sb:
        return 0.0
    return round(difflib.SequenceMatcher(None, sa, sb).ratio(), 3)


def count_fc_videos(videos) -> int:
    return sum(bool(FC_RE.search(f"{v.title} {v.description}")) for v in videos)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M")


def _ensure_configured(conn, creator: dict, now: datetime) -> bool:
    """Canale già indicato a mano (research.json / pros.json): si registra senza cercare nulla. True se gestito."""
    if not (creator["channel_id"] or creator["handle"]):
        return False
    row = conn.execute("SELECT channel_id, handle, verified_auto FROM channels WHERE name=?", (creator["name"],)).fetchone()
    if row is None:
        conn.execute("INSERT INTO channels (name, channel_id, handle, title, subscribers, discovered_at, verified_auto) "
                     "VALUES (?,?,?,?,NULL,?,0)", (creator["name"], creator["channel_id"], creator["handle"], "", _stamp(now)))
    elif (row["channel_id"], row["handle"]) != (creator["channel_id"], creator["handle"]):
        conn.execute("UPDATE channels SET channel_id=?, handle=?, verified_auto=0 WHERE name=?",
                     (creator["channel_id"], creator["handle"], creator["name"]))
    return True


def _recent_miss(conn, name: str, now: datetime, days: float) -> bool:
    r = conn.execute("SELECT ts FROM channel_misses WHERE name=?", (name,)).fetchone()
    if r is None:
        return False
    try:
        return now - datetime.strptime(r["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) < timedelta(days=days)
    except ValueError:
        return False


def _miss(conn, name: str, reason: str, now: datetime) -> str:
    conn.execute("INSERT INTO channel_misses (name, ts, reason) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                 "ts=excluded.ts, reason=excluded.reason", (name, _stamp(now), reason))
    return reason


def discover_one(conn, yt, creator_name: str, cfg: dict, now: datetime) -> tuple[bool, str]:
    """Cerca e (se sicuro) salva il canale di UN nome. Ritorna (accettato, motivo). Non fa commit.
    StopBatch (blocco/tetto) e YtVideoError risalgono al chiamante: non sono 'canale non trovato'."""
    th, ytc = cfg["thresholds"], cfg["youtube"]
    results = yt.search_videos(f"{creator_name} EA FC", ytc["search_results"])
    best: dict[str, tuple[float, str]] = {}  # channel_id -> (similarità, titolo)
    for e in results:
        cid = str(e.get("channel_id") or "")
        title = str(e.get("channel") or e.get("uploader") or "")
        if not cid or not title:
            continue
        sim = name_similarity(creator_name, title)
        if sim > best.get(cid, (-1, ""))[0]:
            best[cid] = (sim, title)
    if not best:
        return False, _miss(conn, creator_name, "nessun canale nei risultati di ricerca", now)
    cid, (sim, _) = max(best.items(), key=lambda kv: kv[1][0])
    if sim < th["channel_name_similarity"]:
        return False, _miss(conn, creator_name, f"nessun canale con nome abbastanza simile (migliore {sim:.2f} < "
                                                f"{th['channel_name_similarity']:.2f})", now)
    info = yt.channel(channel_id=cid, limit=10)
    sim = name_similarity(creator_name, info.title)  # si ricontrolla sul titolo vero del canale
    if sim < th["channel_name_similarity"]:
        return False, _miss(conn, creator_name, f"titolo del canale '{info.title}' troppo diverso dal nome ({sim:.2f})", now)
    if info.subscribers is None:
        return False, _miss(conn, creator_name, "numero di iscritti non leggibile: canale non accettato", now)
    if info.subscribers < th["min_subscribers"]:
        return False, _miss(conn, creator_name, f"solo {info.subscribers} iscritti (minimo {th['min_subscribers']})", now)
    n_fc = count_fc_videos(info.videos)
    if n_fc < th["min_fc_videos"]:
        return False, _miss(conn, creator_name, f"solo {n_fc} video recenti su FC/FIFA (minimo {th['min_fc_videos']})", now)
    conn.execute("INSERT INTO channels (name, channel_id, handle, title, subscribers, discovered_at, verified_auto) "
                 "VALUES (?,?,?,?,?,?,1) ON CONFLICT(name) DO UPDATE SET channel_id=excluded.channel_id, "
                 "handle=excluded.handle, title=excluded.title, subscribers=excluded.subscribers, "
                 "discovered_at=excluded.discovered_at, verified_auto=1",
                 (creator_name, info.channel_id or cid, info.handle, info.title, info.subscribers, _stamp(now)))
    conn.execute("DELETE FROM channel_misses WHERE name=?", (creator_name,))
    return True, f"canale '{info.title}' accettato (similarità {sim:.2f}, {info.subscribers} iscritti, {n_fc} video FC)"


def discover(conn, yt, cfg: dict, creators: list[dict], counters: dict, now: datetime, log=print) -> None:
    """Registra i canali indicati e scopre quelli mancanti (max `max_channels_discovered_per_run` ricerche per esecuzione).
    Aggiorna `counters` (canali_scoperti, errori). StopBatch risale al chiamante."""
    attempts = 0
    cap = cfg["limits"]["max_channels_discovered_per_run"]
    for c in creators:
        if _ensure_configured(conn, c, now):
            conn.commit()
            continue
        if conn.execute("SELECT 1 FROM channels WHERE name=?", (c["name"],)).fetchone():
            continue
        if _recent_miss(conn, c["name"], now, cfg["youtube"]["discovery_retry_days"]):
            continue
        if attempts >= cap:
            continue
        attempts += 1
        try:
            ok, why = discover_one(conn, yt, c["name"], cfg, now)
            conn.commit()
        except YtVideoError as e:
            conn.rollback()
            counters["errori"].append(f"scoperta '{c['name']}': {e}")
            continue
        except Exception:
            conn.rollback()
            raise
        counters["canali_scoperti"] += ok
        log(f"[scoperta] {c['name']}: {why}")
        counters.setdefault("scoperta", {})[c["name"]] = why
