"""Calibrazione dal meta dei pro: confronta il nostro punteggio coi pareri dei creator e propone soglie e pesi.

Nulla cambia da solo: l'app mostra i numeri e propone; la calibrazione si applica (e si annulla) su richiesta e finisce
in `config/local.json`, sopra `patch.json`. Con pochi dati non propone niente (soglie minime in `calibration`).
"""
from __future__ import annotations

import json
import math

from . import db, scoring

DEFAULTS = {"pro_meta_at": 80, "min_cards": 12, "min_cards_role": 8}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if n < 3 or vx == 0 or vy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def collect(conn, cfg: dict) -> list[dict]:
    """Carte con almeno un parere di un creator (il voto automatico della community dei siti non conta)."""
    ops = db.opinions_by_card(conn)
    rows = []
    for r in conn.execute("SELECT * FROM cards"):
        c = db.row_to_card(r)
        pros = [o for o in ops.get(c["id"], []) if not o["creator"].lower().startswith("fut.gg")]
        if not pros:
            continue
        try:
            ex = scoring.explain(c, cfg)
        except ValueError:
            continue
        rows.append({"card": c, "base": ex["base"], "role": ex["role"], "pro": scoring.pro_from_opinions(pros, cfg)})
    return rows


def _confusion(rows: list[dict], threshold: float, pro_at: float) -> dict:
    tp = sum(r["base"] >= threshold and r["pro"] >= pro_at for r in rows)
    fp = sum(r["base"] >= threshold and r["pro"] < pro_at for r in rows)
    fn = sum(r["base"] < threshold and r["pro"] >= pro_at for r in rows)
    tn = len(rows) - tp - fp - fn
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "accuracy": round((tp + tn) / len(rows), 3)}


def report(conn, cfg: dict) -> dict:
    cal = {**DEFAULTS, **cfg.get("calibration", {})}
    rows = collect(conn, cfg)
    n = len(rows)
    out = {"n": n, "needed": cal["min_cards"], "ready": n >= cal["min_cards"], "active": scoring.local_config_path().exists(),
           "pro_meta_at": cal["pro_meta_at"], "thresholds": cfg["meta"]}
    if not out["ready"]:
        out["message"] = (f"Servono almeno {cal['min_cards']} carte con il parere di almeno un creator per calibrare "
                          f"(ora {n}). Più creator e più carte inserisci, più la calibrazione è affidabile.")
        return out
    m = cfg["meta"]
    out["correlation"] = _pearson([r["base"] for r in rows], [r["pro"] for r in rows])
    out["current"] = _confusion(rows, m["meta"], cal["pro_meta_at"])
    # soglia "meta" che meglio separa le carte che i pro approvano da quelle che non approvano
    cand = sorted({r["base"] for r in rows})
    mids = [(a + b) / 2 for a, b in zip(cand, cand[1:])] or [m["meta"]]
    best = max(mids, key=lambda t: (_confusion(rows, t, cal["pro_meta_at"])["accuracy"], -abs(t - m["meta"])))
    new_meta = round(best, 1)
    top = min(100.0, round(new_meta + (m["top"] - m["meta"]), 1))
    playable = max(1.0, round(new_meta - (m["meta"] - m["playable"]), 1))
    out["suggested"] = {"thresholds": {"top": top, "meta": new_meta, "playable": playable},
                        "after": _confusion(rows, new_meta, cal["pro_meta_at"]), "weights": {}}
    # pesi per ruolo: correlazione di ogni stat col giudizio dei pro, attenuata dal numero di carte (poco dato = poco cambio)
    for role, w in cfg["role_weights"].items():
        sub = [r for r in rows if r["role"] == role]
        if len(sub) < cal["min_cards_role"]:
            continue
        shrink = 0.5 * len(sub) / (len(sub) + 20)
        changes = []
        for stat, old in w.items():
            c = _pearson([r["card"]["stats"][stat] for r in sub], [r["pro"] for r in sub])
            if c is None:
                continue
            new = round(max(0.2, old * (1 + shrink * c)), 2)
            changes.append({"stat": stat, "old": old, "new": new, "corr": round(c, 2)})
        if changes:
            out["suggested"]["weights"][role] = {"n": len(sub), "changes": sorted(changes, key=lambda x: -abs(x["corr"]))}
    return out


def apply(conn, cfg: dict, thresholds: bool, weights: bool) -> dict:
    rep = report(conn, cfg)
    if not rep["ready"]:
        raise ValueError(rep["message"])
    path = scoring.local_config_path()
    local = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if thresholds:
        local["meta"] = rep["suggested"]["thresholds"]
    if weights:
        rw = local.setdefault("role_weights", {})
        for role, info in rep["suggested"]["weights"].items():
            new = {c["stat"]: c["new"] for c in info["changes"]}
            rw[role] = {**cfg["role_weights"][role], **new}  # le stat senza dati restano com'erano
    base = json.loads(scoring.CONFIG_PATH.read_text(encoding="utf-8"))
    scoring.validate_config(scoring._merge(base, local))  # se non è valida non si scrive nulla
    path.write_text(json.dumps(local, indent=1), encoding="utf-8")
    scoring.load_config.cache_clear()
    return report(conn, scoring.load_config())


def reset() -> None:
    scoring.local_config_path().unlink(missing_ok=True)
    scoring.load_config.cache_clear()
