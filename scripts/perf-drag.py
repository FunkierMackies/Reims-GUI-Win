"""Drag a window in a circle: the damage path the sluggishness was measured on.

  python scripts\\perf-drag.py <qmp-port> <seconds> <x> <y> [radius]

Presses the left button at (x, y) (a window's title bar), then moves the pointer
around a circle of `radius` pixels (default 120) for `seconds`, and releases. Every
move is a QMP round trip, so the moves/s printed is the rate the guest was asked to
redraw at. MOVE_DELAY paces them: the guest's own present rate is the ceiling.
"""
import json
import math
import socket
import sys
import time

DISPLAY_W, DISPLAY_H = 1744, 984
MOVE_DELAY = 0.010


class Qmp:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.f = self.s.makefile("rwb", buffering=0)
        self._read()
        self._send({"execute": "qmp_capabilities"})
        self._read()

    def _read(self):
        line = self.f.readline()
        if not line:
            raise SystemExit("QMP closed")
        return json.loads(line)

    def _send(self, obj):
        self.f.write((json.dumps(obj) + "\n").encode())

    def event(self, events):
        self._send({"execute": "input-send-event", "arguments": {"events": events}})
        reply = self._read()
        if "error" in reply:
            raise SystemExit("input-send-event failed: %s" % reply["error"])
        return reply

    def move(self, x, y):
        self.event([{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / DISPLAY_W)}},
                    {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / DISPLAY_H)}}])

    def button(self, down):
        self.event([{"type": "btn", "data": {"down": down, "button": "left"}}])


def main():
    port = int(sys.argv[1])
    seconds = float(sys.argv[2])
    x, y = float(sys.argv[3]), float(sys.argv[4])
    radius = float(sys.argv[5]) if len(sys.argv) > 5 else 120.0
    q = Qmp(port)
    q.move(x, y)
    time.sleep(0.3)
    q.button(True)
    moves = 0
    slowest = 0.0
    t0 = time.time()
    while time.time() - t0 < seconds:
        t = time.time()
        angle = 2.0 * math.pi * (time.time() - t0) / 2.0
        q.move(x + radius * math.cos(angle), y + radius * math.sin(angle) * 0.35)
        moves += 1
        slowest = max(slowest, time.time() - t)
        if MOVE_DELAY:
            time.sleep(MOVE_DELAY)
    q.button(False)
    total = time.time() - t0
    print("moves %d in %.1f s: %.1f moves/s; slowest QMP round trip %.3f s"
          % (moves, total, moves / total, slowest))


if __name__ == "__main__":
    main()
