#!/usr/bin/env python3
"""How to do APIs — the API Evangelist method drawn as one metro map.

Source: the five API Evangelist catalogs (local checkouts under ~/Github/api-evangelist):
  lifecycle   30 stages in order, six phases   -> LINES (one per stage) in six DISTRICTS (phases)
  policies    283 policies                     -> STOPS on the line of their first lifecycle stage;
                                                  a policy in more stages changes to those lines
  strategies  75 strategies                    -> EXPRESS ROUTES through their policies, in lifecycle order
  experiences 46 experiences                   -> listed on every stop (the experience a policy delivers)
  rules       617 rules                        -> listed on every stop (how a policy is checked)

Every stop links to its policy page and carries the links to its lifecycle stage, strategies,
experiences and rules, which view.html shows in a panel when the stop is clicked.

It writes a stand-in provider (work/how-to-do-apis/: an OpenAPI whose operations are the stops and
an Arazzo file whose workflows are the strategies) so the same layout engine draws it.

usage: howto.py            -> how-to-do-apis/how-to-do-apis-network-map.svg
"""
import os, re, sys, json, subprocess, collections
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
AE = os.path.expanduser('~/Github/api-evangelist')
SLUG = 'how-to-do-apis'
WORK = os.path.join(ROOT, 'work', SLUG)

def data(repo, name):
    return yaml.safe_load(open(os.path.join(AE, repo, '_data', name))) or []

stages = sorted(data('lifecycle', 'lifecycle.yml'), key=lambda s: s.get('order', 0))
policies = data('policies', 'policies.yml')
strategies = data('strategies', 'strategies.yml')
experiences = data('experiences', 'experiences.yml')
rules = data('rules', 'rules.yml')

URL = {k: f'https://{k}.apievangelist.com/store/' for k in ('lifecycle', 'policies', 'strategies', 'experiences', 'rules')}
stage_by = {s['slug']: s for s in stages}
order = {s['slug']: k for k, s in enumerate(stages)}
exp_by_name = {e['name'].lower(): e for e in experiences}
exp_by_slug = {e['slug']: e for e in experiences}
rule_by = {r['slug']: r for r in rules}
pol_by = {p['slug']: p for p in policies}
strats_of = collections.defaultdict(list)          # policy -> strategies that route through it
for st in strategies:
    for ps in st.get('policies') or []:
        strats_of[ps].append(st)

def exp_ref(x):
    e = exp_by_name.get(str(x).lower()) or exp_by_slug.get(str(x).lower().replace(' ', '-'))
    return {'name': e['name'], 'url': URL['experiences'] + e['slug'] + '/'} if e else None

def stages_of(p):
    return [s for s in (p.get('lifecycle') or []) if s in stage_by]

