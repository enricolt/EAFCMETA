"""Correzioni della revisione critica, area A: calibrazione (deriva, soglie con classi sbilanciate, coerenza base/final)."""
import json

import pytest

from conftest import ST, card
from eafcmeta import calibration, scoring


@pytest.fixture(autouse=True)
def local_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("EAFCMETA_LOCAL_CONFIG", str(tmp_path / "local.json"))
    scoring.load_config.cache_clear()
    yield
    scoring.load_config.cache_clear()


def _mk(client, n, yes_from):
    """n attaccanti con scatto crescente; i pro dicono sì solo da `yes_from` in su."""
    for i in range(n):
        acc = 60 + i * 2
        stats = {**ST, "acceleration": acc, "sprint_speed": acc, "finishing": 70 + (i * 7) % 20}
        cid = client.post("/api/v1/cards", json=card(name=f"ST{i}", stats=stats, price=1000 * (i + 1))).json()["id"]
        client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "Exeed", "stance": "yes" if i >= yes_from else "no"})


def test_apply_twice_does_not_drift(client):
    _mk(client, 16, 8)
    patch = json.loads(scoring.CONFIG_PATH.read_text())
    first = client.post("/api/v1/calibration/apply", json={"thresholds": True, "weights": True}).json()
    w1 = json.loads(scoring.local_config_path().read_text())["role_weights"]
    for _ in range(6):
        client.post("/api/v1/calibration/apply", json={"thresholds": True, "weights": True})
    local = json.loads(scoring.local_config_path().read_text())
    assert local["role_weights"] == w1  # idempotente: stessi dati, stessi pesi
    for role, ws in local["role_weights"].items():
        for stat, v in ws.items():
            ref = patch["role_weights"][role][stat]
            assert ref * 0.7 - 1e-9 <= v <= ref * 1.3 + 1e-9
    assert first["active"]


def test_weights_never_leave_30_percent_even_if_local_is_far(client):
    _mk(client, 16, 8)
    patch = json.loads(scoring.CONFIG_PATH.read_text())
    far = {"role_weights": {"ST": {k: v * 2 for k, v in patch["role_weights"]["ST"].items()}}}
    scoring.local_config_path().write_text(json.dumps(far))
    scoring.load_config.cache_clear()
    client.post("/api/v1/calibration/apply", json={"thresholds": False, "weights": True})
    local = json.loads(scoring.local_config_path().read_text())
    for stat, v in local["role_weights"]["ST"].items():
        assert v <= patch["role_weights"]["ST"][stat] * 1.3 + 1e-9


def test_imbalanced_classes_do_not_push_threshold_to_100(client):
    _mk(client, 20, 19)  # 19 "no" e 1 "sì"
    rep = client.get("/api/v1/calibration").json()
    assert rep["ready"]
    assert rep["suggested"]["thresholds"] is None and "almeno" in rep["suggested"]["thresholds_message"]
    client.post("/api/v1/calibration/apply", json={"thresholds": True})
    assert scoring.load_config()["meta"]["meta"] == 84


def test_threshold_shift_is_limited_and_ordered(client):
    _mk(client, 20, 14)
    t = client.get("/api/v1/calibration").json()["suggested"]["thresholds"]
    assert abs(t["meta"] - 84) <= 3 + 1e-9
    assert t["playable"] < t["meta"] < t["top"] <= 100


def test_thresholds_stay_valid_near_100():
    cfg = scoring.load_config()
    t = calibration.build_thresholds(cfg, {"top": 99, "meta": 98, "playable": 90}, 99.0)
    assert t["playable"] < t["meta"] < t["top"] <= 100


def test_meta_level_uses_base_everywhere(client):
    """Soglie calibrate e applicate sulla stessa grandezza: lo score base (le statistiche), non il finale coi pareri."""
    cid = client.post("/api/v1/cards", json=card(name="Solo")).json()["id"]
    before = client.get(f"/api/v1/cards/{cid}").json()
    client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "Exeed", "stance": "no", "score": 10})
    after = client.get(f"/api/v1/cards/{cid}").json()
    assert after["scores"]["final_score"] < before["scores"]["final_score"]
    assert after["meta_level"] == before["meta_level"] == after["analysis"]["meta_level"]


def test_active_reflects_real_application(client):
    _mk(client, 16, 8)
    assert client.get("/api/v1/calibration").json()["active"] is False
    scoring.local_config_path().write_text("[1, 2]")  # file esistente ma non valido: NON attivo
    scoring.load_config.cache_clear()
    assert client.get("/api/v1/calibration").json()["active"] is False
    assert client.get("/api/v1/meta").json()["calibrated"] is False
