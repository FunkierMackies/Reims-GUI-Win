import json, glob, os, sys

for f in sorted(glob.glob(r'run\guest\mc2\dr\DiagnosticReports\java-*.ips')):
    raw = open(f, encoding='utf-8', errors='replace').read()
    nl = raw.index('\n')
    p = json.loads(raw[nl:])
    th = p.get('faultingThread', 0)
    try:
        frames = p['threads'][th]['frames'][:8]
    except Exception:
        frames = []
    imgs = p.get('usedImages', [])

    def nm(fr):
        return fr.get('symbol') or ('img%s+%s' % (fr.get('imageIndex'), fr.get('imageOffset')))

    print(os.path.basename(f), p.get('exception', {}).get('signal'), 'thread', th)
    print('   ', ' <- '.join(nm(fr) for fr in frames))
    # any width mention in the whole raw text
    for key in ('width (', 'MTLTextureDescriptor has'):
        i = raw.find(key)
        if i >= 0:
            print('    TXT:', raw[i:i+120].replace('\n', ' '))
