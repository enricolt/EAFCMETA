"""Calibrazione dal meta dei pro: confronta il nostro punteggio coi pareri dei creator e propone soglie e pesi.

Nulla cambia da solo: l'app mostra i numeri e propone; la calibrazione si applica (e si annulla) su richiesta e finisce
in `config/local.json`, sopra `patch.json`. Con pochi dati non propone niente (soglie minime in `calibration`).

Grandezza calibrata: le soglie "top/meta/playable" si calibrano sullo score BASE (statistiche + bonus + regole) e
l'etichetta "meta" (`analysis.meta_level`) si applica allo stesso score base. Lo score finale include i pareri dei creator,
cioe' proprio il bersaglio della calibrazione: usarlo sarebbe circolare.

Niente deriva: pesi e soglie si calcolano SEMPRE a partire da `patch.json` (non dai valori gia' calibrati), quindi applicare
due volte la stessa calibrazione da' lo stesso risultato; in piu' lo scostamento da patch.json e' limitato
(pesi +-`max_weight_drift`, soglia meta +-`max_threshold_shift` punti).
"""
from __future__ import annotations

import json
import math

from . import db, rules, scoring

DEFAULTS = {"pro_meta_at": 80, "min_cards": 12, "min_cards_role": 8, "min_class_cards": 4,
            "max_weight_drift": 0.3, "max_threshold_shift": 3.0}


def _patch() -> dict:
    """Valori di partenza non calibrati (patch.json): la calibrazione riparte sempre da qui."""
    return json.loads(scoring.CONFIG_PATH.read_text(encoding="utf-8"))


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
        rows.append({"card": c, "ex": ex, "base": ex["base"], "role": ex["role"], "pro": scoring.pro_from_opinions(pros, cfg)})
    return rows


def _confusion(rows: list[dict], threshold: float, pro_at: float) -> dict:
    tp = sum(r["base"] >= threshold and r["pro"] >= pro_at for r in rows)
    fp = sum(r["base"] >= threshold and r["pro"] < pro_at for r in rows)
    fn = sum(r["base"] < threshold and r["pro"] >= pro_at for r in rows)
    tn = len(rows) - tp - fp - fn
    tpr = tp / (tp + fn) if tp + fn else 0.0
    tnr = tn / (tn + fp) if tn + fp else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "accuracy": round((tp + tn) / len(rows), 3),
            "balanced_accuracy": round((tpr + tnr) / 2, 3)}


def build_thresholds(cfg: dict, ref: dict, new_meta: float, max_shift: float = 3.0) -> dict | None:
    """Soglie coerenti attorno a `new_meta`: meta spostata al massimo di `max_shift` rispetto a `ref` (patch.json), top e
    playable con gli stessi scarti di `ref`. Garantisce playable < meta < top <= 100, altrimenti None."""
    meta = round(min(ref["meta"] + max_shift, max(ref["meta"] - max_shift, new_meta)), 1)
    top = min(100.0, round(meta + (ref["top"] - ref["meta"]), 1))
    playable = round(meta - (ref["meta"] - ref["playable"]), 1)
    if top <= meta:  # vicino a 100: si abbassa meta invece di far coincidere le soglie
        meta = round(top - 1, 1)
    if playable >= meta:
        playable = round(meta - 1, 1)
    if not 0 < playable < meta < top <= 100:
        return None
    return {"top": top, "meta": meta, "playable": playable}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _split(rule: dict, rows: list[dict], cfg: dict) -> tuple[list[float], list[float]]:
    """Voti dei pro per le carte che la regola colpisce e per le altre dello stesso ruolo."""
    hit, other = [], []
    for r in rows:
        if not rules.applies_to(rule, r["role"]):
            continue
        ctx = rules.make_ctx(r["card"], r["role"], cfg, r["ex"]["unknown_playstyles"])
        (hit if rules.match(rule["when"], ctx) is not None else other).append(r["pro"])
    return hit, other


def rules_calibration(rows: list[dict], cfg: dict) -> dict:
    """Soglie e delta delle regole ATTIVE: spinge il delta verso ciò che i pro mostrano (media dei voti delle carte colpite
    meno quella delle altre), a piccoli passi e solo con dati sufficienti. Le soglie si spostano al massimo di un passo."""
    res = rules.resolve(cfg)
    st = res["settings"]["calibration"]
    cap, out = res["settings"]["max_rule_delta"], []
    for rule in res["rules"]:
        if rule["status"] != "active":
            continue
        hit, other = _split(rule, rows, cfg)
        if len(hit) < st["min_cards_rule"] or len(other) < st["min_cards_rule"]:
            continue
        diff = _mean(hit) - _mean(other)
        old = float(rule["effect"].get("score", 0))
        new = old
        if abs(diff) >= st["min_diff"]:
            new = round(max(-cap, min(cap, old + st["delta_step"] * (1 if diff > 0 else -1))), 3)
        change = {"id": rule["id"], "n_hit": len(hit), "n_other": len(other), "diff": round(diff, 1),
                  "delta": {"old": old, "new": new}, "threshold": None}
        w = rule["when"]
        cmp_keys = [k for k in rules.COMPARATORS if k in w]
        if w["type"] in rules.THRESHOLD_TYPES and len(cmp_keys) == 1:
            k, cur = cmp_keys[0], w[cmp_keys[0]]
            best, best_d = cur, abs(diff)
            for cand in (cur - st["threshold_step"], cur + st["threshold_step"]):
                h2, o2 = _split({**rule, "when": {**w, k: cand}}, rows, cfg)
                if len(h2) >= st["min_cards_rule"] and len(o2) >= st["min_cards_rule"]:
                    d2 = abs(_mean(h2) - _mean(o2))
                    if d2 > best_d + 1e-9:
                        best, best_d = cand, d2
            if best != cur:
                change["threshold"] = {"field": k, "old": cur, "new": best}
        if new != old or change["threshold"]:
            out.append(change)
    return {"min_cards_rule": st["min_cards_rule"], "changes": out}


