"""Build EFI/OC/config.plist's Kernel > Add array from the prebuilt set.toml.

`make -C guest prebuilt` writes set.toml as the canonical list: one [[kext]] per
OpenCore entry, in order. This script rewrites only that array, keeping every
other byte of the plist as it was.
"""

import plistlib
import sys
import tomllib

src_plist, set_toml, out_plist = sys.argv[1], sys.argv[2], sys.argv[3]

set_doc = tomllib.load(open(set_toml, "rb"))
entries = set_doc["kext"]

text = open(src_plist, encoding="utf-8").read()
lines = text.splitlines(keepends=True)

ki = next(i for i, l in enumerate(lines) if "<key>Kernel</key>" in l)
ai = next(i for i, l in enumerate(lines[ki:], ki) if "<key>Add</key>" in l)
start = next(i for i in range(ai, len(lines)) if "<array>" in lines[i])
depth = 0
end = None
for i in range(start, len(lines)):
    depth += lines[i].count("<array>") - lines[i].count("</array>")
    if depth == 0:
        end = i
        break

T = "\t"


def entry(k):
    fields = [("Arch", k["Arch"]), ("BundlePath", k["BundlePath"]),
              ("Comment", k["Comment"]), ("Enabled", True),
              ("ExecutablePath", k["ExecutablePath"])]
    body = [f"{T * 3}<dict>"]
    for key, value in fields:
        body.append(f"{T * 4}<key>{key}</key>")
        if value is True:
            body.append(f"{T * 4}<true/>")
        else:
            body.append(f"{T * 4}<string>{value}</string>")
    for key in ("MaxKernel", "MinKernel", "PlistPath"):
        body.append(f"{T * 4}<key>{key}</key>")
        body.append(f"{T * 4}<string>{k.get(key, '')}</string>")
    body.append(f"{T * 3}</dict>")
    return "\n".join(body) + "\n"


new = lines[:start + 1] + [entry(k) for k in entries] + lines[end:]
open(out_plist, "w", encoding="utf-8", newline="\n").write("".join(new))

# prove it parses, and that nothing outside Kernel.Add moved
import copy
a = plistlib.load(open(src_plist, "rb"))
b = plistlib.load(open(out_plist, "rb"))
a2, b2 = copy.deepcopy(a), copy.deepcopy(b)
del a2["Kernel"]["Add"]
del b2["Kernel"]["Add"]
assert a2 == b2, "something outside Kernel.Add changed"
print("entries:", ", ".join(e["BundlePath"] for e in b["Kernel"]["Add"]))
print("outside Kernel.Add identical; bytes:", len(open(src_plist, "rb").read()), "->",
      len(open(out_plist, "rb").read()))
