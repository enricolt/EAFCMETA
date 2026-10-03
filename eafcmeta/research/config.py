"""Caricamento delle configurazioni della ricerca (research.json) e del vocabolario dei criteri (criteria.json)."""
import json
import os
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _read(name: str, env: str | None = None) -> dict:
    path = Path(os.environ.get(env, "")) if env and os.environ.get(env) else CONFIG_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"configurazione non valida o mancante: {path} ({e})") from e


@lru_cache(maxsize=1)
def research_config() -> dict:
    cfg = _read("research.json", "EAFCMETA_RESEARCH_CONFIG")
    for key in ("creators", "limits", "stance_phrases", "keyword_polarity"):
        if key not in cfg:
            raise RuntimeError(f"research.json: manca la sezione '{key}'")
    return cfg


@lru_cache(maxsize=1)
def criteria_config() -> dict:
    cfg = _read("criteria.json")
    if "criteria" not in cfg or "polarity" not in cfg:
        raise RuntimeError("criteria.json: sezioni 'criteria' e 'polarity' obbligatorie")
    return cfg


def reload() -> None:
    research_config.cache_clear()
    criteria_config.cache_clear()


def limit(name: str) -> int:
    return int(research_config()["limits"][name])


def channel_for(creator: str) -> dict | None:
    """Voce di research.json per un creator (confronto senza maiuscole), o None."""
    want = creator.strip().lower()
    for c in research_config()["creators"]:
        if c["name"].strip().lower() == want:
            return c
    return None
