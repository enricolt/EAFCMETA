"""Risoluzione della carta: da un testo libero alle carte del DB a cui si riferisce.

Solo libreria standard (difflib, unicodedata). Regole:
- nome completo = 1.0; soprannome (research.json) = 0.95; cognome = 0.75; cognome con refuso ~0.6-0.7; solo nome = 0.5;
- la versione citata (TOTY, Icon, ...) aggiunge +0.1 e scavalca le altre versioni dello stesso giocatore;
- AMBIGUITÀ: se più carte restano appaiate sulla stessa menzione (due 'Silva', due versioni senza indicazione)
  vengono restituite TUTTE con ambiguous=True. Mai una scelta silenziosa.
- Ogni candidato elenca TUTTE le sue menzioni (`mentions`/`spans`), non solo la migliore: chi estrae i pareri deve poter
  attribuire anche le frasi in cui il giocatore è citato per cognome dopo averlo presentato per nome intero.
- Un cognome condiviso da giocatori diversi ('Silva') si riferisce, se nel testo compare per esteso uno solo dei due,
  a quello presentato per nome intero (il più vicino prima della menzione).
- Refusi di riconoscimento vocale ('Halland', 'Mbape'): confronto anche su forma "fonetica" (h muta, doppie, k/c, y/i);
  confidenza sempre bassa (mai sopra 0.7) perché l'errore potrebbe essere una parola qualunque.
"""
import difflib
from dataclasses import dataclass, field

from . import config, criteria as C, text as T

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
    mentions: list = field(default_factory=list)  # (inizio, fine, confidenza, tipo) di ogni menzione


def _tokens(name: str) -> list[str]:
    return [t.w for t in T.tokenize(T.norm(name))]


def _surname(toks: list[str]) -> str | None:
    core = [t for t in toks if t not in _SKIP_SURNAME]
    return core[-1] if len(core) >= 2 else None


def _version_hit(version: str, wordset: set[str], normed: str) -> bool:
    v = T.norm(version).strip()
    if v in set(config.research_config().get("generic_versions", [])):
        return False
    toks = [t for t in _tokens(version) if not t.isdigit()]
    return bool(toks) and all(t in wordset for t in toks)


# cognomi che sono anche parole comuni: la menzione vale poco
_COMMON_SURNAMES = {"rice", "king", "young", "walker", "cole", "palmer", "gray", "grey", "white", "brown", "green", "black",
                    "best", "long", "short", "fast", "good", "will", "may", "bell", "lewis", "martin", "stone", "wood",
                    "hill", "fox", "fish", "bird", "pace", "mount", "ward"}
_FOLD = (("ph", "f"), ("ck", "k"), ("ch", "k"), ("gh", "g"), ("y", "i"), ("w", "v"), ("c", "k"), ("q", "k"), ("h", ""),
         ("x", "ks"), ("z", "s"), ("j", "i"))


def _fold(w: str) -> str:
    """Forma 'fonetica' grossolana per confrontare nomi stranieri trascritti a orecchio."""
    for a, b in _FOLD:
        w = w.replace(a, b)
    out = []
    for ch in w:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


class _Fuzzy:
    """Indice dei cognomi per cercare refusi senza confrontare ogni parola con ogni cognome."""

    def __init__(self, surnames: list[str]):
        self.raw: dict[tuple[str, int], list[str]] = {}
        self.fold: dict[tuple[str, int], list[tuple[str, str]]] = {}
        for s in surnames:
            self.raw.setdefault((s[0], len(s)), []).append(s)
            f = _fold(s)
            self.fold.setdefault((f[:1], len(f)), []).append((f, s))
        self.cache: dict[str, list[tuple[str, float]]] = {}

    def find(self, w: str) -> list[tuple[str, float]]:
        hit = self.cache.get(w)
        if hit is not None:
            return hit
        out: dict[str, float] = {}
        for L in range(len(w) - 2, len(w) + 3):
            for s in self.raw.get((w[0], L), ()):
                r = difflib.SequenceMatcher(None, w, s).ratio()
                if r >= 0.88:
                    out[s] = max(out.get(s, 0.0), r)
        fw = _fold(w)
        if len(fw) >= 4:
            for L in range(len(fw) - 1, len(fw) + 2):
                for f, s in self.fold.get((fw[:1], L), ()):
                    r = difflib.SequenceMatcher(None, fw, f).ratio()
                    if r >= 0.9:
                        out[s] = max(out.get(s, 0.0), 0.92 * r)
        res = sorted(out.items(), key=lambda x: -x[1])[:3]
        if len(self.cache) > 50000:
            self.cache.clear()
        self.cache[w] = res
        return res


