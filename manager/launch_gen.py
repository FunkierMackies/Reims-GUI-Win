"""Generate a PowerShell 7 launch script from a VMConfig + GPU profile.

build_qemu_args() is the single source of truth for the QEMU argv. The script
renderer only quotes that list for PowerShell, so the exact same arguments can
be tested from Python and emitted to the .ps1.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from config_model import (VMConfig, ROOT as APP_ROOT,
                          FIRMWARE_DIR, REIMS_QEMU_DIR)
import host_detect
from gpu_profiles import get_profile, resolve_resolution
from paths import QEMU_RUNTIME, APPLE_QEMU, qemu_for_profile, runtime_env
from smbios import nic_mac

@dataclass(frozen=True)
class LaunchSpec:
    """QEMU-argument differences between the two GPU stacks.

    The two bundled QEMU builds are different versions: ReimsQEMU 11.1.90
    exposes pcie-root-port speed/width and accepts -smbios memory_type, while
    AppleGFX-QEMU 11.1.50 only exposes x-speed/x-width and rejects memory_type.
    Keeping their settings in one place stops a change for one stack breaking
    the other.
    """
    root_port_props: str
    sound: str          # "reference" (dual streams) or "classic"
    xhci_ports: str     # p2/p3 values


SPECS = {
    "reims": LaunchSpec(
        root_port_props="speed=16,width=32",
        sound="reference",
        xhci_ports="p2=7,p3=7",
    ),
    "apple-gfx": LaunchSpec(
        root_port_props="x-speed=16,x-width=32",
        sound="classic",
        xhci_ports="p2=8,p3=6",
    ),
}


def spec_for(key: str) -> LaunchSpec:
    return SPECS.get(key, SPECS["reims"])


ROOT_FIRMWARE = str(FIRMWARE_DIR)
BIOS_DIR = str(REIMS_QEMU_DIR / "pc-bios")
if not (Path(BIOS_DIR) / "bios-256k.bin").is_file():
    BIOS_DIR = str(APP_ROOT / "build" / "pc-bios")


def ps(value) -> str:
    """PowerShell single-quoted literal with Windows-safe forward slashes."""
    s = str(value).replace("\\", "/").replace("'", "''")
    return "'" + s + "'"


_ROOT_FWD = None


def ps_arg(value) -> str:
    """Quote an argv element, expressing app-root paths via $root.

    PowerShell single quotes never interpolate, so arguments containing a
    root-anchored path are emitted as double-quoted strings with `$root`.
    """
    global _ROOT_FWD
    if _ROOT_FWD is None:
        _ROOT_FWD = str(APP_ROOT).replace("\\", "/")
    s = str(value).replace("\\", "/")
    if _ROOT_FWD not in s:
        return ps(value)
    sentinel = "\x00R\x00"
    s = s.replace(_ROOT_FWD, sentinel)
    # Escape characters PowerShell would interpret inside double quotes.
    s = s.replace("`", "``").replace('"', '`"').replace("$", "`$")
    s = s.replace(sentinel, "${root}")
    return '"' + s + '"'


def accel_for_host(cfg: VMConfig) -> str:
    """intercept-msr-gp is an AMD-host workaround; QEMU defaults it off on Intel."""
    accel = cfg.accel or "whpx"
    if host_detect.detect_host().is_intel:
        parts = [p for p in accel.split(",")
                 if p.strip() and p.strip() != "intercept-msr-gp=on"]
        accel = ",".join(parts)
    return accel or "whpx"


def host_ram_speed(default: int = 2666) -> int:
    """First host DIMM's configured clock speed, or default if unavailable."""
    import ram_info
    module = ram_info.first_module()
    speed = module.speed if module else 0
    return speed if speed and 100 <= speed <= 10000 else default


def smbios_type17_value(qemu_exe) -> str:
    """Build the type=17 value with host-detected speed and memory_type.

    Host RAM identity (manufacturer/serial/part) is injected into OpenCore's
    CustomMemory table instead. Apple-GFX QEMU rejects memory_type, so that
    field is omitted there and DDR4 (26) is set in CustomMemory instead.
    """
    fields = ["type=17", f"speed={host_ram_speed()}"]
    fields.append(smbios_memory_suffix(qemu_exe).lstrip(","))
    return ",".join(f for f in fields if f)


