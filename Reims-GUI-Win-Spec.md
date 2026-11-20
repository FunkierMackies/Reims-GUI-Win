# Reims GUI Manager - design spec

Status: implemented scaffold (PySide6). Base tree and Apple-GFX sources copied.

## 1. What was built

A desktop manager that creates, configures, and launches multiple macOS VMs
on Windows. It writes PowerShell 7 launch scripts and edits a VM-local copy of
the OpenCore FAT image; it does not rebuild QEMU.

Homescreen (VirtualBox-style): VM list on the left; clicking a VM shows its
applied-config summary on the right (General section). The config editor is
hidden until **Configure VM** is pressed. Top toolbar: **Create New VM**, then
icon-over-text buttons in order **Start VM** → **Shutdown VM** → **Configure VM**
(icons in `manager/icons/`). Start runs hidden PowerShell/QEMU and shows a
3-second popup with the last QEMU log line and exit code on crash. Shutdown uses
`taskkill /T /F` on the selected VM's QEMU process (QMP system_powerdown was
unreliable), with "Shutdown Requested" and "Shutdown Success" popups. Save VM Settings returns to the homescreen with either
`New VM Settings Saved` (changes) or `No New VM Settings Found --- Close Only`
(no changes); Cancel returns silently. **Remove VM** (below the list) deletes
the `.json` and `.ps1`, keeping disk/firmware.

Host detection is performed once at startup and cached (was ~2.5 s per call,
causing multi-second freezes on repeated VM clicks); an explicit "Re-detect
host" button is the only other trigger.

**Create New VM** wizard: three layers - name, optional macOS ISO, disk
(reuse a `.qcow2` or create fresh) - with a blue Create button and Cancel.

Each VM gets `<name>.json`, `<name>.ps1`, and `OpenCore.img` together in
`vms/<name>/`. The `.ps1` derives `$root` from `$PSScriptRoot` (two levels up),
so the install can move between drives without path errors.

Paths:

