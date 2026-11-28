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

# reims-ctl / reims-util default boot-args (NVRAM GUID 7C436110-...).
NVRAM_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
REFERENCE_BOOT_ARGS = "keepsyms=1 tlbto_us=0 vti=9"

from gpu_profiles import (  # noqa: E402
    ALL_KECTS, NEUTRAL_KECTS, get_profile,
)


def _fat(image: Path) -> fatsync.Fat16:
    return fatsync.Fat16(str(image))


def read_config(image: Path) -> dict:
    fat = _fat(image)
    try:
        entry, _ = fat.lookup(CONFIG_PATH)
        if entry is None:
            raise FileNotFoundError(f"{CONFIG_PATH} not in {image}")
        # Follow the FAT chain: the file can be fragmented after a config
        # grows (e.g. adding CustomMemory), and a contiguous read would pick
        # up stale bytes from the wrong cluster.
        chunks = []
        remaining = entry["size"]
        for cluster in fat.chain(entry["cluster"]):
            if remaining <= 0:
                break
            take = min(remaining, fat.cluster_bytes)
            chunks.append(fat.read_at(fat.cluster_off(cluster), take))
            remaining -= take
        return plistlib.loads(b"".join(chunks))
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
                if not src.is_dir():
                    continue
                # Replace bundles that are already in the image too: the
                # staged copies are the current source of truth, so an old
                # bundle must never survive a settings save.
                if name in present:
                    fats.delete(f"{KECTS_DIR}/{name}")
                    present.discard(name)
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

    write_config(image, cfg)
    return report


# Booter/Kernel quirks from the known-good reims-ctl OpenCore image.
# These differ from the base image and can freeze boot at the Apple logo.
REFERENCE_BOOTER_QUIRKS = {
    "AvoidRuntimeDefrag": True,
    "EnableSafeModeSlide": True,
    "EnableWriteUnprotector": True,
    "ProvideCustomSlide": True,
    "FixupAppleEfiImages": False,
    "RebuildAppleMemoryMap": False,
    "SetupVirtualMap": False,
    "SyncRuntimePermissions": False,
}
REFERENCE_KERNEL_QUIRKS = {
    "AppleXcpmExtraMsrs": False,
    "ForceSecureBootScheme": True,
    "ProvideCurrentCpuInfo": True,
    "SetApfsTrimTimeout": 0,
}


def apply_reference_quirks(image: Path) -> dict:
    """Align OpenCore quirks/security with the working reims-ctl image."""
    cfg = read_config(image)
    booter = cfg.setdefault("Booter", {}).setdefault("Quirks", {})
    booter.update(REFERENCE_BOOTER_QUIRKS)
    kernel = cfg.setdefault("Kernel", {}).setdefault("Quirks", {})
    kernel.update(REFERENCE_KERNEL_QUIRKS)
    security = cfg.setdefault("Misc", {}).setdefault("Security", {})
    security["SecureBootModel"] = "Disabled"
    security["AllowSetDefault"] = True
    write_config(image, cfg)
    return {"image": str(image), "booter": REFERENCE_BOOTER_QUIRKS,
            "kernel": REFERENCE_KERNEL_QUIRKS}


def apply_custom_memory(image: Path, *,
                        memory_mb: int = 4096, speed: int = 2666,
                        manufacturer: str = "", serial: str = "",
                        part: str = "") -> dict:
    """Inject host-detected RAM identity into OpenCore PlatformInfo.Memory.

    QEMU's -smbios type=17 fields for manufacturer/serial/part do not reach
    macOS reliably; OpenCore's CustomMemory table does. A single populated
    module carries the host DIMM details plus the guest RAM size. The SMBIOS
    model (MacPro7,1 / iMacPro1,1) comes from config.plist.

    Empty identity values never overwrite an existing good value: host
    detection can fail intermittently (e.g. PowerShell unavailable in a
    frozen build), and writing blanks would make macOS show a bare "DIMM 1".
    """
    cfg = read_config(image)
    pi = cfg.setdefault("PlatformInfo", {})
    model = (pi.get("Generic", {}).get("SystemProductName") or "MacPro7,1")
    form = 13 if model == "iMacPro1,1" else 9

    # Preserve previously injected identity when this run detected nothing.
    old_devices = pi.get("Memory", {}).get("Devices") or []
    old = old_devices[0] if old_devices else {}
    manufacturer = manufacturer or old.get("Manufacturer", "")
    serial = serial or old.get("SerialNumber", "")
    part = part or old.get("PartNumber", "")

    pi["Memory"] = {
        "DataWidth": 64,
        "ErrorCorrection": 3,
        "FormFactor": form,
        # OpenCore stores MaxCapacity in BYTES (SmbiosPatch.c divides by 1KB).
        "MaxCapacity": 256 * 1024 * 1024 * 1024,
        "TotalWidth": 64,
        "Type": 26,
        "TypeDetail": 128,
        "Devices": [{
            "AssetTag": "",
            "BankLocator": "",
            "DeviceLocator": "DIMM 1",
            "Manufacturer": manufacturer,
            "PartNumber": part,
            "SerialNumber": serial,
            "Size": memory_mb,
            "Speed": speed,
        }],
    }
    pi["CustomMemory"] = True
    write_config(image, cfg)
    return {"image": str(image), "model": model, "form_factor": form,
            "manufacturer": manufacturer, "serial": serial, "part": part}


