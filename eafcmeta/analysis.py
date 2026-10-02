"""Analisi scritta di una carta: è meta? Su quali basi prenderla o no. Regole fisse sui dati (nessun LLM)."""
from __future__ import annotations

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
MOVERS = {"ST", "CF", "W", "CAM", "FB", "WB", "CB"}  # ruoli in cui scatto e velocità fanno la differenza


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


def describe(card: dict, ex: dict, final: float, verdict: dict, market_size: int, cfg: dict,
             opinions: list[dict] | None = None) -> dict:
    role = ex["role"]
    weights = cfg["role_weights"][role]
    stats = card["stats"]
    level, label = meta_level(final, cfg)
    pros, cons = [], []

    top = sorted(weights, key=lambda k: (-weights[k], -stats.get(k, 0)))
    strong = [k for k in top[:6] if stats.get(k, 0) >= 88][:4]
    if strong:
        pros.append("Punti di forza per il ruolo: " + ", ".join(f"{NAMES[k]} {stats[k]}" for k in strong) + ".")
    weak = sorted((k for k in weights if weights[k] >= 2 and stats.get(k, 0) < 70), key=lambda k: stats[k])[:3]
    if weak:
        cons.append("Punti deboli che contano per il ruolo: " + ", ".join(f"{NAMES[k]} {stats[k]}" for k in weak) + ".")

    if role in MOVERS:
        pace = (stats.get("acceleration", 0) + stats.get("sprint_speed", 0)) / 2
        if pace >= 88:
            pros.append(f"Scatto e velocità da vertice (media {pace:.0f}): nel meta attuale fanno spesso la differenza.")
        elif pace < 72 and role in ("ST", "W", "FB", "WB", "CF"):
            cons.append(f"Scatto e velocità bassi (media {pace:.0f}) per un {ROLE_IT[role]}: soffrirà contro avversari rapidi.")
        elif pace < 66 and role == "CB":
            cons.append(f"Difensore lento (velocità media {pace:.0f}): rischioso contro attaccanti veloci.")

    plus_ok, plus_off = [], []
    for ps in card.get("playstyles", []):
        info = cfg["playstyles"].get(ps)
        if ps.endswith("+") and info:
            (plus_ok if role in info["roles"] else plus_off).append(f"{ps} (tier {info['tier']})")
    if plus_ok:
        pros.append("PlayStyle+ utili al ruolo: " + ", ".join(plus_ok) + ".")
    if plus_off:
        cons.append("PlayStyle+ poco utili a questo ruolo (contano meno): " + ", ".join(plus_off) + ".")
    if ex["unknown_playstyles"]:
        cons.append("PlayStyle+ non in tabella (bonus minimo): " + ", ".join(ex["unknown_playstyles"]) + ".")

    if card.get("body_type") in ("Lean", "Unique", "Custom") and role != "GK":
        pros.append(f"Body type {card['body_type']}: animazioni più reattive.")
    if role in ("ST", "CF", "W", "CAM"):
        if card.get("skill_moves", 3) >= 5:
            pros.append("5 stelle di skill: dribbling e finte complete.")
        elif card.get("skill_moves", 3) <= 2:
            cons.append(f"Solo {card['skill_moves']}★ di skill moves: poche finte per un ruolo offensivo.")
        if card.get("weak_foot", 3) >= 5:
            pros.append("5★ di piede debole: letale con entrambi i piedi.")
        elif card.get("weak_foot", 3) <= 2:
            cons.append(f"Solo {card['weak_foot']}★ di piede debole: prevedibile nelle conclusioni.")

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

    ops = summarize_opinions(opinions or [], meta_level(ex["base"], cfg)[0])  # il confronto è con le sole statistiche
    if opinions:
        pro_avg = card.get("pro_score")
        if pro_avg is not None:
            (pros if pro_avg >= 80 else cons).append(f"Media dei pareri dei creator: {pro_avg:.0f}/100.")
    elif card.get("pro_score") is not None:
        (pros if card["pro_score"] >= 80 else cons).append(f"Parere dei pro: {card['pro_score']:.0f}/100.")
    else:
        cons.append("Manca il parere dei creator: il giudizio si basa solo su statistiche, PlayStyle e prezzo.")

    advice = _advice(level, v, verdict["value_gap"] is None, card)
    if ops["status"] == "disagree":
        advice = "Opinioni divise tra i creator. " + advice
    shown = f"{final:.0f}"
    headline = f"{label} — score {shown} come {ROLE_IT[role]}" + (
        {"top": ": tra le migliori del suo ruolo.", "meta": ": competitiva ai livelli alti.",
         "playable": ": usabile ma non decisiva.", "below": ": sotto il livello che serve ai livelli alti."}[level])
    return {"meta_level": level, "meta_label": label, "headline": headline, "pros": pros, "cons": cons, "advice": advice,
            "opinions": ops}


def _advice(level: str, verdict: str, no_market: bool, card: dict) -> str:
    cheap = {"MUST_DO": True}.get(verdict, False)
    if level in ("top", "meta"):
        if verdict == "MUST_DO":
            return "Da prendere: è forte e costa meno di quanto vale."
        if verdict == "AVOID":
            return "Forte ma cara per quello che offre: valuta alternative più economiche prima di spendere."
        return ("Buona carta: prendila se ti serve il ruolo" +
                (" (confronta il prezzo con altre carte quando ne avrai inserite di più)." if no_market else "."))
    if level == "playable":
        if cheap:
            return "Non è meta, ma per il prezzo è un buon acquisto di ripiego."
        if verdict == "AVOID":
            return "Da evitare: non è meta e costa troppo."
        return "Opzionale: giocabile ma non fa la differenza; prendila solo se la trovi a poco."
    return ("Da evitare per giocare ai livelli alti: " + ("regge solo come riempitivo molto economico." if cheap
            else "non è competitiva e il prezzo non la giustifica."))
