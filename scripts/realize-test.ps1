# Realize test: start the freshly built QEMU with the reims-graphics-pci device,
# let QMP list the PCI bus, then quit. Non-destructive: the VM never runs (-S).
#
# Run from PowerShell:  .\realize-test.ps1

$ErrorActionPreference = 'Stop'

$q = 'D:\qemu-reims-complete\build\qemu-system-x86_64.exe'
$rom = 'D:\reims-vgpu\firmware\prebuilt\ReimsGraphics.rom'
$bios = 'D:\reims-vgpu\vendor\qemu\pc-bios'
$logDir = 'D:\qemu-reims-complete\logs'
$stderr = Join-Path $logDir 'm4-realize-01.err.log'

$env:REIMS_VGPU_LOG = 'info'
$env:REIMS_VGPU_WINDOW = '1'
$env:PATH = 'D:\msys64\ucrt64\bin;' + $env:PATH

$qmp = @(
    '{"execute":"qmp_capabilities"}'
    '{"execute":"human-monitor-command","arguments":{"command-line":"info pci"}}'
    '{"execute":"human-monitor-command","arguments":{"command-line":"info qtree"}}'
    '{"execute":"quit"}'
) -join "`n"

$args = @(
    '-name', 'rgfx-realize'
    '-machine', 'q35,max-ram-below-4g=1G'
    '-accel', 'whpx,hyperv=off,intercept-msr-gp=on'
    '-cpu', 'max'
    '-smp', '2'
    '-m', '2048M'
    '-display', 'none'
    '-L', $bios
    '-device', "reims-graphics-pci,id=gfx,bus=pcie.0,addr=0x4,romfile=$rom"
    '-S'
    '-qmp', 'stdio'
    '-monitor', 'none'
    '-serial', 'none'
)

$qmp | & $q @args 2> $stderr | Tee-Object -FilePath (Join-Path $logDir 'm4-realize-01.qmp.log')
"exit=$LASTEXITCODE"
