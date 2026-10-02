"""Doppio clic su Windows: apre l'app senza finestra di console (i messaggi vanno in app.log)."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
log = open(root / "app.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
os.chdir(root)
sys.path.insert(0, str(root))
sys.argv = [sys.argv[0]]

import start  # noqa: E402

start.main()
