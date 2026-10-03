"""Motore di punteggio: score base da stats/bonus, fusione col pro sentiment, valore vs mercato."""
from __future__ import annotations

import functools
import json
import math
import os
import statistics
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config" / "patch.json"


def local_config_path() -> Path:
    """Calibrazione personale (soglie e pesi dedotti dai pareri dei pro): sopra patch.json, ignorata da git."""
    return Path(os.environ.get("EAFCMETA_LOCAL_CONFIG") or CONFIG_PATH.parent / "local.json")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


_LOCAL = {"active": False}  # la calibrazione locale è davvero applicata? (aggiornato da load_config)


@functools.lru_cache(maxsize=1)
def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_config(cfg)
    if Path(path) != CONFIG_PATH:
        return cfg
    _LOCAL["active"] = False
    local = local_config_path()
    if local.exists():
        try:
            over = json.loads(local.read_text(encoding="utf-8"))
            if not isinstance(over, dict):
                raise ValueError("il file deve contenere un oggetto JSON")
            merged = _merge(cfg, over)
            validate_config(merged)
            _LOCAL["active"] = True
            return merged
        except Exception as e:  # noqa: BLE001 - file locale rotto o di tipo sbagliato: si usa patch.json e si avvisa
            print(f"[config] calibrazione locale ignorata ({type(e).__name__}: {e})", file=sys.stderr)
    return cfg


def local_active() -> bool:
    """True solo se local.json esiste ED è stato letto e applicato (non se è rotto e quindi ignorato)."""
    load_config()
    return _LOCAL["active"]


