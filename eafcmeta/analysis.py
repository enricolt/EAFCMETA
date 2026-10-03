"""Analisi scritta di una carta: è meta? Su quali basi prenderla o no. Regole fisse sui dati (nessun LLM)."""
from __future__ import annotations

from . import rules

NAMES = {
    "acceleration": "accelerazione", "sprint_speed": "velocità di punta", "agility": "agilità", "balance": "equilibrio",
    "reactions": "reattività", "ball_control": "controllo palla", "dribbling": "dribbling", "composure": "freddezza",
    "positioning": "smarcamento", "finishing": "finalizzazione", "shot_power": "potenza di tiro", "long_shots": "tiri da fuori",
    "vision": "visione", "crossing": "cross", "short_passing": "passaggi corti", "long_passing": "passaggi lunghi",
    "heading_accuracy": "colpo di testa", "defensive_awareness": "lettura difensiva", "standing_tackle": "contrasto",
    "sliding_tackle": "scivolata", "interceptions": "intercetti", "aggression": "aggressività", "jumping": "elevazione",
    "stamina": "resistenza", "strength": "forza", "gk_diving": "tuffo", "gk_handling": "presa", "gk_kicking": "rinvio",
    "gk_positioning": "piazzamento", "gk_reflexes": "riflessi", "volleys": "colpi al volo", "curve": "effetto",
    "fk_accuracy": "punizioni", "penalties": "rigori"}
ROLE_IT = {"ST": "attaccante", "CF": "seconda punta", "W": "esterno", "CAM": "trequartista", "CM": "centrocampista",
           "CDM": "mediano", "FB": "terzino", "WB": "esterno basso", "CB": "difensore centrale", "GK": "portiere"}


def meta_level(final: float, cfg: dict) -> tuple[str, str]:
    m = cfg["meta"]
    if final >= m["top"]:
        return "top", "Meta di vertice"
    if final >= m["meta"]:
        return "meta", "Meta"
    if final >= m["playable"]:
        return "playable", "Giocabile, non meta"
    return "below", "Sotto il meta"


STANCE_IT = {"yes": "sì, da prendere", "maybe": "dipende", "no": "no, da evitare"}
STANCE_SHORT = {"yes": "sì", "maybe": "dipende", "no": "no"}


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


def summarize_opinions(opinions: list[dict], level: str) -> dict:
    """Chi dice sì, chi no e perché. Con pareri discordanti lo dice esplicitamente e riporta i motivi."""
    if not opinions:
        return {"status": "none", "summary": "Nessun parere dei creator inserito.", "groups": [], "hint": ""}
    groups = []
    for st in ("yes", "maybe", "no"):
        members = [o for o in opinions if o["stance"] == st]
        if members:
            groups.append({"stance": st, "label": STANCE_IT[st], "creators": [
                {"name": o["creator"], "score": o["score"], "url": o["url"],
                 "reason": o["reason"] or "motivo non indicato"} for o in members]})
    status = "disagree" if len(groups) > 1 else "agree"
    parts = "; ".join(f"secondo {_join([c['name'] for c in g['creators']])} {STANCE_SHORT[g['stance']]}" for g in groups)
    summary = ("Pareri discordanti: " if status == "disagree" else "Pareri concordi: ") + parts + "."
    yes = sum(o["stance"] == "yes" for o in opinions)
    no = sum(o["stance"] == "no" for o in opinions)
    hint = ""
    if level in ("top", "meta") and no > yes:
        hint = ("Le statistiche la danno forte ma i creator la bocciano: di solito pesano aspetti che i numeri non "
                "mostrano (animazioni, reattività, sensazione di gioco). Leggi i loro motivi qui sotto.")
    elif level in ("below", "playable") and yes > no:
        hint = ("I creator la promuovono nonostante statistiche non da vertice: spesso contano PlayStyle, animazioni "
                "o un ruolo specifico che il punteggio cattura solo in parte.")
    elif status == "disagree":
        hint = "I motivi di ciascuno sono sotto: confrontali con come giochi tu (ruolo, modulo, ritmo)."
    return {"status": status, "summary": summary, "groups": groups, "hint": hint}


