"""Test della raccolta automatica. Nessuna rete: yt-dlp, FUT.GG, robots, orologio e lavori sono finti."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import ST

from eafcmeta import api_auto, db
from eafcmeta.api import app
from eafcmeta.auto import config as acfg, discovery, futgg, opinions as AO, runner, scheduler, transcript as TR, videos, ytdlp
from eafcmeta.auto.errors import AutoConfigError, RequestCapReached, YtBlocked, YtDlpMissing, YtVideoError
from eafcmeta.models import CardIn, OpinionIn
from test_collect import gg_page, list_page

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

# testi: i video di test usano frasi che l'estrazione a parole chiave riconosce con sicurezza
STRONG = ["Mbappé TOTY è una bestia", "scatto devastante e finalizzazione top", "lo consiglio assolutamente è meta",
          "voto 9/10 prendetelo subito"]
STRONG_NO = ["Per me Mbappé TOTY non vale il prezzo", "troppo caro e l'animazione è legnosa", "evitatelo sconsiglio"]
WEAK = ["oggi parliamo di Ronaldo", "è fortissimo ottima finalizzazione"]


# ---------------------------------------------------------------- finti ----

def json3(lines, step=5000):
    return json.dumps({"events": [{"tStartMs": i * step, "dDurationMs": step, "segs": [{"utf8": l}]}
                                  for i, l in enumerate(lines)]})


class FakeYouTube:
    """Runner finto di yt-dlp. search: {query: [entry]}; channels: {id|@handle: dict}; videos: {id: info}; subs: {id: json3}."""

    def __init__(self):
        self.calls, self.search, self.channels, self.videos, self.subs = [], {}, {}, {}, {}
        self.block_after = None  # dopo N chiamate risponde 429

    def __call__(self, args, timeout):
        self.calls.append(args)
        last = args[-1]
        if self.block_after is not None and len(self.calls) > self.block_after:
            return ytdlp.RunResult(1, "", "ERROR: unable to download: HTTP Error 429: Too Many Requests")
        if last.startswith("ytsearch"):
            return ytdlp.RunResult(0, json.dumps({"entries": self.search.get(last.split(":", 1)[1], [])}))
        if "--write-subs" in args or "--write-auto-subs" in args:
            vid = last.split("v=")[1]
            out = Path(args[args.index("-o") + 1]).parent
            (out / f"{vid}.it.json3").write_text(self.subs[vid], encoding="utf-8")
            return ytdlp.RunResult(0)
        if "/videos" in last:
            key = last.split("/channel/")[1].split("/")[0] if "/channel/" in last else last.split("youtube.com/")[1].split("/")[0]
            if key not in self.channels:
                return ytdlp.RunResult(1, "", "ERROR: The channel does not exist")
            return ytdlp.RunResult(0, json.dumps(self.channels[key]))
        vid = last.split("v=")[1]
        if vid not in self.videos:
            return ytdlp.RunResult(1, "", "ERROR: [youtube] %s: Video unavailable" % vid)
        return ytdlp.RunResult(0, json.dumps(self.videos[vid]))

    def n(self, needle):
        return sum(any(needle in a for a in c) for c in self.calls)


def yt_of(fake, **kw):
    kw.setdefault("delay", 0)
    return ytdlp.YtDlp(runner=fake, sleep=lambda _s: None, **kw)


def cfg_of(**over):
    c = acfg.load()
    c["youtube"]["request_delay_seconds"] = 0
    c["futgg"]["delay_seconds"] = 0
    c["futgg"]["enabled"] = False
    for sec, vals in over.items():
        c[sec].update(vals)
    return c


FULL = {**ST, "balance": 80, "dribbling": 88, "crossing": 82, "short_passing": 84}


def add_card(conn, name, version="TOTY", position="ST", **kw):
    return db.upsert_card(conn, CardIn(name=name, version=version, position=position, price=100000, stats=dict(FULL), **kw))[0]


def info(vid, title="Prova", date="20261001", duration=900, subs_auto=True, chapters=None, **kw):
    d = {"id": vid, "title": title, "description": "", "upload_date": date, "duration": duration, "channel": "Exeed Esports",
         "language": "it", "automatic_captions": {"it-orig": [{"ext": "json3"}]} if subs_auto else {}, "subtitles": {}}
    if chapters:
        d["chapters"] = chapters
    d.update(kw)
    return d


def chan(title="Exeed Esports", subs=50000, videos=(), cid="UC1"):
    return {"channel": title, "channel_id": cid, "channel_follower_count": subs, "uploader_id": "@exeed",
            "entries": [{"id": i, "title": t, "duration": 600} for i, t in videos]}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("EAFCMETA_DB", str(tmp_path / "a.db"))
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def world(conn, monkeypatch):
    """Una carta (Mbappé TOTY), un canale già registrato con un video e una trascrizione forte."""
    cid = add_card(conn, "Kylian Mbappé")
    conn.execute("INSERT INTO channels (name, channel_id, handle, title, subscribers, discovered_at, verified_auto) "
                 "VALUES ('Exeed','UC1','','Exeed Esports',50000,'2026-10-01 10:00',1)")
    conn.commit()
    fake = FakeYouTube()
    fake.channels["UC1"] = chan(videos=[("vid1", "Mbappé TOTY review")])
    fake.videos["vid1"] = info("vid1", "Mbappé TOTY review")
    fake.subs["vid1"] = json3(STRONG)
    monkeypatch.setattr(acfg, "creators", lambda: ([], []))
    return conn, fake, cid


def go(conn, fake, cfg=None, **kw):
    cfg = cfg or cfg_of()
    return runner.run_once(lambda: db.connect(), cfg=cfg, yt=yt_of(fake), now=NOW, log=lambda *_: None, **kw)


def opinion_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM opinions ORDER BY id")]


# ------------------------------------------------------------- migrazione ----

def test_migration_is_idempotent_and_keeps_old_rows(tmp_path):
    p = str(tmp_path / "old.db")
    old = sqlite3.connect(p)
    old.executescript("""CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
        position TEXT NOT NULL, price INTEGER NOT NULL, is_sbc INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL, pro_score REAL,
        pro_notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE opinions (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, creator TEXT NOT NULL COLLATE NOCASE,
        stance TEXT NOT NULL, score REAL, reason TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL,
        UNIQUE(card_id, creator));
        INSERT INTO cards (name, position, price, data) VALUES ('X','ST',1,'{}');
        INSERT INTO opinions (card_id, creator, stance, ts) VALUES (1,'Gullit','yes','2026-01-01 00:00');""")
    old.commit()
    old.close()
    for _ in range(3):  # più connessioni di fila: nessun errore e nessuna colonna doppia
        c = db.connect(p)
        c.close()
    c = db.connect(p)
    cols = [r["name"] for r in c.execute("PRAGMA table_info(opinions)")]
    assert cols.count("auto") == cols.count("confidence") == cols.count("src_date") == 1
    row = c.execute("SELECT auto, confidence, src_date FROM opinions").fetchone()
    assert (row["auto"], row["confidence"], row["src_date"]) == (0, None, "")  # il vecchio parere resta manuale
    for t in ("channels", "channel_misses", "processed_items", "auto_runs"):
        assert c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] == 0


# ------------------------------------------------------------- trascrizioni ----

def test_parse_json3_and_vtt_and_lines():
    segs = TR.parse(json3(["ciao a tutti", "", "oggi Mbappé"]), "json3")
    assert [s[1] for s in segs] == ["ciao a tutti", "oggi Mbappé"]
    vtt = ("WEBVTT\nKind: captions\n\n00:00:01.000 --> 00:00:03.000\nciao a <c>tutti</c>\n\n"
           "00:00:03.000 --> 00:00:05.000\nciao a tutti\noggi Mbappé\n")
    segs = TR.parse(vtt, "vtt")
    assert [s[1] for s in segs] == ["ciao a tutti", "oggi Mbappé"]  # riga ripetuta scartata
    assert TR.parse("non json", "json3") == []
    assert len(TR.to_lines([(0, "uno due tre quattro")] * 5, words_per_line=8)) == 3


def test_sections_split_by_chapters():
    segs = [(0, "intro del video"), (65, "Mbappé è forte"), (130, "Ronaldo è lento")]
    out = TR.sections(segs, [(0, "Intro"), (60, "Mbappé"), (120, "Ronaldo")], words_per_line=2)
    assert len(out) == 3 and out[1].startswith("Mbappé\n") and "Ronaldo" not in out[1]
    assert TR.sections(segs, None, 2) != [] and TR.sections([], None) == []


def test_choose_subtitle_prefers_manual_and_never_translations():
    assert ytdlp.choose_subtitle({"subtitles": {"en": [1], "it": [1]}}, ["it", "en"]) == ("it", "manuale")
    assert ytdlp.choose_subtitle({"automatic_captions": {"en-orig": [1], "it": [1]}, "language": "en"}, ["it", "en"]) == ("en-orig", "automatica")
    # video in inglese: la versione "it" è una traduzione automatica -> non si usa
    assert ytdlp.choose_subtitle({"automatic_captions": {"it": [1]}, "language": "en"}, ["it"]) is None
    assert ytdlp.choose_subtitle({"automatic_captions": {"it": [1]}, "language": "it"}, ["it"]) == ("it", "automatica")
    assert ytdlp.choose_subtitle({}, ["it"]) is None


# ------------------------------------------------------------ provider yt-dlp ----

def test_provider_commands_are_gentle_and_use_no_cookies_or_login():
    fake = FakeYouTube()
    fake.videos["v1"] = info("v1")
    fake.subs["v1"] = json3(STRONG)
    v = yt_of(fake).video("v1")
    assert v.segments and v.transcript_kind == "automatica" and v.date == "2026-10-01"
    flat = " ".join(a for c in fake.calls for a in c)
    assert "--skip-download" in flat and "--write-auto-subs" in flat
    assert "cookies" not in flat and "--username" not in flat and "--password" not in flat and "--netrc" not in flat


def test_provider_pauses_between_requests_and_missing_ytdlp(monkeypatch):
    fake, pauses = FakeYouTube(), []
    fake.videos["v1"] = info("v1", subs_auto=False)
    y = ytdlp.YtDlp(runner=fake, sleep=pauses.append, delay=3.0)
    y.video("v1")
    y.video("v1")
    assert len(fake.calls) == 2 and pauses == [3.0]  # una pausa tra le due richieste, nessuna prima della prima
    monkeypatch.setattr(ytdlp.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(YtDlpMissing, match="pip install"):
        ytdlp.default_runner(["--version"], 5)


def test_block_and_limits_detection():
    fake = FakeYouTube()
    fake.block_after = 0
    with pytest.raises(YtBlocked, match="limitato"):
        yt_of(fake).video("x")
    fake = FakeYouTube()
    fake.videos["a"] = info("a", subs_auto=False)
    y = yt_of(fake, max_requests=2)
    y.video("a")
    y.video("a")
    with pytest.raises(RequestCapReached):
        y.video("a")
    # errori di fila (rete assente, video privati...): dopo N si sospende
    y = yt_of(FakeYouTube(), max_consecutive_errors=3)
    for _ in range(2):
        with pytest.raises(YtVideoError):
            y.video("sparito")
    with pytest.raises(YtBlocked, match="errori di fila"):
        y.video("sparito")


def test_age_restricted_is_a_single_video_error():
    r = ytdlp.RunResult(1, "", "ERROR: Sign in to confirm your age. This video may be inappropriate")
    with pytest.raises(YtVideoError, match="età"):
        yt_of(lambda a, t: r).video("x")


# ------------------------------------------------------------------ scoperta ----

def search_entry(title, cid="UC1"):
    return {"id": "s1", "title": "EA FC", "channel": title, "channel_id": cid}


def discovery_world(conn, **channel_kw):
    fake = FakeYouTube()
    fake.search["Exeed EA FC"] = [search_entry("Exeed Esports")]
    fv = [("a", "EA FC 26 TOTY pack opening"), ("b", "SBC FUT consigliati"), ("c", "Vlog della settimana")]
    fake.channels["UC1"] = chan(videos=fv, **channel_kw)
    return fake


def test_discovery_accepts_a_matching_channel_and_never_searches_again(conn):
    fake = discovery_world(conn)
    c = {"canali_scoperti": 0, "errori": []}
    cfg = cfg_of()
    discovery.discover(conn, yt_of(fake), cfg, [{"name": "Exeed", "channel_id": "", "handle": ""}], c, NOW, log=lambda *_: None)
    row = conn.execute("SELECT * FROM channels WHERE name='Exeed'").fetchone()
    assert c["canali_scoperti"] == 1 and (row["channel_id"], row["subscribers"], row["verified_auto"]) == ("UC1", 50000, 1)
    assert row["handle"] == "@exeed" and row["title"] == "Exeed Esports"
    n = len(fake.calls)
    discovery.discover(conn, yt_of(fake), cfg, [{"name": "Exeed", "channel_id": "", "handle": ""}], c, NOW, log=lambda *_: None)
    assert len(fake.calls) == n  # canale già noto: nessuna nuova ricerca


@pytest.mark.parametrize("case,expect", [
    ("nome diverso", "nome abbastanza simile"),
    ("pochi iscritti", "iscritti"),
    ("iscritti ignoti", "non leggibile"),
    ("niente fc", "FC/FIFA"),
    ("nessun risultato", "nessun canale"),
])
def test_discovery_rejects_unsafe_channels(conn, case, expect):
    fake = discovery_world(conn)
    if case == "nome diverso":
        fake.search["Exeed EA FC"] = [search_entry("Giochi di Marco")]
    elif case == "pochi iscritti":
        fake.channels["UC1"]["channel_follower_count"] = 120
    elif case == "iscritti ignoti":
        fake.channels["UC1"]["channel_follower_count"] = None
    elif case == "niente fc":
        fake.channels["UC1"]["entries"] = [{"id": "z", "title": "Ricette di cucina"}, {"id": "y", "title": "Vlog"}]
    else:
        fake.search["Exeed EA FC"] = []
    c = {"canali_scoperti": 0, "errori": []}
    cfg = cfg_of()
    discovery.discover(conn, yt_of(fake), cfg, [{"name": "Exeed", "channel_id": "", "handle": ""}], c, NOW, log=lambda *_: None)
    assert c["canali_scoperti"] == 0 and conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0
    miss = conn.execute("SELECT reason FROM channel_misses WHERE name='Exeed'").fetchone()
    assert miss and expect in miss["reason"]
    n = len(fake.calls)  # mai riprovato prima di discovery_retry_days
    discovery.discover(conn, yt_of(fake), cfg, [{"name": "Exeed", "channel_id": "", "handle": ""}], c, NOW, log=lambda *_: None)
    assert len(fake.calls) == n


def test_name_similarity():
    assert discovery.name_similarity("Exeed", "EXEED Esports") == 1.0
    assert discovery.name_similarity("Team Gullit", "Gullit") == 1.0
    assert discovery.name_similarity("Nassada", "Nasada") >= 0.85
    assert discovery.name_similarity("Exeed", "Marco Rossi") < 0.5
    assert discovery.name_similarity("", "x") == 0.0


def test_discovery_registers_configured_channels_without_network_and_caps_searches(conn):
    fake = FakeYouTube()
    cfg = cfg_of(limits={"max_channels_discovered_per_run": 1})
    cs = [{"name": "A", "channel_id": "UCa", "handle": ""}, {"name": "B", "channel_id": "", "handle": "@b"},
          {"name": "C", "channel_id": "", "handle": ""}, {"name": "D", "channel_id": "", "handle": ""}]
    c = {"canali_scoperti": 0, "errori": []}
    discovery.discover(conn, yt_of(fake), cfg, cs, c, NOW, log=lambda *_: None)
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM channels")}
    assert set(rows) == {"A", "B"} and rows["A"]["verified_auto"] == 0 and rows["B"]["handle"] == "@b"
    assert fake.n("ytsearch") == 1  # tetto: una sola ricerca, anche se mancano due canali


def test_creators_merge_and_missing_or_broken_pros_file(tmp_path, monkeypatch):
    names = lambda: [c["name"] for c in acfg.creators()[0]]  # noqa: E731
    base = names()
    assert "Exeed" in base  # pros.json assente (cartella temporanea del test): solo research.json
    p = tmp_path / "pros.json"
    p.write_text("", encoding="utf-8")
    assert names() == base and acfg.creators()[1] == []
    p.write_text("{rotto", encoding="utf-8")
    assert names() == base and "non è JSON valido" in acfg.creators()[1][0]
    p.write_text(json.dumps({"pros": [{"name": "Zio Pera", "youtube_handle": "@zio"}, {"name": "exeed", "youtube_channel_id": "UCx"},
                                      {"nome": "senza name"}]}), encoding="utf-8")
    cs = {c["name"]: c for c in acfg.creators()[0]}
    assert cs["Zio Pera"]["handle"] == "@zio" and cs["Exeed"]["channel_id"] == "UCx" and len(cs) == len(base) + 1
    p.write_text(json.dumps([{"name": "Lista Nuda"}]), encoding="utf-8")  # anche una lista semplice
    assert "Lista Nuda" in names()


# -------------------------------------------------------- video e pareri ----

def test_video_with_transcript_saves_an_automatic_opinion(world):
    conn, fake, cid = world
    res = go(conn, fake)
    assert res["status"] == "ok", res
    s = res["summary"]
    assert (s["video_elaborati"], s["pareri_salvati"], s["pareri_scartati"]) == (1, 1, 0)
    (o,) = opinion_rows(conn)
    assert (o["card_id"], o["creator"], o["stance"], o["auto"], o["src_date"]) == (cid, "Exeed", "yes", 1, "2026-10-01")
    assert o["confidence"] >= 0.6 and o["url"] == "https://www.youtube.com/watch?v=vid1" and o["score"] == 90
    assert conn.execute("SELECT COUNT(*) FROM criteria WHERE opinion_id=?", (o["id"],)).fetchone()[0] >= 1
    p = conn.execute("SELECT status, note FROM processed_items WHERE url=?", (o["url"],)).fetchone()
    assert p["status"] == "ok"
    run = conn.execute("SELECT * FROM auto_runs").fetchone()
    assert run["status"] == "ok" and json.loads(run["summary"])["pareri_salvati"] == 1 and run["finished"]


def test_processed_videos_are_not_processed_again(world):
    conn, fake, _ = world
    go(conn, fake)
    n = fake.n("watch?v=vid1")
    res = go(conn, fake)
    assert fake.n("watch?v=vid1") == n and res["summary"]["video_elaborati"] == 0
    assert len(opinion_rows(conn)) == 1


def test_weak_and_ambiguous_opinions_are_discarded_not_saved(world):
    conn, fake, _ = world
    add_card(conn, "Ronaldo Nazario", "Icon")  # una sola carta 'Ronaldo': testo debole -> sotto soglia
    conn.commit()
    fake.channels["UC1"] = chan(videos=[("w1", "Ronaldo")])
    fake.videos["w1"] = info("w1", "Ronaldo")
    fake.subs["w1"] = json3(WEAK)
    res = go(conn, fake)
    assert res["summary"]["pareri_salvati"] == 0 and res["summary"]["pareri_scartati"] >= 1
    assert opinion_rows(conn) == []
    rej = conn.execute("SELECT status, note, source FROM proposals").fetchall()
    assert rej and all(r["status"] == "rejected" and r["note"].startswith("scartata_auto:") and r["source"] == "auto" for r in rej)
    assert conn.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0] == 0
    # ambigua: due carte 'Silva' -> mai salvata
    add_card(conn, "Bernardo Silva", "TOTW", "RW")
    add_card(conn, "David Silva", "Icon", "RW")
    conn.commit()
    fake.channels["UC1"] = chan(videos=[("s1", "Silva")])
    fake.videos["s1"] = info("s1", "Silva")
    fake.subs["s1"] = json3(["Silva è una bestia", "scatto devastante e finalizzazione top", "lo consiglio assolutamente è meta"])
    res = go(conn, fake)
    assert opinion_rows(conn) == []
    assert any("ambigua" in r["note"] for r in conn.execute("SELECT note FROM proposals"))


def test_threshold_is_configurable(world):
    conn, fake, _ = world
    res = go(conn, fake, cfg_of(thresholds={"min_confidence": 0.95}))
    assert res["summary"]["pareri_salvati"] == 0 and res["summary"]["pareri_scartati"] == 1 and opinion_rows(conn) == []
    note = conn.execute("SELECT note FROM proposals").fetchone()["note"]
    assert "sotto la soglia" in note


def test_video_without_transcript_has_penalized_confidence_and_is_retried(world):
    conn, fake, _ = world
    fake.videos["vid1"] = info("vid1", "Mbappé TOTY è una bestia, scatto devastante e finalizzazione top, lo consiglio, è meta",
                               subs_auto=False, date="20261002")
    res = go(conn, fake)
    assert opinion_rows(conn) == [] and res["summary"]["pareri_scartati"] >= 1  # confidenza dimezzata: mai sopra soglia
    st = conn.execute("SELECT status, note FROM processed_items").fetchone()
    assert st["status"] == "senza_trascrizione" and "data=2026-10-02" in st["note"]
    # più tardi (>= retry_error_hours) la trascrizione compare: il video viene ripreso e il parere salvato
    fake.videos["vid1"] = info("vid1", "Mbappé TOTY review", date="20261002")
    later = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
    res = runner.run_once(lambda: db.connect(), cfg=cfg_of(), yt=yt_of(fake), now=later, log=lambda *_: None)
    assert res["summary"]["pareri_salvati"] == 1 and len(opinion_rows(conn)) == 1
    assert not conn.execute("SELECT 1 FROM proposals WHERE status='rejected'").fetchone()  # vecchi scarti tolti


def test_old_and_short_videos_are_skipped_without_wasting_requests(world):
    conn, fake, _ = world
    fake.channels["UC1"] = {**chan(), "entries": [
        {"id": "sh", "title": "short", "duration": 30, "upload_date": "20261002"},
        {"id": "old1", "title": "vecchio", "duration": 900, "upload_date": "20250101"},
        {"id": "old2", "title": "ancora più vecchio", "duration": 900, "upload_date": "20240101"}]}
    res = go(conn, fake)
    assert fake.n("watch?v=") == 0 and res["summary"]["video_elaborati"] == 0
    rows = {r["url"].split("=")[1]: r["status"] for r in conn.execute("SELECT * FROM processed_items")}
    assert rows == {"sh": "saltato", "old1": "vecchio", "old2": "vecchio"}


def test_real_date_unknown_in_list_is_checked_on_the_video(world):
    conn, fake, _ = world
    fake.videos["vid1"] = info("vid1", date="20240101")
    res = go(conn, fake)
    assert opinion_rows(conn) == [] and conn.execute("SELECT status FROM processed_items").fetchone()[0] == "vecchio"
    assert res["summary"]["video_elaborati"] == 0


def test_one_bad_video_does_not_stop_the_batch(world):
    conn, fake, _ = world
    fake.channels["UC1"] = chan(videos=[("gone", "privato"), ("vid1", "ok")])  # 'gone' non esiste: Video unavailable
    res = go(conn, fake)
    assert res["status"] == "parziale" and res["summary"]["pareri_salvati"] == 1
    assert any("gone" in e for e in res["summary"]["errori"])
    assert conn.execute("SELECT status FROM processed_items WHERE url LIKE '%gone'").fetchone()[0] == "errore"
    # l'errore si ritenta solo dopo retry_error_hours
    n = fake.n("watch?v=gone")
    go(conn, fake)
    assert fake.n("watch?v=gone") == n


def test_chapters_keep_each_card_to_its_own_section(world):
    conn, fake, cid = world
    ro = add_card(conn, "Cristiano Ronaldo", "Icon")
    conn.commit()
    lines = STRONG + ["passiamo a Ronaldo Icon", "è lento e fragile", "non vale il prezzo troppo caro evitatelo sconsiglio"]
    fake.videos["vid1"] = info("vid1", "Review", chapters=[{"start_time": 0, "title": "Mbappé"}, {"start_time": 20, "title": "Ronaldo"}])
    fake.subs["vid1"] = json3(lines, step=5000)
    go(conn, fake)
    got = {o["card_id"]: o["stance"] for o in opinion_rows(conn)}
    assert got.get(cid) == "yes"
    assert got.get(ro, "no") == "no"  # Ronaldo non eredita il parere positivo su Mbappé


def test_cap_of_opinions_per_run(world):
    conn, fake, _ = world
    add_card(conn, "Vinicius Junior", "TOTY", "LW")
    conn.commit()
    txt = STRONG + ["poi Vinicius Junior TOTY è una bestia", "scatto devastante e finalizzazione top", "lo consiglio assolutamente è meta"]
    fake.subs["vid1"] = json3(txt)
    res = go(conn, fake, cfg_of(limits={"max_opinions_per_run": 1}))
    s = res["summary"]
    assert s["pareri_salvati"] == 1 and len(opinion_rows(conn)) == 1
    assert any("tetto" in r["note"] for r in conn.execute("SELECT note FROM proposals"))


def test_cap_of_opinions_per_video(world):
    conn, fake, _ = world
    add_card(conn, "Vinicius Junior", "TOTY", "LW")
    conn.commit()
    fake.subs["vid1"] = json3(STRONG + ["poi Vinicius Junior TOTY è una bestia", "scatto devastante e finalizzazione top",
                                        "lo consiglio assolutamente è meta"])
    res = go(conn, fake, cfg_of(thresholds={"max_opinions_per_video": 1}))
    assert res["summary"]["pareri_salvati"] == 1 and res["summary"]["pareri_scartati"] == 1
    assert any("per video" in r["note"] for r in conn.execute("SELECT note FROM proposals"))


def test_batch_stops_on_block_and_keeps_what_was_done(world):
    conn, fake, _ = world
    fake.channels["UC1"] = chan(videos=[("vid1", "a"), ("vid2", "b"), ("vid3", "c")])
    fake.videos["vid2"] = info("vid2")
    fake.subs["vid2"] = json3(STRONG_NO)
    fake.videos["vid3"] = info("vid3")
    fake.block_after = 4  # elenco canale, info+sottotitoli di vid1, info di vid2 ... poi 429
    res = go(conn, fake)
    s = res["summary"]
    assert res["status"] == "parziale" and "youtube_video" in s["fermato"] and "limitato" in s["fermato"]["youtube_video"]
    assert s["pareri_salvati"] == 1  # quanto fatto prima del blocco resta
    done = {r["url"].split("=")[1] for r in conn.execute("SELECT url FROM processed_items")}
    assert done == {"vid1"}  # vid2 e vid3 non segnati: ripresi alla prossima esecuzione
    assert fake.n("watch?v=vid3") == 0  # niente insistenza dopo il blocco


def test_block_during_discovery_skips_video_step_but_not_fut(conn, monkeypatch):
    monkeypatch.setattr(acfg, "creators", lambda: ([{"name": "Exeed", "channel_id": "", "handle": ""}], []))
    fake = FakeYouTube()
    fake.block_after = 0
    pages = {"https://x/list": list_page([("Pelé", 95, "Base Icon", "CM", "6.8M")]),
             "https://www.fut.gg/players/1-pelé/27-1/": gg_page(name="Pelé", rating=95, rarity="Base Icon")}
    cfg = cfg_of(futgg={"enabled": True, "list_urls": ["https://x/list"], "pages": 1})
    res = go(conn, fake, cfg, fut_fetch=lambda u: pages[u], robots=lambda u: True)
    s = res["summary"]
    assert s["carte_nuove"] == 1  # FUT.GG è andato a buon fine
    assert "youtube_scoperta" in s["fermato"] and "youtube_video" in s["fermato"]
    assert fake.n("watch?v=") == 0 and res["status"] == "parziale"


def test_manual_opinion_is_never_overwritten_and_newer_auto_updates_older_auto(world):
    conn, fake, cid = world
    db.upsert_opinion(conn, cid, OpinionIn(creator="exeed", stance="no", reason="parere mio", score=40))  # manuale, maiuscole diverse
    conn.commit()
    res = go(conn, fake)
    assert res["summary"]["pareri_protetti"] == 1 and res["summary"]["pareri_salvati"] == 0
    (o,) = opinion_rows(conn)
    assert (o["auto"], o["stance"], o["reason"]) == (0, "no", "parere mio")
    # parere automatico: più vecchio non sovrascrive, più recente sì
    op = OpinionIn(creator="Nassada", stance="yes", reason="r1")
    assert AO.save_auto(conn, cid, op, 0.7, "2026-09-10", []) == AO.SAVED
    assert AO.save_auto(conn, cid, OpinionIn(creator="Nassada", stance="no", reason="vecchio"), 0.9, "2026-09-01", []) == AO.OLDER
    assert AO.save_auto(conn, cid, OpinionIn(creator="Nassada", stance="no", reason="nuovo"), 0.9, "2026-09-20", [("price", -1)]) == AO.UPDATED
    r = conn.execute("SELECT * FROM opinions WHERE creator='Nassada'").fetchone()
    assert (r["stance"], r["reason"], r["src_date"], r["confidence"], r["auto"]) == ("no", "nuovo", "2026-09-20", 0.9, 1)
    assert conn.execute("SELECT COUNT(*) FROM criteria WHERE opinion_id=?", (r["id"],)).fetchone()[0] == 1
    conn.commit()


def test_manual_edit_of_an_automatic_opinion_makes_it_manual_and_undo_spares_it(world):
    conn, fake, cid = world
    go(conn, fake)
    assert opinion_rows(conn)[0]["auto"] == 1
    db.upsert_opinion(conn, cid, OpinionIn(creator="Exeed", stance="maybe", reason="corretto a mano"))
    conn.commit()
    o = opinion_rows(conn)[0]
    assert (o["auto"], o["confidence"], o["src_date"]) == (0, None, "")
    assert AO.undo(conn) == 0 and len(opinion_rows(conn)) == 1


def test_undo_removes_only_automatic_and_their_criteria(world):
    conn, fake, cid = world
    go(conn, fake)
    db.upsert_opinion(conn, cid, OpinionIn(creator="Altro", stance="yes", reason="manuale"))
    conn.commit()
    assert AO.undo(conn) == 1
    left = opinion_rows(conn)
    assert [(o["creator"], o["auto"]) for o in left] == [("Altro", 0)]
    assert conn.execute("SELECT COUNT(*) FROM criteria WHERE opinion_id NOT IN (SELECT id FROM opinions)").fetchone()[0] == 0
    # i video restano 'elaborati': l'annullamento non si rifà da solo al giro dopo
    res = go(conn, fake)
    assert res["summary"]["video_elaborati"] == 0 and len(opinion_rows(conn)) == 1


# --------------------------------------------------------------- FUT.GG ----

def fut_pages():
    return {"https://x/list": list_page([("Pelé", 95, "Base Icon", "CM", "6.8M"), ("Zico", 91, "Base Icon", "CM", "4.9M")]),
            "https://x/list?page=2": list_page([("Zico", 91, "Base Icon", "CM", "5.1M")]),
            "https://www.fut.gg/players/1-pelé/27-1/": gg_page(name="Pelé", rating=95, rarity="Base Icon"),
            "https://www.fut.gg/players/2-zico/27-2/": gg_page(name="Zico", rating=91, rarity="Base Icon")}


def test_page_urls():
    assert futgg.page_urls(["https://x/l", "https://x/m?a=1"], 2) == ["https://x/l", "https://x/l?page=2", "https://x/m?a=1",
                                                                  "https://x/m?a=1&page=2"]


def test_futgg_update_adds_cards_and_prices(conn):
    pages = fut_pages()
    cfg = cfg_of(futgg={"enabled": True, "list_urls": ["https://x/list"], "pages": 2})
    c = runner.new_counters()
    futgg.update(conn, cfg, c, fetch=lambda u: pages[u], robots=lambda u: True, log=lambda *_: None)
    assert c["carte_nuove"] == 2 and c["errori"] == [] and not c["fermato"]
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 2
    c2 = runner.new_counters()  # secondo giro: carte note, la pagina 2 aggiorna il prezzo di Zico
    futgg.update(conn, cfg, c2, fetch=lambda u: pages[u], robots=lambda u: True, log=lambda *_: None)
    assert c2["carte_nuove"] == 0 and c2["prezzi_aggiornati"] == 3  # Pelé e Zico dalla pagina 1, Zico ancora dalla 2


def test_futgg_cap_new_cards_and_errors_do_not_lose_the_rest(conn):
    pages = fut_pages()

    def flaky(u):
        if u.endswith("page=2"):
            raise OSError("rete")
        return pages[u]

    cfg = cfg_of(futgg={"enabled": True, "list_urls": ["https://x/list"], "pages": 2}, limits={"max_new_cards": 1})
    c = runner.new_counters()
    futgg.update(conn, cfg, c, fetch=flaky, robots=lambda u: True, log=lambda *_: None)
    assert c["carte_nuove"] == 1 and len(c["errori"]) == 1 and "OSError" in c["errori"][0]


def test_futgg_block_and_robots_stop_the_step(conn):
    from eafcmeta import fetch
    pages = fut_pages()

    def blocked(u):
        raise fetch.Blocked("il sito ha rifiutato la richiesta (HTTP 429)")

    cfg = cfg_of(futgg={"enabled": True, "list_urls": ["https://x/list"], "pages": 2})
    c = runner.new_counters()
    futgg.update(conn, cfg, c, fetch=blocked, robots=lambda u: True, log=lambda *_: None)
    assert "429" in c["fermato"]["futgg"] and c["carte_nuove"] == 0
    c = runner.new_counters()
    futgg.update(conn, cfg, c, fetch=lambda u: pages[u], robots=lambda u: False, log=lambda *_: None)
    assert "robots.txt" in c["fermato"]["futgg"]


# ------------------------------------------------- esecuzioni e scheduler ----

def test_runs_do_not_overlap(world):
    conn, fake, _ = world
    rid = runner.begin_run(conn, NOW)
    assert rid and runner.begin_run(conn, NOW) is None
    assert go(conn, fake)["status"] == "saltata"
    assert runner.is_running(conn, NOW)
    later = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)  # riga 'in_corso' vecchia = esecuzione interrotta
    assert runner.begin_run(conn, later)
    assert conn.execute("SELECT status FROM auto_runs WHERE id=?", (rid,)).fetchone()[0] == "interrotto"


def test_step_crash_does_not_stop_other_steps(world, monkeypatch):
    conn, fake, _ = world
    monkeypatch.setattr(futgg, "update", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res = go(conn, fake, cfg_of(futgg={"enabled": True}))
    assert res["summary"]["pareri_salvati"] == 1 and res["status"] == "parziale"
    assert any("boom" in e for e in res["summary"]["errori"])


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def sched(clock, last=None, **over):
    cfg = {**acfg.load(), "enabled": True, "interval_hours": 24, "run_on_start": True, "start_delay_seconds": 0, **over}
    jobs = []
    st = {"last": last}

    def job(trigger):
        jobs.append(trigger)
        st["last"] = clock()
        return {"status": "ok", "summary": {}}

    s = scheduler.Scheduler(job, clock=clock, cfg_loader=lambda: cfg, last_started=lambda: st["last"], log=lambda *_: None)
    return s, jobs, cfg


def test_scheduler_runs_on_start_when_never_run_or_overdue():
    clk = Clock()
    s, jobs, _ = sched(clk)
    assert s.tick()["status"] == "ok" and jobs == ["pianificata"]
    clk.t += 3600
    assert s.tick() is None and jobs == ["pianificata"]  # prossima fra 24 h
    clk.t += 24 * 3600
    assert s.tick() and len(jobs) == 2
    clk2 = Clock()
    s, jobs, _ = sched(clk2, last=clk2.t - 30 * 3600)  # ultima esecuzione più vecchia dell'intervallo
    assert s.tick() and len(jobs) == 1
    s, jobs, _ = sched(clk2, last=clk2.t - 3600)  # recente: aspetta
    assert s.tick() is None and jobs == []
    assert s.next_due == pytest.approx(clk2.t + 23 * 3600)


def test_scheduler_respects_run_on_start_false_delay_and_enabled():
    clk = Clock()
    s, jobs, _ = sched(clk, last=clk.t - 99 * 3600, run_on_start=False)
    assert s.tick() is None and jobs == []  # scaduta ma niente avvio immediato
    clk.t += 24 * 3600
    assert s.tick() and len(jobs) == 1
    clk = Clock()
    s, jobs, _ = sched(clk, start_delay_seconds=30)
    assert s.tick() is None
    clk.t += 31
    assert s.tick() and len(jobs) == 1
    clk = Clock()
    s, jobs, _ = sched(clk, enabled=False)
    assert s.tick() is None and jobs == [] and s.next_due is None


def test_scheduler_survives_job_failure_and_bad_config():
    clk = Clock()

    def job(trigger):
        raise RuntimeError("giù")

    s = scheduler.Scheduler(job, clock=clk, cfg_loader=lambda: {**acfg.load(), "start_delay_seconds": 0},
                            last_started=lambda: None, log=lambda *_: None)
    assert s.tick()["status"] == "errore"
    assert s.tick() is None  # niente insistenza immediata
    def bad():
        raise AutoConfigError("rotta")
    s = scheduler.Scheduler(lambda t: {}, clock=clk, cfg_loader=bad, log=lambda *_: None)
    assert s.tick() is None


def test_thread_starts_only_if_enabled_and_env_not_zero(monkeypatch, tmp_path):
    svc = scheduler.AutoService()
    assert svc.start_if_enabled() is False  # EAFCMETA_AUTO=0 nei test
    monkeypatch.setenv("EAFCMETA_AUTO", "1")
    monkeypatch.setenv("EAFCMETA_DB", str(tmp_path / "s.db"))
    local = Path(acfg.local_path())
    local.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    assert svc.start_if_enabled() is False
    local.write_text(json.dumps({"enabled": True, "start_delay_seconds": 3600}), encoding="utf-8")
    try:
        assert svc.start_if_enabled() is True and svc.thread_alive and svc.start_if_enabled() is True
    finally:
        svc.stop()
    assert not svc.thread_alive


def test_config_validation(tmp_path):
    with pytest.raises(AutoConfigError):
        acfg.check_value("thresholds", "min_confidence", 3)
    with pytest.raises(AutoConfigError):
        acfg.check_value(None, "enabled", "si")
    with pytest.raises(AutoConfigError):
        acfg.check_value(None, "sconosciuto", 1)
    assert acfg.check_value("limits", "max_new_cards", 5.0) == 5
    acfg.local_path().write_text('{"thresholds": {"min_confidence": 7}}', encoding="utf-8")
    with pytest.raises(AutoConfigError):
        acfg.load()
    acfg.local_path().write_text("rotto", encoding="utf-8")
    with pytest.raises(AutoConfigError):
        acfg.load()


# ------------------------------------------------------------------- API ----

class FakeService:
    thread_alive, next_due = False, None

    def __init__(self, running=False):
        self.running, self.started = running, 0

    def run_async(self, job=None):
        if self.running:
            return False
        self.started += 1
        return True

    def start_if_enabled(self):
        self.started += 10


@pytest.fixture
def svc():
    s = FakeService()
    app.dependency_overrides[api_auto.service_dep] = lambda: s
    yield s
    app.dependency_overrides.pop(api_auto.service_dep, None)


def test_api_status_run_and_409(client, svc):
    r = client.get("/api/v1/auto/status").json()
    assert r["enabled"] is True and r["env_enabled"] is False and r["active"] is False and r["last_run"] is None
    assert r["counters"]["pareri_automatici"] == 0 and r["channels"] == [] and r["config"]["min_confidence"] == 0.6
    assert r["next_run_note"] == "spenta"
    assert client.post("/api/v1/auto/run").status_code == 202 and svc.started == 1
    svc.running = True
    assert client.post("/api/v1/auto/run").status_code == 409


def test_api_status_shows_last_run_and_channels(client, svc, monkeypatch):
    monkeypatch.setenv("EAFCMETA_AUTO", "1")
    c = db.connect()
    c.execute("INSERT INTO channels VALUES ('Exeed','UC1','@e','Exeed Esports',50000,'2026-10-01 10:00',1)")
    c.execute("INSERT INTO auto_runs (started, finished, status, summary) VALUES ('2026-10-03 06:00','2026-10-03 06:05','ok','{\"pareri_salvati\": 3}')")
    c.commit()
    r = client.get("/api/v1/auto/status").json()
    assert r["last_run"]["summary"]["pareri_salvati"] == 3 and r["next_run"] == "2026-10-04 06:00"
    assert r["channels"][0]["verified_auto"] is True and r["counters"]["canali"] == 1


def test_api_config_put_validates_and_persists(client, svc):
    r = client.put("/api/v1/auto/config", json={"min_confidence": 0.8, "interval_hours": 12, "enabled": True})
    assert r.status_code == 200 and r.json()["config"]["min_confidence"] == 0.8 and svc.started == 10
    assert json.loads(acfg.local_path().read_text())["thresholds"]["min_confidence"] == 0.8
    assert client.get("/api/v1/auto/status").json()["config"]["interval_hours"] == 12
    for bad in ({"min_confidence": 0.1}, {"min_confidence": 2}, {"interval_hours": 0}, {"enabled": "forse"},
                {"request_delay_seconds": 0}, {"qualcosa": 1}, {}):
        assert client.put("/api/v1/auto/config", json=bad).status_code == 422, bad
    r = client.put("/api/v1/auto/config", json={"enabled": False})
    assert r.json()["enabled"] is False


def test_api_opinions_list_undo_and_card_detail_flag(client, svc):
    cid = client.post("/api/v1/cards", json={"name": "Kylian Mbappé", "version": "TOTY", "position": "ST", "price": 1000,
                                              "stats": dict(ST)}).json()["id"]
    conn = db.connect()
    AO.save_auto(conn, cid, OpinionIn(creator="Exeed", stance="yes", reason="top", url="https://youtu.be/x"), 0.72, "2026-10-01", [])
    conn.commit()
    client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "Manuale", "stance": "no", "reason": "boh"})
    ops = client.get(f"/api/v1/cards/{cid}").json()["opinions"]
    assert {o["creator"]: o["automatic"] for o in ops} == {"Exeed": True, "Manuale": False}
    lst = client.get("/api/v1/auto/opinions").json()
    assert len(lst) == 1 and lst[0]["creator"] == "Exeed" and lst[0]["url"] == "https://youtu.be/x" and lst[0]["card_name"] == "Kylian Mbappé"
    assert client.delete("/api/v1/auto/opinions").json() == {"removed": 1}
    assert client.get("/api/v1/auto/opinions").json() == []
    assert [o["creator"] for o in client.get(f"/api/v1/cards/{cid}").json()["opinions"]] == ["Manuale"]


def test_api_requires_token_when_set(client, svc, monkeypatch):
    monkeypatch.setenv("EAFCMETA_TOKEN", "segreto")
    for m, u in (("get", "/status"), ("post", "/run"), ("put", "/config"), ("get", "/opinions"), ("delete", "/opinions")):
        assert getattr(client, m)("/api/v1/auto" + u).status_code == 401
    assert client.get("/api/v1/auto/status", headers={"X-Token": "segreto"}).status_code == 200


# ------------------------------------------------------------------- CLI ----

def test_cli_status_undo_and_run_if_due(world, capsys, monkeypatch):
    from eafcmeta.auto import __main__ as cli
    conn, fake, cid = world
    go(conn, fake)
    assert cli.main(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pareri_automatici"] == 1 and out["canali"][0]["name"] == "Exeed" and out["ultima_esecuzione"]["status"] == "ok"
    monkeypatch.setenv("EAFCMETA_AUTO", "1")
    monkeypatch.setattr(runner, "run_once", lambda **k: pytest.fail("non dovuta: non deve partire"))
    conn.execute("UPDATE auto_runs SET started=?", (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),))
    conn.commit()
    assert cli.main(["run", "--se-dovuto"]) == 0 and "Non ancora dovuta" in capsys.readouterr().out
    monkeypatch.setenv("EAFCMETA_AUTO", "0")
    assert cli.main(["run"]) == 0 and "EAFCMETA_AUTO=0" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert cli.main(["undo"]) == 1 and len(opinion_rows(conn)) == 1
    assert cli.main(["undo", "--si"]) == 0 and opinion_rows(conn) == []


def test_offline_empty_results_are_errors_not_misses(conn):
    """Rete assente: yt-dlp esce con 0, entries [null] e ERROR su stderr. Non deve diventare 'canale non trovato' per 7 giorni."""
    r = ytdlp.RunResult(0, json.dumps({"entries": [None]}), "ERROR: Unable to download API page: ('Unable to connect to proxy')")
    c = {"canali_scoperti": 0, "errori": []}
    discovery.discover(conn, yt_of(lambda a, t: r), cfg_of(), [{"name": "Exeed", "channel_id": "", "handle": ""}], c, NOW, log=lambda *_: None)
    assert conn.execute("SELECT COUNT(*) FROM channel_misses").fetchone()[0] == 0 and len(c["errori"]) == 1
    # senza ERROR su stderr un elenco vuoto è davvero 'nessun risultato'
    ok = ytdlp.RunResult(0, json.dumps({"entries": []}), "")
    discovery.discover(conn, yt_of(lambda a, t: ok), cfg_of(), [{"name": "Exeed", "channel_id": "", "handle": ""}], c, NOW, log=lambda *_: None)
    assert conn.execute("SELECT COUNT(*) FROM channel_misses").fetchone()[0] == 1
