"""Host RAM details for SMBIOS type 17 spoofing.

Reads the first installed memory module (by DeviceLocator) from WMI. Only
fields actually reported by the host are surfaced: a blank serial or part
number is omitted rather than sent to QEMU as an empty string.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RamModule:
    manufacturer: str = ""
    serial: str = ""
    part: str = ""
    locator: str = ""
    bank: str = ""
    speed: int = 0
    memory_type: int = 0
    capacity: int = 0

    def is_usable(self) -> bool:
        return bool(self.manufacturer or self.serial or self.part)


# PowerShell script beside this module: one line per DIMM, key=value pairs
# separated by |@|. Sort-Object DeviceLocator makes "first" deterministic.
# In a PyInstaller build the script is bundled at _MEIPASS/read-ram.ps1.
import sys

_PS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "read-ram.ps1"
if not _PS.is_file():
    _PS = Path(__file__).resolve().with_name("read-ram.ps1")


def _pwsh_exe() -> str:
    import shutil
    found = shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    # Fallback for frozen Manager: PATH may not include System32.
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for candidate in (
        Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(windir) / "System32" / "pwsh.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return "powershell"


def host_modules() -> list[RamModule]:
    """Return host DIMMs, first by DeviceLocator; empty on any failure."""
    out: list[RamModule] = []
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        run = subprocess.run([_pwsh_exe(), "-NoProfile", "-ExecutionPolicy",
                              "Bypass", "-File", str(_PS)],
                             capture_output=True, text=True,
                             timeout=20, creationflags=flags)
    except Exception:
        return []
    if run.returncode != 0:
        return []
    for line in (run.stdout or "").splitlines():
        fields = {}
        for item in line.split("|@|"):
            if "=" in item:
                k, v = item.split("=", 1)
                fields[k.strip()] = v.strip()
        if not fields:
            continue

        def number(key: str) -> int:
            try:
                return int(fields.get(key, "0") or 0)
            except ValueError:
                return 0

        out.append(RamModule(
            manufacturer=fields.get("Manufacturer", ""),
            serial=fields.get("SerialNumber", ""),
            part=fields.get("PartNumber", ""),
            locator=fields.get("DeviceLocator", ""),
            bank=fields.get("BankLabel", ""),
            speed=number("ConfiguredClockSpeed"),
            memory_type=number("SMBIOSMemoryType"),
            capacity=number("Capacity"),
        ))
    return out


def first_module() -> RamModule | None:
    """The first DIMM only, as required by the SMBIOS spoofing rules."""
    modules = [m for m in host_modules() if m.is_usable()]
    return modules[0] if modules else None


# QEMU's -smbios type=17 option set (hw/smbios/smbios.c) is limited to:
#   loc_pfx, bank, manufacturer, serial, asset, part, speed, memory_type
# It has NO formfactor or slot-count options: form_factor is hardcoded to
# 0x09 (DIMM) and the Type 16 "number_of_memory_devices" is derived from the
# q35 machine's 16 GiB smbios_memory_device_size, not user-settable.
# Therefore iMacPro1,1's FormFactor=13/4 slots and MacPro7,1's
# FormFactor=9/8 slots cannot be spoofed via QEMU command-line arguments.
# The OpenCore image's PlatformInfo remains the place that controls the
# presented SMBIOS model; guest-visible memory slots follow QEMU's tables.

FORMFACTOR_BY_MODEL = {
    "iMacPro1,1": 13,
    "MacPro7,1": 9,
}
SLOT_COUNT_BY_MODEL = {
    "iMacPro1,1": 4,
    "MacPro7,1": 8,
}


def escape_qemu(value: str) -> str:
    """Escape a string embedded in a comma-separated QEMU option value."""
    return value.replace("\\", "\\\\").replace(",", "\\,")
