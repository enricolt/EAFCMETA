"""Versione di una carta: famiglia e valutazione. Funzioni pure (nessun accesso a db o rete).

`version` e' il testo normalizzato dalle fonti, di solito «famiglia + valutazione» ("Gold 86", "Icon 94", "TOTW 89").
Famiglia di versione (`version_family`) = `version` senza il numero finale, con questi alias unificati:

    Team of the Week -> TOTW,  Team of the Year -> TOTY,  Team of the Season -> TOTS,  Icons -> Icon,  Heroes -> Hero

Il resto resta com'e' (Gold, Silver, Bronze, Icon, Hero, TOTW, TOTY, Rare ...). Un testo vuoto o fatto solo di numeri
diventa "Altro". Il filtro `version=` di GET /catalog usa queste famiglie (senza distinguere maiuscole).
"""
from __future__ import annotations

import re

ALIASES = {"team of the week": "TOTW", "team of the year": "TOTY", "team of the season": "TOTS",
           "icons": "Icon", "base icon": "Icon", "all icons": "Icon", "heroes": "Hero"}
_TRAIL = re.compile(r"\s*\d+\s*$")


def version_family(version: str | None) -> str:
    base = re.sub(r"\s+", " ", _TRAIL.sub("", version or "")).strip()
    if not base:
        return "Altro"
    return ALIASES.get(base.lower(), base)


def rating_from_version(version: str | None) -> int | None:
    """Numero finale della versione ("Gold 86" -> 86), se plausibile (40-99)."""
    m = re.search(r"(\d{2})\s*$", version or "")
    return int(m.group(1)) if m and 40 <= int(m.group(1)) <= 99 else None


def prefixes_of(family: str) -> list[str]:
    """Tutti i prefissi (minuscoli) con cui il testo di `version` puo' iniziare per appartenere a `family`."""
    fam = family.strip()
    canon = ALIASES.get(fam.lower(), fam)
    return sorted({canon.lower(), fam.lower(), *[k for k, v in ALIASES.items() if v.lower() == canon.lower()]})
