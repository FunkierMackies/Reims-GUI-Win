# Reims GUI Manager

A PySide6 desktop manager for creating, configuring, and launching multiple
macOS VMs on Windows with the ported `reims-graphics-pci` and `apple-gfx-pci`
stacks. It writes per-VM PowerShell launch scripts and maintains a per-VM copy
of the OpenCore firmware image; it does not rebuild QEMU.

## Repository contents

This repository contains the **source and documentation only**. Binary
dependencies (QEMU builds, firmware images, Python/PySide6 runtime, PowerShell,
macserial) are intentionally excluded by `.gitignore` because they are large,
platform-specific, and licensed separately. See `NOTICE.md`.

To assemble a runnable tree, obtain the external tools described in
`manager/README.md` and run `manager\Setup-Backend.ps1` on a Windows host with
access to the original build trees, then build the executable with:

```powershell
cd manager
Backend-Apps\Python\python.exe -m PyInstaller --noconfirm --clean build_exe.spec
```

## Quick start (Windows, after Backend-Apps is provisioned)

Double-click `manager\Reims GUI Manager.exe`, or `manager\Run Reims GUI.cmd`.

- **Create New VM** — wizard: name, optional macOS installer ISO, disk
  (reuse an existing `.qcow2` or create a fresh one).
- VM list — click a VM to see its applied configuration summary.
- **Start VM** / **Shutdown VM** / **Configure VM** — top toolbar actions.
- **Configure VM** opens the settings editor (Host / VM Settings / Graphics /
  SMBIOS / Install) with **Save VM Settings** and **Cancel**.

## Layout

```
manager/            PySide6 source, icons, build_exe.spec, README.md
  Backend-Apps/     (not in git) bundled tools and runtime
scripts/            FAT image helpers (fatsync.py, fatimg.py)
macOS-disks/        (not in git) qcow2 disks
vms/<name>/         (not in git) .json, .ps1, OpenCore.img, logs
licenses/           full license texts (GPL-2.0, GPL-3.0, LGPL-3.0)
```

## Documentation

- `manager/README.md` — full user guide and architecture notes.
- `Reims-GUI-Win-Spec.md` — design specification.

## Licensing

See `NOTICE.md` and the `licenses/` directory. This repository includes
components under GPL-2.0-or-later, GPL-3.0-or-later, and LGPL-3.0-or-later.
