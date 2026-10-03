"""Fonti dei testi: testo incollato, YouTube (API ufficiale, solo metadati pubblici) e X (API v2 a consumo).

Niente scraper di X/TikTok/Instagram. TikTok e Instagram: nessuna fonte automatica (le API non sono accessibili a un
privato), l'utente incolla il testo (vedi PastedText). Le trascrizioni YouTube NON sono scaricabili con l'API ufficiale
(captions.download richiede i permessi sul video): arrivano da un provider opzionale e iniettabile, mai incluso di default.
Tutto ciò che arriva da una fonte è DATO non fidato: qui non si interpreta né si esegue nulla.
HTTP e provider sono iniettabili, così i test non toccano la rete.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from . import config, text as T

YT_API = "https://www.googleapis.com/youtube/v3"
X_API = "https://api.twitter.com/2"
X_COST_PER_POST = 0.005  # dollari per post letto (X API v2 a consumo; stima indicativa, verificare il listino)
X_MAX_POSTS_ALLOWED = 500
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
SOURCES_NOTE = (
    "Fonti: testo incollato (qualsiasi social); YouTube (metadati pubblici via API ufficiale con YOUTUBE_API_KEY; "
    "le trascrizioni solo se colleghi un provider); X (API a pagamento, token X_BEARER_TOKEN, tetto di spesa "
    "obbligatorio). TikTok e Instagram: nessuna fonte automatica, le loro API non sono accessibili a un privato: "
    "incolla il testo (didascalia o trascrizione) a mano.")


class ResearchError(Exception):
    """Errore della ricerca: il messaggio è in italiano e adatto all'utente."""


class MissingKeyError(ResearchError):
    pass


class QuotaExceededError(ResearchError):
    pass


class NoCaptionsError(ResearchError):
    """Il provider di trascrizioni non ha una trascrizione per quel video."""


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
    source: str = "paste"  # 'paste' | 'youtube' | 'x'
    low_confidence: bool = False  # es. video senza trascrizione: solo titolo/descrizione/capitoli


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


HttpGet = Callable[..., "tuple[int, dict]"]  # (url, params[, headers]) -> (status, json)
TranscriptProvider = Callable[[str, list], str]


def default_http_get(url: str, params: dict, headers: dict | None = None) -> tuple[int, dict]:
    """GET con urllib (solo libreria standard). Non include mai chiavi o token negli errori."""
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    req = urllib.request.Request(full, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - URL fisso https delle API ufficiali
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"servizio non raggiungibile ({type(e).__name__}): controlla la connessione") from None
    except ValueError:
        raise SourceError("risposta del servizio non valida") from None


_CHAPTER = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.{2,80})$", re.M)


def chapters(description: str) -> list[str]:
    """Capitoli del video: righe 'mm:ss titolo' della descrizione (formato dei capitoli di YouTube)."""
    return [f"{t} {title.strip()}" for t, title in _CHAPTER.findall(description or "")]


class YouTubeSource:
    """Cerca i video di UN canale con l'API ufficiale e legge i metadati pubblici (titolo, descrizione, capitoli).

    Quota indicativa: search.list 100 unità, videos.list 1 (quota giornaliera 10.000).
    L'API ufficiale NON dà le trascrizioni dei video altrui: si può iniettare `transcript_provider(video_id, langs) -> str`
    (alza NoCaptionsError se manca). Senza trascrizione l'item è a bassa confidenza (titolo/descrizione/capitoli)."""
    name = "youtube"

    def __init__(self, creator: str, channel_id: str = "", handle: str = "", api_key: str | None = None,
                 http_get: HttpGet | None = None, transcript_provider: TranscriptProvider | None = None,
                 max_videos: int | None = None, languages: list | None = None):
        self.creator, self.channel_id, self.handle = creator, channel_id.strip(), handle.strip()
        self.api_key = api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY", "")
        self.http_get = http_get or default_http_get
        self.transcript_provider = transcript_provider
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
        found: dict[str, dict] = {}
        for it in (body.get("items") or [])[: self.max_videos]:
            vid = (it.get("id") or {}).get("videoId") if isinstance(it.get("id"), dict) else None
            if isinstance(vid, str) and vid:
                found[vid] = it.get("snippet") or {}
        if not found:
            return []
        details: dict[str, dict] = {}
        for v in self._call("videos", {"part": "snippet", "id": ",".join(found)}).get("items") or []:
            if isinstance(v, dict) and isinstance(v.get("id"), str):
                details[v["id"]] = v.get("snippet") or {}
        items, no_tr = [], 0
        for vid, sn0 in found.items():
            sn = details.get(vid) or sn0
            title, desc = str(sn.get("title", "")), str(sn.get("description", ""))[:3000]
            parts = [title, desc]
            ch = chapters(desc)
            if ch:
                parts.append("Capitoli: " + "; ".join(ch))
            transcript = ""
            if self.transcript_provider:
                try:
                    transcript = self.transcript_provider(vid, self.languages)
                except NoCaptionsError as e:
                    self.warnings.append(str(e))
            if transcript:
                parts.append(transcript)
            else:
                no_tr += 1
            items.append(Item(text=T.clean("\n".join(parts)), url=f"https://www.youtube.com/watch?v={vid}",
                              creator=self.creator, date=str(sn.get("publishedAt", ""))[:10], source="youtube",
                              low_confidence=not transcript))
        if no_tr:
            self.warnings.append(f"{no_tr} video senza trascrizione: estratto da titolo, descrizione e capitoli "
                                 "(confidenza ridotta). Per le trascrizioni collega un provider o incolla il testo.")
        return items