def apply_opencore_gui(image: Path, *,
                       resources_dir: Path | None = None,
                       opencanopy: Path | None = None) -> dict:
    """Install OpenCanopy + OcBinaryData Resources and enable the GUI picker.

    Per Dortania: Misc->Boot PickerMode=External, PickerAttributes=17,
    PickerVariant='Acidanthera\\GoldenGate', and OpenCanopy.efi in
    UEFI->Drivers. The OpenCanopy build must match the image's OpenCore.efi.
    """
    image = Path(image)
    drivers = Path(opencanopy) if opencanopy else None
    resources = Path(resources_dir) if resources_dir else None

    report: dict = {"opencanopy_added": False, "resources_added": False,
                    "config_changed": False}

    f = _fat(image)
    try:
        drv_parent, _ = f.lookup(KECTS_DIR.replace("Kexts", "Drivers"))
        if drv_parent is None:
            drv_parent, _ = f.lookup("EFI/OC")
            drv_parent = f.mkdir(drv_parent, "Drivers")
        if drivers and drivers.is_file():
            existing, _ = f.lookup("EFI/OC/Drivers/OpenCanopy.efi")
            if existing is None:
                f.put(drv_parent, "OpenCanopy.efi", drivers.read_bytes())
                report["opencanopy_added"] = True

        oc_parent, _ = f.lookup("EFI/OC")
        if resources and resources.is_dir():
            existing, _ = f.lookup("EFI/OC/Resources")
            if existing is None:
                f.add_tree_into(str(resources), f.mkdir(oc_parent, "Resources"))
                report["resources_added"] = True
    finally:
        f.close()

    cfg = read_config(image)
    boot = cfg.setdefault("Misc", {}).setdefault("Boot", {})
    if (boot.get("PickerMode") != "External"
            or boot.get("PickerAttributes") != 17
            or boot.get("PickerVariant") != "Acidanthera\\GoldenGate"):
        boot["PickerMode"] = "External"
        boot["PickerAttributes"] = 17
        boot["PickerVariant"] = "Acidanthera\\GoldenGate"
        report["config_changed"] = True
    drivers_list = cfg.setdefault("UEFI", {}).setdefault("Drivers", [])
    paths = {d.get("Path") if isinstance(d, dict) else d for d in drivers_list}
    if "OpenCanopy.efi" not in paths:
        drivers_list.append({
            "Arguments": "", "Comment": "", "Enabled": True,
            "HideVerbose": False, "LoadEarly": False,
            "Path": "OpenCanopy.efi",
        })
        report["config_changed"] = True
    elif "OpenCanopy.efi" in drivers_list:  # repair a plain-string entry
        idx = drivers_list.index("OpenCanopy.efi")
        drivers_list[idx] = {
            "Arguments": "", "Comment": "", "Enabled": True,
            "HideVerbose": False, "LoadEarly": False,
            "Path": "OpenCanopy.efi",
        }
        report["config_changed"] = True
    if report["config_changed"]:
        write_config(image, cfg)
    return report


def apply_picker_style(image: Path, *, background_icns: Path | None = None,
                       timeout: int = 15) -> dict:
    """Set the OpenCanopy picker timeout and install a custom background.

    Background.icns goes in EFI/OC/Resources/Image/Acidanthera/GoldenGate so
    it is picked up by the configured GoldenGate PickerVariant. OpenCanopy
    decodes ic07 (scale 1) / ic13 (scale 2) PNG chunks with no size match.
    """
    image = Path(image)
    report: dict = {"background_added": False, "config_changed": False}

    bg = Path(background_icns) if background_icns else None
    if bg and bg.is_file():
        f = _fat(image)
        try:
            parent, _ = f.lookup("EFI/OC/Resources/Image/Acidanthera/GoldenGate")
            if parent is not None:
                existing, _ = f.lookup(
                    "EFI/OC/Resources/Image/Acidanthera/GoldenGate/Background.icns")
                if existing is None:
                    f.put(parent, "Background.icns", bg.read_bytes())
                    report["background_added"] = True
        finally:
            f.close()

    cfg = read_config(image)
    boot = cfg.setdefault("Misc", {}).setdefault("Boot", {})
    if boot.get("Timeout") != timeout:
        boot["Timeout"] = timeout
        report["config_changed"] = True
    if report["config_changed"]:
        write_config(image, cfg)
    return report


def apply_bootargs(image: Path, boot_args: str = REFERENCE_BOOT_ARGS) -> dict:
    """Write the reims-ctl boot-args into a VM-local OpenCore image."""
    image = Path(image)
    cfg = read_config(image)
    add = cfg.setdefault("NVRAM", {}).setdefault("Add", {})
    nv = add.setdefault(NVRAM_GUID, {})
    nv["boot-args"] = boot_args
    # reims-ctl keeps SIP enabled and no verbose/debug flags.
    nv["csr-active-config"] = b"\x00\x00\x00\x00"
    cfg.setdefault("NVRAM", {}).setdefault("Delete", {}).setdefault(
        NVRAM_GUID, [])
    delete = cfg["NVRAM"]["Delete"][NVRAM_GUID]
    for key in ("boot-args", "ForceDisplayRotationInEFI",
                "csr-active-config", "prev-lang:kbd"):
        if key not in delete:
            delete.append(key)
    write_config(image, cfg)
    return {"boot-args": boot_args, "image": str(image)}


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
                value = bytes.fromhex(value.replace(":", "").replace(" ", ""))
            generic[plist_key] = value
            written[plist_key] = value if plist_key != "ROM" else "<bytes>"
    write_config(image, cfg)
    return written
