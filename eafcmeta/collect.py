"""Raccolta carte da pagine-giocatore salvate (FUT.GG / FUTBIN).

    python -m eafcmeta.collect cartella_o_file.html [altri ...]

Le pagine valide vengono salvate (carte già presenti: aggiornate, con storico prezzi); quelle non leggibili sono
elencate con il motivo.
"""
import sys
from pathlib import Path

from pydantic import ValidationError

from types import SimpleNamespace

from . import db, scoring, sources
from .models import OpinionIn

TIER_STANCE = {"S": "yes", "A": "yes", "B": "maybe", "C": "no", "D": "no", "F": "no"}
MIN_VOTES = 30  # sotto questa soglia il voto della community è poco affidabile


def community_opinion(sig: dict, url: str):
    if "gg_tier" not in sig:
        return None
    n, pct = sig["gg_tier_votes"], sig["gg_tier_pct"]
    note = "" if n >= MIN_VOTES else " (pochi voti: poco affidabile)"
    return OpinionIn(creator="FUT.GG (community)", stance=TIER_STANCE[sig["gg_tier"]], url=url if url.startswith("http") else "",
                     reason=f"Tier {sig['gg_tier']} per il {pct}% di {n} voti della community{note}.")


def apply_list(conn, entries: list[dict], dry_run: bool) -> dict:
    """Pagina elenco: aggiorna il prezzo delle carte già note; le altre vanno scaricate (pagina del giocatore)."""
    updated, unknown, unsupported = 0, [], 0
    supported = scoring.load_config()["position_to_role"]
    for e in entries:
        if e["position"] not in supported:
            unsupported += 1  # es. portieri: l'app non li valuta ancora
            continue
        row = db.find_card(conn, SimpleNamespace(name=e["name"], version=e["version"], position=e["position"]))
        if row is None:
            unknown.append({k: e[k] for k in ("name", "version", "position", "price", "url")})
        elif e["price"] is not None and e["price"] != row["price"]:
            if not dry_run:
                db.set_price(conn, row["id"], e["price"])
            updated += 1
    return {"total": len(entries), "prices_updated": updated, "unknown": unknown, "unsupported": unsupported}


def import_pages(conn, pages: list[tuple[str, str]], dry_run: bool = False) -> dict:
    """pages: [(nome_file, html)]. Ogni pagina è indipendente: le valide si salvano, le altre sono segnalate."""
    new = updated = 0
    errors, cards, lists, pending = [], [], [], []
    for name, html in pages:
        try:
            if sources.is_list_page(html):
                pending.append((name, sources.parse_list(html)))  # le liste si applicano dopo le carte del lotto
                continue
            d = sources.parse_page(html)
            cards.append((name, d, sources.to_card(d)))
        except sources.PageError as e:
            errors.append({"file": name, "error": str(e)})
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg'].removeprefix('Value error, ')}" for x in e.errors())
            errors.append({"file": name, "error": msg})
        except Exception as e:  # noqa: BLE001 - pagina con struttura inattesa
            errors.append({"file": name, "error": f"struttura della pagina inattesa ({type(e).__name__})"})
    found = []
    try:
        for name, d, card in cards:
            cid, status = db.upsert_card(conn, card)
            op = community_opinion(card.signals, d.get("url", "")) if d["site"] == "futgg" else None
            if op:
                db.upsert_opinion(conn, cid, op)
            new += status == "new"
            updated += status == "updated"
            found.append({"file": name, "name": card.name, "version": card.version, "position": card.position,
                          "price": card.price, "site": d["site"], "status": status})
        for name, entries in pending:
            lists.append({"file": name, **apply_list(conn, entries, dry_run)})
        conn.rollback() if dry_run else conn.commit()
    except Exception:
        conn.rollback()
        raise
    saved = not dry_run and bool(found or any(l["prices_updated"] for l in lists))
    return {"new": new, "updated": updated, "errors": errors, "cards": found, "lists": lists, "saved": saved}


def read_paths(paths: list[str]) -> list[tuple[str, str]]:
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(p.rglob("*.htm*")) if p.is_dir() else [p]
    return [(f.name, f.read_text(encoding="utf-8", errors="replace")) for f in files]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    r = import_pages(db.connect(), read_paths(sys.argv[1:]))
    for c in r["cards"]:
        print(f"  {c['status']:8} {c['name']} · {c['version']} ({c['position']}) {c['price']:,}  [{c['site']}]")
    for l in r["lists"]:
        print(f"  elenco {l['file']}: {l['total']} giocatori, {l['prices_updated']} prezzi aggiornati, "
              f"{len(l['unknown'])} non ancora nell'app, {l['unsupported']} non supportati (portieri)")
    for e in r["errors"]:
        print(f"  ERRORE   {e['file']}: {e['error']}")
    print(f"{r['new']} nuove, {r['updated']} aggiornate, {len(r['errors'])} non lette")
