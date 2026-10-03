"""Elaborazione dei nuovi video dei pro: titolo/descrizione/trascrizione -> carte -> estrazione offline -> pareri sicuri.

Usa l'interfaccia esistente della ricerca: resolver.resolve + pipeline.make_extractor('offline').extract (parole chiave,
nessun modello a pagamento). Niente va in `opinions` se non passa `opinions.decide`. Ogni video è indipendente:
un errore su uno solo non ferma il lotto; un blocco/limite (StopBatch) sì, lasciando intatto quanto già fatto.
"""
from datetime import datetime, timedelta, timezone

from ..research import config as rc, pipeline, resolver, text as T
from . import opinions as AO
from .errors import StopBatch, YtVideoError
from .ytdlp import VideoRef, watch_url

FINAL = ("ok", "nessun_parere", "vecchio", "saltato", "senza_trascrizione")


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M")


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _mark(conn, url: str, creator: str, status: str, note: str, now: datetime) -> None:
    conn.execute("INSERT INTO processed_items (url, creator, ts, status, note) VALUES (?,?,?,?,?) ON CONFLICT(url) DO "
                 "UPDATE SET creator=excluded.creator, ts=excluded.ts, status=excluded.status, note=excluded.note",
                 (url, creator, _stamp(now), status, note[:300]))


def _too_old(date: str, cfg: dict, now: datetime) -> bool:
    try:
        d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False  # data ignota: non si scarta
    return now - d > timedelta(days=cfg["youtube"]["lookback_days"])


