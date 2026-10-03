"""Risoluzione della carta: da un testo libero alle carte del DB a cui si riferisce.

Solo libreria standard (difflib, unicodedata). Regole:
- nome completo = 1.0; soprannome (research.json) = 0.95; cognome = 0.75; cognome con refuso ~0.6-0.7; solo nome = 0.5;
- la versione citata (TOTY, Icon, ...) aggiunge +0.1 e scavalca le altre versioni dello stesso giocatore;
- AMBIGUITÀ: se più carte restano appaiate sulla stessa menzione (due 'Silva', due versioni senza indicazione)
  vengono restituite TUTTE con ambiguous=True. Mai una scelta silenziosa.
"""
import difflib
from dataclasses import dataclass, field

from . import config, text as T

MIN_CONF = 0.5
TIE = 0.1
_SKIP_SURNAME = {"jr", "sr", "junior", "senior"}


@dataclass
class Candidate:
    card_id: int
    name: str
    version: str
    position: str
    confidence: float
    start: int  # menzione nel testo originale
    end: int
    ambiguous: bool = False
    kind: str = ""  # full | alias | surname | fuzzy | first
    group: str = ""  # menzioni uguali = stesso gruppo (per riconoscere le alternative)
    cluster: list = field(default_factory=list)  # card_id delle alternative ambigue
    spans: list = field(default_factory=list)  # tutte le menzioni (inizio, fine) nel testo originale


def _tokens(name: str) -> list[str]:
    return [w for w, _, _ in T.words(T.norm(name))]


def _surname(toks: list[str]) -> str | None:
    core = [t for t in toks if t not in _SKIP_SURNAME]
    return core[-1] if len(core) >= 2 else None


def _version_hit(version: str, wordset: set[str], normed: str) -> bool:
    v = T.norm(version).strip()
    if v in set(config.research_config().get("generic_versions", [])):
        return False
    toks = [t for t in _tokens(version) if not t.isdigit()]
    return bool(toks) and all(t in wordset for t in toks)


def resolve(text: str, cards: list[dict]) -> list[Candidate]:
    """cards: dict con id, name, version, position. Ritorna i candidati (vuota se nessuna menzione)."""
    normed = T.norm(text)
    W = T.words(normed)
    if not W or not cards:
        return []
    wordset = {w for w, _, _ in W}
    toks = {c["id"]: _tokens(c["name"]) for c in cards}
    claimed = [False] * len(W)
    matches: dict[int, tuple[float, int, int, str]] = {}  # card_id -> (conf, wstart, wend_excl, kind) della prima menzione migliore
    spans: dict[int, list[tuple[int, int]]] = {}  # card_id -> tutte le menzioni di pari confidenza (indici di parola)

    def put(cid: int, conf: float, i: int, j: int, kind: str) -> None:
        if cid not in matches or conf > matches[cid][0]:
            matches[cid] = (conf, i, j, kind)
            spans[cid] = [(i, j)]
        elif conf == matches[cid][0] and kind == matches[cid][3]:
            spans[cid].append((i, j))

    def claim(i: int, j: int) -> None:
        for k in range(i, j):
            claimed[k] = True

    # 1) nome completo (sequenza consecutiva di parole)
    by_first: dict[str, list[dict]] = {}
    for c in cards:
        if toks[c["id"]]:
            by_first.setdefault(toks[c["id"]][0], []).append(c)
    for i, (w, _, _) in enumerate(W):
        for c in by_first.get(w, []):
            n = toks[c["id"]]
            if [x[0] for x in W[i:i + len(n)]] == n:
                put(c["id"], 1.0, i, i + len(n), "full")
    for cid, (_, i, j, _) in list(matches.items()):
        claim(i, j)

    # 2) soprannomi
    for alias, target in config.research_config().get("aliases", {}).items():
        a = _tokens(alias)
        tt = set(_tokens(target))
        for i in range(len(W) - len(a) + 1):
            if a and [x[0] for x in W[i:i + len(a)]] == a and not any(claimed[i:i + len(a)]):
                hit = [c for c in cards if tt <= set(toks[c["id"]])]
                for c in hit:
                    put(c["id"], 0.95, i, i + len(a), "alias")
                if hit:
                    claim(i, i + len(a))

    # 3) cognome / refuso / solo nome, solo su parole non ancora "spese"
    surnames: dict[str, list[dict]] = {}
    firsts: dict[str, list[dict]] = {}
    for c in cards:
        t = toks[c["id"]]
        s = _surname(t)
        if s and len(s) >= 3:
            surnames.setdefault(s, []).append(c)
        if len(t) >= 2 and len(t[0]) >= 4:
            firsts.setdefault(t[0], []).append(c)
    vocab = set()
    cc = config.criteria_config()
    for crit in cc["criteria"].values():
        for k in crit["keywords"]:
            vocab.update(_tokens(k))
    for i, (w, _, _) in enumerate(W):
        if claimed[i]:
            continue
        if w in surnames:
            for c in surnames[w]:
                put(c["id"], 0.75, i, i + 1, "surname")
        elif len(w) >= 5 and w not in vocab and w not in firsts:
            for s in difflib.get_close_matches(w, list(surnames), n=3, cutoff=0.88):
                r = difflib.SequenceMatcher(None, w, s).ratio()
                for c in surnames[s]:
                    put(c["id"], round(0.75 * r, 2), i, i + 1, "fuzzy")
        if w in firsts:
            for c in firsts[w]:
                put(c["id"], 0.5, i, i + 1, "first")

    by_id = {c["id"]: c for c in cards}
    scored: dict[int, float] = {}
    hit_ids = set()
    for cid, (conf, *_rest) in matches.items():
        if _version_hit(by_id[cid]["version"], wordset, normed):
            hit_ids.add(cid)
            conf = min(1.0, conf + 0.1)
        scored[cid] = conf
    # stesso giocatore in più versioni: se il testo nomina la versione, le altre scendono
    names: dict[str, list[int]] = {}
    for cid in scored:
        names.setdefault(T.norm(by_id[cid]["name"]).strip(), []).append(cid)
    for ids in names.values():
        if any(i in hit_ids for i in ids):
            for i in ids:
                if i not in hit_ids:
                    scored[i] = round(scored[i] * 0.6, 2)

    groups: dict[tuple[int, int], list[int]] = {}
    for cid, conf in scored.items():
        if conf >= MIN_CONF:
            _, i, j, _ = matches[cid]
            groups.setdefault((i, j), []).append(cid)
    out: list[Candidate] = []
    for (i, j), ids in groups.items():
        ids.sort(key=lambda x: (-scored[x], x))
        top = scored[ids[0]]
        cluster = [x for x in ids if scored[x] >= top - TIE]
        amb = len(cluster) > 1
        for x in cluster:
            conf, wi, wj, kind = matches[x]
            c = by_id[x]
            out.append(Candidate(card_id=x, name=c["name"], version=c["version"], position=c["position"],
                                 confidence=round(scored[x], 2), start=W[wi][1], end=W[wj - 1][2],
                                 ambiguous=amb, kind=kind, group=f"{W[wi][1]}-{W[wj - 1][2]}",
                                 cluster=[y for y in cluster if y != x],
                                 spans=[(W[a][1], W[b - 1][2]) for a, b in spans[x]]))
    out.sort(key=lambda c: (-c.confidence, c.start, c.card_id))
    return out
