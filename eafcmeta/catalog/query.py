"""Elenco del catalogo (e della collezione): filtri, ordinamento, paginazione, sfaccettature. Legge SOLO la cache delle valutazioni.

L'ordinamento per uscita (`released`, predefinito) e' released_at decrescente; le carte senza data vanno in coda, ordinate per
first_seen e poi per id. Anche per gli altri ordinamenti i valori mancanti vanno sempre in coda.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata

from .dates import day_bounds
from .versions import prefixes_of, version_family

SORTS = ("released", "score", "price", "rating", "name")
VERDICTS = ("MUST_DO", "NEUTRAL", "AVOID")
METAS = ("top", "meta", "playable", "below")

_SELECT = """SELECT c.id, c.name, c.version, c.position, c.rating, c.released_at, c.first_seen, c.price, c.is_sbc, c.pro_score,
       e.base, e.final, e.pro_share, e.verdict, e.value_gap, e.verdict_reason, e.meta_level, e.meta_label, e.summary,
       e.top_stats, e.bonus_playstyles, e.opinions_count, (col.card_id IS NOT NULL) AS in_collection"""
_FROM = " FROM cards c LEFT JOIN evaluations e ON e.card_id = c.id LEFT JOIN collection col ON col.card_id = c.id"


class QueryError(ValueError):
    """Parametro non valido (l'API lo traduce in 422)."""


def fold(text) -> str:
    """Minuscolo senza accenti: 'Vinícius' -> 'vinicius' (per la ricerca testuale)."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(s: str) -> str:
    return "%" + _esc(s) + "%"


def csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _choice(name: str, values: list[str], allowed: tuple, upper: bool) -> list[str]:
    out = [v.upper() if upper else v.lower() for v in values]
    bad = [v for v in out if v not in allowed]
    if bad:
        raise QueryError(f"{name}: valori non validi {bad} (ammessi: {', '.join(allowed)})")
    return out


def build_where(conn: sqlite3.Connection, f: dict, collection_only: bool = False) -> tuple[str, list]:
    conn.create_function("fold", 1, fold, deterministic=True)
    where, args = [], []
    if collection_only:
        where.append("col.card_id IS NOT NULL")
    elif f.get("in_collection") is not None:
        where.append("col.card_id IS NOT NULL" if f["in_collection"] else "col.card_id IS NULL")
    for word in fold(f.get("q")).split():
        where.append("fold(c.name || ' ' || c.version) LIKE ? ESCAPE '\\'")
        args.append(_like(word))
    pos = [p.upper() for p in csv(f.get("position"))]
    if pos:
        where.append(f"c.position IN ({','.join('?' * len(pos))})")
        args += pos
    fams = csv(f.get("version"))
    if fams:
        ors = []
        for fam in fams:
            for p in prefixes_of(fam):
                n = len(p) + 2
                ors.append("(lower(c.version) = ? OR (lower(c.version) LIKE ? ESCAPE '\\' AND substr(c.version, ?) GLOB '[0-9]*' "
                           "AND substr(c.version, ?) NOT GLOB '*[^0-9]*'))")
                args += [p, _esc(p) + " %", n, n]
        where.append("(" + " OR ".join(ors) + ")")
    for col, key, op in (("c.rating", "rating_min", ">="), ("c.rating", "rating_max", "<="),
                         ("c.price", "price_min", ">="), ("c.price", "price_max", "<=")):
        if f.get(key) is not None:
            where.append(f"{col} {op} ?")
            args.append(f[key])
    vd = _choice("verdict", csv(f.get("verdict")), VERDICTS, True)
    if vd:
        where.append(f"e.verdict IN ({','.join('?' * len(vd))})")
        args += vd
    mt = _choice("meta", csv(f.get("meta")), METAS, False)
    if mt:
        where.append(f"e.meta_level IN ({','.join('?' * len(mt))})")
        args += mt
    if f.get("has_opinions") is not None:
        where.append("e.opinions_count > 0" if f["has_opinions"] else "COALESCE(e.opinions_count, 0) = 0")
    if f.get("released_after"):
        b = day_bounds(f["released_after"])
        if b is None:
            raise QueryError("released_after: usa una data AAAA-MM-GG")
        where.append("c.released_at >= ?")
        args.append(b[0])
    if f.get("released_before"):
        b = day_bounds(f["released_before"])
        if b is None:
            raise QueryError("released_before: usa una data AAAA-MM-GG")
        where.append("c.released_at < ?")  # il giorno indicato e' compreso
        args.append(b[1])
    return (" WHERE " + " AND ".join(where)) if where else "", args


def order_by(sort: str, order: str) -> str:
    if sort not in SORTS:
        raise QueryError(f"sort: valori ammessi {', '.join(SORTS)}")
    if order not in ("asc", "desc"):
        raise QueryError("order: asc o desc")
    d = order.upper()
    return {
        "released": f"c.released_at IS NULL, c.released_at {d}, c.first_seen {d}, c.id {d}",
        "score": f"e.final IS NULL, e.final {d}, c.id {d}",
        "price": f"c.price {d}, c.id {d}",
        "rating": f"c.rating IS NULL, c.rating {d}, c.released_at {d}, c.id {d}",
        "name": f"c.name COLLATE NOCASE {d}, c.id {d}",
    }[sort]


def _item(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "version": r["version"], "version_family": version_family(r["version"]),
            "position": r["position"], "rating": r["rating"], "released_at": r["released_at"], "first_seen": r["first_seen"],
            "cost_credits": r["price"], "is_sbc": bool(r["is_sbc"]),
            "scores": {"base_score": r["base"], "final_score": r["final"], "pro_sentiment_score": r["pro_score"], "pro_share": r["pro_share"]},
            "verdict": r["verdict"], "value_gap": r["value_gap"], "verdict_reason": r["verdict_reason"],
            "meta_level": r["meta_level"], "meta_label": r["meta_label"], "summary": r["summary"],
            "top_stats": json.loads(r["top_stats"] or "[]"), "bonus_playstyles": json.loads(r["bonus_playstyles"] or "[]"),
            "opinions_count": r["opinions_count"] or 0, "in_collection": bool(r["in_collection"])}


def list_items(conn: sqlite3.Connection, f: dict, sort: str, order: str, limit: int, offset: int,
               collection_only: bool = False) -> dict:
    where, args = build_where(conn, f, collection_only)
    ob = order_by(sort, order)
    total = conn.execute("SELECT COUNT(*)" + _FROM + where, args).fetchone()[0]
    rows = conn.execute(_SELECT + _FROM + where + f" ORDER BY {ob} LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    return {"total": total, "offset": offset, "limit": limit, "items": [_item(r) for r in rows]}


def collection_stats(conn: sqlite3.Connection, meta_levels=("top", "meta")) -> dict:
    """Numeri della testata della collezione (sull'intera collezione, senza filtri)."""
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(c.price), 0) v, AVG(e.final) a, "
                     f"COALESCE(SUM(e.meta_level IN ({','.join('?' * len(meta_levels))})), 0) m "
                     "FROM collection col JOIN cards c ON c.id = col.card_id LEFT JOIN evaluations e ON e.card_id = c.id",
                     list(meta_levels)).fetchone()
    best = [x["id"] for x in conn.execute(
        "SELECT c.id FROM collection col JOIN cards c ON c.id = col.card_id LEFT JOIN evaluations e ON e.card_id = c.id "
        "WHERE e.final IS NOT NULL ORDER BY e.final DESC, c.id LIMIT 3")]
    return {"cards": r["n"], "total_value": int(r["v"]), "avg_score": None if r["a"] is None else round(r["a"], 2),
            "meta_count": int(r["m"]), "best": best}


def facets(conn: sqlite3.Connection) -> dict:
    pos = [{"value": r["position"], "count": r["n"]} for r in conn.execute(
        "SELECT position, COUNT(*) n FROM cards GROUP BY position ORDER BY n DESC, position")]
    fam: dict[str, int] = {}
    for r in conn.execute("SELECT version, COUNT(*) n FROM cards GROUP BY version"):
        k = version_family(r["version"])
        fam[k] = fam.get(k, 0) + r["n"]
    ver = [{"value": k, "count": n} for k, n in sorted(fam.items(), key=lambda kv: (-kv[1], kv[0]))]
    r = conn.execute("SELECT COUNT(*) n, MIN(rating) rmin, MAX(rating) rmax, MIN(price) pmin, MAX(price) pmax, "
                     "MIN(released_at) dmin, MAX(released_at) dmax FROM cards").fetchone()
    return {"positions": pos, "versions": ver, "rating": {"min": r["rmin"], "max": r["rmax"]},
            "price": {"min": r["pmin"], "max": r["pmax"]}, "released": {"min": r["dmin"], "max": r["dmax"]}, "total": r["n"]}
