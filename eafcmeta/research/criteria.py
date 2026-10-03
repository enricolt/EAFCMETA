"""Criteri (chiavi di criteria.json) e polarità, offline e deterministici, pensati per trascrizioni automatiche.

Il testo viene tokenizzato con radici leggere (text.stem), poi:
1. `parse` riconosce, con una sola scansione lineare (longest match su tabelle per prima radice, niente regex per parola
   chiave), i *contenuti* (parole chiave di criterio, parole di polarità, frasi di verdetto, stelle) e i *modificatori*
   (negazioni, intensificatori, cautele, ironia, promozioni, discorso riportato, ipotesi, domande, contrasti);
2. `evaluate` trasforma un intervallo di token (una clausola) in *evidenze* [Ev]: ogni criterio prende la polarità dalla
   propria parola chiave (lento = -1) oppure dalla parola di polarità più vicina nella clausola, ignorando le parole
   vuote (ponderata dalle virgole); negazioni (anche doppie e a distanza), intensificatori e cautele modificano peso e
   affidabilità; i casi ambigui producono evidenze marcate `unsure` (che abbassano la confidenza) invece di indovinare.

`analyze`/`extract_criteria` restano l'interfaccia semplice (testo -> criteri) usata dai test e dal modo LLM.
"""
import re
from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache

from . import config, text as T

# ------------------------------------------------------------------ costanti strutturali ----

FILLERS = set(T.STOPWORDS) | {"stato", "stata", "stati", "essere", "ha", "ho", "fa", "va", "sta", "ci", "li", "qui", "qua",
                               "also", "just", "pure", "anche", "ancora", "sua", "suo", "suoi", "sue", "its", "s", "l",
                               "un", "dell", "dall", "nell", "sull", "all", "d", "c", "m", "t", "ve", "vi", "gli", "ne",
                               "ok", "okay", "eh", "ehm", "beh", "cioe", "allora", "ragazzi", "guys", "dai", "ah", "uh"}
COPULA = {"e", "is", "s", "so", "pretty", "also", "still", "sta", "va", "fa", "feels", "feel", "sembra", "looks", "too",
          "molto", "abbastanza", "era", "sono", "are", "was", "very", "proprio", "tutto", "sempre"}
PRICE_CTX = {"prezzo", "prezzi", "costo", "costa", "costi", "crediti", "credito", "monete", "moneta", "coins", "coin",
             "price", "cost", "soldi", "money", "pagare", "paga"}
NOUN_BLOCK = {"livello", "rating", "ritmo", "rate", "workrate", "volume", "numero", "valutazione", "media", "voto",
              "intensita", "pressing", "difficolta", "statistiche", "stats", "punteggio", "overall", "ovr", "work",
              "potenziale", "standard", "profilo", "rischio", "rendimento", "impatto"}
HEIGHT_NOUN = {"giocatore", "difensore", "attaccante", "centrale", "ragazzo", "carta", "tipo", "player", "striker",
               "defender", "guy", "cb", "st", "portiere", "gk", "centravanti", "terzino"}
PS_CTX = {"playstyle", "playstyles", "play", "style", "styles", "chem", "plus", "chemistry", "stile", "stili"}
BODY_CTX = {"body", "type", "types", "bodytype", "corporatura", "build", "fisico"}
PRICE_STEMS = {T.stem(w) for w in ("prezzo", "credito", "moneta", "coin", "price", "worth", "vale", "costa", "expensive",
                                   "overpriced", "cheap", "affare", "bargain", "steal", "soldi", "money", "investimento",
                                   "investment", "costosa", "costoso", "caro")}
_GATE_PS_WORDS = {"block", "aerial", "anticipate", "technical", "wall", "basic", "anchor", "engine", "intercept",
                  "relentless", "gloves", "hawk"}
_GATE_HEIGHT = {"alto", "alta", "alti", "alte", "basso", "bassa", "bassi", "basse", "piccolo", "piccola", "piccoli",
                "piccole", "tall", "short", "tiny", "corto", "corta"}
