"""Valutazione dell'estrazione offline dei pareri su un corpus di trascrizioni SINTETICHE con etichette.

Uso:  python tests/research_eval.py [--split dev|holdout|all] [--verbose]

LIMITI (da dichiarare sempre insieme ai numeri): il corpus è scritto da chi ha scritto anche l'estrattore, quindi i testi
riflettono ciò che chi li scrive si immagina di un sottotitolo automatico; non sono trascrizioni reali di creator. I numeri
misurano la coerenza e la robustezza sui casi previsti (anche avversari), NON la precisione che si avrà su YouTube.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eafcmeta.research import extract as E, resolver  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "transcripts_eval.json"
HIGH = 0.8


def load(split: str = "all") -> tuple[list[dict], list[dict]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cards = [{"id": c["id"], "name": c["name"], "version": c["version"], "position": c["position"]} for c in data["cards"]]
    docs = [d for d in data["transcripts"] if split == "all" or d["split"] == split]
    return cards, docs


def run_doc(doc: dict, cards: list[dict]):
    cands = resolver.resolve(doc["text"], cards)
    return E.OfflineExtractor().extract(doc["text"], cands, "creator", "").proposals


def evaluate(split: str = "all", threshold: float = HIGH) -> dict:
    cards, docs = load(split)
    rows = []   # una riga per proposta
    expected_total = expected_nonthin = 0
    per_doc = {}
    for doc in docs:
        exp = {e["card"]: e for e in doc["expected"]}
        props = run_doc(doc, cards)
        per_doc[doc["id"]] = props
        for p in props:
            e = exp.get(p.card_id)
            rows.append({"doc": doc["id"], "card": p.card_id, "conf": p.confidence, "stance": p.stance,
                         "ok": bool(e and e["stance"] == p.stance), "expected_stance": e["stance"] if e else None,
                         "criteria": p.criteria, "exp": e, "tags": doc["tags"], "ambiguous": p.ambiguous,
                         "details": p.details, "score": p.score})
        expected_total += len(exp)
        expected_nonthin += sum(1 for e in exp.values() if not e["thin"])
    out = {"docs": len(docs), "expected": expected_total, "expected_nonthin": expected_nonthin}

    def stats(th: float) -> dict:
        sel = [r for r in rows if r["conf"] >= th]
        ok = [r for r in sel if r["ok"]]
        found = {(r["doc"], r["card"]) for r in ok}
        exp_keys = {(d["id"], e["card"]): e for d in docs for e in d["expected"]}
        rec = sum(1 for k in exp_keys if k in found)
        rec_nt = sum(1 for k, e in exp_keys.items() if k in found and not e["thin"])
        cp = cw = cn = ce = 0
        for r in ok:
            e = r["exp"]
            got = dict(r["criteria"])
            ignore = set(e["criteria_ignore"])
            for k, pol in got.items():
                if k in ignore:
                    continue
                if e["criteria"].get(k) == pol:
                    cp += 1
                else:
                    cw += 1
            for k, pol in e["criteria"].items():
                ce += 1
                cn += got.get(k) == pol
        return {"threshold": th, "extracted": len(sel), "correct": len(ok),
                "precision": len(ok) / len(sel) if sel else 1.0,
                "recall": rec / max(1, len(exp_keys)), "recall_nonthin": rec_nt / max(1, expected_nonthin),
                "criteria_precision": cp / (cp + cw) if cp + cw else 1.0,
                "criteria_recall": cn / ce if ce else 1.0, "criteria_found": cp, "criteria_wrong": cw}

    out["high"] = stats(threshold)
    out["mid"] = stats(0.6)
    out["any"] = stats(0.0)
    bins = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    out["calibration"] = [{"bin": f"{a:.1f}-{min(b, 1.0):.1f}", "n": len([r for r in rows if a <= r["conf"] < b]),
                           "accuracy": (sum(r["ok"] for r in rows if a <= r["conf"] < b) /
                                        max(1, len([r for r in rows if a <= r["conf"] < b])))} for a, b in bins]
    nop = [r for r in rows if not r["exp"]]
    out["false_on_unexpected"] = [{"doc": r["doc"], "card": r["card"], "conf": r["conf"], "stance": r["stance"]} for r in nop]
    out["wrong_high"] = [{"doc": r["doc"], "card": r["card"], "conf": r["conf"], "stance": r["stance"],
                          "expected": r["expected_stance"]} for r in rows if r["conf"] >= threshold and not r["ok"]]
    out["rows"] = rows
    out["per_doc"] = per_doc
    return out


def report(res: dict, verbose: bool = False) -> str:
    lines = [f"Trascrizioni: {res['docs']}  pareri attesi: {res['expected']} (non esili: {res['expected_nonthin']})"]
    for name in ("high", "mid", "any"):
        s = res[name]
        lines.append(f"conf >= {s['threshold']:.1f}: estratti {s['extracted']:3d}  corretti {s['correct']:3d}  "
                     f"precisione {s['precision']:.3f}  richiamo {s['recall']:.3f} (non esili {s['recall_nonthin']:.3f})  "
                     f"criteri: precisione {s['criteria_precision']:.3f} richiamo {s['criteria_recall']:.3f}")
    lines.append("calibrazione: " + "  ".join(f"[{b['bin']}] n={b['n']} acc={b['accuracy']:.2f}" for b in res["calibration"]))
    if res["wrong_high"]:
        lines.append("ERRORI ad alta confidenza: " + json.dumps(res["wrong_high"], ensure_ascii=False))
    if verbose:
        lines.append("estrazioni su carte non attese: " + json.dumps(res["false_on_unexpected"], ensure_ascii=False))
        for r in res["rows"]:
            mark = "OK " if r["ok"] else "XX "
            lines.append(f"{mark}{r['doc']:14s} card {r['card']:2d} {r['stance']:5s} conf {r['conf']:.2f} atteso {r['expected_stance']} "
                         f"crit {r['criteria']} {r['details'].get('dropped', '')}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["dev", "holdout", "holdout2", "holdout3", "all"])
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    print(report(evaluate(a.split), a.verbose))