- App code + packaged exe: `D:\Reims-GUI-Win\manager\` (`Reims GUI Manager.exe`
  plus `_internal\`, built by `build_exe.spec` with PyInstaller onedir).
- All external tools live under `manager\Backend-Apps\` so the
  `D:\Reims-GUI-Win` tree is portable: Python/PySide6, QEMU-Runtime,
  AppleGFX-QEMU (build4), ReimsQEMU (main QEMU + pc-bios + ROMs), Firmware
  (OpenCore + OVMF), PowerShell7, GenSMBIOS, Scripts (fatsync/fatimg).
- `paths.py` computes the manager/root from `sys.executable` when frozen, so
  the .exe resolves the same bundled paths as running from source.
- Per-VM state including launcher: `D:\Reims-GUI-Win\vms\<name>\`

## Run

- Double-click `D:\Reims-GUI-Win\manager\Run Reims GUI.cmd` (uses the bundled
  Python in `Backend-Apps`; no system Python or global PySide6 needed).
- `manager\Setup-Backend.ps1` rebuilds `Backend-Apps` if it is missing.
- `manager\paths.py` centralises all backend paths and QEMU runtime PATH.

`Backend-Apps` contains: `Python\` (Python 3.13 + PySide6 6.12.0),
`GenSMBIOS\macserial.exe`, and `QEMU-Runtime\` (24 UCRT DLLs).

## 2. Host detection (runs on every manager open, no manual overrides)

| Item | Source | Notes |
|---|---|---|
| CPU vendor | `Win32_Processor.Manufacturer` | Intel / AMD / Unknown |
| Physical cores | sum of `NumberOfCores` across sockets | the VM core slider max |
| Logical cores | sum of `NumberOfLogicalProcessors` | shown for context only |
| AVX / AVX2 / AVX-512 | PowerShell 7 `System.Runtime.Intrinsics.X86` | manual checkboxes if unavailable |
| GPU | `Win32_VideoController.Name` | RTX matched by `RTX \d{3,5}` |

Host values drive the preset directly. The CPU preset field in the VM tab is
locked (read-only, greyed, disabled) - it cannot be edited. Cores are capped at
detected physical cores.

## 3. CPU presets (verbatim, six cases)

Intel + RTX:

- AVX/AVX2, no AVX-512, 2-8 cores -> `Skylake-Client-v4 ... i7-10700 ... ,-arat,enforce`
- AVX/AVX2, no AVX-512, >8 cores -> `Skylake-Client-v4 ... Xeon W-2191B ... ,-arat,enforce`
- AVX/AVX2/AVX-512 -> `Cascadelake-Server-v4 ... Xeon W-3275M ... ,-arat,enforce`

AMD + RTX: same three models, but the tail is `,-arat,-pcid,-invpcid,check`.

Full strings live in `manager/presets.py`. A generic fallback is used when
detection is inconclusive and is flagged in the UI.

## 4. Graphics selector

Interchangeable profiles; neither is marked deprecated.

| | Reims-Graphics | Apple-GFX |
|---|---|---|
| QEMU device | `reims-graphics-pci` | `apple-gfx-pci` |
| ROM | `ReimsGraphics.rom` | `ReimsVgpu.rom` |
| Kexts enabled | Graft, ReimsGraphics, RgfxGate | Lilu, ReimsMetalCompat |
| Kexts disabled | Lilu, ReimsMetalCompat | Graft, ReimsGraphics, RgfxGate |

`AppleMCEReporterDisabler.kext` is never enabled or disabled. Apple-GFX requires
only Lilu + ReimsMetalCompat (verified: macOS boots with GPU acceleration).

Recommendation labels:

- NVIDIA RTX host: Reims *Most Recommended*, Apple-GFX *Not Recommended*.
- Non-RTX (AMD / Intel / GTX / GT): Apple-GFX *Recommended*,
  Reims *Not Recommended, DWYOR*.
- The labels appear inside the Graphics selector items:
  `Reims-Graphics (FOR SYSTEM WITH NVIDIA RTX)` and
  `Apple-GFX (FOR SYSTEM WITHOUT NVIDIA RTX)`.

## 6b. Per-profile QEMU binaries

`apple-gfx-pci` is not compiled into the main `build\qemu-system-x86_64.exe`
(2026-11-17), which only has `reims-graphics-pci`. The newest build containing
`apple-gfx-pci` is `D:\qemu-rg-check\build4\qemu-system-x86_64.exe` (2026-11-07);
the copy in `reims-manager-main` is older (2026-10-19). Build4 has **both**
device names. Build4's exe + `reims_vgpu_c.dll` were copied to
`Backend-Apps\AppleGFX-QEMU\` and are hash-verified against the source.

Resolution selector: 854x480, 1024x600, 1280x720, 1600x900, 1920x1080,
2560x1440, 3840x2160. Apple-GFX is capped at 1920x1080 (higher entries disabled
and selection clamped on switch). Reims supports all seven.

## 6c. Launch scripts and VM actions

The `<name>.ps1` is generated when a VM is created, saved, or launched. It
resolves every install path from `$root = Split-Path -Parent $PSScriptRoot`, so
the tree can move between drives. JSON stores paths as `{ROOT}/relative`.
Icon-toolbar actions: Start VM (hidden PowerShell, crash popup), Shutdown VM
(taskkill the selected VM's QEMU, two-stage popups), Configure VM (opens the editor). Remove VM is below the list.

## 5. Kext / firmware switching

`manager/kext_switch.py` reuses the existing `scripts/fatimg.py` /
`scripts/fatsync.py` helpers. It edits `EFI/OC/config.plist` inside the VM-local
`OpenCore.img`: toggles `Kernel.Add[].Enabled`, adds missing entries when the
bundle is present in the image, stages bundles from `manager/kexts/`, and writes
with `replace_file` so the plist may grow. Base firmware is never touched;
per-profile and SMBIOS writes keep `.bak` copies.

## 6. SMBIOS

Headless `macserial.exe -g -m <model> -n 1` from the bundled GenSMBIOS. Generates
serial, MLB, UUID, and a 6-byte colon-separated ROM, off the UI thread.

- `MacPro7,1` - **MOST RECOMMENDED**
- `iMacPro1,1` - ALTERNATIVE; selecting it shows an orange note below the
  model selector: **NOT COMPATIBLE WITH macOS Tahoe**

## 7. Install ISO, disks and launch

- New qcow2 disks are created from the VM tab (`Create qcow2 in macOS-disks`)
  via the bundled `qemu-img.exe`, into `D:\Reims-GUI-Win\macOS-disks\`.
  Default: `macOS.qcow2`, 120 GB, dynamically expanding. Verified: 20 GiB
  virtual / 192 KiB physical.
- Any macOS installer ISO (CreateInstallMedia or pkg-converted) can be picked;
  it is attached to a dedicated SATA/AHCI controller
  (`ich9-ahci,id=isosata,addr=0x9`, `ide-cd,bus=isosata.0,bootindex=2`). No
  legacy IDE port is used (QEMU models a SATA CD-ROM as `ide-cd` on AHCI).
- Verified: generated script parses cleanly, QEMU accepts the full argument
  array, and a real boot stayed running. `build_qemu_args()` is the single
  source of truth for argv, so tests and the emitted script cannot diverge.
  `manager\Launch VM.cmd` bypasses execution policy for one-click launch.
- Critical fix: `-vga none` is required. Without it QEMU auto-adds a VGA at
  slot 1, which collides with the `pcie-root-port` and aborts the boot.
- Launch command keeps the established flags: `whpx,hyperv=off,intercept-msr-gp=on`,
  the selected `-cpu` preset, `-smp <n>,cores=<n>`, NVMe with model
  `TM8FP6512G` / serial `TM8FP4512G0C101`, `virtio-sound-pci` with `-audiodev
  dsound` and `streams=1` (playback only, no guest microphone), and the selected
  GPU device/ROM.

## 8. Known gaps and risks

- The generic fallback CPU preset is used when detection is inconclusive; there
  are no manual overrides by design.
- Optional `secondary_drive` attaches a `.qcow2` or `.vhd` image on SATA port
  `sata.0` (VHD uses QEMU's `vpc` format driver); stored in the VM JSON with
  the usual `{ROOT}` token.
- Audio capture is disabled in the guest (`streams=1`); this is intentional to
  avoid Bluetooth TWS distortion. Output still works.

## 9. Permission boundary

Only `C:\` and `D:\` were read or written. Drives `E:\` and `F:\` were not
touched. No Windows system files in `C:\Windows` were modified.
