"""Test deterministici (niente GUI, niente rete) della catena di ripiego della finestra e della diagnosi."""
import asyncio
import importlib.util
import ntpath
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eafcmeta import desktop

ROOT = Path(__file__).parent.parent


def load_start():
    spec = importlib.util.spec_from_file_location("start_under_test", ROOT / "start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- ricerca browser
def fake_fs(monkeypatch, system, existing=(), which=None):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(os.path, "exists", lambda p: p in set(existing))
    monkeypatch.setattr(shutil, "which", lambda name: (which or {}).get(name))


def test_find_browsers_windows(monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Io Tu\AppData\Local")
    edge = ntpath.join(r"C:\Program Files (x86)", r"Microsoft\Edge\Application\msedge.exe")
    chrome = ntpath.join(r"C:\Users\Io Tu\AppData\Local", r"Google\Chrome\Application\chrome.exe")
    fake_fs(monkeypatch, "Windows", existing=[edge, chrome])
    assert desktop.find_app_browsers() == [("Microsoft Edge", edge), ("Google Chrome", chrome)]


def test_find_browsers_windows_which_and_dedupe(monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    edge = ntpath.join(r"C:\Program Files", r"Microsoft\Edge\Application\msedge.exe")
    fake_fs(monkeypatch, "Windows", existing=[edge], which={"msedge": edge.upper(), "chrome": r"D:\portable\chrome.exe"})
    names = desktop.find_app_browsers()
    assert names[0] == ("Microsoft Edge", edge) and ("Google Chrome", r"D:\portable\chrome.exe") in names
    assert len([n for n in names if n[0] == "Microsoft Edge"]) == 1  # stesso percorso (maiuscole diverse): una volta


def test_find_browsers_linux_macos_and_none(monkeypatch):
    fake_fs(monkeypatch, "Linux", which={"chromium": "/usr/bin/chromium", "microsoft-edge": "/usr/bin/microsoft-edge"})
    assert desktop.find_app_browsers() == [("Chromium", "/usr/bin/chromium"), ("Microsoft Edge", "/usr/bin/microsoft-edge")]
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    fake_fs(monkeypatch, "Darwin", existing=[mac])
    assert desktop.find_app_browsers() == [("Google Chrome", mac)]
    fake_fs(monkeypatch, "Linux")
    assert desktop.find_app_browsers() == []


# ---------------------------------------------------------------- riga di comando
def test_app_command():
    cmd = desktop.app_command(r"C:\Program Files\Edge\msedge.exe", "http://127.0.0.1:5000/", r"C:\Mia Cartella\.app_profile")
    assert cmd[0] == r"C:\Program Files\Edge\msedge.exe"  # percorso con spazi: un solo elemento
    assert "--app=http://127.0.0.1:5000/" in cmd
    assert r"--user-data-dir=C:\Mia Cartella\.app_profile" in cmd
    assert "--window-size=1280,840" in cmd
    assert not any(a.startswith("http") for a in cmd)  # niente URL "nudo": sarebbe una scheda normale


# ---------------------------------------------------------------- catena di ripiego
def test_chain_falls_through_and_reports_reasons():
    out = []

    def boom(msg):
        def f(url, ctx):
            raise desktop.LevelError(msg)
        return f

    used = []
    levels = [("pywebview", boom("manca pythonnet")), ("app", boom("nessun Edge")),
              ("browser", lambda url, ctx: used.append(url))]
    assert desktop.run_chain("http://x/", {}, levels, say=out.append) == "browser"
    text = "\n".join(out)
    assert "manca pythonnet" in text and "nessun Edge" in text and used == ["http://x/"]


def test_chain_all_fail_and_first_wins():
    out = []
    fail = lambda url, ctx: 1 / 0  # noqa: E731
    assert desktop.run_chain("u", {}, [("a", fail), ("b", fail)], say=out.append) is None
    assert sum("non disponibile" in o for o in out) == 2
    calls = []
    assert desktop.run_chain("u", {}, [("a", lambda u, c: calls.append("a")), ("b", lambda u, c: calls.append("b"))],
                             say=out.append) == "a" and calls == ["a"]


def test_run_with_failing_levels_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("EAFCMETA_DB", str(tmp_path / "d.db"))
    assert desktop.run(levels=[("pywebview", lambda u, c: 1 / 0)], profile_dir=tmp_path) is False
    assert desktop.LAST_LEVEL is None


def test_level_app_without_browser_reports_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "find_app_browsers", lambda: [])
    with pytest.raises(desktop.LevelError, match="nessun Microsoft Edge"):
        desktop.level_app("http://x/", {"profile": tmp_path / "p", "hb": desktop.Heartbeat()})


def test_level_app_launches_with_profile(monkeypatch, tmp_path):
    launched = []

    class P:
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(desktop, "find_app_browsers", lambda: [("Edge", "/x/msedge")])
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: launched.append(cmd) or P())
    monkeypatch.setattr(desktop, "wait_for_window", lambda proc, hb, **kw: (True, "ok"))
    desktop.level_app("http://x/", {"profile": tmp_path / "p", "hb": desktop.Heartbeat()})
    assert launched[0][0] == "/x/msedge" and f"--user-data-dir={tmp_path / 'p'}" in launched[0]
    assert (tmp_path / "p").is_dir()


# ---------------------------------------------------------------- attesa della finestra
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeProc:
    def __init__(self, clock, dies_at=None, code=0):
        self.clock, self.dies_at, self.returncode, self.terminated = clock, dies_at, code, False

    def poll(self):
        return self.returncode if self.terminated or (self.dies_at is not None and self.clock() >= self.dies_at) else None

    def terminate(self):
        self.terminated = True


def run_wait(proc, hb, clock, **kw):
    return desktop.wait_for_window(proc, hb, clock=clock, sleep=clock.sleep, **kw)


def test_wait_reused_instance_ends_when_heartbeat_stops():
    clock = FakeClock()
    hb = desktop.Heartbeat(clock)
    proc = FakeProc(clock, dies_at=1)  # il processo lanciato termina subito (istanza esistente riusata)
    beating = {"on": True}
    orig = clock.sleep

    def sleep(s):
        orig(s)
        if beating["on"] and clock.t < 60:
            hb.beat()
    ok, why = desktop.wait_for_window(proc, hb, clock=clock, sleep=sleep)
    assert ok and clock.t >= 60 and clock.t < 80


def test_wait_fails_if_browser_dies_without_page():
    clock = FakeClock()
    ok, why = run_wait(FakeProc(clock, dies_at=1, code=3), desktop.Heartbeat(clock), clock)
    assert not ok and "codice 3" in why


def test_wait_window_closed_normally_and_zombie_process():
    clock = FakeClock()
    hb = desktop.Heartbeat(clock)
    hb.beat()
    proc = FakeProc(clock)  # processo ancora vivo ma la pagina non batte più: finestra chiusa, si ferma tutto
    ok, _ = run_wait(proc, hb, clock)
    assert ok and proc.terminated
    # pagina mai battuta ma processo vissuto a lungo e poi terminato: chiusura normale, non fallimento
    clock2 = FakeClock()
    ok, _ = run_wait(FakeProc(clock2, dies_at=300), desktop.Heartbeat(clock2), clock2)
    assert ok


# ---------------------------------------------------------------- battito (ASGI)
def test_heartbeat_app_injects_and_counts():
    from fastapi.testclient import TestClient

    from eafcmeta import api

    hb = desktop.Heartbeat()
    c = TestClient(desktop.HeartbeatApp(api.app, hb))
    r = c.get("/")
    assert r.status_code == 200 and "/__alive" in r.text and r.text.rstrip().endswith("</html>")
    assert int(r.headers["content-length"]) == len(r.content)
    assert hb.last is None
    assert c.post("/__alive").status_code == 204 and hb.last is not None
    assert "/__alive" not in c.get("/docs").text  # solo la pagina principale


# ---------------------------------------------------------------- stdout None / cartella non scrivibile
def test_say_with_no_stdout(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    desktop.say("ciao")
    start = load_start()
    start.say("ciao")
    start.setup_console()
    assert sys.stdout is not None  # sostituito con un file nullo


def test_writable_dir_falls_back(tmp_path, monkeypatch):
    ro = tmp_path / "file_non_cartella"
    ro.write_text("x")  # mkdir su un file fallisce -> non scrivibile
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    d = desktop.writable_dir(ro / "sub")
    assert d == tmp_path / "local" / "EAFCMETA" and d.is_dir()


# ---------------------------------------------------------------- diagnosi
def test_diagnosi_cli():
    out = subprocess.run([sys.executable, "start.py", "--diagnosi"], cwd=ROOT, capture_output=True, text=True,
                         check=True, timeout=60).stdout
    for key in ("DIAGNOSI", "Python:", "Sistema operativo:", "git:", "pywebview:", "Browser per l'app mode:",
                "Porta 8000:", "Versione dell'app:", "Ultimo commit:"):
        assert key in out, key


def test_diagnosi_with_stdout_none_and_no_git(monkeypatch, tmp_path):
    start = load_start()
    monkeypatch.setattr(start, "ROOT", tmp_path)
    monkeypatch.setattr(start, "DATA", tmp_path)
    monkeypatch.setattr(shutil, "which", lambda n: None)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    start.main(["--diagnosi"])  # non deve sollevare
    text = (tmp_path / "diagnosi.txt").read_text(encoding="utf-8")
    assert "git: NON trovato" in text and "NESSUNO trovato" in text and "Ultimo commit: non disponibile" in text


# ---------------------------------------------------------------- opzioni di start.py
@pytest.mark.parametrize("args,expected", [([], "auto"), (["--window", "app"], "app"), (["--window", "pywebview"], "pywebview")])
def test_start_window_option_forwarded(monkeypatch, tmp_path, args, expected):
    start = load_start()
    seen = {}
    monkeypatch.setattr(start, "install_deps", lambda: None)
    monkeypatch.setattr(start, "ensure_webview", lambda retry: False)
    monkeypatch.setattr(desktop, "run", lambda window, profile_dir: seen.setdefault("window", window) and True)
    start.main(["--no-update", *args])
    assert seen["window"] == expected


def test_start_browser_option_skips_window(monkeypatch):
    start = load_start()
    seen = {}
    monkeypatch.setattr(start, "install_deps", lambda: None)
    monkeypatch.setattr(desktop, "run", lambda **kw: pytest.fail("la finestra non va tentata con --browser"))
    monkeypatch.setattr(start, "serve", lambda host, port, ob: seen.update(host=host, ob=ob))
    start.main(["--no-update", "--browser", "--port", "0"])
    assert seen["host"] == "127.0.0.1" and seen["ob"] is True


def test_start_falls_back_to_browser_when_no_window(monkeypatch):
    start = load_start()
    seen = {}
    monkeypatch.setattr(start, "install_deps", lambda: None)
    monkeypatch.setattr(start, "ensure_webview", lambda retry: False)
    monkeypatch.setattr(desktop, "run", lambda **kw: False)
    monkeypatch.setattr(start, "serve", lambda host, port, ob: seen.update(host=host))
    start.main(["--no-update", "--port", "0"])
    assert seen["host"] == "127.0.0.1"
