#!/usr/bin/env python3
"""Render a provider's OpenAPI corpus as a transit map (SVG).

Stops = operations, lines = tags, families = colour groups, interchanges = paths that
reach into another line's resource, express routes = Arazzo workflows.
See DESIGN.md for which transit convention encodes which API fact.

usage: build_transit_map.py <provider-dir> <out-dir> [families.yml]
"""
import sys, os, re, glob, json, html, collections
import yaml

PROV, OUT = sys.argv[1], sys.argv[2]
FAM_FILE = sys.argv[3] if len(sys.argv) > 3 else None
slug = os.path.basename(os.path.normpath(PROV))
METHODS = ('get', 'post', 'put', 'patch', 'delete')

# ---------- load operations ----------
ops = []
for f in sorted(glob.glob(os.path.join(PROV, 'openapi', '*.yml')) + sorted(glob.glob(os.path.join(PROV, 'openapi', '*.yaml')))):
    d = yaml.safe_load(open(f)) or {}
    api = re.sub(r'-openapi\.ya?ml$', '', os.path.basename(f))
    for p, item in (d.get('paths') or {}).items():
        for m, o in (item or {}).items():
            if m not in METHODS or not isinstance(o, dict):
                continue
            oid = o.get('operationId') or f'{m}{p}'
            ops.append(dict(id=oid, api=api, m=m, p=p, tag=(o.get('tags') or ['Untagged'])[0],
                            dep=bool(o.get('deprecated')), summary=(o.get('summary') or '').strip()))
byid = {o['id']: o for o in ops}
byid_ci = {k.lower(): k for k in byid}

# ---------- families ----------
fams = yaml.safe_load(open(FAM_FILE)) if FAM_FILE else {'families': [{'name': 'API', 'color': '#2B6CB0', 'tags': []}]}
tag2fam = {}
for fam in fams['families']:
    for t in fam['tags']:
        tag2fam[t] = fam['name']
default_fam = fams['families'][-1]['name']
fam_by_name = {f['name']: f for f in fams['families']}

lines = collections.OrderedDict()
for o in ops:
    lines.setdefault(o['tag'], []).append(o)

RANK = {'get': 0, 'post': 1, 'put': 2, 'patch': 3, 'delete': 4}
def order_key(o):
    segs = o['p'].lower().split('/')
    norm = ['~' if s.startswith('{') else s for s in segs]   # collection before item
    # list (GET collection) -> create (POST collection) -> item ops
    return (norm, RANK[o['m']])
for t in lines:
    lines[t].sort(key=order_key)

# ---------- resource homes + interchanges ----------
def segs(p):
    return [s for s in p.split('/') if s and not re.match(r'v\d+$', s)]
home = {}          # collection name -> op (prefer GET /coll/{id})
for o in ops:
    s = segs(o['p'])
    if len(s) == 2 and s[1].startswith('{') and o['m'] == 'get':
        home.setdefault(s[0].lower(), o)
for o in ops:
    s = segs(o['p'])
    if s and not s[0].startswith('{'):
        home.setdefault(s[0].lower(), o)
transfers = []     # (from op, to op)
for o in ops:
    s = [x.lower() for x in segs(o['p']) if not x.startswith('{')]
    seen = set()
    for i, coll in enumerate(s):
        h = home.get(coll)
        if h and h['tag'] != o['tag'] and h['id'] not in seen:
            # root collection owned elsewhere, or a nested sub-collection with its own line
            if i == 0 or i == len(s) - 1:
                transfers.append((o, h)); seen.add(h['id'])
inter_ids = {a['id'] for a, b in transfers} | {b['id'] for a, b in transfers}

# ---------- Arazzo express routes ----------
routes = []
for f in sorted(glob.glob(os.path.join(PROV, 'arazzo', '*.yml'))):
    if os.path.basename(f).startswith('_'):
        continue
    try:
        d = yaml.safe_load(open(f)) or {}
    except Exception:
        continue
    for wf in d.get('workflows') or []:
        stops = []
        for st in wf.get('steps') or []:
            raw = str(st.get('operationId') or '').split('.')[-1]
            oid = byid_ci.get(raw.lower())            # specs and Arazzo disagree on case (postX vs PostX)
            if oid and oid not in stops:
                stops.append(oid)
        if len(stops) >= 2:
            routes.append(dict(name=(d.get('info') or {}).get('title') or wf.get('workflowId'), stops=stops))