_GATE_BODY = {"lean", "stocky", "unique", "custom", "andy", "athletic"}
_CMP_WORDS = ("meglio", "migliore", "peggio", "peggiore", "better", "worse")
_STAR_WORDS = {"uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "one": 1, "two": 2, "three": 3, "four": 4,
               "five": 5}
_NEG_START = {T.stem(w) for w in ("non", "mica", "senza", "not", "no", "never", "niente", "nulla")}
_TOP_BLOCK = {"scorer", "division", "divisione", "rank", "list", "lista", "players", "squadre", "marcatori"}
_ELITE_BLOCK = {"division", "divisione", "rank", "rewards", "premi", "rivals", "champs", "1", "2", "3"}
_LIGHT_NEG = {T.stem("poco"), T.stem("pochissimo")}
MAX_ITEMS_PER_CLAUSE = 400
_GATES = {}
for _ws, _g in ((_GATE_HEIGHT, "height"), (("cm",), "cm"), (_GATE_BODY, "body"), (_GATE_PS_WORDS, "ps"),
                (("high", "low"), "pricew")):
    for _w in _ws:
        _GATES[T.stem(_w)] = _g

# ------------------------------------------------------------------ vocabolario ----


@dataclass(frozen=True)
class Entry:
    parts: tuple
    kind: str            # crit | pol | verdict  (contenuti) ; neg, intens, weak, emph, hedge, doubt, irony, promo, reported,
    key: str | None      # hypo, question, contrast, shift (funzionali)
    w: float = 0.0
    flag: str = ""
    word: str = ""       # prima parola grezza (per i controlli di contesto)


@dataclass(frozen=True)
class Vocab:
    content: dict
    func: dict
    keys: frozenset
    words: frozenset = frozenset()   # tutte le parole (grezze) del vocabolario: non sono mai nomi di giocatori


def _stems(phrase: str) -> tuple:
    return tuple(t.st for t in T.tokenize(T.norm(phrase)))


_ALLWORDS: set = set()


def _raw(phrase: str) -> tuple:
    r = tuple(t.w for t in T.tokenize(T.norm(phrase)))
    _ALLWORDS.update(r)
    return r


