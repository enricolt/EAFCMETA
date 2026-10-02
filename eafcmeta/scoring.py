"""Motore di punteggio: score base da stats/bonus, fusione col pro sentiment, valore vs mercato."""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config" / "patch.json"


@functools.lru_cache(maxsize=1)
def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text())


def stats_meta(card: dict, cfg: dict) -> float:
    """Media pesata (0-100) delle in-game stats rilevanti per il ruolo."""
    role = cfg["position_to_role"].get(card["position"])
    if role is None:
        raise ValueError(f"posizione non supportata: {card['position']}")
    weights = cfg["role_weights"][role]
    stats = card["stats"]
    missing = [k for k in weights if k not in stats]
    if missing:
        raise ValueError(f"stats mancanti per {card['position']}: {', '.join(missing)}")
    return sum(stats[k] * w for k, w in weights.items()) / sum(weights.values())


def bonus_points(card: dict, cfg: dict) -> float:
    b = 0.0
    tiers = cfg["playstyle_tiers"]
    pb = cfg["playstyle_bonus"]
    for ps in card.get("playstyles", []):
        if ps.endswith("+"):
            b += pb["plus_s"] if tiers.get(ps) == "S" else pb["plus_a"]
        else:
            b += pb["standard"]
    b += cfg["body_type_bonus"].get(card.get("body_type", "Average"), 0)
    if card.get("weak_foot", 0) >= 5:
        b += cfg["weak_foot_5_bonus"]
    if card.get("skill_moves", 0) >= 5:
        b += cfg["skill_moves_5_bonus"]
    return min(b, cfg["max_bonus"])


def base_score(card: dict, cfg: dict) -> float:
    return min(100.0, stats_meta(card, cfg) + bonus_points(card, cfg))


def final_score(base: float, pro: float | None, cfg: dict) -> float:
    """Se manca il parere dei pro, si usa solo lo score base."""
    if pro is None:
        return base
    w = cfg["score_weights"]
    return base * w["stats"] + pro * w["pro"]


def fit_curve(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Regressione lineare score = a + b*ln(prezzo). Ritorna (a, b) o None."""
    pts = [(math.log(p), s) for p, s in points if p > 0]
    n = len(pts)
    if n < 2:
        return None
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    var = sum((x - mx) ** 2 for x, _ in pts)
    if var == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in pts) / var
    return my - b * mx, b


def verdict(score: float, price: int, market: list[tuple[float, float]], cfg: dict) -> dict:
    """Confronta lo score con quello atteso al prezzo (curva del mercato)."""
    v = cfg["verdict"]
    market = [(p, s) for p, s in market if p > 0]
    curve = fit_curve(market) if len(market) >= v["min_cards_for_curve"] else None
    if price <= 0 or curve is None or curve[1] <= 0:
        return {"verdict": "NEUTRAL", "value_gap": None,
                "reason": "Dati di mercato insufficienti o non affidabili per un confronto."}
    a, b = curve
    expected = a + b * math.log(max(price, 1))
    gap = score - expected
    if gap >= v["must_do_gap"]:
        label = "MUST_DO"
    elif gap <= v["avoid_gap"]:
        label = "AVOID"
    else:
        label = "NEUTRAL"
    return {"verdict": label, "value_gap": round(gap, 2),
            "reason": f"Score {score:.1f} vs {expected:.1f} atteso a {price:,} crediti ({gap:+.1f})."}
