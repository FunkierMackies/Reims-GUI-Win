"""Wrap a raw disk image as a fixed VHD (raw bytes + 512-byte footer) and back.

    python vhd-wrap.py wrap  <raw.img> <out.vhd>
    python vhd-wrap.py unwrap <edited.vhd> <out.img>

A fixed VHD is exactly the raw disk followed by a 512-byte footer, so the image
can be mounted by Windows (Mount-DiskImage), edited with ordinary file APIs, and
then stripped back to the byte-identical raw layout QEMU expects.
"""

import os
import struct
import sys
import time
import uuid


def wrap(raw, vhd):
    data = open(raw, "rb").read()
    size = len(data) & ~0x1FF           # VHD sizes are whole sectors
    footer = bytearray(512)
    footer[0:8] = b"conectix"
    struct.pack_into(">I", footer, 8, 0x00000002)     # features: reserved
    struct.pack_into(">I", footer, 12, 0x00010000)    # version 1.0
    struct.pack_into(">Q", footer, 16, 0xFFFFFFFFFFFFFFFF)  # fixed: no data offset
    epoch = int(time.mktime((2000, 1, 1, 0, 0, 0, 0, 0, -1)) - time.timezone)
    struct.pack_into(">I", footer, 24, int(time.time()) - epoch)
    footer[28:32] = b"win "
    struct.pack_into(">I", footer, 32, 0x00050003)
    footer[36:40] = b"Wi2k"
    struct.pack_into(">Q", footer, 40, size)          # original size
    struct.pack_into(">Q", footer, 48, size)          # current size
    # CHS geometry: 16 heads, 63 sectors, cylinders rounded up to 65535 max.
    cylinders = min((size // (16 * 63 * 512)) + 1, 65535)
    struct.pack_into(">H", footer, 56, cylinders)
    footer[58] = 16
    footer[59] = 63
    struct.pack_into(">I", footer, 60, 2)             # fixed disk
    struct.pack_into(">I", footer, 64, 0)             # checksum placeholder
    footer[68:84] = uuid.uuid4().bytes
    footer[84] = 0                                    # saved state: not saved
    struct.pack_into(">I", footer, 64, (~sum(footer)) & 0xFFFFFFFF)
    with open(vhd, "wb") as f:
        f.write(data[:size])
        f.write(footer)
    print(f"wrapped {raw} ({size} bytes) -> {vhd}")


def unwrap(vhd, raw):
    data = open(vhd, "rb").read()
    if data[0x200:0x208] not in (b"", b"conectix") and data[-512:-504] != b"conectix":
        raise SystemExit("not a fixed VHD I wrote: footer missing")
    if data[-512:-504] != b"conectix":
        raise SystemExit("footer missing")
    size = struct.unpack_from(">Q", data, len(data) - 512 + 48)[0]
    with open(raw, "wb") as f:
        f.write(data[:size])
    print(f"unwrapped {vhd} -> {raw} ({size} bytes)")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "wrap":
        wrap(sys.argv[2], sys.argv[3])
    elif mode == "unwrap":
        unwrap(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("usage: wrap|unwrap <in> <out>")