@lru_cache(maxsize=1)
def vocab() -> Vocab:
    cc, rc = config.criteria_config(), config.research_config()
    inh = {T.norm(w): 1 for w in rc["keyword_polarity"].get("positive", [])}
    inh.update({T.norm(w): -1 for w in rc["keyword_polarity"].get("negative", [])})
    content: dict[tuple, Entry] = {}

    def put(e: Entry, replace: bool = False):
        old = content.get((e.parts, e.kind))
        if old is None or replace or abs(e.w) > abs(old.w):
            content[(e.parts, e.kind)] = e

    crit_seen: dict[tuple, str] = {}
    for key, spec in cc["criteria"].items():
        groups = ((spec.get("keywords", []), 0), (spec.get("positive", []), 1), (spec.get("negative", []), -1))
        for words, pol in groups:
            for kw in words:
                n = T.norm(kw)
                parts, raw = _stems(kw), _raw(kw)
                if not parts or kw in ("5 stelle",):
                    continue
                w = pol or inh.get(n, 0)
                if parts in crit_seen and crit_seen[parts] != key:
                    continue  # la stessa parola in due criteri: vince il primo (ordine di criteria.json)
                crit_seen.setdefault(parts, key)
                flag = ""
                if len(parts) == 1:
                    flag = _GATES.get(parts[0], "")
                put(Entry(parts, "crit", key, float(w), flag, raw[0]), replace=bool(w))
    pol = cc["polarity"]
    skip = {"meta", "must", "troppo", "forse"}  # 'meta' e 'must' solo dentro frasi di verdetto; 'troppo' è un intensificatore
    for name, w in (("positive", 1.0), ("negative", -1.0), ("strong_positive", 1.5), ("strong_negative", -1.5),
                    ("mild_positive", 0.5), ("mild_negative", -0.5)):
        for word in pol.get(name, []):
            n = T.norm(word)
            if n in skip:
                continue
            parts = _stems(word)
            if parts:
                flag = "cmp" if any(parts == (T.stem(c),) for c in _CMP_WORDS) else ""
                old = content.get((parts, "pol"))
                if old is not None and (old.w > 0) != (w > 0):
                    content.pop((parts, "pol"))  # in due liste di segno opposto: ambigua, la tolgo
                    continue
                put(Entry(parts, "pol", None, w, flag, _raw(word)[0]))
    for c in _CMP_WORDS:
        sign = 1.0 if c in ("meglio", "migliore", "better") else -1.0
        put(Entry(_stems(c), "pol", None, sign, "cmp", c), replace=True)
    verd = cc.get("verdicts", {})
    stance = rc.get("stance_phrases", {})
    for lst, w in ((verd.get("positive", []) + stance.get("yes", []), 2.0),
                   (verd.get("negative", []) + stance.get("no", []), -2.0),
                   (verd.get("maybe", []) + stance.get("maybe", []), 0.0)):
        for ph in lst:
            parts = _stems(ph)
            if not parts:
                continue
            key = "price" if any(p in PRICE_STEMS for p in parts) and w != 0.0 else None
            put(Entry(parts, "verdict", key, w, "maybe" if w == 0.0 else "", _raw(ph)[0]), replace=True)
    func: dict[tuple, Entry] = {}
    cues = cc.get("cues", {})
    opinion = {T.stem(w) for w in cues.get("opinion_verbs", [])}
    for kind, name in (("neg", "negators"), ("intens", "intensifiers"), ("weak", "weak"), ("emph", "emphatic_neg"),
                       ("hedge", "hedges"), ("doubt", "doubts"), ("irony", "irony"), ("promo", "promo"),
                       ("reported", "reported"), ("hypo", "hypothetical"), ("question", "question"),
                       ("contrast", "contrast"), ("shift", "shift"), ("untried", "untried")):
        for ph in cues.get(name, []):
            parts = _stems(ph)
            if not parts:
                continue
            flag = "op" if kind == "neg" and len(parts) > 1 and parts[-1] in opinion else ""
            w = 0.7 if kind == "neg" and parts in {(s,) for s in _LIGHT_NEG} else 1.0
            func[(parts, kind)] = Entry(parts, kind, None, w, flag, _raw(ph)[0])
    # dizionari per prima radice
    cidx: dict[str, list] = {}
    for e in content.values():
        cidx.setdefault(e.parts[0], []).append(e)
    fidx: dict[str, list] = {}
    for e in func.values():
        fidx.setdefault(e.parts[0], []).append(e)
    return Vocab(cidx, fidx, frozenset(cc["criteria"]), frozenset(_ALLWORDS))


def reset_cache() -> None:
    vocab.cache_clear()


def valid_keys() -> set[str]:
    return set(config.criteria_config()["criteria"])


# ------------------------------------------------------------------ parsing ----

@dataclass
class Item:
    i: int
    j: int
    kind: str            # crit | pol | verdict | stars
    key: str | None
    w: float
    flag: str = ""
    val: int = 0


@dataclass
class FTag:
    i: int
    j: int
    kind: str
    w: float = 1.0
    flag: str = ""


class Parse:
    """Risultato di `parse`: token, contenuti riconosciuti, modificatori e tabelle di prefissi per i calcoli O(1)."""

    def __init__(self, toks, normed, comma_before):
        self.toks, self.normed = toks, normed
        self.items: list[Item] = []
        self.starts: list[int] = []
        self.ftags: dict[int, list[FTag]] = {}
        self.fend: dict[int, list[FTag]] = {}
        self.comma_before = comma_before
        self.cm: list[int] = []   # prefisso virgole
        self.cp: list[int] = []   # prefisso token "di contenuto"


