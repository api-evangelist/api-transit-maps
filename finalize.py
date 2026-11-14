#!/usr/bin/env python3
"""Make built maps canonical, verify them, and print the review table.

For each slug: <slug>-network-map-<tag>.svg/.json -> <slug>-network-map.svg/.json (older canonical
and iteration files move to <slug>/iterations/), preview PNG copied beside it; then every map is
checked (valid XML, one apis.io link per stop) and flagged for review.

usage: finalize.py <tag> <slug>...
"""
import os, re, sys, glob, json, shutil, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
tag, slugs = sys.argv[1], sys.argv[2:]

def xml_ok(path):
    return subprocess.run(['xmllint', '--noout', path], capture_output=True).returncode == 0

rows = []
for s in slugs:
    if os.path.exists(f'{s}/{s}-atlas.html'):
        sheets = sorted(glob.glob(f'{s}/{s}-atlas-[0-9][0-9].svg'))
        bad = [x for x in sheets if not xml_ok(x)]
        worst = max((json.load(open(x.replace('.svg', '.json')))['labels_on_track'] for x in sheets), default=0)
        rows.append((s, 'atlas', f'{len(sheets)} sheets', '', worst, 'INVALID ' + ','.join(bad) if bad else ''))
        continue
    src = f'{s}/{s}-network-map-{tag}.svg'
    if not os.path.exists(src):
        rows.append((s, 'MISSING', '', '', '', f'no {src} — see logs/{s}-{tag}.log'))
        continue
    os.makedirs(f'{s}/iterations', exist_ok=True)
    for old in glob.glob(f'{s}/{s}-network-map-*.svg') + glob.glob(f'{s}/{s}-network-map-*.json'):
        if f'-{tag}.' not in old:
            shutil.move(old, f'{s}/iterations/{os.path.basename(old)}')
    shutil.move(src, f'{s}/{s}-network-map.svg')
    shutil.move(src.replace('.svg', '.json'), f'{s}/{s}-network-map.json')
    prev = f'previews/{s}-{tag}.png.full.png'
    if os.path.exists(prev):
        shutil.copy(prev, f'{s}/{s}-network-map.png')
    svg = open(f'{s}/{s}-network-map.svg').read()
    m = json.load(open(f'{s}/{s}-network-map.json'))
    stops = int(re.search(r'>(\d+) operations as stops', svg).group(1))
    links = len(re.findall(r'href="https://apis.apis.io/apis/', svg))
    flags = []
    if not xml_ok(f'{s}/{s}-network-map.svg'):
        flags.append('INVALID XML')
    if links != stops:
        flags.append(f'links {links} != stops {stops}')
    if m['labels_on_track'] > 25:
        flags.append('labels')
    if max(m['size']) > 12000:
        flags.append('huge sheet — look for islands')
    if stops < 25:
        flags.append('thin catalog')
    rows.append((s, 'map', stops, f"{m['size'][0]}x{m['size'][1]}", m['labels_on_track'], ', '.join(flags)))

print(f"{'provider':16} {'kind':6} {'stops':>8} {'size':>12} {'lbl':>4}  flags")
for r in rows:
    print(f'{r[0]:16} {r[1]:6} {str(r[2]):>8} {r[3]:>12} {str(r[4]):>4}  {r[5]}')
