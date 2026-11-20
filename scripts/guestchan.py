"""Guest evidence channel: serve files to the guest and receive its uploads.

The guest reaches the host as 10.0.2.2 (QEMU slirp user networking).  This
server exposes GET from a directory and accepts POST /upload/<name> (raw body)
so guest output can be pulled back to the host without SSH.

Usage:  python guestchan.py 8000 D:\\qemu-reims-complete\\guestchan
"""

import http.server
import os
import sys
import time

root = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.getcwd()
os.makedirs(root, exist_ok=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=root, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        sys.stderr.flush()

    def do_POST(self):
        name = self.path.rsplit("/", 1)[-1] or "upload.bin"
        name = "".join(c for c in name if c.isalnum() or c in "-._")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        out = os.path.join(root, "upload-" + time.strftime("%H%M%S") + "-" + name)
        with open(out, "wb") as f:
            f.write(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok\n")
        print(f"upload: {out} ({len(body)} bytes)", flush=True)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
