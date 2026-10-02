from pydantic import BaseModel, Field, field_validator, model_validator

from . import scoring


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

    @field_validator("name", "version")
    @classmethod
    def _strip(cls, v):
        return v.strip()

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


class PageIn(BaseModel):
    name: str = Field(max_length=200)
    html: str = Field(max_length=4_000_000)


class PagesIn(BaseModel):
    pages: list[PageIn] = Field(max_length=40)
    dry_run: bool = True
