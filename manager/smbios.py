"""Headless SMBIOS generation via the bundled GenSMBIOS macserial.exe.

Runs macserial.exe directly (no interactive GenSMBIOS prompt, no GUI window),
from a background thread in the UI. Model policy from the spec:
  * MacPro7,1  -> MOST RECOMMENDED
  * iMacPro1,1 -> ALTERNATIVE (warning shown)
"""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from paths import macserial_path

MACSERIAL = macserial_path()

MODELS = [
    ("MacPro7,1", "MOST RECOMMENDED"),
    ("iMacPro1,1", "ALTERNATIVE - NOT COMPATIBLE WITH macOS Tahoe"),
]

INCOMPATIBLE_WARNING = "NOT COMPATIBLE WITH macOS Tahoe"

# `-g -m <model> -n 1` prints "SERIAL | MLB"; `-a` prints
# "MODEL | SERIAL | MLB". Accept both shapes.
_LINE_RE = re.compile(
    r"^\s*(?:(?:[A-Za-z0-9]+,?[0-9]?)\s*\|\s*)?"
    r"([A-Z0-9]{10,14})\s*\|\s*([A-Z0-9]{10,20})\s*$"
)


@dataclass
class SmbiosValues:
    model: str
    serial: str
    mlb: str
    uuid: str
    rom: str

    def as_dict(self) -> dict:
        return {
            "SystemProductName": self.model,
            "SystemSerialNumber": self.serial,
            "SystemUUID": self.uuid,
            "MLB": self.mlb,
            "ROM": self.rom,
        }


def model_warning(model: str) -> str:
    if model == "iMacPro1,1":
        return INCOMPATIBLE_WARNING
    return ""


def generate(model: str = "MacPro7,1", *, macserial: Path = MACSERIAL) -> SmbiosValues:
    """Generate a fresh, unique SMBIOS tuple. Raises RuntimeError on failure."""
    if not macserial.is_file():
        raise RuntimeError(f"macserial.exe not found: {macserial}")
    if not any(model == m for m, _ in MODELS):
        # Still allow other models, but callers surface the Tahoe warning.
        pass

    proc = subprocess.run(
        [str(macserial), "-g", "-m", model, "-n", "1"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"macserial failed: {proc.stderr.strip()[:200]}")

    serial = mlb = None
    for line in (proc.stdout or "").splitlines():
        m = _LINE_RE.match(line)
        if m:
            serial, mlb = m.group(1), m.group(2)
            break
    if not serial or not mlb:
        raise RuntimeError(f"could not parse macserial output: {proc.stdout[:200]!r}")

    # First 6 bytes of a fresh UUID, uppercased hex with colons.
    rom_hex = uuid.uuid4().hex[:12].upper()
    rom = ":".join(rom_hex[i:i + 2] for i in range(0, 12, 2))

    return SmbiosValues(
        model=model, serial=serial, mlb=mlb,
        uuid=str(uuid.uuid4()).upper(), rom=rom,
    )
