import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def run(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_update_flow(tmp_path):
    origin, work = tmp_path / "origin", tmp_path / "work"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(origin))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        run(origin, "git", "config", k, v)
    (origin / "f.txt").write_text("1")
    (origin / "requirements.txt").write_text("")
    (origin / "start.py").write_text((ROOT / "start.py").read_text())
    run(origin, "git", "add", "-A"); run(origin, "git", "commit", "-qm", "uno")
    run(tmp_path, "git", "clone", "-q", str(origin), str(work))
    start = lambda: run(work, sys.executable, "start.py", "--check")
    assert "già aggiornato" in start()
    (origin / "f.txt").write_text("2"); run(origin, "git", "commit", "-qam", "due")
    assert "aggiornato (1 nuovi commit)" in start() and (work / "f.txt").read_text() == "2"
    (origin / "f.txt").write_text("3"); run(origin, "git", "commit", "-qam", "tre")
    (work / "f.txt").write_text("sporco")
    assert "modifiche locali" in start() and (work / "f.txt").read_text() == "sporco"
