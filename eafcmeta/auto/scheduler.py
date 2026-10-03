"""Pianificazione: un thread demone controlla ogni `poll_seconds` se è ora di eseguire la raccolta.

La logica è in `Scheduler.tick()` (orologio, configurazione e lavoro INIETTABILI: i test non aspettano mai). Il thread è
avviato dal lifespan dell'app solo se "enabled" è vero E la variabile EAFCMETA_AUTO non vale "0".
Le esecuzioni non si sovrappongono: un lock in memoria (stesso processo) + la riga 'in_corso' in `auto_runs` (tra processi).
"""
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from . import config, runner
from .errors import AutoConfigError

JobResult = dict


class Scheduler:
    MIN_RETRY = 300  # secondi tra due tentativi consecutivi

    def __init__(self, job: Callable[[str], JobResult], clock: Callable[[], float] = time.time,
                 cfg_loader: Callable[[], dict] = config.load, last_started: Callable[[], float | None] = lambda: None,
                 log=print):
        self.job, self.clock, self.cfg_loader, self.last_started, self.log = job, clock, cfg_loader, last_started, log
        self.started_at: float | None = None
        self.next_due: float | None = None
        self.last_result: JobResult | None = None
        self.lock = threading.Lock()
        self._attempt: float | None = None

    def due_time(self, cfg: dict) -> float:
        """Istante (epoch) della prossima esecuzione."""
        interval = cfg["interval_hours"] * 3600
        last = self.last_started()
        start = self.started_at if self.started_at is not None else self.clock()
        ready = start + cfg["start_delay_seconds"]
        if last is None:  # mai eseguita: all'avvio (dopo il ritardo) oppure dopo un intervallo
            return ready if cfg["run_on_start"] else start + interval
        due = last + interval
        # già scaduta all'avvio: parte subito (dopo il ritardo) solo con run_on_start, altrimenti dopo un intervallo
        return max(due, ready) if cfg["run_on_start"] else max(due, start + interval)

    def tick(self) -> JobResult | None:
        """Un controllo. Esegue il lavoro (in modo sincrono) se è il momento; ritorna il risultato o None."""
        try:
            cfg = self.cfg_loader()
        except AutoConfigError as e:
            self.log(f"[auto] configurazione non valida, nessuna esecuzione: {e}")
            return None
        now = self.clock()
        if self.started_at is None:
            self.started_at = now
        if not cfg["enabled"]:
            self.next_due = None
            return None
        self.next_due = self.due_time(cfg)
        if now < self.next_due:
            return None
        if self._attempt is not None and now < self._attempt + self.MIN_RETRY:
            return None  # un tentativo appena fatto (anche saltato o fallito): non si insiste
        if not self.lock.acquire(blocking=False):
            return None
        self._attempt = now
        try:
            self.last_result = self.job("pianificata")
        except Exception as e:  # noqa: BLE001 - il thread non deve morire
            self.last_result = {"status": "errore", "summary": {"errori": [f"{type(e).__name__}: {e}"]}}
            self.log(f"[auto] esecuzione fallita: {e}")
        finally:
            self.lock.release()
        # dopo un'esecuzione (anche saltata/fallita) si riparte dall'ultimo avvio registrato, non si insiste
        self.next_due = None
        return self.last_result


class AutoService:
    """Tiene il thread demone e l'avvio manuale in background (usato da lifespan e API)."""

    def __init__(self):
        self._sched: Scheduler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._manual = threading.Lock()
        self._guard = threading.Lock()

    # --- lavoro reale ---
    @staticmethod
    def _real_job(trigger: str) -> JobResult:
        return runner.run_once()

    @staticmethod
    def _last_started() -> float | None:
        from .. import db
        conn = db.connect()
        try:
            return runner.last_started_epoch(conn)
        finally:
            conn.close()

    # --- thread ---
    @property
    def thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def next_due(self) -> float | None:
        return self._sched.next_due if self._sched else None

    def start_if_enabled(self) -> bool:
        """Avvia il thread demone se enabled (config) E EAFCMETA_AUTO != '0'. Idempotente."""
        with self._guard:
            if self.thread_alive:
                return True
            if not config.env_enabled():
                return False
            try:
                if not config.load()["enabled"]:
                    return False
            except AutoConfigError as e:
                print(f"[auto] configurazione non valida, raccolta non avviata: {e}")
                return False
            self._stop.clear()
            self._sched = Scheduler(self._real_job, last_started=self._last_started)
            poll = max(1.0, float(config.load()["poll_seconds"]))
            self._thread = threading.Thread(target=self._loop, args=(poll,), name="eafcmeta-auto", daemon=True)
            self._thread.start()
            return True

    def _loop(self, poll: float) -> None:
        while not self._stop.wait(poll):
            self._sched.tick()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2)
        self._thread = None

    # --- avvio manuale ---
    @property
    def running(self) -> bool:
        if self._manual.locked() or (self._sched and self._sched.lock.locked()):
            return True
        from .. import db
        conn = db.connect()
        try:
            return runner.is_running(conn)
        finally:
            conn.close()

    def run_async(self, job: Callable[[str], JobResult] | None = None) -> bool:
        """Avvia ora in background. False se un'esecuzione è già in corso."""
        if self.running or not self._manual.acquire(blocking=False):
            return False
        job = job or self._real_job

        def work():
            try:
                job("manuale")
            except Exception as e:  # noqa: BLE001
                print(f"[auto] esecuzione manuale fallita: {e}")
            finally:
                self._manual.release()

        threading.Thread(target=work, name="eafcmeta-auto-run", daemon=True).start()
        return True


service = AutoService()


def utc_iso(epoch: float | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M")