def smbios_memory_suffix(qemu_exe) -> str:
    """Match reims-ctl: only add memory_type=26 if this QEMU accepts it."""
    import os
    import subprocess
    key = (str(qemu_exe),
           os.stat(qemu_exe).st_mtime if os.path.isfile(qemu_exe) else None)
    cached = getattr(smbios_memory_suffix, "_cache", {})
    if key in cached:
        return cached[key]
    takes = False
    try:
        # The QEMU exe needs its sibling DLLs; use the same runtime PATH the
        # generated launcher sets, or every probe would fail with exit 127.
        from paths import APPLE_QEMU, QEMU_RUNTIME
        runtime = APPLE_QEMU if str(qemu_exe).startswith(str(APPLE_QEMU)) \
            else QEMU_RUNTIME
        env = os.environ.copy()
        env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [str(qemu_exe), "-smbios", "type=17,memory_type=26", "-version"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, creationflags=flags,
            env=env)
        takes = proc.returncode == 0
    except Exception:
        takes = False
    suffix = ",memory_type=26" if takes else ""
    cached[key] = suffix
    smbios_memory_suffix._cache = cached
    return suffix


def _rom_path(cfg: VMConfig) -> Path:
    profile = get_profile(cfg.gpu_profile)
    p = Path(profile.rom_path)
    local = cfg.vm_dir / profile.rom_name
    if local.is_file():
        p = local
    return p


def build_qemu_args(cfg: VMConfig) -> list:
    """Return the exact QEMU argv (no shell quoting)."""
    profile = get_profile(cfg.gpu_profile)
    spec = spec_for(cfg.gpu_profile)
    rom = _rom_path(cfg)
    resolution = resolve_resolution(cfg.resolution)
    accel = accel_for_host(cfg)
    qemu_exe = qemu_for_profile(cfg.gpu_profile)
    smp = f"{cfg.cores},sockets=1,cores={cfg.cores},threads=1"
    memory = f"{cfg.memory_mb}M"
    vmdir = str(cfg.vm_dir)
    ovmf_code = str(Path(ROOT_FIRMWARE) / "OVMF_CODE.fd")
    vars_path = str(cfg.vm_dir / "vars.fd")
    oc = str(cfg.local_firmware)

    backend = cfg.audio_backend or "dsound"
    if spec.sound == "reference":
        # reims-ctl: dsound without a capture device gets a silent backend;
        # dual virtio-sound devices (playback + capture).
        input_backend = "none" if backend == "dsound" else backend
        input_audiodev = "sndin" if input_backend != backend else "snd0"
        audio_devs = ["-audiodev", f"{backend},id=snd0"]
        if input_backend != backend:
            audio_devs += ["-audiodev", f"{input_backend},id=sndin"]
    else:
        # Apple-GFX: one playback-only stream (no capture, avoids Bluetooth
        # headset distortion), matching the original manager behaviour.
        input_backend = input_audiodev = None
        audio_devs = ["-audiodev", f"{backend},id=snd0"]

    args = [
        "-name", "reims-gui-vm",
        "-machine", "q35,memory-backend=ram,max-ram-below-4g=1G",
        "-object", f"memory-backend-ram,id=ram,size={memory},share=on",
        "-accel", accel,
        "-cpu", cfg.cpu,
        "-smp", smp,
        "-m", memory,
        # Match reims-ctl (qemu.rs): the new QEMU build uses speed/width, not
        # the old x-speed/x-width properties (which abort every launch).
        "-global", "ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off",
        "-global", "ICH9-LPC.disable_s3=1",
        "-device", f"pcie-root-port,bus=pcie.0,addr=0x1,slot=1,{spec.root_port_props}",
        "-device", "isa-applesmc,osk=ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc",
        "-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
        "-drive", f"if=pflash,format=raw,file={vars_path}",
        "-smbios", "type=2",
        "-smbios", smbios_type17_value(qemu_exe),
        "-device", "ich9-ahci,id=sata,addr=0x2",
        "-drive", f"id=opencore,if=none,format=raw,snapshot=on,file={oc}",
        "-device", "ide-hd,bus=sata.2,drive=opencore,bootindex=1",
        "-drive", f"id=disk,if=none,format=qcow2,file={cfg.disk}",
        "-device", f"nvme,id=nvme0,drive=disk,serial={cfg.nvme_serial},model={cfg.nvme_model},addr=0x7",
    ]
    # Optional secondary SATA disk (qcow2 or vhd). OpenCore uses sata.2, so
    # the secondary drive occupies the free sata.0 port.
    secondary = (getattr(cfg, "secondary_drive", "") or "").strip()
    if secondary:
        fmt = "vpc" if Path(secondary).suffix.lower() == ".vhd" else "qcow2"
        args += [
            "-drive", f"id=secondary,if=none,format={fmt},file={secondary}",
            "-device", "ide-hd,bus=sata.0,drive=secondary",
        ]
    args += [
        "-netdev", "user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:2222-:22",
        "-device", f"virtio-net-pci,netdev=net0,id=net0,mac={nic_mac(cfg.smbios)},addr=0x3",
        "-device", f"{profile.qemu_device},id=gfx,bus=pcie.0,addr=0x4,romfile={rom},resolution={resolution}",
        *audio_devs,
        *([
            "-device", "virtio-sound-pci,id=audio,audiodev=snd0,addr=0x8,streams=1",
            "-device", f"virtio-sound-pci,id=audioin,audiodev={input_audiodev},"
                       "addr=0xa,streams=1,input-streams=1",
        ] if spec.sound == "reference" else [
            "-device", "virtio-sound-pci,audiodev=snd0,id=snd0,"
                       "bus=pcie.0,addr=0x8,streams=1",
        ]),
        "-device", f"qemu-xhci,id=xhci,addr=0x5,{spec.xhci_ports},"
                   "conditional-intr-mapping=on",
        "-device", "usb-kbd,id=kbd,bus=xhci.0",
        "-device", "usb-tablet,id=tablet,bus=xhci.0",
        "-display", "none",
        "-vga", "none",
        "-L", BIOS_DIR,
        "-serial", f"file:{vmdir}/serial.log",
        "-qmp", "tcp:127.0.0.1:4444,server=on,wait=off",
        "-msg", "timestamp=on",
    ]

    if cfg.install_iso and Path(cfg.install_iso).is_file():
        args += [
            "-device", "ich9-ahci,id=isosata,addr=0x9",
            "-drive", f"id=install,if=none,media=cdrom,file={cfg.install_iso}",
            "-device", "ide-cd,bus=isosata.0,drive=install,bootindex=2",
        ]
    return args


