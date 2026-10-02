from conftest import ST, card

GK = {"gk_diving": 88, "gk_handling": 85, "gk_kicking": 80, "gk_positioning": 87, "gk_reflexes": 90}


def _analysis(client, **kw):
    cid = client.post("/api/v1/cards", json=card(**kw)).json()["id"]
    return client.get(f"/api/v1/cards/{cid}").json()


def test_strong_striker_description(client):
    a = _analysis(client, playstyles=["Finesse Shot+", "Rapid+"], body_type="Lean", skill_moves=5, weak_foot=5)["analysis"]
    assert a["meta_level"] in ("top", "meta") and a["meta_label"] in ("Meta", "Meta di vertice")
    text = " ".join(a["pros"])
    assert "finalizzazione" in text and "Finesse Shot+" in text and "5 stelle di skill" in text and "5★ di piede debole" in text
    assert any("manca il parere dei creator" in c.lower() for c in a["cons"])
    assert a["advice"] and a["headline"].startswith(a["meta_label"])


def test_weak_card_is_flagged(client):
    weak = {k: 55 for k in ST}
    d = _analysis(client, stats=weak, skill_moves=1, weak_foot=1, playstyles=["Anticipate+"])
    a = d["analysis"]
    assert a["meta_level"] == "below" and "Da evitare" in a["advice"]
    cons = " ".join(a["cons"])
    assert "Punti deboli" in cons and "Scatto e velocità bassi" in cons and "1★ di skill" in cons
    assert "Anticipate+" in cons  # PlayStyle+ fuori ruolo per un attaccante


def test_pro_score_and_goalkeeper(client):
    d = _analysis(client, position="GK", stats=GK, playstyles=["Far Reach+"])
    assert d["breakdown"]["role"] == "GK" and any("portiere" in d["analysis"]["headline"] for _ in [0])
    client.put(f"/api/v1/cards/{d['id']}/pro", json={"pro_score": 90})
    a = client.get(f"/api/v1/cards/{d['id']}").json()["analysis"]
    assert any("Parere dei pro: 90/100" in p for p in a["pros"])  # voto manuale (senza creator)


def test_list_has_meta_label_but_no_internal_fields(client):
    client.post("/api/v1/cards", json=card())
    c = client.get("/api/v1/cards").json()[0]
    assert c["meta_label"] and not any(k.startswith("_") for k in c)


def _op(client, cid, creator, stance, **kw):
    return client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": creator, "stance": stance, **kw})


def test_opinions_disagree_are_explained(client):
    cid = client.post("/api/v1/cards", json=card(name="Controversa")).json()["id"]
    assert _op(client, cid, "Team Gullit", "yes", score=90, reason="Scatto e finalizzazione ottimi").status_code == 200
    _op(client, cid, "Exeed", "no", score=55, reason="Troppo lenta nei cambi di direzione")
    d = _op(client, cid, "Nassada", "no", reason="").json()
    ops = d["analysis"]["opinions"]
    assert ops["status"] == "disagree"
    assert ops["summary"] == "Pareri discordanti: secondo Team Gullit sì; secondo Exeed e Nassada no."
    yes, no = ops["groups"]
    assert yes["creators"][0]["reason"] == "Scatto e finalizzazione ottimi"
    assert [c["name"] for c in no["creators"]] == ["Exeed", "Nassada"]
    assert no["creators"][1]["reason"] == "motivo non indicato"
    assert d["analysis"]["advice"].startswith("Opinioni divise")
    # il punteggio pro è la media: 90, 55 e (no senza voto = 50)
    assert d["scores"]["pro_sentiment_score"] == 65.0


def test_opinions_agree_update_and_delete(client):
    cid = client.post("/api/v1/cards", json=card()).json()["id"]
    _op(client, cid, "Exeed", "yes", reason="Ottima")
    d = _op(client, cid, "Nassada", "yes").json()
    assert d["analysis"]["opinions"]["status"] == "agree" and "concordi" in d["analysis"]["opinions"]["summary"]
    d = _op(client, cid, "exeed", "no").json()  # stesso creator (maiuscole diverse): aggiorna, non duplica
    assert len(d["opinions"]) == 2 and d["opinions"][0]["stance"] == "no"
    oid = d["opinions"][0]["id"]
    d = client.delete(f"/api/v1/cards/{cid}/opinions/{oid}").json()
    assert all(o["id"] != oid for o in d["opinions"])
    assert client.delete(f"/api/v1/cards/{cid}/opinions/9999").status_code == 404


def test_opinion_validation_and_cascade(client):
    cid = client.post("/api/v1/cards", json=card()).json()["id"]
    for bad in [{"creator": "", "stance": "yes"}, {"creator": "X", "stance": "boh"}, {"creator": "X", "stance": "no", "score": 120},
                {"creator": "X", "stance": "no", "url": "javascript:alert(1)"}]:
        assert client.put(f"/api/v1/cards/{cid}/opinions", json=bad).status_code == 422, bad
    _op(client, cid, "X", "yes", url="https://youtu.be/abc")
    client.delete(f"/api/v1/cards/{cid}")
    assert client.put(f"/api/v1/cards/{cid}/opinions", json={"creator": "X", "stance": "yes"}).status_code == 404


def test_hint_when_stats_and_creators_clash(client):
    cid = client.post("/api/v1/cards", json=card(playstyles=["Finesse Shot+", "Rapid+"], body_type="Lean")).json()["id"]
    d = _op(client, cid, "Exeed", "no", reason="Non si sente reattiva").json()
    assert "i creator la bocciano" in d["analysis"]["opinions"]["hint"]
