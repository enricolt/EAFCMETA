"""Revisione critica, area H: l'accettazione di una proposta passa dalla stessa validazione di OpinionIn."""
from test_research import EXEED, GULLIT, cards  # noqa: F401 - fixture `cards`


def _prop(client, text=GULLIT, creator="Team Gullit"):
    return client.post("/api/v1/research/paste", json={"text": text, "creator": creator}).json()["created"][0]


def _opinions(client, cid):
    return client.get(f"/api/v1/cards/{cid}").json()["opinions"]


def test_accept_incoherent_stance_and_score_is_422_and_writes_nothing(client, cards):
    p = _prop(client)
    assert (p["stance"], p["score"]) == ("yes", 90)
    for body in ({"stance": "no"}, {"stance": "no", "score": 90}, {"stance": "yes", "score": 40}):
        r = client.post(f"/api/v1/research/proposals/{p['id']}/accept", json=body)
        assert r.status_code == 422, body
        assert "voto" in r.json()["detail"] and "Value error" not in r.json()["detail"]
    assert _opinions(client, p["card_id"]) == []
    assert client.get("/api/v1/research/proposals?status=pending").json()[0]["id"] == p["id"]  # resta in attesa
    ok = client.post(f"/api/v1/research/proposals/{p['id']}/accept", json={"stance": "no", "score": 40})
    assert ok.status_code == 200


def test_accept_stored_incoherent_proposal_is_422(client, cards):
    from eafcmeta import db
    p = _prop(client)
    conn = db.connect()
    conn.execute("UPDATE proposals SET stance='yes', score=30 WHERE id=?", (p["id"],))  # dato incoerente gia' in archivio
    conn.commit()
    conn.close()
    r = client.post(f"/api/v1/research/proposals/{p['id']}/accept")
    assert r.status_code == 422 and _opinions(client, p["card_id"]) == []


def test_accept_bad_values(client, cards):
    p = _prop(client, EXEED, "Exeed")
    assert client.post(f"/api/v1/research/proposals/{p['id']}/accept", json={"score": 500}).status_code == 422
    assert client.post(f"/api/v1/research/proposals/{p['id']}/accept", json={"reason": "x" * 900}).status_code == 422
