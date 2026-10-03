"""CLI: python -m eafcmeta.research {paste,youtube,list,accept,reject} ...  (equivalente delle API /api/v1/research)."""
import argparse
import json
import sys

from .. import db
from . import pipeline, store
from .llm import get_llm
from .sources import SOURCES_NOTE, PastedText, ResearchError, XSource, YouTubeSource


def _print_proposal(p: dict) -> None:
    card = p["card"] or {}
    amb = "  [AMBIGUA]" if p["ambiguous"] else ""
    crit = ", ".join(f"{c['criterion']}{'+' if c['polarity'] > 0 else '-'}" for c in p["criteria"]) or "-"
    print(f"#{p['id']} [{p['status']}] {card.get('name', '?')} ({card.get('version', '')} {card.get('position', '')}) "
          f"- {p['creator']}: {p['stance']}" + (f" {p['score']:g}" if p["score"] is not None else "")
          + f"  conf {p['confidence']:.2f}{amb}")
    print(f"    motivo: {p['reason']}\n    criteri: {crit}\n    fonte: {p['url'] or p['source']}")
    if p["note"]:
        print(f"    nota: {p['note']}")


def _report(rep: dict) -> None:
    print(f"Modalità: {rep['mode']} - nuove proposte: {len(rep['created'])}, già presenti: {len(rep['duplicates'])}")
    for p in rep["created"]:
        _print_proposal(p)
    for w in rep["warnings"]:
        print(f"Attenzione: {w}")
    if rep["created"]:
        print("Per confermare: python -m eafcmeta.research accept ID   (o reject ID). Nulla è ancora nei pareri.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eafcmeta.research", description="Ricerca dei pareri dei pro con revisione umana.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mode_help = "auto (modello se c'è ANTHROPIC_API_KEY, altrimenti offline) | offline | llm"
    p = sub.add_parser("paste", help="proposte da un testo incollato (stdin o --file)")
    p.add_argument("--creator", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--card-id", type=int)
    p.add_argument("--file")
    p.add_argument("--mode", default="auto", help=mode_help)
    y = sub.add_parser("youtube", help="cerca nei video del canale del creator (serve YOUTUBE_API_KEY)")
    y.add_argument("--creator", required=True)
    y.add_argument("--query", required=True)
    y.add_argument("--mode", default="auto", help=mode_help)
    x = sub.add_parser("x", help="post recenti di un handle (X API a pagamento, serve X_BEARER_TOKEN)")
    x.add_argument("--creator", required=True)
    x.add_argument("--handle", default="")
    x.add_argument("--max-posts", type=int, default=100, help="tetto di spesa: post letti al massimo (default 100)")
    x.add_argument("--yes", action="store_true", help="esegui senza chiedere conferma dopo la stima del costo")
    x.add_argument("--mode", default="auto", help=mode_help)
    sub.add_parser("sources", help="fonti disponibili e limiti")
    ls = sub.add_parser("list", help="elenca le proposte")
    ls.add_argument("--status", choices=["pending", "accepted", "rejected"])
    a = sub.add_parser("accept", help="accetta una proposta (diventa un parere)")
    a.add_argument("id", type=int)
    a.add_argument("--stance", choices=["yes", "maybe", "no"])
    a.add_argument("--score", type=float)
    a.add_argument("--reason")
    a.add_argument("--card-id", type=int)
    r = sub.add_parser("reject", help="rifiuta una proposta")
    r.add_argument("id", type=int)
    args = ap.parse_args(argv)

    conn = db.connect()
    try:
        if args.cmd == "paste":
            text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
            items = PastedText(text, args.creator, args.url).search()
            rep = pipeline.process_items(conn, items, pipeline.make_extractor(args.mode, get_llm()), args.card_id)
            conn.commit()
            _report(rep)
        elif args.cmd == "youtube":
            extractor = pipeline.make_extractor(args.mode, get_llm())
            src = YouTubeSource.for_creator(args.creator)
            items = src.search(args.query)
            rep = pipeline.process_items(conn, items, extractor)
            rep["warnings"] = list(src.warnings) + rep["warnings"]
            conn.commit()
            _report(rep)
        elif args.cmd == "x":
            extractor = pipeline.make_extractor(args.mode, get_llm())
            src = XSource.for_creator(args.creator, args.handle, args.max_posts)
            print(src.estimate())  # la stima si vede SEMPRE prima di spendere
            if not args.yes and input("Procedo? [s/N] ").strip().lower() not in ("s", "si", "sì", "y"):
                print("Annullato: nessuna spesa.")
                return 0
            rep = pipeline.process_items(conn, src.search(""), extractor)
            conn.commit()
            _report(rep)
        elif args.cmd == "sources":
            print(SOURCES_NOTE)
        elif args.cmd == "list":
            props = store.list_proposals(conn, args.status)
            for p_ in props:
                _print_proposal(p_)
            if not props:
                print("Nessuna proposta.")
        elif args.cmd == "accept":
            kw = {k: v for k, v in (("stance", args.stance), ("score", args.score), ("reason", args.reason),
                                    ("card_id", args.card_id)) if v is not None}
            res = store.accept(conn, args.id, **kw)
            conn.commit()
            print(f"Accettata: parere #{res['opinion_id']}" + (" (ha sostituito il precedente)" if res["replaced"] else ""))
        elif args.cmd == "reject":
            store.reject(conn, args.id)
            conn.commit()
            print("Rifiutata.")
    except (ResearchError, ValueError, OSError) as e:
        conn.rollback()
        print(f"Errore: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
