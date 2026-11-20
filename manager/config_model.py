"""VM configuration model and JSON persistence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from paths import ROOT

VMS_DIR = ROOT / "vms"
BACKEND = ROOT / "manager" / "Backend-Apps"
FIRMWARE_DIR = BACKEND / "Firmware"
if not (FIRMWARE_DIR / "OpenCore-rgfx-3kext.img").is_file():
    FIRMWARE_DIR = ROOT / "firmware"
REIMS_QEMU_DIR = BACKEND / "ReimsQEMU"
if not (REIMS_QEMU_DIR / "qemu-system-x86_64.exe").is_file():
    REIMS_QEMU_DIR = ROOT / "build"

DEFAULT_DISK = str(ROOT / "macOS-disks" / "macOS.qcow2")
DEFAULT_FIRMWARE = str(FIRMWARE_DIR / "OpenCore-rgfx-3kext.img")
DEFAULT_QEMU = str(REIMS_QEMU_DIR / "qemu-system-x86_64.exe")
ROOT_TOKEN = "{ROOT}/"
_APP_TOKENS = ("vms", "firmware", "build", "macOS-disks",
               "manager", "dist", "scripts")


def _rel_to_root(value: str) -> str | None:
    try:
        return Path(value).resolve().relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def store_path(value: str) -> str:
    """Store app-internal paths as {ROOT}/... so the drive letter is irrelevant."""
    if not value:
        return value
    rel = _rel_to_root(value)
    return ROOT_TOKEN + rel if rel is not None else str(value)


def load_path(value: str) -> str:
    """Expand {ROOT}/... tokens; re-anchor legacy absolute paths to this ROOT."""
    if not value:
        return value
    if value.startswith(ROOT_TOKEN):
        return str(ROOT / value[len(ROOT_TOKEN):])
    parts = Path(value).parts
    for token in _APP_TOKENS:
        if token in parts:
            i = parts.index(token)
            return str(ROOT.joinpath(*parts[i:]))
    return value


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return cleaned or "vm"


@dataclass
class VMConfig:
    name: str = "macOS Tahoe"
    disk: str = DEFAULT_DISK
    secondary_drive: str = ""     # optional SATA qcow2/vhd disk
    firmware_image: str = DEFAULT_FIRMWARE
    qemu_exe: str = DEFAULT_QEMU
    install_iso: str = ""          # optional installer ISO to attach

    cpu: str = ""                  # -cpu string from presets
    cores: int = 4                 # capped at host physical cores in the UI
    memory_mb: int = 4096
    accel: str = "whpx,hyperv=off,intercept-msr-gp=on"

    nvme_model: str = "TM8FP6512G"
    nvme_serial: str = "TM8FP4512G0C101"
    audio_backend: str = "dsound"  # -audiodev dsound,id=snd0

    gpu_profile: str = "reims"     # reims | apple-gfx
    resolution: str = "1280x720"
    smbios: dict = field(default_factory=dict)

    @property
    def vm_dir(self) -> Path:
        return VMS_DIR / safe_name(self.name)

    @property
    def config_path(self) -> Path:
        return self.vm_dir / f"{safe_name(self.name)}.json"

    @property
    def legacy_config_path(self) -> Path:
        return self.vm_dir / "vm.json"

    @property
    def local_firmware(self) -> Path:
        return self.vm_dir / "OpenCore.img"

    @property
    def launch_script(self) -> Path:
        return self.vm_dir / f"{safe_name(self.name)}.ps1"

    @property
    def legacy_launch_script(self) -> Path:
        """Old location (root/generated/) - read-only, for cleanup/import."""
        return ROOT / "generated" / f"{safe_name(self.name)}.ps1"

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("disk", "secondary_drive", "firmware_image",
                    "qemu_exe", "install_iso"):
            data[key] = store_path(data[key])
        return data

    @staticmethod
    def from_dict(data: dict) -> "VMConfig":
        values = {
            k: v for k, v in (data or {}).items()
            if k in VMConfig.__dataclass_fields__
        }
        for key in ("disk", "secondary_drive", "firmware_image",
                    "qemu_exe", "install_iso"):
            values[key] = load_path(values.get(key, ""))
        return VMConfig(**values)

    def save(self) -> Path:
        self.vm_dir.mkdir(parents=True, exist_ok=True)
        target = self.config_path
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        # Remove an old vm.json if present so there is one canonical file.
        legacy = self.legacy_config_path
        if legacy != target and legacy.exists():
            legacy.unlink()
        return target

    @classmethod
    def load(cls, name: str) -> "VMConfig":
        directory = VMS_DIR / safe_name(name)
        path = directory / f"{safe_name(name)}.json"
        if not path.is_file():  # legacy layout
            path = directory / "vm.json"
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def list_names(cls) -> list:
        """Return the display names saved in each VM's JSON, not folder names."""
        if not VMS_DIR.exists():
            return []
        names = []
        for p in VMS_DIR.iterdir():
            if not p.is_dir():
                continue
            path = p / f"{p.name}.json"
            if not path.is_file():
                path = p / "vm.json"
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                names.append(str(data.get("name") or p.name))
            except (ValueError, OSError):
                names.append(p.name)
        return sorted(names)

    def prepare_firmware(self) -> Path:
        """Create the VM-local firmware copy if absent, return its path."""
        self.vm_dir.mkdir(parents=True, exist_ok=True)
        target = self.local_firmware
        if not target.exists():
            src = Path(self.firmware_image)
            if not src.is_file():
                raise FileNotFoundError(f"base firmware not found: {src}")
            import shutil
            shutil.copy2(src, target)
        return target

    def disk_in_use_elsewhere(self) -> bool:
        """True if another VM's config references this VM's qcow2 disk."""
        if not self.disk:
            return False
        target = Path(self.disk).resolve()
        for name in self.list_names():
            if name == self.name:
                continue
            try:
                other = self.load(name)
            except (OSError, ValueError):
                continue
            if other.disk and Path(other.disk).resolve() == target:
                return True
        return False

    def delete_all(self, include_disk: bool = True) -> list[Path]:
        """Permanently delete this VM's files. Returns the paths removed.

        Removes the whole VM directory (config, ps1, OpenCore.img, vars.fd,
        logs) plus the main and secondary qcow2/vhd disks when requested.
        Callers must confirm first.
        """
        import shutil
        removed: list[Path] = []
        if self.vm_dir.is_dir():
            removed.append(self.vm_dir)
            shutil.rmtree(self.vm_dir)
        else:
            for p in (self.config_path, self.legacy_config_path,
                      self.launch_script, self.legacy_launch_script):
                if p.is_file():
                    p.unlink()
                    removed.append(p)
        if include_disk:
            for value in (self.disk, getattr(self, "secondary_drive", "")):
                if not value:
                    continue
                disk = Path(value)
                if disk.is_file() and disk.suffix.lower() in (".qcow2", ".vhd"):
                    disk.unlink()
                    removed.append(disk)
        return removed
