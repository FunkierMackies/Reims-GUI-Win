"""Run a shell script in the guest through guestchan/rc and print its output.

Usage:
  python guest_run.py <path-to-script> [timeout-seconds]

The guest's `rc` poller (running in Terminal) GETs /cmd every 2 s, runs the
script, and POSTs its combined output as upload-<HHMMSS>-out.txt. This helper
writes /cmd atomically with a fresh nonce, waits for a new upload, prints it,
and exits with the guest's exit status when one is visible.
"""

import os
import re
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAN = os.path.join(ROOT, "guestchan")


def uploads():
    return {n for n in os.listdir(CHAN) if n.startswith("upload-") and n.endswith("-out.txt")}


def main():
    script_path = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0
    with open(script_path, "r", encoding="utf-8", newline="\n") as f:
        script = f.read()
    nonce = "n" + uuid.uuid4().hex[:12]
    body = nonce + "\n" + script
    before = uploads()
    tmp = os.path.join(CHAN, "cmd.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, os.path.join(CHAN, "cmd"))

    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        new = uploads() - before
        if new:
            seen = max((os.path.join(CHAN, n) for n in new), key=os.path.getmtime)
            # Give the POST a moment to finish writing the file.
            size = -1
            while time.time() < deadline:
                cur = os.path.getsize(seen)
                if cur == size:
                    break
                size = cur
                time.sleep(0.3)
            break
        time.sleep(1.0)
    if not seen:
        print("guest_run: no upload within %.0f s" % timeout, file=sys.stderr)
        return 2
    with open(seen, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    sys.stdout.write(text)
    m = re.search(r"^exit=(\d+)$", text, re.M)
    return int(m.group(1)) if m else 0


if __name__ == "__main__":
    sys.exit(main())
