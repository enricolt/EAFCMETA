"""Import da testo incollato (CSV/TSV/JSON) e aggiornamento rapido dei prezzi.

Colonne accettate (nomi in italiano o inglese): name/nome, version/versione, position/pos, price/prezzo,
is_sbc/sbc, body_type/body, weak_foot/wf, skill_moves/sm, playstyles/ps + una colonna per ogni stat
(acceleration, sprint_speed, ...). Prezzi tipo 12.500, 12500, 12k, 1.2m.
"""
import csv
import io
import json
import math
import re
import sys

from pydantic import ValidationError

from . import db, scoring
from .models import CardIn

MAX_ROWS = 2000
# errori che un testo incollato strano puo' provocare: sempre errore di riga/di testo, mai un 500
BAD_INPUT = (ValueError, TypeError, KeyError, OverflowError, RecursionError, csv.Error, json.JSONDecodeError)
ALIASES = {"nome": "name", "versione": "version", "pos": "position", "posizione": "position", "prezzo": "price",
           "sbc": "is_sbc", "body": "body_type", "bodytype": "body_type", "wf": "weak_foot", "piede_debole": "weak_foot",
           "sm": "skill_moves", "skill": "skill_moves", "ps": "playstyles", "playstyle": "playstyles"}
TRUE = {"1", "true", "si", "sì", "yes", "x", "y"}


def parse_price(v) -> int:
    s = str(v).strip().lower().replace(" ", "")
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    if mult > 1:
        s = s.replace(",", ".")
        n = float(s) * mult
        if not math.isfinite(n):
            raise ValueError(f"prezzo non valido: {v}")
        return int(round(n))
    if not re.fullmatch(r"[\d.,]+", s):
        raise ValueError(f"prezzo non valido: {v}")
    return int(s.replace(".", "").replace(",", ""))


def _norm_key(k: str) -> str:
    k = re.sub(r"[\s.\-]+", "_", k.strip().lower()).strip("_")
    return ALIASES.get(k, k)


def _rows_from_text(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        raise ValueError("testo vuoto")
    if text[0] in "[{":
        data = json.loads(text)
        data = data.get("cards", []) if isinstance(data, dict) else data
        if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
            raise ValueError("JSON non valido: serve una lista di carte")
        rows = []
        for d in data:
            stats = d.get("stats") or {}
            if not isinstance(stats, dict):
                raise ValueError("JSON non valido: 'stats' deve essere un oggetto {stat: valore}")
            flat = {k: v for k, v in d.items() if k != "stats"}
            flat.update(stats)
            rows.append(flat)
        return rows
    first = text.splitlines()[0]
    delim = max("\t;,", key=first.count)
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def _to_card(raw: dict, stat_keys: set[str]) -> CardIn:
    r = {_norm_key(str(k)): v for k, v in raw.items() if k is not None}
    stats = {k: int(r[k]) for k in stat_keys if str(r.get(k, "")).strip() != ""}
    ps = r.get("playstyles") or []
    if isinstance(ps, str):
        ps = [p for p in re.split(r"[;|,]", ps) if p.strip()]
    return CardIn(
        name=str(r.get("name") or ""), version=str(r.get("version") or ""), position=str(r.get("position") or ""),
        price=parse_price(r.get("price", "")), is_sbc=str(r.get("is_sbc", "")).strip().lower() in TRUE,
        stats=stats, playstyles=ps, body_type=str(r.get("body_type") or "Average"),
        weak_foot=int(r.get("weak_foot") or 3), skill_moves=int(r.get("skill_moves") or 3))


def _err(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg'].removeprefix('Value error, ')}" for x in e.errors())
    if isinstance(e, (OverflowError, RecursionError)):
        return "valore troppo grande o struttura troppo annidata"
    if isinstance(e, csv.Error):
        return f"CSV non valido: {e}"
    if isinstance(e, (TypeError, KeyError)):
        return f"dati non validi ({type(e).__name__})"
    return str(e)


def import_text(conn, text: str, dry_run: bool = False) -> dict:
    """Tutto o niente: se dry_run o se ci sono errori non scrive nulla. Ritorna riepilogo con errori per riga."""
    stat_keys = set(scoring.load_config()["stat_keys"])
    try:
        raw_rows = _rows_from_text(text)
    except BAD_INPUT as e:
        return {"new": 0, "updated": 0, "errors": [{"row": 0, "error": _err(e)}], "saved": False, "total": 0}
    if len(raw_rows) > MAX_ROWS:
        return {"new": 0, "updated": 0, "errors": [{"row": 0, "error": f"troppe righe (max {MAX_ROWS})"}],
                "saved": False, "total": len(raw_rows)}
    cards, errors = [], []
    for i, raw in enumerate(raw_rows, start=2):  # riga 1 = intestazione
        try:
            cards.append(_to_card(raw, stat_keys))
        except (ValidationError, *BAD_INPUT) as e:
            errors.append({"row": i, "error": _err(e)})
    new = updated = 0
    try:
        for c in cards:
            _, status = db.upsert_card(conn, c)
            new += status == "new"
            updated += status == "updated"
        if dry_run or errors:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"new": new, "updated": updated, "errors": errors, "total": len(raw_rows),
            "saved": not dry_run and not errors}


def update_prices(conn, text: str, dry_run: bool = False) -> dict:
    """Righe 'nome;versione;prezzo' oppure 'nome;prezzo'. Tutto o niente."""
    updated, errors = 0, []
    plan: list[tuple[int, int]] = []
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) > MAX_ROWS:
        return {"updated": 0, "errors": [{"row": 0, "error": f"troppe righe (max {MAX_ROWS})"}], "saved": False}
    for n, line in enumerate(lines, start=1):
        parts = [p.strip() for p in re.split(r"[;\t]", line)] if re.search(r"[;\t]", line) else \
            [p.strip() for p in line.split(",")]
        try:
            if len(parts) not in (2, 3):
                raise ValueError("formato: nome;versione;prezzo")
            price = parse_price(parts[-1])
            if price < 0 or price > 100_000_000:
                raise ValueError("prezzo fuori intervallo")
            q, args = "SELECT id FROM cards WHERE name = ? COLLATE NOCASE", [parts[0]]
            if len(parts) == 3:
                q += " AND version = ? COLLATE NOCASE"
                args.append(parts[1])
            ids = [r["id"] for r in conn.execute(q, args)]
            if not ids:
                raise ValueError(f"carta non trovata: {parts[0]}")
            if len(ids) > 1:
                raise ValueError(f"'{parts[0]}' è ambigua ({len(ids)} carte): indica anche la versione")
            plan.append((ids[0], price))
        except BAD_INPUT as e:
            errors.append({"row": n, "error": str(e)})
    if not errors and not dry_run:
        for cid, price in plan:
            db.set_price(conn, cid, price)
        conn.commit()
    return {"updated": len(plan), "errors": errors, "saved": not dry_run and not errors}


TEMPLATE_COLUMNS = ["name", "version", "position", "price", "is_sbc", "body_type", "weak_foot", "skill_moves",
                    "playstyles"]


def template_csv() -> str:
    cfg = scoring.load_config()
    cols = TEMPLATE_COLUMNS + cfg["stat_keys"]
    return ";".join(cols) + "\n"


if __name__ == "__main__":
    c = db.connect()
    print(import_text(c, open(sys.argv[1], encoding="utf-8").read()))
