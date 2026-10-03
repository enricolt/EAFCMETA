#!/usr/bin/env python3
"""Avvio locale: controlla gli aggiornamenti su GitHub, aggiorna la copia, poi lancia l'app.

Uso:  python start.py [opzioni]
  --window auto|pywebview|app|browser
             auto (predefinito): prova 1) finestra nativa pywebview, 2) finestra "app" di Edge/Chrome (senza barra
             degli indirizzi), 3) browser predefinito. Gli altri valori forzano un solo livello.
  --browser  come --window browser
  --diagnosi stampa un rapporto (versione di Python, sistema, git, pywebview, browser, porta...) da copiare e incollare
  --retry-app riprova l'installazione di pywebview (se era fallita)
  --check    solo controllo/aggiornamento, non avvia il server
  --no-update  non controllare gli aggiornamenti
  --lan      ascolta su tutta la rete (amici in LAN) con chiave d'accesso; default solo questo PC
  --port N   porta (solo per --lan e --window browser; la finestra dell'app usa una porta libera)
  --no-browser  avvia solo il server (con --window browser/--lan)
"""
import argparse
import hashlib
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))  # True dentro l'eseguibile creato con PyInstaller
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).parent.resolve()
DATA = ROOT  # cartella dei dati/marker: diventa una cartella scrivibile in prepare_data_dir()
PIP_TIMEOUT = 300
WEBVIEW_TIMEOUT = 90


def setup_console() -> None:
    """Sotto pythonw stdout/stderr sono None: li sostituisco; sulla console Windows (cp1252) evito errori di codifica."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            # eseguibile senza console: messaggi in app.log accanto all'.exe; altrimenti scartati
            target = str(ROOT / "app.log") if FROZEN else os.devnull
            for t, mode in ((target, "a"), (os.devnull, "w")):
                try:
                    setattr(sys, name, open(t, mode, encoding="utf-8", errors="replace", buffering=1))
                    break
                except OSError:
                    continue
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def say(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except Exception:  # noqa: BLE001 - la console può mancare o essere chiusa
        pass


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_text(path: Path, text: str, private: bool = False) -> bool:
    """Scrive un file di testo; con private=True lo crea leggibile solo dal proprietario (600, dove il sistema lo supporta)."""
    try:
        if private:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            restrict(path)
        else:
            path.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def restrict(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows o filesystem senza permessi: pazienza
        pass


def find_git() -> str | None:
    """git dal PATH o, su Windows, dai percorsi di installazione abituali."""
    g = shutil.which("git")
    if g:
        return g
    if platform.system() == "Windows":
        for env, rel in (("PROGRAMFILES", r"Git\cmd\git.exe"), ("PROGRAMFILES(X86)", r"Git\cmd\git.exe"),
                         ("LOCALAPPDATA", r"Programs\Git\cmd\git.exe")):
            base = os.environ.get(env)
            if base and os.path.exists(os.path.join(base, rel)):
                return os.path.join(base, rel)
    return None


def git(*args: str) -> subprocess.CompletedProcess:
    exe = find_git()
    if not exe:
        return subprocess.CompletedProcess(args, 1, "", "git non trovato")
    try:
        return subprocess.run([exe, *args], cwd=ROOT, capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "timeout")
    except OSError as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def update() -> None:
    if not (ROOT / ".git").exists() or not find_git():
        say("[update] git non disponibile o non è una copia git: salto.")
        return
    f = git("fetch", "--quiet")
    if f.returncode:
        say("[update] offline o GitHub non raggiungibile: uso la versione locale.")
        return
    behind = git("rev-list", "--count", "HEAD..@{u}")
    if behind.returncode:
        say("[update] nessun branch remoto collegato, salto.")
        return
    n = int(behind.stdout.strip() or 0)
    if n == 0:
        say("[update] già aggiornato.")
        return
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        say(f"[update] ci sono {n} aggiornamenti ma hai modifiche locali non salvate: non aggiorno.")
        return
    p = git("pull", "--ff-only", "--quiet")
    if p.returncode:
        say("[update] impossibile aggiornare senza conflitti:\n" + p.stderr.strip())
        return
    say(f"[update] aggiornato ({n} nuovi commit):")
    say(git("log", "--oneline", f"-{min(n, 5)}").stdout.rstrip())


def prepare_data_dir() -> None:
    """Se la cartella dell'app non è scrivibile (o è un .exe) database e configurazione personale vanno altrove."""
    global DATA
    sys.path.insert(0, str(ROOT))
    from eafcmeta import desktop
    DATA = desktop.writable_dir(ROOT)
    if DATA != ROOT or FROZEN:
        os.environ.setdefault("EAFCMETA_DB", str(DATA / "eafcmeta.db"))
        os.environ.setdefault("EAFCMETA_LOCAL_CONFIG", str(DATA / "local.json"))
    if DATA != ROOT:
        say(f"[dati] la cartella dell'app non è scrivibile: uso {DATA}")


