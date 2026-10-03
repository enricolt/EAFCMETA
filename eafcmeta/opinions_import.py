"""Import di GRUPPO dei pareri dei creator da testo incollato (CSV/TSV/JSON), per esempio da un foglio di calcolo.

Colonne (nomi anche in italiano): carta/nome, versione (facoltativa), ruolo (facoltativo), creator, scelta (sì/dipende/no),
voto (facoltativo), motivo, link. La carta si trova per nome (come per le pagine dei siti); se è ambigua la riga è
segnalata e non si indovina. Tutto o niente: con un errore non si salva nulla.
"""
from __future__ import annotations

import csv
import io
import json
import re
from types import SimpleNamespace

from pydantic import ValidationError

from . import db
from .models import OpinionIn

ALIAS = {"carta": "name", "nome": "name", "giocatore": "name", "player": "name", "card": "name", "versione": "version",
         "ruolo": "position", "posizione": "position", "pos": "position", "scelta": "stance", "parere": "stance",
         "voto": "score", "punteggio": "score", "motivo": "reason", "perché": "reason", "perche": "reason",
         "note": "reason", "link": "url", "fonte": "url", "autore": "creator", "canale": "creator"}
STANCE = {"si": "yes", "sì": "yes", "yes": "yes", "y": "yes", "da prendere": "yes", "dipende": "maybe", "maybe": "maybe",
          "forse": "maybe", "no": "no", "n": "no", "da evitare": "no", "evita": "no"}
MAX_ROWS = 2000


def _norm(k: str) -> str:
    k = re.sub(r"\s+", " ", k.strip().lower())
    return ALIAS.get(k, k)


def _rows(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        raise ValueError("testo vuoto")
    if text[0] in "[{":
        data = json.loads(text)
        data = data.get("opinions", []) if isinstance(data, dict) else data
        if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
            raise ValueError("JSON non valido: serve una lista di pareri")
        return data
    delim = max("\t;,", key=text.splitlines()[0].count)
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def _find(conn, name: str, version: str, position: str):
    q, args = "SELECT id, name, version, position FROM cards WHERE name = ? COLLATE NOCASE", [name]
    exact = [dict(r) for r in conn.execute(q, args)]
    if not exact:  # nome parziale: "Kelly" = "Chloe Kelly"
        words = set(db._words(name))
        exact = [dict(r) for r in conn.execute("SELECT id, name, version, position FROM cards")
                 if words and (words <= db._words(r["name"]) or db._words(r["name"]) <= words)]
    if version:
        exact = [r for r in exact if r["version"].lower() == version.lower()]
    if position:
        exact = [r for r in exact if r["position"].upper() == position.upper()]
    return exact


def import_opinions(conn, text: str, dry_run: bool = False) -> dict:
    try:
        raw = _rows(text)
    except (ValueError, json.JSONDecodeError, csv.Error) as e:
        return {"saved": False, "count": 0, "errors": [{"row": 0, "error": str(e)}]}
    if len(raw) > MAX_ROWS:
        return {"saved": False, "count": 0, "errors": [{"row": 0, "error": f"troppe righe (max {MAX_ROWS})"}]}
    plan, errors = [], []
    for i, row in enumerate(raw, start=2):
        try:
            r = {_norm(str(k)): ("" if v is None else str(v).strip()) for k, v in row.items() if k is not None}
            name = r.get("name", "")
            if not name:
                raise ValueError("manca il nome della carta")
            stance = STANCE.get(r.get("stance", "").lower())
            if stance is None:
                raise ValueError(f"scelta non riconosciuta: «{r.get('stance', '')}» (usa sì, dipende, no)")
            score = float(r["score"].replace(",", ".")) if r.get("score") else None
            op = OpinionIn(creator=r.get("creator", ""), stance=stance, score=score, reason=r.get("reason", ""),
                           url=r.get("url", ""))
            found = _find(conn, name, r.get("version", ""), r.get("position", ""))
            if not found:
                raise ValueError(f"carta non trovata: {name}")
            if len(found) > 1:
                raise ValueError(f"«{name}» è ambigua ({len(found)} carte): indica anche versione o ruolo")
            plan.append((found[0]["id"], op))
        except (ValueError, ValidationError) as e:
            msg = "; ".join(x["msg"].removeprefix("Value error, ") for x in e.errors()) if isinstance(e, ValidationError) else str(e)
            errors.append({"row": i, "error": msg})
    if not errors and not dry_run:
        for cid, op in plan:
            db.upsert_opinion(conn, cid, op)
        conn.commit()
    return {"saved": not errors and not dry_run and bool(plan), "count": len(plan), "errors": errors}
