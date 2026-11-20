# Boot the freshly built QEMU with the reims-graphics-pci device.
#
# The macOS disk is opened READ-WRITE by default, so guest changes persist. Pass
# -Snapshot to boot a temporary overlay instead (nothing is written to the base
# image). The OpenCore ESP and OVMF_VARS stay protected either way: the ESP is
# opened with snapshot=on and OVMF_VARS is a copy.
#
# Usage:
#   .\boot-rgfx.ps1 [-OpenCore <path>] [-Disk <path>] [-Seconds <n>] [-Snapshot]
#                   [-Audio <driver>] [-Check]
#
# Firmware is local to this build: <repo>\firmware holds the OpenCore ESP whose
# config injects the reims-graphics kext set, OVMF_CODE, and a clean OVMF_VARS
# template. Nothing is read from reims-manager-main's VM slot any more, so the
# two installs can be updated independently.
#
# The default ESP is the 3-kext system (Graft, ReimsGraphics, RgfxGate):
# firmware\OpenCore-rgfx-3kext.img. The 5-kext images (OpenCore-rgfx.img,
# OpenCore-rgfx.working.img) are the old system and must be named explicitly.
#
# The QEMU binary is an MSYS2/UCRT64 build: its runtime DLLs (libbz2-1.dll,
# libiconv-2.dll, libfdt-1.dll, liblzo2-2.dll, SDL2.dll, GTK, ...) live in the
# toolchain's bin directory, not next to the exe. This script finds that
# directory, puts it on PATH for the child process, and proves the binary loads
# before starting a VM (a fresh PowerShell has no MSYS2 paths, which is why the
# bare exe used to stop at "libbz2-1.dll not found").

