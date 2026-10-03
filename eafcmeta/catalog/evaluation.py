"""Cache delle valutazioni (tabella `evaluations`): una riga per carta, calcolata con gli STESSI passi di api._scored/_eval.

Passi: score base (scoring.explain: stats, bonus, regole attive) -> parere dei creator (opinion_model) -> score finale ->
verdetto sul prezzo contro la curva Theil-Sen della posizione -> etichetta meta (analysis.meta_level) -> riga di sintesi.

Prestazioni: la curva prezzo/score e' calcolata UNA volta per posizione (non per carta). Con piu' di `curve_max_points`
punti (default 300) la curva usa un SOTTOCAMPIONE deterministico (punti ordinati per prezzo e presi a distanza regolare):
sotto soglia il calcolo e' identico a quello esatto di prima. I mercati piccoli (<= verdict.exact_market_max altre carte)
restano "esatti": curva per carta senza la carta stessa, come in api.py.

Invalidazione: trigger SQL (vedi schema.py) segnano obsoleta (config_hash = '') la valutazione di una carta quando cambiano
la carta (nome, versione, posizione, prezzo, dati, voto manuale) o i suoi pareri; `config_hash` cambia se cambiano patch.json,
calibrazione locale, regole (con stato e ritocchi) o i parametri del catalogo. La LETTURA non ricalcola mai tutto:
`ensure_fresh` ricalcola solo le righe obsolete o mancanti (una volta, in modo esplicito); se cambia la configurazione sono tutte.
Limite noto: quando cambia il prezzo di una carta, i verdetti delle altre carte della STESSA posizione sono rifatti sulla
nuova curva (solo il verdetto, non lo score); una carta cancellata non aggiorna la curva finche' la posizione non viene toccata.
"""
from __future__ import annotations

import hashlib
import json
import threading

from .. import analysis, db, opinion_model, rules, scoring
from . import config as catcfg
from .schema import now_iso

EVAL_VERSION = 1  # alzarlo se cambia la logica del modulo (invalida tutta la cache)
SUMMARY_MAX = 120
_LOCK = threading.RLock()  # un solo ricalcolo alla volta nel processo (gli altri aspettano e trovano la cache fresca)
_CHUNK = 400  # variabili SQL per query


# ------------------------------------------------------------------ configurazione e impronta