for i, r in enumerate(routes, 1):
    r['code'] = f'X{i}'
route_at = collections.defaultdict(list)
for r in routes:
    for s in r['stops']:
        route_at[s].append(r['code'])

# ---------- geometry ----------
STEP, MAXROW, ROWGAP, LINEGAP = 46, 18, 150, 40
BADGE_W = 54
def code(tag):
    w = re.findall(r'[A-Z][a-z]*|\d+', tag.replace('-', ' ').title().replace(' ', ''))
    c = ''.join(x[0] for x in w) if len(w) > 1 else tag[:3]
    return c.upper()[:4]
codes, used = {}, set()
for t in lines:
    c = code(t); n = 2
    while c in used:
        c = code(t)[:3] + str(n); n += 1
    used.add(c); codes[t] = c

def singular(w):
    for a, b in (('ies', 'y'), ('sses', 'ss'), ('ses', 's'), ('xes', 'x'), ('s', '')):
        if w.endswith(a) and not w.endswith('ss'):
            return w[:-len(a)] + b
    return w
def human(seg):
    return seg.replace('_', ' ').replace('-', ' ').strip().lower()
ACTION_VERBS = {'search': 'Search'}
def label(o):
    """Station name from the method + path, like a street name: the line already says which resource."""
    s = [x for x in segs(o['p'])]
    if not s:
        return o['id']
    last = s[-1]; is_item = last.startswith('{')
    nouns = [x for x in s if not x.startswith('{')]
    noun = human(nouns[-1]) if nouns else o['id']
    m = o['m']
    plural = noun.endswith('s') and not noun.endswith('ss')
    if not is_item and len(nouns) >= 2 and m in ('post', 'get') and noun in (
            'capture', 'confirm', 'cancel', 'finalize', 'void', 'pay', 'send', 'reverse', 'close', 'approve',
            'decline', 'expire', 'attach', 'detach', 'verify', 'accept', 'resume', 'release', 'apply', 'reject',
            'mark uncollectible', 'submit', 'redact', 'reattempt', 'increment authorization', 'verify microdeposits',
            'finalize quote', 'deliver', 'fail', 'return', 'refresh', 'disconnect', 'subscribe', 'unsubscribe',
            'activate', 'deactivate', 'run', 'process', 'present', 'reprocess', 'shipping', 'migrate', 'preview',
            'add lines', 'remove lines', 'update lines', 'apply customer balance', 'cancel action',
            'process payment intent', 'process setup intent', 'set reader display', 'collect inputs', 'refund payment',
            'expire', 'fund cash balance', 'delete', 'find', 'reactivate', 'invalidate', 'mark', 'cancel actions'):
        subj = singular(human(nouns[-2])) if len(nouns) >= 2 else ''
        return f'{noun.capitalize()} {subj}'.strip()
    if noun == 'search':
        return f'Search {human(nouns[-2]) if len(nouns) >= 2 else ""}'.strip()
    if m == 'get':
        return f'Get {singular(noun)}' if (is_item or not plural) else f'List {noun}'
    if m == 'delete':
        return f'Delete {singular(noun)}'
    if m in ('post', 'put', 'patch'):
        return f'Update {singular(noun)}' if (is_item or not plural) else f'Create {singular(noun)}'
    return f'{m.upper()} {noun}'

# shade each line within its family
def shade(hexc, k, n):
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    t = 0 if n <= 1 else (k / (n - 1) - 0.5) * 0.55   # -0.27..+0.27
    f = (lambda c: int(c + (255 - c) * t)) if t > 0 else (lambda c: int(c * (1 + t)))
    return '#%02x%02x%02x' % (f(r), f(g), f(b))

fam_lines = collections.OrderedDict((f['name'], []) for f in fams['families'])
for t in lines:
    fam_lines.setdefault(tag2fam.get(t, default_fam), []).append(t)
color = {}
for fname, ts in fam_lines.items():
    ts.sort(key=lambda t: -len(lines[t]))
    for k, t in enumerate(ts):
        color[t] = shade(fam_by_name[fname]['color'], k, len(ts))

