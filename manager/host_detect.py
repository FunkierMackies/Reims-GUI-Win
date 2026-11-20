"""Host specification detection for the Reims GUI manager.

Read-only. Uses PowerShell CIM for CPU/GPU inventory and PowerShell 7's
System.Runtime.Intrinsics for AVX/AVX2/AVX512 feature flags. Nothing here
writes to the machine; every value can be overridden in the GUI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


def _ps_exe() -> Optional[str]:
    """Prefer PowerShell 7 (has System.Runtime.Intrinsics), fall back to 5.1."""
    candidates = [
        shutil.which("pwsh"),
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Program Files\PowerShell\7-preview\pwsh.exe",
        shutil.which("powershell"),
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def _run_ps(script: str, timeout: int = 25) -> Optional[str]:
    exe = _ps_exe()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _json_or_none(text: Optional[str]):
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


@dataclass
class HostSpec:
    cpu_name: str = ""
    cpu_vendor: str = ""          # "Intel" | "AMD" | "Unknown"
    physical_cores: int = 0       # sum of NumberOfCores across sockets
    logical_cores: int = 0
    avx: Optional[bool] = None
    avx2: Optional[bool] = None
    avx512: Optional[bool] = None
    gpus: list = field(default_factory=list)
    has_nvidia_rtx: bool = False
    detection_notes: list = field(default_factory=list)

    # ---- derived -------------------------------------------------------
    @property
    def core_band(self) -> str:
        """'low' for 2-8 physical cores, 'high' for >8, 'unknown' otherwise."""
        if self.physical_cores <= 0:
            return "unknown"
        return "low" if self.physical_cores <= 8 else "high"

    @property
    def is_intel(self) -> bool:
        return self.cpu_vendor == "Intel"

    @property
    def is_amd(self) -> bool:
        return self.cpu_vendor == "AMD"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "HostSpec":
        return HostSpec(**{
            k: v for k, v in (data or {}).items()
            if k in HostSpec.__dataclass_fields__
        })


_CPU_SCRIPT = r"""
$out = @(Get-CimInstance Win32_Processor | ForEach-Object {
  [pscustomobject]@{
    Manufacturer   = [string]$_.Manufacturer
    Name           = [string]$_.Name
    PhysicalCores  = [int]$_.NumberOfCores
    LogicalCores   = [int]$_.NumberOfLogicalProcessors
  }
})
$out | ConvertTo-Json -Compress
"""

_GPU_SCRIPT = r"""
$out = @(Get-CimInstance Win32_VideoController |
  Where-Object { $_.Name } |
  ForEach-Object { [string]$_.Name })
$out | ConvertTo-Json -Compress
"""

# PowerShell 7 exposes CPU feature intrinsics directly. Wrapped per-feature so
# a missing intrinsic on an older runtime never aborts the whole scan.
_FEATURES_SCRIPT = r"""
function F($n) {
  try {
    if ($n -eq 'Avx') { return [bool][System.Runtime.Intrinsics.X86.Avx]::IsSupported }
    if ($n -eq 'Avx2') { return [bool][System.Runtime.Intrinsics.X86.Avx2]::IsSupported }
    if ($n -eq 'Avx512F') { return [bool][System.Runtime.Intrinsics.X86.Avx512F]::IsSupported }
    return $null
  } catch { return $null }
}
[pscustomobject]@{
  avx    = F 'Avx'
  avx2   = F 'Avx2'
  avx512 = F 'Avx512F'
} | ConvertTo-Json -Compress
"""

_RTX_RE = re.compile(r"\bRTX\s?\d{3,5}\b", re.IGNORECASE)


def is_rtx_name(name: str) -> bool:
    return bool(_RTX_RE.search(name or ""))


def detect_host() -> HostSpec:
    """Detect the current Windows host. Never raises; reports issues in notes."""
    spec = HostSpec()
    notes = spec.detection_notes

    if not _ps_exe():
        notes.append("No PowerShell found; CPU/GPU detection unavailable. "
                     "Use the manual overrides on the Host tab.")
        return spec

    # --- CPU identity + physical cores ---
    cpu_data = _json_or_none(_run_ps(_CPU_SCRIPT))
    if isinstance(cpu_data, dict):
        cpu_data = [cpu_data]
    if cpu_data:
        spec.cpu_name = str(cpu_data[0].get("Name") or "")
        mfr = str(cpu_data[0].get("Manufacturer") or "").lower()
        if "intel" in mfr:
            spec.cpu_vendor = "Intel"
        elif "amd" in mfr or "advanced micro" in mfr:
            spec.cpu_vendor = "AMD"
        else:
            spec.cpu_vendor = "Unknown"
        spec.physical_cores = sum(int(c.get("PhysicalCores") or 0) for c in cpu_data)
        spec.logical_cores = sum(int(c.get("LogicalCores") or 0) for c in cpu_data)
    else:
        notes.append("CPU inventory query failed; physical cores unknown.")

    # --- SIMD features ---
    feat = _json_or_none(_run_ps(_FEATURES_SCRIPT))
    if isinstance(feat, dict):
        spec.avx = bool(feat["avx"]) if feat.get("avx") is not None else None
        spec.avx2 = bool(feat["avx2"]) if feat.get("avx2") is not None else None
        spec.avx512 = bool(feat["avx512"]) if feat.get("avx512") is not None else None
    else:
        notes.append("AVX feature query failed (needs PowerShell 7). "
                     "Set the feature checkboxes manually on the Host tab.")

    # --- GPUs ---
    gpu_data = _json_or_none(_run_ps(_GPU_SCRIPT))
    if isinstance(gpu_data, str):
        gpu_data = [gpu_data]
    if gpu_data:
        spec.gpus = [str(g) for g in gpu_data]
        spec.has_nvidia_rtx = any(is_rtx_name(g) for g in spec.gpus)

    if spec.cpu_vendor == "Unknown":
        notes.append("CPU vendor not recognised; preset selection needs a manual override.")
    return spec


if __name__ == "__main__":
    print(json.dumps(detect_host().to_dict(), indent=2))
