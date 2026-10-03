"""Fonti dei testi: testo incollato dall'utente e YouTube (API ufficiale).

Niente scraper di X/TikTok/Instagram: per quei social l'utente incolla il testo (vedi PastedText).
Tutto ciò che arriva da una fonte è DATO non fidato: qui non si interpreta né si esegue nulla.
HTTP e trascrizioni sono iniettabili, così i test non toccano la rete.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from . import config, text as T

YT_API = "https://www.googleapis.com/youtube/v3"
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}


class ResearchError(Exception):
    """Errore della ricerca: il messaggio è in italiano e adatto all'utente."""


class MissingKeyError(ResearchError):
    pass


class QuotaExceededError(ResearchError):
    pass


class NoCaptionsError(ResearchError):
    pass


class SourceError(ResearchError):
    pass


class ConfigError(ResearchError):
    """Configurazione mancante (es. canale non compilato in research.json)."""


@dataclass
class Item:
    text: str
    url: str = ""
    creator: str = ""
    date: str = ""
    source: str = "paste"  # 'paste' | 'youtube'


class Source(Protocol):
    name: str

    def search(self, query: str) -> list[Item]: ...


class PastedText:
    """Testo incollato (post, didascalia, trascrizione di qualunque social). La query è ignorata."""
    name = "paste"

    def __init__(self, text: str, creator: str, url: str = "", date: str = ""):
        self.item = Item(text=T.clean(text), url=url.strip(), creator=creator.strip(), date=date, source="paste")

    def search(self, query: str = "") -> list[Item]:
        return [self.item] if self.item.text else []


HttpGet = Callable[[str, dict], "tuple[int, dict]"]
TranscriptFetcher = Callable[[str, list], str]


def default_http_get(url: str, params: dict) -> tuple[int, dict]:
    """GET con urllib (solo libreria standard). Non include mai la chiave negli errori."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full, timeout=15) as r:  # noqa: S310 - URL fisso https dell'API ufficiale
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"YouTube non raggiungibile ({type(e).__name__}): controlla la connessione") from None
    except ValueError:
        raise SourceError("risposta di YouTube non valida") from None


def default_transcript_fetcher(video_id: str, languages: list) -> str:
    """Sottotitoli tramite la libreria OPZIONALE youtube-transcript-api (non nei requirements).
    Nota: usa un endpoint non documentato di YouTube; è opzionale e sostituibile iniettando un fetcher."""
    try:
        import youtube_transcript_api as yta  # type: ignore
    except ImportError:
        raise NoCaptionsError("libreria 'youtube-transcript-api' non installata: impossibile leggere i sottotitoli "
                              "(pip install youtube-transcript-api) oppure incolla la trascrizione a mano") from None
    try:
        api = yta.YouTubeTranscriptApi
        if hasattr(api, "get_transcript"):
            parts = api.get_transcript(video_id, languages=languages)
            return " ".join(p["text"] for p in parts)
        parts = api().fetch(video_id, languages=languages)
        return " ".join(getattr(p, "text", "") for p in parts)
    except Exception as e:  # la libreria ha molte eccezioni specifiche: qui basta dire che mancano
        raise NoCaptionsError(f"nessun sottotitolo disponibile per il video {video_id} ({type(e).__name__})") from None


class YouTubeSource:
    """Cerca i video di UN canale (YouTube Data API v3, chiave in YOUTUBE_API_KEY) e ne legge i sottotitoli."""
    name = "youtube"

    def __init__(self, creator: str, channel_id: str = "", handle: str = "", api_key: str | None = None,
                 http_get: HttpGet | None = None, transcript_fetcher: TranscriptFetcher | None = None,
                 max_videos: int | None = None, languages: list | None = None):
        self.creator, self.channel_id, self.handle = creator, channel_id.strip(), handle.strip()
        self.api_key = api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY", "")
        self.http_get = http_get or default_http_get
        self.fetch_transcript = transcript_fetcher or default_transcript_fetcher
        self.max_videos = max_videos or config.limit("max_videos")
        self.languages = languages or config.research_config().get("youtube", {}).get("languages", ["it", "en"])
        self.warnings: list[str] = []

    @classmethod
    def for_creator(cls, creator: str, **kw) -> "YouTubeSource":
        entry = config.channel_for(creator) or {}
        return cls(creator, entry.get("channel_id", ""), entry.get("handle", ""), **kw)

    def _call(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise MissingKeyError("manca la chiave YouTube: imposta la variabile d'ambiente YOUTUBE_API_KEY")
        status, body = self.http_get(f"{YT_API}/{path}", {**params, "key": self.api_key})
        if status == 200:
            return body
        reasons = {e.get("reason") for e in (body.get("error", {}).get("errors") or []) if isinstance(e, dict)}
        if status in (403, 429) and reasons & _QUOTA_REASONS:
            raise QuotaExceededError("quota giornaliera di YouTube esaurita: riprova domani o usa il testo incollato")
        if status in (400, 401, 403):
            raise SourceError(f"YouTube ha rifiutato la richiesta (HTTP {status}): controlla la chiave API")
        raise SourceError(f"errore di YouTube (HTTP {status})")

    def _channel(self) -> str:
        if self.channel_id:
            return self.channel_id
        if not self.handle:
            raise ConfigError(f"canale di '{self.creator}' non configurato: compila channel_id o handle in "
                              "eafcmeta/config/research.json")
        body = self._call("channels", {"part": "id", "forHandle": self.handle})
        items = body.get("items") or []
        if not items or not isinstance(items[0].get("id"), str):
            raise SourceError(f"canale '{self.handle}' non trovato su YouTube")
        self.channel_id = items[0]["id"]
        return self.channel_id

    def search(self, query: str) -> list[Item]:
        if not self.api_key:  # errore chiaro prima di tutto
            raise MissingKeyError("manca la chiave YouTube: imposta la variabile d'ambiente YOUTUBE_API_KEY")
        channel = self._channel()
        body = self._call("search", {"part": "snippet", "type": "video", "channelId": channel, "q": query,
                                     "maxResults": self.max_videos, "order": "relevance"})
        items, missing = [], 0
        for it in (body.get("items") or [])[: self.max_videos]:
            vid = (it.get("id") or {}).get("videoId") if isinstance(it.get("id"), dict) else None
            sn = it.get("snippet") or {}
            if not isinstance(vid, str) or not vid:
                continue
            try:
                transcript = self.fetch_transcript(vid, self.languages)
            except NoCaptionsError as e:
                missing += 1
                self.warnings.append(str(e))
                continue
            title = str(sn.get("title", ""))
            items.append(Item(text=T.clean(f"{title}\n{transcript}"), url=f"https://www.youtube.com/watch?v={vid}",
                              creator=self.creator, date=str(sn.get("publishedAt", ""))[:10], source="youtube"))
        if not items and missing:
            raise NoCaptionsError(f"{missing} video trovati ma nessuno ha sottotitoli leggibili: "
                                  "incolla la trascrizione a mano")
        return items
