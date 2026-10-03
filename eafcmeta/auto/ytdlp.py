"""Provider YouTube senza chiave API, basato su yt-dlp (metodo NON ufficiale: zona grigia dei termini di YouTube).

Uso personale, basso ritmo: pausa tra le richieste, tetto di richieste per esecuzione, niente cookie né login.
yt-dlp si lancia come processo (`python -m yt_dlp`), importato in modo pigro: se manca, errore chiaro solo quando serve.
Il `runner` è INIETTABILE (i test non toccano mai la rete): runner(args, timeout) -> RunResult.
Blocco/limite di YouTube (429, "not a bot", ...) => YtBlocked: il lotto si ferma subito. Errore su UN video => YtVideoError.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import transcript as TR
from .errors import RequestCapReached, YtBlocked, YtDlpMissing, YtVideoError

_BLOCK = re.compile(r"http error 429|too many requests|not a bot|rate[- ]?limit|captcha|unusual traffic", re.I)
_AGE = re.compile(r"confirm your age|age[- ]restricted|inappropriate for some users", re.I)
_BASE = ["--ignore-config", "--no-color", "--socket-timeout", "20", "--retries", "2", "--extractor-retries", "1"]


@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list, float], RunResult]


def default_runner(args: list, timeout: float) -> RunResult:
    if importlib.util.find_spec("yt_dlp") is None:
        raise YtDlpMissing("yt-dlp non è installato: esegui  pip install -r requirements.txt  (oppure  pip install yt-dlp)")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run([sys.executable, "-m", "yt_dlp", *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return RunResult(124, "", "ERROR: tempo scaduto")
    return RunResult(p.returncode, p.stdout, p.stderr)


def channel_url(channel_id: str = "", handle: str = "") -> str:
    if channel_id:
        return f"https://www.youtube.com/channel/{channel_id}/videos"
    if handle:
        return f"https://www.youtube.com/{'@' + handle.lstrip('@')}/videos"
    raise ValueError("serve channel_id o handle")


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _date(d: dict) -> str:
    """AAAA-MM-GG da upload_date (AAAAMMGG) o timestamp; '' se ignota."""
    s = str(d.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    ts = d.get("timestamp") or d.get("release_timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return time.strftime("%Y-%m-%d", time.gmtime(ts))
    return ""


@dataclass
class VideoRef:
    id: str
    title: str = ""
    date: str = ""        # '' se l'elenco "flat" non la riporta
    duration: float | None = None
    description: str = ""


@dataclass
class ChannelInfo:
    title: str = ""
    channel_id: str = ""
    handle: str = ""
    subscribers: int | None = None
    videos: list = field(default_factory=list)  # [VideoRef], dal più recente


@dataclass
class VideoData:
    id: str
    url: str
    title: str = ""
    description: str = ""
    date: str = ""
    duration: float | None = None
    channel: str = ""
    chapters: list = field(default_factory=list)   # [(secondo, titolo)]
    segments: list | None = None                   # [(secondo, testo)] oppure None se manca la trascrizione
    transcript_lang: str = ""
    transcript_kind: str = ""                      # 'manuale' | 'automatica'


def choose_subtitle(info: dict, languages) -> tuple[str, str] | None:
    """(chiave della lingua, 'manuale'|'automatica') oppure None. Mai le traduzioni automatiche: solo sottotitoli manuali
    o la trascrizione automatica nella lingua ORIGINALE del video (meno richieste, meno rischio di limiti)."""
    manual = info.get("subtitles") if isinstance(info.get("subtitles"), dict) else {}
    auto = info.get("automatic_captions") if isinstance(info.get("automatic_captions"), dict) else {}
    own = str(info.get("language") or "").split("-")[0].lower()
    for lang in languages:
        lang = lang.lower()
        for k in sorted(manual):
            if k.lower().split("-")[0] == lang and manual[k]:
                return k, "manuale"
        if f"{lang}-orig" in auto:
            return f"{lang}-orig", "automatica"
        if own == lang:
            for k in sorted(auto):
                if k.lower().split("-")[0] == lang and "-orig" not in k.lower() and auto[k]:
                    return k, "automatica"
    return None


class YtDlp:
    def __init__(self, runner: Runner | None = None, sleep: Callable[[float], None] = time.sleep, delay: float = 4.0,
                 timeout: float = 90.0, max_requests: int = 150, max_consecutive_errors: int = 5,
                 languages=("it", "en"), words_per_line: int = 14, max_chars: int = 30000):
        self.runner = runner or default_runner
        self.sleep, self.delay, self.timeout = sleep, delay, timeout
        self.max_requests, self.max_consecutive_errors = max_requests, max_consecutive_errors
        self.languages, self.words_per_line, self.max_chars = list(languages), words_per_line, max_chars
        self.requests = 0
        self.consecutive_errors = 0
        self.last_stderr = ""

    # ---- richieste ----
    def _run(self, args: list) -> str:
        if self.requests >= self.max_requests:
            raise RequestCapReached(f"tetto di {self.max_requests} richieste a YouTube per esecuzione raggiunto: mi fermo")
        if self.requests and self.delay > 0:
            self.sleep(self.delay)  # gentilezza: pausa tra una richiesta e l'altra
        self.requests += 1
        res = self.runner(_BASE + args, self.timeout)
        err = self.last_stderr = (res.stderr or "")
        if _AGE.search(err):
            self._fail()
            raise YtVideoError("video con restrizione d'età: serve il login, saltato")
        if _BLOCK.search(err):
            raise YtBlocked("YouTube ha limitato o bloccato le richieste (429 / controllo anti-bot): mi fermo e riprovo alla "
                            "prossima esecuzione. Non insisto e non uso cookie né login.")
        if res.returncode != 0:
            self._fail()
            lines = [l for l in err.splitlines() if l.strip()]
            msg = next((l for l in reversed(lines) if "ERROR" in l), lines[-1] if lines else "errore sconosciuto")
            raise YtVideoError("yt-dlp: " + re.sub(r"^ERROR:\s*", "", msg.strip())[:200])
        self.consecutive_errors = 0
        return res.stdout or ""

    def _fail(self) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.max_consecutive_errors:
            raise YtBlocked(f"{self.consecutive_errors} errori di fila da YouTube: sospendo il lotto "
                            "(rete assente o limitazione in corso)")

    def _empty_is_error(self, what: str) -> None:
        """Elenchi/ricerche con rete assente escono con codice 0 ma 'entries' vuoto e ERROR su stderr: non è 'nessun risultato'."""
        if "ERROR:" in self.last_stderr:
            self._fail()
            raise YtVideoError(f"{what}: yt-dlp non ha ottenuto risposta (rete o servizio non disponibili)")

    def _json(self, args: list) -> dict:
        out = self._run(args)
        try:
            data = json.loads(out)
        except ValueError:
            self._fail()
            raise YtVideoError("risposta di yt-dlp non leggibile") from None
        if not isinstance(data, dict):
            raise YtVideoError("risposta di yt-dlp inattesa")
        return data

    # ---- ricerca e canali ----
    def search_videos(self, query: str, n: int = 15) -> list[dict]:
        """ytsearch: video che corrispondono alla ricerca (ognuno con channel, channel_id, title...)."""
        data = self._json(["--flat-playlist", "--skip-download", "--dump-single-json", f"ytsearch{int(n)}:{query}"])
        found = [e for e in data.get("entries") or [] if isinstance(e, dict)]
        if not found:
            self._empty_is_error("ricerca")
        return found

    def channel(self, channel_id: str = "", handle: str = "", limit: int = 5) -> ChannelInfo:
        """Titolo, iscritti e ultimi video del canale (elenco flat: id, titolo, data/durata dove disponibili)."""
        data = self._json(["--flat-playlist", "--skip-download", "--dump-single-json", "--playlist-end", str(int(limit)),
                           channel_url(channel_id, handle)])
        subs = data.get("channel_follower_count")
        info = ChannelInfo(
            title=str(data.get("channel") or data.get("uploader") or "").strip(),
            channel_id=str(data.get("channel_id") or channel_id or ""),
            handle=str(data["uploader_id"]) if str(data.get("uploader_id") or "").startswith("@") else handle,
            subscribers=int(subs) if isinstance(subs, (int, float)) and not isinstance(subs, bool) else None)
        for e in data.get("entries") or []:
            if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"]:
                dur = e.get("duration")
                info.videos.append(VideoRef(id=e["id"], title=str(e.get("title") or ""), date=_date(e),
                                            duration=float(dur) if isinstance(dur, (int, float)) else None,
                                            description=str(e.get("description") or "")[:3000]))
        if not info.videos and not info.title:
            self._empty_is_error("canale")
        return info

    # ---- singolo video ----
    def video(self, video_id: str) -> VideoData:
        """Metadati + (se esistono) sottotitoli it/en, SENZA scaricare il video. Alza YtVideoError per questo solo video."""
        info = self._json(["--skip-download", "--no-playlist", "--dump-single-json", watch_url(video_id)])
        if str(info.get("live_status") or "") in ("is_live", "is_upcoming"):
            raise YtVideoError("diretta in corso o non ancora pubblicata: riprovo più tardi")
        chapters = [(float(c.get("start_time") or 0), str(c.get("title") or "").strip())
                    for c in info.get("chapters") or [] if isinstance(c, dict) and str(c.get("title") or "").strip()]
        dur = info.get("duration")
        v = VideoData(id=video_id, url=watch_url(video_id), title=str(info.get("title") or ""),
                      description=str(info.get("description") or "")[:5000], date=_date(info),
                      duration=float(dur) if isinstance(dur, (int, float)) else None,
                      channel=str(info.get("channel") or info.get("uploader") or ""), chapters=chapters)
        choice = choose_subtitle(info, self.languages)
        if choice:
            segs = self._subtitles(video_id, *choice)
            if segs:
                v.segments, v.transcript_lang, v.transcript_kind = segs, choice[0], choice[1]
        return v

    def _subtitles(self, video_id: str, lang: str, kind: str) -> list:
        with tempfile.TemporaryDirectory(prefix="eafcmeta_sub_") as tmp:
            self._run(["--skip-download", "--no-playlist", "--ignore-no-formats-error",
                       "--write-subs" if kind == "manuale" else "--write-auto-subs", "--sub-langs", lang,
                       "--sub-format", "json3/vtt", "-o", str(Path(tmp) / "%(id)s.%(ext)s"), watch_url(video_id)])
            files = sorted(Path(tmp).glob("*.json3")) or sorted(Path(tmp).glob("*.vtt"))
            if not files:
                return []
            raw = files[0].read_text(encoding="utf-8", errors="replace")
            return TR.parse(raw, files[0].suffix)

    def sections(self, v: VideoData) -> list[str]:
        """Testo del video in sezioni: titolo+descrizione (+capitoli) e poi la trascrizione (per capitolo se c'è)."""
        head = [v.title.strip(), v.description.strip()]
        if v.chapters:
            head.append("Capitoli: " + "; ".join(t for _, t in v.chapters))
        out = ["\n".join(p for p in head if p)] if any(head) else []
        if v.segments:
            out += TR.sections(v.segments, v.chapters, self.words_per_line, self.max_chars)
        return out
