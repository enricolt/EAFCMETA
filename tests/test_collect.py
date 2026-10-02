import json

import pytest

from eafcmeta import collect, sources

GG_STATS = ("Attributes|Chemistry Style|Pace|87|Acceleration|83|Sprint Speed|90|Shooting|85|Att. Pos.|89|Finishing|90|"
            "Shot Power|82|Long Shots|80|Volleys|74|Penalties|75|Passing|85|Vision|89|Crossing|75|Fk Acc.|70|"
            "Short Pass|90|Long Pass|86|Curve|77|Dribbling|87|Agility|88|Balance|83|Reactions|85|Ball Control|90|"
            "Dribbling|86|Composure|80|Defending|63|Interceptions|63|Heading Acc.|78|Def. Aware.|40|Stand Tackle|79|"
            "Slide Tackle|68|Physical|83|Jumping|89|Stamina|91|Strength|81|Aggression|74|Basic|C")


def gg_page(price="800,000", ps=("Chip Shot", "Tiki Taka"), skills="4", plus=(), name="Kika Nazareth", rating=86, rarity="Destined for Glory"):
    ld = [{"@type": "BreadcrumbList", "itemListElement": [{"name": "Players"}, {"name": name},
                                                            {"name": f"{rarity} {rating} OVR"}]},
          {"@type": "WebPage", "description": f"{name} {rarity} {rating} OVR CM (FC Barcelona) on EA FC 27."}]
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(x)}</script>' for x in ld)
    body = "|".join(["Players", "Skill Moves", skills, "Weak Foot", "4", "Body Type", "Lean Short", GG_STATS,
                     price, "Current price", "· Updated"])
    shape = lambda p: "M12.8,104.9L68.1,21" if p in plus else "M128,12.808L243.192,128,12.8,128Z"
    icons = "".join(f'<div title="{p}"><svg height="42"><path d="{shape(p)}"/></svg></div>' for p in ps)
    divs = "".join(f"<div>{t}</div>" for t in body.split("|"))
    return f"<html><head><title>x FUT.GG</title>{scripts}</head><body>{icons}{divs}</body></html>"


FB_STATS = ("Player Stats|Pace|85|Acceleration|81|Sprint Speed|88|Shooting|82|Att. Position|86|Finishing|87|"
            "Shot Power|79|Long Shots|77|Volleys|71|Penalties|72|Passing|82|Vision|86|Crossing|72|FK Acc.|68|"
            "Short Pass|87|Long Pass|83|Curve|74|Dribbling|84|Agility|85|Balance|80|Reactions|82|Ball Control|87|"
            "Dribbling|83|Composure|77|Defending|60|Interceptions|60|Heading Acc.|74|Def. Aware|38|Stand Tackle|75|"
            "Slide Tackle|65|Physical|80|Jumping|86|Stamina|88|Strength|78|Aggression|71|Total Chem. style added:")


def fb_page(price="80,000", plus=False, name="Kika Nazareth", rating="83", rarity="Gold"):
    head = "|".join([rating, "CM", "++", "R", "4", "4", "83.4", name, "85", "Pac", "82", "Sho", "Add to Compare"])
    info = "|".join(["Portugal", "FC Barcelona", rarity, "Skills", "4", "Weak Foot", "4", "B.Type", "Short & Lean"])
    f = "27_plus_scoring-chipshot.png" if plus else "27_scoring-chipshot.png"
    ps = (f'<a class="playStyle-table-icon column active{" psplus" if plus else ""}" href="x"><img class="ps-logo" src="{f}">'
          '<div>Chip Shot</div></a><a class="playStyle-table-icon column" href="y"><div>Inattivo</div></a>')
    mk = lambda s: "".join(f"<div>{t}</div>" for t in s.split("|"))
    ld = json.dumps({"@type": "Product", "name": f"{name} {rarity} EA FC 27 Player Card"})
    return (f'<html><head><title>FUTBIN</title><script type="application/ld+json">{ld}</script></head><body><div class="price-box platform-ps-only">'
            f'<div class="price lowest-price-1">{price}</div></div>{mk(head)}{mk(info)}{ps}{mk(FB_STATS)}</body></html>')


