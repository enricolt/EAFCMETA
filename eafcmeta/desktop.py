"""App desktop: avvia il server su una porta libera e lo mostra in una finestra, con ripiego a 3 livelli.

  1. pywebview            finestra nativa (su Windows richiede pythonnet/WebView2)
  2. app mode             finestra di Edge/Chrome/Chromium già installati (--app=URL, senza barra degli indirizzi,
                          profilo dedicato): sembra un'app nativa e non richiede nessuna libreria
  3. browser predefinito  ultimo ripiego

Ogni livello che fallisce stampa il motivo. Il server si chiude quando la finestra si chiude.
"""
import ntpath
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

LEVELS = ("pywebview", "app", "browser")
WINDOW_SIZE = (1280, 840)
BEAT_INTERVAL_MS = 3000
BEAT_PATH = "/__alive"
BEAT_SNIPPET = (
    b"<script>(function(){var b=function(){try{fetch('" + BEAT_PATH.encode() +
    b"',{method:'POST',keepalive:true})}catch(e){}};b();setInterval(b," + str(BEAT_INTERVAL_MS).encode() +
    b")})()</script>"
)
LAST_LEVEL: str | None = None  # livello che ha effettivamente mostrato l'app (per i messaggi e i test)


def say(msg: str) -> None:
    """print che non fallisce mai (sotto pythonw stdout/stderr possono essere None o chiusi)."""
    try:
        print(msg, flush=True)
    except Exception:  # noqa: BLE001
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def writable_dir(preferred: Path, fallback_name: str = "EAFCMETA") -> Path:
    """Ritorna `preferred` se si può scrivere, altrimenti una cartella nel profilo utente o nella cartella temporanea."""
    options = [Path(preferred)]
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    options += [Path(base) / fallback_name, Path(tempfile.gettempdir()) / fallback_name]
    for d in options:
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".scrittura_ok"
            probe.write_text("1")
            probe.unlink()
            return d
        except OSError:
            continue
    return Path(preferred)


