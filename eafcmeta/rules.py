"""Motore di regole dichiarative: il giudizio su una carta esce da `config/rules.json`, non da costanti nel codice.

- `evaluate(card, explain, cfg)`: unico valutatore. Applica le regole *active* e produce testi pro/contro, tag,
  contributo allo score (con tetto per regola e totale) e l'elenco di "cosa ha contato".
- Le regole *proposed* (apprese dai pro) non influenzano mai lo score né i testi finché l'utente non le approva.
- `learn(conn, cfg)`: aggrega la tabella `criteria` (criterio/polarità per ruolo e creator) e PROPONE regole.
- `criteria_disagreements(conn, role)`: dove i creator si contraddicono su un criterio.

Stato dell'utente (regole apprese, approvazioni) in `rules.local.json`, accanto a `local.json` (ignorato da git).
Gli aggiustamenti di soglie/delta fatti dalla calibrazione stanno in `local.json` sotto la chiave "rules".
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
RULES_PATH = CONFIG_DIR / "rules.json"
CRITERIA_PATH = CONFIG_DIR / "criteria.json"

COMPARATORS = ("gte", "gt", "lte", "lt")
STATUSES = ("active", "proposed", "rejected")
SOURCES = ("manual", "learned")
GROUPS = ("in_role", "off_role", "unknown")
THRESHOLD_TYPES = ("stat", "avg", "role_top_stats", "role_weak_stats", "height_cm", "weight_kg")
# tipo di condizione -> (campi obbligatori, ha un confronto numerico)
WHEN_TYPES = {
    "stat": (("stat",), True), "avg": (("stats",), True),
    "role_top_stats": (("top_n", "max_items"), True), "role_weak_stats": (("min_weight", "max_items"), True),
    "playstyle": (("name",), False), "playstyle_group": (("group",), False), "body_type": (("in",), False),
    "skill_moves": ((), True), "weak_foot": ((), True), "height_cm": ((), True), "weight_kg": ((), True),
    "price": ((), True),
}
_DUMMY = {"items": "x", "value": 1.0, "avg": 1.0, "role_it": "x", "name": "x", "body_type": "x", "stars": 1, "role": "ST"}

_cache: dict = {}


def clear_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------- caricamento e validazione

def _mtime(path: Path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def load_criteria() -> dict:
    """Vocabolario condiviso dei criteri (config/criteria.json): si usano SOLO queste chiavi."""
    key = ("criteria", _mtime(CRITERIA_PATH))
    if key not in _cache:
        _cache[key] = json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))["criteria"]
    return _cache[key]


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_text(text, where: str) -> None:
    if text is None:
        return
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"rules.json: {where}: il testo deve essere una stringa non vuota o null")
    try:
        text.format(**_DUMMY)
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"rules.json: {where}: segnaposto non valido nel testo ({e})")


def validate_when(when, where: str) -> None:
    if not isinstance(when, dict) or when.get("type") not in WHEN_TYPES:
        raise ValueError(f"rules.json: {where}: condizione 'when.type' sconosciuta (ammessi: {', '.join(WHEN_TYPES)})")
    required, has_cmp = WHEN_TYPES[when["type"]]
    for f in required:
        if f not in when:
            raise ValueError(f"rules.json: {where}: nella condizione '{when['type']}' manca '{f}'")
    cmps = [c for c in COMPARATORS if c in when]
    if has_cmp and not cmps:
        raise ValueError(f"rules.json: {where}: serve almeno un confronto tra {', '.join(COMPARATORS)}")
    if any(not _num(when[c]) for c in cmps):
        raise ValueError(f"rules.json: {where}: i confronti devono essere numeri")
    t = when["type"]
    if t == "stat" and not isinstance(when["stat"], str):
        raise ValueError(f"rules.json: {where}: 'stat' deve essere un testo")
    if t == "avg" and (not isinstance(when["stats"], list) or not when["stats"]):
        raise ValueError(f"rules.json: {where}: 'stats' deve essere una lista non vuota")
    if t in ("role_top_stats", "role_weak_stats"):
        for f in required:
            if not _num(when[f]) or when[f] <= 0:
                raise ValueError(f"rules.json: {where}: '{f}' deve essere un numero > 0")
    if t == "playstyle_group" and when["group"] not in GROUPS:
        raise ValueError(f"rules.json: {where}: group deve essere uno tra {', '.join(GROUPS)}")
    if t == "body_type" and (not isinstance(when["in"], list) or not when["in"]):
        raise ValueError(f"rules.json: {where}: 'in' deve essere una lista non vuota")


def validate_rule(rule, settings: dict, crit_keys, where: str) -> None:
    if not isinstance(rule, dict):
        raise ValueError(f"rules.json: {where}: la regola deve essere un oggetto")
    rid = rule.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError(f"rules.json: {where}: manca 'id'")
    where = f"regola '{rid}'"
    roles, roles_except = rule.get("roles"), rule.get("roles_except")
    if (roles is None) == (roles_except is None):
        raise ValueError(f"rules.json: {where}: serve esattamente uno tra 'roles' e 'roles_except'")
    for name, lst in (("roles", roles), ("roles_except", roles_except)):
        if lst is not None and (not isinstance(lst, list) or not all(isinstance(r, str) for r in lst) or
                                (name == "roles" and not lst)):
            raise ValueError(f"rules.json: {where}: '{name}' non valido")
    validate_when(rule.get("when"), where)
    eff = rule.get("effect")
    if not isinstance(eff, dict) or not _num(eff.get("score", 0)):
        raise ValueError(f"rules.json: {where}: 'effect.score' deve essere un numero")
    if abs(eff.get("score", 0)) > settings["max_rule_delta"] + 1e-9:
        raise ValueError(f"rules.json: {where}: |effect.score| supera max_rule_delta ({settings['max_rule_delta']})")
    if "tag" in eff and not isinstance(eff["tag"], str):
        raise ValueError(f"rules.json: {where}: 'effect.tag' deve essere un testo")
    _check_text(rule.get("pro_text"), where + " pro_text")
    _check_text(rule.get("con_text"), where + " con_text")
    if rule.get("source") not in SOURCES:
        raise ValueError(f"rules.json: {where}: source deve essere 'manual' o 'learned'")
    if rule.get("status") not in STATUSES:
        raise ValueError(f"rules.json: {where}: status deve essere active, proposed o rejected")
    if not isinstance(rule.get("evidence", {}), dict):
        raise ValueError(f"rules.json: {where}: 'evidence' deve essere un oggetto")
    if "criterion" in rule and rule["criterion"] not in crit_keys:
        raise ValueError(f"rules.json: {where}: criterio '{rule['criterion']}' non presente in criteria.json")


def validate_settings(s) -> None:
    if not isinstance(s, dict):
        raise ValueError("rules.json: manca 'settings'")
    for k in ("max_rule_delta", "max_total_delta"):
        if not _num(s.get(k)) or s[k] <= 0:
            raise ValueError(f"rules.json: settings.{k} deve essere un numero > 0")
    ln = s.get("learn")
    if not isinstance(ln, dict):
        raise ValueError("rules.json: manca 'settings.learn'")
    for k in ("min_mentions", "min_creators", "min_consensus", "per_mention", "max_new_delta", "max_step",
              "positive_gte", "negative_lt"):
        if not _num(ln.get(k)) or ln[k] <= 0:
            raise ValueError(f"rules.json: settings.learn.{k} deve essere un numero > 0")
    if ln["min_consensus"] > 1 or ln["max_new_delta"] > s["max_rule_delta"]:
        raise ValueError("rules.json: settings.learn: min_consensus <= 1 e max_new_delta <= max_rule_delta")
    crit = load_criteria()
    for crit_key, tpl in (ln.get("templates") or {}).items():
        if crit_key not in crit:
            raise ValueError(f"rules.json: settings.learn.templates: criterio '{crit_key}' non presente in criteria.json")
        for side in ("pos", "neg"):
            if tpl.get(side) is not None:
                validate_when(tpl[side], f"template '{crit_key}.{side}'")
    cal = s.get("calibration")
    if not isinstance(cal, dict) or any(not _num(cal.get(k)) or cal[k] <= 0 for k in
                                        ("min_cards_rule", "min_diff", "delta_step", "threshold_step")):
        raise ValueError("rules.json: settings.calibration non valido (min_cards_rule, min_diff, delta_step, threshold_step)")


def validate_advice(advice) -> None:
    if not isinstance(advice, list) or not advice:
        raise ValueError("rules.json: manca 'advice'")
    for i, a in enumerate(advice):
        ok = (isinstance(a, dict) and isinstance(a.get("levels"), list) and a["levels"] and
              set(a["levels"]) <= {"top", "meta", "playable", "below"} and
              a.get("verdict") in ("MUST_DO", "AVOID", "NEUTRAL", "*") and isinstance(a.get("text"), str))
        if not ok:
            raise ValueError(f"rules.json: advice[{i}] non valido")
    for lv in ("top", "meta", "playable", "below"):  # ogni livello deve avere almeno un consiglio generico
        if not any(lv in a["levels"] and a["verdict"] == "*" and "no_market" not in a for a in advice):
            raise ValueError(f"rules.json: advice senza consiglio generico per il livello '{lv}'")


def validate_file(data) -> None:
    """Controllo strutturale di rules.json (non dipende dalla config del motore)."""
    if not isinstance(data, dict):
        raise ValueError("rules.json: deve essere un oggetto JSON")
    validate_settings(data.get("settings"))
    if not isinstance(data.get("rules"), list):
        raise ValueError("rules.json: manca 'rules'")
    crit_keys = set(load_criteria())
    seen = set()
    for i, r in enumerate(data["rules"]):
        validate_rule(r, data["settings"], crit_keys, f"rules[{i}]")
        if r["id"] in seen:
            raise ValueError(f"rules.json: id duplicato '{r['id']}'")
        seen.add(r["id"])
    validate_advice(data.get("advice"))


def load_base() -> dict:
    """Legge e valida rules.json. Errore chiaro (ValueError) se è rotto."""
    key = ("base", str(RULES_PATH), _mtime(RULES_PATH))
    if key not in _cache:
        try:
            data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(f"rules.json: file mancante ({RULES_PATH})")
        except json.JSONDecodeError as e:
            raise ValueError(f"rules.json: JSON non valido ({e})")
        validate_file(data)
        _cache[key] = data
    return _cache[key]


def state_path() -> Path:
    from . import scoring
    return scoring.local_config_path().with_name("rules.local.json")


def _load_state() -> dict:
    p = state_path()
    key = ("state", str(p), _mtime(p))
    if key not in _cache:
        state = {}
        if p.exists():
            try:
                state = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("deve essere un oggetto")
            except (ValueError, OSError) as e:
                print(f"[rules] stato regole ignorato ({e})", file=sys.stderr)
                state = {}
        _cache[key] = {"learned": state.get("learned", []), "status": state.get("status", {})}
    return _cache[key]


def _save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    clear_cache()


def _overlay(cfg: dict | None) -> dict:
    ov = (cfg or {}).get("rules") or {}
    if not isinstance(ov, dict) or not isinstance(ov.get("overrides", {}), dict):
        raise ValueError("config: 'rules.overrides' deve essere un oggetto")
    return ov.get("overrides", {})


def resolve(cfg: dict | None = None) -> dict:
    """Regole effettive = rules.json + regole apprese e approvazioni (stato) + ritocchi della calibrazione."""
    if cfg is None:
        from . import scoring
        cfg = scoring.load_config()
    base = load_base()
    state = _load_state()
    overrides = _overlay(cfg)
    key = ("resolved", str(RULES_PATH), _mtime(RULES_PATH), str(state_path()), _mtime(state_path()),
           json.dumps(overrides, sort_keys=True))
    if key not in _cache:
        rules = copy.deepcopy(base["rules"])
        ids = {r["id"] for r in rules}
        for lr in copy.deepcopy(state["learned"]):
            if isinstance(lr, dict) and lr.get("id") not in ids:
                rules.append(lr)
                ids.add(lr["id"])
        for r in rules:
            r.setdefault("evidence", {})
            r["status"] = state["status"].get(r["id"], r["status"])
            ov = overrides.get(r["id"])
            if isinstance(ov, dict):
                for part in ("effect", "when"):
                    if isinstance(ov.get(part), dict):
                        r[part] = {**r[part], **ov[part]}
        _cache[key] = {"settings": base["settings"], "rules": rules, "advice": base["advice"]}
    return _cache[key]


def validate_config(cfg: dict) -> None:
    """Chiamata da scoring.validate_config: rules.json + stato + ritocchi, coerenti con ruoli e stat del motore."""
    res = resolve(cfg)
    crit_keys = set(load_criteria())
    roles, stat_keys = set(cfg["role_weights"]), set(cfg["stat_keys"])
    seen = set()
    for r in res["rules"]:
        validate_rule(r, res["settings"], crit_keys, "regola")
        rid = r["id"]
        if rid in seen:
            raise ValueError(f"rules.json: id duplicato '{rid}'")
        seen.add(rid)
        bad = [x for x in (r.get("roles") or r.get("roles_except") or []) if x not in roles]
        if bad:
            raise ValueError(f"rules.json: regola '{rid}': ruoli sconosciuti {bad}")
        w = r["when"]
        used = ([w["stat"]] if w["type"] == "stat" else list(w.get("stats", [])) if w["type"] == "avg" else [])
        bad = [s for s in used if s not in stat_keys]
        if bad:
            raise ValueError(f"rules.json: regola '{rid}': stat sconosciute {bad}")
        if w["type"] == "body_type":
            bad = [b for b in w["in"] if b not in cfg["body_type_bonus"]]
            if bad:
                raise ValueError(f"rules.json: regola '{rid}': body type sconosciuti {bad}")


# ---------------------------------------------------------------- valutazione

def _cmp(when: dict, v: float) -> bool:
    return ((("gte" not in when) or v >= when["gte"]) and (("gt" not in when) or v > when["gt"]) and
            (("lte" not in when) or v <= when["lte"]) and (("lt" not in when) or v < when["lt"]))


def make_ctx(card: dict, role: str, cfg: dict, unknown_playstyles=()) -> dict:
    return {"card": card, "role": role, "cfg": cfg, "stats": card.get("stats", {}),
            "weights": cfg["role_weights"][role], "unknown": list(unknown_playstyles)}


def _names() -> tuple[dict, dict]:
    from . import analysis  # import tardivo: analysis importa questo modulo
    return analysis.NAMES, analysis.ROLE_IT


def _items(ctx: dict, keys: list[str]) -> str:
    names, _ = _names()
    return ", ".join(f"{names.get(k, k)} {ctx['stats'][k]}" for k in keys)


def match(when: dict, ctx: dict) -> dict | None:
    """None se la condizione non vale, altrimenti i valori per i segnaposto dei testi. Dati mancanti = non vale."""
    t, card, stats = when["type"], ctx["card"], ctx["stats"]
    if t == "stat":
        v = stats.get(when["stat"])
        return {"value": v, "items": _items(ctx, [when["stat"]])} if v is not None and _cmp(when, v) else None
    if t == "avg":
        have = [stats[k] for k in when["stats"] if k in stats]
        if not have:
            return None
        v = sum(have) / len(have)
        return {"value": v, "avg": v} if _cmp(when, v) else None
    if t in ("role_top_stats", "role_weak_stats"):
        w = ctx["weights"]
        if t == "role_top_stats":
            order = sorted(w, key=lambda k: (-w[k], -stats.get(k, 0)))[:int(when["top_n"])]
            keys = [k for k in order if k in stats and _cmp(when, stats[k])]
        else:
            keys = sorted((k for k in w if w[k] >= when["min_weight"] and _cmp(when, stats.get(k, 0))),
                          key=lambda k: stats.get(k, 0))
            keys = [k for k in keys if k in stats]
        keys = keys[:int(when["max_items"])]
        return {"items": _items(ctx, keys), "value": stats[keys[0]]} if keys else None
    if t in ("playstyle", "playstyle_group"):
        cfg, role = ctx["cfg"], ctx["role"]
        ps_cfg = cfg["playstyles"]
        if t == "playstyle":
            return {"name": when["name"], "items": when["name"]} if when["name"] in card.get("playstyles", []) else None
        grp = []
        if when["group"] == "unknown":
            grp = list(ctx["unknown"])
        else:
            for ps in card.get("playstyles", []):
                info = ps_cfg.get(ps)
                if ps.endswith("+") and info and ((role in info["roles"]) == (when["group"] == "in_role")):
                    grp.append(f"{ps} (tier {info['tier']})")
        return {"items": ", ".join(grp)} if grp else None
    if t == "body_type":
        bt = card.get("body_type")
        return {"body_type": bt, "items": bt} if bt in when["in"] else None
    if t in ("skill_moves", "weak_foot"):
        v = card.get(t, when.get("default", 3))
        return {"value": v, "stars": v} if _cmp(when, v) else None
    if t in ("height_cm", "weight_kg"):
        v = card.get(t)
        return {"value": v} if _num(v) and _cmp(when, v) else None
    if t == "price":
        v = card.get("price")
        return {"value": v} if _num(v) and _cmp(when, v) else None
    return None


def applies_to(rule: dict, role: str) -> bool:
    if "roles" in rule:
        return role in rule["roles"]
    return role not in rule.get("roles_except", [])


def _fmt(text: str | None, vals: dict, role: str) -> str | None:
    if not text:
        return None
    _, role_it = _names()
    return text.format(**{**_DUMMY, **vals, "role_it": role_it.get(role, role), "role": role})


def evaluate(card: dict, explain: dict, cfg: dict | None = None) -> dict:
    """Valuta le regole attive su una carta. `explain` deve contenere almeno `role` (e `unknown_playstyles`).

    Ritorna {score_delta (con tetto), score_delta_raw, capped, reasons_pro, reasons_con, tags, contributions, pending}.
    `pending` elenca le regole proposte che scatterebbero: informative, senza effetto su score e testi.
    """
    if cfg is None:
        from . import scoring
        cfg = scoring.load_config()
    res = resolve(cfg)
    role = explain["role"]
    ctx = make_ctx(card, role, cfg, explain.get("unknown_playstyles", ()))
    pro, con, tags, contribs, pending, raw = [], [], [], [], [], 0.0
    for rule in res["rules"]:
        if rule["status"] == "rejected" or not applies_to(rule, role):
            continue
        vals = match(rule["when"], ctx)
        if vals is None:
            continue
        delta = float(rule["effect"].get("score", 0))
        tag = rule["effect"].get("tag") or rule.get("criterion")
        p, c = _fmt(rule.get("pro_text"), vals, role), _fmt(rule.get("con_text"), vals, role)
        if rule["status"] == "proposed":
            pending.append({"id": rule["id"], "delta": delta, "tag": tag, "text": p or c})
            continue
        raw += delta
        if p:
            pro.append(p)
        if c:
            con.append(c)
        if tag and tag not in tags:
            tags.append(tag)
        contribs.append({"id": rule["id"], "tag": tag, "kind": "pro" if p and not c else "con" if c and not p else "info",
                         "text": p or c, "delta": round(delta, 3), "source": rule["source"],
                         "evidence": rule.get("evidence") or {}})
    cap = res["settings"]["max_total_delta"]
    delta = max(-cap, min(cap, raw))
    return {"score_delta": round(delta, 3), "score_delta_raw": round(raw, 3), "capped": abs(raw) > cap + 1e-9,
            "max_total_delta": cap, "reasons_pro": pro, "reasons_con": con, "tags": tags,
            "contributions": contribs, "pending": pending}


def advice(level: str, verdict: str, no_market: bool, cfg: dict | None = None) -> str:
    """Consiglio finale (matrice livello x verdetto) dalle regole 'advice'; vince la prima voce che combacia."""
    for a in resolve(cfg)["advice"]:
        if level in a["levels"] and a["verdict"] in (verdict, "*") and a.get("no_market", no_market) == no_market:
            return a["text"]
    return ""


# ---------------------------------------------------------------- stato: elenco, approvazione

def list_rules(cfg: dict | None = None) -> list[dict]:
    return copy.deepcopy(resolve(cfg)["rules"])


def set_status(rule_id: str, status: str, cfg: dict | None = None) -> dict:
    """Approva (active) o rifiuta (rejected) una regola in stato 'proposed'. Altrimenti ValueError / KeyError."""
    if status not in ("active", "rejected"):
        raise ValueError("lo stato può essere solo 'active' o 'rejected'")
    rule = next((r for r in resolve(cfg)["rules"] if r["id"] == rule_id), None)
    if rule is None:
        raise KeyError(rule_id)
    if rule["status"] != "proposed":
        raise ValueError(f"la regola '{rule_id}' è '{rule['status']}': si possono decidere solo le regole proposte")
    state = copy.deepcopy(_load_state())
    state["status"][rule_id] = status
    _save_state(state)
    return next(r for r in resolve(cfg)["rules"] if r["id"] == rule_id)


# ---------------------------------------------------------------- criteri dei pro: lettura, apprendimento, discordanze

def _norm_role(role, cfg: dict) -> str | None:
    r = str(role or "").strip().upper()
    if r in cfg["role_weights"]:
        return r
    return cfg["position_to_role"].get(r)


def criteria_rows(conn, cfg: dict) -> list[dict] | None:
    """Righe valide della tabella `criteria`; None se la tabella non esiste (non è un errore)."""
    try:
        rows = conn.execute("SELECT creator, role, criterion, polarity FROM criteria").fetchall()
    except sqlite3.OperationalError:
        return None
    crit, out = load_criteria(), []
    for creator, role, criterion, pol in (tuple(r) for r in rows):
        role = _norm_role(role, cfg)
        if role and criterion in crit and pol in (1, -1) and creator:
            out.append({"creator": str(creator), "role": role, "criterion": criterion, "polarity": int(pol)})
    return out


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


def criteria_disagreements(conn, role: str, cfg: dict | None = None) -> list[dict]:
    """Per il ruolo: criteri su cui i creator hanno polarità opposte, con frase in italiano e nomi reali."""
    if cfg is None:
        from . import scoring
        cfg = scoring.load_config()
    rows = criteria_rows(conn, cfg)
    if not rows:
        return []
    crit, out = load_criteria(), []
    for key, info in crit.items():
        net: dict[str, list] = {}
        for r in rows:
            if r["role"] == role and r["criterion"] == key:
                ent = net.setdefault(r["creator"].lower(), [r["creator"], 0])
                ent[1] += r["polarity"]
        pos = [n for n, v in net.values() if v > 0]
        neg = [n for n, v in net.values() if v < 0]
        if pos and neg:
            text = (f"{_join(pos)} {'premia' if len(pos) == 1 else 'premiano'} {info['it']}, "
                    f"{_join(neg)} {'lo ritiene' if len(neg) == 1 else 'lo ritengono'} negativo.")
            out.append({"criterion": key, "role": role, "positive": pos, "negative": neg, "text": text})
    return out


def _learn_when(key: str, side: str, ln: dict) -> dict | None:
    tpl = (ln.get("templates") or {}).get(key)
    if tpl is not None:
        return copy.deepcopy(tpl.get(side))
    stats = load_criteria()[key]["stats"]
    if not stats:
        return None
    return ({"type": "avg", "stats": list(stats), "gte": ln["positive_gte"]} if side == "pos"
            else {"type": "avg", "stats": list(stats), "lt": ln["negative_lt"]})


def learn(conn, cfg: dict | None = None) -> dict:
    """Aggrega `criteria` per (ruolo, criterio) e PROPONE regole (status 'proposed'); non attiva mai nulla.

    Per ogni gruppo servono almeno `min_mentions` menzioni da `min_creators` creator, e un consenso di polarità
    >= `min_consensus`. Il delta è limitato (`max_new_delta`); una proposta già presente cambia al massimo di `max_step`
    per esecuzione. Le regole già approvate o rifiutate non vengono toccate.
    """
    if cfg is None:
        from . import scoring
        cfg = scoring.load_config()
    res = resolve(cfg)
    ln = res["settings"]["learn"]
    rows = criteria_rows(conn, cfg)
    out = {"ready": False, "n_rows": 0, "proposals": [], "skipped": [], "message": ""}
    if rows is None:
        out["message"] = "Nessun criterio dei pro disponibile: la tabella 'criteria' non esiste ancora."
        return out
    out["n_rows"] = len(rows)
    groups: dict = {}
    for r in rows:
        g = groups.setdefault((r["role"], r["criterion"]), {"n": 0, "pos": 0, "neg": 0, "creators": {}})
        g["n"] += 1
        g["pos" if r["polarity"] > 0 else "neg"] += 1
        g["creators"].setdefault(r["creator"].lower(), r["creator"])
    names, role_it = _names()
    state = copy.deepcopy(_load_state())
    existing = {r["id"]: r for r in res["rules"]}
    crit = load_criteria()
    for (role, key), g in sorted(groups.items()):
        if g["n"] < ln["min_mentions"] or len(g["creators"]) < ln["min_creators"]:
            out["skipped"].append({"role": role, "criterion": key, "motivo": "dati insufficienti",
                                   "mentions": g["n"], "creators": len(g["creators"])})
            continue
        net = g["pos"] - g["neg"]
        if abs(net) / g["n"] < ln["min_consensus"]:
            out["skipped"].append({"role": role, "criterion": key, "motivo": "i creator non sono concordi",
                                   "mentions": g["n"], "creators": len(g["creators"])})
            continue
        side = "pos" if net > 0 else "neg"
        when = _learn_when(key, side, ln)
        if when is None:
            out["skipped"].append({"role": role, "criterion": key, "motivo": "nessuna condizione associabile"})
            continue
        rid = f"learned-{role.lower()}-{key}-{side}"
        target = (1 if side == "pos" else -1) * min(ln["max_new_delta"], ln["per_mention"] * abs(net),
                                                    res["settings"]["max_rule_delta"])
        evidence = {"mentions": g["n"], "creators": sorted(g["creators"].values()), "positive": g["pos"],
                    "negative": g["neg"], "net": net}
        cur = existing.get(rid)
        if cur is not None and cur["status"] != "proposed":
            continue  # già decisa dall'utente: non si tocca
        if cur is not None:
            prev = cur["effect"]["score"]
            target = max(prev - ln["max_step"], min(prev + ln["max_step"], target))
        label = crit[key]["it"]
        txt = (f"I creator premiano {label} per il ruolo di {role_it.get(role, role)}: questa carta è ai livelli che apprezzano."
               if side == "pos" else
               f"I creator criticano {label} per il ruolo di {role_it.get(role, role)}: questa carta è sotto i livelli accettabili.")
        rule = {"id": rid, "roles": [role], "criterion": key, "when": when,
                "effect": {"score": round(target, 3), "tag": key},
                "pro_text": txt if side == "pos" else None, "con_text": txt if side == "neg" else None,
                "source": "learned", "status": "proposed", "evidence": evidence}
        state["learned"] = [x for x in state["learned"] if x.get("id") != rid] + [rule]
        out["proposals"].append(rule)
    if out["proposals"]:
        for r in out["proposals"]:
            validate_rule(r, res["settings"], set(crit), "proposta")
        _save_state(state)
    out["ready"] = True
    out["message"] = (f"{len(out['proposals'])} regole proposte: approvale o rifiutale." if out["proposals"]
                      else "Dati letti, ma nessuna regola da proporre (servono "
                           f"almeno {ln['min_mentions']} menzioni da {ln['min_creators']} creator concordi).")
    return out