def _imports_ok(*modules: str) -> bool:
    import importlib
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception:  # noqa: BLE001
            return False
    return True


def install_deps() -> None:
    if FROZEN:
        return
    req = ROOT / "requirements.txt"
    if not req.exists():
        return
    stamp = DATA / ".deps_hash"
    h = hashlib.sha256(req.read_bytes()).hexdigest()
    if read_text(stamp) == h:
        return
    say("[deps] installo le dipendenze...")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)], timeout=PIP_TIMEOUT,
                           capture_output=True, text=True, errors="replace")
        ok, err = r.returncode == 0, r.stderr.strip()[-600:]
    except (subprocess.TimeoutExpired, OSError) as e:
        ok, err = False, f"{type(e).__name__}: {e}"
    if ok:
        write_text(stamp, h)
    elif _imports_ok("fastapi", "uvicorn", "bs4", "lxml"):
        say(f"[deps] installazione non riuscita ({err}), ma le librerie necessarie ci sono già: continuo.")
    else:
        sys.exit("[deps] installazione fallita e librerie mancanti. Prova a mano:  "
                 f"python -m pip install -r requirements.txt\n{err}")


def lan_token() -> str:
    """Chiave d'accesso per la modalità rete locale: creata una volta e salvata in .token (ignorato da git)."""
    f = DATA / ".token"
    t = (read_text(f) or "").strip()
    if t:
        restrict(f)  # anche un .token creato da una versione precedente
        return t
    t = secrets.token_urlsafe(12)
    if not write_text(f, t, private=True):
        say("[lan] non riesco a salvare la chiave: vale solo per questa sessione.")
    return t


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            return sk.getsockname()[0]
    except OSError:
        return "IP-DEL-PC"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def webview_error() -> str | None:
    """None se pywebview è importabile, altrimenti il motivo."""
    try:
        import webview  # noqa: F401
        return None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def ensure_webview(retry: bool) -> bool:
    """Prova a importare pywebview; se manca tenta UNA volta l'installazione (con timeout). Non blocca mai l'avvio:
    se non riesce stampa il motivo e ritorna False (si passa al livello successivo)."""
    err = webview_error()
    if err is None:
        return True
    if FROZEN:
        say(f"[app] pywebview non incluso nell'eseguibile ({err}).")
        return False
    failed = DATA / ".webview_failed"
    tag = sys.version.split()[0]
    if read_text(failed) == tag and not retry:
        say(f"[app] pywebview non installabile con Python {tag} (tentativo precedente). Motivo: {err}. "
            "Riprova con: python start.py --retry-app")
        return False
    say(f"[app] pywebview non installato ({err}): provo a installarlo (max {WEBVIEW_TIMEOUT}s, solo la prima volta)...")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "pywebview"], capture_output=True, text=True,
                           errors="replace", timeout=WEBVIEW_TIMEOUT)
        detail = "\n      ".join(r.stderr.strip().splitlines()[-6:])
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        ok, detail = False, f"tempo scaduto ({WEBVIEW_TIMEOUT}s)"
    except OSError as e:
        ok, detail = False, str(e)
    err = webview_error() if ok else None
    if ok and err is None:
        try:
            failed.unlink(missing_ok=True)
        except OSError:
            pass
        return True
    say("[app] installazione di pywebview fallita. Motivo:")
    say("      " + (err or detail))
    write_text(failed, tag)
    return False


