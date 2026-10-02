"""Raccolta carte da pagine-giocatore salvate (FUT.GG / FUTBIN).

    python -m eafcmeta.collect cartella_o_file.html [altri ...]

Le pagine valide vengono salvate (carte già presenti: aggiornate, con storico prezzi); quelle non leggibili sono
elencate con il motivo.
"""
import sys
from pathlib import Path

from pydantic import ValidationError

from . import db, sources


def import_pages(conn, pages: list[tuple[str, str]], dry_run: bool = False) -> dict:
    """pages: [(nome_file, html)]. Ogni pagina è indipendente: le valide si salvano, le altre sono segnalate."""
    new = updated = 0
    errors, cards = [], []
    for name, html in pages:
        try:
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
            _, status = db.upsert_card(conn, card)
            new += status == "new"
            updated += status == "updated"
            found.append({"file": name, "name": card.name, "version": card.version, "position": card.position,
                          "price": card.price, "site": d["site"], "status": status})
        conn.rollback() if dry_run else conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"new": new, "updated": updated, "errors": errors, "cards": found, "saved": not dry_run and bool(found)}


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
    for e in r["errors"]:
        print(f"  ERRORE   {e['file']}: {e['error']}")
    print(f"{r['new']} nuove, {r['updated']} aggiornate, {len(r['errors'])} non lette")
