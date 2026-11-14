#!/usr/bin/env python3
"""Build the transit.apievangelist.com gallery: index.html + thumbs/ from the maps on disk.

Single-sheet maps: <slug>/<slug>-network-map.svg (+ .json metrics, + .png preview for the thumb).
Atlases:           <slug>/<slug>-atlas.html with sheets <slug>-atlas-NN.svg and -thumb.png.
"""
import os, re, glob, json, html, subprocess, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
E = html.escape
os.makedirs('thumbs', exist_ok=True)

def display_name(slug):
    f = os.path.join('work', slug, 'apis.yml')
    if os.path.exists(f):
        m = re.search(r'^name:\s*(.+)$', open(f).read(), re.M)
        if m:
            return m.group(1).strip().strip('"\'')
    return slug.replace('-', ' ').title()

cards = []
for svg in sorted(glob.glob('*/*-network-map.svg')):
    slug = svg.split('/')[0]
    head = open(svg).read(4000)
    m = re.search(r'>(\d+) operations as stops · (\d+) tags as lines · (\d+) interchanges', head)
    ops, lines, xch = (int(x) for x in m.groups()) if m else (0, 0, 0)
    png = f'{slug}/{slug}-network-map.png'
    thumb = f'thumbs/{slug}.png'
    if os.path.exists(png) and (not os.path.exists(thumb) or os.path.getmtime(png) > os.path.getmtime(thumb)):
        subprocess.run(['sips', '-Z', '720', png, '--out', thumb], capture_output=True)
    cards.append(dict(slug=slug, name=display_name(slug), ops=ops, lines=lines, xch=xch,
                      href=svg, thumb=thumb, kind='map', sheets=1))
for idx in sorted(glob.glob('*/*-atlas.html')):
    slug = idx.split('/')[0]
    sheets = sorted(glob.glob(f'{slug}/{slug}-atlas-[0-9][0-9].svg'))
    plan = json.load(open(f'{slug}/{slug}-atlas-plan.json'))
    ops = sum(sh['ops'] for sh in plan['sheets']); lines = sum(len(sh['lines']) for sh in plan['sheets'])
    cards.append(dict(slug=slug, name=display_name(slug), ops=ops, lines=lines, xch=None,
                      href=idx, thumb=f'{slug}/{slug}-atlas-01-thumb.png', kind='atlas', sheets=len(sheets)))
cards.sort(key=lambda c: c['name'].lower())

total_ops = sum(c['ops'] for c in cards)
items = []
for c in cards:
    meta = (f"{c['ops']:,} operations · {c['lines']} lines" +
            (f" · {c['sheets']}-sheet atlas" if c['kind'] == 'atlas' else f" · {c['xch']} interchanges"))
    items.append(f'''<article data-name="{E(c['name'].lower())}" data-ops="{c['ops']}">
<a class="pic" href="{E(c['href'])}"><img src="{E(c['thumb'])}" alt="{E(c['name'])} API transit map" loading="lazy"></a>
<div class="body"><h2><a href="{E(c['href'])}">{E(c['name'])}</a>{'<span class="tag">Atlas</span>' if c['kind'] == 'atlas' else ''}</h2>
<p>{meta}</p><p class="links"><a href="{E(c['href'])}">Open map</a> · <a href="https://apis.io/providers/{E(c['slug'])}/">On APIs.io</a></p></div></article>''')