def test_futgg():
    d = sources.parse_page(gg_page())
    assert (d["name"], d["version"], d["position"], d["price"]) == ("Kika Nazareth", "Destined for Glory 86", "CM", 800000)
    assert d["body_type"] == "Lean" and d["skill_moves"] == 4 and d["weak_foot"] == 4
    assert d["playstyles"] == ["Chip Shot", "Tiki Taka"]
    assert d["stats"]["dribbling"] == 86 and d["stats"]["positioning"] == 89 and len(d["stats"]) == 29


def test_futbin():
    d = sources.parse_page(fb_page())
    assert (d["name"], d["version"], d["position"], d["price"]) == ("Kika Nazareth", "Gold 83", "CM", 80000)
    assert d["body_type"] == "Lean" and d["playstyles"] == ["Chip Shot"]  # solo quelli "active"
    assert d["stats"]["dribbling"] == 83 and d["stats"]["finishing"] == 87 and len(d["stats"]) == 29
    assert sources.parse_page(fb_page(plus=True))["playstyles"] == ["Chip Shot+"]


def test_unrecognized_and_broken_pages():
    with pytest.raises(sources.PageError):
        sources.parse_page("<html><body>ciao</body></html>")
    with pytest.raises(sources.PageError):
        sources.parse_page(gg_page(price="n/d"))


def test_import_pages_flow(client):
    pages = [{"name": "a.html", "html": gg_page()}, {"name": "b.html", "html": fb_page()},
             {"name": "c.html", "html": "<html>boh</html>"}]
    r = client.post("/api/v1/import/pages", json={"pages": pages, "dry_run": True}).json()
    assert r["new"] == 2 and len(r["errors"]) == 1 and not r["saved"]
    assert client.get("/api/v1/cards").json() == []
    r = client.post("/api/v1/import/pages", json={"pages": pages, "dry_run": False}).json()
    assert r["saved"] and r["new"] == 2
    cards = client.get("/api/v1/cards").json()
    assert {c["version"] for c in cards} == {"Destined for Glory 86", "Gold 83"}
    # stessa pagina con prezzo nuovo -> aggiorna e allunga lo storico
    r = client.post("/api/v1/import/pages", json={"pages": [{"name": "a.html", "html": gg_page("900,000")}],
                                                   "dry_run": False}).json()
    assert r["updated"] == 1
    cid = next(c["id"] for c in cards if c["version"].startswith("Destined"))
    assert [h["price"] for h in client.get(f"/api/v1/cards/{cid}").json()["price_history"]] == [800000, 900000]


def test_cli_reads_folder(tmp_path):
    (tmp_path / "x.html").write_text(gg_page(), encoding="utf-8")
    (tmp_path / "y.txt").write_text("ignorato")
    assert [n for n, _ in collect.read_paths([str(tmp_path)])] == ["x.html"]


def test_playstyle_plus_detection():
    assert sources.parse_page(gg_page(plus=("Tiki Taka",)))["playstyles"] == ["Chip Shot", "Tiki Taka+"]
    d = sources.parse_page(fb_page(plus=True))
    assert d["playstyles"] == ["Chip Shot+"]


def test_rare_and_gold_versions_unify():
    assert sources.parse_page(gg_page(rarity="Rare", rating=86))["version"] == "Gold 86"
    assert sources.parse_page(fb_page(rating="86", rarity="Gold"))["version"] == "Gold 86"
    assert sources.parse_page(gg_page())["version"] == "Destined for Glory 86"


def test_same_card_from_both_sites_is_not_duplicated(client):
    pages = [{"name": "gg.html", "html": gg_page(name="Chloe Kelly", rarity="Rare", rating=86, price="11,750")},
             {"name": "fb.html", "html": fb_page(name="Kelly", rating="86", price="12,000")}]
    r = client.post("/api/v1/import/pages", json={"pages": pages, "dry_run": False}).json()
    assert (r["new"], r["updated"]) == (1, 1)
    cards = client.get("/api/v1/cards").json()
    assert len(cards) == 1 and cards[0]["name"] == "Chloe Kelly" and cards[0]["cost_credits"] == 12000


def list_page(rows):
    cards = "".join(
        f'<a href="https://www.fut.gg/players/{i}-{n.lower()}/27-{i}/"><div class="fc-card"><img alt="{n} - {r} - {v}">'
        f'<span>{pos}</span><span>9{i}.0</span>{f"<span>{p}</span>" if p else ""}</div></a>'
        for i, (n, r, v, pos, p) in enumerate(rows, start=1))
    return f"<html><head><title>EA FC 27 Players - FUT.GG</title></head><body>{cards}</body></html>"


