"""Measure presents/s while a window is dragged in a circle.

  python scripts\\measure-presents-drag.py <qmp-port> <seconds> <stderr-log> <x> <y> [radius]

Drives scripts\\perf-drag.py for the given seconds, then counts the
present_timing lines QEMU appended to its stderr log over that window (the boot
must run with 'reims_vgpu::graphics::present_timing=debug'; the library's lines
carry no timestamp, so the count is over the bytes appended while the drag ran).
"""
import os
import subprocess
import sys
import time

TARGET = "reims_vgpu::graphics::present_timing"


def main():
    port, seconds, log, x, y = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    here = os.path.dirname(os.path.abspath(__file__))
    before = os.path.getsize(log)
    t0 = time.time()
    subprocess.run(
        [sys.executable, os.path.join(here, "perf-drag.py"), port, seconds, x, y]
        + sys.argv[6:],
        check=True,
    )
    elapsed = time.time() - t0
    after = os.path.getsize(log)
    with open(log, "rb") as f:
        f.seek(before)
        added = f.read(after - before).decode("utf-8", "replace")
    presents = sum(1 for line in added.splitlines() if TARGET in line)
    print("appended %d bytes; presents %d over %.1f s: %.1f presents/s"
          % (after - before, presents, elapsed, presents / elapsed))


if __name__ == "__main__":
    main()