param(
    # Default: <repo>\firmware\OpenCore-rgfx-3kext.img
    [string]$OpenCore,
    [string]$Disk = 'D:\macOS disks\current.qcow2',
    [int]$Seconds = 240,
    # Boot a temporary overlay instead of writing the macOS disk. Off by
    # default: the guest's changes persist in $Disk.
    [switch]$Snapshot,
    # Host audio backend for the guest's USB audio device. `dsound` plays to the
    # Windows default output; capture is made on first use, so a host without a
    # capture device still plays (see vendor/qemu/audio/dsoundaudio.c).
    # `none` is silent; `wav,path=D:/x.wav` records the guest to a file
    # (the script appends ,id=snd0).
    [string]$Audio = 'dsound',
    [string]$Memory = '4096M',
    [int]$Cpus = 5,
    # reims: the ported device (and its ROM) is the only GPU.
    # std:   a plain QEMU VGA, no reims device -- the control for bring-up
    #        experiments (does the guest reach XNU's console without it?).
    [ValidateSet('reims', 'std')]
    [string]$Display = 'reims',
    # The library's log filter (REIMS_VGPU_LOG). env_logger syntax, so one
    # target can be raised without the rest, e.g.
    # 'info,reims_vgpu::graphics::present_timing=debug'.
    [string]$LogLevel = 'info',
    # Optional diagnostic timeline (REIMS_VGPU_TRACE): one line per device
    # event, with nanosecond timestamps. The file is truncated at start.
    [string]$TraceFile,
    # Validate paths + the DLL runtime + free ports, then exit without booting.
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
if (-not $OpenCore) { $OpenCore = Join-Path $root 'firmware\OpenCore-rgfx-3kext.img' }

$q = Join-Path $root 'build\qemu-system-x86_64.exe'
$rom = 'D:\reims-vgpu\firmware\prebuilt\ReimsGraphics.rom'
$ovmfCode = Join-Path $root 'firmware\OVMF_CODE.fd'
$ovmfVarsSrc = Join-Path $root 'firmware\OVMF_VARS.fd'
$bios = 'D:\reims-vgpu\vendor\qemu\pc-bios'
$dir = Join-Path $root 'run'
$vars = Join-Path $dir 'vars.fd'
$serial = Join-Path $dir 'serial.log'
$stderr = Join-Path $dir 'stderr.log'
$qmpLog = Join-Path $dir 'qmp.log'

# The runtime directory: the first one that carries the two DLLs every start
# depends on first (bz2 for the image formats, SDL2 for the window). QEMU_RUNTIME
# overrides the search.
function Resolve-QemuRuntime {
    $candidates = @(
        $env:QEMU_RUNTIME
        (Join-Path $root 'build')
        'D:\msys64\ucrt64\bin'
        'C:\msys64\ucrt64\bin'
        'D:\msys64\mingw64\bin'
        'C:\msys64\mingw64\bin'
    ) | Where-Object { $_ }
    foreach ($cand in $candidates) {
        if ((Test-Path (Join-Path $cand 'libbz2-1.dll')) -and
            (Test-Path (Join-Path $cand 'SDL2.dll'))) {
            return (Resolve-Path $cand).Path
        }
    }
    throw ('QEMU runtime not found: no libbz2-1.dll + SDL2.dll in ' +
        ($candidates -join ', ') + '. Install MSYS2 UCRT64 or set QEMU_RUNTIME.')
}

$qemuRuntime = Resolve-QemuRuntime
$env:PATH = "$qemuRuntime;$env:PATH"

foreach ($p in @($q, $rom, $ovmfCode, $ovmfVarsSrc, $bios, $OpenCore, $Disk)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

# Prove the runtime resolves now: an unresolved import fails here, with the
# loader's own message, instead of halfway through a boot.
$ver = (& $q --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "QEMU cannot start with runtime '$qemuRuntime': $ver"
}
$verFirst = ($ver -split "`r?`n")[0]
"qemu: $verFirst"
"runtime: $qemuRuntime"
"firmware: $OpenCore"
"ovmf: $ovmfCode"

# Two VMs on the same QMP/SSH forward fail confusingly; say so up front.
$busy = @()
foreach ($port in @(4444, 2222)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        $busy += $port
    }
}
if ($busy.Count -and $Check) { "warning: port(s) in use: $($busy -join ', ') (another VM?)" }
if ($busy.Count -and -not $Check) { throw "port(s) in use: $($busy -join ', ') -- another VM is running" }

if ($Check) {
    if (-not (Test-Path $Disk)) { throw "missing: $Disk" }
    $diskInfo = Get-Item $Disk
    "disk: $($diskInfo.FullName)  $('{0:N0}' -f $diskInfo.Length) bytes  $($diskInfo.LastWriteTime)"
    "disk mode: $(if ($Snapshot) { 'snapshot=on (temporary overlay, base untouched)' } else { 'read-write (changes persist)' })"
    "audio: $Audio,id=snd0 -> usb-audio (macOS native driver)"
    "check: ok"
    return
}

New-Item -ItemType Directory -Force -Path $dir | Out-Null
# Rotate instead of deleting: the previous boot's log is often the evidence a
# crash report refers to (run\stderr.prev.log).
foreach ($f in @($serial, $stderr, $qmpLog, (Join-Path $dir 'stdout.log'))) {
    if (Test-Path $f) {
        $prev = Join-Path $dir ([IO.Path]::GetFileNameWithoutExtension($f) + '.prev.log')
        Move-Item $f $prev -Force
    }
}
Copy-Item $ovmfVarsSrc $vars -Force

$env:REIMS_VGPU_LOG = $LogLevel
if ($TraceFile) {
    Set-Content -Path $TraceFile -Value '' -NoNewline
    $env:REIMS_VGPU_TRACE = $TraceFile
} else {
    Remove-Item Env:REIMS_VGPU_TRACE -ErrorAction SilentlyContinue
}
$env:REIMS_VGPU_WINDOW = '1'
$env:REIMS_VGPU_WINDOW_IMPL = 'sdl'

# The macOS disk's mode: read-write (the default, changes persist) or a
# temporary overlay (-Snapshot, base untouched).
$diskSnapshot = if ($Snapshot) { ',snapshot=on' } else { '' }

$qemuArgs = @(
    '-name', 'rgfx-port'
    '-machine', 'q35,memory-backend=ram,max-ram-below-4g=1G'
    # Must equal -m exactly, so it follows $Memory (a literal size here made
    # every boot that did not pass -Memory 4096M die with "Machine memory size
    # does not match the size of the memory backend").
    '-object', "memory-backend-ram,id=ram,size=$Memory,share=on"
    '-accel', 'whpx,hyperv=off,intercept-msr-gp=on'
    '-cpu', 'Skylake-Client-v4,vendor=GenuineIntel,model-id=Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz,+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+xsave,+xsaveopt,-arat,-pcid,-invpcid,check'
    '-smp', "$Cpus,sockets=1,cores=$Cpus,threads=1"
    '-m', $Memory
    '-global', 'ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off'
    '-global', 'ICH9-LPC.disable_s3=1'
    '-global', 'ICH9-LPC.disable_s4=1'
    '-device', 'pcie-root-port,bus=pcie.0,addr=0x1,slot=1,x-speed=16,x-width=32'
    '-device', 'isa-applesmc,osk=ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc'
    '-drive', "if=pflash,format=raw,readonly=on,file=$ovmfCode"
    '-drive', "if=pflash,format=raw,file=$vars"
    '-smbios', 'type=2'
    '-device', 'ich9-ahci,id=sata,addr=0x2'
    '-drive', "id=opencore,if=none,format=raw,snapshot=on,file=$OpenCore"
    '-device', 'ide-hd,bus=sata.2,drive=opencore,bootindex=1'
    '-drive', "id=disk,if=none,format=qcow2$diskSnapshot,file=$Disk"
    # Slot 6 is where OpenCore's DeviceProperties injects the apple-gfx donor
    # spoof (class-code/device-id/vendor-id = 0xffffffff, name "#display"):
    # putting the NVMe there hides it from IONVMeFamily. Keep 6 free.
    '-device', 'nvme,id=nvme0,drive=disk,serial=TM8FP4512G0C101,model=TM8FP512G,addr=0x7'
    '-netdev', 'user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:2222-:22'
    '-device', 'virtio-net-pci,netdev=net0,id=net0,mac=52:54:00:00:00:01,addr=0x3'
)

if ($Display -eq 'reims') {
    $qemuArgs += @(
        '-vga', 'none'
        '-device', "reims-graphics-pci,id=gfx,bus=pcie.0,addr=0x4,romfile=$rom,resolution=1280x720"
    )
} else {
    # Slot 1 is the root port; put the control VGA out of its way.
    $qemuArgs += @('-device', 'VGA,bus=pcie.0,addr=0x8')
}

$qemuArgs += @(
    '-audiodev', "$Audio,id=snd0"
    '-device', 'qemu-xhci,id=xhci,addr=0x5,p2=8,p3=6,conditional-intr-mapping=on'
    '-device', 'usb-kbd,id=kbd,bus=xhci.0'
    '-device', 'usb-tablet,id=tablet,bus=xhci.0'
    # macOS has no virtio-snd driver, so the guest's sound is a USB audio class 1
    # device, which AppleUSBAudio drives natively (no kext or DeviceProperties).
    # Its output plays through the host backend named by -Audio.
    '-device', 'usb-audio,id=snda,bus=xhci.0,audiodev=snd0'
    '-display', 'none'
    '-L', $bios
    '-serial', "file:$serial"
    '-qmp', 'tcp:127.0.0.1:4444,server=on,wait=off'
    '-msg', 'timestamp=on'
)

$argLine = ($qemuArgs | ForEach-Object {
    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
}) -join ' '

$proc = Start-Process -FilePath $q -ArgumentList $argLine -PassThru `
    -RedirectStandardError $stderr -RedirectStandardOutput (Join-Path $dir 'stdout.log') `
    -WindowStyle Hidden
"qemu pid=$($proc.Id); serial=$serial; stderr=$stderr"
"running for up to $Seconds s (Ctrl+C stops the wait; the VM keeps running)"
try {
    Wait-Process -Id $proc.Id -Timeout $Seconds -ErrorAction Stop
    "qemu exited: $($proc.ExitCode)"
} catch {
    "still running after $Seconds s"
}
