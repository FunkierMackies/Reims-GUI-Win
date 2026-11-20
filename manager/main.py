"""Reims GUI Manager - entry point.

Run from PowerShell:
    python D:/Reims-GUI-Win/manager/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import ensure_pyside6  # noqa: E402

# Make the bundled PySide6 importable even when launched with system Python.
ensure_pyside6()

from ui_main import main  # noqa: E402

if "--reims-selftest" in sys.argv:
    # Hidden smoke-test mode for the packaged .exe: import everything, write a
    # marker with resolved paths, then exit without opening a window.
    import json
    from paths import (preflight, pwsh_exe, qemu_for_profile, MAIN_QEMU,
                       FIRMWARE)
    exe_dir = (Path(sys.executable).resolve().parent
               if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent)
    marker = exe_dir / "selftest-result.json"
    marker.write_text(json.dumps({
        "preflight": preflight(),
        "shell": str(pwsh_exe()),
        "qemu_reims": str(qemu_for_profile("reims")),
        "qemu_apple": str(qemu_for_profile("apple-gfx")),
        "main_qemu": str(MAIN_QEMU),
        "firmware": str(FIRMWARE),
    }, indent=2), encoding="utf-8")
    raise SystemExit(0)

if __name__ == "__main__":
    main()