def _gate(toks, i: int, j: int, e: Entry):
    """Controllo di contesto per parole chiave ambigue. True = ok, None = scartata, (key, w) = ricondotta ad altro criterio."""
    f = e.flag
    if f == "cm":
        return True if i > 0 and toks[i - 1].w.isdigit() else None
    if f == "pricew":   # "the price is way too high", "price is low": solo accanto a una parola di prezzo
        if any(t.w in PRICE_CTX for t in toks[max(0, i - 5):i]):
            return ("price", -1.0 if toks[i].w == "high" else 1.0)
        return None
    if f == "height":
        lo = max(0, i - 4)
        prev = [t.w for t in toks[lo:i]]
        tw = toks[i].w
        if any(p in PRICE_CTX for p in prev) and tw[:3] in ("alt", "bas", "cor"):
            return ("price", -1.0 if tw.startswith("alt") else 1.0)
        if any(p in NOUN_BLOCK for p in prev):
            return None
        near = [t.w for t in toks[max(0, i - 2):i]]
        nxt = toks[j].w if j < len(toks) else ""
        after = [t.w for t in toks[j:j + 4]]
        if nxt.isdigit() or any(w in ("metro", "metri", "cm", "centimetri", "metre", "meters", "feet") for w in after):
            return None   # misura ("è alto un metro e settantotto"): un dato, non un giudizio
        if any(p in COPULA or p in HEIGHT_NOUN for p in near):
            return True
        return None
    if f == "body":
        ctx = [t.w for t in toks[max(0, i - 3):i]] + [t.w for t in toks[j:j + 3]]
        return True if any(c in BODY_CTX for c in ctx) else None
    if f == "ps":
        ctx = [t.w for t in toks[max(0, i - 4):i]] + [t.w for t in toks[j:j + 4]]
        return True if any(c in PS_CTX or c.endswith("+") for c in ctx) else None
    return True


def _pol_guard(toks, i: int, j: int, e: Entry) -> bool:
    w = e.word
    nxt = toks[j].w if j < len(toks) else ""
    if w == "top":
        return not (nxt.isdigit() or nxt in _TOP_BLOCK)
    if w == "elite":
        return nxt not in _ELITE_BLOCK
    if w in ("ok", "okay"):
        return i > 0 and toks[i - 1].w in COPULA
    return True


def _match(toks, i: int, entries) -> list:
    n = len(toks)
    out = []
    for e in entries:
        L = len(e.parts)
        if i + L > n:
            continue
        ok = True
        for k in range(1, L):
            if toks[i + k].st != e.parts[k]:
                ok = False
                break
        if ok:
            out.append(e)
    return out


_KIND_PRIO = {"verdict": 0, "crit": 1, "pol": 2}