def test_short_prices():
    f = sources.parse_short_price
    assert (f("6.8M"), f("783K"), f("750"), f("EXTINCT"), f(None)) == (6_800_000, 783_000, 750, None, None)


def test_list_parse_and_detection():
    html = list_page([("Pelé", 95, "Base Icon", "CAM", "6.8M"), ("Raphinha", 88, "Rare", "LM", "550K"),
                      ("Ronaldo", 94, "Base Icon", "ST", None)])
    assert sources.is_list_page(html) and not sources.is_list_page(gg_page())
    rows = sources.parse_futgg_list(html)
    assert [(r["name"], r["version"], r["position"], r["price"]) for r in rows] == [
        ("Pelé", "Icon 95", "CAM", 6_800_000), ("Raphinha", "Gold 88", "LM", 550_000), ("Ronaldo", "Icon 94", "ST", None)]


def test_list_updates_known_prices_and_reports_unknown(client):
    client.post("/api/v1/import/pages", json={"pages": [{"name": "a", "html": gg_page(name="Pelé", rating=95, rarity="Base Icon", price="5,000,000")}],
                                              "dry_run": False})
    lst = list_page([("Pelé", 95, "Base Icon", "CM", "6.8M"), ("Zico", 91, "Base Icon", "CAM", "4.9M")])
    r = client.post("/api/v1/import/pages", json={"pages": [{"name": "lista.html", "html": lst}], "dry_run": False}).json()
    l = r["lists"][0]
    assert (l["total"], l["prices_updated"], [u["name"] for u in l["unknown"]]) == (2, 1, ["Zico"])
    assert client.get("/api/v1/cards").json()[0]["cost_credits"] == 6_800_000


def test_crawl_with_fake_site(client, tmp_path):
    from eafcmeta import db, fetch
    pages = {"https://x/list": list_page([("Pelé", 95, "Base Icon", "CM", "6.8M"), ("Zico", 91, "Base Icon", "CM", "4.9M")]),
             "https://www.fut.gg/players/1-pelé/27-1/": gg_page(name="Pelé", rating=95, rarity="Base Icon"),
             "https://www.fut.gg/players/2-zico/27-2/": gg_page(name="Zico", rating=91, rarity="Base Icon")}
    conn = db.connect()
    r = fetch.crawl(conn, ["https://x/list"], fetch=lambda u: pages[u], delay=0, check_robots=False, log=lambda *_: None)
    assert (r["new"], r["stopped"]) == (2, None)
    assert conn.execute("select count(*) from cards").fetchone()[0] == 2
    # limite --max e blocco anti-bot
    conn2 = db.connect(str(tmp_path / "b.db"))
    r = fetch.crawl(conn2, ["https://x/list"], fetch=lambda u: pages[u], delay=0, check_robots=False, max_cards=1, log=lambda *_: None)
    assert r["new"] == 1

    def blocked(u):
        raise fetch.Blocked("HTTP 403")

    r = fetch.crawl(db.connect(str(tmp_path / "c.db")), ["https://x/list"], fetch=blocked, delay=0, check_robots=False)
    assert r["stopped"] == "HTTP 403" and r["new"] == 0


def futbin_list(rows):
    trs = "".join(
        f'<tr class="player-row text-nowrap"><td class="table-name"><a class="player-row-playercard" href="https://www.futbin.com/27/player/{i}/{n.lower()}">'
        f'<img alt=""><img class="playercard-27-special-img" alt="{n}"><img alt="Nation"><div class="table-player-revision">{rev}</div></a></td>'
        f'<td class="table-rating">{r}</td><td class="table-pos">{pos} ++</td><td class="table-price">{p} 3.1%</td></tr>'
        for i, (n, r, rev, pos, p) in enumerate(rows, start=1))
    return f"<html><head><title>Players | FUTBIN</title></head><body><table>{trs}</table></body></html>"


