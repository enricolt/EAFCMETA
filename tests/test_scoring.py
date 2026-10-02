import json

import pytest

from eafcmeta import scoring

CFG = scoring.load_config()
from conftest import ST  # noqa: E402


def card(**kw):
    return {"position": "ST", "stats": ST, "playstyles": [], "body_type": "Average", "weak_foot": 3,
            "skill_moves": 3, **kw}


def test_bonus_capped_and_soft_cap_below_100():
    c = card(playstyles=["Rapid+", "Finesse Shot+", "Anticipate+", "Whipped Pass+", "Technical+"], body_type="Custom",
             weak_foot=5, skill_moves=5)
    assert scoring.bonus_points(c, CFG)[0] == CFG["max_bonus"]
    top = scoring.base_score(card(stats={k: 99 for k in ST}, playstyles=["Rapid+"] * 1), CFG)
    assert top < 100
    assert scoring.soft_cap(80, CFG) == 80 and scoring.soft_cap(95, CFG) < 95


def test_soft_cap_keeps_order():
    xs = [88, 90, 92, 95, 100, 110]
    ys = [scoring.soft_cap(x, CFG) for x in xs]
    assert ys == sorted(ys) and len(set(ys)) == len(ys)


def test_playstyle_role_dependence():
    st = scoring.bonus_points(card(playstyles=["Finesse Shot+"]), CFG)[0]
    cb = scoring.bonus_points(card(position="CB", playstyles=["Finesse Shot+"]), CFG)[0]
    assert st > cb
    assert scoring.bonus_points(card(playstyles=["Mai Visto+"]), CFG)[1] == ["Mai Visto+"]


def test_final_score():
    assert scoring.final_score(80, None, CFG) == 80
    assert round(scoring.final_score(89.2, 92.0, CFG), 2) == 90.04


def test_unknown_position_and_missing_stats():
    with pytest.raises(ValueError):
        scoring.stats_meta(card(position="GK"), CFG)
    with pytest.raises(ValueError):
        scoring.stats_meta(card(stats={"finishing": 90}), CFG)


MARKET = [(5_000, 70), (12_000, 74), (30_000, 78), (70_000, 82), (150_000, 85), (400_000, 88), (900_000, 91),
          (2_000_000, 93)]


def test_verdict_must_do_avoid_neutral():
    assert scoring.verdict(90, 30_000, MARKET, CFG)["verdict"] == "MUST_DO"
    assert scoring.verdict(70, 400_000, MARKET, CFG)["verdict"] == "AVOID"
    assert scoring.verdict(78, 30_000, MARKET, CFG)["verdict"] == "NEUTRAL"


def test_verdict_edge_cases():
    assert scoring.verdict(90, 0, MARKET, CFG)["value_gap"] is None
    assert scoring.verdict(90, 5000, MARKET[:5], CFG)["value_gap"] is None  # troppe poche carte
    assert scoring.verdict(90, 5000, [(1000, 80)] * 9, CFG)["value_gap"] is None  # stesso prezzo
    inverted = [(p, 100 - i * 4) for i, (p, _) in enumerate(MARKET)]
    assert scoring.verdict(90, 5000, inverted, CFG)["value_gap"] is None  # prezzo e score anticorrelati


def test_verdict_robust_to_outlier():
    out = MARKET + [(50_000, 40)]  # un solo prezzo anomalo
    base = scoring.verdict(90, 30_000, MARKET, CFG)["value_gap"]
    assert abs(scoring.verdict(90, 30_000, out, CFG)["value_gap"] - base) < 3


def test_validate_config():
    bad = json.loads(json.dumps(CFG))
    bad["score_weights"]["pro"] = 0.5
    with pytest.raises(ValueError):
        scoring.validate_config(bad)
    bad = json.loads(json.dumps(CFG))
    del bad["role_weights"]["CB"]
    with pytest.raises(ValueError):
        scoring.validate_config(bad)