def parse(toks, normed: str = "", text: str = "") -> Parse:
    v = vocab()
    n = len(toks)
    # virgole/due punti/trattini tra i token (fermano la portata delle negazioni e penalizzano l'aggancio)
    src = text or normed
    cb = [False] * (n + 1)
    if src:
        for k in range(1, n):
            gap = src[toks[k - 1].b:toks[k].a]
            if gap and ("," in gap or ":" in gap or "—" in gap or " - " in gap):
                cb[k] = True
    P = Parse(toks, normed, cb)
    consumed = [False] * n
    i = 0
    while i < n:
        cand = v.content.get(toks[i].st)
        best = None
        if cand:
            for e in _match(toks, i, cand):
                L = len(e.parts)
                if e.kind == "pol" and not _pol_guard(toks, i, i + L, e):
                    continue
                key, w = e.key, e.w
                if e.kind == "crit" and e.flag:
                    g = _gate(toks, i, i + L, e)
                    if g is None:
                        continue
                    if isinstance(g, tuple):
                        key, w = g
                score = (L, -_KIND_PRIO[e.kind])
                if best is None or score > best[0]:
                    best = (score, e, key, w)
        if best is not None:
            _, e, key, w = best
            L = len(e.parts)
            P.items.append(Item(i, i + L, e.kind, key, w, e.flag))
            for k in range(i, i + L):
                consumed[k] = True
            i += L
        else:
            # stelle: "5 stelle", "4 star"
            sv = int(toks[i].w) if (toks[i].w.isdigit() and len(toks[i].w) == 1) else _STAR_WORDS.get(toks[i].w)
            if sv and i + 1 < n and toks[i + 1].st in ("stell", "star", "stelline"):
                P.items.append(Item(i, i + 2, "stars", None, 0.0, "", sv))
                consumed[i] = consumed[i + 1] = True
                i += 2
                continue
            i += 1
    # modificatori e cue: longest match per tipo, solo sui token non consumati dai contenuti
    for i in range(n):
        if consumed[i]:
            continue
        cand = v.func.get(toks[i].st)
        if not cand:
            continue
        best: dict[str, Entry] = {}
        for e in _match(toks, i, cand):
            if any(consumed[i + k] for k in range(1, len(e.parts))):
                continue
            o = best.get(e.kind)
            if o is None or len(e.parts) > len(o.parts):
                best[e.kind] = e
        for kind, e in best.items():
            L = len(e.parts)
            tag = FTag(i, i + L, kind, e.w, e.flag)
            P.ftags.setdefault(i, []).append(tag)
            if kind in ("intens", "weak", "emph"):
                P.fend.setdefault(i + L, []).append(tag)
    P.starts = [it.i for it in P.items]
    light = [False] * n
    for k in range(n):
        light[k] = toks[k].w in FILLERS or toks[k].w in HEIGHT_NOUN
    for it in P.items:
        if it.kind == "crit":
            for k in range(it.i, it.j):
                light[k] = True
    for i, tags in P.ftags.items():
        for t in tags:
            if t.kind in ("neg", "intens", "weak", "emph", "hedge", "doubt", "opinion"):
                for k in range(t.i, t.j):
                    light[k] = True
    cp = [0] * (n + 1)
    cm = [0] * (n + 2)
    for k in range(n):
        cp[k + 1] = cp[k] + (0 if light[k] else 1)
    for k in range(n + 1):
        cm[k + 1] = cm[k] + (1 if cb[k] else 0)
    P.cp, P.cm = cp, cm
    return P


def tags_in(P: Parse, i: int, j: int, kinds) -> list:
    out = []
    for k in range(i, j):
        for t in P.ftags.get(k, ()):
            if t.kind in kinds and t.j <= j:
                out.append(t)
    return out


def clause_ranges(P: Parse, i: int, j: int) -> list[tuple[int, int, str]]:
    """Divide [i, j) alle parole di contrasto. Ritorna [(inizio, fine, tipo)] dove tipo è '' (inizio), 'contrast' o 'shift'
    (la clausola che segue una parola di cambio soggetto: 'invece', 'mentre', 'while'...)."""
    out, start, kind = [], i, ""
    k = i
    while k < j:
        cut = None
        for t in P.ftags.get(k, ()):
            if t.kind in ("contrast", "shift") and t.j <= j:
                if cut is None or t.j > cut.j:
                    cut = t
        if cut is not None:
            if k > start:
                out.append((start, k, kind))
            start, kind, k = cut.j, cut.kind, cut.j
            continue
        k += 1
    if start < j:
        out.append((start, j, kind))
    return out


# ------------------------------------------------------------------ valutazione di una clausola ----

@dataclass
class Ev:
    kind: str                 # crit | verdict | free | maybe
    key: str | None
    w: float
    pos: int
    flags: frozenset = frozenset()


_CONCORD = {T.stem(w) for w in ("niente", "nulla", "mai", "nemmeno", "neanche", "nessuno", "nessuna", "mica")}


def _item_between(P: Parse, a: int, b: int) -> bool:
    """C'è un contenuto (criterio, polarità, verdetto) che inizia tra i token a (incluso) e b (escluso)?"""
    k = bisect_left(P.starts, a)
    while k < len(P.items) and P.items[k].i < b:
        it = P.items[k]
        if it.kind in ("pol", "verdict") or (it.kind == "crit" and it.w):  # i criteri neutri non "assorbono" la negazione
            return True
        k += 1
    return False