def test_futbin_list(client):
    html = futbin_list([("Pelé", 95, "Icon", "CAM", "6.8M"), ("Buffon", 92, "Icon", "GK", "865K"), ("Kelly", 86, "Rare", "RM", "-")])
    assert sources.is_list_page(html)
    rows = sources.parse_list(html)
    assert [(r["name"], r["version"], r["position"], r["price"]) for r in rows] == [
        ("Pelé", "Icon 95", "CAM", 6_800_000), ("Buffon", "Icon 92", "GK", 865_000), ("Kelly", "Gold 86", "RM", None)]
    r = client.post("/api/v1/import/pages", json={"pages": [{"name": "l.html", "html": html}], "dry_run": True}).json()
    l = r["lists"][0]
    assert l["unsupported"] == 0 and {u["name"] for u in l["unknown"]} == {"Pelé", "Kelly", "Buffon"}


def test_base_prefix_unified():
    assert sources.normalize_version("Base Icon", 95) == sources.normalize_version("Icon", 95) == "Icon 95"


def test_normal_is_gold():
    assert sources.normalize_version("Normal", 88) == sources.normalize_version("Rare", 88) == "Gold 88"


GG_GK_STATS = ("Attributes|Chemistry Style|Diving|92|Diving|92|Handling|88|Handling|88|Kicking|79|Kicking|79|Reflexes|94|"
               "Reflexes|94|Reactions|93|Speed|59|Acceleration|60|Sprint Speed|57|Positioning|92|Positioning|92|GK Basic|L")


def test_futgg_goalkeeper():
    html = gg_page(name="Nadine Angerer", rating=92, rarity="Base Icon").replace(GG_STATS, GG_GK_STATS.replace("|", "</div><div>"))
    html = html.replace("CM (FC Barcelona)", "GK (Germany)").replace("</div><div>".join(GG_STATS.split("|")), "</div><div>".join(GG_GK_STATS.split("|")))
    d = sources.parse_page(html)
    assert (d["name"], d["version"], d["position"]) == ("Nadine Angerer", "Icon 92", "GK")
    assert d["stats"] == {"gk_diving": 92, "gk_handling": 88, "gk_kicking": 79, "gk_reflexes": 94, "reactions": 93,
                          "acceleration": 60, "sprint_speed": 57, "gk_positioning": 92}
    assert sources.to_card(d).position == "GK"


def test_list_in_same_batch_sees_new_cards(client):
    pages = [{"name": "lista.html", "html": list_page([("Pelé", 95, "Base Icon", "CM", "6.8M")])},  # prima nel lotto
             {"name": "a.html", "html": gg_page(name="Pelé", rating=95, rarity="Base Icon", price="5,000,000")}]
    l = client.post("/api/v1/import/pages", json={"pages": pages, "dry_run": False}).json()["lists"][0]
    assert l["prices_updated"] == 1 and l["unknown"] == []


def test_futbin_goalkeeper_icon():
    head = "|".join(["45K", "92", "GK", "++", "R", "1", "3", "Angerer", "Add to Compare"])
    stats = ("Player Stats|Evolution Builder|92|Show All Stats|Diving|92|Handling|88|Kicking|79|Reflexes|94|Reactions|93|Speed|59|"
             "Acceleration|60|Sprint Speed|57|Positioning|92|Pace|58|Acceleration|60|Sprint Speed|57|Shooting|22|Att. Position|12|"
             "Total Chem. style added:")
    info = "|".join(["Germany", "Icons", "EA FC ICONS", "All Icons", "Skills", "1", "Weak Foot", "3"])
    mk = lambda s: "".join(f"<div>{t}</div>" for t in s.split("|"))
    ld = json.dumps({"@type": "Product", "name": "Angerer All Icons EA FC 27 Player Card"})
    html = (f'<html><head><title>FUTBIN</title><script type="application/ld+json">{ld}</script></head><body>'
            f'<div class="price-box platform-ps-only"><div class="price lowest-price-1">217,000</div></div>'
            f'{mk(head)}{mk(info)}<a class="playStyle-table-icon column active psplus"><div>Far Reach</div></a>{mk(stats)}</body></html>')
    d = sources.parse_page(html)
    assert (d["name"], d["version"], d["position"], d["price"]) == ("Angerer", "Icon 92", "GK", 217000)
    assert d["playstyles"] == ["Far Reach+"] and "finishing" not in d["stats"] and d["stats"]["gk_reflexes"] == 94
