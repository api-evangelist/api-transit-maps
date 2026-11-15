"""Shared model for API transit maps: stops, lines, families, colours, interchanges, express routes.

load(provider_dir, families_yml) -> namespace with ops, byid, lines, fam_lines, color, codes,
transfers, inter_ids, routes, route_at, label(), segs(), slug.
"""
import os, re, glob, collections, types
import yaml

class _NoDates(yaml.SafeLoader):
    """specs are text: '2019-02-31' in an example must not be parsed (or crash) as a date"""
_NoDates.yaml_implicit_resolvers = {k: [(tag, rx) for tag, rx in v if tag != 'tag:yaml.org,2002:timestamp']
                                    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
def _yload(f):
    return yaml.load(f, Loader=_NoDates)

def load(PROV, FAM_FILE=None):
    slug = os.path.basename(os.path.normpath(PROV))
    METHODS = ('get', 'post', 'put', 'patch', 'delete')

    # ---------- path reading ----------
    # version-like segments (/v1, /2026-03, /unstable, /api, /admin) and query strings are not places
    VERSION_SEG = re.compile(r'^(v\d+(\.\d+)*|\d{4}-\d{2}(-\d{2})?|unstable|latest|api|admin|rest)$', re.I)
    def clean(p):
        p = p.split('?')[0]
        return '/'.join(re.sub(r'\.(json|xml)$', '', x) for x in p.split('/'))
    def segs(p):
        return [x for x in clean(p).split('/') if x and not VERSION_SEG.match(x)]
    def version_of(p):
        v = [x for x in p.split('?')[0].split('/') if x and VERSION_SEG.match(x) and not re.match(r'^(api|admin|rest)$', x, re.I)]
        return tuple('0000' if x.lower() == 'unstable' else x for x in v)

    # ---------- load operations ----------
    ops = []
    for f in sorted(glob.glob(os.path.join(PROV, 'openapi', '*.yml')) + sorted(glob.glob(os.path.join(PROV, 'openapi', '*.yaml'))) + sorted(glob.glob(os.path.join(PROV, 'openapi', '*.json')))):
        try:
            d = (__import__('json').load(open(f)) if f.endswith('.json') else _yload(open(f))) or {}
        except (yaml.YAMLError, ValueError) as e:  # one malformed spec (duplicate key, broken flow map) must
            import sys                         # not sink the provider: map the rest, say which was skipped
            print(f'skipped unparseable {os.path.basename(f)}: {str(e).splitlines()[0]}', file=sys.stderr)
            continue
        if not isinstance(d, dict):
            continue
        api = re.sub(r'-openapi\.(ya?ml|json)$', '', os.path.basename(f))
        for p, item in (d.get('paths') or {}).items():
            for m, o in (item or {}).items():
                if m not in METHODS or not isinstance(o, dict):
                    continue
                oid = o.get('operationId') or f'{m}{p}'
                srv = ((item.get('servers') or o.get('servers') or [{}])[0] or {}).get('url', '')
                ops.append(dict(id=oid, opid=oid, api=api, m=m, p=p, host=srv, tag=(o.get('tags') or ['Untagged'])[0],
                                dep=bool(o.get('deprecated')), summary=(o.get('summary') or '').strip(),
                                raw=o, comp=(d.get('components') or {}).get('schemas') or {}))
    # operationIds can repeat across specs (Twilio: FetchTask in TaskRouter and Flex). Internal ids
    # must be unique; `opid` keeps the real one for the apis.io anchor.
    seen_ids = collections.Counter(o['id'] for o in ops)
    for o in ops:
        if seen_ids[o['opid']] > 1:
            o['id'] = f"{o['api']}:{o['opid']}"
    # families file may re-home operations whose tag is a split artefact (verb tags, singular/plural twins)
    fams_raw = (yaml.safe_load(open(FAM_FILE)) if FAM_FILE else None) or {}
    explicit_fams = bool(fams_raw.get('families'))
    retagged = collections.Counter()
    for o in ops:
        for r in fams_raw.get('retag') or []:
            if o['tag'] == r['tag'] and re.search(r.get('match', ''), o['p']):
                retagged[(o['tag'], r['to'])] += 1
                o['orig_tag'] = o['tag']; o['tag'] = r['to']
                break
    # a tag that spans several products (one per host) becomes one line per product
    split_tags = set(fams_raw.get('split_by_host') or [])
    for o in ops:
        if o['tag'] in split_tags and o['host']:
            prod = re.sub(r'^https?://', '', o['host']).split('.')[0].replace('-', ' ').title()
            o['orig_tag'] = o['tag']; o['tag'] = f"{prod} {o['tag']}"
            retagged[(o['orig_tag'], o['tag'])] += 1

    # ---------- the same operation listed twice: older API versions, or two specs ----------
    # Shopify publishes every operation once per version (2020-01, 2020-10, unstable...). A transit map
    # of four timetables is noise: keep the newest version of each (method, path) per host.
    collapsed = collections.Counter()
    if fams_raw.get('collapse_versions', True):
        best_ = {}
        for o in ops:
            key = (o['m'], o['host'], '/'.join('{}' if x.startswith('{') else x.lower() for x in segs(o['p'])))
            if key not in best_ or version_of(o['p']) > version_of(best_[key]['p']):
                if key in best_:
                    collapsed['older version' if version_of(o['p']) != version_of(best_[key]['p']) else 'listed twice'] += 1
                best_[key] = o
            else:
                collapsed['older version' if version_of(o['p']) != version_of(best_[key]['p']) else 'listed twice'] += 1
        keep = {id(o) for o in best_.values()}
        ops = [o for o in ops if id(o) in keep]

    # ---------- where do lines come from? ----------
    # tags  : the provider's tags are resources -> tags are lines (giant ones split, 1-2 stop ones merged)
    # coarse: a few huge tags that are really products (Xero Accounting/Payroll) -> tags are families,
    #         path groups are lines
    # paths : tags are verbs or capability words (Get, Create, Basic, Batch) -> lines from paths
    GENERIC = {'get', 'gets', 'create', 'creates', 'delete', 'deletes', 'update', 'updates', 'list', 'lists',
               'describe', 'basic', 'batch', 'advanced', 'search', 'all', 'count', 'code', 'default', 'general',
               'misc', 'other', 'untagged', 'api', 'core', 'public', 'private', 'beta', 'v1', 'v2', 'v3'}
    MAX_LINE = int(fams_raw.get('max_line', 45))
    tagc = collections.Counter(o['tag'] for o in ops)
    n_ops = max(1, len(ops))
    generic_share = sum(c for t, c in tagc.items() if t.lower().strip() in GENERIC) / n_ops
    biggest = tagc.most_common(1)[0][1] if tagc else 0
    mode = fams_raw.get('lines', 'auto')
    if mode == 'auto':
        tiny_share = sum(1 for t, c in tagc.items() if c <= 2) / max(1, len(tagc))
        if generic_share > 0.3 or (biggest > 0.6 * n_ops and len(tagc) < 4) or (tiny_share > 0.5 and len(tagc) > 20):
            mode = 'paths'
        elif len(tagc) <= 15 and biggest > MAX_LINE * 2 and not fams_raw.get('families'):
            mode = 'coarse'
        else:
            mode = 'tags'

    def human_seg(x):
        x = re.sub(r'\(.*?\)|\{.*?\}|[:;=].*$', '', x)              # Products({key}) -> Products
        if re.fullmatch(r'[A-Za-z]{2,3}', x) and x.lower() not in ('api', 'app', 'job', 'run', 'tag', 'key', 'log', 'set', 'map', 'pay', 'tax', 'fee', 'use', 'web', 'ads'):
            return x.upper()                                          # ng, cv, ccm, ai -> NG, CV, CCM, AI
        x = re.sub(r'([a-z])([A-Z])', r'\1 \2', x).replace('_', ' ').replace('-', ' ')
        return ' '.join(w if w.isupper() and len(w) > 1 else w.capitalize() for w in x.split())

    disp = {}                                          # lowercased segment -> how the provider writes it
    for o in ops:
        for y in segs(o['p']):
            if not y.startswith('{'):
                disp.setdefault(y.lower(), y)

    # scope roots: /accounts/{id}/..., /zones/{id}/..., /repos/{owner}/{repo}/... say WHOSE resource it
    # is, not WHICH product. A first segment is a scope when it covers >= 10% of operations and is
    # nearly always followed by a parameter; the product is the next named segment.
    first_c = collections.Counter(); first_p = collections.Counter()
    for o in ops:
        sg = segs(o['p'])
        if sg:
            first_c[sg[0].lower()] += 1
            if len(sg) > 2 and sg[1].startswith('{'):
                first_p[sg[0].lower()] += 1
    scopes = {k for k, c in first_c.items() if (c >= 0.1 * max(1, len(ops)) and first_p[k] >= 0.7 * c)
              or (c >= 0.5 * max(1, len(ops)) and len(first_c) > 1)}
    def namespace(o):
        named = [y for y in segs(o['p']) if not y.startswith('{')]
        if not named:
            return 'Root'
        if named[0].lower() in scopes and len(named) > 1:
            return human_seg(disp.get(named[1].lower(), named[1]))
        return human_seg(disp.get(named[0].lower(), named[0]))

    def path_groups(group_ops, depth0):
        """group operations by their first k meaningful segments, deepening only groups that are too big"""
        out = collections.OrderedDict()
        def rec(gops, k):
            keyf = lambda o: tuple(x.lower() for x in [y for y in segs(o['p']) if not y.startswith('{')][:k])
            g = collections.OrderedDict()
            for o in gops:
                g.setdefault(keyf(o), []).append(o)
            for key, lst in g.items():
                nxt = len({tuple(x.lower() for x in [y for y in segs(o['p']) if not y.startswith('{')][:k + 1]) for o in lst})
                flat = len(lst) > MAX_LINE * 2 and nxt > 0.6 * len(lst)
                if len(lst) > MAX_LINE and k < depth0 + 3 and nxt > 1 and not flat:
                    rec(lst, k + 1)
                elif flat:
                    # flat RPC catalog (/x402/caesar-cipher, /x402/evm-batch-gas-sum, ...): one segment
                    # of hundreds of distinct leaves. Lines come from the leaf's first word (evm, json, csv)
                    def lw(o):
                        nm = [y for y in segs(o['p']) if not y.startswith('{')]
                        leaf = nm[len(key)] if len(nm) > len(key) else (nm[-1] if nm else 'root')
                        return re.split(r'[-_.]|(?<=[a-z])(?=[A-Z])', leaf)[0].lower() or 'root'
                    byw = collections.OrderedDict()
                    for o in lst:
                        byw.setdefault(lw(o), []).append(o)
                    if len(byw) < 2:
                        out[key] = lst; continue
                    small = []
                    for w, wl in byw.items():
                        if len(wl) >= 3:
                            out[key + (w,)] = wl
                        else:
                            small.extend(wl)
                    # long tail: try the leaf's last word (…-check, …-sum, …-report) before 'more'
                    byl = collections.OrderedDict()
                    for o in small:
                        nm = [y for y in segs(o['p']) if not y.startswith('{')]
                        leaf = nm[len(key)] if len(nm) > len(key) else (nm[-1] if nm else 'root')
                        byl.setdefault(re.split(r'[-_.]|(?<=[a-z])(?=[A-Z])', leaf)[-1].lower() or 'root', []).append(o)
                    rest_ = []
                    for w, wl in byl.items():
                        if len(wl) >= 3 and key + (w,) not in out:
                            out[key + (w,)] = wl
                        else:
                            rest_.extend(wl)
                    if rest_:                       # still too long: alphabetical runs, like an index
                        rest_.sort(key=lambda o: o['p'].lower())
                        nch = -(-len(rest_) // MAX_LINE)
                        for c_ in range(nch):
                            part = rest_[c_ * len(rest_) // nch:(c_ + 1) * len(rest_) // nch]
                            lf = lambda o: ([y for y in segs(o['p']) if not y.startswith('{')] or ['?'])[-1][:1].upper()
                            nm_ = 'more' if nch == 1 else f'{lf(part[0])}–{lf(part[-1])}'
                            out[key + (nm_,)] = part
                else:
                    out[key] = lst
        rec(group_ops, depth0)
        # fold 1-2 stop groups into the group sharing their longest path prefix (any depth)
        for key in sorted(list(out), key=lambda q: len(out[q])):
            if key in out and len(out[key]) <= 2:
                def cp(q):
                    n_ = 0
                    while n_ < min(len(q), len(key)) and q[n_] == key[n_]:
                        n_ += 1
                    return n_
                cands = [q for q in out if q != key and len(out[q]) > 2 and cp(q) >= 1]
                if cands:
                    tgt = max(cands, key=lambda q: (cp(q), len(out[q])))
                    out[tgt].extend(out.pop(key))
        return out

    def group_name(key, drop, avoid=''):
        parts = [human_seg(disp.get(x, x)) for x in key[drop:]] or [human_seg(disp.get(x, x)) for x in key[-1:]]
        words, seen_w = [], {w.lower() for w in avoid.split()}
        for w in ' '.join(parts).split():
            if w.lower() not in seen_w:
                words.append(w); seen_w.add(w.lower())
        return ' '.join(words) if words else (' '.join(parts) or 'Root')

    auto_fam_of = {}                                   # line -> family name, filled by the modes below
    if mode == 'paths':
        groups = path_groups(ops, 1)
        for key, lst in groups.items():
            name = group_name(key, 1 if len(key) > 1 else 0)
            fam = namespace(lst[0]) if key else 'API'
            if name in auto_fam_of and auto_fam_of[name] != fam:
                name = f'{fam} {name}'
            for o in lst:
                o['orig_tag'] = o['tag']; o['tag'] = name
            auto_fam_of[name] = fam
    elif mode == 'coarse':
        for t in list(tagc):
            lst = [o for o in ops if o['tag'] == t]
            groups = path_groups(lst, 1)
            for key, g in groups.items():
                name = group_name(key, 0)
                if name in auto_fam_of and auto_fam_of[name] != t:
                    name = f'{t} {group_name(key, 0, t)}'
                for o in g:
                    o['orig_tag'] = o['tag']; o['tag'] = name
                auto_fam_of[name] = t
    else:
        # tags are lines: split giant tags where their paths diverge, merge 1-2 stop tags into the
        # line that owns their root resource
        for t, c in list(tagc.items()):
            if c > MAX_LINE * 1.5:
                lst = [o for o in ops if o['tag'] == t]
                common = 0
                sl = [[y.lower() for y in segs(o['p']) if not y.startswith('{')] for o in lst]
                while all(len(x) > common for x in sl) and len({x[common] for x in sl}) == 1:
                    common += 1
                groups = path_groups(lst, common + 1)
                if len(groups) > 1:
                    for key, g in groups.items():
                        name = f'{t} · {group_name(key, common, t)}'
                        for o in g:
                            o['orig_tag'] = o['tag']; o['tag'] = name
                        retagged[(t, name)] += len(g)
        tagc = collections.Counter(o['tag'] for o in ops)
        owner_ng = collections.defaultdict(collections.Counter)
        for o in ops:
            sg = [y.lower() for y in segs(o['p']) if not y.startswith('{')]
            if sg and o['tag'].lower() not in GENERIC:
                owner_ng[sg[0]][o['tag']] += 1
        for o in ops:                              # 'All', 'Get', 'Batch' are not lines: re-home by path
            if o['tag'].lower().strip() in GENERIC and not explicit_fams:
                sg = [y for y in segs(o['p']) if not y.startswith('{')]
                if sg and owner_ng[sg[0].lower()]:
                    new = owner_ng[sg[0].lower()].most_common(1)[0][0]
                else:
                    new = human_seg(sg[0]) if sg else 'Root'
                retagged[(o['tag'], new)] += 1
                o['orig_tag'] = o['tag']; o['tag'] = new
        tagc = collections.Counter(o['tag'] for o in ops)
        root_owner = collections.defaultdict(collections.Counter)
        for o in ops:
            sg = [y.lower() for y in segs(o['p']) if not y.startswith('{')]
            if sg:
                root_owner[sg[0]][o['tag']] += 1
        for o in ops:
            if tagc[o['tag']] <= 2 and not fams_raw.get('retag'):
                sg = [y.lower() for y in segs(o['p']) if not y.startswith('{')]
                if sg:
                    owner, n_ = root_owner[sg[0]].most_common(1)[0]
                    if owner != o['tag'] and tagc[owner] > 2 and owner.lower() not in GENERIC:
                        retagged[(o['tag'], owner)] += 1
                        o['orig_tag'] = o['tag']; o['tag'] = owner

    byid = {o['id']: o for o in ops}
    byid_ci = {}
    for o in ops:
        byid_ci.setdefault(o['opid'].lower(), o['id'])
        byid_ci.setdefault(o['id'].lower(), o['id'])

    # ---------- families ----------
    PALETTE = ['#D6264F', '#0E9F6E', '#1F5FD1', '#E07B12', '#7B3FE4', '#B0287F', '#0B7A75', '#B8860B',
               '#C2410C', '#2E7D32', '#5C6BC0', '#00838F', '#8D6E63', '#4A5568']
    if fams_raw.get('families'):
        fams = fams_raw
    else:
        if not auto_fam_of:
            # tags are lines: a line's family is the product host serving it (when there are several),
            # else the first path segment most of its operations start with
            hosts_ = collections.Counter(re.sub(r'^https?://', '', o['host']).split('/')[0].split('.')[0] for o in ops if o['host'])
            by_host = len([h for h, c in hosts_.items() if c >= 5]) >= 3
            for t in {o['tag'] for o in ops}:
                votes = collections.Counter()
                for o in ops:
                    if o['tag'] != t:
                        continue
                    if by_host and o['host']:
                        votes[human_seg(re.sub(r'^https?://', '', o['host']).split('/')[0].split('.')[0])] += 1
                    else:
                        votes[namespace(o)] += 1
                auto_fam_of[t] = votes.most_common(1)[0][0]
        n_lines_auto = len({o['tag'] for o in ops})
        if n_lines_auto <= int(fams_raw.get('single_city_max', 20)):
            # a small API is one city: one district, every line its own colour
            for t in list(auto_fam_of) + [o['tag'] for o in ops]:
                auto_fam_of[t] = slug.replace('-', ' ').title()
            fams_raw = dict(fams_raw); fams_raw['distinct_colors'] = True
        famc = collections.Counter()
        for o in ops:
            famc[auto_fam_of.get(o['tag'], 'API')] += 1
        MAXF = int(fams_raw.get('max_families', 12))
        keepf = [f for f, _ in famc.most_common(MAXF - 1)] if len(famc) > MAXF else list(famc)
        # a family that is only one line is not a district: fold small one-line families into 'More'
        lines_in = collections.Counter(auto_fam_of[t] for t in {o['tag'] for o in ops} if t in auto_fam_of)
        keepf = [f for f in keepf if not (lines_in[f] <= 1 and famc[f] < 0.03 * n_ops and len(famc) > 6)]
        famlist = []
        for k_, f in enumerate(sorted(keepf, key=lambda f: -famc[f])):
            famlist.append({'name': f, 'color': PALETTE[k_ % len(PALETTE)],
                            'tags': sorted(t for t, ff in auto_fam_of.items() if ff == f)})
        famlist.append({'name': 'More', 'color': '#6B7280', 'tags': []})
        fams = dict(fams_raw); fams['families'] = famlist
        fams.setdefault('branching', True)
        fams_raw = fams
    auto_mode = mode
    tag2fam = {}
    for fam in fams['families']:
        for t in fam.get('tags') or []:
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

    # ---------- local lines: a family's 1-2 stop resources ride one thinner line (DLR / tram) ----------
    local_lines = set()
    if fams_raw.get('local_lines', not explicit_fams):
        for fam in fams['families']:
            small = [t for t in lines if tag2fam.get(t, default_fam) == fam['name'] and len(lines[t]) <= 2]
            if len(small) < 2:
                continue
            name = f"{fam['name']} Local"
            merged = []
            for t in small:
                for o in lines.pop(t):
                    retagged[(o['tag'], name)] += 1
                    o['orig_tag'] = o.get('orig_tag', o['tag']); o['tag'] = name
                    merged.append(o)
            merged.sort(key=order_key)
            # a family with dozens of loose resources (Kong: 122 stops) would get one endless tram;
            # cut it into path-ordered runs of <= 30, numbered (Local 1, Local 2, ...)
            CAP = 30
            nch = -(-len(merged) // CAP)
            for c_ in range(nch):
                part = merged[c_ * len(merged) // nch:(c_ + 1) * len(merged) // nch]
                if nch > 1:
                    nm_ = f"{name} {c_ + 1}"
                    for o in part:
                        o['tag'] = nm_
                else:
                    nm_ = name
                lines[nm_] = part; tag2fam[nm_] = fam['name']; local_lines.add(nm_)
                fam.setdefault('tags', []).append(nm_)

    # ---------- resource homes + interchanges ----------
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
                    transfers.append((o, h, 'path')); seen.add(h['id'])

    # ---------- Arazzo express routes ----------
    routes = []
    for f in sorted(glob.glob(os.path.join(PROV, 'arazzo', '*.yml'))):
        if os.path.basename(f).startswith('_'):
            continue
        try:
            d = _yload(open(f)) or {}
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

    def code_candidates(tag):
        """badge codes, best first: initials, first three letters, first letter + consonants, ..."""
        words = [tag[:-1]] if re.fullmatch(r'[A-Z]{2,}s', tag) else re.findall(r'[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+', tag)
        flat = re.sub(r'[^A-Za-z0-9]', '', tag).upper()
        cons = flat[0] + re.sub(r'[AEIOU]', '', flat[1:])
        cands = []
        if len(words) > 1:
            ini = ''.join(w[0] for w in words).upper()
            cands += [ini[:4], (words[0][:2] + ''.join(w[0] for w in words[1:])).upper()[:4]]
        cands += [flat[:3], cons[:3], flat[0] + cons[1:3] if len(cons) > 2 else flat[:3], flat[:4], cons[:4],
                  flat[0] + flat[2:4], flat[0] + cons[2:4] if len(cons) > 3 else flat[:2] + flat[-1]]
        seen_c = []
        for c in cands:
            if c and len(c) >= 2 and c not in seen_c:
                seen_c.append(c)
        return seen_c
    codes, used = {}, set()
    for t in sorted(lines, key=lambda t: -len(lines[t])):      # big lines claim the best codes
        for c in code_candidates(t):
            if c not in used:
                break
        else:
            base = code_candidates(t)[0][:3]; n = 2
            while f'{base}{n}' in used:
                n += 1
            c = f'{base}{n}'
        used.add(c); codes[t] = c

    def singular(w):
        for a, b in (('ies', 'y'), ('sses', 'ss'), ('ses', 's'), ('xes', 'x'), ('s', '')):
            if w.endswith(a) and not w.endswith('ss'):
                return w[:-len(a)] + b
        return w
    def human(seg):
        return seg.replace('_', ' ').replace('-', ' ').strip().lower()
    ACTION_VERBS = {'search': 'Search'}
    _rpcv = {'get', 'list', 'create', 'update', 'remove', 'delete', 'sync', 'cancel', 'refresh', 'exchange'}
    rpc_style = sum(1 for o in ops if [x for x in segs(o['p']) if not x.startswith('{')][-1:] and
                    [x for x in segs(o['p']) if not x.startswith('{')][-1].lower() in _rpcv) >= 0.4 * max(1, len(ops))

    def label(o):
        """Station name from the method + path, like a street name: the line already says which resource."""
        if o['raw'].get('x-station'):                     # a hand-named station (the How to do APIs map)
            return o['raw']['x-station']
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
        # RPC style (Plaid: POST /transfer/get, /transactions/sync): the last segment is the verb
        RPC = {'get', 'list', 'create', 'update', 'remove', 'delete', 'sync', 'cancel', 'refresh',
               'exchange', 'invalidate', 'fire', 'reset', 'import', 'export', 'enrich', 'recategorize', 'migrate',
               'authorize', 'evaluate', 'register', 'set', 'unlink', 'archive', 'reverse', 'send', 'simulate',
               'fire_webhook', 'categorize', 'verify', 'start', 'complete', 'submit', 'retry', 'link', 'unarchive'}
        if not is_item and len(nouns) >= 2 and (nouns[-1].lower() in RPC or rpc_style):
            subj = ' '.join(human(x) for x in nouns[-3:-1] if x.lower() not in RPC)
            return f"{human(nouns[-1]).capitalize()} {subj}".strip()
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
        """line k of n in a family: hue spread +-28 deg around the family colour, lightness alternating
        so neighbouring lines differ; the family's biggest line keeps the family colour"""
        import colorsys
        r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
        h, l, s_ = colorsys.rgb_to_hls(r, g, b)
        if n <= 1 or k == 0:
            return hexc
        spread = min(28, 6 * n) / 360
        hk = h + spread * ((k + 1) // 2) / max(1, (n // 2)) * (1 if k % 2 else -1)
        lk = min(0.62, max(0.26, l + (0.12 if (k // 2) % 2 else -0.08)))
        sk = min(1, s_ * (0.95 if k % 3 else 1.1))
        r2, g2, b2 = colorsys.hls_to_rgb(hk % 1, lk, sk)
        return '#%02x%02x%02x' % (int(r2 * 255), int(g2 * 255), int(b2 * 255))

    fam_lines = collections.OrderedDict((f['name'], []) for f in fams['families'])
    # a line with no tag mapping joins the family whose `hosts` match most of its operations
    def host_fam(t):
        votes = collections.Counter()
        for o in lines[t]:
            for f in fams['families']:
                if any(h in o.get('host', '') for h in f.get('hosts') or []):
                    votes[f['name']] += 1; break
        return votes.most_common(1)[0][0] if votes else None
    for t in lines:
        fam_lines.setdefault(tag2fam.get(t) or host_fam(t) or default_fam, []).append(t)
    color = {}
    for fname, ts in fam_lines.items():
        ts.sort(key=lambda t: -len(lines[t]))
        for k, t in enumerate(ts):
            color[t] = (PALETTE[k % len(PALETTE)] if fams_raw.get('distinct_colors')
                        else shade(fam_by_name[fname]['color'], k, len(ts)))

    # ---------- interchanges from request parameters ----------
    # a string field named after another line's resource (customer, payment_method, price...) is a
    # place where you change lines. Namespaces (billing, issuing, tax...) are not resources.
    namespaces = set()
    for o in ops:
        s_ = segs(o['p'])
        if len(s_) >= 2 and not s_[1].startswith('{') and s_[1] != 'search':
            namespaces.add(s_[0].lower())
    resource = {}
    for coll, h in home.items():
        if coll not in namespaces:
            resource[singular(coll.replace('-', '_'))] = h
    def is_id_field(sc):
        if not isinstance(sc, dict):
            return False
        if sc.get('type') == 'string':
            return True
        return any(isinstance(x, dict) and x.get('type') == 'string' for x in (sc.get('anyOf') or []))
    def op_fields(o):
        raw = o['raw']; out = set()
        for prm in raw.get('parameters') or []:
            if isinstance(prm, dict) and is_id_field(prm.get('schema')):
                out.add(prm.get('name'))
        for ct, c in ((raw.get('requestBody') or {}).get('content') or {}).items():
            sc = (c or {}).get('schema') or {}
            if '$ref' in sc:
                sc = o['comp'].get(sc['$ref'].split('/')[-1], {})
            for k, v in (sc.get('properties') or {}).items():
                if is_id_field(v):
                    out.add(k)
        return out
    for o in ops:
        o['refs'] = set()
        for f in op_fields(o):
            h = resource.get(f)
            if h and h['tag'] != o['tag']:
                o['refs'].add(h['tag'])
                transfers.append((o, h, 'param'))
    # one interchange per pair of lines: a path link beats a param link, then line order
    pos_in_line = {}
    for t_, st_ in lines.items():
        for k_, o_ in enumerate(st_):
            pos_in_line[o_['id']] = k_
    best = {}
    for tr in transfers:
        a_, b_ = tr[0], tr[1]; kind = tr[2] if len(tr) > 2 else 'path'
        key = frozenset((a_['tag'], b_['tag']))
        score = (0 if kind == 'path' else 1, pos_in_line[a_['id']])
        if key not in best or score < best[key][0]:
            best[key] = (score, (a_, b_, kind))
    all_links = [(tr[0], tr[1]) for tr in transfers]
    transfers = [v[1][:2] for v in best.values()]
    transfer_kind = {(v[1][0]['id'], v[1][1]['id']): v[1][2] for v in best.values()}
    inter_ids = {a['id'] for a, b in transfers} | {b['id'] for a, b in transfers}

    # ---------- auto families, second pass: merge by how products actually connect ----------
    # seeds are a split tag's pieces (GitHub Repos -> Repos · Branches...) or the product namespace;
    # while there are too many, the smallest family joins the one it shares most interchanges with.
    if not explicit_fams and not fams_raw.get('distinct_colors'):
        MAXF = int(fams_raw.get('max_families', 12))
        seed = {}
        for t, st in lines.items():
            if t in local_lines:
                continue
            o0 = st[0]
            if ' · ' in t and t.split(' · ')[0].lower() not in scopes:
                seed[t] = t.split(' · ')[0]
            else:
                seed[t] = collections.Counter(namespace(o) for o in st).most_common(1)[0][0]
        fam_of = dict(seed)
        size_f = lambda f: sum(len(lines[t]) for t, ff in fam_of.items() if ff == f)
        link_n = collections.Counter()
        def is_scope_home(o):
            named = [y.lower() for y in segs(o['p']) if not y.startswith('{')]
            return len(named) <= 1 and (not named or named[0] in scopes)
        for a_, b_ in all_links:
            if is_scope_home(a_) or is_scope_home(b_):
                continue                       # everything reaches /accounts/{id}; that is not affinity
            if a_['tag'] in fam_of and b_['tag'] in fam_of:
                link_n[(a_['tag'], b_['tag'])] += 1
        while len(set(fam_of.values())) > MAXF:
            fams_now = set(fam_of.values()) - {'More'}
            small = min(fams_now, key=size_f)
            aff_ = collections.Counter()
            for (ta, tb), n_ in link_n.items():
                fa_, fb_ = fam_of[ta], fam_of[tb]
                if fa_ == small and fb_ != small:
                    aff_[fb_] += n_
                elif fb_ == small and fa_ != small:
                    aff_[fa_] += n_
            target = aff_.most_common(1)[0][0] if aff_ else 'More'
            for t in fam_of:
                if fam_of[t] == small:
                    fam_of[t] = target
        # a merged family is named after its biggest member namespace
        for t in local_lines:
            fam_of[t] = 'More'
        ordered = sorted(set(fam_of.values()) - {'More'}, key=lambda f: -size_f(f)) + (['More'] if 'More' in fam_of.values() else [])
        famlist = [{'name': f, 'color': ('#6B7280' if f == 'More' else PALETTE[k_ % len(PALETTE)]),
                    'tags': sorted(t for t, ff in fam_of.items() if ff == f)} for k_, f in enumerate(ordered)]
        # local lines belong to the family their stops mostly came from
        for t in local_lines:
            src = collections.Counter(fam_of.get(o.get('orig_tag'), None) for o in lines[t])
            src.pop(None, None)
            if src:
                fam_of[t] = src.most_common(1)[0][0]
                for fl in famlist:
                    if fl['name'] == fam_of[t]:
                        fl['tags'].append(t)
                    elif t in fl['tags']:
                        fl['tags'].remove(t)
        famlist = [fl for fl in famlist if fl['tags']]
        fams['families'] = famlist
        fam_by_name = {f['name']: f for f in famlist}
        tag2fam = {t: f['name'] for f in famlist for t in f['tags']}
        fam_lines = collections.OrderedDict((f['name'], sorted(f['tags'], key=lambda t: -len(lines[t]))) for f in famlist)
        color = {}
        n_all = sum(len(ts) for ts in fam_lines.values())
        for fname, ts in fam_lines.items():
            # one family holding most of the API (MongoDB: 48 lines all 'Atlas') would be 48 shades of one
            # hue -- give its lines distinct palette colours, like a small single-city API
            dominant = len(ts) >= 8 and len(ts) > 0.6 * n_all
            for k, t in enumerate(ts):
                if fname == 'More':
                    color[t] = PALETTE[(k * 5) % len(PALETTE)]
                elif dominant:
                    color[t] = shade(PALETTE[k % len(PALETTE)], k // len(PALETTE), -(-len(ts) // len(PALETTE)))
                else:
                    color[t] = shade(fam_by_name[fname]['color'], k, len(ts))

    # ---------- automatic backbone (consolidated design) ----------
    # every map gets a circle line like Stripe's Customers: the line with most real interchanges
    # (scope homes excluded), so families wrap around one centre instead of drifting into islands
    if not explicit_fams and fams_raw.get('circle', 'auto') == 'auto':
        deg_ = collections.Counter()
        for a_, b_ in all_links:
            na = [y.lower() for y in segs(a_['p']) if not y.startswith('{')]
            nb = [y.lower() for y in segs(b_['p']) if not y.startswith('{')]
            if (len(na) <= 1 and (not na or na[0] in scopes)) or (len(nb) <= 1 and (not nb or nb[0] in scopes)):
                continue
            if a_['tag'] != b_['tag']:
                deg_[a_['tag']] += 1; deg_[b_['tag']] += 1
        cands = [t for t, _ in deg_.most_common() if len(lines.get(t, [])) >= 12 and t not in local_lines]
        if not cands:                     # nothing interchanges much (Plaid): the biggest line is the backbone
            cands = [t for t in sorted(lines, key=lambda t: -len(lines[t])) if len(lines[t]) >= 12 and t not in local_lines]
        # one city up to ~60 lines; beyond that the map is a conurbation of woven boroughs (no city ring)
        city_max = int(fams_raw.get('city_max_lines', 60))
        fams_raw['circle'] = cands[:1] if len(lines) <= city_max else []
        fams['circle'] = fams_raw['circle']

    # ---------- chains: a line is a trunk plus a few branches (opt-in `branching: true`) ----------
    # ops are grouped by collection path; groups form a tree (/workspaces > /workspaces/tasks > ...).
    # A child subtree with >= BRANCH_MIN stops leaves its parent's item stop as a branch (at most
    # BRANCH_MAX per junction); anything smaller stays inline on the parent's track.
    BRANCH_MIN = int(fams_raw.get('branch_min', 6)); BRANCH_MAX = int(fams_raw.get('branch_max', 3))
    def coll_key(o):
        return tuple(x.lower() for x in segs(o['p']) if not x.startswith('{'))
    chains = {}
    circles = set(fams_raw.get('circle') or [])
    for t_, st_ in lines.items():
        if not fams_raw.get('branching') or t_ in circles:
            chains[t_] = [list(st_)]; continue
        groups = collections.OrderedDict()
        for o_ in st_:
            groups.setdefault(coll_key(o_), []).append(o_)
        keys = list(groups)
        parent = {}
        for k_ in keys:
            ps = [q for q in keys if q != k_ and len(q) < len(k_) and k_[:len(q)] == q]
            parent[k_] = max(ps, key=len) if ps else None
        kids = collections.defaultdict(list)
        for k_ in keys:
            kids[parent[k_]].append(k_)
        size = {}
        def sz(k_):
            if k_ not in size:
                size[k_] = len(groups[k_]) + sum(sz(c) for c in kids[k_])
            return size[k_]
        out_ = []
        def walk(k_, chain):
            chain.extend(groups[k_])
            big = sorted([c for c in kids[k_] if sz(c) >= BRANCH_MIN], key=sz, reverse=True)[:BRANCH_MAX]
            for c in kids[k_]:
                if c not in big:
                    walk(c, chain)
            junction = next((o_ for o_ in groups[k_] if o_['m'] == 'get' and o_['p'].rstrip('/').endswith('}')),
                            groups[k_][-1])
            for c in big:
                br = [junction]; out_.append(br); walk(c, br)
        roots = kids[None]
        trunk = []; out_.insert(0, trunk)
        for r_ in roots:
            walk(r_, trunk)
        chains[t_] = [c for c in out_ if len(c) > 1 or c is trunk]
    line_mode = mode
    weave = fams_raw.get('weave', not explicit_fams)
    transfers = [tr for tr in transfers if tr[0]['tag'] != tr[1]['tag']]
    return types.SimpleNamespace(**{k: v for k, v in locals().items()})


def subset(M, keep_lines, sheet_of_line=None):
    """A sheet of an atlas: only `keep_lines`. Interchanges that leave the sheet become
    `offsheet[op_id] = [(line, sheet number)]`, printed as 'change for CODE p.N'."""
    import copy
    keep_lines = set(keep_lines)
    S = copy.copy(M)
    S.ops = [o for o in M.ops if o['tag'] in keep_lines]
    ids = {o['id'] for o in S.ops}
    S.byid = {k: v for k, v in M.byid.items() if k in ids}
    S.lines = collections.OrderedDict((t, st) for t, st in M.lines.items() if t in keep_lines)
    S.chains = {t: c for t, c in M.chains.items() if t in keep_lines}
    S.fam_lines = collections.OrderedDict((f, [t for t in ts if t in keep_lines]) for f, ts in M.fam_lines.items())
    S.fam_lines = collections.OrderedDict((f, ts) for f, ts in S.fam_lines.items() if ts)
    S.fams_raw = dict(M.fams_raw)
    S.fams_raw['families'] = [f for f in M.fams_raw['families'] if f['name'] in S.fam_lines]
    S.transfers = [(a, b) for a, b in M.transfers if a['id'] in ids and b['id'] in ids]
    S.all_links = [(a, b) for a, b in M.all_links if a['id'] in ids and b['id'] in ids]
    S.routes = [r for r in M.routes if all(x in ids for x in r['stops'])]
    S.local_lines = {t for t in M.local_lines if t in keep_lines}
    # each sheet has its own backbone: the on-sheet line with most on-sheet interchanges, else the biggest
    if getattr(M, 'weave', False):
        deg = collections.Counter()
        for a, b in S.all_links:
            if a['tag'] != b['tag']:
                deg[a['tag']] += 1; deg[b['tag']] += 1
        cands = [t for t, _ in deg.most_common() if len(S.lines[t]) >= 12 and t not in S.local_lines] or \
                [t for t in sorted(S.lines, key=lambda t: -len(S.lines[t])) if len(S.lines[t]) >= 12]
        S.fams_raw['circle'] = cands[:1]
        S.chains = dict(S.chains)
        for t in S.fams_raw['circle']:
            S.chains[t] = [list(S.lines[t])]
    S.offsheet = collections.defaultdict(list)
    for a, b in M.transfers:
        for x, y in ((a, b), (b, a)):
            if x['id'] in ids and y['id'] not in ids:
                S.offsheet[x['id']].append((y['tag'], (sheet_of_line or {}).get(y['tag'])))
    return S