def _modify(P: Parse, i0: int, it_i: int, it_j: int, w: float, sup: bool, phrase_neg: bool = False) -> tuple[float, set]:
    """Applica intensificatori, negazioni, cautele a un peso base. Ritorna (peso, flags)."""
    flags: set = set()
    cp = P.cp
    mult = 1.4 if sup else 1.0
    emph = False
    pos = it_i
    for _ in range(3):  # catena di intensificatori subito prima
        tags = P.fend.get(pos)
        if not tags:
            break
        t = max(tags, key=lambda x: x.j - x.i)
        if t.i < i0:
            break
        if t.kind == "intens":
            mult = min(1.8, mult * 1.4)
        elif t.kind == "weak":
            mult *= 0.6
        else:
            emph = True
        pos = t.i
    # "per niente"/"affatto" subito dopo ("lentissimo per niente", "non mi ha convinto per niente")
    for t in P.ftags.get(it_j, ()):
        if t.kind == "emph" and not phrase_neg:
            emph = True
    # negazioni nella portata: nessun contenuto tra la negazione e la parola (le parole vuote non contano);
    # le negazioni concordanti ("non ... nemmeno", "non ... mai") valgono come una sola
    negs = []
    q = it_i - 1
    lowest = max(i0, it_i - 14)
    while q >= lowest:
        if P.comma_before[q + 1]:
            break
        for t in P.ftags.get(q, ()):
            if t.kind == "neg" and t.j <= it_i:
                gap = cp[it_i] - cp[t.j]
                limit = 2 if t.flag == "op" else 0
                if gap <= limit and it_i - t.j <= (10 if t.flag == "op" else 8) and not _item_between(P, t.j, it_i):
                    negs.append(t)
                break
        q -= 1
    negs.sort(key=lambda x: x.i)
    n_neg, neg_strength, root = 0, 1.0, None
    for t in negs:
        if root is not None and P.toks[t.i].st in _CONCORD and t.i - root.j <= 5:
            continue
        n_neg += 1
        neg_strength = min(neg_strength, t.w)
        root = t
    if n_neg == 0 and emph:
        n_neg, neg_strength = 1, 1.0  # "per niente veloce" senza altra negazione = negazione
        emph = False
    if n_neg == 0:
        # negazione plausibile ma fuori portata nella stessa clausola: non so se vale per questa parola
        for q2 in range(max(i0, it_i - 12), it_i):
            if any(t.kind == "neg" and t.j <= it_i for t in P.ftags.get(q2, ())):
                g = cp[it_i] - cp[q2 + 1]
                if 1 <= g <= 2 and not any(P.comma_before[x + 1] for x in range(q2, it_i)) \
                        and not _item_between(P, q2 + 1, it_i):
                    flags.add("unsure")
                    break
    # cautele
    for q3 in range(max(i0, it_i - 4), it_i):
        for t in P.ftags.get(q3, ()):
            if t.kind == "hedge":
                flags.add("hedged")
    for q3 in range(max(i0, it_i - 6), it_i + 2):
        for t in P.ftags.get(q3, ()):
            if t.kind == "doubt":
                flags.add("doubt")
    base = w * mult
    if n_neg == 0:
        return base, flags
    if n_neg == 1:
        out = -base * 0.8 * neg_strength
        if mult > 1.0 and not emph:
            out *= 0.6   # "non è molto veloce" = poco convincente, non "lento"
        if emph:
            out *= 1.25
        return out, flags
    flags.add("unsure")
    return base * 0.6, flags


_COPULA_CORE = {"e", "is", "are", "was", "were", "era", "sono", "sta", "feels", "feel", "sembra", "looks", "seems", "ha"}