def _num(x) -> float | None:
    """Numero finito oppure None: dati vecchi o sporchi (testo) non devono mai far fallire la pagina."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def site_signals(card: dict) -> list[str]:
    sig, out = card.get("signals") or {}, []
    if _num(sig.get("gg_rating")) is not None:
        r = f"FUT.GG: GG Rating {_num(sig['gg_rating']):g} come {sig.get('gg_role', '?')}"
        r += f" (n°{_num(sig['gg_rank']):g} nel ruolo)" if _num(sig.get("gg_rank")) is not None else ""
        r += f" — rende meglio come {sig['gg_role']} che come {card['position']}" if sig.get("gg_role") not in (None, card["position"]) else ""
        out.append(r + ".")
    if _num(sig.get("futbin_rating")) is not None:
        r = f"FUTBIN: Rating {_num(sig['futbin_rating']):g} come {sig.get('futbin_role', '?')}"
        r += f" (n°{_num(sig['futbin_rank']):g} nel ruolo)" if _num(sig.get("futbin_rank")) is not None else ""
        out.append(r + ".")
    return out


def describe(card: dict, ex: dict, final: float, verdict: dict, market_size: int, cfg: dict,
             opinions: list[dict] | None = None, criteria_notes: list[dict] | None = None) -> dict:
    role = ex["role"]
    level, label = meta_level(ex["base"], cfg)  # stessa grandezza su cui si calibrano le soglie (vedi calibration.py)
    pros, cons = [], []

    # tutte le regole del giudizio (forza/debolezza, scatto, PlayStyle, body type, skill, piede debole, regole apprese
    # approvate) vengono da rules.json tramite l'unico valutatore rules.evaluate
    rd = ex.get("rules") or rules.evaluate(card, ex, cfg)
    pros += rd["reasons_pro"]
    cons += rd["reasons_con"]

    v = verdict["verdict"]
    if verdict["value_gap"] is None:
        cons.append(f"Prezzo non valutabile con sicurezza: nell'app servono più carte {card['position']} "
                    f"(ora {market_size}) per confrontarlo.")
    elif v == "MUST_DO":
        pros.append(f"Rende più del prezzo: {verdict['reason']}")
    elif v == "AVOID":
        cons.append(f"Costa più di quanto vale rispetto alle carte simili: {verdict['reason']}")
    else:
        pros.append(f"Prezzo in linea con il valore: {verdict['reason']}")

    ops = summarize_opinions(opinions or [], level)  # il confronto è con le sole statistiche
    if opinions:
        pro_avg = card.get("pro_score")
        if pro_avg is not None:
            (pros if pro_avg >= 80 else cons).append(f"Media dei pareri dei creator: {pro_avg:.0f}/100.")
    elif card.get("pro_score") is not None:
        (pros if card["pro_score"] >= 80 else cons).append(f"Parere dei pro: {card['pro_score']:.0f}/100.")
    else:
        cons.append("Manca il parere dei creator: il giudizio si basa solo su statistiche, PlayStyle e prezzo.")

    if opinions and card.get("pro_share") is not None:
        agg = card.get("pro_agg") or {}
        ops["influence"] = (f"I pareri pesano il {card['pro_share'] * 100:.0f}% dello score finale "
                            f"({len(opinions)} parer{'e' if len(opinions) == 1 else 'i'}; peso complessivo {agg.get('n_eff', 0):g}): "
                            "più creator e più recenti = più peso.")
    advice = rules.advice(level, v, verdict["value_gap"] is None, cfg)
    if ops["status"] == "disagree":
        advice = "Opinioni divise tra i creator. " + advice
    shown = f"{ex['base']:.0f}"  # coerente con l'etichetta, calcolata sullo score base
    headline = f"{label} — score {shown} come {ROLE_IT[role]}" + (
        {"top": ": tra le migliori del suo ruolo.", "meta": ": competitiva ai livelli alti.",
         "playable": ": usabile ma non decisiva.", "below": ": sotto il livello che serve ai livelli alti."}[level])
    return {"meta_level": level, "meta_label": label, "headline": headline, "pros": pros, "cons": cons, "advice": advice,
            "opinions": ops, "site_signals": site_signals(card),
            "contributions": rd["contributions"], "rules_score_delta": rd["score_delta"],
            "rules_score_delta_raw": rd["score_delta_raw"], "rules_capped": rd["capped"],
            "rules_pending": rd["pending"], "criteria_disagreements": criteria_notes or []}