def config_hash(cfg: dict | None = None, cat: dict | None = None) -> str:
    """Impronta di TUTTO cio' che influisce sulla valutazione: config effettiva (patch.json + local.json), regole risolte
    (rules.json + stato + ritocchi), parametri del catalogo che toccano il calcolo."""
    cfg = cfg or scoring.load_config()
    cat = cat or catcfg.load()
    blob = json.dumps([EVAL_VERSION, cfg, rules.resolve(cfg), cat["curve_max_points"], cat["summary"]], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


# ------------------------------------------------------------------ curva per posizione

def sample_points(points: list[tuple[float, float]], k: int) -> list[tuple[float, float]]:
    """Sottocampione deterministico di al piu' k punti: ordinati per (prezzo, score), presi a distanza regolare."""
    if len(points) <= k:
        return points
    pts = sorted(points)
    n = len(pts)
    return [pts[(i * (n - 1)) // (k - 1)] for i in range(k)]


class Markets:
    """Mercato per posizione, costruito UNA volta. `items` = (id, posizione, prezzo, score finale).
    Mercato piccolo (<= verdict.exact_market_max altre carte): curva per carta senza la carta stessa (esatta).
    Mercato grande: una curva condivisa per posizione (la carta pesa 1/m), con sottocampionamento oltre `max_points` punti."""

    def __init__(self, items, cfg: dict, max_points: int = 300):
        self.limit, self.max_points = cfg["verdict"]["exact_market_max"], max_points
        self.pts: dict[str, list[tuple[int, float, float]]] = {}
        for i, pos, price, final in items:
            self.pts.setdefault(pos, []).append((i, price, final))
        self._shared: dict[str, tuple[list, tuple | None]] = {}

    def for_card(self, card_id: int, position: str):
        """(mercato [(prezzo, score)] per il verdetto, curva gia' calcolata o scoring._FIT, n. di altre carte)."""
        pts = self.pts[position]
        if len(pts) - 1 <= self.limit:
            return [(p, f) for i, p, f in pts if i != card_id], scoring._FIT, len(pts) - 1
        if position not in self._shared:
            mk = [(p, f) for _, p, f in pts]
            self._shared[position] = (mk, scoring.fit_curve(sample_points([(p, f) for p, f in mk if p > 0], self.max_points)))
        mk, curve = self._shared[position]
        return mk, curve, len(pts) - 1


# ------------------------------------------------------------------ una carta

def score_card(card: dict, ops: list[dict] | None, cfg: dict):
    """(card con pareri aggregati, explain, score finale) oppure None se la carta non e' valutabile con la config attuale.
    Stessi passi di api._scored."""
    if ops:  # con pareri dei creator, il punteggio "pro" e' la loro media
        agg = opinion_model.aggregate(ops, cfg)
        card["pro_score"], card["pro_share"], card["pro_agg"] = agg["pro"], agg["share"], agg
    try:
        ex = scoring.explain(card, cfg)
    except ValueError:
        return None
    return card, ex, scoring.final_score(ex["base"], card["pro_score"], cfg, card.get("pro_share"))


def top_stats(card: dict, role: str, cfg: dict, n: int = 4) -> list[dict]:
    weights = cfg["role_weights"][role]
    top = sorted(weights, key=lambda k: (-weights[k], -card["stats"][k]))[:n]
    return [{"k": k, "v": card["stats"][k]} for k in top]


def _num_it(x: float) -> str:
    return f"{x:+.1f}".replace(".", ",")


def factor_text(card: dict, ex: dict, cfg: dict, sm: dict) -> str:
    """Prima parte della riga di sintesi: le stats che contano per il ruolo e sono alte, altrimenti i PlayStyle+."""
    weights, stats = cfg["role_weights"][ex["role"]], card["stats"]
    strong, elite = sm["strong_stat"], sm["elite_stat"]
    ranked = sorted(weights, key=lambda k: (-weights[k], -stats[k]))[:6]
    items: list[tuple[str, int]] = []  # (testo, valore minimo)
    if all(k in weights and stats.get(k, 0) >= strong for k in ("acceleration", "sprint_speed")):
        items.append(("scatto e velocità", min(stats["acceleration"], stats["sprint_speed"])))
    for k in ranked:
        if len(items) >= 2:
            break
        if k in ("acceleration", "sprint_speed") and items and items[0][0] == "scatto e velocità":
            continue
        if stats[k] >= strong:
            items.append((analysis.NAMES.get(k, k.replace("_", " ")), stats[k]))
    if items:
        text = ", ".join(t for t, _ in items)
        text += " da vertice" if min(v for _, v in items) >= elite else " di alto livello"
    else:
        plus = [p for p in card.get("playstyles", []) if p.endswith("+")][:2]
        text = f"PlayStyle+ {', '.join(plus)}" if plus else "profilo nella media del ruolo"
    return text[0].upper() + text[1:]


def verdict_tail(v: dict) -> str:
    gap = v.get("value_gap")
    if gap is None:
        return ""
    if v["verdict"] == "MUST_DO":
        return f"rende più del prezzo ({_num_it(gap)})"
    if v["verdict"] == "AVOID":
        return f"costa più di quanto rende ({_num_it(gap)})"
    return "prezzo in linea"


def make_summary(factor: str, v: dict | None) -> str:
    tail = verdict_tail(v) if v else ""
    s = f"{factor} · {tail}" if tail else factor
    return s if len(s) <= SUMMARY_MAX else s[:SUMMARY_MAX - 1].rstrip() + "…"


# ------------------------------------------------------------------ ricalcolo

def _opinions(conn, ids: list[int], everything: bool) -> dict[int, list[dict]]:
    if everything:
        return db.opinions_by_card(conn)
    out: dict[int, list[dict]] = {}
    for i in ids:
        out.update(db.opinions_by_card(conn, i))
    return out


def recompute(conn, ids=None, cfg: dict | None = None) -> dict:
    """Ricalcola la cache. ids=None: tutte le carte. ids=[...]: quelle indicate PIU' tutte le righe obsolete o mancanti
    (ids=() ricalcola solo le obsolete). Rifa i verdetti sul prezzo di tutte le carte delle posizioni toccate.
    Ritorna {'valutate': n, 'posizioni': n, 'totale': True|False}."""
    with _LOCK:
        cfg = cfg or scoring.load_config()
        cat = catcfg.load()
        h, ts = config_hash(cfg, cat), now_iso()
        light = {r["id"]: (r["position"], r["price"]) for r in conn.execute("SELECT id, position, price FROM cards")}
        cached = {r["card_id"]: r for r in conn.execute("SELECT card_id, position, final, factor, config_hash FROM evaluations")}
        if ids is None:
            touched = set(light)
        else:
            touched = {i for i in ids if i in light} | {i for i in light if i not in cached or cached[i]["config_hash"] != h}
        if not touched:
            return {"valutate": 0, "posizioni": 0, "totale": False}
        # carte da rivalutare: leggo solo queste (JSON incluso)
        order = sorted(touched)
        rows = []
        if len(order) * 3 > len(light):
            rows = [r for r in conn.execute("SELECT * FROM cards") if r["id"] in touched]
        else:
            for k in range(0, len(order), _CHUNK):
                part = order[k:k + _CHUNK]
                rows += conn.execute(f"SELECT * FROM cards WHERE id IN ({','.join('?' * len(part))})", part).fetchall()
        ops = _opinions(conn, order, everything=len(order) > 200)
        entries: dict[int, dict | None] = {}
        for r in rows:
            card = db.row_to_card(r)
            sc = score_card(card, ops.get(card["id"]), cfg)
            if sc is None:
                entries[card["id"]] = None
                continue
            card, ex, final = sc
            level, label = analysis.meta_level(ex["base"], cfg)
            entries[card["id"]] = {
                "card": card, "ex": ex, "final": final, "meta": (level, label), "n_ops": len(ops.get(card["id"], [])),
                "factor": factor_text(card, ex, cfg, cat["summary"]),
                "scores": (round(ex["base"], 2), round(final, 2),
                           round(card.get("pro_share", cfg["score_weights"]["pro"]) if card["pro_score"] is not None else 0, 4))}
        # mercato: carte rivalutate (valori freschi) + carte in cache (score finale gia' calcolato)
        items = []
        for i, (pos, price) in light.items():
            if i in entries:
                if entries[i] is not None:
                    items.append((i, pos, price, entries[i]["final"]))
            elif i in cached and cached[i]["final"] is not None:
                items.append((i, pos, price, cached[i]["final"]))
        affected = {light[i][0] for i in entries} | {cached[i]["position"] for i in entries if i in cached and cached[i]["position"]}
        by_pos = [x for x in items if x[1] in affected]
        markets = Markets(by_pos, cfg, cat["curve_max_points"])
        full, part = [], []
        for i, pos, price, final in by_pos:
            market, curve, _n = markets.for_card(i, pos)
            v = scoring.verdict(final, price, market, cfg, curve)
            if i in entries:
                e = entries[i]
                card, ex = e["card"], e["ex"]
                full.append((i, e["scores"][0], e["scores"][1], e["scores"][2], card["pro_score"], v["verdict"], v["value_gap"], v["reason"],
                             e["meta"][0], e["meta"][1], make_summary(e["factor"], v),
                             json.dumps(top_stats(card, ex["role"], cfg)),
                             json.dumps([p for p in card["playstyles"] if p.endswith("+")][:3], ensure_ascii=False),
                             e["n_ops"], h, ts, e["factor"], pos))
            else:
                part.append((v["verdict"], v["value_gap"], v["reason"], make_summary(cached[i]["factor"], v), h, ts, pos, i))
        for i, e in entries.items():
            if e is None:  # carta non valutabile con la config attuale: riga senza punteggi (non si ricalcola a ogni lettura)
                full.append((i, None, None, None, None, "NEUTRAL", None, "Carta non valutabile con la configurazione attuale.",
                             None, None, "Carta non valutabile con la configurazione attuale", "[]", "[]", 0, h, ts, "", light[i][0]))
        conn.executemany("INSERT OR REPLACE INTO evaluations (card_id, base, final, pro_share, pro_score, verdict, value_gap, verdict_reason, "
                         "meta_level, meta_label, summary, top_stats, bonus_playstyles, opinions_count, config_hash, ts, factor, position) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", full)
        conn.executemany("UPDATE evaluations SET verdict=?, value_gap=?, verdict_reason=?, summary=?, config_hash=?, ts=?, position=? "
                         "WHERE card_id=?", part)
        conn.commit()
        return {"valutate": len(entries), "posizioni": len(affected), "totale": len(entries) == len(light)}


def stale_count(conn, h: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM cards c LEFT JOIN evaluations e ON e.card_id = c.id "
                        "WHERE e.card_id IS NULL OR e.config_hash != ?", (h,)).fetchone()[0]


def ensure_fresh(conn, cfg: dict | None = None) -> int:
    """Ricalcola SOLO le valutazioni obsolete o mancanti (nessun lavoro se la cache e' aggiornata). Ritorna quante carte."""
    cfg = cfg or scoring.load_config()
    h = config_hash(cfg)
    if stale_count(conn, h) == 0:
        return 0
    with _LOCK:
        if stale_count(conn, h) == 0:  # un altro thread l'ha appena fatto
            return 0
        return recompute(conn, ids=(), cfg=cfg)["valutate"]
