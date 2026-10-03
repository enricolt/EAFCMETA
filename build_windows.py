#!/usr/bin/env python3
"""Crea dist/EAFCMETA.exe con PyInstaller (da eseguire su Windows):  python build_windows.py

NON provato su Windows reale dall'autore: se fallisce, copia l'errore e chiedi aiuto (vedi anche  python start.py --diagnosi).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def run(*cmd: str) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> None:
    run(sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "pyinstaller>=6")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "eafcmeta.spec")
    exe = ROOT / "dist" / ("EAFCMETA.exe" if sys.platform == "win32" else "EAFCMETA")
    print(f"\nFatto: {exe}" if exe.exists() else "\nATTENZIONE: l'eseguibile non è stato creato.")


if __name__ == "__main__":
    main()
