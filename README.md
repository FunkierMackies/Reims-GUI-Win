# Reims GUI Manager - Windows

A PySide6 desktop manager for creating, configuring, and launching multiple
macOS VMs on Windows with GPU Acceleration initializing the ported `reims-graphics-pci` / `apple-gfx-pci` GPU
stacks from Linux KVM -> Windows WHPX. 
It writes per-VM PowerShell launch scripts and maintains a per-VM copy
of the OpenCore firmware image; it does not rebuild QEMU.

## Acknowledgments
This project was developed entirely with the assistance of AI tools.

## Location

- App + packaged exe: `\Reims-GUI-Win\manager\`
- All tools: `\Reims-GUI-Win\manager\Backend-Apps\`
  - `Python\` (Python 3.13 + PySide6 + PyInstaller)
  - `QEMU-Runtime\` (full UCRT64 DLL set + `qemu-img.exe`)
  - `AppleGFX-QEMU\` (build4: `apple-gfx-pci` + `reims_vgpu_c.dll`)
  - `ReimsQEMU\` (main QEMU + `pc-bios\` + `roms\`)
  - `Firmware\` (`OpenCore-rgfx-3kext.img`, OVMF_CODE/VARS)
  - `PowerShell7\` (pwsh.exe and modules for generated launchers)
  - `GenSMBIOS\` (macserial + helpers)
  - `Scripts\` (fatsync.py, fatimg.py)
- `Reims GUI Manager.exe` + `_internal\` at manager root = packaged app.
- Per-VM state (all in one folder): `D:\Reims-GUI-Win\vms\<safe-name>\`
  - config: `<safe-name>.json`
  - launcher: `<safe-name>.ps1`
  - firmware copy: `OpenCore.img`
- New disks: `\Reims-GUI-Win\macOS-disks\*.qcow2`

Both the `.json` and `.ps1` filenames are the VM name you entered (with
unsafe characters replaced by `_`). Legacy `vm.json` / `<name>-launch.ps1`
files are still loaded.

All paths in the JSON are stored as `{ROOT}/relative/path` and the generated
`.ps1` derives its own `$root` from `$PSScriptRoot`. Moving the whole tree to
another drive (e.g. `C:\`) keeps everything working - no "path not found"
errors from a hard-coded `D:`. External disks/ISOs selected by the user keep
their absolute paths.

## Run

Double-click **`\Reims-GUI-Win\manager\Reims GUI Manager.exe`** or use the Shortcut in **`\Reims-GUI-Win\Reims GUI Manager.exe`**. This is the
packaged executable (windowed; no console), using the monitor graphic from
`icons\app.ico` as its Windows application icon; `icons\app.png` is the window
icon. `Run Reims GUI.cmd` falls back to the bundled Python if the exe is absent.

Both resolve all tools from `manager\Backend-Apps\`. The `.gitignore` at the
repository root excludes binaries and VM state, so the git repository contains
source and docs only. A local `main` branch is initialised with an initial
source commit; the GitHub connector token cannot create remote repositories,
so publishing requires an existing empty remote or a token with repo-creation
scope.

## Homescreen

Click a VM in the list to see its **applied configuration summary** on the
right (General section). With no VM selected the right pane shows the
**Reims GUI Manager logo** instead. The settings editor is hidden until
**Configure VM** is pressed.

Top toolbar: **Create New VM**, **Import VM** (select a `.json` config), then
icon buttons **Start VM** (power) → **Shutdown VM** (red power) → **Configure VM**
(gear), each with the icon above its text.

Import copies the source VM folder's `OpenCore.img` and `vars.fd`. When the
config's `{ROOT}` paths (disk, ISO, firmware) are missing from this tree, they
are re-anchored to the source tree instead, so a VM copied from another drive
still boots; the result dialog lists every path that was re-anchored. Absolute
paths chosen by the user are never changed.

- **Start VM** launches hidden PowerShell/QEMU. If QEMU exits with an error,
  a 3-second popup shows the last QEMU log line and the exit code
  (e.g. `qemu-system-x86_64: -cpu: no CPUID is Valid | Exit Code: 1`).
- **Shutdown VM** force-terminates this VM's QEMU with `taskkill /T /F`
  (QMP `system_powerdown` was unreliable in the macOS guest). A
  `Shutdown Requested` popup appears on click and `Shutdown Success` once the
  process is gone; a `Shutdown Failed` popup appears if it is still running
  after 20 seconds. Only the selected VM's process is targeted.
- **Configure VM** opens the settings editor; **Save VM Settings** returns to
  the homescreen with `New VM Settings Saved` when anything changed, or
  `No New VM Settings Found --- Close Only` when nothing changed. **Cancel**
  returns silently.
- **Secondary Drive (qcow2/vhd)** — optional second disk on the SATA bus
  (`sata.0`; OpenCore uses `sata.2`). The picker accepts only `.qcow2` and
  `.vhd` files; `.vhd` is passed to QEMU as `format=vpc`. Clearing the field
  removes the drive.
- **Remove VM** (below the list) permanently deletes the VM directory
  (`.json`, `.ps1`, `OpenCore.img`, logs) **and its qcow2 main disk and its secondary vhd/qcow2 disk** after a
  warning. A disk shared with another VM is kept.
- Firmware profile/SMBIOS changes no longer create `.bak-*` images.

Host detection runs once at startup and is cached, so clicking VMs is instant.
Use "Re-detect host" in the Host section to refresh it explicitly.

**Create New VM** opens a three-layer wizard: name → optional macOS ISO →
disk (reuse an existing `.qcow2` or create a fresh one), then a blue
**Create New VM** / **Cancel** pair. It writes the `.json`, `.ps1`, and a
per-VM firmware copy immediately.

## VM settings editor

Left nav: **Host | VM Settings | Graphics | SMBIOS | Install**. The configurator
is on the right; **Save VM Settings** and **Cancel** sit below it on the right.
Saving commits every section at once - including SMBIOS values and the graphics
profile - applies the kext set to that VM's OpenCore image, writes the `.json`,
and regenerates the `.ps1`.

1. **Host** - detected on every open; explains why (CPU spoof + GPU selection).
2. **VM Settings** - name, disk (pick/create qcow2), secondary disk (optional, only .qcow2 & .vhd format are supported), locked CPU preset, cores
   capped at host physical cores, memory, NVMe identity, audio, accelerator.
3. **Graphics** - recommendation in the selector; resolution 854x480 → 3840x2160
   (Apple-GFX capped at 1920x1080); per-profile QEMU binary/device/ROM/kexts.
4. **SMBIOS** - headless `macserial.exe`; `MacPro7,1` MOST RECOMMENDED;
   `iMacPro1,1` ALTERNATIVE; shows orange **NOT COMPATIBLE WITH macOS Tahoe**.
5. **Install** - attach any installer ISO on a dedicated SATA/AHCI controller.
   Audio is **output-only**: `virtio-sound-pci,...,streams=1` exposes just one
   PCM stream, which QEMU always assigns to playback. The guest has no
   microphone/capture device, avoiding distorted sound with Bluetooth TWS
   headsets (host speakers are unaffected). This is fixed in the launcher;
   regenerate by saving VM settings or launching once.

## GPU rules

- **NVIDIA RTX host** -> Reims default; selector text:
  `Reims-Graphics (FOR SYSTEM WITH NVIDIA RTX)` / `Apple-GFX (FOR SYSTEM WITHOUT NVIDIA RTX)`.
- **Non-RTX** (AMD / Intel / GTX / GT) -> Apple-GFX default (same selector text).
- Default profile still follows the RTX rule; the parenthetical text is fixed.
- Reims-Graphics requires the **Graft**, **ReimsGraphics**, and **RgfxGate**
  kexts. It delivers the best overall performance and acceleration, though it
  has only been tested on NVIDIA RTX systems (specifically RTX Blackwell).
- Apple-GFX requires only the **Lilu** and **ReimsMetalCompat** kexts. It offers
  broad hardware compatibility across a wide range of GPUs, but lacks OpenGL
  support and delivers lower performance compared to Reims-Graphics.
- Note (for Apple-GFX): Once macOS is installed and you reach the desktop, you
  may only see the wallpaper. To fix this, boot into macOS Recovery, open
  Terminal, and run the following commands:

  ```
  cd "/Volumes/Macintosh HD - Data/Library/Preferences"
  defaults write .GlobalPreferences METAL_ERROR_MODE -int 5
  chmod 644 .GlobalPreferences.plist
  reboot
  ```
- `AppleMCEReporterDisabler.kext` is never touched.

Six CPU presets cover Intel/AMD x AVX-512 x core-band, exactly as specified.

## Licensing

The **Manager source code** (this repository's own Python/PowerShell code and
documentation) is MIT-licensed — see `manager/LICENSE`. It is provided **AS IS**,
without warranty of any kind.

The **bundled third-party runtime components** (not committed here) keep their
own licenses: QEMU under GPL-2.0-or-later, PySide6 under LGPL-3.0/GPL-3.0,
PowerShell 7 under MIT, and the firmware/kext components under their respective
upstream licenses. Those components are documented in `NOTICE.md`, with full
texts in `licenses/`.

The MIT license on the Manager does not alter the license obligations of the
bundled components.

## Baseline firmware SMBIOS

`manager\Backend-Apps\Firmware\OpenCore-rgfx-3kext.img` is the base image copied
to new VMs. Its SMBIOS identity is:

| Key | Value |
|---|---|
| SystemProductName | `MacPro7,1` |
| SystemSerialNumber | `W0000000001` |
| SystemUUID | `3075185E-D610-4C2D-BA85-A0D2E102C352` |
| MLB | `F5K248401GUK3F7UE` |
| ROM | `42E0FF3C4283` (hex, 6 raw bytes) |

Use **Configure VM → SMBIOS** to generate a unique identity per VM.

## Recent changes

- Packaged PyInstaller exe with a monitor-and-stand application icon.
- Added `.gitignore`, `NOTICE.md`, and GPL-2.0 / GPL-3.0 / LGPL-3.0 / MIT licenses.
- Unified `.json` and `.ps1` in `vms\<name>\`; removed `generated\`.
- Stopped creating `.bak-*` OpenCore images.
- Replaced QMP shutdown with targeted `taskkill` and two-stage popups.
- Added optional secondary SATA drive (`.qcow2` / `.vhd`).
- Fixed and improved `Import VM` for cross-drive trees.
- Set the base firmware SMBIOS (placeholder) to the fixed values above.

## Notes

- Two QEMU builds: Reims uses `Backend-Apps\ReimsQEMU\qemu-system-x86_64.exe` (newer, 2026-11-17);
  Apple-GFX uses `Backend-Apps\AppleGFX-QEMU\` imported from `D:\qemu-rg-check\build4` (2026-11-07 build).
- The generic fallback CPU preset is used when detection is inconclusive; there
  are no manual overrides by design.
- `manager\Launch VM.cmd` is the one-click legacy launcher (deprecated).
