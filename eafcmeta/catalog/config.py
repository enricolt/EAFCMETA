"""Configurazione del catalogo: config/catalog.json (default, nel repo) + catalog.local.json (personale, ignorato da git).

Il file personale sta accanto a local.json (stessa cartella, quindi isolato nei test da EAFCMETA_LOCAL_CONFIG) oppure dove
indica EAFCMETA_CATALOG_LOCAL_CONFIG; il file di base si puo' spostare con EAFCMETA_CATALOG_CONFIG. Nessuna cache: ogni
lettura vede le modifiche.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .. import fetch

CONFIG_DIR = Path(__file__).parent.parent / "config"
# Host consentiti per l'elenco e le pagine: FUT.GG (default) e FUTBIN (di solito bloccato dal sito: errore chiaro, nessun aggiramento)
HOSTS = (*fetch.ALLOWED_HOSTS, "futbin.com", "www.futbin.com")
LIST_URLS_MAX = 5
PAGES_RANGE = (1, 20)
LOOKBACK_RANGE = (1, 365)


class CatalogConfigError(ValueError):
    pass


def base_path() -> Path:
    return Path(os.environ["EAFCMETA_CATALOG_CONFIG"]) if os.environ.get("EAFCMETA_CATALOG_CONFIG") else CONFIG_DIR / "catalog.json"


def local_path() -> Path:
    if os.environ.get("EAFCMETA_CATALOG_LOCAL_CONFIG"):
        return Path(os.environ["EAFCMETA_CATALOG_LOCAL_CONFIG"])
    from .. import scoring
    return scoring.local_config_path().with_name("catalog.local.json")


def _read(path: Path, required: bool) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise CatalogConfigError(f"configurazione del catalogo mancante: {path}") from None
        return {}
    except (OSError, json.JSONDecodeError) as e:
        raise CatalogConfigError(f"configurazione del catalogo non valida: {path.name} ({e})") from None
    if not isinstance(data, dict):
        raise CatalogConfigError(f"{path.name}: deve essere un oggetto JSON")
    return data


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def check_url(u) -> str:
    if not isinstance(u, str) or len(u) > 300:
        raise CatalogConfigError("list_urls: ogni indirizzo deve essere un testo di al massimo 300 caratteri")
    u = u.strip()
    try:
        fetch.validate_url(u, HOSTS)
    except ValueError:
        raise CatalogConfigError(f"indirizzo non consentito: solo https verso {', '.join(h for h in HOSTS if h.startswith('www.'))} "
                                 f"(ricevuto: {u[:80]})") from None
    return u


def _int(name: str, v, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        raise CatalogConfigError(f"{name}: deve essere un numero intero")
    if not lo <= v <= hi:
        raise CatalogConfigError(f"{name}: deve essere tra {lo} e {hi}")
    return int(v)


def check_patch(patch: dict) -> dict:
    """Valida le sole voci modificabili da PUT /catalog/config; ritorna i valori normalizzati."""
    out: dict = {}
    if patch.get("list_urls") is not None:
        urls = patch["list_urls"]
        if not isinstance(urls, list) or len(urls) > LIST_URLS_MAX:
            raise CatalogConfigError(f"list_urls: elenco di al massimo {LIST_URLS_MAX} indirizzi")
        out["list_urls"] = list(dict.fromkeys(check_url(u) for u in urls))
    if patch.get("pages_per_update") is not None:
        out["pages_per_update"] = _int("pages_per_update", patch["pages_per_update"], *PAGES_RANGE)
    if patch.get("lookback_days") is not None:
        out["lookback_days"] = _int("lookback_days", patch["lookback_days"], *LOOKBACK_RANGE)
    return out


def validate(cfg: dict) -> dict:
    urls = cfg.get("list_urls")
    if not isinstance(urls, list):
        raise CatalogConfigError("catalog.json: list_urls deve essere un elenco")
    cfg["list_urls"] = [check_url(u) for u in urls]
    cfg["pages_per_update"] = _int("pages_per_update", cfg.get("pages_per_update"), *PAGES_RANGE)
    cfg["lookback_days"] = _int("lookback_days", cfg.get("lookback_days"), *LOOKBACK_RANGE)
    cfg["setup_confirmed"] = bool(cfg.get("setup_confirmed"))
    if not isinstance(cfg.get("page_param"), str) or not cfg["page_param"]:
        raise CatalogConfigError("catalog.json: page_param deve essere un testo non vuoto")
    d = cfg.get("delay_seconds")
    if isinstance(d, bool) or not isinstance(d, (int, float)) or not 0 <= d <= 120:
        raise CatalogConfigError("catalog.json: delay_seconds deve essere tra 0 e 120")
    if cfg.get("price_source") not in ("list", "list+cards"):
        raise CatalogConfigError("catalog.json: price_source deve essere 'list' o 'list+cards'")
    lim = cfg.get("limits")
    if not isinstance(lim, dict):
        raise CatalogConfigError("catalog.json: manca 'limits'")
    for k, hi in (("max_pages", 500), ("max_new_cards", 5000), ("max_price_pages", 500), ("max_requests", 5000), ("max_consecutive_errors", 20)):
        lim[k] = _int(f"limits.{k}", lim.get(k), 1 if k != "max_new_cards" else 0, hi)
    cfg["curve_max_points"] = _int("curve_max_points", cfg.get("curve_max_points"), 50, 5000)
    sm = cfg.get("summary")
    if not isinstance(sm, dict):
        raise CatalogConfigError("catalog.json: manca 'summary'")
    sm["strong_stat"] = _int("summary.strong_stat", sm.get("strong_stat"), 50, 99)
    sm["elite_stat"] = _int("summary.elite_stat", sm.get("elite_stat"), sm["strong_stat"], 99)
    cfg["check_robots"] = bool(cfg.get("check_robots", True))
    return cfg


def load() -> dict:
    """Configurazione effettiva (default + personale), validata."""
    return validate(_merge(_read(base_path(), True), _read(local_path(), False)))


def needs_setup(cfg: dict) -> bool:
    """L'indirizzo dell'elenco 'ultime uscite' va ancora indicato/confermato."""
    return not cfg["list_urls"] or not cfg["setup_confirmed"]


def save_local(patch: dict) -> dict:
    """Unisce `patch` (gia' validato) al file personale e ritorna la configurazione effettiva."""
    path = local_path()
    merged = _merge(_read(path, False), patch)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return load()


def public(cfg: dict) -> dict:
    """Il blocco `config` di GET /catalog/status."""
    return {"list_urls": cfg["list_urls"], "pages_per_update": cfg["pages_per_update"], "lookback_days": cfg["lookback_days"],
            "needs_setup": needs_setup(cfg)}


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
