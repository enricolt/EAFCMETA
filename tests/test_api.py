from conftest import ST, card


def test_crud_flow(client):
    r = client.post("/api/v1/cards", json=card(name="Uno", playstyles=["Rapid+"]))
    assert r.status_code == 201 and r.json()["status"] == "new"
    cid = r.json()["id"]
    d = client.get(f"/api/v1/cards/{cid}").json()
    assert d["scores"]["final_score"] > 80 and d["pro_missing"] and d["card"]["stats"]["finishing"] == 90
    # upsert: stessa chiave -> aggiorna e traccia lo storico
    assert client.post("/api/v1/cards", json=card(name="UNO", price=120000)).json() == {"id": cid, "status": "updated"}
    assert [h["price"] for h in client.get(f"/api/v1/cards/{cid}").json()["price_history"]] == [100000, 120000]
    # pro
    r = client.put(f"/api/v1/cards/{cid}/pro", json={"pro_score": 95}).json()
    assert r["scores"]["pro_sentiment_score"] == 95 and not r["pro_missing"]
    assert client.put(f"/api/v1/cards/{cid}/pro", json={"pro_score": None}).json()["pro_missing"]
    assert client.put(f"/api/v1/cards/{cid}/pro", json={"pro_score": 101}).status_code == 422
    # modifica ed eliminazione
    assert client.put(f"/api/v1/cards/{cid}", json=card(name="Uno", price=5)).json()["cost_credits"] == 5
    assert client.delete(f"/api/v1/cards/{cid}").status_code == 204
    assert client.get(f"/api/v1/cards/{cid}").status_code == 404
    assert client.delete(f"/api/v1/cards/{cid}").status_code == 404


def test_validation(client):
    bad = [card(position="XX"), card(position="GK"), card(price=-1), card(stats={"finishing": 90}), card(stats={**ST, "finishing": 150}),
           card(stats={**ST, "inventata": 50}), card(body_type="Gigante"), card(weak_foot=9), card(name=""),
           card(name="x" * 200), card(playstyles=["a"] * 30)]
    for b in bad:
        assert client.post("/api/v1/cards", json=b).status_code == 422, b


def test_list_filters_and_empty(client):
    assert client.get("/api/v1/cards").json() == []
    client.post("/api/v1/cards", json=card(name="Alfa"))
    client.post("/api/v1/cards", json=card(name="Beta", price=5))
    assert len(client.get("/api/v1/cards").json()) == 2
    assert [c["name"] for c in client.get("/api/v1/cards?q=alf").json()] == ["Alfa"]
    assert client.get("/api/v1/cards?position=CB").json() == []
    assert len(client.get("/api/v1/cards?limit=1").json()) == 1


def test_auth(client, monkeypatch):
    monkeypatch.setenv("EAFCMETA_TOKEN", "segreto")
    assert client.get("/api/v1/cards").status_code == 401
    assert client.get("/api/v1/cards", headers={"X-Token": "no"}).status_code == 401
    assert client.get("/api/v1/cards", headers={"X-Token": "segreto"}).status_code == 200
    assert client.get("/").status_code == 200  # la pagina è libera, i dati no


def test_meta_and_template(client):
    m = client.get("/api/v1/meta").json()
    assert "ST" in m["positions"] and "finishing" in m["role_weights"]["ST"]
    assert client.get("/api/v1/import/template").text.startswith("name;version;position")


def test_goalkeeper_flow(client):
    gk = {"gk_diving": 88, "gk_handling": 85, "gk_kicking": 80, "gk_positioning": 87, "gk_reflexes": 90}
    r = client.post("/api/v1/cards", json=card(name="Portiere", position="GK", stats=gk, playstyles=["Far Reach+"]))
    assert r.status_code == 201
    d = client.get(f"/api/v1/cards/{r.json()['id']}").json()
    assert d["breakdown"]["role"] == "GK" and {t["k"] for t in d["top_stats"]} == {"gk_reflexes", "gk_diving", "gk_positioning", "gk_handling"}
