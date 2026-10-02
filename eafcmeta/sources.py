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
    "strength": "strength", "aggression": "aggression",
    # portieri: su FUT.GG le etichette sono semplici (verificato); le varianti "GK ..." sono per FUTBIN (da verificare)
    "diving": "gk_diving", "handling": "gk_handling", "kicking": "gk_kicking",
    "positioning": "gk_positioning", "reflexes": "gk_reflexes",
    "gkdiving": "gk_diving", "gkhandling": "gk_handling", "gkkicking": "gk_kicking",
    "gkpositioning": "gk_positioning", "gkreflexes": "gk_reflexes"}


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


def normalize_version(rarity: str, rating: int) -> str:
    """'Rare'/'Common' non cambiano le stats: si unificano come 'Gold' così le due fonti danno la stessa versione."""
    rarity = re.sub(r"\bIcons\b", "Icon", re.sub(r"\bHeroes\b", "Hero", re.sub(r"^All\s+", "", rarity.strip())))
    base = re.sub(r"\b(Rare|Common|Normal)\b", "", re.sub(r"^Base\s+", "", rarity)).strip(" -") or "Gold"
    return f"{base} {rating}"


DIAMOND = "M128,12.808L243.192,128"  # forma dell'icona di un PlayStyle normale su FUT.GG; le altre forme sono PlayStyle+


def _num(x: str) -> float | None:
    try:
        return float(x.replace(",", ""))
    except ValueError:
        return None


def signals_futgg(t: list[str]) -> dict:
    """Voto della community (tier) e GG Rating, se presenti nella pagina."""
    sig: dict = {}
    if "Tier vote" in t:
        i = t.index("Tier vote")
        votes, tier, pct = t[i + 1:i + 2], t[i + 3:i + 4], t[i + 4:i + 5]
        if votes and votes[0].isdigit() and tier and tier[0] in ("S", "A", "B", "C", "D", "F") and pct and pct[0].endswith("%"):
            sig.update(gg_tier=tier[0], gg_tier_pct=int(pct[0][:-1]), gg_tier_votes=int(votes[0]))
    if "View All" in t:  # classifica dei ruoli: "ST | 87.9 | Advanced Forward | ++ | #70 Ranked"
        i = t.index("View All")
        role, val = t[i + 1:i + 2], _num(t[i + 2]) if i + 2 < len(t) else None
        rank = next((int(m.group(1)) for x in t[i + 3:i + 8] if (m := re.fullmatch(r"#(\d+) Ranked", x))), None)
        if role and val is not None:
            sig.update(gg_role=role[0], gg_rating=val)
            if rank:
                sig["gg_rank"] = rank
    return sig


def signals_futbin(t: list[str]) -> dict:
    sig: dict = {}
    if "FUTBIN Rating" in t and "RPP Map" in t:  # "83.4 | CM | Playmaker++ | Rank #85"
        i = t.index("RPP Map")
        val = _num(t[i + 1]) if i + 1 < len(t) else None
        rank = next((int(m.group(1)) for x in t[i + 2:i + 6] if (m := re.fullmatch(r"Rank #(\d+)", x))), None)
        if val is not None and i + 2 < len(t):
            sig.update(futbin_rating=val, futbin_role=t[i + 2])
            if rank:
                sig["futbin_rank"] = rank
    return sig


def _page_url(soup: BeautifulSoup) -> str:
    for sc in soup.select('script[type="application/ld+json"]'):
        for x in _ld_items(sc):
            u = x.get("url") if x.get("@type") == "WebPage" else (x.get("@id") if x.get("@type") == "Product" else None)
            if isinstance(u, str) and u.startswith("http"):
                return u.split("#")[0]
    return ""


def detect_site(html: str) -> str:
    head = html[:200_000].lower()
    if "futbin" in head and "playstyle-table-icon" in html.lower():
        return "futbin"
    if "fut.gg" in head:
        return "futgg"
    raise PageError("pagina non riconosciuta (servono pagine-giocatore salvate da FUT.GG o FUTBIN)")


def _ld_items(script) -> list[dict]:
    try:
        d = json.loads(script.string or "")
    except json.JSONDecodeError:
        return []
    return d.get("@graph", [d]) if isinstance(d, dict) else []