class XSource:
    """Post recenti di UN handle con X API v2 (a consumo: ~0,005 $ per post letto, nessun piano gratuito).

    Token in X_BEARER_TOKEN. Il tetto `max_posts` (default 100, massimo 500) è obbligatorio: non si legge mai di più.
    Mai scraping: solo l'API ufficiale."""
    name = "x"

    def __init__(self, creator: str, handle: str, max_posts: int = 100, bearer_token: str | None = None,
                 http_get: HttpGet | None = None):
        if isinstance(max_posts, bool) or not isinstance(max_posts, int) or not 1 <= max_posts <= X_MAX_POSTS_ALLOWED:
            raise ValueError(f"max_posts deve essere tra 1 e {X_MAX_POSTS_ALLOWED}")
        self.creator, self.handle, self.max_posts = creator, handle.strip().lstrip("@"), max_posts
        self.token = bearer_token if bearer_token is not None else os.environ.get("X_BEARER_TOKEN", "")
        self.http_get = http_get or default_http_get
        self.warnings: list[str] = []

    @classmethod
    def for_creator(cls, creator: str, handle: str = "", max_posts: int = 100, **kw) -> "XSource":
        entry = config.channel_for(creator) or {}
        return cls(creator, handle or entry.get("x_handle", ""), max_posts, **kw)

    @staticmethod
    def estimate_cost(max_posts: int) -> float:
        """Costo massimo stimato in dollari (da mostrare PRIMA di eseguire)."""
        return round(max_posts * X_COST_PER_POST, 2)

    def estimate(self) -> str:
        return (f"Leggere fino a {self.max_posts} post di @{self.handle or '?'} costa al massimo circa "
                f"{self.estimate_cost(self.max_posts):.2f} dollari (X API a consumo).")

    def _get(self, path: str, params: dict) -> dict:
        status, body = self.http_get(f"{X_API}/{path}", params, {"Authorization": f"Bearer {self.token}"})
        if status == 200:
            return body
        if status == 429:
            raise QuotaExceededError("limite di richieste di X raggiunto: riprova più tardi")
        if status in (401, 403):
            raise SourceError(f"X ha rifiutato il token (HTTP {status}): controlla X_BEARER_TOKEN e i permessi del piano")
        if status == 402:
            raise SourceError("X ha rifiutato la richiesta: credito esaurito (API a consumo)")
        raise SourceError(f"errore di X (HTTP {status})")

    def search(self, query: str = "") -> list[Item]:
        if not self.token:
            raise MissingKeyError("manca il token di X: imposta la variabile d'ambiente X_BEARER_TOKEN")
        if not self.handle:
            raise ConfigError(f"handle X di '{self.creator}' non configurato: compila x_handle in "
                              "eafcmeta/config/research.json")
        u = self._get(f"users/by/username/{urllib.parse.quote(self.handle, safe='')}", {})
        uid = (u.get("data") or {}).get("id")
        if not isinstance(uid, str) or not uid:
            raise SourceError(f"utente X '@{self.handle}' non trovato")
        items: list[Item] = []
        token = None
        while len(items) < self.max_posts:
            params = {"max_results": max(5, min(100, self.max_posts - len(items))), "exclude": "retweets,replies",
                      "tweet.fields": "created_at"}
            if token:
                params["pagination_token"] = token
            body = self._get(f"users/{uid}/tweets", params)
            for t in body.get("data") or []:
                if len(items) >= self.max_posts:
                    break  # il tetto vale sempre, anche se la pagina ne porta di più
                if isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("text"), str):
                    items.append(Item(text=T.clean(t["text"]), url=f"https://x.com/{self.handle}/status/{t['id']}",
                                      creator=self.creator, date=str(t.get("created_at", ""))[:10], source="x"))
            token = (body.get("meta") or {}).get("next_token")
            if not token or not body.get("data"):
                break
        return [i for i in items if i.text]
