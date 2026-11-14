#!/usr/bin/env python3
"""Print the next N exemplar-band providers that have no transit map yet, best Kin Score first.

The band comes from each provider's latest kin/score-*.yml in the local all/ cache (cached to
work/exemplars.json; --refresh rescans, and a scan older than 7 days is refreshed automatically).
A provider is done when <slug>/<slug>-network-map.svg or <slug>/<slug>-atlas.html exists.
skip.yml lists providers never picked (our own properties).

usage: next_batch.py N [--refresh] [--band exemplar] [--status]
"""
import os, re, sys, json, glob, time, argparse
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
ALL = os.path.expanduser('~/Github/all')
CACHE = os.path.join(ROOT, 'work', 'exemplars.json')

ap = argparse.ArgumentParser()
ap.add_argument('n', type=int, nargs='?', default=50)
ap.add_argument('--refresh', action='store_true')
ap.add_argument('--band', default='exemplar')
ap.add_argument('--status', action='store_true', help='print done / remaining counts only')
a = ap.parse_args()

def scan():
    rows = []
    for d in sorted(os.listdir(ALL)):
        files = sorted(glob.glob(os.path.join(ALL, d, 'kin', 'score-*.yml')))
        if not files:
            continue
        txt = open(files[-1]).read(3000)
        c = re.search(r'^\s{2}composite:\s*([\d.]+)', txt, re.M)
        b = re.search(r'^\s{2}band:\s*(\S+)', txt, re.M)
        if c and b:
            rows.append({'slug': d, 'score': float(c.group(1)), 'band': b.group(1), 'scored': os.path.basename(files[-1])})
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump({'scanned': time.time(), 'rows': rows}, open(CACHE, 'w'))
    return rows

if a.refresh or not os.path.exists(CACHE) or time.time() - json.load(open(CACHE))['scanned'] > 7 * 86400:
    rows = scan()
else:
    rows = json.load(open(CACHE))['rows']

skip = set((yaml.safe_load(open(os.path.join(ROOT, 'skip.yml'))) or {}).get('skip', {}) or {})
def done(s):
    return os.path.exists(os.path.join(ROOT, s, f'{s}-network-map.svg')) or os.path.exists(os.path.join(ROOT, s, f'{s}-atlas.html'))
band = sorted([r for r in rows if r['band'] == a.band and r['slug'] not in skip], key=lambda r: -r['score'])
todo = [r for r in band if not done(r['slug'])]
if a.status:
    print(f"{a.band}: {len(band)} providers, {len(band) - len(todo)} mapped, {len(todo)} to go (cache: {len(rows)} scored)")
    sys.exit(0)
print(' '.join(r['slug'] for r in todo[:a.n]))
