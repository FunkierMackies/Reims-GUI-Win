"""Add and remove files/directories inside the FAT16 EFI image (no mounting).

    python fatsync.py ls    <image> [dir]
    python fatsync.py rm    <image> <path>
    python fatsync.py add   <image> <hostdir> <destdir>

`rm` is recursive. `add` copies every file and subdirectory of <hostdir> into
<destdir> (created if missing), preserving names with VFAT long-file-name
entries so OpenCore's own FAT driver can match the names in config.plist.

Both FAT copies are updated, cluster chains are freed on delete, and directory
entries are reused when possible. The MBR, boot sector and FAT geometry are left
untouched: only data clusters, directory entries and FAT tables change.
"""

import os
import struct
import sys
import time

ATTR_DIR = 0x10
ATTR_LFN = 0x0F
ATTR_ARCHIVE = 0x20
DELETED = 0xE5
END = 0x00

# Fixed timestamp for new entries: 2026-11-14 00:00:00 in DOS format.
DOS_DATE = ((2026 - 1980) << 9) | (11 << 5) | 14
DOS_TIME = 0


def lfn_checksum(short11):
    s = 0
    for b in short11:
        s = (((s & 0x01) << 7) + (s >> 1) + b) & 0xFF
    return s


def lfn_entries(name, short11):
    """Return the LFN directory entries for name, on-disk order (last first)."""
    units = name.encode("utf-16-le")
    chars = [units[i:i + 2] for i in range(0, len(units), 2)]
    count = (len(chars) + 12) // 13
    if len(chars) % 13:
        chars.append(b"\x00\x00")
    while len(chars) % 13:
        chars.append(b"\xff\xff")
    checksum = lfn_checksum(short11)
    entries = []
    for seq in range(count, 0, -1):
        chunk = chars[(seq - 1) * 13:seq * 13]
        e = bytearray(32)
        e[0] = seq | (0x40 if seq == count else 0)
        e[1:11] = b"".join(chunk[0:5])
        e[11] = ATTR_LFN
        e[12] = 0
        e[13] = checksum
        e[14:26] = b"".join(chunk[5:11])
        e[26:28] = b"\x00\x00"
        e[28:32] = b"".join(chunk[11:13])
        entries.append(bytes(e))
    return entries


