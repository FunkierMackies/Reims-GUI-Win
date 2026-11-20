"""Force continuous presentation in the guest without a login: the password field redraws on
every keystroke, so typing a character and deleting it makes WindowServer present at the rate
the guest can manage. Prints how many strokes it got through, and how long it took.

  python scripts\\perf-damage.py <qmp-port> <seconds>
"""
import json
import socket
import sys
import time

DISPLAY_W, DISPLAY_H = 1744, 984


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

    def keycode(self, code, down):
        return self.event([{"type": "key",
                            "data": {"down": down, "key": {"type": "qcode", "data": code}}}])

    def tap(self, code):
        self.keycode(code, True)
        self.keycode(code, False)

    def move(self, x, y):
        self.event([{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / DISPLAY_W)}},
                    {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / DISPLAY_H)}}])


def main():
    port = int(sys.argv[1])
    seconds = float(sys.argv[2])
    q = Qmp(port)
    q.move(872, 500)
    q.event([{"type": "btn", "data": {"down": True, "button": "left"}}])
    q.event([{"type": "btn", "data": {"down": False, "button": "left"}}])
    time.sleep(0.5)
    t0 = time.time()
    strokes = 0
    slowest = 0.0
    while time.time() - t0 < seconds:
        t = time.time()
        q.tap("a")
        q.tap("backspace")
        dt = time.time() - t
        slowest = max(slowest, dt)
        strokes += 2
        q.move(800 + int(120 * ((time.time() - t0) % 2.0)), 480)
    total = time.time() - t0
    print("strokes %d in %.1f s: %.1f strokes/s; slowest QMP round trip %.3f s"
          % (strokes, total, strokes / total, slowest))


if __name__ == "__main__":
    main()
