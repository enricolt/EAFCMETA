"""Quanto pesano i pareri dei creator sul punteggio di una carta.

- Ogni parere ha un VALORE (voto, o quello di sì/dipende/no) e un PESO = peso del creator × freschezza × affidabilità.
- Il parere complessivo è la media pesata dei valori.
- La QUOTA del parere sullo score finale cresce con il peso totale: pochi pareri contano poco, tanti contano fino al
  massimo `score_weights.pro`:  quota = max · n/(n + k),  n = somma dei pesi.
  (con max 0,30 e k 2: un creator → 10%, tre → 18%, otto → 24%).
Tutti i parametri sono in `patch.json` (`opinion_weighting`).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def opinion_value(o: dict, cfg: dict) -> float:
    return o["score"] if o.get("score") is not None else cfg["stance_scores"][o["stance"]]


def _age_days(ts: str | None, now: datetime) -> float:
    try:
        t = datetime.strptime(ts or "", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return max(0.0, (now - t).total_seconds() / 86400)


def opinion_weight(o: dict, cfg: dict, now: datetime | None = None) -> float:
    ow = cfg["opinion_weighting"]
    weights = {k.lower(): v for k, v in ow["creator_weights"].items()}
    w = weights.get(o["creator"].lower(), ow["default_creator_weight"])
    if ow["low_confidence_marker"] and ow["low_confidence_marker"] in (o.get("reason") or ""):
        w *= ow["low_confidence_factor"]
    fresh = 0.5 ** (_age_days(o.get("ts"), now or _now()) / ow["half_life_days"])
    return w * max(fresh, ow["min_recency"])


def aggregate(opinions: list[dict], cfg: dict, now: datetime | None = None) -> dict:
    """{'pro': media pesata|None, 'share': quota sullo score finale, 'n_eff': somma dei pesi, 'items': [...]}"""
    items = []
    for o in opinions:
        items.append({"creator": o["creator"], "stance": o["stance"], "value": opinion_value(o, cfg),
                      "weight": round(opinion_weight(o, cfg, now), 3)})
    total = sum(i["weight"] for i in items)
    if not items or total <= 0:
        return {"pro": None, "share": 0.0, "n_eff": 0.0, "items": items}
    pro = sum(i["value"] * i["weight"] for i in items) / total
    mx, k = cfg["score_weights"]["pro"], cfg["opinion_weighting"]["k"]
    share = mx * total / (total + k)
    for i in items:
        i["influence"] = round(share * i["weight"] / total, 4)  # quota di score finale dovuta a questo parere
    return {"pro": pro, "share": share, "n_eff": round(total, 3), "items": items}