# lay each line out as a snaking strip, each family as a stacked block
def line_rows(t):
    n = len(lines[t]); return max(1, -(-n // MAXROW))
def line_h(t):
    return line_rows(t) * ROWGAP + LINEGAP
blocks = []
for fname, ts in fam_lines.items():
    if not ts: continue
    w = BADGE_W * 2 + min(MAXROW, max(len(lines[t]) for t in ts)) * STEP + 40
    h = 60 + sum(line_h(t) for t in ts)
    blocks.append(dict(name=fname, tags=ts, w=w, h=h))
# pack blocks into columns, tallest first, shortest column wins
NCOL = 3
cols = [[] for _ in range(NCOL)]
colh = [0] * NCOL
for b in sorted(blocks, key=lambda b: -b['h']):
    i = colh.index(min(colh)); cols[i].append(b); colh[i] += b['h'] + 40
TOP, LEFT, SIDEBAR = 150, 60, 520
colw = [max([b['w'] for b in c] or [0]) for c in cols]
x0 = LEFT
pos = {}       # op id -> (x, y)
paths = {}     # tag -> svg path d
fam_boxes = []
for ci, c in enumerate(cols):
    y = TOP
    for b in c:
        fam_boxes.append((b['name'], x0, y, colw[ci], b['h']))
        ly = y + 60 + 140
        for t in b['tags']:
            st = lines[t]; pts = []
            for i, o in enumerate(st):
                r, k = divmod(i, MAXROW)
                kk = k if r % 2 == 0 else MAXROW - 1 - k   # odd rows run back under the hairpin
                x = x0 + BADGE_W + 20 + kk * STEP
                yy = ly + r * ROWGAP
                pos[o['id']] = (x, yy); pts.append((x, yy, r))
            # path with rounded hairpins between rows
            d = f'M{pts[0][0] - 22},{pts[0][1]} L{pts[0][0]},{pts[0][1]}'
            for (xa, ya, ra), (xb, yb, rb) in zip(pts, pts[1:]):
                if ra == rb:
                    d += f' L{xb},{yb}'
                else:
                    ex = xa + (STEP / 2 if ra % 2 == 0 else -STEP / 2)
                    rad = ROWGAP / 2
                    sweep = 1 if ra % 2 == 0 else 0
                    d += f' L{ex},{ya} A{rad},{rad} 0 0 {sweep} {ex},{yb} L{xb},{yb}'
            last = pts[-1]
            d += f' L{last[0] + (22 if last[2] % 2 == 0 else -22)},{last[1]}'
            paths[t] = (d, pts[0], last)
            ly += line_h(t)
        y += b['h'] + 40
    x0 += colw[ci] + 60
W = x0 + SIDEBAR
H = max(max(colh) + TOP + 40, TOP + 400 + 18 * len(routes) + 22 * len(fam_lines))

# ---------- render ----------
E = html.escape
out = []
A = out.append
A(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Helvetica Neue, Helvetica, Arial, sans-serif">')
A('''<style>
.st text{font-size:10.5px;fill:#1A1F2C}
.st:hover text{font-weight:700;fill:#000}
.st:hover circle{stroke-width:4}
.xfer{fill:none;stroke:#8A93A6;stroke-width:1.2;stroke-dasharray:2 3;opacity:.35}
.xfer.hot{stroke:#1A1F2C;opacity:1;stroke-width:2}
.line.dim,.st.dim{opacity:.12}
.route.hot{opacity:1}
.rt{cursor:pointer}
.rt:hover text{font-weight:700}
</style>''')
A(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')
A(f'<text x="{LEFT}" y="62" font-size="38" font-weight="700" fill="#1A1F2C">The {E(slug.title())} API · Transit Map</text>')
A(f'<text x="{LEFT}" y="94" font-size="15" fill="#5A6275" letter-spacing=".5">{len(ops)} operations as stops · {len(lines)} tags as lines · {len(fam_lines)} families · '
  f'{len(transfers)} interchanges · {len(routes)} express routes (Arazzo) · click any stop for its apis.io page</text>')

for name, x, y, w, h in fam_boxes:
    A(f'<text x="{x}" y="{y + 30}" font-size="20" font-weight="700" fill="{fam_by_name[name]["color"]}" letter-spacing="2">{E(name.upper())}</text>')
    A(f'<line x1="{x}" y1="{y + 40}" x2="{x + w}" y2="{y + 40}" stroke="{fam_by_name[name]["color"]}" stroke-width="1" opacity=".4"/>')

# transfers under everything
A('<g id="xfers">')
for a, b in transfers:
    (xa, ya), (xb, yb) = pos[a['id']], pos[b['id']]
    mx = (xa + xb) / 2
    A(f'<path class="xfer" data-a="{E(a["id"])}" data-b="{E(b["id"])}" d="M{xa},{ya} C{mx},{ya} {mx},{yb} {xb},{yb}"/>')
A('</g>')

# lines
for t, (d, first, last) in paths.items():
    dash = ' stroke-dasharray="10 6"' if t.lower().startswith('test') else ''
    A(f'<path class="line" data-line="{E(t)}" d="{d}" fill="none" stroke="{color[t]}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"{dash}/>')
    for (bx, by, br), side in ((first, -1), (last, 1 if last[2] % 2 == 0 else -1)):
        cx = bx + side * (22 + BADGE_W / 2 - 4)
        A(f'<g class="badge"><rect x="{cx - 24}" y="{by - 11}" width="48" height="22" rx="5" fill="{color[t]}"/>'
          f'<text x="{cx}" y="{by + 5}" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">{E(codes[t])}</text></g>')
    fx, fy, _ = first
    A(f'<text x="{fx - 22}" y="{fy + 30}" font-size="13" font-weight="700" fill="{color[t]}">{E(t)} <tspan font-weight="400" fill="#5A6275">· {len(lines[t])}</tspan></text>')

# stations
for o in ops:
    x, y = pos[o['id']]
    t = o['tag']; c = color[t]
    url = f'https://apis.apis.io/apis/{slug}/{o["api"]}/#op-{o["id"]}'
    tip = f'{o["m"].upper()} {o["p"]}\n{o["summary"]}\n{o["id"]}' + (f'\nExpress: {", ".join(route_at[o["id"]])}' if o['id'] in route_at else '') + ('\nDEPRECATED' if o['dep'] else '')
    A(f'<a href="{E(url)}" target="_blank" class="st" data-op="{E(o["id"])}" data-line="{E(t)}"><title>{E(tip)}</title>')
    if o['id'] in inter_ids:
        A(f'<circle cx="{x}" cy="{y}" r="8" fill="#fff" stroke="#1A1F2C" stroke-width="3"/>')
    elif o['dep']:
        A(f'<circle cx="{x}" cy="{y}" r="6" fill="#fff" stroke="#A0A7B4" stroke-width="2" stroke-dasharray="2 2"/>')
    else:
        fill = c if o['m'] in ('post', 'put', 'patch') else '#fff'
        A(f'<circle cx="{x}" cy="{y}" r="5.5" fill="{fill}" stroke="{c}" stroke-width="2.5"/>')
    if o['m'] == 'delete' and not o['dep']:
        A(f'<line x1="{x - 4}" y1="{y + 4}" x2="{x + 4}" y2="{y - 4}" stroke="{c}" stroke-width="2"/>')
    if o['id'] in route_at:
        A(f'<rect x="{x - 3.5}" y="{y + 10}" width="7" height="7" transform="rotate(45 {x} {y + 13.5})" fill="#1A1F2C"/>')
    lab = label(o)
    col = '#A0A7B4' if o['dep'] else '#1A1F2C'
    A(f'<text x="{x + 4}" y="{y - 11}" transform="rotate(-45 {x + 4} {y - 11})" style="fill:{col}">{E(lab)}</text></a>')

# sidebar: legend + express routes
sx, sy = W - SIDEBAR + 20, TOP
A(f'<text x="{sx}" y="{sy}" font-size="15" font-weight="700" letter-spacing="2" fill="#1A1F2C">KEY</text>')
key = [('<circle r="5.5" fill="#fff" stroke="#1A1F2C" stroke-width="2.5"/>', 'GET — read (hollow)'),
       ('<circle r="5.5" fill="#1A1F2C" stroke="#1A1F2C" stroke-width="2.5"/>', 'POST — write (filled)'),
       ('<g><circle r="5.5" fill="#fff" stroke="#1A1F2C" stroke-width="2.5"/><line x1="-4" y1="4" x2="4" y2="-4" stroke="#1A1F2C" stroke-width="2"/></g>', 'DELETE (crossed)'),
       ('<circle r="8" fill="#fff" stroke="#1A1F2C" stroke-width="3"/>', 'Interchange — reaches another line\'s resource'),
       ('<path d="M-20,0 C-5,0 5,0 20,0" class="xfer" style="opacity:1"/>', 'Passage to that resource\'s home stop'),
       ('<rect x="-3.5" y="-3.5" width="7" height="7" transform="rotate(45)" fill="#1A1F2C"/>', 'Express stop (Arazzo workflow)'),
       ('<circle r="6" fill="#fff" stroke="#A0A7B4" stroke-width="2" stroke-dasharray="2 2"/>', 'Closed — deprecated'),
       ('<line x1="-20" x2="20" stroke="#8A93A6" stroke-width="7" stroke-dasharray="10 6"/>', 'Depot track — test mode only')]
for i, (g, txt) in enumerate(key):
    yy = sy + 30 + i * 26
    A(f'<g transform="translate({sx + 22},{yy})">{g}</g><text x="{sx + 52}" y="{yy + 4}" font-size="12" fill="#1A1F2C">{E(txt)}</text>')
yy = sy + 30 + len(key) * 26 + 30
A(f'<text x="{sx}" y="{yy}" font-size="15" font-weight="700" letter-spacing="2" fill="#1A1F2C">EXPRESS ROUTES</text>')
A(f'<text x="{sx}" y="{yy + 18}" font-size="11" fill="#5A6275">Arazzo workflows · hover to light up the route</text>')
for i, r in enumerate(routes):
    ry = yy + 42 + i * 18
    A(f'<g class="rt" data-stops="{E(" ".join(r["stops"]))}"><rect x="{sx}" y="{ry - 11}" width="30" height="15" rx="3" fill="#1A1F2C"/>'
      f'<text x="{sx + 15}" y="{ry}" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">{r["code"]}</text>'
      f'<text x="{sx + 38}" y="{ry}" font-size="11" fill="#1A1F2C">{E(re.sub(r"^" + re.escape(slug.title()) + r" ", "", r["name"])[:52])} <tspan fill="#5A6275">· {len(r["stops"])} stops</tspan></text></g>')
A(f'<text x="{LEFT}" y="{H - 20}" font-size="11" fill="#5A6275">Source: s3://api-evangelist-profiles/providers/{slug}/openapi + arazzo · generated by api-transit-maps/build_transit_map.py · stops link to apis.io</text>')

A('''<script><![CDATA[
(function(){
 var svg=document.documentElement;
 function all(s){return Array.prototype.slice.call(svg.querySelectorAll(s));}
 function clear(){all('.dim').forEach(function(e){e.classList.remove('dim')});all('.xfer.hot').forEach(function(e){e.classList.remove('hot')});}
 all('.st').forEach(function(st){
  st.addEventListener('mouseenter',function(){var id=st.getAttribute('data-op');all('.xfer').forEach(function(x){if(x.getAttribute('data-a')==id||x.getAttribute('data-b')==id)x.classList.add('hot');});});
  st.addEventListener('mouseleave',clear);
 });
 all('.rt').forEach(function(rt){
  rt.addEventListener('mouseenter',function(){var s=rt.getAttribute('data-stops').split(' ');
   all('.st').forEach(function(e){if(s.indexOf(e.getAttribute('data-op'))<0)e.classList.add('dim');});
   all('.line').forEach(function(e){e.classList.add('dim');});});
  rt.addEventListener('mouseleave',clear);
 });
})();
]]></script>''')
A('</svg>')

os.makedirs(OUT, exist_ok=True)
fn = os.path.join(OUT, f'{slug}-transit-map.svg')
open(fn, 'w').write('\n'.join(out))
json.dump(dict(ops=len(ops), lines=len(lines), transfers=len(transfers), routes=len(routes), size=[W, H]),
          open(os.path.join(OUT, f'{slug}-transit-map.json'), 'w'), indent=1)
print(fn, dict(ops=len(ops), lines=len(lines), transfers=len(transfers), routes=len(routes), size=(W, H)))
