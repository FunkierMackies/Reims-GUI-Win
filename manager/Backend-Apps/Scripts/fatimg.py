"""Read and replace files inside the FAT16 EFI image without mounting it.

    python fatimg.py ls    <image> <dir>
    python fatimg.py cat   <image> <path> <outfile>
    python fatimg.py put   <image> <path> <infile>

Only in-place replacement is supported: the new file must fit in the clusters
the old one already owns (an EFI config.plist edit does). The image is opened
read-write only for `put`, and the new content is written cluster by cluster,
then the directory entry's size is updated. Nothing else in the image moves.
"""

import struct
import sys

ATTR_DIR = 0x10
ATTR_LFN = 0x0F
END = 0x00
DELETED = 0xE5


class Fat:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            mbr = f.read(512)
        self.part = struct.unpack_from("<I", mbr, 446 + 8)[0] * 512
        with open(path, "rb") as f:
            f.seek(self.part)
            bpb = f.read(64)
        (self.bps, self.spc, self.reserved, self.nfats, self.root_entries,
         self.total16, _, self.fat16) = struct.unpack_from("<HBHBHHBH", bpb, 11)
        self.fat_secs = self.fat16
        self.root_secs = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.fat_off = self.part + self.reserved * self.bps
        self.root_off = self.fat_off + self.nfats * self.fat_secs * self.bps
        self.data_off = self.root_off + self.root_secs * self.bps
        self.cluster_bytes = self.spc * self.bps

    # ---- low level -------------------------------------------------------
    def read_at(self, off, n):
        with open(self.path, "rb") as f:
            f.seek(off)
            return f.read(n)

    def write_at(self, off, data):
        with open(self.path, "r+b") as f:
            f.seek(off)
            f.write(data)

    def cluster_off(self, cluster):
        return self.data_off + (cluster - 2) * self.cluster_bytes

    def chain(self, cluster):
        out = []
        while 2 <= cluster < 0xFFF8 and len(out) < 1 << 20:
            out.append(cluster)
            nxt = struct.unpack("<H", self.read_at(self.fat_off + cluster * 2, 2))[0]
            if nxt == cluster:            # self-loop guard
                break
            cluster = nxt
        return out

    # ---- directory walk --------------------------------------------------
    @staticmethod
    def name83(entry):
        base = entry[0:8].decode("latin-1").rstrip(" ")
        ext = entry[8:11].decode("latin-1").rstrip(" ")
        return base + ("." + ext if ext else "")

    def entries(self, path):
        """Yield (name, attr, cluster, size, entry_offset) for a directory."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            start, size = None, self.root_entries * 32
            off = self.root_off
        else:
            ent = self.lookup(parts)
            if not ent:
                raise SystemExit(f"no such directory: {path}")
            if not (ent[1] & ATTR_DIR):
                raise SystemExit(f"not a directory: {path}")
            chain = self.chain(ent[2])
            start = chain[0]
            size = len(chain) * self.cluster_bytes
            off = self.cluster_off(start)
        data = self.read_at(off, size)
        lfn = {}
        for i in range(0, len(data), 32):
            e = data[i:i + 32]
            if len(e) < 32 or e[0] == END:
                break
            if e[0] == DELETED:
                lfn = {}
                continue
            attr = e[11]
            if attr == ATTR_LFN:
                seq = e[0] & 0x1F
                lfn[seq] = self.lfn_chars(e)
                continue
            if attr & 0x08:
                continue
            name = self.name83(e)
            if lfn:
                name = "".join(lfn[k] for k in sorted(lfn))
                lfn = {}
            if name in (".", ".."):
                continue
            cluster = struct.unpack_from("<H", e, 26)[0]
            if cluster == 0:
                cluster = struct.unpack_from("<H", e, 20)[0]
            yield name, attr, cluster, struct.unpack_from("<I", e, 28)[0], off + i

    @staticmethod
    def lfn_chars(entry):
        raw = entry[1:11] + entry[14:26] + entry[28:32]
        chars = []
        for i in range(0, len(raw), 2):
            c = struct.unpack_from("<H", raw, i)[0]
            if c == 0x0000 or c == 0xFFFF:
                break
            chars.append(chr(c))
        return "".join(chars)

    def lookup(self, parts):
        """Return (name, attr, cluster, size, entry_offset) for parts[-1]."""
        cur = ""
        found = None
        for part in parts:
            want = part.upper()
            found = None
            for name, attr, cluster, size, off in self.entries(cur):
                if name.upper() == want:
                    found = (name, attr, cluster, size, off)
                    break
            if not found:
                return None
            cur = cur + "/" + part if cur else part
        return found

    # ---- commands --------------------------------------------------------
    def cat(self, path, out):
        ent = self.lookup([p for p in path.replace("\\", "/").split("/") if p])
        if not ent:
            raise SystemExit(f"no such file: {path}")
        name, attr, cluster, size, off = ent
        if attr & ATTR_DIR:
            raise SystemExit(f"is a directory: {path}")
        data = b""
        for c in self.chain(cluster):
            data += self.read_at(self.cluster_off(c), self.cluster_bytes)
        with open(out, "wb") as f:
            f.write(data[:size])
        print(f"{path}: {size} bytes -> {out}")

    def put(self, path, src):
        ent = self.lookup([p for p in path.replace("\\", "/").split("/") if p])
        if not ent:
            raise SystemExit(f"no such file: {path}")
        name, attr, cluster, size, entry_off = ent
        new = open(src, "rb").read()
        chain = self.chain(cluster)
        capacity = len(chain) * self.cluster_bytes
        if len(new) > capacity:
            raise SystemExit(
                f"{path}: {len(new)} bytes exceeds the {capacity} bytes it owns")
        for i, c in enumerate(chain):
            chunk = new[i * self.cluster_bytes:(i + 1) * self.cluster_bytes]
            if not chunk:
                break
            if len(chunk) < self.cluster_bytes:
                chunk = chunk + b"\x00" * (self.cluster_bytes - len(chunk))
            self.write_at(self.cluster_off(c), chunk)
        self.write_at(entry_off + 28, struct.pack("<I", len(new)))
        print(f"{path}: {size} -> {len(new)} bytes (capacity {capacity})")


if __name__ == "__main__":
    cmd, img = sys.argv[1], sys.argv[2]
    fat = Fat(img)
    if cmd == "ls":
        for name, attr, cluster, size, off in fat.entries(sys.argv[3] if len(sys.argv) > 3 else ""):
            kind = "d" if attr & ATTR_DIR else "-"
            print(f"{kind} {name:<12} {size:>10} {cluster:>6}")
    elif cmd == "cat":
        fat.cat(sys.argv[3], sys.argv[4])
    elif cmd == "put":
        fat.put(sys.argv[3], sys.argv[4])
    else:
        raise SystemExit("usage: ls|cat|put ...")
