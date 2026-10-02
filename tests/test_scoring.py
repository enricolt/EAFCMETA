import os

import tempfile

os.environ["EAFCMETA_DB"] = tempfile.mktemp(suffix=".db")
from fastapi.testclient import TestClient

from eafcmeta import api, scoring

CFG = scoring.load_config()
ST = {"acceleration": 90, "sprint_speed": 90, "agility": 85, "reactions": 88, "composure": 85,
      "finishing": 90, "shot_power": 85}


def card(**kw):
    base = {"position": "ST", "stats": ST, "playstyles": [], "body_type": "Average", "weak_foot": 3, "skill_moves": 3}
    return {**base, **kw}


def test_bonus_capped_and_score_le_100():
    c = card(playstyles=["Rapid+", "Finesse Shot+", "Anticipate+", "Whipped Pass+"], body_type="Custom",
             weak_foot=5, skill_moves=5)
    assert scoring.bonus_points(c, CFG) == CFG["max_bonus"]
    assert scoring.base_score(card(stats={k: 99 for k in ST}, **{}), CFG) <= 100


def test_final_without_pro_equals_base():
    assert scoring.final_score(80, None, CFG) == 80
    assert round(scoring.final_score(89.2, 92.0, CFG), 2) == 90.04


def test_unknown_position():
    try:
        scoring.stats_meta(card(position="GK"), CFG)
        assert False
    except ValueError:
        pass


def test_verdict_needs_data():
    assert scoring.verdict(85, 100000, [], CFG)["verdict"] == "NEUTRAL"


def test_verdict_cheap_good_is_must_do():
    market = [(10_000, 75), (50_000, 80), (150_000, 85), (400_000, 88), (1_000_000, 91)]
    assert scoring.verdict(88, 50_000, market, CFG)["verdict"] == "MUST_DO"
    assert scoring.verdict(72, 500_000, market, CFG)["verdict"] == "AVOID"


def test_api_flow():
    c = TestClient(api.app)
    payload = {"name": "X", "position": "ST", "price": 100000, "stats": ST}
    cid = c.post("/api/v1/cards", json=payload).json()["id"]
    r = c.get(f"/api/v1/cards/{cid}/eval").json()
    assert r["scores"]["final_score"] > 80
    r = c.put(f"/api/v1/cards/{cid}/pro", json={"pro_score": 95}).json()
    assert r["scores"]["pro_sentiment_score"] == 95
    assert c.get("/api/v1/cards/999/eval").status_code == 404
    assert c.post("/api/v1/cards", json={**payload, "position": "GK"}).status_code == 422
    assert c.get("/api/v1/cards").status_code == 200


def test_edge_cases():
    market = [(10_000, 75), (50_000, 80), (150_000, 85), (400_000, 88), (1_000_000, 91)]
    assert scoring.verdict(90, 0, market, CFG)["verdict"] == "NEUTRAL"
    assert scoring.verdict(90, 5000, [(0, 80)] * 5, CFG)["verdict"] == "NEUTRAL"
    assert scoring.verdict(90, 5000, [(1000, 80)] * 5, CFG)["verdict"] == "NEUTRAL"
    inverted = [(10_000, 90), (50_000, 85), (150_000, 80), (400_000, 75), (1_000_000, 70)]
    assert scoring.verdict(90, 5000, inverted, CFG)["verdict"] == "NEUTRAL"


def test_missing_and_out_of_range_stats():
    c = TestClient(api.app)
    bad = {"name": "Y", "position": "ST", "price": 1, "stats": {"finishing": 90}}
    assert c.post("/api/v1/cards", json=bad).status_code == 422
    assert c.post("/api/v1/cards", json={**bad, "stats": {**ST, "finishing": 150}}).status_code == 422
