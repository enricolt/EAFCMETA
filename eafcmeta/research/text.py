"""Utilità di testo (solo libreria standard): normalizzazione che conserva le posizioni dei caratteri,
tokenizzazione con radici (stemming leggero italiano/inglese) e segmentazione in frasi senza fidarsi della punteggiatura.

Tutto è lineare nella lunghezza del testo: nessuna espressione regolare con backtracking esponenziale (ReDoS).
"""
import re
import unicodedata
from bisect import bisect_left

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[a-z0-9+']+")
_TOKEN = re.compile(r"[a-z0-9+]+")
_APOS = "'’`´"
_HARD = re.compile(r"[.!?;…]")
_BLANK = re.compile(r"\n[ \t]*\n")
_TS = re.compile(r"(?m)^[ \t]*[\[(]?(\d{1,2}):(\d{2})(?::(\d{2}))?[\])]?[ \t]+")

MAX_TOKENS = 120_000          # oltre questo limite il testo viene troncato (difesa da testi giganti)
MAX_SENT_TOKENS = 40          # lunghezza massima di una "frase" quando manca la punteggiatura
PAUSE_SECONDS = 7             # salto tra due righe con timestamp considerato una pausa

# contrazioni inglesi: "isn't" -> not ; senza apostrofo ("isnt") idem
_NT_STEMS = {"isn", "aren", "wasn", "weren", "don", "doesn", "didn", "can", "couldn", "won", "wouldn", "shouldn",
             "haven", "hasn", "hadn", "ain"}
_NT_FORMS = {"isnt", "arent", "wasnt", "werent", "dont", "doesnt", "didnt", "cant", "cannot", "wont", "wouldnt",
             "couldnt", "shouldnt", "havent", "hasnt", "hadnt", "aint"}

# parole che aprono una nuova frase di parlato (transizioni): niente punteggiatura nelle trascrizioni automatiche
_BREAK_WORDS = {"allora", "passiamo", "andiamo", "dunque", "next", "veniamo", "ragazzi", "guys", "moving", "let"}
_SOFT_CUT = {"e", "che", "perche", "quindi", "and", "because", "but", "so", "then", "ma", "pero", "poi", "while"}

STOPWORDS = frozenset("""il lo la i gli le un uno una di del dello della dei degli delle da dal dalla in nel nella nei
nelle con su sul sulla per tra fra a al alla ai e ed o ma che chi cui non si ci mi ti vi ne se come piu questo questa
questi queste quello quella sono e ha hanno ho hai abbiamo era sia essere fa fanno the a an of to in on for with and or
but is are was were be been it this that these those i you he she we they at by from as has have had do does did not
my your his her our their me him them what which who so very just""".split())


def _norm_char(ch: str) -> str:
    d = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
    return d if len(d) == 1 else (ch.lower() if len(ch.lower()) == 1 else " ")


def norm(text: str) -> str:
    """Minuscolo senza accenti, della STESSA lunghezza dell'originale (gli indici restano confrontabili)."""
    return "".join(_norm_char(ch) for ch in text)


def clean(text: str, limit: int | None = None) -> str:
    """Toglie i caratteri di controllo, uniforma gli a capo e (opzionale) tronca."""
    t = _CTRL.sub(" ", text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return t[:limit] if limit else t


def words(normed: str) -> list[tuple[str, int, int]]:
    """Parole di un testo GIÀ normalizzato, con inizio e fine."""
    return [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(normed)]


# ---------------------------------------------------------------- radici ----

_SUP = ("issimo", "issima", "issimi", "issime")
_stem_cache: dict[str, str] = {}


def stem(w: str) -> str:
    """Radice leggera: toglie plurale inglese (-s), superlativo italiano (-issimo/a/i/e) e la vocale finale.
    'veloci'/'veloce' -> 'veloc'; 'ottime'/'ottimo' -> 'ottim'; 'fortissimo' -> 'fort'. Non è linguisticamente
    completa: serve solo a far coincidere flessioni di una stessa parola (il vocabolario passa dalla stessa funzione)."""
    r = _stem_cache.get(w)
    if r is not None:
        return r
    s = w
    if len(s) > 3 and not s.isdigit() and "+" not in s:
        if s.endswith(_SUP) and len(s) > 7:
            s = s[:-6]
        else:
            if len(s) >= 5 and s.endswith("s") and not s.endswith("ss"):
                s = s[:-1]
            if len(s) >= 4 and s[-1] in "aeiou":
                s = s[:-1]
    if len(_stem_cache) > 200_000:
        _stem_cache.clear()
    _stem_cache[w] = s
    return s


def is_superlative(w: str) -> bool:
    return len(w) > 7 and w.endswith(_SUP)


class Tok:
    """Un token: parola normalizzata, radice, posizione nel testo originale, flag superlativo."""
    __slots__ = ("w", "st", "a", "b", "sup")

    def __init__(self, w: str, a: int, b: int):
        self.w, self.a, self.b = w, a, b
        self.st = stem(w)
        self.sup = is_superlative(w)

    def __repr__(self):
        return f"Tok({self.w!r},{self.a}-{self.b})"


def blank_timestamps(normed: str) -> tuple[str, list[tuple[int, int]]]:
    """Sostituisce con spazi i timestamp a inizio riga ('12:03 testo', '[00:05] testo'); ritorna (testo, [(pos, secondi)])."""
    marks: list[tuple[int, int]] = []

    def rep(m: re.Match) -> str:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        sec = a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b
        marks.append((m.start(), sec))
        return " " * (m.end() - m.start())

    if ":" not in normed:
        return normed, marks
    return _TS.sub(rep, normed), marks


def tokenize(normed: str, limit: int = MAX_TOKENS) -> list[Tok]:
    """Token alfanumerici (con '+', es. 'playstyle+'); l'apostrofo separa ('l'animazione' -> 'l', 'animazione');
    le contrazioni inglesi negative diventano 'not'."""
    out: list[Tok] = []
    for m in _TOKEN.finditer(normed):
        w, a, b = m.group(0), m.start(), m.end()
        if w == "t" and out and out[-1].b == a - 1 and normed[a - 1] in _APOS and out[-1].w in _NT_STEMS:
            out[-1] = Tok("not", out[-1].a, b)
        else:
            out.append(Tok("not" if w in _NT_FORMS else w, a, b))
        if len(out) >= limit:
            break
    return out


def drop_repeats(toks: list[Tok], min_len: int = 6, window: int = 90) -> list[Tok]:
    """Toglie le ripetizioni ravvicinate di sequenze di almeno `min_len` token (sottotitoli automatici che si sovrappongono
    riga dopo riga, testo copiato due volte): ripetere non è una prova in più. Lineare: finestra scorrevole di hash."""
    n = len(toks)
    if n < 2 * min_len:
        return toks
    keep = [True] * n
    last: dict[int, int] = {}
    i = 0
    while i + min_len <= n:
        key = hash(tuple(t.st for t in toks[i:i + min_len]))
        p = last.get(key)
        if p is not None and 0 < i - p <= window and all(toks[p + t].st == toks[i + t].st for t in range(min_len)):
            ln = min_len
            while i + ln < n and p + ln < i and toks[p + ln].st == toks[i + ln].st:
                ln += 1
            for t in range(i, i + ln):
                keep[t] = False
            i += ln
            continue
        last[key] = i
        i += 1
    return [t for t, k in zip(toks, keep) if k] if not all(keep) else toks


def _newline_is_hard(text: str) -> bool:
    """I sottotitoli vanno a capo ogni ~40 caratteri a metà frase: l'a capo conta come fine frase solo se le righe
    sono lunghe (testo scritto) e non quando sembrano righe di sottotitoli."""
    if "\n" not in text:
        return False
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 3:
        return len(lines) > 1
    return sum(len(ln) for ln in lines) / len(lines) >= 70


def sentences(text: str, toks: list[Tok], marks: list[tuple[int, int]] | None = None,
              max_len: int = MAX_SENT_TOKENS) -> list[tuple[int, int]]:
    """Divide i token in 'frasi' [i, j) con euristiche: punteggiatura forte (se c'è), pause temporali dei timestamp,
    parole di transizione del parlato ('allora', 'passiamo a', 'ragazzi'...) e lunghezza massima."""
    n = len(toks)
    if n == 0:
        return []
    nl_hard = _newline_is_hard(text)
    starts = [t.a for t in toks]
    pause_idx: set[int] = set()
    if marks:
        prev = None
        for pos, sec in marks:
            if prev is not None and sec - prev >= PAUSE_SECONDS:
                pause_idx.add(bisect_left(starts, pos))
            prev = sec
    cuts = [0]
    last = 0
    for k in range(1, n):
        gap = text[toks[k - 1].b:toks[k].a]
        hard = False
        if gap:
            if _HARD.search(gap):
                hard = not (len(gap) <= 1 and toks[k - 1].w.isdigit() and toks[k].w.isdigit())  # 9.5 / 1,90
            elif "\n" in gap and (nl_hard or _BLANK.search(gap)):
                hard = True
        if not hard and k in pause_idx:
            hard = True
        if not hard and toks[k].w in _BREAK_WORDS and k - last >= 3:
            hard = True
        if not hard and k - last >= max_len and (toks[k].w in _SOFT_CUT or k - last >= max_len + 12):
            hard = True
        if hard:
            cuts.append(k)
            last = k
    cuts.append(n)
    return [(cuts[x], cuts[x + 1]) for x in range(len(cuts) - 1) if cuts[x + 1] > cuts[x]]


def looks_cased(text: str) -> bool:
    """True se il testo ha maiuscole 'normali' (nomi propri riconoscibili), non tutto minuscolo né tutto maiuscolo."""
    ws = [w for w in re.findall(r"[A-Za-zÀ-ÿ]+", text[:20000])]
    if len(ws) < 6:
        return False
    cap = sum(1 for w in ws if w[0].isupper())
    return 0.04 <= cap / len(ws) <= 0.45


def stopword_share(toks: list[Tok]) -> float:
    if not toks:
        return 0.0
    return sum(1 for t in toks if t.w in STOPWORDS) / len(toks)
