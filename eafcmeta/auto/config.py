"""Configurazione della raccolta automatica: auto.json (default, nel repo) + auto.local.json (personale, ignorato da git)
e l'elenco dei pro (research.json + pros.json). Niente è memorizzato in cache: ogni lettura vede le modifiche.
"""
import json
import os
from pathlib import Path

from .errors import AutoConfigError

CONFIG_DIR = Path(__file__).parent.parent / "config"


def base_path() -> Path:
    return Path(os.environ["EAFCMETA_AUTO_CONFIG"]) if os.environ.get("EAFCMETA_AUTO_CONFIG") else CONFIG_DIR / "auto.json"


def local_path() -> Path:
    if os.environ.get("EAFCMETA_AUTO_LOCAL_CONFIG"):
        return Path(os.environ["EAFCMETA_AUTO_LOCAL_CONFIG"])
    return base_path().with_name("auto.local.json")


def pros_path() -> Path:
    return Path(os.environ["EAFCMETA_PROS_CONFIG"]) if os.environ.get("EAFCMETA_PROS_CONFIG") else CONFIG_DIR / "pros.json"


def _read(path: Path, required: bool) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise AutoConfigError(f"configurazione mancante: {path}") from None
        return {}
    except (OSError, json.JSONDecodeError) as e:
        raise AutoConfigError(f"configurazione non valida: {path} ({e})") from None
    if not isinstance(data, dict):
        raise AutoConfigError(f"{path.name}: deve essere un oggetto JSON")
    return data


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# (sezione, chiave) -> (tipo, minimo, massimo); sezione None = livello radice
RULES = {
    (None, "enabled"): (bool, None, None), (None, "run_on_start"): (bool, None, None),
    (None, "interval_hours"): (float, 0.25, 24 * 30), (None, "start_delay_seconds"): (float, 0, 3600),
    (None, "poll_seconds"): (float, 0.01, 3600),
    ("limits", "max_opinions_per_run"): (int, 0, 500), ("limits", "max_videos_per_run"): (int, 0, 500),
    ("limits", "max_videos_per_channel"): (int, 1, 50), ("limits", "max_new_cards"): (int, 0, 500),
    ("limits", "max_channels_discovered_per_run"): (int, 0, 100), ("limits", "max_requests_per_run"): (int, 1, 2000),
    ("thresholds", "min_confidence"): (float, 0.0, 1.0), ("thresholds", "min_card_confidence"): (float, 0.0, 1.0),
    ("thresholds", "max_opinions_per_video"): (int, 1, 50),
    ("thresholds", "channel_name_similarity"): (float, 0.5, 1.0), ("thresholds", "min_subscribers"): (int, 0, 10**9),
    ("thresholds", "min_fc_videos"): (int, 0, 50),
    ("youtube", "enabled"): (bool, None, None), ("youtube", "lookback_days"): (int, 1, 365),
    ("youtube", "request_delay_seconds"): (float, 0, 600), ("youtube", "timeout_seconds"): (float, 5, 1200),
    ("youtube", "min_duration_seconds"): (int, 0, 7200), ("youtube", "retry_error_hours"): (float, 0, 24 * 30),
    ("youtube", "retry_no_transcript_days"): (int, 0, 60), ("youtube", "discovery_retry_days"): (int, 0, 365),
    ("youtube", "search_results"): (int, 1, 50), ("youtube", "max_consecutive_errors"): (int, 1, 100),
    ("youtube", "words_per_line"): (int, 3, 60),
    ("catalog", "enabled"): (bool, None, None),
    ("futgg", "enabled"): (bool, None, None), ("futgg", "pages"): (int, 1, 20),
    ("futgg", "delay_seconds"): (float, 0, 120), ("futgg", "check_robots"): (bool, None, None),
}


