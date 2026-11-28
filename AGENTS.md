# Reims GUI Manager

A PySide6 desktop manager for creating, configuring, importing, and launching
multiple macOS VMs on Windows with the ported `reims-graphics-pci` and
`apple-gfx-pci` stacks. It writes per-VM PowerShell launch scripts and
maintains a per-VM copy of the OpenCore firmware image; it does not rebuild
QEMU.

## Repository contents

This repository contains the **source, icons, and documentation only**. Binary
dependencies (QEMU builds, firmware images, Python/PySide6 runtime, PowerShell,
macserial) and all VM state are intentionally excluded by `.gitignore` because
they are large, platform-specific, user-specific, and licensed separately. See
`NOTICE.md`.

To assemble a runnable tree, obtain the external tools described in
`README.md`, then build the executable with:

```powershell
cd manager
Backend-Apps\Python\python.exe -m PyInstaller --noconfirm --clean build_exe.spec
```

## Quick start (Windows, after Backend-Apps is provisioned)

Double-click `manager\Reims GUI Manager.exe`.

- **Create New VM** — wizard: name, optional macOS installer ISO, disk
  (reuse an existing `.qcow2` or create a fresh one).
- **Import VM** — choose a `.json` config. VM-local `OpenCore.img` and
  `vars.fd` are copied; `{ROOT}` paths missing locally are re-anchored to the
  source tree (absolute user paths are left alone).
- VM list — click a VM to see its applied configuration summary. With no VM
  selected the right pane shows the Reims GUI Manager logo.
- **Start VM** / **Shutdown VM** / **Configure VM** — top toolbar actions.
  Shutdown uses `taskkill /T /F` on the selected VM's QEMU process with
  `Shutdown Requested` then `Shutdown Success` popups.
- **Configure VM** opens the settings editor (Host / VM Settings / Graphics /
  SMBIOS / Install), including an optional **Secondary Drive (qcow2/vhd)** on
  SATA with a **Detach** button. Saving applies kext/SMBIOS changes, the
  reims-ctl OpenCore quirks, host RAM identity, and a regenerated launcher,
  without creating `.bak` images.
- **Remove VM** permanently deletes the VM folder and its qcow2/vhd disks
  after a warning (shared disks are kept).

## Layout

```
manager/            PySide6 source, icons, build_exe.spec, README.md
  Backend-Apps/     (not in git) bundled tools and runtime
  Reims GUI Manager.exe + _internal/   (not in git) packaged app
scripts/            FAT image helpers (fatsync.py, fatimg.py) and utilities
vms/<name>/         (not in git) .json, .ps1, OpenCore.img, vars.fd, logs
macOS-disks/        (not in git) qcow2/vhd disks
licenses/           full license texts (GPL-2.0, GPL-3.0, LGPL-3.0)
```

## Source notes

- `manager/presets.py` — eight CPU presets (six RTX + two no-AVX2 `IvyBridge`),
  plus the generic fallback; chosen from `host_detect.HostSpec`.
- `manager/launch_gen.py` — single source of truth for QEMU argv. Per-profile
  `LaunchSpec` keeps Reims (dual virtio-sound, `speed/width` root port,
  `p2=7,p3=7`) and Apple-GFX (playback-only sound, `x-speed/x-width`,
  `p2=8,p3=6`) correct. `intercept-msr-gp=on` is dropped on Intel hosts.
- `manager/ram_info.py` + `read-ram.ps1` — host DIMM identity and clock speed;
  injected into OpenCore `CustomMemory` and used for `-smbios type=17`.
- `manager/smbios.py` — `MacPro7,1` only; ROM values normalized to unicast MACs
  and mirrored to the guest NIC.
- `manager/kext_switch.py` — staged kexts replace stale bundles, applies
  reference quirks, CustomMemory and the OpenCanopy/GoldenGate picker.
- `manager/Backend-Apps/Scripts/fatsync.py` — FAT16 writes and directory
  offsets follow cluster chains (fragmented large files are safe).

## Documentation

- `README.md` — full user guide, location, licensing, credits and Recent Changes.
- `Reims-GUI-Win-Spec.md` — design specification.
- `NOTICE.md` — third-party attribution.

## Licensing

The Manager's own source code and documentation are **MIT-licensed** (provided
AS IS, no warranty) — see `manager/LICENSE`. Bundled third-party components keep their
own licenses (GPL-2.0-or-later, GPL-3.0-or-later, LGPL-3.0-or-later as
applicable); see `NOTICE.md` and `licenses/`.
