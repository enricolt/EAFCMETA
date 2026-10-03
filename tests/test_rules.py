import json
import sqlite3

import pytest

from conftest import ST, card
from eafcmeta import calibration, db, rules, scoring


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Config locale, stato delle regole e rules.json isolati per ogni test."""
    monkeypatch.setenv("EAFCMETA_LOCAL_CONFIG", str(tmp_path / "local.json"))
    scoring.load_config.cache_clear()
    rules.clear_cache()
    yield
    monkeypatch.undo()
    scoring.load_config.cache_clear()
    rules.clear_cache()


def _cfg(overrides=None):
    cfg = scoring.load_config()
    return {**cfg, "rules": {"overrides": overrides}} if overrides else cfg


def _ctx(role="ST", **kw):
    c = card(**kw)
    return rules.make_ctx(c, role, scoring.load_config(), kw.get("unknown", ()))


def _write_rules(path, mutate):
    data = json.loads(rules.RULES_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- tipi di condizione: un positivo e un negativo ciascuno

def test_when_stat():
    assert rules.match({"type": "stat", "stat": "finishing", "gte": 88}, _ctx())["value"] == 90
    assert rules.match({"type": "stat", "stat": "finishing", "gte": 95}, _ctx()) is None
    assert rules.match({"type": "stat", "stat": "volleys", "gte": 1}, _ctx()) is None  # stat assente: non vale


def test_when_avg():
    w = {"type": "avg", "stats": ["acceleration", "sprint_speed"], "gte": 88}
    assert rules.match(w, _ctx())["value"] == 90
    assert rules.match(w, _ctx(stats={**ST, "acceleration": 60, "sprint_speed": 60})) is None
    assert rules.match({**w, "stats": ["inesistente"]}, _ctx()) is None


def test_when_role_top_and_weak_stats():
    top = {"type": "role_top_stats", "top_n": 6, "gte": 88, "max_items": 4}
    assert "finalizzazione 90" in rules.match(top, _ctx())["items"]
    assert rules.match(top, _ctx(stats={k: 60 for k in ST})) is None
    weak = {"type": "role_weak_stats", "min_weight": 2, "lt": 70, "max_items": 3}
    assert rules.match(weak, _ctx()) is None
    assert rules.match(weak, _ctx(stats={k: 60 for k in ST})) is not None


def test_when_playstyle_and_group():
    c = _ctx(playstyles=["Finesse Shot+", "Anticipate+", "Rapid"])
    assert rules.match({"type": "playstyle", "name": "Finesse Shot+"}, c)
    assert rules.match({"type": "playstyle", "name": "Block+"}, c) is None
    assert "Finesse Shot+ (tier S)" in rules.match({"type": "playstyle_group", "group": "in_role"}, c)["items"]
    assert "Anticipate+" in rules.match({"type": "playstyle_group", "group": "off_role"}, c)["items"]
    assert rules.match({"type": "playstyle_group", "group": "unknown"}, c) is None
    assert rules.match({"type": "playstyle_group", "group": "unknown"}, _ctx(unknown=["Boh+"]))


def test_when_body_type_stars_height_weight_price():
    assert rules.match({"type": "body_type", "in": ["Lean"]}, _ctx(body_type="Lean"))
    assert rules.match({"type": "body_type", "in": ["Lean"]}, _ctx(body_type="Stocky")) is None
    assert rules.match({"type": "skill_moves", "gte": 5}, _ctx(skill_moves=5))
    assert rules.match({"type": "skill_moves", "gte": 5}, _ctx(skill_moves=3)) is None
    assert rules.match({"type": "weak_foot", "lte": 2}, _ctx(weak_foot=1))
    assert rules.match({"type": "weak_foot", "lte": 2}, _ctx()) is None  # default 3 se manca
    assert rules.match({"type": "height_cm", "gte": 185}, _ctx(height_cm=190))
    assert rules.match({"type": "height_cm", "gte": 185}, _ctx(height_cm=170)) is None
    assert rules.match({"type": "height_cm", "gte": 185}, _ctx()) is None  # altezza non disponibile
    assert rules.match({"type": "weight_kg", "lte": 70}, _ctx(weight_kg=65))
    assert rules.match({"type": "weight_kg", "lte": 70}, _ctx()) is None
    assert rules.match({"type": "price", "lte": 1000}, _ctx(price=500))
    assert rules.match({"type": "price", "lte": 1000}, _ctx(price=5000)) is None


def test_roles_and_exceptions():
    assert rules.applies_to({"roles": ["ST"]}, "ST") and not rules.applies_to({"roles": ["ST"]}, "CB")
    assert rules.applies_to({"roles_except": ["GK"]}, "ST") and not rules.applies_to({"roles_except": ["GK"]}, "GK")


# ---------------------------------------------------------------- valutatore, tetto, score

def test_evaluate_reproduces_texts():
    c = card(playstyles=["Finesse Shot+", "Rapid+"], body_type="Lean", skill_moves=5, weak_foot=5)
    rd = rules.evaluate(c, {"role": "ST", "unknown_playstyles": []}, scoring.load_config())
    text = " ".join(rd["reasons_pro"])
    assert "Punti di forza per il ruolo" in text and "5 stelle di skill" in text and "Body type Lean" in text
    assert rd["score_delta"] == 0 and {x["id"] for x in rd["contributions"]} >= {"skill-5-stelle", "piede-debole-5"}


def test_delta_enters_score_and_is_capped():
    c = card(skill_moves=5)  # colpisce: forza-stat-alte, scatto-vertice, skill-5-stelle
    base = scoring.explain(c, _cfg())["base"]
    ex = scoring.explain(c, _cfg({"scatto-vertice": {"effect": {"score": 1.0}}}))
    assert ex["rules_delta"] == 1.0 and ex["base"] > base
    big = scoring.explain(c, _cfg({"scatto-vertice": {"effect": {"score": 3.0}}, "forza-stat-alte": {"effect": {"score": 3.0}}}))
    assert big["rules"]["score_delta_raw"] == 6.0 and big["rules_delta"] == 4.0 and big["rules"]["capped"]
    neg = scoring.explain(card(stats={k: 55 for k in ST}),
                          _cfg({"debolezza-stat-basse": {"effect": {"score": -2.0}}}))
    assert neg["rules_delta"] == -2.0 and neg["base"] < scoring.explain(card(stats={k: 55 for k in ST}), _cfg())["base"]


def test_contributions_exposed_in_analysis(client):
    cid = client.post("/api/v1/cards", json=card(skill_moves=5)).json()["id"]
    d = client.get(f"/api/v1/cards/{cid}").json()
    a = d["analysis"]
    assert {c["id"] for c in a["contributions"]} >= {"scatto-vertice", "skill-5-stelle"}
    assert a["rules_score_delta"] == 0 and d["breakdown"]["rules_delta"] == 0 and a["criteria_disagreements"] == []


def test_proposed_rules_do_not_affect_score(tmp_path):
    prop = {"id": "learned-st-pace-pos", "roles": ["ST"], "criterion": "pace",
            "when": {"type": "avg", "stats": ["acceleration", "sprint_speed"], "gte": 85},
            "effect": {"score": 1.5, "tag": "pace"}, "pro_text": "Scatto che i pro premiano.", "con_text": None,
            "source": "learned", "status": "proposed", "evidence": {"mentions": 6}}
    (tmp_path / "rules.local.json").write_text(json.dumps({"learned": [prop]}))
    rules.clear_cache()
    c = card()
    ex = scoring.explain(c, _cfg())
    assert ex["rules_delta"] == 0 and "Scatto che i pro premiano." not in ex["rules"]["reasons_pro"]
    assert ex["rules"]["pending"][0]["id"] == "learned-st-pace-pos"  # informativa, senza effetto
    before = ex["base"]
    rules.set_status("learned-st-pace-pos", "active", scoring.load_config())
    after = scoring.explain(c, _cfg())
    assert after["rules_delta"] == 1.5 and after["base"] > before
    assert "Scatto che i pro premiano." in after["rules"]["reasons_pro"]


def test_rejected_rule_is_ignored(tmp_path):
    prop = {"id": "learned-x", "roles": ["ST"], "when": {"type": "stat", "stat": "finishing", "gte": 80},
            "effect": {"score": 1.0}, "pro_text": "x", "con_text": None, "source": "learned", "status": "rejected"}
    (tmp_path / "rules.local.json").write_text(json.dumps({"learned": [prop]}))
    rules.clear_cache()
    assert scoring.explain(card(), _cfg())["rules_delta"] == 0


def test_advice_from_rules():
    cfg = scoring.load_config()
    assert rules.advice("top", "MUST_DO", False, cfg).startswith("Da prendere")
    assert "confronta il prezzo" in rules.advice("meta", "NEUTRAL", True, cfg)
    assert rules.advice("below", "AVOID", False, cfg).startswith("Da evitare per giocare")


# ---------------------------------------------------------------- config rotta

def _broken(tmp_path, monkeypatch, mutate):
    p = tmp_path / "rules_rotto.json"
    _write_rules(p, mutate)
    monkeypatch.setattr(rules, "RULES_PATH", p)
    rules.clear_cache()
    scoring.load_config.cache_clear()
    return p


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["rules"][0]["when"].update(type="boh"), "when.type"),
    (lambda d: d["rules"][0]["effect"].update(score=99), "max_rule_delta"),
    (lambda d: d["rules"][0].update(pro_text="{inesistente}"), "segnaposto"),
    (lambda d: d["rules"][0].update(status="forse"), "status"),
    (lambda d: d["rules"][0].update(source="altro"), "source"),
    (lambda d: d["rules"][1].update(id=d["rules"][0]["id"]), "duplicato"),
    (lambda d: d["rules"][2].update(criterion="inventato"), "criteria.json"),
    (lambda d: d["rules"][2]["when"].pop("gte"), "confronto"),
    (lambda d: d["rules"][0].update(roles=["ST"]), "esattamente uno"),
    (lambda d: d["rules"][2].update(roles=["XX"]), "ruoli sconosciuti"),
    (lambda d: d["rules"][2]["when"].update(stats=["inventata"]), "stat sconosciute"),
    (lambda d: d["settings"].pop("learn"), "settings.learn"),
    (lambda d: d.pop("advice"), "advice"),
])
def test_broken_rules_config_is_a_clear_error(tmp_path, monkeypatch, mutate, msg):
    _broken(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError, match=msg):
        scoring.load_config()


def test_broken_json_and_missing_file(tmp_path, monkeypatch):
    p = tmp_path / "r.json"
    p.write_text("{non json")
    monkeypatch.setattr(rules, "RULES_PATH", p)
    rules.clear_cache()
    with pytest.raises(ValueError, match="JSON non valido"):
        rules.load_base()
    monkeypatch.setattr(rules, "RULES_PATH", tmp_path / "manca.json")
    rules.clear_cache()
    with pytest.raises(ValueError, match="file mancante"):
        rules.load_base()


def test_changing_rules_json_changes_judgement_without_code(tmp_path, monkeypatch):
    c = card()
    assert any("Scatto e velocità da vertice" in t for t in rules.evaluate(c, {"role": "ST"}, scoring.load_config())["reasons_pro"])
    _broken(tmp_path, monkeypatch, lambda d: d["rules"][2]["when"].update(gte=95))
    assert not any("Scatto e velocità da vertice" in t for t in rules.evaluate(c, {"role": "ST"}, scoring.load_config())["reasons_pro"])


def test_broken_overlay_in_local_config_is_ignored(tmp_path):
    (tmp_path / "local.json").write_text(json.dumps({"rules": {"overrides": {"scatto-vertice": {"effect": {"score": 50}}}}}))
    scoring.load_config.cache_clear()
    assert scoring.load_config().get("rules") is None  # ritocco non valido: calibrazione locale ignorata


# ---------------------------------------------------------------- apprendimento

def _table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS criteria (id INTEGER PRIMARY KEY, opinion_id INTEGER, creator TEXT, "
                 "role TEXT, criterion TEXT, polarity INTEGER, ts TEXT)")


def _add(conn, rows):
    _table(conn)
    conn.executemany("INSERT INTO criteria (opinion_id, creator, role, criterion, polarity, ts) VALUES (1,?,?,?,?,'t')", rows)
    conn.commit()


@pytest.fixture
def mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_learn_without_table_is_not_an_error(mem):
    out = rules.learn(mem, scoring.load_config())
    assert out["ready"] is False and out["proposals"] == [] and "non esiste" in out["message"]
    assert rules.criteria_disagreements(mem, "ST", scoring.load_config()) == []


def test_learn_proposes_rules_but_never_activates(mem):
    _add(mem, [("Team Gullit", "ST", "pace", 1)] * 3 + [("Exeed", "ST", "pace", 1)] * 3 + [("Nassada", "ST", "pace", 1)])
    out = rules.learn(mem, scoring.load_config())
    assert out["ready"] and len(out["proposals"]) == 1
    r = out["proposals"][0]
    assert r["id"] == "learned-st-pace-pos" and r["status"] == "proposed" and r["source"] == "learned"
    assert r["roles"] == ["ST"] and r["when"]["stats"] == ["acceleration", "sprint_speed"]
    assert r["evidence"]["mentions"] == 7 and r["evidence"]["creators"] == ["Exeed", "Nassada", "Team Gullit"]
    assert 0 < r["effect"]["score"] <= 1.5
    assert scoring.explain(card(), _cfg())["rules_delta"] == 0  # proposta: nessun effetto
    assert [x for x in rules.list_rules() if x["id"] == r["id"]][0]["status"] == "proposed"


def test_learn_negative_polarity_gives_negative_delta(mem):
    _add(mem, [("A", "CB", "pace", -1)] * 3 + [("B", "CB", "pace", -1)] * 3)
    r = rules.learn(mem, scoring.load_config())["proposals"][0]
    assert r["effect"]["score"] < 0 and r["con_text"] and r["when"]["lt"] == 70


def test_learn_respects_minimums_and_consensus(mem):
    _add(mem, [("A", "ST", "pace", 1)] * 4 +                      # meno di 5 menzioni
             [("A", "W", "dribbling", 1)] * 6 +                   # un solo creator
             [("A", "CM", "passing", 1)] * 3 + [("B", "CM", "passing", -1)] * 3 +  # nessun consenso
             [("A", "ST", "inventato", 1)] * 9 + [("B", "ST", "inventato", 1)] * 9)  # chiave non in criteria.json
    out = rules.learn(mem, scoring.load_config())
    assert out["proposals"] == [] and "nessuna regola" in out["message"].lower()
    why = {(s["role"], s["criterion"]): s["motivo"] for s in out["skipped"]}
    assert why[("ST", "pace")] == "dati insufficienti" and why[("W", "dribbling")] == "dati insufficienti"
    assert why[("CM", "passing")] == "i creator non sono concordi"
    assert not (state := rules.state_path()).exists() or json.loads(state.read_text())["learned"] == []


def test_learn_delta_capped_and_step_limited(mem):
    _add(mem, [("A", "ST", "pace", 1)] * 20 + [("B", "ST", "pace", 1)] * 20)
    cfg = scoring.load_config()
    r1 = rules.learn(mem, cfg)["proposals"][0]
    assert r1["effect"]["score"] == 1.5  # tetto di max_new_delta
    mem.execute("DELETE FROM criteria")
    _add(mem, [("A", "ST", "pace", 1)] * 3 + [("B", "ST", "pace", 1)] * 3)  # evidenza più debole: target 1.2
    r2 = rules.learn(mem, cfg)["proposals"][0]
    assert r2["effect"]["score"] == pytest.approx(1.2) and r1["effect"]["score"] - r2["effect"]["score"] <= 0.5
    assert len([x for x in rules.list_rules() if x["id"] == r2["id"]]) == 1  # aggiornata, non duplicata


def test_learn_keeps_user_decisions(mem):
    _add(mem, [("A", "ST", "pace", 1)] * 3 + [("B", "ST", "pace", 1)] * 3)
    cfg = scoring.load_config()
    rid = rules.learn(mem, cfg)["proposals"][0]["id"]
    rules.set_status(rid, "rejected", cfg)
    assert rules.learn(mem, cfg)["proposals"] == []  # rifiutata: non si ripropone


def test_learn_non_stat_templates(mem):
    _add(mem, [("A", "ST", "body_type", 1)] * 3 + [("B", "ST", "body_type", 1)] * 3 +
             [("A", "ST", "price", 1)] * 3 + [("B", "ST", "price", 1)] * 3)
    out = rules.learn(mem, scoring.load_config())
    assert [p["id"] for p in out["proposals"]] == ["learned-st-body_type-pos"]  # price non ha condizione associabile
    assert out["proposals"][0]["when"]["type"] == "body_type"
    assert any(s["criterion"] == "price" for s in out["skipped"])


def test_set_status_only_for_proposed(mem):
    cfg = scoring.load_config()
    with pytest.raises(ValueError, match="proposte"):
        rules.set_status("scatto-vertice", "rejected", cfg)
    with pytest.raises(KeyError):
        rules.set_status("non-esiste", "active", cfg)
    with pytest.raises(ValueError):
        rules.set_status("scatto-vertice", "boh", cfg)


# ---------------------------------------------------------------- discordanze a livello di criterio

def test_criteria_disagreements_use_real_names(mem):
    _add(mem, [("Team Gullit", "ST", "pace", 1), ("Exeed", "ST", "pace", -1), ("Nassada", "ST", "pace", -1),
               ("Team Gullit", "ST", "finishing", 1), ("Exeed", "ST", "finishing", 1),
               ("Exeed", "CB", "pace", 1), ("Nassada", "CB", "pace", -1)])
    cfg = scoring.load_config()
    st = rules.criteria_disagreements(mem, "ST", cfg)
    assert len(st) == 1 and st[0]["criterion"] == "pace"
    assert st[0]["text"] == "Team Gullit premia scatto e velocità, Exeed e Nassada lo ritengono negativo."
    assert rules.criteria_disagreements(mem, "CB", cfg)[0]["text"] == "Exeed premia scatto e velocità, Nassada lo ritiene negativo."
    assert rules.criteria_disagreements(mem, "GK", cfg) == []


def test_disagreements_in_card_analysis(client):
    conn = db.connect()
    _add(conn, [("Team Gullit", "ST", "pace", 1), ("Exeed", "ST", "pace", -1)])
    conn.close()
    cid = client.post("/api/v1/cards", json=card()).json()["id"]
    a = client.get(f"/api/v1/cards/{cid}").json()["analysis"]
    assert a["criteria_disagreements"][0]["text"] == "Team Gullit premia scatto e velocità, Exeed lo ritiene negativo."


# ---------------------------------------------------------------- API

def test_rules_api_flow(client):
    r = client.get("/api/v1/rules").json()
    assert r["counts"]["active"] >= 10 and all("evidence" in x and "status" in x for x in r["rules"])
    conn = db.connect()
    _add(conn, [("A", "ST", "pace", 1)] * 3 + [("B", "ST", "pace", 1)] * 3)
    conn.close()
    out = client.post("/api/v1/rules/learn").json()
    rid = out["proposals"][0]["id"]
    assert client.get("/api/v1/rules").json()["counts"]["proposed"] == 1
    assert client.put("/api/v1/rules/scatto-vertice/status", json={"status": "rejected"}).status_code == 409
    assert client.put("/api/v1/rules/nulla/status", json={"status": "active"}).status_code == 404
    assert client.put(f"/api/v1/rules/{rid}/status", json={"status": "boh"}).status_code == 422
    cid = client.post("/api/v1/cards", json=card()).json()["id"]
    before = client.get(f"/api/v1/cards/{cid}").json()["scores"]["base_score"]
    assert client.put(f"/api/v1/rules/{rid}/status", json={"status": "active"}).json()["status"] == "active"
    d = client.get(f"/api/v1/cards/{cid}").json()
    assert d["scores"]["base_score"] > before and d["breakdown"]["rules_delta"] > 0
    assert any(c["id"] == rid and c["source"] == "learned" and c["evidence"]["mentions"] == 6 for c in d["analysis"]["contributions"])
    assert client.put(f"/api/v1/rules/{rid}/status", json={"status": "rejected"}).status_code == 409  # ormai attiva


def test_rules_api_learn_without_criteria_table(client):
    out = client.post("/api/v1/rules/learn")
    assert out.status_code == 200 and out.json()["proposals"] == []


def test_rules_api_requires_token(client, monkeypatch):
    monkeypatch.setenv("EAFCMETA_TOKEN", "segreto")
    assert client.get("/api/v1/rules").status_code == 401
    assert client.post("/api/v1/rules/learn").status_code == 401
    assert client.put("/api/v1/rules/x/status", json={"status": "active"}).status_code == 401
    assert client.get("/api/v1/rules", headers={"X-Token": "segreto"}).status_code == 200


# ---------------------------------------------------------------- calibrazione delle regole

def _rated(client, n=20):
    """n attaccanti con scatto crescente: i pro approvano (voto 90) solo quelli con scatto >= 88."""
    for i in range(n):
        acc = 60 + 2 * i
        cid = client.post("/api/v1/cards", json=card(name=f"ST{i}", price=1000 * (i + 1),
                                                     stats={**ST, "acceleration": acc, "sprint_speed": acc})).json()["id"]
        client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "Exeed", "stance": "yes" if acc >= 88 else "no", "score": 90 if acc >= 88 else 50})


def test_rules_calibration_needs_data(client):
    _rated(client, 4)
    r = client.get("/api/v1/calibration").json()
    assert r["rules"]["changes"] == []
    assert client.post("/api/v1/calibration/apply", json={"rules": True}).status_code == 422


def test_rules_calibration_apply_and_reset(client):
    _rated(client)
    rep = client.get("/api/v1/calibration").json()
    ch = {c["id"]: c for c in rep["rules"]["changes"]}
    assert ch["scatto-vertice"]["delta"] == {"old": 0.0, "new": 0.25} and ch["scatto-vertice"]["diff"] > 5
    assert all(abs(c["delta"]["new"] - c["delta"]["old"]) <= 0.25 + 1e-9 for c in ch.values())  # passo prudente
    top = max(client.get("/api/v1/cards").json(), key=lambda e: e["breakdown"]["stats_meta"])
    out = client.post("/api/v1/calibration/apply", json={"thresholds": False, "rules": True}).json()
    assert out["active"]
    top2 = max(client.get("/api/v1/cards").json(), key=lambda e: e["breakdown"]["stats_meta"])
    assert top2["breakdown"]["rules_delta"] > 0 and top["breakdown"]["rules_delta"] == 0
    after = client.post("/api/v1/calibration/reset").json()
    assert after["active"] is False
    again = max(client.get("/api/v1/cards").json(), key=lambda e: e["breakdown"]["stats_meta"])
    assert again["breakdown"]["rules_delta"] == 0


def test_rules_calibration_ignores_proposed_rules(client, tmp_path):
    prop = {"id": "learned-st-pace-pos", "roles": ["ST"], "when": {"type": "avg", "stats": ["acceleration", "sprint_speed"], "gte": 88},
            "effect": {"score": 0.5}, "pro_text": "x", "con_text": None, "source": "learned", "status": "proposed"}
    (tmp_path / "rules.local.json").write_text(json.dumps({"learned": [prop]}))
    rules.clear_cache()
    _rated(client)
    ids = {c["id"] for c in client.get("/api/v1/calibration").json()["rules"]["changes"]}
    assert "learned-st-pace-pos" not in ids
