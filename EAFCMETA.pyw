"""Doppio clic su Windows: apre l'app senza finestra di console (i messaggi vanno in app.log).

Sotto pythonw stdout/stderr non esistono: li reindirizzo su un file di log. Se la cartella non è scrivibile
il log va in una cartella temporanea; se nemmeno quella, i messaggi vengono scartati (l'app parte lo stesso).
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

root = Path(__file__).resolve().parent


def open_log():
    for d in (root, Path(tempfile.gettempdir())):
        try:
            return open(d / "app.log", "a", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            continue
    return open(os.devnull, "w", encoding="utf-8")


log = open_log()
sys.stdout = sys.stderr = log
try:
    os.chdir(root)
except OSError:
    pass
sys.path.insert(0, str(root))
sys.argv = [sys.argv[0]]  # nessuna opzione: avvio normale (per le opzioni usa python start.py da terminale)

try:
    import start

    start.main([])
except SystemExit as e:
    if e.code not in (None, 0):
        print(f"[errore] {e.code}")
except Exception:  # noqa: BLE001 - lascia traccia nel log invece di sparire in silenzio
    traceback.print_exc()
