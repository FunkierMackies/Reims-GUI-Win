"""Drive a live QEMU guest's input devices over QMP (keyboard / absolute pointer).

Usage:
  python qmp-guest.py 4444 key "meta+spc"
  python qmp-guest.py 4444 type "Terminal"
  python qmp-guest.py 4444 click 870 492
  python qmp-guest.py 4444 script steps.json

`steps.json` is a list of ["key","..."], ["type","..."], ["click",x,y],
["sleep",seconds] and ["mark","text"] entries.

QKeyCode names follow QEMU's qapi/ui.json ('ret', 'spc', 'meta_l', 'a'..'z',
'1'..'0', 'minus', 'equal', 'slash', 'dot', 'comma', 'semicolon',
'apostrophe', 'grave_accent', 'bracket_left', 'bracket_right', 'backslash').
"""

import json
import socket
import sys
import time

# Characters that need shift, mapped to (qcode, shift).
SHIFTED = {
    "~": ("grave_accent", True),
    "!": ("1", True),
    "@": ("2", True),
    "#": ("3", True),
    "$": ("4", True),
    "%": ("5", True),
    "^": ("6", True),
    "&": ("7", True),
    "*": ("8", True),
    "(": ("9", True),
    ")": ("0", True),
    "_": ("minus", True),
    "+": ("equal", True),
    "{": ("bracket_left", True),
    "}": ("bracket_right", True),
    "|": ("backslash", True),
    ":": ("semicolon", True),
    '"': ("apostrophe", True),
    "<": ("comma", True),
    ">": ("dot", True),
    "?": ("slash", True),
    "A": ("a", True),
    "B": ("b", True),
    "C": ("c", True),
    "D": ("d", True),
    "E": ("e", True),
    "F": ("f", True),
    "G": ("g", True),
    "H": ("h", True),
    "I": ("i", True),
    "J": ("j", True),
    "K": ("k", True),
    "L": ("l", True),
    "M": ("m", True),
    "N": ("n", True),
    "O": ("o", True),
    "P": ("p", True),
    "Q": ("q", True),
    "R": ("r", True),
    "S": ("s", True),
    "T": ("t", True),
    "U": ("u", True),
    "V": ("v", True),
    "W": ("w", True),
    "X": ("x", True),
    "Y": ("y", True),
    "Z": ("z", True),
    " ": ("spc", False),
    "-": ("minus", False),
    "=": ("equal", False),
    "/": ("slash", False),
    ".": ("dot", False),
    ",": ("comma", False),
    ";": ("semicolon", False),
    "'": ("apostrophe", False),
    "[": ("bracket_left", False),
    "]": ("bracket_right", False),
    "\\": ("backslash", False),
    "`": ("grave_accent", False),
    "\n": ("ret", False),
    "\t": ("tab", False),
}

PLAIN = {}
for c in "abcdefghijklmnopqrstuvwxyz":
    PLAIN[c] = c
for c in "0123456789":
    PLAIN[c] = c

# Guest framebuffer size in pixels (the QMP absolute axes are 0..0x7fff).
DISPLAY_W = 1744
DISPLAY_H = 984

MODIFIERS = {    "cmd": "meta_l",
    "cmd_r": "meta_r",
    "shift": "shift",
    "shift_r": "shift_r",
    "ctrl": "ctrl",
    "ctrl_r": "ctrl_r",
    "alt": "alt",
    "alt_r": "alt_r",
    "fn": "fn",
}


class Qmp:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.f = self.s.makefile("rwb", buffering=0)
        self._read()  # greeting
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
            raise SystemExit(f"input-send-event failed: {reply['error']}")

    def keycode(self, code, down):
        self.event([{"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": code}}}])

    def tap(self, code):
        self.keycode(code, True)
        time.sleep(0.03)
        self.keycode(code, False)
        time.sleep(0.03)

    def combo(self, spec):
        """spec like 'meta+spc' or 'meta+shift+3'."""
        parts = spec.split("+")
        mods = []
        base = parts[-1]
        for p in parts[:-1]:
            if p not in MODIFIERS:
                raise SystemExit(f"unknown modifier {p!r}")
            mods.append(MODIFIERS[p])
        for m in mods:
            self.keycode(m, True)
            time.sleep(0.02)
        if base in MODIFIERS:
            self.keycode(MODIFIERS[base], True)
            time.sleep(0.02)
            self.keycode(MODIFIERS[base], False)
        else:
            self.tap(base)
        for m in reversed(mods):
            self.keycode(m, False)
            time.sleep(0.02)

    def type_text(self, text):
        for ch in text:
            if ch in PLAIN:
                self.tap(PLAIN[ch])
            elif ch in SHIFTED:
                code, shift = SHIFTED[ch]
                if shift:
                    self.keycode("shift", True)
                    time.sleep(0.02)
                    self.tap(code)
                    self.keycode("shift", False)
                else:
                    self.tap(code)
            else:
                raise SystemExit(f"no keycode for {ch!r}")

    def click(self, x, y, button="left"):
        # QMP absolute axes are 0..0x7fff, not guest pixels: scale the guest's
        # framebuffer coordinates (1744x984 here) onto that range, or clicks
        # land near the top-left (menu bar) instead of on the target.
        self.move(x, y)
        time.sleep(0.1)
        self.event([{"type": "btn", "data": {"down": True, "button": button}}])
        time.sleep(0.05)
        self.event([{"type": "btn", "data": {"down": False, "button": button}}])

    def move(self, x, y):
        """Move the pointer without clicking (reveals hover-only UI)."""
        ax = int(round(x * 32767 / DISPLAY_W))
        ay = int(round(y * 32767 / DISPLAY_H))
        self.event([{"type": "abs", "data": {"axis": "x", "value": ax}},
                    {"type": "abs", "data": {"axis": "y", "value": ay}}])


def main():
    port = int(sys.argv[1])
    command = sys.argv[2]
    q = Qmp(port)
    if command == "key":
        q.combo(sys.argv[3])
    elif command == "type":
        q.type_text(sys.argv[3])
    elif command in ("click", "rclick"):
        q.click(int(sys.argv[3]), int(sys.argv[4]),
                "right" if command == "rclick" else "left")
    elif command == "move":
        q.move(int(sys.argv[3]), int(sys.argv[4]))
    elif command == "release":
        for code in ("meta_l", "meta_r", "ctrl", "ctrl_r", "alt", "alt_r",
                     "shift", "shift_r"):
            q.keycode(code, False)
            time.sleep(0.02)
    elif command == "script":
        steps = json.load(open(sys.argv[3], encoding="utf-8"))
        for step in steps:
            kind = step[0]
            if kind == "mark":
                print(f"== {step[1]}", flush=True)
            elif kind == "key":
                q.combo(step[1])
            elif kind == "type":
                q.type_text(step[1])
            elif kind == "click":
                q.click(int(step[1]), int(step[2]))
            elif kind == "sleep":
                time.sleep(float(step[1]))
            else:
                raise SystemExit(f"unknown step {kind}")
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()