def resolve(text: str, cards: list[dict]) -> list[Candidate]:
    """cards: dict con id, name, version, position. Ritorna i candidati (vuota se nessuna menzione)."""
    normed = T.norm(text)
    toks = T.tokenize(normed)
    W = [(t.w, t.a, t.b) for t in toks]
    if not W or not cards:
        return []
    wordset = {w for w, _, _ in W}
    toks_of = {c["id"]: _tokens(c["name"]) for c in cards}
    claimed = [False] * len(W)
    mentions: dict[int, list[tuple[float, int, int, str]]] = {}  # card_id -> [(conf, i, j_escl, kind)]

    def put(cid: int, conf: float, i: int, j: int, kind: str) -> None:
        mentions.setdefault(cid, []).append((conf, i, j, kind))

    def claim(i: int, j: int) -> None:
        for k in range(i, j):
            claimed[k] = True

    # 1) nome completo (sequenza consecutiva di parole)
    by_first: dict[str, list[dict]] = {}
    for c in cards:
        if toks_of[c["id"]]:
            by_first.setdefault(toks_of[c["id"]][0], []).append(c)
    full_spans = []
    for i, (w, _, _) in enumerate(W):
        for c in by_first.get(w, []):
            n = toks_of[c["id"]]
            if [x[0] for x in W[i:i + len(n)]] == n:
                put(c["id"], 1.0, i, i + len(n), "full")
                full_spans.append((i, i + len(n)))
    for i, j in full_spans:
        claim(i, j)

    # 2) soprannomi
    for alias, target in config.research_config().get("aliases", {}).items():
        a = _tokens(alias)
        tt = set(_tokens(target))
        for i in range(len(W) - len(a) + 1):
            if a and [x[0] for x in W[i:i + len(a)]] == a and not any(claimed[i:i + len(a)]):
                hit = [c for c in cards if tt <= set(toks_of[c["id"]])]
                for c in hit:
                    put(c["id"], 0.95, i, i + len(a), "alias")
                if hit:
                    claim(i, i + len(a))

    # 3) cognome / refuso / solo nome, solo su parole non ancora "spese"
    surnames: dict[str, list[dict]] = {}
    firsts: dict[str, list[dict]] = {}
    for c in cards:
        t = toks_of[c["id"]]
        s = _surname(t)
        if s and len(s) >= 3:
            surnames.setdefault(s, []).append(c)
        if len(t) >= 2 and len(t[0]) >= 4:
            firsts.setdefault(t[0], []).append(c)
    known = set(C.vocab().words) | set(T.STOPWORDS)
    for crit in config.criteria_config()["criteria"].values():
        for k in crit["keywords"]:
            known.update(_tokens(k))
    fz = _Fuzzy([s for s in surnames if len(s) >= 5 and s not in known])
    for i, (w, _, _) in enumerate(W):
        if claimed[i]:
            continue
        if w in surnames:
            conf = 0.5 if w in _COMMON_SURNAMES else 0.75
            for c in surnames[w]:
                put(c["id"], conf, i, i + 1, "surname")
        elif len(w) >= 5 and w not in known and w not in firsts and not w.isdigit():
            for s, r in fz.find(w):
                for c in surnames[s]:
                    put(c["id"], round(min(0.7, 0.75 * r), 2), i, i + 1, "fuzzy")
        if w in firsts:
            for c in firsts[w]:
                put(c["id"], 0.5, i, i + 1, "first")

    by_id = {c["id"]: c for c in cards}
    # un cognome condiviso da giocatori diversi: vale per chi è stato presentato per nome intero (il più vicino prima)
    pname = {cid: T.norm(by_id[cid]["name"]).strip() for cid in mentions}
    named_full = {cid: sorted(i for (_cf, i, _j, k) in ms if k in ("full", "alias")) for cid, ms in mentions.items()}
    named_full = {cid: v for cid, v in named_full.items() if v}
    if named_full:
        by_span: dict[tuple[int, int], list[int]] = {}
        for cid, ms in mentions.items():
            for (_cf, i, j, k) in ms:
                if k == "surname":
                    by_span.setdefault((i, j), []).append(cid)
        for (i, j), ids in by_span.items():
            names_here = {pname[x] for x in ids}
            if len(names_here) < 2:
                continue
            named = [x for x in ids if x in named_full]
            if not named or len({pname[x] for x in named}) == len(names_here):
                continue

            def dist(x, i=i):
                prev = [p for p in named_full[x] if p < i]
                return (i - max(prev)) if prev else 10 ** 6 + min(abs(p - i) for p in named_full[x])

            keep_name = pname[min(named, key=dist)]
            for x in ids:
                if pname[x] != keep_name:
                    mentions[x] = [m for m in mentions[x] if not (m[3] == "surname" and (m[1], m[2]) == (i, j))]
        mentions = {cid: ms for cid, ms in mentions.items() if ms}

    matches: dict[int, tuple[float, int, int, str]] = {}
    for cid, ms in mentions.items():
        matches[cid] = max(ms, key=lambda m: (m[0], -m[1]))
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
            ms = sorted(mentions[x], key=lambda m: m[1])
            out.append(Candidate(card_id=x, name=c["name"], version=c["version"], position=c["position"],
                                 confidence=round(scored[x], 2), start=W[wi][1], end=W[wj - 1][2],
                                 ambiguous=amb, kind=kind, group=f"{W[wi][1]}-{W[wj - 1][2]}",
                                 cluster=[y for y in cluster if y != x],
                                 spans=[(W[a][1], W[b - 1][2]) for (_cf, a, b, _k) in ms],
                                 mentions=[(W[a][1], W[b - 1][2], cf, k) for (cf, a, b, k) in ms]))
    out.sort(key=lambda c: (-c.confidence, c.start, c.card_id))
    return out
