"""Generate a PowerShell 7 launch script from a VMConfig + GPU profile.

build_qemu_args() is the single source of truth for the QEMU argv. The script
renderer only quotes that list for PowerShell, so the exact same arguments can
be tested from Python and emitted to the .ps1.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config_model import (VMConfig, ROOT as APP_ROOT,
                          FIRMWARE_DIR, REIMS_QEMU_DIR)
from gpu_profiles import get_profile, resolve_resolution
from paths import QEMU_RUNTIME, APPLE_QEMU, qemu_for_profile, runtime_env

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
    rom = _rom_path(cfg)
    resolution = resolve_resolution(cfg.resolution)
    smp = f"{cfg.cores},sockets=1,cores={cfg.cores},threads=1"
    memory = f"{cfg.memory_mb}M"
    vmdir = str(cfg.vm_dir)
    ovmf_code = str(Path(ROOT_FIRMWARE) / "OVMF_CODE.fd")
    vars_path = str(cfg.vm_dir / "vars.fd")
    oc = str(cfg.local_firmware)

    # reims-ctl: dsound without a capture device gets a silent input backend.
    # Other backends use one audiodev for both directions.
    backend = cfg.audio_backend or "dsound"
    input_backend = "none" if backend == "dsound" else backend
    input_audiodev = "sndin" if input_backend != backend else "snd0"
    audio_devs = ["-audiodev", f"{backend},id=snd0"]
    if input_backend != backend:
        audio_devs += ["-audiodev", f"{input_backend},id=sndin"]

    args = [
        "-name", "reims-gui-vm",
        "-machine", "q35,memory-backend=ram,max-ram-below-4g=1G",
        "-object", f"memory-backend-ram,id=ram,size={memory},share=on",
        "-accel", cfg.accel,
        "-cpu", cfg.cpu,
        "-smp", smp,
        "-m", memory,
        # Match reims-ctl (qemu.rs): the new QEMU build uses speed/width, not
        # the old x-speed/x-width properties (which abort every launch).
        "-global", "ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off",
        "-global", "ICH9-LPC.disable_s3=1",
        "-device", "pcie-root-port,bus=pcie.0,addr=0x1,slot=1,speed=16,width=32",
        "-device", "isa-applesmc,osk=ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc",
        "-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
        "-drive", f"if=pflash,format=raw,file={vars_path}",
        "-smbios", "type=2",
        "-smbios", "type=17,speed=2666,memory_type=26",
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
        "-device", "virtio-net-pci,netdev=net0,id=net0,mac=52:54:00:00:00:01,addr=0x3",
        "-device", f"{profile.qemu_device},id=gfx,bus=pcie.0,addr=0x4,romfile={rom},resolution={resolution}",
        # Audio matches reims-ctl (qemu.rs): separate output/input streams,
        # plus an HDA boot-chime output. The input device uses a silent
        # audiodev when the backend has no capture capability.
        *audio_devs,
        "-device", "virtio-sound-pci,id=audio,audiodev=snd0,addr=0x8,streams=1",
        "-device", f"virtio-sound-pci,id=audioin,audiodev={input_audiodev},"
                   "addr=0xa,streams=1,input-streams=1",
        "-device", "ich9-intel-hda,id=hda,addr=0x1b",
        "-device", "hda-output,bus=hda.0,audiodev=snd0",
        "-device", "qemu-xhci,id=xhci,addr=0x5,p2=7,p3=7,conditional-intr-mapping=on",
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
