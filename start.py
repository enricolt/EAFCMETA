#!/usr/bin/env python3
"""Avvio locale: controlla gli aggiornamenti su GitHub, aggiorna la copia, poi lancia l'app.

Uso:  python start.py [--no-update] [--check] [--lan] [--port 8000] [--no-browser]
  --check    solo controllo/aggiornamento, non avvia il server
  --retry-app riprova l'installazione di pywebview
  --browser  apri nel browser invece che nella finestra dell'app
  --lan      ascolta su tutta la rete (amici in LAN) con chiave d'accesso; default solo questo PC
"""
import argparse
import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "timeout")


def update() -> None:
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        print("[update] git non disponibile o non è una copia git: salto.")
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
    if not req.exists():
        return
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


def lan_token() -> str:
    """Chiave d'accesso per la modalità rete locale: creata una volta e salvata in .token (ignorato da git)."""
    f = ROOT / ".token"
    if f.exists() and f.read_text().strip():
        return f.read_text().strip()
    t = secrets.token_urlsafe(12)
    f.write_text(t)
    return t


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            return sk.getsockname()[0]
    except OSError:
        return "IP-DEL-PC"


def ensure_webview(retry: bool) -> bool:
    """Prova a importare pywebview; se manca lo installa. Se non ci riesce stampa il motivo e ritorna False."""
    def importable() -> str | None:
        try:
            import webview  # noqa: F401
            return None
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"

    err = importable()
    if err is None:
        return True
    failed = ROOT / ".webview_failed"
    tag = sys.version.split()[0]
    if failed.exists() and failed.read_text() == tag and not retry:
        print(f"[app] pywebview non installabile con Python {tag} (tentativo precedente): uso il browser. "
              "Riprova con: python start.py --retry-app")
        return False
    print("[app] installo pywebview (solo la prima volta)...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "pywebview"], capture_output=True, text=True)
    err = importable() if r.returncode == 0 else None
    if r.returncode == 0 and err is None:
        failed.unlink(missing_ok=True)
        return True
    print("[app] installazione di pywebview fallita. Motivo:")
    print("      " + (err or "\n      ".join(r.stderr.strip().splitlines()[-6:])))
    failed.write_text(tag)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lan", action="store_true")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--retry-app", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    if not a.no_update:
        update()
    if a.check:
        return
    install_deps()
    if not a.lan and not a.browser:
        sys.path.insert(0, str(ROOT))
        from eafcmeta import desktop
        if ensure_webview(a.retry_app):
            print("[app] avvio la finestra dell'app...")
            if desktop.run():
                return
            print("[app] la finestra non si è aperta (vedi sopra): uso il browser.")
        else:
            print("[app] uso il browser al posto della finestra.")
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    url = f"http://localhost:{a.port}"
    env = os.environ.copy()
    if a.lan:
        env["EAFCMETA_TOKEN"] = lan_token()
        print(f"[lan] Gli amici aprono:  http://{lan_ip()}:{a.port}/#token={env['EAFCMETA_TOKEN']}")
        print("[lan] Il link contiene la chiave d'accesso: mandalo solo a chi vuoi.")
    if not a.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"[run] {url}  (Ctrl+C per fermare)")
    subprocess.run([sys.executable, "-m", "uvicorn", "eafcmeta.api:app", "--host", host, "--port", str(a.port)], cwd=ROOT, env=env)


if __name__ == "__main__":
    main()
