"""Collegamento tra ricerca dei pareri, criteri e motore di regole (costruiti separatamente)."""
from conftest import ST, card

TESTI = {  # creator -> testo (positivo sullo scatto)
    "Exeed": "{n} è fortissima: scatto e velocità devastanti, la consiglio, è meta.",
    "Hollywood285": "Su {n} niente da dire, scatto eccellente e rapida, ottima carta.",
    "RiberaRibell": "{n} ha uno scatto top e una velocità pazzesca, la prenderei.",
}


def _flow(client):
    ids = [client.post("/api/v1/cards", json=card(name=n, version="Gold 86", stats={**ST, "acceleration": 95, "sprint_speed": 94})).json()["id"]
           for n in ("Chloe Kelly", "Lauren James")]
    for name, cid in zip(("Chloe Kelly", "Lauren James"), ids):
        for creator, tpl in TESTI.items():
            r = client.post("/api/v1/research/paste", json={"text": tpl.format(n=name), "creator": creator, "card_id": cid})
            assert r.status_code == 200, r.text
            for p in r.json()["created"]:
                assert client.post(f"/api/v1/research/proposals/{p['id']}/accept", json={}).status_code == 200
    return ids


def test_paste_accept_learn_flow(client):
    ids = _flow(client)
    # i pareri accettati sono veri pareri e pesano sul punteggio
    d = client.get(f"/api/v1/cards/{ids[0]}").json()
    assert len(d["opinions"]) == 3 and d["scores"]["pro_share"] > 0.15
    # 6 menzioni di 'scatto' da 3 creator -> il motore di regole PROPONE una regola (non la attiva)
    out = client.post("/api/v1/rules/learn").json()
    props = out["proposals"]
    assert props, out
    assert all(p["status"] == "proposed" for p in props)
    assert any("ST" in p["roles"] and p["evidence"]["mentions"] >= 5 for p in props)
    assert client.get(f"/api/v1/cards/{ids[0]}").json()["breakdown"]["rules_delta"] == 0  # senza approvazione non cambia nulla


def test_approved_learned_rule_changes_the_score(client):
    ids = _flow(client)
    props = client.post("/api/v1/rules/learn").json()["proposals"]
    before = client.get(f"/api/v1/cards/{ids[0]}").json()["scores"]["base_score"]
    rid = next(p["id"] for p in props if "ST" in p["roles"])
    assert client.put(f"/api/v1/rules/{rid}/status", json={"status": "active"}).status_code == 200
    d = client.get(f"/api/v1/cards/{ids[0]}").json()
    assert d["scores"]["base_score"] > before and d["breakdown"]["rules_delta"] > 0
