"""Measure presents/s while typing at the lock screen: drives perf-damage.py for
the given seconds, then counts the present_timing lines QEMU appended to its
stderr log in that window (the boot must run with
'reims_vgpu::graphics::present_timing=debug'; the library's lines carry no
timestamp, so the count is over the bytes appended while the driver typed).

  python scripts\\measure-presents.py <qmp-port> <seconds> <stderr-log>

Prints the presents counted, the wall time they were appended over, and
presents/s. perf-damage.py's own strokes/s is printed beside it.
"""
import os
import subprocess
import sys
import time

TARGET = "reims_vgpu::graphics::present_timing"


def main():
    port, seconds, log = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    here = os.path.dirname(os.path.abspath(__file__))
    before = os.path.getsize(log)
    t0 = time.time()
    subprocess.run(
        [sys.executable, os.path.join(here, "perf-damage.py"), port, str(seconds)],
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
