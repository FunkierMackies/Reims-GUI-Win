# Replace the RgfxGate.kext inside the OpenCore ESP image with the rebuilt one.
# Usage: patch-esp-rgfxgate.ps1 -Image <img> -KextDir <dir containing RgfxGate.kext>
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$KextDir
)
$ErrorActionPreference = 'Stop'
$m = 'D:\msys64\mingw64\bin'
$off = 32256  # FAT16 partition at LBA 63 (63 * 512)
$dev = "$Image@@$off"

if (-not (Test-Path "$KextDir\RgfxGate.kext\Contents\MacOS\RgfxGate")) {
    throw "no RgfxGate.kext under $KextDir"
}

& "$m\mdir.exe" -i $dev "::/EFI/OC/Kexts/RgfxGate.kext/Contents/MacOS" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ESP listing failed (exit $LASTEXITCODE)" }

& "$m\mdeltree.exe" -i $dev "::/EFI/OC/Kexts/RgfxGate.kext"
if ($LASTEXITCODE -ne 0) { throw "mdeltree failed (exit $LASTEXITCODE)" }

& "$m\mcopy.exe" -s -o -i $dev "$KextDir\RgfxGate.kext" "::/EFI/OC/Kexts/"
if ($LASTEXITCODE -ne 0) { throw "mcopy failed (exit $LASTEXITCODE)" }

& "$m\mdir.exe" -i $dev "::/EFI/OC/Kexts/RgfxGate.kext/Contents/MacOS"
if ($LASTEXITCODE -ne 0) { throw "verify listing failed (exit $LASTEXITCODE)" }
"patched $Image"