# ---------------------------------------------------------------- diagnosi
def diagnosi_lines(port: int = 8000) -> list[str]:
    """Rapporto in italiano da copiare e incollare per chiedere aiuto."""
    L = ["=== DIAGNOSI EAFCMETA ==="]
    L.append(f"Python: {sys.version.split()[0]} ({platform.architecture()[0]}) - {sys.executable}")
    L.append(f"Sistema operativo: {platform.platform()}")
    enc = getattr(sys.stdout, "encoding", None) if sys.stdout is not None else None
    L.append(f"Console: codifica {enc or 'nessuna console (pythonw?)'}")
    L.append(f"Cartella dell'app: {ROOT}" + (" (eseguibile PyInstaller)" if FROZEN else ""))
    g = find_git()
    if g:
        try:
            v = subprocess.run([g, "--version"], capture_output=True, text=True, errors="replace",
                               timeout=15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            v = ""
        L.append(f"git: trovato ({g}) {v}")
    else:
        L.append("git: NON trovato nel PATH (gli aggiornamenti automatici sono saltati; l'app funziona lo stesso)")
    err = webview_error()
    L.append("pywebview: importabile" if err is None else f"pywebview: NON importabile - {err}")
    mark = read_text(DATA / ".webview_failed") or read_text(ROOT / ".webview_failed")
    if mark:
        L.append(f"  (installazione già fallita con Python {mark.strip()}; riprova con --retry-app)")
    try:
        sys.path.insert(0, str(ROOT))
        from eafcmeta import desktop
        browsers = desktop.find_app_browsers()
        wd = desktop.writable_dir(ROOT)
        L.append("Browser per l'app mode: " + ("; ".join(f"{n} ({p})" for n, p in browsers) if browsers
                                              else "NESSUNO trovato (serve Microsoft Edge, Chrome o Chromium)"))
        L.append(f"Cartella scrivibile: {wd}" + ("" if wd == ROOT else " (quella dell'app NON è scrivibile)"))
    except Exception as e:  # noqa: BLE001
        L.append(f"Browser per l'app mode: impossibile cercare ({type(e).__name__}: {e})")
    L.append(f"Porta {port}: " + ("libera" if port_free(port) else "OCCUPATA (l'app userà un'altra porta)"))
    version = "sconosciuta"
    try:
        import json
        cfg = json.loads((ROOT / "eafcmeta" / "config" / "patch.json").read_text(encoding="utf-8"))
        version = "configurazione " + str(cfg.get("patch_version", "?"))
    except Exception:  # noqa: BLE001
        pass
    L.append(f"Versione dell'app: {version}")
    if g and (ROOT / ".git").exists():
        c = git("log", "-1", "--format=%h %ad %s", "--date=short")
        L.append("Ultimo commit: " + (c.stdout.strip() if c.returncode == 0 and c.stdout.strip() else "non leggibile"))
    else:
        L.append("Ultimo commit: non disponibile (non è una copia git o git manca)")
    L.append("=== fine diagnosi ===")
    return L


# ---------------------------------------------------------------- avvio
def serve(host: str, port: int, open_browser: bool) -> None:
    """Server in questo processo (funziona anche sotto pythonw); apre il browser predefinito."""
    import threading
    import time

    import uvicorn

    url = f"http://localhost:{port}"
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    say(f"[run] {url}  (Ctrl+C per fermare)")
    uvicorn.run("eafcmeta.api:app", host=host, port=port, log_level="info",
                log_config=uvicorn.config.LOGGING_CONFIG if sys.stdout is not None and sys.stderr is not None else None)


def main(argv: list[str] | None = None) -> None:
    setup_console()
    ap = argparse.ArgumentParser(description="Avvio di EA FC Meta")
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lan", action="store_true")
    ap.add_argument("--browser", action="store_true", help="come --window browser")
    ap.add_argument("--window", choices=("auto", "pywebview", "app", "browser"), default="auto")
    ap.add_argument("--diagnosi", action="store_true", help="stampa il rapporto di diagnosi ed esce")
    ap.add_argument("--retry-app", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    if a.browser:
        a.window = "browser"

    if a.diagnosi:
        lines = diagnosi_lines(a.port)
        for line in lines:
            say(line)
        write_text(ROOT / "diagnosi.txt", "\n".join(lines) + "\n")
        return
    if not a.no_update and not FROZEN:
        try:
            update()
        except Exception as e:  # noqa: BLE001 - gli aggiornamenti non devono mai impedire l'avvio
            say(f"[update] errore inatteso ({type(e).__name__}: {e}): uso la versione locale.")
    if a.check:
        return
    prepare_data_dir()
    install_deps()
    sys.path.insert(0, str(ROOT))

    if not a.lan and a.window != "browser":
        from eafcmeta import desktop
        if a.window in ("auto", "pywebview") and not ensure_webview(a.retry_app) and a.window == "pywebview":
            say("[app] pywebview forzato ma non disponibile.")
        say(f"[app] modalità finestra: {a.window}")
        if desktop.run(window=a.window, profile_dir=DATA / ".app_profile"):
            return
        say("[app] nessuna finestra disponibile (motivi sopra): uso il browser predefinito.")

    host = "0.0.0.0" if a.lan else "127.0.0.1"
    port = a.port
    if not port_free(port, host):
        from eafcmeta import desktop
        port = desktop.free_port()
        say(f"[run] la porta {a.port} è occupata: uso la {port}.")
    if a.lan:
        os.environ["EAFCMETA_TOKEN"] = lan_token()
        say(f"[lan] Gli amici aprono:  http://{lan_ip()}:{port}/#token={os.environ['EAFCMETA_TOKEN']}")
        say("[lan] Il link contiene la chiave d'accesso: mandalo solo a chi vuoi.")
    serve(host, port, not a.no_browser)


if __name__ == "__main__":
    main()
