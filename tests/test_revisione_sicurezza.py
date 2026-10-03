"""Revisione critica, area F: validazioni, token, config locale, limiti, upsert concorrente."""
import importlib.util
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from conftest import card
from eafcmeta import api, db, scoring
from test_collect import gg_page

ROOT = Path(__file__).parent.parent


def test_blank_names_are_rejected(client):
    for bad in ("   ", "\t\n", ""):
        assert client.post("/api/v1/cards", json=card(name=bad)).status_code == 422
    cid = client.post("/api/v1/cards", json=card(name="  Ok  ")).json()["id"]
    assert client.get(f"/api/v1/cards/{cid}").json()["name"] == "Ok"
    assert client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "   ", "stance": "yes"}).status_code == 422


def test_non_numeric_site_signals_do_not_give_500(client):
    r = client.post("/api/v1/cards", json=card(name="S", signals={"gg_rating": "abc", "gg_role": "ST"}))
    assert r.status_code == 422  # rifiutato all'ingresso...
    cid = client.post("/api/v1/cards", json=card(name="S2")).json()["id"]
    conn = db.connect()
    d = json.loads(conn.execute("SELECT data FROM cards WHERE id=?", (cid,)).fetchone()["data"])
    d["signals"] = {"gg_rating": "abc", "gg_rank": "x", "futbin_rating": "n/a"}  # ... e anche dati vecchi/sporchi
    conn.execute("UPDATE cards SET data=? WHERE id=?", (json.dumps(d), cid))
    conn.commit()
    conn.close()
    assert client.get(f"/api/v1/cards/{cid}").status_code == 200
    assert client.get("/api/v1/cards").status_code == 200


def test_token_non_ascii_gives_401_not_500(client, monkeypatch):
    monkeypatch.setenv("EAFCMETA_TOKEN", "segreto")
    for tok in ("caffè".encode("utf-8"), "è".encode("latin-1")):
        r = client.get("/api/v1/cards", headers={"X-Token": tok})
        assert r.status_code == 401
    assert client.get("/api/v1/cards", headers={"X-Token": "segreto"}).status_code == 200
    assert client.get("/api/v1/cards").status_code == 401


def test_local_config_with_wrong_types_is_ignored_with_warning(tmp_path, monkeypatch, capsys):
    path = tmp_path / "local.json"
    monkeypatch.setenv("EAFCMETA_LOCAL_CONFIG", str(path))
    base_meta = json.loads(scoring.CONFIG_PATH.read_text())["meta"]["meta"]
    for bad in ("[1, 2]", '{"verdict": {"min_gap": "tanto"}}', '{"role_weights": {"ST": {"finishing": -3}}}',
                '{"stance_scores": {"yes": "x", "maybe": 1, "no": 2}}', '{"playstyle_bonus": {"S": null}}',
                '{"meta": [1]}', '{"score_weights": {"stats": 1.2, "pro": -0.2}}', '"testo"', "null"):
        path.write_text(bad)
        scoring.load_config.cache_clear()
        cfg = scoring.load_config()
        assert cfg["meta"]["meta"] == base_meta, bad
        assert scoring.local_active() is False, bad
        assert "calibrazione locale ignorata" in capsys.readouterr().err, bad
    path.write_text('{"meta": {"top": 91, "meta": 85, "playable": 77}}')
    scoring.load_config.cache_clear()
    assert scoring.load_config()["meta"]["meta"] == 85 and scoring.local_active() is True
    scoring.load_config.cache_clear()


def test_validate_config_checks_numbers_and_weights():
    cfg = json.loads(scoring.CONFIG_PATH.read_text())
    scoring.validate_config(cfg)
    for path, val in ((("verdict", "min_gap"), "x"), (("playstyle_bonus", "S"), "4"), (("stance_scores", "yes"), None),
                      (("role_weights", "ST", "finishing"), -1), (("playstyle_bonus", "A"), -2)):
        bad = json.loads(json.dumps(cfg))
        d = bad
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = val
        with pytest.raises(ValueError):
            scoring.validate_config(bad)


def test_add_card_survives_concurrent_upsert(client, monkeypatch):
    real, calls = db.upsert_card, []

    def flaky(conn, c):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.IntegrityError("UNIQUE constraint failed: cards.name")
        return real(conn, c)

    monkeypatch.setattr(db, "upsert_card", flaky)
    r = client.post("/api/v1/cards", json=card(name="Race"))
    assert r.status_code == 201 and len(calls) == 2  # riprova e riesce

    def always(conn, c):
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(db, "upsert_card", always)
    assert client.post("/api/v1/cards", json=card(name="Race2")).status_code == 409


def test_import_pages_limits(client):
    page = {"name": "a", "html": "<html></html>"}
    r = client.post("/api/v1/import/pages", json={"pages": [page] * 13, "dry_run": True})
    assert r.status_code == 422 and "12" in r.text
    big = {"name": "big", "html": "x" * 3_000_001}
    r = client.post("/api/v1/import/pages", json={"pages": [big], "dry_run": True})
    assert r.status_code == 422 and "3 MB" in r.text
    ok = client.post("/api/v1/import/pages", json={"pages": [page] * 12, "dry_run": True})
    assert ok.status_code == 200
    assert client.post("/api/v1/import/pages", json={"pages": [{"name": "g", "html": gg_page()}], "dry_run": True}).status_code == 200


def _load_start():
    spec = importlib.util.spec_from_file_location("start_under_test_f", ROOT / "start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(sys.platform == "win32", reason="permessi POSIX")
def test_lan_token_file_is_private(tmp_path, monkeypatch):
    start = _load_start()
    monkeypatch.setattr(start, "DATA", tmp_path)
    t = start.lan_token()
    f = tmp_path / ".token"
    assert f.read_text().strip() == t and stat.S_IMODE(os.stat(f).st_mode) == 0o600
    os.chmod(f, 0o644)  # un .token creato da una versione precedente: viene ristretto alla prossima lettura
    assert start.lan_token() == t and stat.S_IMODE(os.stat(f).st_mode) == 0o600
