"""Revisione critica, area E: parser dei siti e import testuale robusti (mai 500, errori di riga chiari)."""
import json

import pytest

from conftest import card
from eafcmeta import importer, sources
from test_collect import GG_SIGNALS, fb_page, gg_page


def _pages(client, *htmls, dry_run=False):
    r = client.post("/api/v1/import/pages", json={"pages": [{"name": f"p{i}", "html": h} for i, h in enumerate(htmls)],
                                                  "dry_run": dry_run})
    assert r.status_code == 200, r.text
    return r.json()


def test_short_price_decimal_comma_and_thousands():
    f = sources.parse_short_price
    assert f("6,8M") == 6_800_000 and f("6.8M") == 6_800_000 and f("1,5K") == 1_500
    assert f("1.234.567") == 1_234_567 and f("1,234,567") == 1_234_567 and f("750") == 750
    assert f("1.2.3M") is None and f("EXTINCT") is None and f(None) is None and f("infK") is None


def test_extinct_price_keeps_the_card_with_warning(client):
    r = _pages(client, gg_page(price="EXTINCT"))
    assert r["errors"] == [] and r["new"] == 1
    assert r["cards"][0]["price"] == 0
    assert any("prezzo" in w["warning"] for w in r["warnings"])
    assert client.get("/api/v1/cards").json()[0]["cost_credits"] == 0


def test_missing_price_on_futbin_page_keeps_the_card(client):
    html = fb_page().replace('<div class="price lowest-price-1">80,000</div>', '<div class="price lowest-price-1">N/A</div>')
    r = _pages(client, html)
    assert r["errors"] == [] and r["new"] == 1 and r["warnings"]


def test_extinct_does_not_overwrite_known_price(client):
    _pages(client, gg_page(price="800,000"))
    r = _pages(client, gg_page(price="EXTINCT"))
    assert r["updated"] == 1 and r["cards"][0]["price"] == 800_000
    assert any("precedente" in w["warning"] for w in r["warnings"])
    assert client.get("/api/v1/cards").json()[0]["cost_credits"] == 800_000


def test_decimal_tier_percentage_does_not_fail_the_page(client):
    gg = gg_page().replace("</body>", "".join(f"<div>{t}</div>" for t in "Tier vote|321|placements|S|79.5%|Where would".split("|")) + "</body>")
    r = _pages(client, gg)
    assert r["errors"] == [] and r["new"] == 1
    assert sources.parse_page(gg)["signals"]["gg_tier_pct"] == 80


def test_view_all_in_another_section_does_not_hide_the_roles():
    before = "".join(f"<div>{t}</div>" for t in "Similar players|View All|Next page|Footer".split("|"))
    gg = gg_page().replace("<div>Players</div>", before + "<div>Players</div>", 1)
    gg = gg.replace("</body>", "".join(f"<div>{t}</div>" for t in GG_SIGNALS.split("|")) + "</body>")
    sig = sources.parse_page(gg)["signals"]
    assert sig["gg_role"] == "ST" and sig["gg_rating"] == 87.9 and sig["gg_rank"] == 70


def test_unknown_body_type_warns_instead_of_silent_average(client):
    r = _pages(client, gg_page().replace("Lean Short", "Wobbly Tall"))
    assert r["errors"] == [] and any("body type" in w["warning"].lower() for w in r["warnings"])
    assert sources.parse_page(gg_page().replace("Lean Short", "Normal (170-)"))["warnings"] == []  # "Normal" = Average
    assert sources.parse_page(gg_page())["warnings"] == []


# --- import testuale ---------------------------------------------------------------------------------------------
HEAD = "name;position;price;acceleration;sprint_speed;agility;reactions;composure;finishing;shot_power;positioning;ball_control"


def _row(price="1000", name="X"):
    return f"{name};ST;{price};90;90;85;88;85;90;85;88;87"


@pytest.mark.parametrize("price", ["infk", "1e999m", "nanm", "-5k", "1e999999999k"])
def test_bad_prices_are_row_errors_not_500(client, price):
    r = client.post("/api/v1/import", json={"text": HEAD + "\n" + _row(price), "dry_run": True})
    assert r.status_code == 200
    assert r.json()["errors"] and r.json()["errors"][0]["row"] == 2
    r = client.post("/api/v1/prices", json={"text": f"X;{price}", "dry_run": True})
    assert r.status_code == 200 and r.json()["errors"]


def test_json_stats_as_list_is_error_not_500(client):
    for payload in ('[{"name":"A","position":"ST","price":"1k","stats":[1,2,3]}]',
                    '[{"name":"A","position":"ST","price":"1k","stats":"x"}]',
                    '{"cards": [{"name":"A","stats":5}]}',
                    '[1,2]', '[[[[' + '[' * 100_000 + ']'):
        r = client.post("/api/v1/import", json={"text": payload, "dry_run": True})
        assert r.status_code == 200, payload[:40]
        assert r.json()["errors"] and r.json()["saved"] is False


def test_huge_csv_field_is_error_not_500(client):
    text = HEAD + "\n" + _row(name="A" * 140_000)
    r = client.post("/api/v1/import", json={"text": text, "dry_run": True})
    assert r.status_code == 200 and r.json()["errors"]


def test_opinions_import_robust(client):
    client.post("/api/v1/cards", json=card(name="Kelly", version="Gold 86"))
    for text in ('[{"name":"Kelly","creator":"E","stance":"sì","score":"inf"}]', "[" * 100_000,
                 "carta;creator;scelta\n" + "A" * 140_000 + ";X;sì"):
        r = client.post("/api/v1/import/opinions", json={"text": text, "dry_run": True})
        assert r.status_code == 200 and r.json()["errors"]


def test_parse_price_rejects_non_finite():
    for v in ("infk", "1e999m", "nank"):
        with pytest.raises(ValueError):
            importer.parse_price(v)
    assert importer.parse_price("1,2m") == 1_200_000
    json.dumps(importer.parse_price("12k"))