def make_short(name, used):
    """Pick a unique 8.3 name (11 bytes) for name, avoiding names in used."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    clean = lambda s: "".join(c for c in s.upper() if c.isalnum())
    stem = clean(stem)[:8] or "X"
    ext = clean(ext)[:3]
    base = (stem.ljust(8) + ext.ljust(3)).encode("ascii")
    if base not in used:
        return base
    prefix = stem[:6]
    for n in range(1, 1000):
        cand = ((prefix + "~" + str(n))[:8].ljust(8) + ext.ljust(3)).encode("ascii")
        if cand not in used:
            return cand
    raise RuntimeError("no free 8.3 name for " + name)


class Fat16:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "r+b")
        mbr = self.f.read(512)
        self.part = struct.unpack_from("<I", mbr, 446 + 8)[0] * 512
        self.f.seek(self.part)
        bpb = self.f.read(64)
        (self.bps, self.spc, self.res, self.nfats, self.root_entries,
         self.tot16, self.media, self.fat16) = struct.unpack_from("<HBHBHHBH", bpb, 11)
        self.total = self.tot16 or struct.unpack_from("<I", bpb, 32)[0]
        self.root_secs = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.fat_off = self.part + self.res * self.bps
        self.fat_bytes = self.fat16 * self.bps
        self.root_off = self.fat_off + self.nfats * self.fat_bytes
        self.data_off = self.root_off + self.root_secs * self.bps
        self.cluster_bytes = self.spc * self.bps
        self.nclusters = (self.total - (self.res + self.nfats * self.fat16 +
                                        self.root_secs)) // self.spc
        self.f.seek(self.fat_off)
        raw = self.f.read(self.fat_bytes)
        self.fat = list(struct.unpack("<%dH" % (self.nclusters + 2), raw[:2 * (self.nclusters + 2)]))
        self.next_free = 2

    # ---- raw io ---------------------------------------------------------
    def cluster_off(self, cluster):
        return self.data_off + (cluster - 2) * self.cluster_bytes

    def read_at(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def write_at(self, off, data):
        self.f.seek(off)
        self.f.write(data)

    def write_fat(self, cluster, value):
        for copy in range(self.nfats):
            self.write_at(self.fat_off + copy * self.fat_bytes + cluster * 2,
                          struct.pack("<H", value))
        self.fat[cluster] = value

    def chain(self, cluster):
        out = []
        while 2 <= cluster < 0xFFF8 and len(out) < self.nclusters:
            out.append(cluster)
            cluster = self.fat[cluster]
        return out

    def free_chain(self, cluster):
        for c in self.chain(cluster):
            self.write_fat(c, 0)

    def alloc(self, count):
        out = []
        c = self.next_free
        while len(out) < count:
            if c > self.nclusters + 1:
                raise RuntimeError("disk full")
            if self.fat[c] == 0:
                out.append(c)
                self.next_free = c + 1
            c += 1
        for i, c in enumerate(out):
            self.write_fat(c, out[i + 1] if i + 1 < len(out) else 0xFFFF)
        return out

    # ---- directory access ----------------------------------------------
    def dir_slots(self, entry):
        """Return (bytes, absolute_offset, cluster_chain) of a directory."""
        if entry is None:
            return (self.read_at(self.root_off, self.root_entries * 32),
                    self.root_off, [])
        chain = self.chain(entry["cluster"])
        data = b""
        for c in chain:
            data += self.read_at(self.cluster_off(c), self.cluster_bytes)
        return data, self.cluster_off(chain[0]), chain

    def list_dir(self, entry):
        """Yield dicts: name, attr, cluster, size, slot (absolute), lfn_slots."""
        data, base, _ = self.dir_slots(entry)
        records = []
        lfn = []          # [(absolute_slot, chars)]
        for i in range(0, len(data), 32):
            e = data[i:i + 32]
            if len(e) < 32 or e[0] == END:
                break
            slot = base + i
            if e[0] == DELETED:
                lfn = []
                continue
            attr = e[11]
            if attr == ATTR_LFN:
                seq = e[0] & 0x1F
                raw = e[1:11] + e[14:26] + e[28:32]
                chars = []
                for j in range(0, len(raw), 2):
                    c = struct.unpack_from("<H", raw, j)[0]
                    if c in (0x0000, 0xFFFF):
                        break
                    chars.append(chr(c))
                lfn.append((seq, slot, "".join(chars)))
                continue
            if attr & 0x08:
                continue
            short = e[0:8].decode("latin-1").rstrip(" ") + "." + \
                e[8:11].decode("latin-1").rstrip(" ") \
                if e[8:11].strip(b" ") else e[0:8].decode("latin-1").rstrip(" ")
            name = short
            if lfn:
                name = "".join(t[2] for t in sorted(lfn))
                lfn_slots = [t[1] for t in lfn]
            else:
                lfn_slots = []
            if name in (".", ".."):
                continue
            cluster = struct.unpack_from("<H", e, 26)[0] or struct.unpack_from("<H", e, 20)[0]
            records.append(dict(name=name, short=short, attr=attr, cluster=cluster,
                                size=struct.unpack_from("<I", e, 28)[0],
                                slot=slot, lfn_slots=lfn_slots))
            lfn = []
        return records

    def lookup(self, path):
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        entry = None
        for part in parts:
            found = None
            for rec in self.list_dir(entry):
                if rec["name"].upper() == part.upper() or rec["short"].upper() == part.upper():
                    found = rec
                    break
            if not found:
                return None, parts
            entry = found
        return entry, parts

    # ---- operations -----------------------------------------------------
    def delete(self, path):
        entry, parts = self.lookup(path)
        if entry is None:
            raise SystemExit("no such path: " + path)
        self._delete_entry(entry)
        print("removed " + path)

    def _delete_entry(self, entry):
        data, base, chain = self.dir_slots(entry)
        if entry["attr"] & ATTR_DIR:
            for rec in self.list_dir(entry):
                self._delete_entry(rec)
            self.free_chain(entry["cluster"])
        else:
            self.free_chain(entry["cluster"])
        for slot in entry["lfn_slots"] + [entry["slot"]]:
            self.write_at(slot, b"\xE5")

    def mkdir(self, parent, name):
        used = {r["short"].encode("latin-1") for r in self.list_dir(parent)}
        short11 = make_short(name, used)
        cluster = self.alloc(1)[0]
        self.write_at(self.cluster_off(cluster), b"\x00" * self.cluster_bytes)
        # "." and ".." as the first two entries of the new cluster
        dot = bytearray(32)
        dot[0:11] = b".".ljust(11, b" ")
        dot[11] = ATTR_DIR
        struct.pack_into("<H", dot, 26, cluster)
        self.write_at(self.cluster_off(cluster), bytes(dot))
        dotdot = bytearray(32)
        dotdot[0:11] = b"..".ljust(11, b" ")
        dotdot[11] = ATTR_DIR
        parent_cluster = parent["cluster"] if parent else 0
        struct.pack_into("<H", dotdot, 26, parent_cluster)
        self.write_at(self.cluster_off(cluster) + 32, bytes(dotdot))
        self._add_entry(parent, name, short11, ATTR_DIR, cluster, 0)
        return dict(name=name, short=name.upper(), attr=ATTR_DIR, cluster=cluster,
                    size=0, slot=0, lfn_slots=[])

    def _add_entry(self, parent, name, short11, attr, cluster, size):
        data, base, chain = self.dir_slots(parent)
        lfn = lfn_entries(name, short11) if name.upper() != \
            (short11[0:8].decode("latin-1").rstrip() + "." +
             short11[8:11].decode("latin-1").rstrip()).upper() else []
        need = len(lfn) + 1
        short_entry = bytearray(32)
        short_entry[0:11] = short11
        short_entry[11] = attr
        struct.pack_into("<H", short_entry, 16, DOS_DATE)   # write date
        struct.pack_into("<H", short_entry, 18, DOS_DATE)   # access date
        struct.pack_into("<H", short_entry, 22, DOS_TIME)   # write time
        struct.pack_into("<H", short_entry, 24, DOS_DATE)   # write date
        struct.pack_into("<H", short_entry, 26, cluster)
        struct.pack_into("<I", short_entry, 28, size)
        block = b"".join(lfn) + bytes(short_entry)

        # first-fit run of free slots
        slots = [i for i in range(0, len(data), 32)]
        run_start = None
        run = 0
        for i in slots:
            first = data[i]
            if first in (DELETED, END):
                if run == 0:
                    run_start = i
                run += 1
                if run == need:
                    break
            else:
                run = 0
                run_start = None
        if run != need:
            # extend the directory by one zeroed cluster
            if not chain:
                raise RuntimeError("cannot extend root directory for " + name)
            new = self.alloc(1)[0]
            self.write_at(self.cluster_off(new), b"\x00" * self.cluster_bytes)
            self.write_fat(chain[-1], new)
            run_start = len(slots) * 32
            data = data + b"\x00" * self.cluster_bytes
        self.write_at(base + run_start, block)
        return run_start

    def put(self, parent, name, data):
        used = {r["short"].encode("latin-1") for r in self.list_dir(parent)}
        short11 = make_short(name, used)
        need = max(1, (len(data) + self.cluster_bytes - 1) // self.cluster_bytes)
        chain = self.alloc(need)
        for i, c in enumerate(chain):
            chunk = data[i * self.cluster_bytes:(i + 1) * self.cluster_bytes]
            if len(chunk) < self.cluster_bytes:
                chunk += b"\x00" * (self.cluster_bytes - len(chunk))
            self.write_at(self.cluster_off(c), chunk)
        self._add_entry(parent, name, short11, ATTR_ARCHIVE, chain[0], len(data))
        print(f"  {name}: {len(data)} bytes, {need} clusters")

    def add_tree_into(self, src, parent):
        for name in sorted(os.listdir(src)):
            path = os.path.join(src, name)
            if os.path.isdir(path):
                rec = self.mkdir(parent, name)
                self.add_tree_into(path, rec)
            else:
                self.put(parent, name, open(path, "rb").read())

    def replace_file(self, path, data):
        entry, parts = self.lookup(path)
        parent, _ = self.lookup("/".join(parts[:-1])) if len(parts) > 1 else (None, [])
        if entry is None:
            self.put(parent, parts[-1], data)
            return
        chain = self.chain(entry["cluster"])
        if len(data) <= len(chain) * self.cluster_bytes:
            for i, c in enumerate(chain):
                chunk = data[i * self.cluster_bytes:(i + 1) * self.cluster_bytes]
                if not chunk and i:
                    break
                if len(chunk) < self.cluster_bytes:
                    chunk += b"\x00" * (self.cluster_bytes - len(chunk))
                self.write_at(self.cluster_off(c), chunk)
            self.write_at(entry["slot"] + 28, struct.pack("<I", len(data)))
            print(f"replaced {path}: {entry['size']} -> {len(data)} bytes")
        else:
            self._delete_entry(entry)
            self.put(parent, parts[-1], data)

    def close(self):
        self.f.close()


def main():
    cmd, img = sys.argv[1], sys.argv[2]
    fat = Fat16(img)
    try:
        if cmd == "ls":
            entry, _ = fat.lookup(sys.argv[3]) if len(sys.argv) > 3 else (None, [])
            for r in fat.list_dir(entry):
                kind = "d" if r["attr"] & ATTR_DIR else "-"
                print(f"{kind} {r['name']:<34} {r['size']:>9} {r['cluster']:>6}")
        elif cmd == "rm":
            fat.delete(sys.argv[3])
        elif cmd == "put":
            fat.replace_file(sys.argv[3], open(sys.argv[4], "rb").read())
        elif cmd == "add":
            hostdir, dest = sys.argv[3], sys.argv[4]
            parent, _ = fat.lookup(dest)
            if parent is None:
                raise SystemExit("no such directory: " + dest)
            print(f"adding {hostdir} -> {dest}")
            fat.add_tree_into(hostdir, parent)
        else:
            raise SystemExit("usage: ls|rm|add ...")
    finally:
        fat.close()


if __name__ == "__main__":
    main()
