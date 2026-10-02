import random

import pytest

from conftest import ST, card
from eafcmeta import calibration, scoring


@pytest.fixture(autouse=True)
def local_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("EAFCMETA_LOCAL_CONFIG", str(tmp_path / "local.json"))
    scoring.load_config.cache_clear()
    yield
    scoring.load_config.cache_clear()


def _add(client, n, seed=1):
    """n attaccanti: i pro approvano quelli con scatto alto (la accelerazione guida il loro giudizio)."""
    rnd = random.Random(seed)
    for i in range(n):
        acc = 60 + i * 3
        stats = {**ST, "acceleration": acc, "sprint_speed": acc, "finishing": rnd.randint(78, 92)}
        cid = client.post("/api/v1/cards", json=card(name=f"ST{i}", stats=stats, price=1000 * (i + 1))).json()["id"]
        client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "Exeed", "stance": "yes" if acc >= 80 else "no"})


def test_not_ready_with_few_cards(client):
    _add(client, 3)
    r = client.get("/api/v1/calibration").json()
    assert r["ready"] is False and r["n"] == 3 and "Servono almeno 12" in r["message"] and "suggested" not in r
    assert client.post("/api/v1/calibration/apply", json={}).status_code == 422


def test_report_and_suggestions(client):
    _add(client, 14)
    r = client.get("/api/v1/calibration").json()
    assert r["ready"] and r["n"] == 14 and r["correlation"] > 0.5
    sug = r["suggested"]
    assert sug["after"]["accuracy"] >= r["current"]["accuracy"]
    t = sug["thresholds"]
    assert t["playable"] < t["meta"] < t["top"] <= 100
    st = sug["weights"]["ST"]
    assert st["n"] == 14
    by = {c["stat"]: c for c in st["changes"]}
    assert by["acceleration"]["corr"] > 0.8 and by["acceleration"]["new"] > by["acceleration"]["old"]
    assert all(0.2 <= c["new"] <= c["old"] * 1.5 for c in st["changes"])  # il cambio è sempre contenuto


def test_apply_and_reset(client):
    _add(client, 14)
    rep = client.get("/api/v1/calibration").json()
    before = client.get("/api/v1/cards").json()[0]["scores"]["base_score"]
    out = client.post("/api/v1/calibration/apply", json={"thresholds": True, "weights": True}).json()
    assert out["active"] and scoring.load_config()["meta"]["meta"] == rep["suggested"]["thresholds"]["meta"]
    assert client.get("/api/v1/meta").json()["calibrated"] is True
    assert client.get("/api/v1/cards").json()[0]["scores"]["base_score"] != before  # i pesi nuovi cambiano gli score
    after = client.post("/api/v1/calibration/reset").json()
    assert after["active"] is False and scoring.load_config()["meta"]["meta"] == 84
    assert client.get("/api/v1/cards").json()[0]["scores"]["base_score"] == before


def test_community_opinion_is_not_a_pro(client):
    for i in range(14):
        cid = client.post("/api/v1/cards", json=card(name=f"C{i}", price=1000 * (i + 1))).json()["id"]
        client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "FUT.GG (community)", "stance": "yes"})
    assert client.get("/api/v1/calibration").json()["n"] == 0


def test_broken_local_config_is_ignored(tmp_path):
    (tmp_path / "local.json").write_text('{"meta": {"top": 10, "meta": 50, "playable": 70}}')
    scoring.load_config.cache_clear()
    assert scoring.load_config()["meta"]["meta"] == 84  # file non valido: si usano i valori originali