def needs_work(row, cfg: dict, now: datetime) -> bool:
    """True se il video non è mai stato elaborato o è da riprovare (errore recente scaduto, trascrizione ancora attesa)."""
    if row is None:
        return True
    ts = _parse_ts(row["ts"])
    waited = ts is None or now - ts >= timedelta(hours=cfg["youtube"]["retry_error_hours"])
    if row["status"] == "errore":
        return waited
    if row["status"] == "senza_trascrizione":
        date = next((p[5:] for p in row["note"].split(";") if p.strip().startswith("data=")), "")
        try:
            d = datetime.strptime(date.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return waited and now - d <= timedelta(days=cfg["youtube"]["retry_no_transcript_days"])
    return False


def extract_best(cards: list[dict], sections: list[str], creator: str, url: str, extractor, low: bool,
                 max_chars: int) -> list[tuple]:
    """[(proposta, certezza della carta)] — una per carta (la più sicura tra le sezioni). `low` = senza trascrizione:
    confidenza dimezzata (come in pipeline.process_items)."""
    best: dict[int, tuple] = {}
    for raw in sections:
        text = T.clean(raw, max_chars)
        if not text:
            continue
        cands = resolver.resolve(text, cards)
        if not cands:
            continue
        cc: dict[int, float] = {}
        for c in cands:
            cc[c.card_id] = max(cc.get(c.card_id, 0.0), c.confidence)
        for p in extractor.extract(text, cands, creator, url).proposals:
            if low:
                p.confidence = round(p.confidence * 0.5, 2)
                p.note = (p.note + "; " if p.note else "") + "fonte senza trascrizione: solo titolo/descrizione/capitoli"
            cur = best.get(p.card_id)
            if cur is None or p.confidence > cur[0].confidence:
                best[p.card_id] = (p, cc.get(p.card_id, 0.0))
    return sorted(best.values(), key=lambda t: -t[0].confidence)


def process_video(conn, yt, ref: VideoRef, creator: str, cards: list[dict], extractor, cfg: dict, counters: dict,
                  now: datetime) -> str:
    """Elabora UN video e ritorna 'ok' | 'vecchio' | ... (stato salvato in processed_items). Fa commit.
    StopBatch risale senza segnare il video (verrà ripreso alla prossima esecuzione)."""
    url = watch_url(ref.id)
    try:
        v = yt.video(ref.id)
    except StopBatch:
        raise
    except YtVideoError as e:
        _mark(conn, url, creator, "errore", str(e), now)
        conn.commit()
        counters["errori"].append(f"{creator}: video {ref.id}: {e}")
        return "errore"
    note_date = f"data={v.date}"
    if v.date and _too_old(v.date, cfg, now):
        _mark(conn, url, creator, "vecchio", note_date, now)
        conn.commit()
        return "vecchio"
    if v.duration is not None and v.duration < cfg["youtube"]["min_duration_seconds"]:
        _mark(conn, url, creator, "saltato", f"{note_date}; video troppo breve (short)", now)
        conn.commit()
        return "saltato"

    th, lim = cfg["thresholds"], cfg["limits"]
    low = v.segments is None
    try:
        AO.forget_discarded(conn, url)
        found = extract_best(cards, yt.sections(v), creator, url, extractor, low, rc.limit("max_text_chars"))
        discarded, per_video = [], 0
        for p, card_conf in found:
            op, why = AO.decide(p, card_conf, th)
            if op is None:
                discarded.append((p, why))
                continue
            if per_video >= th["max_opinions_per_video"]:
                discarded.append((p, f"tetto di {th['max_opinions_per_video']} pareri per video"))
                continue
            if counters["pareri_salvati"] + counters["pareri_aggiornati"] >= lim["max_opinions_per_run"]:
                discarded.append((p, f"tetto di {lim['max_opinions_per_run']} pareri per esecuzione"))
                continue
            res = AO.save_auto(conn, p.card_id, op, p.confidence, v.date, p.criteria)
            if res == AO.SAVED:
                counters["pareri_salvati"] += 1
                per_video += 1
            elif res == AO.UPDATED:
                counters["pareri_aggiornati"] += 1
                per_video += 1
            elif res == AO.PROTECTED:
                counters["pareri_protetti"] += 1  # c'è già un parere manuale dello stesso creator: intatto
            else:
                discarded.append((p, "esiste già un parere automatico più recente"))
        AO.record_discarded(conn, discarded[:20], url)
        counters["pareri_scartati"] += len(discarded)
        status = "senza_trascrizione" if low else ("ok" if found else "nessun_parere")
        _mark(conn, url, creator, status, note_date + ("; " + str(len(found)) + " pareri trovati" if found else ""), now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    counters["video_elaborati"] += 1
    return status


def run_videos(conn, yt, cfg: dict, counters: dict, now: datetime, extractor=None, log=print) -> None:
    """Passa i canali in elenco, dal video più recente: elabora solo i nuovi entro `lookback_days`."""
    extractor = extractor or pipeline.make_extractor("offline")
    cards = pipeline._cards(conn)
    lim = cfg["limits"]
    channels = [dict(r) for r in conn.execute("SELECT name, channel_id, handle FROM channels ORDER BY name")]
    done = 0
    for ch in channels:
        if not (ch["channel_id"] or ch["handle"]):
            continue
        if done >= lim["max_videos_per_run"]:
            counters.setdefault("avvisi", []).append("tetto di video per esecuzione raggiunto")
            break
        try:
            info = yt.channel(ch["channel_id"], ch["handle"], limit=lim["max_videos_per_channel"])
        except YtVideoError as e:
            counters["errori"].append(f"{ch['name']}: elenco video: {e}")
            continue
        refs = info.videos[: lim["max_videos_per_channel"]]
        for i, ref in enumerate(refs):
            url = watch_url(ref.id)
            if not needs_work(conn.execute("SELECT * FROM processed_items WHERE url=?", (url,)).fetchone(), cfg, now):
                continue
            if ref.date and _too_old(ref.date, cfg, now):  # l'elenco è dal più recente: i successivi sono più vecchi
                for r in refs[i:]:
                    _mark(conn, watch_url(r.id), ch["name"], "vecchio", f"data={r.date}", now)
                conn.commit()
                break
            if ref.duration is not None and ref.duration < cfg["youtube"]["min_duration_seconds"]:
                _mark(conn, url, ch["name"], "saltato", f"data={ref.date}; video troppo breve (short)", now)
                conn.commit()
                continue
            if done >= lim["max_videos_per_run"]:
                break
            try:
                st = process_video(conn, yt, ref, ch["name"], cards, extractor, cfg, counters, now)
            except StopBatch:
                raise
            except Exception as e:  # noqa: BLE001 - un video difettoso non ferma il lotto
                counters["errori"].append(f"{ch['name']}: video {ref.id}: errore interno ({type(e).__name__})")
                _mark(conn, url, ch["name"], "errore", f"errore interno ({type(e).__name__})", now)
                conn.commit()
                continue
            log(f"[video] {ch['name']} {ref.id}: {st}")
            done += st != "errore"
            if st == "vecchio":  # data vera scoperta solo ora: anche i successivi sono più vecchi
                for r in refs[i + 1:]:
                    _mark(conn, watch_url(r.id), ch["name"], "vecchio", "dopo un video fuori finestra", now)
                conn.commit()
                break