def _bind(P: Parse, crit: Item, polars: list[Item]):
    """Parola di polarità più vicina a un criterio. Costo = token di contenuto in mezzo + 1.5 per virgola; bonus per il
    predicato 'criterio + è + aggettivo' ('lo scatto è scarso'). A parità di costo con segni opposti il legame è
    ambiguo ('ottimo scatto pessima finalizzazione' senza punteggiatura): ritorna "ambiguous"."""
    toks = P.toks
    scored = []
    for p in polars:
        if p.i >= crit.j:   # a destra
            gap = P.cp[p.i] - P.cp[crit.j]
            commas = P.cm[p.i + 1] - P.cm[crit.j]
            raw = p.i - crit.j
            cost = gap + 2.5 * commas
            if raw >= 1 and toks[crit.j].w in _COPULA_CORE and P.cp[p.i] - P.cp[crit.j + 1] <= 0 and commas == 0:
                cost -= 0.5
        elif p.j <= crit.i:  # a sinistra
            gap = P.cp[crit.i] - P.cp[p.j]
            commas = P.cm[crit.i + 1] - P.cm[p.j]
            raw = crit.i - p.j
            cost = gap + 2.5 * commas
        else:
            continue
        if cost > 2.0 or raw > 8:
            continue
        scored.append((cost, p))
    if not scored:
        return None
    best = min(c for c, _ in scored)
    top = [p for c, p in scored if abs(c - best) < 1e-9]
    if len({p.w > 0 for p in top}) > 1:
        return "ambiguous"
    return top[0]


_STARS = {"weak_foot": {5: 1.0, 4: 0.7, 2: -1.0, 1: -1.0}, "skill_moves": {5: 1.0, 2: -1.0, 1: -1.0}}


def evaluate(P: Parse, i: int, j: int, lo: int | None = None) -> list[Ev]:
    """Evidenze di una clausola [i, j). `lo` = limite sinistro per la portata di negazioni e cautele (di solito l'inizio
    della clausola della frase, che può cominciare prima del tratto attribuito alla carta: 'non credo che X sia veloce')."""
    lo = i if lo is None else min(lo, i)
    a = bisect_left(P.starts, i)
    items = []
    for it in P.items[a:a + MAX_ITEMS_PER_CLAUSE]:
        if it.i >= j:
            break
        if it.j <= j:
            items.append(it)
    if not items:
        return []
    toks = P.toks
    polars = [it for it in items if it.kind == "pol"]
    bound: set[int] = set()
    evs: list[Ev] = []
    crits = [it for it in items if it.kind == "crit"]

    def sup(it: Item) -> bool:
        return toks[it.j - 1].sup

    for it in items:
        if it.kind == "verdict":
            if it.w == 0.0:
                _, fl = _modify(P, lo, it.i, it.j, 1.0, False)
                evs.append(Ev("maybe", None, 0.0, it.i, frozenset(fl)))
                continue
            w, fl = _modify(P, lo, it.i, it.j, it.w, sup(it), toks[it.i].st in _NEG_START)
            evs.append(Ev("verdict", it.key, w, it.i, frozenset(fl)))
        elif it.kind == "crit":
            if it.w:
                w, fl = _modify(P, lo, it.i, it.j, it.w, sup(it))
                evs.append(Ev("crit", it.key, w, it.i, frozenset(fl)))
                continue
            if any(c2 is not it and c2.key == it.key and c2.w and (0 <= c2.i - it.j <= 1 or 0 <= it.i - c2.j <= 1)
                   for c2 in crits):
                continue  # "prezzo alto": la polarità è già in 'alto', il nome non cerca altrove
            tgt = _bind(P, it, polars) if polars else None
            if tgt is None:
                continue
            if tgt == "ambiguous":
                evs.append(Ev("crit", it.key, 0.0, it.i, frozenset({"unsure", "ambiguous"})))
                continue
            bound.add(id(tgt))
            w, fl = _modify(P, lo, tgt.i, tgt.j, tgt.w, sup(tgt))
            if tgt.flag == "cmp":
                fl.add("cmp")
            fl.add("explicit")   # nome del criterio + aggettivo ("lo scatto è ottimo"): parla davvero di quel criterio
            evs.append(Ev("crit", it.key, w, it.i, frozenset(fl)))
        elif it.kind == "stars":
            near = [c for c in crits if c.key in _STARS and abs(c.i - it.j if c.i >= it.j else it.i - c.j) <= 4]
            if not near:
                continue
            c = min(near, key=lambda c: abs(c.i - it.j) if c.i >= it.j else abs(it.i - c.j))
            val = _STARS[c.key].get(it.val)
            if val is None:
                continue
            w, fl = _modify(P, lo, c.i, c.j, val, False)
            fl.add("explicit")
            evs.append(Ev("crit", c.key, w, it.i, frozenset(fl)))
    for p in polars:
        if id(p) in bound:
            continue
        w, fl = _modify(P, lo, p.i, p.j, p.w, sup(p))
        if p.flag == "cmp":
            fl = set(fl) | {"cmp"}
        evs.append(Ev("free", None, w, p.i, frozenset(fl)))
    return evs


