# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller per EAFCMETA.exe (finestra "app mode" di Edge/Chrome; pywebview non incluso).
# Uso:  python build_windows.py   (oppure: pyinstaller --noconfirm --clean eafcmeta.spec)
# NON provato su Windows reale dall'autore (vedi README).
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + collect_submodules("eafcmeta") + [
    "anyio._backends._asyncio", "h11", "bs4", "lxml", "lxml.etree", "lxml._elementpath", "multipart",
]

a = Analysis(
    ["start.py"],
    pathex=["."],
    datas=[("eafcmeta/static", "eafcmeta/static"), ("eafcmeta/config", "eafcmeta/config")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="EAFCMETA",
    console=False,   # nessuna finestra nera; i messaggi vanno in app.log accanto all'.exe
    upx=False,
)
