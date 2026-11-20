"""Resolve bundled backend tools for the Reims GUI Manager.

Every external app the manager needs lives under manager\\Backend-Apps so the
GUI works without relying on the system Python or a global PySide6 install.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

def _app_dir() -> Path:
    """Folder containing the manager's Python modules / the packaged exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


MANAGER = _app_dir()
ROOT = MANAGER.parent
BACKEND = MANAGER / "Backend-Apps"
PYTHON_HOME = BACKEND / "Python"
QEMU_RUNTIME = BACKEND / "QEMU-Runtime"
APPLE_QEMU = BACKEND / "AppleGFX-QEMU"
GENSMBIOS = BACKEND / "GenSMBIOS"
MACSERIAL = GENSMBIOS / "macserial.exe"
REIMS_QEMU = BACKEND / "ReimsQEMU"
REIMS_ROMS = REIMS_QEMU / "roms"
FIRMWARE = BACKEND / "Firmware"
PS7 = BACKEND / "PowerShell7"
PWSH = PS7 / "pwsh.exe"
MACOS_DISKS = ROOT / "macOS-disks"
# Prefer the bundled copy; fall back to the original build tree.
MAIN_QEMU = (REIMS_QEMU / "qemu-system-x86_64.exe")
if not MAIN_QEMU.is_file():
    MAIN_QEMU = ROOT / "build" / "qemu-system-x86_64.exe"


def ensure_pyside6():
    """Add the bundled Python's site-packages so PySide6 imports on any host."""
    candidates = [
        PYTHON_HOME / "Lib" / "site-packages",
        PYTHON_HOME / "lib" / "site-packages",
    ]
    for p in candidates:
        if (p / "PySide6").is_dir():
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)
            return s
    return None


def python_exe() -> Path:
    for name in ("python.exe", "pythonw.exe"):
        p = PYTHON_HOME / name
        if p.is_file():
            return p
    return Path(sys.executable)


def pwsh_exe() -> str:
    if PWSH.is_file():
        return str(PWSH)
    found = shutil.which("pwsh") or shutil.which("powershell")
    return found or "powershell"


def qemu_img_exe() -> Path:
    """Prefer a copy beside the bundled runtime, then known local builds."""
    for p in (QEMU_RUNTIME / "qemu-img.exe",
              ROOT / "build" / "qemu-img.exe",
              Path(r"D:\msys64\ucrt64\bin\qemu-img.exe")):
        if p.is_file():
            return p
    return QEMU_RUNTIME / "qemu-img.exe"


def qemu_for_profile(profile_key: str) -> Path:
    """Each GPU stack needs its own QEMU build (device names differ)."""
    if profile_key == "apple-gfx":
        return APPLE_QEMU / "qemu-system-x86_64.exe"
    return MAIN_QEMU


def runtime_env(profile_key: str | None = None) -> dict:
    """PATH with the correct bundled QEMU runtime prepended, for subprocesses."""
    env = os.environ.copy()
    runtime = APPLE_QEMU if profile_key == "apple-gfx" else QEMU_RUNTIME
    if runtime.is_dir():
        env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
    return env


def macserial_path() -> Path:
    if MACSERIAL.is_file():
        return MACSERIAL
    legacy = Path(
        r"C:\Users\Windows 11 Pro 26H2\Downloads\GenSMBIOS-master"
        r"\GenSMBIOS-master\Scripts\macserial.exe")
    return legacy


def preflight() -> list:
    """Return a list of missing critical backend items (empty = healthy)."""
    missing = []
    if not (PYTHON_HOME / "python.exe").is_file():
        missing.append("Backend-Apps/Python/python.exe")
    if not (PYTHON_HOME / "Lib" / "site-packages" / "PySide6").is_dir():
        missing.append("Backend-Apps/Python/Lib/site-packages/PySide6")
    if not MACSERIAL.is_file():
        missing.append("Backend-Apps/GenSMBIOS/macserial.exe")
    if not QEMU_RUNTIME.is_dir():
        missing.append("Backend-Apps/QEMU-Runtime")
    if not qemu_img_exe().is_file():
        missing.append("qemu-img.exe (in QEMU-Runtime)")
    if not (APPLE_QEMU / "qemu-system-x86_64.exe").is_file():
        missing.append("Backend-Apps/AppleGFX-QEMU/qemu-system-x86_64.exe")
    if not MAIN_QEMU.is_file():
        missing.append("ReimsQEMU/qemu-system-x86_64.exe (Reims QEMU)")
    if not PWSH.is_file() and not (shutil.which("pwsh") or shutil.which("powershell")):
        missing.append("PowerShell 7 (pwsh.exe)")
    for f in ("OVMF_CODE.fd", "OVMF_VARS.fd"):
        if not (FIRMWARE / f).is_file():
            missing.append(f"Firmware/{f}")
    if not (REIMS_QEMU / "pc-bios" / "bios-256k.bin").is_file():
        missing.append("ReimsQEMU/pc-bios/bios-256k.bin")
    return missing
