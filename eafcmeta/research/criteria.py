"""Estrazione dei CRITERI (chiavi di criteria.json) con polarità +1/-1, offline e deterministica.

Regole: il testo è diviso in clausole (punto, ';', 'ma', 'però', 'mentre'). In ogni clausola una parola chiave di un
criterio prende la polarità (a) dalla parola chiave stessa se ne porta una ('lento' = -1, 'veloce' = +1, vedi
research.json), altrimenti (b) dalla parola positiva/negativa più vicina nella clausola. Una negazione ('non',
'mica', 'senza'...) nelle 3 parole precedenti inverte il segno. Per ogni criterio si tiene il segno netto (parità = scartato).
Le parole di polarità non usate da nessun criterio sono restituite a parte ('libere'): servono a stimare la stance.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config, text as T

NEGATIONS = {"non", "mica", "senza", "niente", "nemmeno", "mai"}
_CLAUSE = re.compile(r"[.!?;\n]+|\b(?:ma|pero|mentre|anche se)\b")


def _rx(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9+])" + re.escape(T.norm(phrase)) + r"(?![a-z0-9+])")


@dataclass(frozen=True)
class Vocab:
    criteria: tuple  # (key, kw_norm, regex, inherent_polarity|0)
    polwords: tuple  # (word_norm, regex, polarity)


@lru_cache(maxsize=1)
def vocab() -> Vocab:
    cc, rc = config.criteria_config(), config.research_config()
    inherent = {T.norm(w): 1 for w in rc["keyword_polarity"].get("positive", [])}
    inherent.update({T.norm(w): -1 for w in rc["keyword_polarity"].get("negative", [])})
    crit = []
    for key, spec in cc["criteria"].items():
        for kw in spec["keywords"]:
            n = T.norm(kw)
            crit.append((key, n, _rx(n), inherent.get(n, 0)))
    crit.sort(key=lambda x: -len(x[1]))  # frasi lunghe prima
    pol = [(T.norm(w), _rx(w), +1) for w in cc["polarity"]["positive"]]
    pol += [(T.norm(w), _rx(w), -1) for w in cc["polarity"]["negative"]]
    pol.sort(key=lambda x: -len(x[0]))
    return Vocab(tuple(crit), tuple(pol))


def reset_cache() -> None:
    vocab.cache_clear()


def _negated(clause: str, start: int) -> bool:
    before = re.findall(r"[a-z0-9+']+", clause[:start])[-3:]
    return any(w in NEGATIONS for w in before)


def clauses(normed: str) -> list[tuple[int, int]]:
    out, last = [], 0
    for m in _CLAUSE.finditer(normed):
        if m.start() > last:
            out.append((last, m.start()))
        last = m.end()
    if last < len(normed):
        out.append((last, len(normed)))
    return out


@dataclass
class Analysis:
    criteria: list  # [(key, +1|-1)]
    free_polarity: int  # somma delle parole di polarità non legate a un criterio
    evidence: int  # numero di segnali trovati (criteri + parole libere)


def analyze(text: str) -> Analysis:
    v = vocab()
    normed = T.norm(text)
    net: dict[str, int] = {}
    free, evidence = 0, 0
    for a, b in clauses(normed):
        cl = normed[a:b]
        hits = []  # (start, end, key, inherent)
        taken: list[tuple[int, int]] = []
        for key, kw, rx, inh in v.criteria:
            for m in rx.finditer(cl):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                if kw == "cm" and not re.search(r"\d\s*$", cl[:m.start()]):
                    continue  # 'CM' = ruolo centrocampista, non altezza
                taken.append((m.start(), m.end()))
                hits.append((m.start(), m.end(), key, inh))
        pols = []  # (start, end, polarity)
        for w, rx, p in v.polwords:
            for m in rx.finditer(cl):
                if not any(m.start() < e and s < m.end() for s, e, _ in pols):
                    pols.append((m.start(), m.end(), p))
        used = set()
        for s, e, key, inh in hits:
            for i, (ps, pe, _) in enumerate(pols):  # la parola chiave che è anch'essa polarità ('lento') è già contata
                if ps < e and s < pe:
                    used.add(i)
            if inh:
                p = -inh if _negated(cl, s) else inh
            else:
                cand = [(max(ps - e, s - pe, 0), i) for i, (ps, pe, _) in enumerate(pols) if not (ps < e and s < pe)]  # distanza tra i bordi
                if not cand:
                    continue
                _, i = min(cand)
                used.add(i)
                p = pols[i][2]
                p = -p if _negated(cl, pols[i][0]) else p
            net[key] = net.get(key, 0) + p
            evidence += 1
        for i, (ps, pe, p) in enumerate(pols):
            if i not in used:
                free += -p if _negated(cl, ps) else p
                evidence += 1
    crit = [(k, 1 if n > 0 else -1) for k, n in net.items() if n != 0]
    return Analysis(criteria=crit, free_polarity=free, evidence=evidence)


def extract_criteria(text: str) -> list[tuple[str, int]]:
    """Solo i criteri [(chiave, +1|-1)] citati nel testo."""
    return analyze(text).criteria


def valid_keys() -> set[str]:
    return set(config.criteria_config()["criteria"])
