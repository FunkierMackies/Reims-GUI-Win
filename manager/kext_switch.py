"""Switch the guest kext set and SMBIOS inside a per-VM OpenCore image.

Design rules (from the manager spec):
  * Operate on a VM-local COPY of the firmware image; never mutate the base
    OpenCore-rgfx-3kext.img shipped with the build.
  * Six managed kexts: Graft, ReimsGraphics, RgfxGate (Reims), Lilu and
    ReimsMetalCompat (Apple-GFX), plus AppleMCEReporterDisabler (neutral).
  * AppleMCEReporterDisabler.kext is NEVER enabled/disabled - untouched.
  * Switching a profile also changes the launch device + ROM (launch_gen.py).

The FAT image helpers (scripts/fatimg.py, scripts/fatsync.py) are the existing
tooling in this build; we reuse them instead of mounting the image.
"""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path

from paths import ROOT

SCRIPTS = ROOT / "manager" / "Backend-Apps" / "Scripts"
if not (SCRIPTS / "fatsync.py").is_file():
    SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fatsync  # noqa: E402  (local helper, path injected above)

CONFIG_PATH = "EFI/OC/config.plist"
KECTS_DIR = "EFI/OC/Kexts"

from gpu_profiles import (  # noqa: E402
    ALL_KECTS, NEUTRAL_KECTS, get_profile,
)
from smbios import normalize_rom

NIC_PCI_PATH = "PciRoot(0x0)/Pci(0x3,0x0)"
VMM_PATCH_COMMENT = "Reims: kern.hv_vmm_present -> 0"
_VMM_FIND = bytes.fromhex(
    "4889cb" "e800000000" "48c1e83f" "8945f4" "488d75f4" "ba04000000"
    "4889df" "ff5328")
_VMM_MASK = bytes.fromhex("ffffff" "ff00000000") + b"\xff" * (len(_VMM_FIND) - 8)
_VMM_REPLACE = _VMM_FIND[:8] + bytes.fromhex("31c09090") + _VMM_FIND[12:]
_VMM_REPLACE_MASK = b"\x00" * 8 + b"\xff" * 4 + b"\x00" * (len(_VMM_FIND) - 12)


def ensure_builtin_nic(cfg: dict) -> bool:
    add = cfg.setdefault("DeviceProperties", {}).setdefault("Add", {})
    props = add.setdefault(NIC_PCI_PATH, {})
    if props.get("built-in") == b"\x01":
        return False
    props["built-in"] = b"\x01"
    return True


def ensure_vmm_patch(cfg: dict) -> bool:
    patches = cfg.setdefault("Kernel", {}).setdefault("Patch", [])
    if any(p.get("Comment") == VMM_PATCH_COMMENT for p in patches):
        return False
    patches.append({
        "Arch": "x86_64",
        "Base": "",
        "Comment": VMM_PATCH_COMMENT,
        "Count": 1,
        "Enabled": True,
        "Find": _VMM_FIND,
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": _VMM_MASK,
        "MaxKernel": "",
        "MinKernel": "25.0.0",
        "Replace": _VMM_REPLACE,
        "ReplaceMask": _VMM_REPLACE_MASK,
        "Skip": 0,
    })
    return True


def apply_apple_services(image: Path) -> dict:
    image = Path(image)
    cfg = read_config(image)
    report = {
        "builtin_nic_added": ensure_builtin_nic(cfg),
        "vmm_patch_added": ensure_vmm_patch(cfg),
    }
    if any(report.values()):
        write_config(image, cfg)
    return report


def _fat(image: Path) -> fatsync.Fat16:
    return fatsync.Fat16(str(image))


def read_config(image: Path) -> dict:
    fat = _fat(image)
    try:
        entry, _ = fat.lookup(CONFIG_PATH)
        if entry is None:
            raise FileNotFoundError(f"{CONFIG_PATH} not in {image}")
        data = fat.read_at(fat.cluster_off(entry["cluster"]), entry["size"])
        return plistlib.loads(data)
    finally:
        fat.close()


def write_config(image: Path, cfg: dict) -> int:
    """Write config.plist back, allowing it to grow (fatsync.replace_file)."""
    data = plistlib.dumps(cfg, sort_keys=False)
    fat = _fat(image)
    try:
        fat.replace_file(CONFIG_PATH, data)
        return len(data)
    finally:
        fat.close()


def list_image_kexts(image: Path) -> list:
    fat = _fat(image)
    try:
        entry, _ = fat.lookup(KECTS_DIR)
        if entry is None:
            return []
        return [r["name"] for r in fat.list_dir(entry)
                if r["attr"] & fatsync.ATTR_DIR]
    finally:
        fat.close()


