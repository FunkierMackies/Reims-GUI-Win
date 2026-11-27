"""GPU profiles: device + ROM + kext set for the two interchangeable stacks.

AppleMCEReporterDisabler.kext is never enabled/disabled/touched by the manager - it
stays exactly as shipped in the firmware image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from paths import ROOT

BACKEND = ROOT / "manager" / "Backend-Apps"
REIMS_ROMS = BACKEND / "ReimsQEMU" / "roms"
if not (REIMS_ROMS / "ReimsGraphics.rom").is_file():
    REIMS_ROMS = ROOT / "build" / "hw" / "display"

# The six kexts the manager knows about. Apple-GFX needs only Lilu and
# ReimsMetalCompat (verified: macOS boots with GPU acceleration).
REIMS_KECTS = ("Graft.kext", "ReimsGraphics.kext", "RgfxGate.kext")
APPLE_GFX_KECTS = ("Lilu.kext", "ReimsMetalCompat.kext")
NEUTRAL_KECTS = ("AppleMCEReporterDisabler.kext",)
ALL_KECTS = REIMS_KECTS + APPLE_GFX_KECTS + NEUTRAL_KECTS


@dataclass(frozen=True)
class GpuProfile:
    key: str
    title: str
    qemu_device: str
    rom_name: str
    rom_path: Path
    enable: tuple
    disable: tuple
    max_resolution: str
    notes: str = ""


def _reims_rom() -> Path:
    # Prefer the ROM shipped inside the copied tree; the dist copy is fallback.
    for rel in (REIMS_ROMS / "ReimsGraphics.rom",
                ROOT / "dist" / "share" / "reims-vgpu" / "ReimsGraphics.rom",
                ROOT / "firmware" / "ReimsGraphics.rom"):
        p = Path(rel)
        if p.exists():
            return p
    return REIMS_ROMS / "ReimsGraphics.rom"


def _apple_rom() -> Path:
    bundled = REIMS_ROMS / "ReimsVgpu.rom"
    return bundled if bundled.is_file() else ROOT / "build" / "hw" / "display" / "ReimsVgpu.rom"


REIMS = GpuProfile(
    key="reims",
    title="Reims-Graphics",
    qemu_device="reims-graphics-pci",
    rom_name="ReimsGraphics.rom",
    rom_path=_reims_rom(),
    enable=REIMS_KECTS,
    disable=APPLE_GFX_KECTS,
    max_resolution="3840x2160",
    notes="Primary GPU.",
)

APPLE_GFX = GpuProfile(
    key="apple-gfx",
    title="Apple-GFX",
    qemu_device="apple-gfx-pci",
    rom_name="ReimsVgpu.rom",
    rom_path=_apple_rom(),
    enable=APPLE_GFX_KECTS,
    disable=REIMS_KECTS,
    max_resolution="1920x1080",
    notes="Apple-GFX device with Lilu + ReimsMetalCompat. "
          "Maximum resolution 1920x1080.",
)

PROFILES = {"reims": REIMS, "apple-gfx": APPLE_GFX}

RESOLUTIONS = [
    "854x480", "1024x600", "1280x720", "1600x900",
    "1920x1080", "2560x1440", "3840x2160", "Max",
]

MAX_RESOLUTION = "Max"


def host_max_resolution() -> str:
    """Return the host's primary monitor native size as WxH.

    Uses Win32 EnumDisplaySettings (all modes, picks the largest) with a
    ctypes fallback to GetSystemMetrics; returns 1920x1080 if unavailable.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class DEVMODE(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmOrientation", ctypes.c_short),
                ("dmPaperSize", ctypes.c_short),
                ("dmPaperLength", ctypes.c_short),
                ("dmPaperWidth", ctypes.c_short),
                ("dmScale", ctypes.c_short),
                ("dmCopies", ctypes.c_short),
                ("dmDefaultSource", ctypes.c_short),
                ("dmPrintQuality", ctypes.c_short),
                ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short),
                ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
            ]

        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        user32 = ctypes.windll.user32
        best = (0, 0)
        i = 0
        while user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
            if dm.dmPelsWidth * dm.dmPelsHeight > best[0] * best[1]:
                best = (dm.dmPelsWidth, dm.dmPelsHeight)
            i += 1
            if i > 4096:
                break
        if best[0] and best[1]:
            return f"{best[0]}x{best[1]}"
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return f"{w}x{h}"
    except Exception:
        pass
    return "1920x1080"


def resolve_resolution(value: str) -> str:
    """Turn the UI's Max choice into a concrete WxH for the QEMU device."""
    if value == MAX_RESOLUTION:
        return host_max_resolution()
    return value


def get_profile(key: str) -> GpuProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(f"unknown GPU profile: {key!r}")


def availability() -> dict:
    """Report which of the six kext bundles are staged on disk.

    Kexts are staged per-VM under <vm>/kexts/ by the manager. This function
    looks at the shared staging folder only; the UI shows the real per-VM view.
    """
    staged = ROOT / "manager" / "kexts"
    return {k: (staged / k).exists() for k in ALL_KECTS}
