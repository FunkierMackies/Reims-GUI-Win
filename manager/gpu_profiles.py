"""GPU profiles: device + ROM + kext set for the two interchangeable stacks.

AppleMCEReporterDisabler.kext is never enabled/disabled by the manager - it
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
    notes="Primary GPU on the ported stack.",
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
    "1920x1080", "2560x1440", "3840x2160",
]


def get_profile(key: str) -> GpuProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(f"unknown GPU profile: {key!r}")


def availability() -> dict:
    """Report which of the seven kext bundles are staged on disk.

    Kexts are staged per-VM under <vm>/kexts/ by the manager. This function
    looks at the shared staging folder only; the UI shows the real per-VM view.
    """
    staged = ROOT / "manager" / "kexts"
    return {k: (staged / k).exists() for k in ALL_KECTS}