def prepare_runtime_files(cfg: VMConfig) -> None:
    """Copy the clean OVMF_VARS template into the VM folder (fresh each boot)."""
    cfg.vm_dir.mkdir(parents=True, exist_ok=True)
    src = Path(ROOT_FIRMWARE) / "OVMF_VARS.fd"
    dst = cfg.vm_dir / "vars.fd"
    if src.is_file():
        shutil.copy2(src, dst)


def render_launch(cfg: VMConfig) -> str:
    profile = get_profile(cfg.gpu_profile)
    cfg.vm_dir.mkdir(parents=True, exist_ok=True)
    prepare_runtime_files(cfg)

    qemu_exe = qemu_for_profile(cfg.gpu_profile)
    runtime = APPLE_QEMU if cfg.gpu_profile == "apple-gfx" else QEMU_RUNTIME

    checks = [
        qemu_exe, cfg.disk, str(cfg.local_firmware),
        str(_rom_path(cfg)),
        str(Path(ROOT_FIRMWARE) / "OVMF_CODE.fd"),
        str(Path(ROOT_FIRMWARE) / "OVMF_VARS.fd"),
        BIOS_DIR,
    ]
    runtime_rel = runtime.relative_to(APP_ROOT).as_posix()

    lines = [
        "# Generated by Reims GUI Manager - do not hand-edit.",
        "# Profile: " + profile.title,
        "$ErrorActionPreference = 'Stop'",
        "# Paths are relative to the install root; the drive letter (C:, D:, ...)",
        "# is taken from this script's own location, so moving the tree is safe.",
        "# This script lives in <root>\\vms\\<name>\\, so root is two levels up.",
        "$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)",
        f"$env:PATH = (Join-Path $root {ps(runtime_rel)}) + ';' + $env:PATH",
        "foreach ($p in @(" + ", ".join(ps_arg(c) for c in checks) + ")) {",
        "    if (-not (Test-Path -LiteralPath $p)) { throw \"missing: $p\" }",
        "}",
        "$env:REIMS_VGPU_LOG = 'warn'",
        # reims-ctl unsets WINDOW so the device draws its own window; only 0
        # makes it headless.
        "Remove-Item Env:REIMS_VGPU_WINDOW -ErrorAction SilentlyContinue",
        "Remove-Item Env:REIMS_VGPU_WINDOW_IMPL -ErrorAction SilentlyContinue",
        *([f"$env:REIMS_VGPU_WINDOW_BORDERLESS = '1'"]
          if cfg.resolution == "Max" else []),
        "$qemuArgs = @(",
    ]
    lines.append(",\n".join("    " + ps_arg(arg) for arg in build_qemu_args(cfg)))
    lines += [
        ")",
        f"& {ps_arg(qemu_exe)} @qemuArgs",
        "exit $LASTEXITCODE",
    ]
    return "\n".join(lines) + "\n"


def write_launch(cfg: VMConfig) -> Path:
    """Write <name>.ps1 into the VM's own directory."""
    cfg.vm_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.launch_script
    path.write_text(render_launch(cfg), encoding="utf-8")
    return path