page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>API Transit Maps</title>
<meta name="description" content="Every operation of {len(cards)} public APIs drawn as a metro map: operations are stops, tags are lines, every stop links to its documentation on APIs.io.">
<style>
:root{{--bg:#FFFFFF;--fg:#1A1F2C;--mut:#5A6275;--card:#F6F7F9;--line:#DCE0E7;--accent:#D6264F}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#12151C;--fg:#E8EAF0;--mut:#9AA3B5;--card:#1B2029;--line:#2C3340;--accent:#F0567A}}}}
:root[data-theme="dark"]{{--bg:#12151C;--fg:#E8EAF0;--mut:#9AA3B5;--card:#1B2029;--line:#2C3340;--accent:#F0567A}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 "Helvetica Neue",Helvetica,Arial,sans-serif}}
header,main,footer{{max-width:1280px;margin:0 auto;padding:0 16px}}
header{{padding-top:48px;padding-bottom:8px}} h1{{font-size:clamp(32px,5vw,52px);margin:0 0 8px;letter-spacing:-.5px}}
.lede{{font-size:18px;color:var(--mut);max-width:820px;margin:0 0 20px}}
.key{{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:14px;color:var(--mut);margin:0 0 24px;padding:0;list-style:none}}
.key b{{color:var(--fg)}}
.tools{{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 22px}}
.tools input,.tools select{{font:inherit;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}}
.tools input{{flex:1;min-width:200px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;padding-bottom:40px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}}
.pic{{display:block;aspect-ratio:4/3;background:#fff;overflow:hidden}} .pic img{{width:100%;height:100%;object-fit:cover;object-position:center;transition:transform .3s}}
article:hover .pic img{{transform:scale(1.04)}}
.body{{padding:12px 14px 14px}} h2{{font-size:18px;margin:0 0 4px;display:flex;align-items:center;gap:8px}}
h2 a{{color:inherit;text-decoration:none}} .tag{{font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;background:var(--accent);color:#fff;border-radius:5px;padding:1px 6px}}
.body p{{margin:2px 0;font-size:14px;color:var(--mut)}} .links a{{color:var(--accent);text-decoration:none}}
footer{{border-top:1px solid var(--line);padding-top:18px;padding-bottom:40px;font-size:14px;color:var(--mut)}} footer a{{color:var(--accent)}}
</style></head><body>
<header><h1>API Transit Maps</h1>
<p class="lede">Every operation of {len(cards)} public APIs, drawn as a metro map. {total_ops:,} operations become stops, tags become lines, and the places where one resource reaches into another become interchanges. Every stop links to its documentation on <a href="https://apis.io">APIs.io</a>.</p>
<ul class="key"><li><b>○</b> GET</li><li><b>●</b> POST</li><li><b>⊘</b> DELETE</li><li><b>⇄CODE</b> change for another line</li><li><b>◆</b> stop on an Arazzo workflow</li><li><b>Atlas</b> APIs too big for one sheet</li></ul>
<div class="tools"><input id="q" type="search" placeholder="Find a provider…" aria-label="Find a provider">
<select id="sort" aria-label="Sort"><option value="name">A–Z</option><option value="ops">Most operations</option></select></div>
</header>
<main><div class="grid" id="grid">
{chr(10).join(items)}
</div></main>
<footer><p>Generated from the API Evangelist catalog by <a href="https://github.com/api-evangelist/api-transit-maps">api-transit-maps</a> — the design notes explain which transit convention encodes which API fact. Maps CC BY-NC-SA 4.0, code Apache-2.0. Updated {datetime.date.today().isoformat()}.</p></footer>
<script>
(function(){{var g=document.getElementById('grid'),q=document.getElementById('q'),s=document.getElementById('sort');
var cards=[].slice.call(g.children);
function apply(){{var t=q.value.trim().toLowerCase();cards.forEach(function(c){{c.style.display=c.dataset.name.indexOf(t)<0?'none':'';}});
var k=s.value;cards.slice().sort(function(a,b){{return k==='ops'?b.dataset.ops-a.dataset.ops:a.dataset.name.localeCompare(b.dataset.name);}}).forEach(function(c){{g.appendChild(c);}});}}
q.addEventListener('input',apply);s.addEventListener('change',apply);}})();
</script></body></html>'''
open('index.html', 'w').write(page)
print(f'index.html: {len(cards)} providers, {total_ops:,} operations')