# ------------------------------------------------------------------ punteggi espliciti ----

_NUMW = {"uno": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
         "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_SCORE = re.compile(r"(?<![\d.,/])(\d{1,3}(?:[.,]\d)?)\s{0,2}(?:/|su|out of)\s{0,2}(10|100|5)(?![\d/])")
_SCORE_W = re.compile(r"(?<![a-z])(uno|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|one|two|three|four|five|six|seven|"
                      r"eight|nine|ten)\s{1,2}(?:su|out of)\s{1,2}(dieci|ten)(?![a-z])")
_SCORE_V = re.compile(r"(?<![a-z])(?:voto|rating|vote|score|giudizio|pagella)\s{0,2}(?:di|e|is|:)?\s{0,2}"
                      r"(\d{1,2}(?:[.,]\d)?)(?![\d/]|\s{0,2}(?:su|out))")
_SCORE_G = re.compile(r"(?<![a-z])(?:do|gli do|le do|give it|give him|give her|i give)\s{1,2}(?:un|a|an)?\s{0,2}"
                      r"(\d{1,2}(?:[.,]\d)?)(?![\d/]|\s{0,2}(?:su|out))")


def scores_in(normed: str, a: int, b: int) -> list[float]:
    """Voti espliciti ('9/10', '8 su 10', 'voto 9', 'nove su dieci') nell'intervallo, in scala 0-100."""
    seg = normed[a:b]
    out: list[float] = []
    for m in _SCORE.finditer(seg):
        val, scale = float(m.group(1).replace(",", ".")), int(m.group(2))
        if 0 <= val <= scale:
            out.append(round(val * 100 / scale, 1))
    for m in _SCORE_W.finditer(seg):
        out.append(float(_NUMW[m.group(1)] * 10))
    for rx in (_SCORE_V, _SCORE_G):
        for m in rx.finditer(seg):
            val = float(m.group(1).replace(",", "."))
            if 0 <= val <= 10:
                out.append(round(val * 10, 1))
    return out


# ------------------------------------------------------------------ interfaccia semplice ----

@dataclass
class Analysis:
    criteria: list  # [(key, +1|-1)]
    free_polarity: int  # somma dei segni delle parole di polarità non legate a un criterio
    evidence: int  # numero di segnali trovati


def analyze(text: str) -> Analysis:
    """Criteri con polarità (+1/-1) e segnali liberi in un testo, senza attribuzione a carte."""
    normed = T.norm(text)
    blanked, marks = T.blank_timestamps(normed)
    toks = T.tokenize(blanked)
    if not toks:
        return Analysis([], 0, 0)
    P = parse(toks, blanked, text)
    net: dict[str, float] = {}
    free, evidence = 0, 0
    for (si, sj) in T.sentences(text, toks, marks):
        for (a, b, _kind) in clause_ranges(P, si, sj):
            for ev in evaluate(P, a, b):
                evidence += 1
                if ev.kind == "free":
                    free += (ev.w > 0) - (ev.w < 0)
                elif ev.key and ev.w:
                    net[ev.key] = net.get(ev.key, 0.0) + ev.w
    crit = [(k, 1 if n > 0 else -1) for k, n in net.items() if abs(n) > 1e-9]
    return Analysis(criteria=crit, free_polarity=free, evidence=evidence)


def extract_criteria(text: str) -> list[tuple[str, int]]:
    """Solo i criteri [(chiave, +1|-1)] citati nel testo."""
    return analyze(text).criteria