def check_value(section, key, value):
    """Valida un singolo valore; ritorna il valore normalizzato o alza AutoConfigError."""
    if (section, key) not in RULES:
        raise AutoConfigError(f"parametro sconosciuto: {key}")
    typ, lo, hi = RULES[(section, key)]
    name = f"{section}.{key}" if section else key
    if typ is bool:
        if not isinstance(value, bool):
            raise AutoConfigError(f"{name}: deve essere vero/falso")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise AutoConfigError(f"{name}: deve essere un numero")
    if typ is int and value != int(value):
        raise AutoConfigError(f"{name}: deve essere un numero intero")
    if not lo <= value <= hi:
        raise AutoConfigError(f"{name}: deve essere tra {lo:g} e {hi:g}")
    return int(value) if typ is int else float(value)


def validate(cfg: dict) -> dict:
    for (section, key) in RULES:
        src = cfg.get(section, {}) if section else cfg
        if not isinstance(src, dict) or key not in src:
            raise AutoConfigError(f"auto.json: manca {section + '.' if section else ''}{key}")
        src[key] = check_value(section, key, src[key])
    yt, fut = cfg["youtube"], cfg["futgg"]
    langs = yt.get("languages")
    if not (isinstance(langs, list) and langs and all(isinstance(x, str) and x for x in langs)):
        raise AutoConfigError("youtube.languages: elenco di codici lingua (es. [\"it\", \"en\"])")
    urls = fut.get("list_urls")
    if not (isinstance(urls, list) and all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in urls)):
        raise AutoConfigError("futgg.list_urls: elenco di indirizzi http(s)")
    if not isinstance(fut.get("page_param", "page"), str) or not fut.get("page_param", "page"):
        raise AutoConfigError("futgg.page_param: testo non vuoto")
    fut.setdefault("page_param", "page")
    return cfg


def load() -> dict:
    """Configurazione effettiva (default + personale), validata."""
    return validate(_merge(_read(base_path(), True), _read(local_path(), False)))


def save_local(patch: dict) -> dict:
    """Unisce `patch` (già validato) a auto.local.json e ritorna la configurazione effettiva."""
    current = _read(local_path(), False)
    merged = _merge(current, patch)
    path = local_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return load()


def env_enabled() -> bool:
    """La variabile EAFCMETA_AUTO=0 spegne sempre tutto (scheduler compreso)."""
    return os.environ.get("EAFCMETA_AUTO", "1").strip() != "0"


def pros() -> tuple[list[dict], list[str]]:
    """Voci di pros.json valide + avvisi. File assente, vuoto o rotto => elenco vuoto (mai un errore)."""
    path, warns = pros_path(), []
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return [], warns
    if not raw:
        return [], warns
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [f"{path.name} non è JSON valido ({e.msg}): ignorato"]
    entries = data.get("pros", data.get("creators", [])) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return [], [f"{path.name}: atteso un elenco di pro"]
    out = []
    for i, e in enumerate(entries):
        name = e.get("name") if isinstance(e, dict) else None
        if not isinstance(name, str) or not name.strip():
            warns.append(f"{path.name}: voce {i + 1} senza 'name', saltata")
            continue
        out.append({"name": name.strip(),
                    "channel_id": str(e.get("youtube_channel_id") or "").strip(),
                    "handle": str(e.get("youtube_handle") or "").strip()})
    return out, warns


def creators() -> tuple[list[dict], list[str]]:
    """Creator da seguire = research.json (creators) + pros.json, uniti per nome (senza maiuscole).
    Campi: name, channel_id, handle (vuoti = da scoprire). pros.json vince sui campi che compila."""
    from ..research import config as rc
    merged: dict[str, dict] = {}
    for c in rc.research_config().get("creators", []):
        if isinstance(c, dict) and str(c.get("name", "")).strip():
            merged[c["name"].strip().lower()] = {"name": c["name"].strip(), "channel_id": str(c.get("channel_id") or "").strip(),
                                                 "handle": str(c.get("handle") or "").strip()}
    p, warns = pros()
    for e in p:
        cur = merged.setdefault(e["name"].lower(), {"name": e["name"], "channel_id": "", "handle": ""})
        for k in ("channel_id", "handle"):
            if e[k]:
                cur[k] = e[k]
    return list(merged.values()), warns
