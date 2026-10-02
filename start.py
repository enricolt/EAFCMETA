#!/usr/bin/env python3
"""Avvio locale: controlla gli aggiornamenti su GitHub, aggiorna la copia, poi lancia l'app.

Uso:  python start.py [--no-update] [--check] [--lan] [--port 8000] [--no-browser]
  --check    solo controllo/aggiornamento, non avvia il server
  --lan      ascolta su tutta la rete (per gli amici in LAN); default solo questo PC
"""
import argparse
import hashlib
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def update() -> None:
    if not (ROOT / ".git").exists():
        print("[update] non è una copia git, salto.")
        return
    f = git("fetch", "--quiet")
    if f.returncode:
        print("[update] offline o GitHub non raggiungibile: uso la versione locale.")
        return
    behind = git("rev-list", "--count", "HEAD..@{u}")
    if behind.returncode:
        print("[update] nessun branch remoto collegato, salto.")
        return
    n = int(behind.stdout.strip() or 0)
    if n == 0:
        print("[update] già aggiornato.")
        return
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        print(f"[update] ci sono {n} aggiornamenti ma hai modifiche locali non salvate: non aggiorno.")
        return
    p = git("pull", "--ff-only", "--quiet")
    if p.returncode:
        print("[update] impossibile aggiornare senza conflitti:\n" + p.stderr.strip())
        return
    print(f"[update] aggiornato ({n} nuovi commit):")
    print(git("log", "--oneline", f"-{min(n, 5)}").stdout.rstrip())


def install_deps() -> None:
    req = ROOT / "requirements.txt"
    stamp = ROOT / ".deps_hash"
    h = hashlib.sha256(req.read_bytes()).hexdigest()
    if stamp.exists() and stamp.read_text() == h:
        return
    print("[deps] installo le dipendenze...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)])
    if r.returncode == 0:
        stamp.write_text(h)
    else:
        sys.exit("[deps] installazione fallita.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lan", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    if not a.no_update:
        update()
    if a.check:
        return
    install_deps()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    url = f"http://localhost:{a.port}"
    if not a.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"[run] {url}  (Ctrl+C per fermare)")
    subprocess.run([sys.executable, "-m", "uvicorn", "eafcmeta.api:app", "--host", host, "--port", str(a.port)], cwd=ROOT)


if __name__ == "__main__":
    main()