def parse_futbin(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    plays = []
    for a in soup.select("a.playStyle-table-icon"):
        name = a.get_text(" ", strip=True)
        img = a.select_one("img.ps-logo")
        src = (img.get("src") or "") if img else ""
        plus = "plus" in " ".join(a.get("class", [])).lower() or bool(re.search(r"(^|[_/])plus[_.]", src, re.I))
        if name and a.get("class") and "active" in a.get("class"):
            plays.append(name + ("+" if plus else ""))
    price_el = soup.select_one(".price-box.platform-ps-only .lowest-price-1") or soup.select_one(".lowest-price-1")
    price = _price(price_el.get_text(strip=True) if price_el else None)
    page_url = _page_url(soup)
    ld_name = next((x.get("name", "") for sc in soup.select('script[type="application/ld+json"]')
                    for x in _ld_items(sc) if x.get("@type") == "Product"), "")
    t = _tokens(soup)
    try:
        i_cmp = t.index("Add to Compare")
    except ValueError:
        raise PageError("non trovo la carta")
    positions = set(scoring.load_config()["position_to_role"])
    rating = position = name = None
    for j in range(i_cmp - 1, max(0, i_cmp - 60), -1):
        if re.fullmatch(r"\d{2}", t[j]) and t[j + 1] in positions:
            rating, position = int(t[j]), t[j + 1]
            break
    if rating is None:
        raise PageError("non trovo valutazione e posizione")
    for tok in t[j + 2:i_cmp]:  # primo testo "vero": salta '++', 'R', numeri e posizioni alternative
        if not (re.fullmatch(r"[\d.]+|\+*|[RL]", tok) or tok in positions):
            name = tok
            break
    if not name:
        raise PageError("non trovo il nome")
    m = re.fullmatch(re.escape(name) + r"\s+(.+?)\s+EA FC \d+ Player Card", ld_name)
    i_sk = t.index("Skills") if "Skills" in t else -1
    rarity = m.group(1) if m else (t[i_sk - 1] if i_sk > 0 else "Gold")
    try:
        s0, s1 = t.index("Player Stats"), t.index("Total Chem. style added:")
    except ValueError:
        raise PageError("non trovo le statistiche")
    stats = _stats(t, s0, s1)
    if position == "GK":  # sulle pagine dei portieri compaiono anche le stats da giocatore di movimento (inutili)
        stats = {k: v for k, v in stats.items() if k.startswith("gk_") or k in ("reactions", "acceleration", "sprint_speed")}
    return {"site": "futbin", "name": name, "version": normalize_version(rarity, rating), "position": position,
            "price": price, "skill_moves": _int(_after(t, "Skills"), "skill moves"),
            "weak_foot": _int(_after(t, "Weak Foot"), "piede debole"), "body_type": _body_type(_after(t, "B.Type")),
            "playstyles": plays, "stats": stats, "signals": signals_futbin(t), "url": page_url}


def parse_futgg(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    page_url = _page_url(soup)
    name = version = rating = position = None
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            d = json.loads(s.string or "")
        except json.JSONDecodeError:
            continue
        if d.get("@type") == "BreadcrumbList" and len(d["itemListElement"]) >= 3:
            name = d["itemListElement"][1]["name"]
            version = d["itemListElement"][2]["name"]
        if d.get("@type") == "WebPage":
            m = re.search(r"(\d+) OVR ([A-Z]{2,3}) \(", d.get("description", ""))
            if m:
                rating, position = int(m.group(1)), m.group(2)
    if not (name and version and position and rating):
        raise PageError("non trovo nome/versione/posizione")
    version = normalize_version(re.sub(r"\s*\d*\s*OVR$", "", version), rating)
    titles = []
    for d in soup.select("div[title]"):
        sv = d.find("svg")
        if sv is not None and sv.get("height") == "42" and d["title"] not in titles:
            path = sv.find("path")
            plus = path is not None and bool(path.get("d")) and not path["d"].startswith(DIAMOND)
            titles.append(d["title"] + ("+" if plus else ""))
    t = _tokens(soup)
    price = _price(t[t.index("Current price") - 1] if "Current price" in t else None)
    try:
        s0 = next(i for i, x in enumerate(t) if x == "Attributes" and t[i + 1] == "Chemistry Style")
        s1 = next(i for i in range(s0, len(t)) if t[i] in ("Basic", "GK Basic"))
    except StopIteration:
        raise PageError("non trovo le statistiche")
    return {"site": "futgg", "name": name, "version": version, "position": position, "price": price,
            "skill_moves": _int(_after(t, "Skill Moves"), "skill moves"), "weak_foot": _int(_after(t, "Weak Foot"), "piede debole"),
            "body_type": _body_type(_after(t, "Body Type")), "playstyles": titles, "stats": _stats(t, s0, s1),
            "signals": signals_futgg(t), "url": page_url}


def parse_short_price(tok: str | None) -> int | None:
    """'6.8M' -> 6800000, '783K' -> 783000, '750' -> 750; 'EXTINCT'/assente -> None."""
    m = re.fullmatch(r"([\d.,]+)\s*([KkMm]?)", (tok or "").strip())
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(round(n * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]))


def is_list_page(html: str) -> bool:
    """Pagina elenco giocatori (FUT.GG o FUTBIN): molte carte e nessun 'breadcrumb' di un singolo giocatore."""
    head = html[:200_000].lower()
    if "futbin" in head and "player-row" in html:
        return len(BeautifulSoup(html, "lxml").select("tr.player-row")) >= 1
    if "fut.gg" not in head:
        return False
    soup = BeautifulSoup(html, "lxml")
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            d = json.loads(s.string or "")
        except json.JSONDecodeError:
            continue
        if d.get("@type") == "BreadcrumbList" and len(d.get("itemListElement", [])) >= 3:
            return False
    return len(_list_anchors(soup)) >= 1


def _list_anchors(soup):
    return [a for a in soup.select('a[href*="/players/"]') if a.select_one(".fc-card, [class*=fc-card]")]


def parse_futbin_list(html: str) -> list[dict]:
    """Elenco FUTBIN (tabella) -> stessa forma di parse_futgg_list; prezzo = console."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for r in soup.select("tr.player-row"):
        a = r.select_one("a.player-row-playercard")
        img = next((i for i in r.find_all("img") if (i.get("alt") or "").strip()
                    and i["alt"].strip() not in ("Nation", "League", "Club")), None)  # carta base o speciale
        rev = r.select_one(".table-player-revision")
        rating = (r.select_one("td.table-rating") or r).get_text(strip=True)
        pos = (r.select_one("td.table-pos") or r).get_text(" ", strip=True).split(" ")[0]
        price = r.select_one("td.table-price")
        if not (a and img and img.get("alt") and rev and rating.isdigit()):
            continue
        ptxt = price.get_text(" ", strip=True).split(" ")[0] if price else None
        out.append({"name": img["alt"].strip(), "rating": int(rating), "version": normalize_version(rev.get_text(strip=True), int(rating)),
                    "position": pos, "price": parse_short_price(ptxt), "url": a["href"]})
    if not out:
        raise PageError("non trovo giocatori nella lista")
    return out


def parse_list(html: str) -> list[dict]:
    return parse_futbin_list(html) if "futbin" in html[:200_000].lower() and "player-row" in html else parse_futgg_list(html)


def parse_futgg_list(html: str) -> list[dict]:
    """Elenco FUT.GG -> [{name, rating, version, position, price|None, url}]. Niente stats: servono le pagine singole."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in _list_anchors(soup):
        img = a.select_one("img[alt]")
        m = re.fullmatch(r"(.+?) - (\d+) - (.+)", (img.get("alt") if img else "") or "")
        if not m:
            continue
        for sv in a.find_all("svg"):
            sv.decompose()
        toks = [t for t in a.get_text("\n", strip=True).split("\n") if t.strip()]
        rating = int(m.group(2))
        out.append({"name": m.group(1).strip(), "rating": rating, "version": normalize_version(m.group(3).strip(), rating),
                    "position": toks[0] if toks else "", "price": parse_short_price(toks[-1]) if len(toks) >= 3 else None,
                    "url": a["href"]})
    if not out:
        raise PageError("non trovo giocatori nella lista")
    return out


def parse_page(html: str) -> dict:
    site = detect_site(html)
    return parse_futbin(html) if site == "futbin" else parse_futgg(html)


def to_card(d: dict):
    """Dati estratti -> CardIn (con tutte le validazioni dell'app)."""
    from .models import CardIn
    return CardIn(name=d["name"], version=d["version"], position=d["position"], price=d["price"], is_sbc=False,
                  stats=d["stats"], playstyles=d["playstyles"], body_type=d["body_type"],
                  weak_foot=d["weak_foot"], skill_moves=d["skill_moves"], signals=d.get("signals", {}))
