"""Measure presents/s while the guest is left alone.

  python scripts\\measure-idle.py <seconds> <stderr-log>

Counts the present_timing lines QEMU appends to its stderr log over the given
seconds (the boot must run with
'reims_vgpu::graphics::present_timing=debug'; the library's lines carry no
timestamp, so the count is over the bytes appended while nothing drove the guest).
"""
import os
import sys
import time

TARGET = "reims_vgpu::graphics::present_timing"


def main():
    seconds, log = float(sys.argv[1]), sys.argv[2]
    before = os.path.getsize(log)
    t0 = time.time()
    time.sleep(seconds)
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
