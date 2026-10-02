import pytest
from fastapi.testclient import TestClient

from eafcmeta import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EAFCMETA_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("EAFCMETA_TOKEN", raising=False)
    with TestClient(api.app) as c:
        yield c


ST = {"acceleration": 90, "sprint_speed": 90, "agility": 85, "reactions": 88, "composure": 85, "finishing": 90,
      "shot_power": 85, "positioning": 88, "ball_control": 87}


def card(**kw):
    return {"name": "X", "position": "ST", "price": 100000, "stats": dict(ST), **kw}
