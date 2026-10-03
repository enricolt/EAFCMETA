import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import scoring


ACCELERATES = ("Explosive", "Controlled", "Lengthy")
NUMERIC_SIGNALS = ("gg_rating", "gg_rank", "gg_tier_pct", "gg_tier_votes", "futbin_rating", "futbin_rank")


class RoleRating(BaseModel):
    """Voto di un ruolo (es. FUTBIN Rating o GG Rating per posizione) letto dai siti."""
    role: str = Field(min_length=1, max_length=8)
    rating: float = Field(ge=0, le=100)
    name: str = Field("", max_length=40)
    site: Literal["futgg", "futbin"] | None = None
    rank: int | None = Field(None, ge=1, le=10_000_000)


class CardIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    version: str = Field("", max_length=64)
    position: str
    price: int = Field(ge=0, le=100_000_000)
    is_sbc: bool = False
    stats: dict[str, int] = Field(max_length=40)
    playstyles: list[str] = Field(default_factory=list, max_length=12)
    body_type: str = "Average"
    weak_foot: int = Field(3, ge=1, le=5)
    skill_moves: int = Field(3, ge=1, le=5)
    signals: dict[str, float | int | str] = Field(default_factory=dict, max_length=12)
    # dati opzionali letti dai siti (nel JSON "data"); assenti = sconosciuti, non cancellano quelli già salvati
    height_cm: int | None = Field(None, ge=100, le=230)
    weight_kg: int | None = Field(None, ge=30, le=160)
    accelerate: str | None = None
    foot: str | None = None
    club: str | None = Field(None, max_length=64)
    league: str | None = Field(None, max_length=64)
    nation: str | None = Field(None, max_length=64)
    age: int | None = Field(None, ge=10, le=70)
    chem_style_top: str | None = Field(None, max_length=24)
    roles: list[RoleRating] = Field(default_factory=list, max_length=24)

    @field_validator("accelerate")
    @classmethod
    def _accelerate(cls, v):
        if v is None:
            return v
        known = {a.lower(): a for a in ACCELERATES}
        if v.strip().lower() not in known:
            raise ValueError(f"AcceleRATE sconosciuto: {v} (validi: {', '.join(ACCELERATES)})")
        return known[v.strip().lower()]

    @field_validator("foot")
    @classmethod
    def _foot(cls, v):
        if v is None:
            return v
        if v.strip().lower() not in ("right", "left"):
            raise ValueError("piede preferito: Right o Left")
        return v.strip().capitalize()

    @field_validator("club", "league", "nation", "chem_style_top")
    @classmethod
    def _strip_opt(cls, v):
        return (v.strip() or None) if v is not None else v

    @field_validator("signals")
    @classmethod
    def _signals(cls, v):
        allowed = {"gg_rating", "gg_role", "gg_rank", "gg_tier", "gg_tier_pct", "gg_tier_votes",
                   "futbin_rating", "futbin_role", "futbin_rank"}
        if set(v) - allowed or any(isinstance(x, str) and len(x) > 40 for x in v.values()):
            raise ValueError("segnali non validi")
        out = dict(v)
        for k in NUMERIC_SIGNALS:  # voti e classifiche devono essere numeri (anche scritti come testo: "87.9")
            if k in out and not isinstance(out[k], (int, float)):
                try:
                    out[k] = float(str(out[k]).replace(",", "."))
                except ValueError:
                    raise ValueError(f"segnale {k} non numerico: {out[k]!r}") from None
            if k in out and not math.isfinite(out[k]):
                raise ValueError(f"segnale {k} non valido")
        return out

    @field_validator("name", "version")
    @classmethod
    def _strip(cls, v, info):
        v = v.strip()
        if info.field_name == "name" and not v:  # "   " passa min_length ma dopo lo strip e' vuoto
            raise ValueError("il nome non può essere vuoto")
        return v

    @field_validator("position")
    @classmethod
    def _position(cls, v):
        v = v.strip().upper()
        if v not in scoring.load_config()["position_to_role"]:
            raise ValueError(f"posizione non supportata: {v}")
        return v

    @field_validator("body_type")
    @classmethod
    def _body(cls, v):
        known = {k.lower(): k for k in scoring.load_config()["body_type_bonus"]}
        if v.strip().lower() not in known:
            raise ValueError(f"body type sconosciuto: {v} (validi: {', '.join(known.values())})")
        return known[v.strip().lower()]

    @field_validator("stats")
    @classmethod
    def _stats(cls, v):
        keys = set(scoring.load_config()["stat_keys"])
        bad = [k for k in v if k not in keys]
        if bad:
            raise ValueError(f"stat sconosciute: {', '.join(bad)}")
        if any(not 1 <= x <= 99 for x in v.values()):
            raise ValueError("le stats devono essere tra 1 e 99")
        return v

    @field_validator("playstyles")
    @classmethod
    def _ps(cls, v):
        out = []
        for p in (x.strip() for x in v):
            if not p or len(p) > 40:
                raise ValueError("PlayStyle vuoto o troppo lungo")
            if p not in out:
                out.append(p)
        return out

    @model_validator(mode="after")
    def _required_stats(self):
        scoring.stats_meta({"position": self.position, "stats": self.stats}, scoring.load_config())
        return self


class ProIn(BaseModel):
    pro_score: float | None = Field(None, ge=0, le=100)
    notes: str = Field("", max_length=500)


class ImportIn(BaseModel):
    text: str = Field(max_length=300_000)
    dry_run: bool = True


MAX_PAGES = 12  # pagine per richiesta
MAX_PAGE_BYTES = 3_000_000  # 3 MB ciascuna


class PageIn(BaseModel):
    name: str = Field(max_length=200)
    html: str

    @field_validator("html")
    @classmethod
    def _size(cls, v):
        if len(v) > MAX_PAGE_BYTES:
            raise ValueError(f"pagina troppo grande (massimo {MAX_PAGE_BYTES // 1_000_000} MB): salva solo la pagina del giocatore")
        return v


class PagesIn(BaseModel):
    pages: list[PageIn]
    dry_run: bool = True

    @field_validator("pages")
    @classmethod
    def _count(cls, v):
        if len(v) > MAX_PAGES:
            raise ValueError(f"troppe pagine in una richiesta (massimo {MAX_PAGES}): importale a gruppi")
        return v


class OpinionIn(BaseModel):
    """Parere di un creator. Il voto, se c'è, deve essere coerente con sì/dipende/no."""
    creator: str = Field(min_length=1, max_length=40)
    stance: Literal["yes", "maybe", "no"]
    score: float | None = Field(None, ge=0, le=100)
    reason: str = Field("", max_length=800)
    url: str = Field("", max_length=300)

    @field_validator("creator", "reason", "url")
    @classmethod
    def _strip(cls, v, info):
        v = v.strip()
        if info.field_name == "creator" and not v:
            raise ValueError("il nome del creator non può essere vuoto")
        return v

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("il link deve iniziare con http:// o https://")
        return v

    @model_validator(mode="after")
    def _coherent(self):
        if self.score is not None and ((self.stance == "yes" and self.score < 70) or (self.stance == "no" and self.score > 60)):
            raise ValueError("il voto non è coerente con la scelta (sì ≥ 70, no ≤ 60)")
        return self
