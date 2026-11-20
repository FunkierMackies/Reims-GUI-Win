import json, glob, os

f = r'run\guest\mc2\dr\DiagnosticReports\java-2026-11-15-142701.ips'
raw = open(f, encoding='utf-8', errors='replace').read()
nl = raw.index('\n')
p = json.loads(raw[nl:])
imgs = p.get('usedImages', [])
th = p.get('faultingThread', 0)
print('threads:', len(p['threads']), 'faulting:', th)
for i, t in enumerate(p['threads']):
    nm = t.get('name') or t.get('queue') or ''
    print('--- thread', i, nm, 'frames', len(t.get('frames', [])))
    for fr in t.get('frames', [])[:14]:
        idx = fr.get('imageIndex')
        img = imgs[idx] if idx is not None and idx < len(imgs) else {}
        name = img.get('name') or os.path.basename(img.get('path', '?'))
        sym = fr.get('symbol') or ('%s+%s' % (name, fr.get('imageOffset')))
        print('    ', name, '|', sym, '| off', fr.get('symbolLocation'), 'imgOff', fr.get('imageOffset'))
