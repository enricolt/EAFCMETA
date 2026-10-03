"""Errori della raccolta automatica (messaggi in italiano, adatti all'utente)."""


class AutoError(Exception):
    pass


class AutoConfigError(AutoError):
    """Configurazione non valida (auto.json / auto.local.json / pros.json)."""


class StopBatch(AutoError):
    """Il lotto si ferma subito (blocco, limite, tetto): il resto dell'esecuzione prosegue con gli altri passi."""


class YtDlpMissing(StopBatch):
    """yt-dlp non è installato (pip install -r requirements.txt)."""


class YtBlocked(StopBatch):
    """YouTube ha limitato o bloccato le richieste: niente insistenza."""


class RequestCapReached(StopBatch):
    """Tetto di richieste per esecuzione raggiunto (gentilezza verso il servizio)."""


class YtVideoError(AutoError):
    """Errore su un singolo video (privato, non disponibile, senza dati...): il lotto continua."""
