import json, socket, sys, time

s = socket.create_connection(('127.0.0.1', 4444), timeout=5)
f = s.makefile('rwb')

def read():
    line = f.readline()
    if not line:
        return None
    return json.loads(line)

read()  # greeting
f.write(json.dumps({'execute': 'qmp_capabilities'}).encode() + b'\n')
f.flush()
read()
f.write(json.dumps({'execute': 'system_powerdown'}).encode() + b'\n')
f.flush()
print(read())
s.close()
