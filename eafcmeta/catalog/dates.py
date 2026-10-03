"""Date di uscita dei siti -> testo ISO UTC ("2026-09-25T17:01:00Z"). Funzioni pure.

Formati noti (verificati su pagine vere, ottobre 2026):
- FUT.GG, dopo l'etichetta «Added On»:  "Sep 25, 2026, 5:01 PM UTC"  (anche "Aug 18, 2026, 2:17 PM UTC");
- FUTBIN: «Release date:  2026-09-11»  (solo il giorno: l'ora diventa 00:00:00Z) oppure nessuna data.
In piu': ISO gia' pronto ("2026-09-11", "2026-09-11T10:00:00Z", "2026-09-11 10:00") e "Sep 25, 2026" senza ora.
Un testo non riconosciuto da' None (mai un errore: la data e' un dato accessorio).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_EN = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})(?:,?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?\s*(UTC|GMT|Z)?$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|UTC|\+00:?00)?$")
_PREFIX = re.compile(r"^\s*(?:Release date|Added On)\s*:?\s*", re.I)


def _build(y, mo, d, h=0, mi=0, s=0) -> str | None:
    try:
        return datetime(y, mo, d, h, mi, s).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def parse_release_date(text: str | None) -> str | None:
    t = _PREFIX.sub("", (text or "").strip())
    if not t:
        return None
    m = _ISO.match(t)
    if m:
        return _build(*(int(x) if x else 0 for x in m.groups()[:6]))
    m = _EN.match(t)
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon is None:
            return None
        h, mi, s, ap = (int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0), (m.group(7) or "").lower())
        if ap:
            if not 1 <= h <= 12:
                return None
            h = h % 12 + (12 if ap == "pm" else 0)
        return _build(int(m.group(3)), mon, int(m.group(2)), h, mi, s)
    return None


def day_bounds(day: str) -> tuple[str, str] | None:
    """'2026-09-25' -> (inizio del giorno, inizio del giorno dopo) in ISO UTC; None se non e' una data valida."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        return None
    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    f = "%Y-%m-%dT%H:%M:%SZ"
    return d.strftime(f), (d + timedelta(days=1)).strftime(f)