# ---------------------------------------------------------------- browser per l'app mode
def find_app_browsers() -> list[tuple[str, str]]:
    """Browser Chromium già installati adatti all'app mode: lista (nome, percorso) in ordine di preferenza."""
    system = platform.system()
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(name: str, path: str | None) -> None:
        if path and path.lower() not in seen:
            seen.add(path.lower())
            found.append((name, path))

    if system == "Windows":
        env = os.environ
        bases = [env.get("PROGRAMFILES", r"C:\Program Files"), env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")]
        if env.get("LOCALAPPDATA"):
            bases.append(env["LOCALAPPDATA"])
        rel = [("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
               ("Google Chrome", r"Google\Chrome\Application\chrome.exe"),
               ("Chromium", r"Chromium\Application\chrome.exe"),
               ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe")]
        for name, r in rel:
            for base in bases:
                p = ntpath.join(base, r)
                if os.path.exists(p):
                    add(name, p)
        for name, exe in (("Microsoft Edge", "msedge"), ("Google Chrome", "chrome"), ("Chromium", "chromium")):
            add(name, shutil.which(exe))
    elif system == "Darwin":
        apps = [("Microsoft Edge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                ("Google Chrome", "Google Chrome.app/Contents/MacOS/Google Chrome"),
                ("Chromium", "Chromium.app/Contents/MacOS/Chromium"),
                ("Brave", "Brave Browser.app/Contents/MacOS/Brave Browser")]
        for name, r in apps:
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                p = f"{base}/{r}"
                if os.path.exists(p):
                    add(name, p)
        for name, exe in (("Microsoft Edge", "microsoft-edge"), ("Google Chrome", "google-chrome"),
                          ("Chromium", "chromium")):
            add(name, shutil.which(exe))
    else:
        for name, exe in (("Google Chrome", "google-chrome"), ("Google Chrome", "google-chrome-stable"),
                          ("Chromium", "chromium"), ("Chromium", "chromium-browser"),
                          ("Microsoft Edge", "microsoft-edge"), ("Microsoft Edge", "microsoft-edge-stable"),
                          ("Brave", "brave-browser")):
            add(name, shutil.which(exe))
    return found


def app_command(exe: str, url: str, profile: str | Path, size: tuple[int, int] = WINDOW_SIZE) -> list[str]:
    """Riga di comando per aprire `url` come finestra d'app (senza barra degli indirizzi) con profilo dedicato."""
    return [exe, f"--app={url}", f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]


# ---------------------------------------------------------------- battito dalla pagina al server
class Heartbeat:
    """Ultimo segnale di vita inviato dalla pagina (serve quando Edge/Chrome riusano un'istanza già aperta)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last: float | None = None

    def beat(self) -> None:
        self.last = self.clock()


class HeartbeatApp:
    """Avvolge l'app ASGI: risponde a POST /__alive e inserisce lo script del battito nella pagina principale.
    Non modifica l'app originale."""

    def __init__(self, app, hb: Heartbeat):
        self.app, self.hb = app, hb

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == BEAT_PATH:
            self.hb.beat()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            return await send({"type": "http.response.body", "body": b""})
        if scope["path"] not in ("/", "/index.html") or scope["method"] != "GET":
            return await self.app(scope, receive, send)

        start, chunks = {}, []

        async def grab(message):
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                encoded = any(k.lower() == b"content-encoding" for k, _ in headers)
                if start.get("status") == 200 and not encoded:
                    i = body.rfind(b"</body>")
                    body = body[:i] + BEAT_SNIPPET + body[i:] if i >= 0 else body + BEAT_SNIPPET
                headers.append((b"content-length", str(len(body)).encode()))
                await send({"type": "http.response.start", "status": start["status"], "headers": headers})
                await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, grab)


def wait_for_window(proc, hb: Heartbeat, *, alive_timeout: float = 15.0, dead_timeout: float = 8.0,
                    first_beat: float = 20.0, clock=time.monotonic, sleep=time.sleep) -> tuple[bool, str]:
    """Aspetta che la finestra in app mode venga chiusa. Ritorna (ok, motivo).

    - il processo del browser è vivo e la pagina batte: si aspetta;
    - il processo è terminato ma la pagina batte ancora: Edge/Chrome hanno passato la finestra a un'istanza già aperta,
      si aspetta che il battito si fermi;
    - la pagina non ha mai battuto e il processo è terminato: dopo `first_beat` secondi è un fallimento;
    - il processo è vivo ma la pagina non ha mai battuto: si aspetta solo il processo (se lo script non arrivasse).
    """
    t0 = clock()
    died_at = None
    while True:
        now = clock()
        alive = proc.poll() is None
        last = hb.last
        if last is not None:
            if now - last > (alive_timeout if alive else dead_timeout):
                if alive:
                    try:
                        proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                return True, "finestra chiusa"
        elif not alive:
            if died_at is None:
                died_at = now
            if now - t0 > first_beat:
                if died_at - t0 > first_beat:  # ha vissuto a lungo: chiusura normale (la pagina non ha battuto)
                    return True, "finestra chiusa"
                return False, f"il browser è terminato (codice {proc.returncode}) senza aprire la pagina dell'app"
        sleep(0.5)


# ---------------------------------------------------------------- i tre livelli
class LevelError(Exception):
    """Un livello non è utilizzabile: il messaggio è il motivo (mostrato all'utente)."""


def level_pywebview(url: str, ctx: dict) -> None:
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        raise LevelError(f"pywebview non importabile ({type(e).__name__}: {e})") from e
    try:
        webview.create_window("EA FC Meta", url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                              min_size=(440, 620), background_color="#080b14")
        webview.start()
    except Exception as e:  # noqa: BLE001
        raise LevelError(f"pywebview non riesce ad aprire la finestra ({type(e).__name__}: {e})") from e


def level_app(url: str, ctx: dict) -> None:
    browsers = find_app_browsers()
    if not browsers:
        raise LevelError("nessun Microsoft Edge / Chrome / Chromium trovato sul computer")
    profile = Path(ctx["profile"])
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise LevelError(f"non posso creare il profilo dell'app in {profile} ({e})") from e
    errors = []
    for name, exe in browsers:
        try:
            proc = subprocess.Popen(app_command(exe, url, profile), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            errors.append(f"{name} ({exe}): {e}")
            continue
        say(f"[app] finestra app mode con {name}")
        ok, why = wait_for_window(proc, ctx["hb"])
        if ok:
            return
        errors.append(f"{name}: {why}")
    raise LevelError("; ".join(errors))


def level_browser(url: str, ctx: dict) -> None:
    if not webbrowser.open(url):
        raise LevelError("nessun browser predefinito disponibile")
    say(f"[app] aperto nel browser predefinito: {url}\n[app] il server resta attivo finché non chiudi questo programma "
        "(Ctrl+C, oppure termina il processo se non c'è la console).")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


LEVEL_FUNCS = {"pywebview": level_pywebview, "app": level_app, "browser": level_browser}
LEVEL_LABELS = {"pywebview": "finestra nativa (pywebview)", "app": "finestra app mode (Edge/Chrome)",
                "browser": "browser predefinito"}


def run_chain(url: str, ctx: dict, levels: list[tuple[str, object]], say=say) -> str | None:
    """Prova i livelli in ordine. Ritorna il nome di quello riuscito (e stampa perché gli altri no), altrimenti None."""
    for name, func in levels:
        say(f"[app] provo: {LEVEL_LABELS.get(name, name)}...")
        try:
            func(url, ctx)
        except Exception as e:  # noqa: BLE001 - qualunque problema della GUI -> livello successivo
            say(f"[app] livello '{LEVEL_LABELS.get(name, name)}' non disponibile: {e}")
            continue
        return name
    return None


def run(open_window=None, window: str = "auto", profile_dir: str | Path | None = None, levels=None) -> bool:
    """Avvia il server e mostra l'app. `window`: auto | pywebview | app | browser.
    Ritorna False se nessun livello ha funzionato (il chiamante ripiega sul browser classico).

    `open_window(url)` (usato dai test) sostituisce l'intera catena con un unico livello personalizzato."""
    global LAST_LEVEL
    LAST_LEVEL = None
    import uvicorn

    from eafcmeta.api import app

    if levels is None:
        if open_window is not None:
            levels = [("personalizzato", lambda url, ctx: open_window(url))]
        else:
            names = LEVELS if window == "auto" else (window,)
            levels = [(n, LEVEL_FUNCS[n]) for n in names]
    if profile_dir is None:
        profile_dir = writable_dir(Path(__file__).resolve().parent.parent) / ".app_profile"

    hb = Heartbeat()
    port = free_port()
    config = uvicorn.Config(HeartbeatApp(app, hb), host="127.0.0.1", port=port, log_level="warning",
                            log_config=None if sys.stdout is None or sys.stderr is None else uvicorn.config.LOGGING_CONFIG)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        say("[app] il server locale non è partito in tempo.")
        server.should_exit = True
        return False
    try:
        LAST_LEVEL = run_chain(f"http://127.0.0.1:{port}/", {"profile": profile_dir, "hb": hb}, levels)
    finally:
        server.should_exit = True
        thread.join(5)
    if LAST_LEVEL:
        say(f"[app] usato: {LEVEL_LABELS.get(LAST_LEVEL, LAST_LEVEL)}.")
    return LAST_LEVEL is not None