def report(conn, cfg: dict) -> dict:
    cal = {**DEFAULTS, **cfg.get("calibration", {})}
    rows = collect(conn, cfg)
    n = len(rows)
    out = {"n": n, "needed": cal["min_cards"], "ready": n >= cal["min_cards"], "active": scoring.local_active(),
           "pro_meta_at": cal["pro_meta_at"], "thresholds": cfg["meta"]}
    if not out["ready"]:
        out["message"] = (f"Servono almeno {cal['min_cards']} carte con il parere di almeno un creator per calibrare "
                          f"(ora {n}). Più creator e più carte inserisci, più la calibrazione è affidabile.")
        out["rules"] = {"changes": []}
        return out
    m, ref = cfg["meta"], _patch()["meta"]
    out["correlation"] = _pearson([r["base"] for r in rows], [r["pro"] for r in rows])
    out["current"] = _confusion(rows, m["meta"], cal["pro_meta_at"])
    out["suggested"] = {"thresholds": None, "weights": {}, "on": "base"}
    # soglia "meta" che meglio separa le carte approvate da quelle no, con accuratezza BILANCIATA (le classi sono quasi
    # sempre sbilanciate) e solo se ci sono abbastanza carte in entrambe le classi
    pos = sum(r["pro"] >= cal["pro_meta_at"] for r in rows)
    neg = n - pos
    if min(pos, neg) < cal["min_class_cards"]:
        out["suggested"]["thresholds_message"] = (
            f"Per proporre le soglie servono almeno {cal['min_class_cards']} carte approvate e {cal['min_class_cards']} "
            f"non approvate dai creator (ora {pos} e {neg}).")
    else:
        lo, hi = ref["meta"] - cal["max_threshold_shift"], ref["meta"] + cal["max_threshold_shift"]
        cand = sorted({r["base"] for r in rows})
        mids = [(a + b) / 2 for a, b in zip(cand, cand[1:])]
        tries = {round(min(hi, max(lo, t)), 1) for t in mids} | {float(ref["meta"])}
        best = max(sorted(tries), key=lambda t: (_confusion(rows, t, cal["pro_meta_at"])["balanced_accuracy"], -abs(t - ref["meta"])))
        th = build_thresholds(cfg, ref, best, cal["max_threshold_shift"])
        if th is None:
            out["suggested"]["thresholds_message"] = "Le soglie calcolate non sarebbero coerenti: non propongo modifiche."
        else:
            out["suggested"].update(thresholds=th, after=_confusion(rows, th["meta"], cal["pro_meta_at"]))
    # pesi per ruolo: correlazione di ogni stat col giudizio dei pro, attenuata dal numero di carte (poco dato = poco cambio)
    base_w = _patch()["role_weights"]
    drift = cal["max_weight_drift"]
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
            ref_w = base_w.get(role, {}).get(stat, old)  # si parte da patch.json, mai dai pesi gia' calibrati
            new = round(min(ref_w * (1 + drift), max(ref_w * (1 - drift), ref_w * (1 + shrink * c))), 2)
            changes.append({"stat": stat, "old": old, "new": new, "corr": round(c, 2)})
        if changes:
            out["suggested"]["weights"][role] = {"n": len(sub), "changes": sorted(changes, key=lambda x: -abs(x["corr"]))}
    out["rules"] = rules_calibration(rows, cfg)
    return out


def apply(conn, cfg: dict, thresholds: bool, weights: bool, rules_too: bool = False) -> dict:
    rep = report(conn, cfg)
    if not rep["ready"]:
        raise ValueError(rep["message"])
    path = scoring.local_config_path()
    local = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if thresholds and rep["suggested"]["thresholds"]:
        local["meta"] = rep["suggested"]["thresholds"]
    if weights:
        rw = local.setdefault("role_weights", {})
        for role, info in rep["suggested"]["weights"].items():
            new = {c["stat"]: c["new"] for c in info["changes"]}
            rw[role] = {**_patch()["role_weights"][role], **new}  # le stat senza dati tornano a patch.json
    if rules_too:
        ov = local.setdefault("rules", {}).setdefault("overrides", {})
        for ch in rep["rules"]["changes"]:
            o = ov.setdefault(ch["id"], {})
            o["effect"] = {"score": ch["delta"]["new"]}
            if ch["threshold"]:
                o["when"] = {ch["threshold"]["field"]: ch["threshold"]["new"]}
    base = json.loads(scoring.CONFIG_PATH.read_text(encoding="utf-8"))
    scoring.validate_config(scoring._merge(base, local))  # se non è valida non si scrive nulla
    path.write_text(json.dumps(local, indent=1), encoding="utf-8")
    scoring.load_config.cache_clear()
    return report(conn, scoring.load_config())


def reset() -> None:
    scoring.local_config_path().unlink(missing_ok=True)
    scoring.load_config.cache_clear()
