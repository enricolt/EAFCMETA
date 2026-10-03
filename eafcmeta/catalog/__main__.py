"""Catalogo da riga di comando.

    python -m eafcmeta.catalog update [--mode new|backfill|prices] [--pages N] [--restart]
    python -m eafcmeta.catalog status
    python -m eafcmeta.catalog recompute        (ricalcola TUTTE le valutazioni in cache)

NON provato contro il sito vero (dal cloud la rete e' bloccata). Un blocco del sito ferma l'aggiornamento: nessun aggiramento.
"""
import argparse
import json
import sys
import time

from .. import db
from . import config as catcfg, evaluation, updater


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eafcmeta.catalog", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update", help="aggiorna il catalogo (a comando)")
    up.add_argument("--mode", choices=updater.MODES, default="new")
    up.add_argument("--pages", type=int, default=None, help="pagine dell'elenco da leggere (default: pages_per_update)")
    up.add_argument("--restart", action="store_true", help="backfill: riparte dalla prima pagina")
    sub.add_parser("status", help="stato del catalogo e dell'ultimo aggiornamento")
    sub.add_parser("recompute", help="ricalcola tutte le valutazioni")
    a = ap.parse_args(argv)
    if a.cmd == "update":
        if a.restart:
            c = db.connect()
            updater.reset_backfill(c)
            c.close()
        r = updater.run_update(mode=a.mode, pages=a.pages)
        s = r["summary"]
        print(f"{r['status']}: {s.get('nuove', 0)} nuove, {s.get('aggiornate', 0)} aggiornate, {s.get('prezzi', 0)} prezzi, "
              f"{s.get('valutate', 0)} valutate, {s.get('richieste', 0)} richieste")
        if s.get("messaggio"):
            print(s["messaggio"])
        for n in s.get("note", []):
            print(f"  nota: {n}")
        for e in s.get("errori", [])[:10]:
            print(f"  errore: {e}")
        if s.get("fermato"):
            print(f"FERMATO: {s['fermato']}")
        return 0 if r["status"] in ("ok", "parziale") else 1
    conn = db.connect()
    try:
        if a.cmd == "recompute":
            t = time.perf_counter()
            r = evaluation.recompute(conn)
            print(f"{r['valutate']} carte rivalutate in {time.perf_counter() - t:.2f} s")
            return 0
        cfg = catcfg.load()
        n = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        ev = conn.execute("SELECT COUNT(*) FROM evaluations WHERE final IS NOT NULL").fetchone()[0]
        print(json.dumps({"carte": n, "valutate": ev, "config": catcfg.public(cfg), "ultimo": updater.last_finished(conn)},
                         ensure_ascii=False, indent=1))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