def _numeric(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_config(cfg: dict) -> None:
    """Controlla la configurazione all'avvio: meglio un errore chiaro subito che un KeyError dopo."""
    for key in ("score_weights", "role_weights", "position_to_role", "playstyle_bonus", "playstyles",
                "body_type_bonus", "verdict", "stat_keys", "max_bonus", "soft_cap_start", "meta", "stance_scores", "opinion_weighting"):
        if key not in cfg:
            raise ValueError(f"patch.json: manca '{key}'")
    for sec in ("verdict", "playstyle_bonus", "stance_scores", "body_type_bonus", "meta", "score_weights"):
        if not isinstance(cfg[sec], dict) or not all(_numeric(v) for v in cfg[sec].values()):
            raise ValueError(f"patch.json: '{sec}' deve contenere solo numeri")
    if not (_numeric(cfg["max_bonus"]) and _numeric(cfg["soft_cap_start"])):
        raise ValueError("patch.json: max_bonus e soft_cap_start devono essere numeri")
    if any(v < 0 for sec in ("verdict", "playstyle_bonus", "score_weights") for v in cfg[sec].values()):
        raise ValueError("patch.json: verdict, playstyle_bonus e score_weights non possono avere valori negativi")
    for role, w in cfg["role_weights"].items():
        if not isinstance(w, dict) or not all(_numeric(v) and v >= 0 for v in w.values()):
            raise ValueError(f"patch.json: i pesi di {role} devono essere numeri non negativi")
    sw = cfg["score_weights"]
    if abs(sw["stats"] + sw["pro"] - 1) > 1e-9:
        raise ValueError("patch.json: score_weights.stats + pro deve fare 1")
    for pos, role in cfg["position_to_role"].items():
        if role not in cfg["role_weights"]:
            raise ValueError(f"patch.json: ruolo '{role}' (posizione {pos}) senza role_weights")
    for role, w in cfg["role_weights"].items():
        bad = [k for k in w if k not in cfg["stat_keys"]]
        if bad or sum(w.values()) <= 0:
            raise ValueError(f"patch.json: pesi non validi per {role}: {bad}")
    for name, p in cfg["playstyles"].items():
        if p["tier"] not in ("S", "A", "B") or any(r not in cfg["role_weights"] for r in p["roles"]):
            raise ValueError(f"patch.json: playstyle '{name}' non valido")
    if set(cfg["stance_scores"]) != {"yes", "maybe", "no"}:
        raise ValueError("patch.json: stance_scores deve avere yes, maybe, no")
    ow = cfg["opinion_weighting"]
    if not (ow["half_life_days"] > 0 and 0 < ow["min_recency"] <= 1 and ow["k"] > 0 and ow["default_creator_weight"] >= 0
            and all(v >= 0 for v in ow["creator_weights"].values())):
        raise ValueError("patch.json: opinion_weighting non valido")
    m = cfg["meta"]
    if not 0 < m["playable"] < m["meta"] < m["top"] <= 100:
        raise ValueError("patch.json: meta deve rispettare playable < meta < top <= 100")
    if not 0 < cfg["soft_cap_start"] < 100:
        raise ValueError("patch.json: soft_cap_start deve essere tra 0 e 100")
    from . import rules  # regole dichiarative (rules.json + stato + ritocchi): errore chiaro se rotte
    rules.validate_config(cfg)


def role_of(position: str, cfg: dict) -> str:
    role = cfg["position_to_role"].get(position)
    if role is None:
        raise ValueError(f"posizione non supportata: {position}")
    return role


def stats_meta(card: dict, cfg: dict) -> float:
    """Media pesata (1-99) delle in-game stats rilevanti per il ruolo."""
    weights = cfg["role_weights"][role_of(card["position"], cfg)]
    stats = card["stats"]
    missing = [k for k in weights if k not in stats]
    if missing:
        raise ValueError(f"stats mancanti per {card['position']}: {', '.join(missing)}")
    return sum(stats[k] * w for k, w in weights.items()) / sum(weights.values())


def bonus_points(card: dict, cfg: dict) -> tuple[float, list[str]]:
    """Bonus totale (limitato a max_bonus) e lista di PlayStyle+ sconosciuti."""
    role = role_of(card["position"], cfg)
    pb = cfg["playstyle_bonus"]
    b, unknown = 0.0, []
    for ps in card.get("playstyles", []):
        if ps.endswith("+"):
            info = cfg["playstyles"].get(ps)
            if info is None:
                unknown.append(ps)
                b += pb["B"]
            else:
                b += pb[info["tier"]] * (1 if role in info["roles"] else pb["off_role_factor"])
        else:
            b += pb["standard"]
    b += cfg["body_type_bonus"].get(card.get("body_type", "Average"), 0)
    if card.get("weak_foot", 0) >= 5:
        b += cfg["weak_foot_5_bonus"]
    if card.get("skill_moves", 0) >= 5:
        b += cfg["skill_moves_5_bonus"]
    return min(b, cfg["max_bonus"]), unknown


def soft_cap(x: float, cfg: dict) -> float:
    """Sopra la soglia lo score cresce sempre più piano verso 100, senza appiattirsi di colpo."""
    s = cfg["soft_cap_start"]
    return x if x <= s else s + (100 - s) * math.tanh((x - s) / (100 - s))


def explain(card: dict, cfg: dict) -> dict:
    sm = stats_meta(card, cfg)
    bonus, unknown = bonus_points(card, cfg)
    from . import rules
    role = role_of(card["position"], cfg)
    rd = rules.evaluate(card, {"role": role, "unknown_playstyles": unknown}, cfg)  # solo regole attive, con tetto
    return {"role": role, "stats_meta": round(sm, 2), "bonus": round(bonus, 2),
            "base": soft_cap(sm + bonus + rd["score_delta"], cfg), "unknown_playstyles": unknown,
            "rules": rd, "rules_delta": rd["score_delta"]}


def base_score(card: dict, cfg: dict) -> float:
    return explain(card, cfg)["base"]


def pro_from_opinions(opinions: list[dict], cfg: dict) -> float | None:
    """Parere complessivo dei creator (media pesata, vedi opinion_model)."""
    from . import opinion_model
    return opinion_model.aggregate(opinions, cfg)["pro"]


def final_score(base: float, pro: float | None, cfg: dict, pro_share: float | None = None) -> float:
    """Se manca il parere dei pro si usa solo lo score base. `pro_share` è la quota del parere (di solito calcolata da
    opinion_model.aggregate); se non indicata vale il massimo `score_weights.pro` (voto manuale senza creator)."""
    if pro is None:
        return base
    share = cfg["score_weights"]["pro"] if pro_share is None else pro_share
    return base * (1 - share) + pro * share


def fit_curve(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """Theil-Sen (robusta agli outlier): score = a + b*ln(prezzo). Ritorna (a, b, sigma_residui) o None."""
    pts = [(math.log(p), s) for p, s in points if p > 0]
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(pts) for x2, y2 in pts[i + 1:]
              if abs(x2 - x1) > 1e-9]
    if not slopes:
        return None
    b = statistics.median(slopes)
    a = statistics.median(y - b * x for x, y in pts)
    res = [y - (a + b * x) for x, y in pts]
    med = statistics.median(res)
    sigma = 1.4826 * statistics.median(abs(r - med) for r in res)
    return a, b, sigma


def verdict(score: float, price: int, market: list[tuple[float, float]], cfg: dict) -> dict:
    """Confronta lo score con quello atteso al prezzo (curva robusta del mercato nella stessa posizione)."""
    v = cfg["verdict"]
    market = [(p, s) for p, s in market if p > 0]
    unreliable = {"verdict": "NEUTRAL", "value_gap": None,
                  "reason": f"Servono almeno {v['min_cards_for_curve']} carte con prezzi diversi nella posizione "
                            f"per un confronto affidabile (ora: {len(market)})."}
    if price <= 0 or len(market) < v["min_cards_for_curve"] or len({p for p, _ in market}) < v["min_distinct_prices"]:
        return unreliable
    curve = fit_curve(market)
    if curve is None or curve[1] <= 0:
        return {**unreliable, "reason": "Nel mercato inserito prezzo e score non sono correlati: confronto non affidabile."}
    lo, hi = min(p for p, _ in market) / v["price_range_factor"], max(p for p, _ in market) * v["price_range_factor"]
    if not lo <= price <= hi:  # niente estrapolazione: la curva vale solo vicino ai prezzi osservati
        return {**unreliable, "reason": f"Prezzo {price:,} fuori dall'intervallo dei prezzi del mercato inserito "
                                        f"({lo:,.0f}-{hi:,.0f}): confronto non affidabile."}
    a, b, sigma = curve
    expected = min(100.0, max(0.0, a + b * math.log(price)))
    gap = score - expected
    thr = max(v["min_gap"], v["k_sigma"] * sigma)
    label = "MUST_DO" if gap >= thr else "AVOID" if gap <= -thr else "NEUTRAL"
    return {"verdict": label, "value_gap": round(gap, 2), "threshold": round(thr, 2),
            "reason": f"Score {score:.1f} vs {expected:.1f} atteso a {price:,} crediti ({gap:+.1f}, soglia ±{thr:.1f})."}