# ---------- stand-in OpenAPI: stations, then policies ----------
paths = {}
dropped = []
# a stage with more than CAP policies (Design 87, Governance 51) rides several lines, one per policy
# scope (API / Technical / Business Contract), cut into runs of <= CAP; the station sits on the first
CAP = 30
line_of, stage_lines = {}, collections.defaultdict(list)
for s in stages:
    mine = sorted([p for p in policies if stages_of(p)[:1] == [s['slug']]], key=lambda p: p['name'].lower())
    if len(mine) <= CAP:
        for p in mine:
            line_of[p['slug']] = s['name']
        stage_lines[s['slug']] = [s['name']]
        continue
    by = collections.defaultdict(list)
    for p in mine:
        sc = (p.get('scope') or 'General').replace('Standard', 'Standards').replace('Standardss', 'Standards')
        by[sc].append(p)
    big = max(by, key=lambda k: len(by[k]))
    for k in [k for k in by if len(by[k]) < 6 and k != big]:
        by[big if len(by) > 1 else k] += by.pop(k) if k != big else []
    for sc, ps in sorted(by.items(), key=lambda kv: -len(kv[1])):
        n = -(-len(ps) // CAP)
        for c in range(n):
            nm = f'{s["name"]} · {sc}' + (f' {c + 1}' if n > 1 else '')
            for p in ps[c * len(ps) // n:(c + 1) * len(ps) // n]:
                line_of[p['slug']] = nm
            stage_lines[s['slug']].append(nm)
def seg(slug):                                     # numbered so the Lifecycle line runs in order
    return f'{order[slug] + 1:02d}-{slug}'
for s in stages:
    paths[f'/{seg(s["slug"])}'] = {'get': {
        'operationId': f'stage-{s["slug"]}', 'tags': ['Lifecycle'], 'summary': s['name'],
        'x-station': f'{s["name"]} ★', 'x-url': URL['lifecycle'] + s['slug'] + '/',
        'x-tip': f'{s["name"]} — lifecycle stage ({s["phase"]})\n{(s.get("description") or "")[:200]}',
        'x-info': {'kind': 'Lifecycle stage', 'name': s['name'], 'url': URL['lifecycle'] + s['slug'] + '/',
                   'text': s.get('description') or '', 'groups': [
                       ['Phase', [{'name': s['phase']}]],
                       ['Policies at this stage', [{'name': p['name'], 'url': URL['policies'] + p['slug'] + '/'}
                                                   for p in policies if s['slug'] in stages_of(p)]]]}}}
for p in sorted(policies, key=lambda p: p['slug']):
    st = stages_of(p)
    if not st:
        dropped.append(p['slug']); continue
    home, others = st[0], st[1:]
    path = '/' + seg(home) + '/' + p['slug'] + ''.join('/' + seg(o) for o in others[:1])   # last segment = a change
    strs = strats_of.get(p['slug'], []) + [x for x in strategies if x['slug'] in (p.get('strategies') or [])
                                            and x not in strats_of.get(p['slug'], [])]
    exps = {}
    for x in list(p.get('experiences') or []) + [e for s_ in strs for e in s_.get('experiences') or []]:
        r = exp_ref(x)
        if r:
            exps[r['name']] = r
    rls = [{'name': rule_by[r]['name'], 'url': URL['rules'] + r + '/'} for r in p.get('rules') or [] if r in rule_by]
    groups = [['Lifecycle', [{'name': stage_by[s]['name'], 'url': URL['lifecycle'] + s + '/'} for s in st]],
              ['Strategies', [{'name': s_['name'], 'url': URL['strategies'] + s_['slug'] + '/'} for s_ in strs]],
              ['Experiences', list(exps.values())],
              ['Rules', rls]]
    paths[path] = {'get': {
        'operationId': p['slug'], 'tags': [line_of[p['slug']]], 'summary': p['name'],
        'x-station': p['name'], 'x-url': URL['policies'] + p['slug'] + '/',
        'x-tip': f'{p["name"]} — policy\n' + ' · '.join(f'{len(g[1])} {g[0].lower()}' for g in groups[1:]),
        'x-info': {'kind': 'Policy', 'name': p['name'], 'url': URL['policies'] + p['slug'] + '/',
                   'text': p.get('description') or '', 'groups': [g for g in groups if g[1]]}}}

os.makedirs(os.path.join(WORK, 'openapi'), exist_ok=True)
os.makedirs(os.path.join(WORK, 'arazzo'), exist_ok=True)
yaml.safe_dump({'openapi': '3.1.0', 'info': {'title': 'How to do APIs', 'version': '1'}, 'paths': paths},
               open(os.path.join(WORK, 'openapi', f'{SLUG}-openapi.yml'), 'w'), sort_keys=False, allow_unicode=True)

# ---------- strategies as express routes, in lifecycle order ----------
for f in os.listdir(os.path.join(WORK, 'arazzo')):
    os.remove(os.path.join(WORK, 'arazzo', f))
n_routes = 0
for st in strategies:
    ps = [p for p in st.get('policies') or [] if p in pol_by and stages_of(pol_by[p])]
    ps.sort(key=lambda p: order[stages_of(pol_by[p])[0]])
    if len(ps) < 2:
        continue
    n_routes += 1
    yaml.safe_dump({'arazzo': '1.0.1', 'info': {'title': st['name'], 'version': '1'},
                    'workflows': [{'workflowId': st['slug'], 'steps': [{'stepId': p, 'operationId': p} for p in ps]}]},
                   open(os.path.join(WORK, 'arazzo', f'{st["slug"]}.yml'), 'w'), sort_keys=False)

# ---------- districts = lifecycle phases ----------
PHASE_COLORS = {'Define': '#7B3FE4', 'Design': '#1F5FD1', 'Develop': '#0E9F6E', 'Beta': '#E07B12',
                'Production': '#D6264F', 'Deprecate': '#4A5568'}
phases = list(dict.fromkeys(s['phase'] for s in stages))
fams = {
    'title': 'How to do APIs · The API Evangelist Transit Map',
    'subtitle': '{N} stops: the 30 lifecycle stages (★) and the policies that apply at each · {L} lines, one per stage, '
                'in six districts from Define to Deprecate · {R} strategies as express routes · click any stop for its '
                'strategies, experiences and rules',
    'express_title': 'STRATEGIES', 'express_sub': 'each strategy runs through its policies · hover to trace',
    'express_chars': 44, 'lines': 'tags',
    'source': 'Source: lifecycle, policies, strategies, experiences and rules at *.apievangelist.com · '
              'api-transit-maps/howto.py · every stop links to its API Evangelist page',
    'circle': ['Lifecycle'], 'weave': True,
    'families': [{'name': 'Lifecycle', 'color': '#1A1F2C', 'tags': ['Lifecycle']}] + [{'name': ph, 'color': PHASE_COLORS.get(ph, '#4A5568'),
                  'tags': [t for s in stages if s['phase'] == ph for t in stage_lines[s['slug']]]} for ph in phases],
    'max_line': 200,
}
fam_file = os.path.join(ROOT, f'{SLUG}-families.yml')
yaml.safe_dump(fams, open(fam_file, 'w'), sort_keys=False, allow_unicode=True)

print(f'{len(stages)} stages, {len(paths) - len(stages)} policies as stops ({len(dropped)} with no lifecycle stage: '
      f'{", ".join(dropped[:8])}), {n_routes} strategies as routes', file=sys.stderr)
os.makedirs(os.path.join(ROOT, SLUG), exist_ok=True)
sys.exit(subprocess.call([sys.executable, 'build_network_map.py', WORK, SLUG, fam_file] + sys.argv[1:], cwd=ROOT))