def stage_kext(source: Path, image: Path) -> bool:
    """Copy one staged .kext bundle from disk into the image's Kexts dir."""
    if not source.is_dir():
        return False
    fat = _fat(image)
    try:
        parent, _ = fat.lookup(KECTS_DIR)
        if parent is None:
            raise FileNotFoundError(f"no {KECTS_DIR} in image")
        # add_tree_into copies the children of `source`, so pass the bundle's
        # parent dir with a single-bundle staging layout, or add the bundle
        # tree directly via mkdir/add_tree_into.
        fat.mkdir(parent, source.name)
        newdir, _ = fat.lookup(f"{KECTS_DIR}/{source.name}")
        fat.add_tree_into(str(source), newdir)
        return True
    finally:
        fat.close()


def apply_profile(image: Path, profile_key: str, *,
                  staged_kexts: Path | None = None,
                  backup: bool = False) -> dict:
    """Apply a GPU profile to a VM-local OpenCore image.

    Returns a report dict. Never raises on missing kexts - reports them.
    """
    profile = get_profile(profile_key)
    image = Path(image)
    if not image.is_file():
        raise FileNotFoundError(f"OpenCore image not found: {image}")

    if backup:
        bak = image.with_suffix(image.suffix + f".bak-{profile_key}")
        if not bak.exists():
            shutil.copy2(image, bak)

    present = set(list_image_kexts(image))
    cfg = read_config(image)
    add = cfg.setdefault("Kernel", {}).setdefault("Add", [])

    # Index existing entries by BundlePath.
    by_path = {}
    for entry in add:
        bp = entry.get("BundlePath")
        if isinstance(bp, str):
            by_path[bp] = entry

    report = {
        "profile": profile_key,
        "image": str(image),
        "enabled": [],
        "disabled": [],
        "untouched": [],
        "missing": [],
        "added_to_config": [],
    }

    def set_enabled(name: str, value: bool):
        entry = by_path.get(name)
        if entry is None:
            # Only create an entry for a bundle the image actually contains.
            if name in present:
                entry = {
                    "BundlePath": name,
                    "Enabled": value,
                    "Comment": "managed by Reims GUI",
                    "ExecutablePath": f"Contents/MacOS/{name[:-5]}",
                    "PlistPath": "Contents/Info.plist",
                    "MinKernel": "20.0.0",
                    "MaxKernel": "",
                    "Arch": "x86_64",
                }
                add.append(entry)
                by_path[name] = entry
                report["added_to_config"].append(name)
            else:
                report["missing"].append(name)
                return
        if name in NEUTRAL_KECTS:
            report["untouched"].append(name)
            return
        entry["Enabled"] = value
        (report["enabled"] if value else report["disabled"]).append(name)

    # Optionally copy staged bundles into the image first. Do this once for
    # all missing bundles, rather than repeating a whole-folder add per name.
    if staged_kexts:
        staged = Path(staged_kexts)
        fats = _fat(image)
        try:
            parent, _ = fats.lookup(KECTS_DIR)
            if parent is None:
                raise FileNotFoundError(f"no {KECTS_DIR} in image")
            for name in ALL_KECTS:
                src = staged / name
                if name not in present and src.is_dir():
                    fats.mkdir(parent, name)
                    newdir, _ = fats.lookup(f"{KECTS_DIR}/{name}")
                    fats.add_tree_into(str(src), newdir)
                    present.add(name)
        finally:
            fats.close()

    for name in profile.enable:
        set_enabled(name, True)
    for name in profile.disable:
        set_enabled(name, False)
    for name in NEUTRAL_KECTS:
        report["untouched"].append(name)

    report["builtin_nic_added"] = ensure_builtin_nic(cfg)
    report["vmm_patch_added"] = ensure_vmm_patch(cfg)
    write_config(image, cfg)
    return report


def apply_smbios(image: Path, values: dict, *, backup: bool = False) -> dict:
    """Write SystemProductName/SerialNumber/SystemUUID/MLB/ROM into config.plist."""
    image = Path(image)
    if backup:
        shutil.copy2(image, str(image) + ".bak-smbios")
    cfg = read_config(image)
    generic = cfg.setdefault("PlatformInfo", {}).setdefault("Generic", {})
    mapping = {
        "SystemProductName": "model",
        "SystemSerialNumber": "serial",
        "SystemUUID": "uuid",
        "MLB": "mlb",
        "ROM": "rom",
    }
    written = {}
    for plist_key, value in values.items():
        if plist_key in mapping and value:
            if plist_key == "ROM" and isinstance(value, str):
                value = bytes.fromhex(
                    (normalize_rom(value) or value).replace(":", "").replace(" ", ""))
            generic[plist_key] = value
            written[plist_key] = value if plist_key != "ROM" else "<bytes>"
    write_config(image, cfg)
    return written
