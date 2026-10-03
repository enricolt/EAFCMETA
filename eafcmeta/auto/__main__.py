"""CLI: python -m eafcmeta.auto run|status|undo   (comodo per l'Utilità di pianificazione di Windows).

  run [--se-dovuto]  esegue ora la raccolta (con --se-dovuto solo se l'ultima è più vecchia dell'intervallo)
  status             mostra ultima esecuzione, prossima, canali e conteggi
  undo [--si]        annulla TUTTI i pareri automatici (mai i manuali); chiede conferma salvo --si
"""
import argparse
import json
import sys
import time

from .. import db
from . import config, opinions, runner
from .errors import AutoError


def _status(conn, cfg) -> dict:
    last = runner.last_run(conn)
    nxt = None
    ls = runner.last_started_epoch(conn)
    if cfg["enabled"] and ls is not None:
        nxt = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ls + cfg["interval_hours"] * 3600))
    return {
        "enabled": cfg["enabled"] and config.env_enabled(), "interval_hours": cfg["interval_hours"],
        "ultima_esecuzione": last, "prossima_esecuzione_utc": nxt,
        "pareri_automatici": conn.execute("SELECT COUNT(*) FROM opinions WHERE auto=1").fetchone()[0],
        "canali": [dict(r) for r in conn.execute("SELECT name, channel_id, handle, title, subscribers, verified_auto FROM channels ORDER BY name")],
        "video_elaborati": conn.execute("SELECT COUNT(*) FROM processed_items").fetchone()[0]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eafcmeta.auto", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="esegue ora la raccolta")
    r.add_argument("--se-dovuto", action="store_true", help="solo se l'ultima esecuzione è più vecchia dell'intervallo")
    sub.add_parser("status", help="stato e conteggi")
    u = sub.add_parser("undo", help="annulla tutti i pareri automatici")
    u.add_argument("--si", action="store_true", help="non chiedere conferma")
    a = ap.parse_args(argv)
    try:
        cfg = config.load()
        conn = db.connect()
        try:
            if a.cmd == "status":
                print(json.dumps(_status(conn, cfg), indent=1, ensure_ascii=False))
                return 0
            if a.cmd == "undo":
                n = conn.execute("SELECT COUNT(*) FROM opinions WHERE auto=1").fetchone()[0]
                if n and not a.si and input(f"Annullare {n} pareri automatici? [s/N] ").strip().lower() not in ("s", "si", "sì", "y"):
                    print("Annullato: nessun parere toccato.")
                    return 1
                print(f"Rimossi {opinions.undo(conn)} pareri automatici (i manuali sono intatti).")
                return 0
            if not config.env_enabled():
                print("Raccolta spenta dalla variabile EAFCMETA_AUTO=0: nessuna esecuzione.")
                return 0
            if a.se_dovuto:
                ls = runner.last_started_epoch(conn)
                if ls is not None and time.time() - ls < cfg["interval_hours"] * 3600:
                    print("Non ancora dovuta: nessuna esecuzione.")
                    return 0
        finally:
            conn.close()
        res = runner.run_once(cfg=cfg)
        s = res["summary"]
        print(f"Esecuzione {res['status']}: " + ", ".join(f"{k}={v}" for k, v in s.items() if isinstance(v, int)))
        for k, v in (s.get("fermato") or {}).items():
            print(f"  FERMATO [{k}]: {v}")
        for e in s.get("errori", [])[:10]:
            print(f"  errore: {e}")
        return 0 if res["status"] in ("ok", "parziale", "saltata") else 1
    except AutoError as e:
        print(f"Errore: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
