# Build the library dist (and its pkg-config files) and place it where QEMU's
# configure finds it. Runs in the project's clean Windows toolchain.
. D:\reims-vgpu\scripts\windows\environment.ps1
Push-Location D:\reims-vgpu
cargo xtask dist --platform vulkan --profile release --out D:\qemu-reims-complete\dist
$code = $LASTEXITCODE
Pop-Location
exit $code
