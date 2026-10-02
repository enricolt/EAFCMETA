import json
from pathlib import Path

from conftest import ST

HEAD = "name;version;position;price;playstyles;" + ";".join(ST)
ROW = ";".join(map(str, ST.values()))


def csv(*rows):
    return "\n".join([HEAD, *rows])


def test_example_file_imports(client):
    text = (Path(__file__).parent.parent / "data" / "esempio.csv").read_text(encoding="utf-8")
    r = client.post("/api/v1/import", json={"text": text, "dry_run": False}).json()
    assert r["saved"] and r["new"] == 30 and not r["errors"]
    again = client.post("/api/v1/import", json={"text": text, "dry_run": False}).json()
    assert again["new"] == 0 and again["updated"] == 30  # niente doppioni
    assert len(client.get("/api/v1/cards").json()) == 30


def test_dry_run_writes_nothing(client):
    r = client.post("/api/v1/import", json={"text": csv(f"A;v;ST;12.500;Rapid+|Flair+;{ROW}"), "dry_run": True}).json()
    assert r["new"] == 1 and not r["saved"]
    assert client.get("/api/v1/cards").json() == []


def test_all_or_nothing_with_row_errors(client):
    text = csv(f"Ok;v;ST;1k;;{ROW}", f"Male;v;GK;1k;;{ROW}", f"Male2;v;ST;abc;;{ROW}")
    r = client.post("/api/v1/import", json={"text": text, "dry_run": False}).json()
    assert not r["saved"] and [e["row"] for e in r["errors"]] == [3, 4]
    assert client.get("/api/v1/cards").json() == []


def test_json_tsv_and_aliases(client):
    js = json.dumps([{"nome": "J", "posizione": "ST", "prezzo": "2k", "stats": ST}])
    assert client.post("/api/v1/import", json={"text": js, "dry_run": False}).json()["new"] == 1
    tsv = "Nome\tPos\tPrezzo\t" + "\t".join(ST) + "\nT\tST\t3k\t" + "\t".join(map(str, ST.values()))
    assert client.post("/api/v1/import", json={"text": tsv, "dry_run": False}).json()["new"] == 1


def test_bad_input(client):
    for t in ["", "[1,2]", "{rotto"]:
        r = client.post("/api/v1/import", json={"text": t, "dry_run": False}).json()
        assert r["errors"] and not r["saved"]


def test_price_update(client):
    client.post("/api/v1/import", json={"text": csv(f"Aa;x;ST;1000;;{ROW}", f"Aa;y;ST;2000;;{ROW}"), "dry_run": False})
    p = lambda t, d=False: client.post("/api/v1/prices", json={"text": t, "dry_run": d}).json()
    assert p("Aa;x;1.500")["updated"] == 1
    assert p("aa;9999")["errors"]  # ambigua
    assert p("Boh;5")["errors"]  # non trovata
    assert p("Aa;x;7", d=True)["saved"] is False
    prices = {c["version"]: c["cost_credits"] for c in client.get("/api/v1/cards").json()}
    assert prices == {"x": 1500, "y": 2000}
