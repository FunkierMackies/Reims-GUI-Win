# Run the Windows OS-layer tests of reims-vgpu-vulkan.
. D:\reims-vgpu\scripts\windows\environment.ps1
Push-Location D:\reims-vgpu
& $env:CARGO_HOME\..\rustup\toolchains\nightly-2026-11-03-x86_64-pc-windows-gnu\bin\cargo.exe test -p reims-vgpu-vulkan --lib os::windows -- --test-threads=4
$code = $LASTEXITCODE
Pop-Location
exit $code
