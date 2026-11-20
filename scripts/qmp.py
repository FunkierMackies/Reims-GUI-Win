"""Minimal QMP client: connect, capabilities, human-monitor-command, quit."""

import json
import socket
import sys

host, port = "127.0.0.1", int(sys.argv[1] if len(sys.argv) > 1 else 4444)
commands = sys.argv[2:] or ["info pci"]

s = socket.create_connection((host, port), timeout=30)
f = s.makefile("rwb", buffering=0)


def recv():
    line = f.readline()
    if not line:
        raise SystemExit("QMP closed")
    return json.loads(line)


def send(obj):
    f.write((json.dumps(obj) + "\n").encode())


recv()  # greeting
send({"execute": "qmp_capabilities"})
recv()
for command in commands:
    send({"execute": "human-monitor-command", "arguments": {"command-line": command}})
    reply = recv()
    print(f"===== {command}")
    print(reply.get("return", reply))
send({"execute": "quit"})
try:
    recv()
except SystemExit:
    pass
