# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: Reims GUI Manager (onedir, windowed)."""
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = ["fatsync", "fatimg", "ram_info"]
for mod in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
            "PySide6.QtNetwork", "PySide6.QtSvg", "PySide6.QtXml"):
    hidden += collect_submodules(mod)

datas = [
    ("icons", "icons"),
    ("read-ram.ps1", "."),
]

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH, os.path.join(SPECPATH, "..", "scripts")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Reims GUI Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "icons", "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Reims GUI Manager",
)