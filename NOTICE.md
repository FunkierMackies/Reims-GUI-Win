# NOTICE

## Manager source code

The Reims GUI Manager source code in this repository (the `manager\` and
`scripts\` Python/PowerShell code, and the project documentation) is licensed
under the **MIT License**, provided **AS IS** without warranty. See `LICENSE`
for the full text. Copyright (c) 2026 FunkierMackies.

## Third-party components

This project incorporates third-party components. Their licenses are included
in full under `licenses/`:

- `licenses/COPYING.gpl-2.0` — GNU General Public License v2 or later
  (GPL-2.0-or-later).
- `licenses/COPYING.gpl-3.0` — GNU General Public License v3 or later
  (GPL-3.0-or-later).
- `licenses/COPYING.lgpl-3.0` — GNU Lesser General Public License v3 or later
  (LGPL-3.0-or-later).

## Bundled components (not distributed in this repository)

The runnable application bundles the following external tools under
`manager\Backend-Apps\`. They are excluded from git by `.gitignore` and remain
subject to their own upstream licenses:

- **Python 3.13 + PySide6** — Python Software Foundation License / LGPLv3
  (PySide6). PySide6 is licensed under LGPL-3.0 / GPL-3.0; see the copy shipped
  with the runtime.
- **QEMU (Reims and Apple-GFX builds)** — GPL-2.0-or-later. QEMU source and
  license are available from https://www.qemu.org/.
- **OpenCore / OVMF firmware** — BSD / MIT-style licenses (NOKIA, Intel, Apple,
  and contributors as applicable). See the firmware distribution.
- **PowerShell 7** — MIT License (Microsoft).
- **GenSMBIOS / macserial** — GPL-3.0-or-later (as used by the project).
- **Lilu, ReimsGraphics, RgfxGate, ReimsMetalCompat,
  AppleMCEReporterDisabler kexts** — respective upstream licenses, shipped
  inside the firmware image.

## Project assets

- `manager/icons/app.png` and `app.ico` are derived from a user-supplied
  generative image of the Reims GUI Manager monitor and stand; the background
  was removed and the graphic is used as the application/window icon.
- `manager/icons/start.png`, `shutdown.png`, and `settings.png` are toolbar
  icons used by the application.

## Repository scope

Only source, icons, documentation, and license texts are committed. Binary
runtime dependencies, firmware images, virtual disks, VM configurations, logs,
and generated launch scripts are excluded by `.gitignore`. The original build
trees used to assemble `Backend-Apps` were removed from the working tree during
cleanup and are not part of this repository.

## Baseline firmware SMBIOS

The base image `manager\Backend-Apps\Firmware\OpenCore-rgfx-3kext.img` (not in
git) is configured with a fixed identity:

| Key | Value |
|---|---|
| SystemProductName | `MacPro7,1` |
| SystemSerialNumber | `W0000000001` |
| SystemUUID | `00000000-0000-0000-0000-000000000000` |
| MLB | `M000000000001` |
| ROM | `112233000000` (hex, stored as 6 raw bytes) |

Generated VMs copy this image and may override SMBIOS values through the
SMBIOS settings page.

## Project code

The Manager's own source is MIT-licensed (see `LICENSE`). This NOTICE is
provided for attribution and does not replace the full license texts in
`licenses/`, which govern the bundled third-party components.
