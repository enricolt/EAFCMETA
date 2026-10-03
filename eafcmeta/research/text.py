"""Utilità di testo (solo libreria standard): normalizzazione che conserva le posizioni dei caratteri."""
import re
import unicodedata

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[a-z0-9+']+")


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
