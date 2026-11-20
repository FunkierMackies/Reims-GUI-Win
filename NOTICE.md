# NOTICE

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

## Project code

Project source code is provided under the terms described in the license files
above, as applicable to each component. This NOTICE is provided for
attribution and does not replace the full license texts in `licenses/`.
