"""Lettura di pagine-giocatore salvate da FUT.GG e FUTBIN (HTML) -> dati carta nel formato dell'app.

Si parte da pagine che l'utente salva col browser (Ctrl+S). Il testo visibile delle pagine è letto a "token"
ancorandosi alle etichette (Pace, Acceleration, Weak Foot, ...), che sono più stabili delle classi CSS.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from . import scoring

STAT_LABELS = {
    "acceleration": "acceleration", "sprintspeed": "sprint_speed", "attposition": "positioning", "attpos": "positioning",
    "finishing": "finishing", "shotpower": "shot_power", "longshots": "long_shots", "volleys": "volleys",
    "penalties": "penalties", "vision": "vision", "crossing": "crossing", "fkacc": "fk_accuracy",
    "shortpass": "short_passing", "longpass": "long_passing", "curve": "curve", "agility": "agility",
    "balance": "balance", "reactions": "reactions", "ballcontrol": "ball_control", "composure": "composure",
    "interceptions": "interceptions", "headingacc": "heading_accuracy", "defaware": "defensive_awareness",
    "standtackle": "standing_tackle", "slidetackle": "sliding_tackle", "jumping": "jumping", "stamina": "stamina",
    "strength": "strength", "aggression": "aggression"}


class PageError(ValueError):
    pass


def _tokens(soup: BeautifulSoup) -> list[str]:
    for s in soup(["script", "style", "noscript", "svg"]):
        s.decompose()
    return [t for t in soup.get_text("\n", strip=True).split("\n") if t.strip()]


def _after(tokens: list[str], label: str, start: int = 0) -> str | None:
    try:
        return tokens[tokens.index(label, start) + 1]
    except (ValueError, IndexError):
        return None


def _int(v: str | None, what: str) -> int:
    if v is None or not re.fullmatch(r"\d+", v.strip()):
        raise PageError(f"non trovo {what}")
    return int(v)


def _price(v: str | None) -> int:
    if v is None or not re.fullmatch(r"[\d.,]+", v.strip()):
        raise PageError("non trovo il prezzo")
    return int(re.sub(r"[.,]", "", v))


def _body_type(text: str | None) -> str:
    t = (text or "").lower()
    for key, val in (("lean", "Lean"), ("stocky", "Stocky"), ("unique", "Unique"), ("custom", "Custom")):
        if key in t:
            return val
    return "Average"


def _stats(tokens: list[str], start: int, end: int) -> dict[str, int]:
    """Coppie etichetta -> numero. 'Dribbling' compare due volte: la prima è la categoria, la seconda la stat."""
    stats, drib = {}, 0
    i = start
    while i < end - 1:
        key = re.sub(r"[^a-z]", "", tokens[i].lower())
        if key == "dribbling" and re.fullmatch(r"\d+", tokens[i + 1]):
            drib += 1
            if drib == 2:
                stats["dribbling"] = int(tokens[i + 1])
        elif key in STAT_LABELS and re.fullmatch(r"\d+", tokens[i + 1]):
            stats[STAT_LABELS[key]] = int(tokens[i + 1])
        i += 1
    return stats


def detect_site(html: str) -> str:
    head = html[:200_000].lower()
    if "futbin" in head and "playstyle-table-icon" in html.lower():
        return "futbin"
    if "fut.gg" in head:
        return "futgg"
    raise PageError("pagina non riconosciuta (servono pagine-giocatore salvate da FUT.GG o FUTBIN)")


def parse_futbin(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    plays = []
    for a in soup.select("a.playStyle-table-icon"):
        name = a.get_text(" ", strip=True)
        img = a.select_one("img.ps-logo")
        src = (img.get("src") or "") if img else ""
        plus = "plus" in " ".join(a.get("class", [])).lower() or bool(re.search(r"plus\.png", src, re.I))
        if name and a.get("class") and "active" in a.get("class"):
            plays.append(name + ("+" if plus else ""))
    price_el = soup.select_one(".price-box.platform-ps-only .lowest-price-1") or soup.select_one(".lowest-price-1")
    price = _price(price_el.get_text(strip=True) if price_el else None)
    t = _tokens(soup)
    try:
        i_pac = t.index("Pac")
    except ValueError:
        raise PageError("non trovo la carta (Pac)")
    name = t[i_pac - 2]
    positions = set(scoring.load_config()["position_to_role"]) | {"GK"}
    rating = position = None
    for j in range(i_pac - 3, max(0, i_pac - 40), -1):
        if re.fullmatch(r"\d{2}", t[j]) and t[j + 1] in positions:
            rating, position = int(t[j]), t[j + 1]
            break
    if rating is None:
        raise PageError("non trovo valutazione e posizione")
    i_sk = t.index("Skills") if "Skills" in t else -1
    rarity = t[i_sk - 1] if i_sk > 0 else "Gold"
    try:
        s0, s1 = t.index("Player Stats"), t.index("Total Chem. style added:")
    except ValueError:
        raise PageError("non trovo le statistiche")
    return {"site": "futbin", "name": name, "version": f"{rarity} {rating}", "position": position, "price": price,
            "skill_moves": _int(_after(t, "Skills"), "skill moves"), "weak_foot": _int(_after(t, "Weak Foot"), "piede debole"),
            "body_type": _body_type(_after(t, "B.Type")), "playstyles": plays, "stats": _stats(t, s0, s1)}


def parse_futgg(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    name = version = rating = position = None
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            d = json.loads(s.string or "")
        except json.JSONDecodeError:
            continue
        if d.get("@type") == "BreadcrumbList" and len(d["itemListElement"]) >= 3:
            name = d["itemListElement"][1]["name"]
            version = re.sub(r"\s*OVR$", "", d["itemListElement"][2]["name"])
        if d.get("@type") == "WebPage":
            m = re.search(r"(\d+) OVR ([A-Z]{2,3}) \(", d.get("description", ""))
            if m:
                rating, position = int(m.group(1)), m.group(2)
    if not (name and version and position):
        raise PageError("non trovo nome/versione/posizione")
    titles = []
    for d in soup.select("div[title]"):
        sv = d.find("svg")
        if sv is not None and sv.get("height") == "42" and d["title"] not in titles:
            titles.append(d["title"])
    t = _tokens(soup)
    price = _price(t[t.index("Current price") - 1] if "Current price" in t else None)
    try:
        s0 = next(i for i, x in enumerate(t) if x == "Attributes" and t[i + 1] == "Chemistry Style")
        s1 = t.index("Basic", s0)
    except (StopIteration, ValueError):
        raise PageError("non trovo le statistiche")
    return {"site": "futgg", "name": name, "version": version, "position": position, "price": price,
            "skill_moves": _int(_after(t, "Skill Moves"), "skill moves"), "weak_foot": _int(_after(t, "Weak Foot"), "piede debole"),
            "body_type": _body_type(_after(t, "Body Type")), "playstyles": titles, "stats": _stats(t, s0, s1)}


def parse_page(html: str) -> dict:
    site = detect_site(html)
    return parse_futbin(html) if site == "futbin" else parse_futgg(html)


def to_card(d: dict):
    """Dati estratti -> CardIn (con tutte le validazioni dell'app)."""
    from .models import CardIn
    return CardIn(name=d["name"], version=d["version"], position=d["position"], price=d["price"], is_sbc=False,
                  stats=d["stats"], playstyles=d["playstyles"], body_type=d["body_type"],
                  weak_foot=d["weak_foot"], skill_moves=d["skill_moves"])
