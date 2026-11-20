import json, socket, sys

out = sys.argv[1]
s = socket.create_connection(('127.0.0.1', 4444), timeout=5)
f = s.makefile('rwb')

def read():
    line = f.readline()
    return json.loads(line) if line else None

read()
f.write(json.dumps({'execute': 'qmp_capabilities'}).encode() + b'\n')
f.flush()
read()
f.write(json.dumps({'execute': 'screendump', 'arguments': {'filename': out}}).encode() + b'\n')
f.flush()
print(read())
s.close()
