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
from gpu_profiles import get_profile
from paths import QEMU_RUNTIME, APPLE_QEMU, qemu_for_profile, runtime_env
from smbios import nic_mac

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
    smp = f"{cfg.cores},sockets=1,cores={cfg.cores},threads=1"
    memory = f"{cfg.memory_mb}M"
    vmdir = str(cfg.vm_dir)
    ovmf_code = str(Path(ROOT_FIRMWARE) / "OVMF_CODE.fd")
    vars_path = str(cfg.vm_dir / "vars.fd")
    oc = str(cfg.local_firmware)

    args = [
        "-name", "reims-gui-vm",
        "-machine", "q35,memory-backend=ram,max-ram-below-4g=1G",
        "-object", f"memory-backend-ram,id=ram,size={memory},share=on",
        "-accel", cfg.accel,
        "-cpu", cfg.cpu,
        "-smp", smp,
        "-m", memory,
        "-global", "ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off",
        "-global", "ICH9-LPC.disable_s3=1",
        "-global", "ICH9-LPC.disable_s4=1",
        "-device", "pcie-root-port,bus=pcie.0,addr=0x1,slot=1,x-speed=16,x-width=32",
        "-device", "isa-applesmc,osk=ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc",
        "-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
        "-drive", f"if=pflash,format=raw,file={vars_path}",
        "-smbios", "type=2",
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
        "-device", f"{profile.qemu_device},id=gfx,bus=pcie.0,addr=0x4,romfile={rom},resolution={cfg.resolution}",
        "-audiodev", f"{cfg.audio_backend},id=snd0",
        # streams=1 exposes a single PCM stream. QEMU's direction formula makes
        # the first (and only) stream playback, so the guest has no capture
        # device / microphone. Avoids distortion with Bluetooth headsets.
        "-device", "virtio-sound-pci,audiodev=snd0,id=snd0,bus=pcie.0,addr=0x8,streams=1",
        "-device", "qemu-xhci,id=xhci,addr=0x5,p2=8,p3=6,conditional-intr-mapping=on",
        "-device", "usb-kbd,id=kbd,bus=xhci.0",
        "-device", "usb-tablet,id=tablet,bus=xhci.0",
        "-display", "none",
        "-vga", "none",
        "-L", BIOS_DIR,
        "-serial", f"file:{vmdir}/serial.log",
        "-qmp", f"tcp:127.0.0.1:{QMP_PORT},server=on,wait=off",
        "-msg", "timestamp=on",
        "-action", "reboot=shutdown",
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
        "$env:REIMS_VGPU_LOG = 'info'",
        "$env:REIMS_VGPU_WINDOW = '1'",
        "# QEMU has CONFIG_SDL undefined; the reims library draws the SDL window.",
        "$env:REIMS_VGPU_WINDOW_IMPL = 'sdl'",
        "$qemuArgs = @(",
    ]
    lines.append(",\n".join("    " + ps_arg(arg) for arg in build_qemu_args(cfg)))
    lines.append(")")
    lines += REBOOT_LOOP.replace("@QEMU@", ps_arg(qemu_exe)) \
                        .replace("@QMP_PORT@", str(QMP_PORT)).splitlines()
    return "\n".join(lines) + "\n"


QMP_PORT = 4444

REBOOT_LOOP = r"""$qmpWatch = {
    param($port, $state)
    $deadline = (Get-Date).AddSeconds(60)
    $client = $null
    while (-not $client) {
        try { $client = [System.Net.Sockets.TcpClient]::new('127.0.0.1', $port) }
        catch {
            if ((Get-Date) -gt $deadline) { return }
            Start-Sleep -Milliseconds 250
        }
    }
    try {
        $stream = $client.GetStream()
        $reader = [System.IO.StreamReader]::new($stream)
        $writer = [System.IO.StreamWriter]::new($stream)
        $writer.AutoFlush = $true
        $null = $reader.ReadLine()
        $writer.WriteLine('{"execute":"qmp_capabilities"}')
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line -match '"event"\s*:\s*"SHUTDOWN"' -and
                $line -match '"reason"\s*:\s*"guest-reset"') { $state.Reset = $true }
        }
    } catch { } finally { $client.Dispose() }
}
$quickResets = 0
while ($true) {
    $state = [hashtable]::Synchronized(@{ Reset = $false })
    $watcher = [powershell]::Create().AddScript($qmpWatch).AddArgument(@QMP_PORT@).AddArgument($state)
    $pending = $watcher.BeginInvoke()
    $started = Get-Date
    & @QEMU@ @qemuArgs
    $code = $LASTEXITCODE
    $null = $pending.AsyncWaitHandle.WaitOne(3000)
    $watcher.Dispose()
    if (-not $state.Reset) { exit $code }
    if (((Get-Date) - $started).TotalSeconds -lt 30) { $quickResets++ } else { $quickResets = 0 }
    if ($quickResets -ge 5) {
        Write-Output 'Reims launcher: guest reset 5 times within 30 s of starting; giving up.'
        exit 1
    }
    Write-Output ("Reims launcher: guest restarted at {0:HH:mm:ss}; starting QEMU again." -f (Get-Date))
}
"""


def write_launch(cfg: VMConfig) -> Path:
    """Write <name>.ps1 into the VM's own directory."""
    cfg.vm_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.launch_script
    path.write_text(render_launch(cfg), encoding="utf-8")
    return path
