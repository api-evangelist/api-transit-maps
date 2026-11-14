#!/usr/bin/env python3
"""Atlas mode for providers too big for one sheet (Cloudflare: 4,531 operations).

Plans sheets of <= sheet_max_ops operations at line level, keeping products together and putting
products that interchange most on the same sheet; builds every sheet with build_network_map.py;
writes an index page with thumbnails, the products on each sheet and the cross-sheet links.

usage: atlas.py <provider-dir> <out-dir> <families.yml> [--jobs 3]
"""
import sys, os, json, subprocess, collections, argparse, html
import transit_model as T

ap = argparse.ArgumentParser()
ap.add_argument('prov'); ap.add_argument('out'); ap.add_argument('fams')
ap.add_argument('--jobs', type=int, default=3)
args = ap.parse_args()
M = T.load(args.prov, args.fams)
CAP = int((M.fams_raw.get('atlas') or {}).get('sheet_max_ops', 450))
fam_of = {t: f for f, ts in M.fam_lines.items() for t in ts}

# units: a product that fits is one unit; a bigger one is cut into consecutive chunks of its lines
units = []
for f, ts in M.fam_lines.items():
    ts = sorted(ts, key=lambda t: -len(M.lines[t]))
    size = sum(len(M.lines[t]) for t in ts)
    if size <= CAP:
        units.append((f, ts)); continue
    chunk, n = [], 0
    for t in ts:
        if n + len(M.lines[t]) > CAP and chunk:
            units.append((f, chunk)); chunk, n = [], 0
        chunk.append(t); n += len(M.lines[t])
    if chunk:
        units.append((f, chunk))

link = collections.Counter()
for a, b in M.all_links:
    if a['tag'] != b['tag']:
        link[frozenset((a['tag'], b['tag']))] += 1
def links(ls1, ls2):
    return sum(link[frozenset((x, y))] for x in ls1 for y in ls2)

sheets = []
for f, ts in sorted(units, key=lambda u: -sum(len(M.lines[t]) for t in u[1])):
    n = sum(len(M.lines[t]) for t in ts)
    room = [sh for sh in sheets if sh['ops'] + n <= CAP]
    best = max(room, key=lambda sh: (links(sh['lines'], ts), -sh['ops'])) if room else None
    if best is None:
        best = {'lines': [], 'ops': 0, 'families': []}; sheets.append(best)
    best['lines'] += ts; best['ops'] += n
    if f not in best['families']:
        best['families'].append(f)
# biggest sheets first, titled by their main products
sheets.sort(key=lambda sh: -sh['ops'])
sheet_of_line = {}
for k, sh in enumerate(sheets, 1):
    fs = sorted(sh['families'], key=lambda f: -sum(len(M.lines[t]) for t in sh['lines'] if fam_of[t] == f))
    named = [f for f in fs if f != 'More']
    if len(named) < 2:                     # a 'More' sheet is titled by its biggest lines instead
        top = sorted(sh['lines'], key=lambda t: -len(M.lines[t]))
        named += [t.split(' · ')[-1] for t in top if t not in named][:3 - len(named)]
    sh['title'] = ', '.join(named[:3]) + (f' +{len(fs) - 3}' if len(fs) > 3 else '')
    for t in sh['lines']:
        sheet_of_line[t] = k
os.makedirs(args.out, exist_ok=True)
plan = {'sheets': sheets, 'sheet_of_line': sheet_of_line}
plan_fn = os.path.join(args.out, f'{M.slug}-atlas-plan.json')
json.dump(plan, open(plan_fn, 'w'), indent=1)
print(f'{len(sheets)} sheets', [(k + 1, sh['ops'], sh['title']) for k, sh in enumerate(sheets)])

# build sheets
procs = []
for k in range(1, len(sheets) + 1):
    cmd = [sys.executable, 'build_network_map.py', args.prov, args.out, args.fams, '--atlas-plan', plan_fn, '--sheet', str(k)]
    procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    while sum(p.poll() is None for p in procs) >= args.jobs:
        procs[[p.poll() is None for p in procs].index(True)].wait()
for k, p in enumerate(procs, 1):
    out = p.communicate()[0]
    print(f'sheet {k}:', out.strip().splitlines()[-1] if out.strip() else '(no output)')
    svg = os.path.join(args.out, f'{M.slug}-atlas-{k:02d}.svg')
    subprocess.run(['./render.sh', svg, svg.replace('.svg', '-thumb.png'), '900'])

# cross-sheet links
cross = collections.Counter()
for a, b in M.transfers:
    sa, sb = sheet_of_line.get(a['tag']), sheet_of_line.get(b['tag'])
    if sa and sb and sa != sb:
        cross[tuple(sorted((sa, sb)))] += 1

E = html.escape
cards = []
for k, sh in enumerate(sheets, 1):
    nb = sorted(((b if a == k else a), n) for (a, b), n in cross.items() if k in (a, b))
    fam_html = ''.join(f'<li>{E(f)}</li>' for f in sh['families'])
    nb_html = ', '.join(f'<a href="#s{j}">p.{j}</a> ({n})' for j, n in nb) or 'none'
    cards.append(f'''<article id="s{k}"><a href="{M.slug}-atlas-{k:02d}.svg"><img src="{M.slug}-atlas-{k:02d}-thumb.png" alt="Sheet {k}"></a>
<h2><span>p.{k}</span> {E(sh['title'])}</h2><p>{sh['ops']} operations · {len(sh['lines'])} lines</p>
<ul>{fam_html}</ul><p class="nb">Interchanges to {nb_html}</p></article>''')
page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(M.slug.title())} API Atlas</title><style>
:root{{--bg:#fff;--fg:#1A1F2C;--mut:#5A6275;--card:#F6F7F9;--line:#DCE0E7}}
@media (prefers-color-scheme: dark){{:root:not([data-theme=light]){{--bg:#12151c;--fg:#E8EAF0;--mut:#9AA3B5;--card:#1b2029;--line:#2c3340}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 "Helvetica Neue",Helvetica,Arial,sans-serif}}
main{{max-width:1280px;margin:0 auto;padding:32px 16px}} h1{{font-size:34px;margin:0 0 6px}} .sub{{color:var(--mut);margin:0 0 28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}} img{{width:100%;border-radius:8px;background:#fff}}
h2{{font-size:17px;margin:10px 0 2px}} h2 span{{background:var(--fg);color:var(--bg);border-radius:6px;padding:1px 7px;font-size:13px;margin-right:6px}}
p{{margin:2px 0;color:var(--mut);font-size:13px}} ul{{margin:6px 0;padding-left:18px;font-size:13px}} a{{color:inherit}}
</style></head><body><main><h1>The {E(M.slug.title())} API · Atlas</h1>
<p class="sub">{len(M.ops)} operations on {len(sheets)} sheets · products that interchange most share a sheet · on each sheet, "⇄CODE p.N" means change for that line on page N · every stop links to apis.io</p>
<div class="grid">{''.join(cards)}</div></main></body></html>'''
open(os.path.join(args.out, f'{M.slug}-atlas.html'), 'w').write(page)
print('index:', os.path.join(args.out, f'{M.slug}-atlas.html'))
