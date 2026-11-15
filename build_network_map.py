#!/usr/bin/env python3
"""API transit map, network layout.

1. stress-majorisation places every stop (line edges ~1, interchange pairs pulled together,
   lines with no interchange tied loosely to a family anchor so families form districts)
2. grid hill-climb makes it a metro map: octilinear edges, straight running, no sharp bends,
   stops kept off other lines' track, interchange pairs adjacent
3. labels placed by local line direction with collision checks

usage: build_network_map.py <provider-dir> <out-dir> <families.yml> [--seed N] [--iters N]
"""
import sys, os, re, math, json, html, argparse, collections
import numpy as np
from scipy.sparse.csgraph import shortest_path
from scipy.sparse import lil_matrix
import transit_model as T

ap = argparse.ArgumentParser()
ap.add_argument('prov'); ap.add_argument('out'); ap.add_argument('fams')
ap.add_argument('--seed', type=int, default=7)
ap.add_argument('--iters', type=int, default=40)
ap.add_argument('--tag', default='')
ap.add_argument('--atlas-plan', default=None)
ap.add_argument('--sheet', type=int, default=None)
ap.add_argument('--relax', type=int, default=600)
ap.add_argument('--radius', type=float, default=2.6)
ap.add_argument('--umin', type=int, default=0)   # atlas retry: draw a crowded sheet at a larger scale
args = ap.parse_args()
M = T.load(args.prov, args.fams if args.fams not in ('', '/dev/null', 'auto') else None)
SHEET = None
if args.atlas_plan is not None and args.sheet is not None:
    plan = json.load(open(args.atlas_plan))
    SHEET = plan['sheets'][args.sheet - 1]
    M = T.subset(M, SHEET['lines'], {t: int(k) for t, k in plan['sheet_of_line'].items()})
rng = np.random.default_rng(args.seed)

ops = M.ops
idx = {o['id']: i for i, o in enumerate(ops)}
N = len(ops)

# ---------- graph ----------
line_edges = []          # (i, j, tag)
for t in M.lines:
    for ch in M.chains[t]:
        for a, b in zip(ch, ch[1:]):
            line_edges.append((idx[a['id']], idx[b['id']], t))
CIRCLE = set((M.fams_raw or {}).get('circle') or [])
for t in CIRCLE:                                 # a circle line closes on itself: no termini
    st = M.lines[t]
    line_edges.append((idx[st[-1]['id']], idx[st[0]['id']], t))
xfer_edges = [(idx[a['id']], idx[b['id']]) for a, b in M.transfers]
xfer_param = [M.transfer_kind.get((a['id'], b['id'])) == 'param' for a, b in M.transfers]
# only path interchanges shape the layout; parameter interchanges are drawn, not pulled
render_xfers = xfer_edges
xfer_edges = [e for e, p_ in zip(xfer_edges, xfer_param) if not p_]
xfer_param = [False] * len(xfer_edges)
if not set((M.fams_raw or {}).get('circle') or []):
    _fl = {t: f for f, ts in M.fam_lines.items() for t in ts}
    xfer_edges = [(i, j) for i, j in xfer_edges if _fl[ops[i]['tag']] == _fl[ops[j]['tag']]]
    xfer_param = [False] * len(xfer_edges)

fam_names = [f for f, ts in M.fam_lines.items() if ts]
fam_of_line = {t: f for f, ts in M.fam_lines.items() for t in ts}
# ---------- boroughs: a family of more than DIST_MAX lines is split into districts by community
# detection on its own interchange graph (lines that interchange stay together), 5..DIST_MAX lines each
import networkx as nx
DIST_MAX = int((M.fams_raw or {}).get('district_max', 16))
fam_names_orig = list(fam_names)
dist_lines = collections.OrderedDict(); DIST_FAM = {}
_CIRC = set((M.fams_raw or {}).get('circle') or [])
link_w = collections.Counter()
for a_, b_ in getattr(M, 'all_links', []):
    if a_['tag'] != b_['tag']:
        link_w[frozenset((a_['tag'], b_['tag']))] += 1
for f in fam_names:
    ts = list(M.fam_lines[f])
    if len(ts) <= DIST_MAX or _CIRC:
        dist_lines[f] = ts; DIST_FAM[f] = f; continue
    Gl = nx.Graph(); Gl.add_nodes_from(ts)
    for pr, w in link_w.items():
        a_, b_ = tuple(pr)
        if a_ in Gl and b_ in Gl:
            Gl.add_edge(a_, b_, weight=w)
    comms = [sorted(c, key=lambda t: -len(M.lines[t])) for c in
             nx.algorithms.community.greedy_modularity_communities(Gl, weight='weight')]
    groups = []
    def links_between(ga, gb):
        return sum(link_w[frozenset((x, y))] for x in ga for y in gb)
    for c in sorted(comms, key=len, reverse=True):
        while len(c) > DIST_MAX:
            groups.append(c[:DIST_MAX]); c = c[DIST_MAX:]
        room = [g for g in groups if len(g) + len(c) <= DIST_MAX]
        best_g = max(room, key=lambda g: (links_between(g, c), -len(g))) if room else None
        if best_g is not None and (links_between(best_g, c) > 0 or len(c) < 5):
            best_g.extend(c)
        else:
            groups.append(list(c))
    # fold any group still under 5 lines into the group it links to most (or the smallest)
    changed = True
    while changed and len(groups) > 1:
        changed = False
        for g in sorted(groups, key=len):
            if len(g) < 5:
                others = [h for h in groups if h is not g]
                tgt = max(others, key=lambda h: (links_between(g, h), -len(h)))
                tgt.extend(g); groups.remove(g); changed = True; break
    for g in groups:
        g.sort(key=lambda t: -len(M.lines[t]))
        short = lambda t: t.split(' · ')[-1]
        name = f"{f} · {short(g[0])}" + (f" & {short(g[1])}" if len(g) > 1 else '')
        dist_lines[name] = g; DIST_FAM[name] = f
fam_names = list(dist_lines)
fam_of_line = {t: d for d, ts in dist_lines.items() for t in ts}
if not _CIRC:
    # only interchanges inside a district pull, and at most one per pair of lines (more ties knots)
    _seen = set(); _keep = []
    def _scope_home(k):
        named = [y.lower() for y in M.segs(ops[k]['p']) if not y.startswith('{')]
        return len(named) <= 1 and (not named or named[0] in M.scopes)
    for i, j in xfer_edges:
        if _scope_home(i) or _scope_home(j):
            continue                          # /orgs/{org}, /accounts/{id}: drawn as change-for, never pulled
        pr = frozenset((ops[i]['tag'], ops[j]['tag']))
        if fam_of_line[ops[i]['tag']] == fam_of_line[ops[j]['tag']] and pr not in _seen:
            _seen.add(pr); _keep.append((i, j))
    xfer_edges = _keep; xfer_param = [False] * len(xfer_edges)
V = N + len(fam_names) + 1                     # + family anchors + centre
fa = {f: N + k for k, f in enumerate(fam_names)}
centre = V - 1
G = lil_matrix((V, V))
def edge(i, j, w):
    if G[i, j] == 0 or w < G[i, j]:
        G[i, j] = G[j, i] = w
for i, j, _ in line_edges:
    edge(i, j, 1.0)
for (i, j), prm in zip(xfer_edges, xfer_param):
    edge(i, j, 2.0 if prm else 1.0)
for t, st in M.lines.items():                  # every line touches its family district
    mid = st[len(st) // 2]
    # in a crowded district the tie lengthens, so line midpoints ring the centre instead of stacking on it
    tie = 3.0 if _CIRC else max(3.0, 2.0 + 0.35 * len(dist_lines[fam_of_line[t]]))
    edge(idx[mid['id']], fa[fam_of_line[t]], tie)
for f in fam_names:
    edge(fa[f], centre, 6.0)
D = shortest_path(G.tocsr(), directed=False)
D[np.isinf(D)] = D[~np.isinf(D)].max() + 2

# ---------- designed skeleton: circle line on an octagon, family districts pinned around it ----------
pinned = {}                                      # node -> fixed position, in hop units (1 = one line edge)
def octagon(n, step=1.0):
    """n points walking an octagon clockwise from its top-left corner: straight sides step 1,
    diagonal sides step (0.75, 0.75) so every station gap is ~1 hop"""
    dirs = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    per = [n // 8 + (1 if k < n % 8 else 0) for k in range(8)]
    # keep opposite sides equal so the ring closes: give the remainder to pairs
    per = [n // 8] * 8; r = n - 8 * (n // 8)
    for k in (0, 4, 2, 6, 1, 5, 3, 7)[:r]:
        per[k] += 1
    pts, p = [], np.zeros(2)
    for k, d in enumerate(dirs):
        for _ in range(per[k]):
            pts.append(p.copy()); p = p + np.array(d) * step
    pts = np.array(pts)
    return pts - pts.mean(0)
for t in CIRCLE:
    st = M.lines[t]
    ring = octagon(len(st))
    for o, q in zip(st, ring):
        pinned[idx[o['id']]] = q
    ring_r = np.abs(ring).max()
    # family bearings from the ring stops their lines interchange with
    ang = collections.defaultdict(list)
    ring_ids = {idx[o['id']] for o in st}
    for i, j in xfer_edges:
        for a_, b_ in ((i, j), (j, i)):
            if a_ in ring_ids and b_ not in ring_ids:
                f = fam_of_line[ops[b_]['tag']]
                ang[f].append(math.atan2(pinned[a_][1], pinned[a_][0]))
    bearing = {}
    for f in fam_names:
        if ang[f]:
            bearing[f] = math.atan2(np.mean(np.sin(ang[f])), np.mean(np.cos(ang[f])))
    free = [f for f in fam_names if f not in bearing]
    # spread: walk families by bearing, push apart to >= 360/n * .7, then drop the free ones into the widest gaps
    for f in free:
        taken = sorted(bearing.values()) or [0.0]
        gaps = [((taken[(k + 1) % len(taken)] - a) % (2 * math.pi), a) for k, a in enumerate(taken)]
        g, a = max(gaps)
        bearing[f] = a + g / 2
    order_f = sorted(bearing, key=lambda f: bearing[f])
    sep = 2 * math.pi / len(order_f) * 0.75
    for _ in range(50):
        for k, f in enumerate(order_f):
            g = order_f[(k + 1) % len(order_f)]
            d = (bearing[g] - bearing[f]) % (2 * math.pi)
            if d < sep:
                bearing[f] -= (sep - d) / 2; bearing[g] += (sep - d) / 2
    for f in fam_names:
        pinned[fa[f]] = np.array([math.cos(bearing[f]), math.sin(bearing[f])]) * ring_r * 2.1
    pinned[centre] = np.zeros(2)
if not CIRCLE:
    # no circle line: the centre family sits in the middle, the rest evenly around it, neighbours
    # chosen greedily by how many interchanges two families share
    centre_f = (M.fams_raw or {}).get('center')
    aff = collections.Counter()
    for (i, j) in render_xfers:
        fi, fj = fam_of_line[ops[i]['tag']], fam_of_line[ops[j]['tag']]
        if fi != fj:
            aff[frozenset((fi, fj))] += 1
    ring_f = [f for f in fam_names if f != centre_f]
    size = {f: sum(len(M.lines[t]) for t in dist_lines[f]) for f in fam_names}
    order_f = [max(ring_f, key=lambda f: size[f])]
    while len(order_f) < len(ring_f):
        last = order_f[-1]
        nxt = max((f for f in ring_f if f not in order_f), key=lambda f: (aff[frozenset((last, f))], size[f]))
        order_f.append(nxt)
    R = math.sqrt(N) * 0.75
    # angular share proportional to family size, so big families get more of the compass
    tot = sum(math.sqrt(size[f]) for f in order_f); a = -math.pi / 2
    for f in order_f:
        share = 2 * math.pi * math.sqrt(size[f]) / tot
        pinned[fa[f]] = np.array([math.cos(a + share / 2), math.sin(a + share / 2)]) * R
        a += share
    if centre_f in fa:
        pinned[fa[centre_f]] = np.zeros(2)
    pinned[centre] = np.zeros(2)
# ---------- weave: every line without a real interchange is tied to the ring at its family's side,
# so lines pass the core and cross each other instead of hanging alone at the edge (layout only)
if CIRCLE and getattr(M, 'weave', False):
    # two levels, like a big city: a family of >= 8 lines weaves into its own backbone (its best
    # connected line), and every backbone or small-family line weaves into the city ring
    ring_ids = [idx[o['id']] for t in CIRCLE for o in M.lines[t]]
    tied = {ops[i]['tag'] for e in xfer_edges for i in e}
    intra = collections.Counter()
    for a_, b_ in getattr(M, 'all_links', []):
        if a_['tag'] != b_['tag'] and fam_of_line.get(a_['tag']) == fam_of_line.get(b_['tag']):
            intra[a_['tag']] += 1; intra[b_['tag']] += 1
    fam_backbone = {}
    for f in fam_names:
        ts = [t for t in dist_lines[f] if t not in CIRCLE]
        if len(ts) >= 8:
            fam_backbone[f] = max(ts, key=lambda t: (intra[t], len(M.lines[t])))
    def ring_stop_near(bearing_):
        return min(ring_ids, key=lambda r: abs(math.atan2(math.sin(math.atan2(pinned[r][1], pinned[r][0]) - bearing_),
                                                            math.cos(math.atan2(pinned[r][1], pinned[r][0]) - bearing_))))
    per_fam = collections.Counter(); per_bb = collections.Counter()
    for t, st in M.lines.items():
        if t in CIRCLE or t in tied:
            continue
        f = fam_of_line[t]
        if fa[f] not in pinned:
            continue
        mid = st[len(st) // 2]
        bb = fam_backbone.get(f)
        if bb and t != bb:
            bst = M.lines[bb]; k = per_bb[f]; per_bb[f] += 1
            n_other = max(1, len(dist_lines[f]) - 1)
            edge(idx[mid['id']], idx[bst[min(len(bst) - 1, int((k + 0.5) * len(bst) / n_other))]['id']], 2.5)
            continue
        b0 = math.atan2(pinned[fa[f]][1], pinned[fa[f]][0])
        k = per_fam[f]; per_fam[f] += 1
        edge(idx[mid['id']], ring_stop_near(b0 + ((k + 1) // 2) * 0.22 * (1 if k % 2 else -1)), 2.5)
    D = shortest_path(G.tocsr(), directed=False)
    D[np.isinf(D)] = D[~np.isinf(D)].max() + 2

if not CIRCLE and getattr(M, 'weave', False):
    # conurbation: inside every borough of >= 4 lines, lines weave into the borough's backbone (its
    # best-connected line), so each borough is a compact woven mini-city rather than loose spokes
    intra = collections.Counter()
    for a_, b_ in getattr(M, 'all_links', []):
        if a_['tag'] != b_['tag'] and fam_of_line.get(a_['tag']) == fam_of_line.get(b_['tag']):
            intra[a_['tag']] += 1; intra[b_['tag']] += 1
    tied = {ops[i]['tag'] for e in xfer_edges for i in e}
    for f in fam_names:
        ts = list(dist_lines[f])
        if len(ts) < 4:
            continue
        bb = max(ts, key=lambda t: (intra[t], len(M.lines[t])))
        bst = M.lines[bb]; others = [t for t in ts if t != bb and t not in tied]
        for k, t in enumerate(others):
            st = M.lines[t]; mid = st[len(st) // 2]
            edge(idx[mid['id']], idx[bst[min(len(bst) - 1, int((k + 0.5) * len(bst) / max(1, len(others))))]['id']], 2.5)
    D = shortest_path(G.tocsr(), directed=False)
    D[np.isinf(D)] = D[~np.isinf(D)].max() + 2

pin_idx = np.array(sorted(pinned)) if pinned else np.array([], int)
pin_pos = np.array([pinned[k] for k in sorted(pinned)]) if pinned else None

# ---------- stress majorisation ----------
def stress(Dm, X0_, pins=None, iters=300):
    """SMACOF stress majorisation on distance matrix Dm; pins = {row: position} held fixed"""
    Xs = X0_.copy(); n = len(Dm)
    Wm = 1.0 / np.maximum(Dm, 1e-9) ** 2; np.fill_diagonal(Wm, 0)
    Lw = -Wm.copy(); np.fill_diagonal(Lw, Wm.sum(1)); Lw += 1e-6 * np.eye(n)
    for it in range(iters):
        diff = Xs[:, None, :] - Xs[None, :, :]
        dist = np.sqrt((diff ** 2).sum(-1)) + 1e-9
        Bm = -Wm * Dm / dist; np.fill_diagonal(Bm, 0); np.fill_diagonal(Bm, -Bm.sum(1))
        Xs = np.linalg.solve(Lw, Bm @ Xs)
        if pins:
            for k, v in pins.items():
                Xs[k] = v
    return Xs

DISTRICTS = not CIRCLE
if DISTRICTS:
    # each family is laid out on its own, then the districts are placed around the centre family
    # like neighbourhoods and pushed apart until they no longer overlap
    X = np.zeros((V, 2))
    blocks = {}
    for f in fam_names:
        mem = [k for k in range(N) if fam_of_line[ops[k]['tag']] == f] + [fa[f]]
        Dm = D[np.ix_(mem, mem)]
        Xf = stress(Dm, rng.normal(size=(len(mem), 2)) * 3, iters=250)
        Xf -= Xf[-1]                                   # anchor at the district's origin
        el_ = [np.linalg.norm(Xf[mem.index(i)] - Xf[mem.index(j)]) for i, j, _ in line_edges
               if i in mem and j in mem]
        Xf /= (np.median(el_) if el_ else 1.0)          # one hop = one line edge
        blocks[f] = (mem, Xf, np.linalg.norm(Xf, axis=1).max() + 3)
    # districts placed by the product graph: products that share interchanges sit side by side
    from scipy.sparse.csgraph import shortest_path as _sp
    fidx = {f: k for k, f in enumerate(fam_names)}
    deg_f = collections.Counter()
    for pr, n_ in aff.items():
        for f in pr:
            deg_f[f] += n_
    centre_f = (M.fams_raw or {}).get('center') or (deg_f.most_common(1)[0][0] if deg_f else fam_names[0])
    if centre_f == 'auto':
        centre_f = deg_f.most_common(1)[0][0]
    Gf = np.full((len(fam_names), len(fam_names)), np.inf)
    for pr, n_ in aff.items():
        f, g = tuple(pr)
        Gf[fidx[f], fidx[g]] = Gf[fidx[g], fidx[f]] = (blocks[f][2] + blocks[g][2]) * 0.75
    for f in fam_names:                                   # products with no cross links ride on the edge
        if deg_f[f] == 0 and f != centre_f:
            Gf[fidx[f], fidx[centre_f]] = Gf[fidx[centre_f], fidx[f]] = (blocks[f][2] + blocks[centre_f][2]) * 1.3
    np.fill_diagonal(Gf, 0)
    Df = _sp(np.where(np.isinf(Gf), 0, Gf), directed=False)
    Df[np.isinf(Df)] = np.nanmax(Df[~np.isinf(Df)]) * 1.2
    Pf = stress(Df, rng.normal(size=(len(fam_names), 2)) * Df.max() / 3, pins={fidx[centre_f]: np.zeros(2)}, iters=500)
    for f, (mem, Xf, r) in blocks.items():
        X[mem] = Xf + Pf[fidx[f]]
    # separate: push overlapping districts apart along the line between their centres
    from scipy.spatial import cKDTree
    pts_all = {f: [k for k in blocks[f][0] if k < N] for f in blocks}
    for rnd in range(300):
        clash = False
        for f in fam_names:
            for g in fam_names:
                if f >= g:
                    continue
                d_ = cKDTree(X[pts_all[g]]).query(X[pts_all[f]])[0].min()
                if d_ < (1.5 if getattr(M, 'weave', False) else 3.0):
                    clash = True
                    v = X[pts_all[f]].mean(0) - X[pts_all[g]].mean(0); v /= (np.linalg.norm(v) + 1e-9)
                    if f != centre_f:
                        X[pts_all[f]] += v * 1.0
                    if g != centre_f:
                        X[pts_all[g]] -= v * 1.0
        if not clash:
            break
    order_f = [f for f in fam_names if f != centre_f]
    # gravity compaction: pull each district toward the centre while it keeps >= GAP hops from every
    # other district's stops (point sets, not circles, so long thin districts pack tightly)
    from scipy.spatial import cKDTree
    GAP = 1.5 if getattr(M, 'weave', False) else 3.0
    pts_of = {f: [k for k in blocks[f][0] if k < N] for f in blocks}
    # compaction: each district steps toward the centre district — straight in, or sliding around an
    # obstacle (any of 8 directions that brings it closer) — while keeping GAP from every other district
    dirs8 = [np.array(d, float) / np.linalg.norm(d) for d in
             [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]]
    for rnd in range(300):
        moved_any = False
        cc = X[pts_of[centre_f]].mean(0)
        for f in sorted(order_f, key=lambda f: np.linalg.norm(X[pts_of[f]].mean(0) - cc)):
            c = X[pts_of[f]].mean(0) - cc; n_ = np.linalg.norm(c)
            if n_ < 1:
                continue
            tree = cKDTree(np.vstack([X[pts_of[g]] for g in blocks if g != f]))
            best_m = None
            for step in (3.0, 1.5, 0.5):
                for dv in [-c / n_] + dirs8:
                    trial_c = c + dv * step
                    if np.linalg.norm(trial_c) >= n_ - 0.25:
                        continue
                    trial = X[pts_of[f]] + dv * step
                    if tree.query(trial)[0].min() >= GAP:
                        if best_m is None or np.linalg.norm(trial_c) < best_m[0]:
                            best_m = (np.linalg.norm(trial_c), dv * step)
                if best_m:
                    break
            if best_m:
                X[pts_of[f]] = X[pts_of[f]] + best_m[1]; moved_any = True
        if not moved_any:
            break
    X = X[:N]
else:
    X = rng.normal(size=(V, 2)) * 10
    if pinned:
        X[pin_idx] = pin_pos
    W = 1.0 / np.maximum(D, 1e-9) ** 2
    np.fill_diagonal(W, 0)
    for it in range(400):
        diff = X[:, None, :] - X[None, :, :]
        dist = np.sqrt((diff ** 2).sum(-1)) + 1e-9
        B = -W * D / dist
        np.fill_diagonal(B, 0)
        np.fill_diagonal(B, -B.sum(1))
        Lw = -W.copy(); np.fill_diagonal(Lw, W.sum(1))
        X = np.linalg.lstsq(Lw + 1e-6 * np.eye(V), B @ X, rcond=None)[0]
        if pinned:
            X[pin_idx] = pin_pos
    X = X[:N]

# ---------- grid octilinear hill-climb ----------
CELL = 2                                        # preferred edge = 2 cells
el = np.array([np.linalg.norm(X[i] - X[j]) for i, j, _ in line_edges])
X = X * CELL if (pinned or DISTRICTS) else X / np.median(el) * CELL
PIN_N = {k: v * CELL for k, v in pinned.items() if k < N}

# continuous relaxation: springs keep line spacing, stops repel inside a personal radius,
# so dense hubs open up before anything is snapped to the grid
EI = np.array([(i, j) for i, j, _ in line_edges] + xfer_edges)
EL = np.array([CELL] * len(line_edges) + [2.5 if p_ else 1.2 for p_ in xfer_param], float)
EK = np.array([1.0] * len(line_edges) + [0.25 if p_ else 0.6 for p_ in xfer_param])
RAD = args.radius
for it in range(args.relax):
    F = np.zeros_like(X)
    v = X[EI[:, 1]] - X[EI[:, 0]]; L = np.linalg.norm(v, axis=1) + 1e-9
    f = (EK * (L - EL) / L)[:, None] * v
    np.add.at(F, EI[:, 0], f); np.add.at(F, EI[:, 1], -f)
    diff = X[:, None, :] - X[None, :, :]; dist = np.sqrt((diff ** 2).sum(-1)) + 1e-9
    push = np.clip(RAD - dist, 0, None); np.fill_diagonal(push, 0)
    F += ((push / dist)[:, :, None] * diff).sum(1) * 0.5
    X += np.clip(F, -0.5, 0.5) * 0.5
    for k, v in PIN_N.items():
        X[k] = v
P = np.round(X).astype(int)
# ---------- fisheye: like the Tube map, the dense core is drawn larger and the outskirts compressed.
# r' = R (r/R)^gamma around the ring's centre; gamma by network size (1.0 = no distortion)
if CIRCLE and getattr(M, 'weave', False):
    gamma = 1.0 if N < 300 else 0.75 if N < 800 else 0.55
    if gamma < 1.0:
        c0 = np.mean([X[idx[o['id']]] for t in CIRCLE for o in M.lines[t]], axis=0)
        v = X - c0; r = np.linalg.norm(v, axis=1) + 1e-9; R = r.max()
        X = c0 + v / r[:, None] * (R * (r / R) ** gamma)[:, None]
        # the map grew in the middle: let springs and personal space settle once more before snapping
        for it in range(200):
            F = np.zeros_like(X)
            vv = X[EI[:, 1]] - X[EI[:, 0]]; L = np.linalg.norm(vv, axis=1) + 1e-9
            f = (EK * (L - EL) / L)[:, None] * vv
            np.add.at(F, EI[:, 0], f); np.add.at(F, EI[:, 1], -f)
            diff = X[:, None, :] - X[None, :, :]; dist = np.sqrt((diff ** 2).sum(-1)) + 1e-9
            push = np.clip(RAD - dist, 0, None); np.fill_diagonal(push, 0)
            F += ((push / dist)[:, :, None] * diff).sum(1) * 0.5
            X += np.clip(F, -0.5, 0.5) * 0.5
            for k, v_ in PIN_N.items():
                X[k] = v_
X0 = X.copy()

nbr_line = collections.defaultdict(list)       # node -> [(other, tag)]
for i, j, t in line_edges:
    nbr_line[i].append((j, t)); nbr_line[j].append((i, t))
PARAM_PAIRS = {(min(i, j), max(i, j)) for (i, j), p_ in zip(xfer_edges, xfer_param) if p_}
nbr_x = collections.defaultdict(list)
for i, j in xfer_edges:
    nbr_x[i].append(j); nbr_x[j].append(i)
E = np.array([(i, j) for i, j, _ in line_edges])

def oct_dev(v):
    a = math.degrees(math.atan2(v[1], v[0])) % 45
    return min(a, 45 - a) / 22.5                # 0 octilinear .. 1 worst

BEND = {0: 0, 45: 1.0, 90: 4.0, 135: 15.0, 180: 60.0}
def turn_pen(u, v, w):
    a = np.array(v) - np.array(u); b = np.array(w) - np.array(v)
    na, nb = np.hypot(*a), np.hypot(*b)
    if na == 0 or nb == 0:
        return 60.0
    c = max(-1, min(1, (a @ b) / (na * nb)))
    ang = math.degrees(math.acos(c))
    k = min(BEND, key=lambda x: abs(x - ang))
    return BEND[k] + abs(ang - k) * 0.05

def seg_dist(p, A, B):
    AB = B - A; t = np.clip(((p - A) * AB).sum(1) / np.maximum((AB ** 2).sum(1), 1e-9), 0, 1)
    C = A + AB * t[:, None]
    return np.sqrt(((p - C) ** 2).sum(1))

def node_cost(i, pi, P):
    c = 0.0
    for j, t in nbr_line[i]:
        v = P[j] - pi; L = math.hypot(*v)
        if L == 0:
            return 1e9
        c += 6.0 * oct_dev(v) + 2.0 * max(0, CELL - L) ** 2 + 0.6 * max(0, L - CELL * 1.5) ** 2
    # straightness at i and at its neighbours
    by_t = collections.defaultdict(list)
    for j, t in nbr_line[i]:
        by_t[t].append(j)
    for t, js in by_t.items():
        if len(js) == 2:
            c += turn_pen(P[js[0]], pi, P[js[1]])
        for j in js:
            others = [k for k, tt in nbr_line[j] if tt == t and k != i]
            for k in others:
                c += turn_pen(pi, P[j], P[k])
    for j in nbr_x[i]:
        L = math.hypot(*(P[j] - pi))
        if L == 0:
            return 1e9
        c += (0.4 * max(0, L - 2.0) ** 2) if (min(i, j), max(i, j)) in PARAM_PAIRS else 3.0 * (L - 1.0) ** 2
    # occupancy + personal space: non-neighbouring stops keep >= R apart, termini more (badge room)
    dv = P - pi; dd = np.sqrt((dv ** 2).sum(1))
    dd[i] = 99
    if (dd[~exempt_mask[i]] == 0).any() or (dd == 0).any():
        return 1e9
    R = np.where(is_term | is_term[i], R_TERM, R_NODE)
    over = np.clip(R - dd, 0, None); over[exempt_mask[i]] = 0
    c += W_REP * (over ** 2).sum()
    # clearance of this stop from other track
    mask = (E[:, 0] != i) & (E[:, 1] != i)
    Af, Bf = P[E[mask, 0]].astype(float), P[E[mask, 1]].astype(float)
    d = seg_dist(pi.astype(float), Af, Bf)
    c += 25.0 * np.clip(0.9 - d, 0, None).sum()
    # this stop's own track must not run along or across other track
    for j, t in nbr_line[i]:
        pj = P[j].astype(float); pf = pi.astype(float)
        m2 = mask & (E[:, 0] != j) & (E[:, 1] != j)
        A2, B2 = P[E[m2, 0]].astype(float), P[E[m2, 1]].astype(float)
        for s in (0.25, 0.5, 0.75):
            q = pf + (pj - pf) * s
            c += 12.0 * np.clip(0.7 - seg_dist(q, A2, B2), 0, None).sum()
        c += W_CROSS * crossings(pf, pj, A2, B2)
    c += 0.02 * ((pi - X0[i]) ** 2).sum()
    return c

def crossings(p, q, A, B):
    """count proper intersections of segment pq with segments A[k]B[k]"""
    def orient(a, b, c):
        return np.sign((b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0]))
    p_ = np.broadcast_to(p, A.shape); q_ = np.broadcast_to(q, A.shape)
    o1 = orient(p_, q_, A); o2 = orient(p_, q_, B); o3 = orient(A, B, p_); o4 = orient(A, B, q_)
    return int(((o1 * o2 < 0) & (o3 * o4 < 0)).sum())

R_NODE, R_TERM, W_REP, W_CROSS = 1.9, 2.8, 30.0, 4.0
deg = collections.Counter()
for i, j, _ in line_edges:
    deg[i] += 1; deg[j] += 1
is_term = np.array([deg[i] <= 1 for i in range(N)])
exempt_mask = np.zeros((N, N), bool)
for i in range(N):
    exempt_mask[i, i] = True
    for j, _ in nbr_line[i]:
        exempt_mask[i, j] = True
    for j in nbr_x[i]:
        exempt_mask[i, j] = True

MOVES = [np.array(m) for m in [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1),
                                (2, 0), (-2, 0), (0, 2), (0, -2)]]
for sweep in range(args.iters):
    moved = 0
    for i in rng.permutation(N):
        if i in PIN_N:
            continue
        best, bc = P[i].copy(), node_cost(i, P[i], P)
        for m in MOVES[1:]:
            c = node_cost(i, P[i] + m, P)
            if c < bc - 1e-6:
                best, bc = P[i] + m, c
        if not np.array_equal(best, P[i]):
            P[i] = best; moved += 1
    if moved == 0:
        break
print(f'hill-climb stopped after {sweep + 1} sweeps', file=sys.stderr)

# ---------- insets: families flagged `inset: true` leave the network and sit in a framed box below it,
# as a compact snake (like a depot or an airport branch drawn in a corner of a real map)
INSET_LINES = [t for f in (M.fams_raw or {}).get('families', []) if f.get('inset') for t in M.fam_lines.get(f['name'], [])]
INSET_IDX = []
if INSET_LINES:
    rest = np.array([P[k] for k in range(N) if ops[k]['tag'] not in INSET_LINES])
    ROWN, STEPX, ROWY = 12, 3, 5
    need_w = ROWN * STEPX + 8
    need_h = sum((len(M.lines[t]) - 1) // ROWN + 1 for t in INSET_LINES) * ROWY + 10
    bx0, by0 = rest.min(0); bx1, by1 = rest.max(0)
    spot = None
    # lowest empty rectangle inside the map's bounds (labels need ~4 cells of clearance)
    for y in range(int(by1 - need_h), int(by0), -1):
        for x in range(int(bx0), int(bx1 - need_w) + 1):
            inside = ((rest[:, 0] > x - 4) & (rest[:, 0] < x + need_w + 4) &
                      (rest[:, 1] > y - 6) & (rest[:, 1] < y + need_h + 3))
            if not inside.any():
                spot = (x + 3, y + 6); break
        if spot:
            break
    x0, y0 = spot if spot else (bx0 + 2, by1 + 9)
    cy = y0
    for t in INSET_LINES:
        st = M.lines[t]
        for k, o in enumerate(st):
            r, c = divmod(k, ROWN)
            cc = c if r % 2 == 0 else ROWN - 1 - c
            P[idx[o['id']]] = (x0 + cc * STEPX, cy + r * ROWY)
            INSET_IDX.append(idx[o['id']])
        cy += ((len(st) - 1) // ROWN + 2) * ROWY

# ---------- strays: a stop left far from every other stop (a one-stop line with nothing to pull it
# in, e.g. Azure's lone "List operations") would stretch the canvas; dock it beside the network
from scipy.spatial import cKDTree as _KD
for _ in range(3):
    _d, _ = _KD(P.astype(float)).query(P.astype(float), k=2) if N > 2 else (np.zeros((N, 2)), None)
    strays = [i for i in range(N) if _d[i][1] > 10 and i not in INSET_IDX]
    if not strays:
        break
    for i in strays:
        rest = np.array([P[k] for k in range(N) if k != i], dtype=float)
        tgt = rest[np.argmin(((rest - P[i]) ** 2).sum(1))]
        best = None
        for r in range(3, 12):
            for dx in range(-r, r + 1):
                for dy in (-r, r) if abs(dx) != r else range(-r, r + 1):
                    c = tgt + (dx, dy)
                    if ((rest - c) ** 2).sum(1).min() >= 9:
                        best = c; break
                if best is not None: break
            if best is not None: break
        if best is not None:
            P[i] = best
print('stray-stop docking done', file=sys.stderr)

# ---------- render ----------
# px per cell: 42 normally; a crowded core is drawn larger so labels (fixed font size) get room
from scipy.spatial import cKDTree as _KD
_crowd = max(len(n_) for n_ in _KD(P.astype(float)).query_ball_point(P.astype(float), 3.0)) if N else 1
U = int(min(60, max(42, 42 * math.sqrt(_crowd / 7.0)))) if getattr(M, 'weave', False) else 42
U = max(U, args.umin)
MARGIN, TOPH, SIDEBAR = 160, 170, 540
mn = P.min(0)
S = (P - mn) * U + np.array([MARGIN, TOPH + 60])
Wpx = int(S[:, 0].max() + MARGIN + SIDEBAR)
Hpx = int(max(S[:, 1].max() + MARGIN, TOPH + 520 + 19 * len(M.routes)))

def octi_path(a, b):
    """two-segment octilinear connection: diagonal first, then straight"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 or dy == 0 or abs(dx) == abs(dy):
        return [b]
    d = min(abs(dx), abs(dy))
    mid = (a[0] + math.copysign(d, dx), a[1] + math.copysign(d, dy))
    return [mid, b]

def rounded(pts, r=14):
    """polyline with rounded corners"""
    if len(pts) < 3:
        return 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in pts)
    d = f'M{pts[0][0]:.1f},{pts[0][1]:.1f}'
    for k in range(1, len(pts) - 1):
        p0, p1, p2 = np.array(pts[k - 1]), np.array(pts[k]), np.array(pts[k + 1])
        v1, v2 = p1 - p0, p2 - p1
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 == 0 or n2 == 0 or abs(v1[0] * v2[1] - v1[1] * v2[0]) < 1e-6:
            d += f' L{p1[0]:.1f},{p1[1]:.1f}'; continue
        rr = min(r, n1 / 2, n2 / 2)
        a = p1 - v1 / n1 * rr; b = p1 + v2 / n2 * rr
        d += f' L{a[0]:.1f},{a[1]:.1f} Q{p1[0]:.1f},{p1[1]:.1f} {b[0]:.1f},{b[1]:.1f}'
    d += f' L{pts[-1][0]:.1f},{pts[-1][1]:.1f}'
    return d

E_ = html.escape
out = []; A = out.append
A(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {Wpx} {Hpx}" width="{Wpx}" height="{Hpx}" font-family="Helvetica Neue, Helvetica, Arial, sans-serif">')
A('''<style>
.st text{font-size:11px;fill:#1A1F2C}
.st:hover text{font-weight:700}
.xfer{fill:none;stroke:#1A1F2C;stroke-width:1.4;stroke-dasharray:2 3;opacity:.5}
.dim{opacity:.1}
.rt{cursor:pointer}.rt:hover text{font-weight:700}
</style>''')
A(f'<rect width="{Wpx}" height="{Hpx}" fill="#FFFFFF"/>')
_title = (M.fams_raw.get('title') or f'The {M.slug.title()} API · Transit Map') + (f' · Sheet {args.sheet}: {SHEET["title"]}' if SHEET else '')
A(f'<text x="{MARGIN}" y="80" font-size="44" font-weight="700" fill="#1A1F2C">{E_(_title)}</text>')
_sub = (M.fams_raw.get('subtitle') or '{N} operations as stops · {L} tags as lines · __NXFER__ interchanges · '
        '{R} express routes (Arazzo) · click any stop for its apis.io page')
A(f'<text x="{MARGIN}" y="116" font-size="16" fill="#5A6275">' + E_(_sub).replace('{N}', str(N)).replace('{L}', str(len(M.lines))).replace('{R}', str(len(M.routes))) + '</text>')

if INSET_IDX:
    q = S[INSET_IDX]
    fx0, fy0, fx1, fy1 = q[:, 0].min() - 90, q[:, 1].min() - 150, q[:, 0].max() + 90, q[:, 1].max() + 90
    fam_inset = [f for f in M.fams_raw['families'] if f.get('inset')][0]
    A(f'<rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" rx="14" fill="#F6F7F9" stroke="#C9CED8" stroke-width="1.5" stroke-dasharray="6 5"/>')
    A(f'<text x="{fx0 + 22}" y="{fy0 + 32}" font-size="17" font-weight="700" letter-spacing="2" fill="#5A6275">{E_(fam_inset["name"].upper())}</text>'
      f'<text x="{fx0 + 22}" y="{fy0 + 52}" font-size="12" fill="#8A93A6">{E_(fam_inset.get("inset_note", "Inset"))}</text>')

# line geometry
line_polys = {}
for t, st in M.lines.items():
    polys = []
    for ch in M.chains[t]:
        pts = [tuple(S[idx[ch[0]['id']]])]
        for a, b in zip(ch, ch[1:]):
            pts += octi_path(tuple(S[idx[a['id']]]), tuple(S[idx[b['id']]]))
        polys.append(pts)
    if t in CIRCLE:
        polys[0] += octi_path(tuple(S[idx[st[-1]['id']]]), tuple(S[idx[st[0]['id']]]))
    line_polys[t] = polys
line_pts = {t: ps[0] for t, ps in line_polys.items()}     # trunk, for code that wants one polyline
all_polys = [pl for ps in line_polys.values() for pl in ps]

# intercity links (district mode): one connector per pair of products, drawn between the closest
# pair of stops that actually interchange, labelled with how many interchanges join the two
if DISTRICTS:
    pair_best = {}
    pair_n = collections.Counter()
    for i, j in render_xfers:
        f, g = fam_of_line[ops[i]['tag']], fam_of_line[ops[j]['tag']]
        if f == g:
            continue
        key = frozenset((f, g)); pair_n[key] += 1
        d_ = np.hypot(*(S[i] - S[j]))
        if key not in pair_best or d_ < pair_best[key][0]:
            pair_best[key] = (d_, i, j)
    for key, (d_, i, j) in pair_best.items():
        a_, b_ = S[i], S[j]
        pts = [tuple(a_)] + octi_path(tuple(a_), tuple(b_))
        A(f'<path d="{rounded(pts, 40)}" fill="none" stroke="#DCE0E7" stroke-width="4" stroke-dasharray="12 10" stroke-linecap="round"/>')
        mx, my = (a_ + b_) / 2
        f, g = sorted(key)
        A(f'<g><rect x="{mx - 62}" y="{my - 12}" width="124" height="24" rx="12" fill="#fff" stroke="#C3C8D2" stroke-width="1.5"/>'
          f'<text x="{mx}" y="{my + 4}" text-anchor="middle" font-size="11" fill="#5A6275"><title>{E_(f)} ⇄ {E_(g)}</title>'
          f'{pair_n[key]} interchange{"s" if pair_n[key] > 1 else ""}</text></g>')
    # district names, set above each district like a neighbourhood label
    for f in fam_names:
        ks = [k for k in range(N) if fam_of_line[ops[k]['tag']] == f and k not in INSET_IDX]
        if not ks:
            continue
        q = S[ks]; cx = q[:, 0].mean(); top = q[:, 1].min() - 70
        A(f'<text x="{cx}" y="{top}" text-anchor="middle" font-size="30" font-weight="700" letter-spacing="4" '
          f'fill="{M.fam_by_name[DIST_FAM[f]]["color"]}" opacity=".85">{E_(f.upper())}</text>')

# interchange passages (under lines); a far interchange becomes 'change for <line>' text instead
change_for = collections.defaultdict(list)
# one interchange per pair of lines, at the stop nearest the other line's home (real maps mark it once)
best_pair = {}
for i, j in render_xfers:
    key = (ops[i]['tag'], ops[j]['tag'])
    d = np.hypot(*(S[i] - S[j]))
    if key not in best_pair or d < best_pair[key][0]:
        best_pair[key] = (d, i, j)
kept_xfers = [(i, j) for _, i, j in best_pair.values()]
page_of = {}
for i, o in enumerate(ops):
    for t_, sh in getattr(M, 'offsheet', {}).get(o['id'], []):
        if t_ not in change_for[i]:
            change_for[i].append(t_); page_of[t_] = sh
def ccode(t):
    return M.codes[t] + (f' p.{page_of[t]}' if t in page_of and page_of[t] else '')
for i, j in kept_xfers:
    a, b = S[i], S[j]
    if np.hypot(*(a - b)) <= U * 1.5:
        A(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="#1A1F2C" stroke-width="11" stroke-linecap="round"/>'
          f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="#fff" stroke-width="5" stroke-linecap="round"/>')
    else:
        change_for[i].append(ops[j]['tag'])

for t, pts in [(t, pl) for t, ps in line_polys.items() for pl in ps]:
    dash = ' stroke-dasharray="12 7"' if t.lower().startswith('test') else ''
    A(f'<path class="line" data-line="{E_(t)}" d="{rounded(pts)}" fill="none" stroke="{M.color[t]}" stroke-width="{10 if t in CIRCLE else 4 if t in M.local_lines else 7}" '
      f'stroke-linecap="round" stroke-linejoin="round"{dash}><title>{E_(t)}</title></path>')

# line badges at both termini, pushed outward along the end segment
badges = []
seg_A0 = np.array([p for pl in all_polys for p in pl[:-1]], float)
seg_B0 = np.array([p for pl in all_polys for p in pl[1:]], float)
for t, st in M.lines.items():
    pts = line_pts[t]
    if t in CIRCLE:                              # one badge, on the outside of the ring's top stop
        top = min(pts, key=lambda p: p[1]); c = np.array(top) + np.array([0, -40])
        badges.append((c, t))
        A(f'<rect x="{c[0] - 30}" y="{c[1] - 13}" width="60" height="26" rx="13" fill="{M.color[t]}"/>'
          f'<text x="{c[0]}" y="{c[1] + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#fff">{E_(M.codes[t])}</text>')
        continue
    ends = [(pts[0], pts[1] if len(pts) > 1 else pts[0]), (pts[-1], pts[-2] if len(pts) > 1 else pts[-1])]
    for bp in line_polys[t][1:]:                 # branch ends get a terminus bar, not a badge
        if len(bp) > 1:
            e, pv = np.array(bp[-1]), np.array(bp[-2]); v = e - pv; v = v / (np.linalg.norm(v) + 1e-9)
            q = np.array([-v[1], v[0]]) * 9
            A(f'<line x1="{e[0] - q[0]}" y1="{e[1] - q[1]}" x2="{e[0] + q[0]}" y2="{e[1] + q[1]}" stroke="{M.color[t]}" stroke-width="6" stroke-linecap="round"/>')
    for end, prev in ends:
        v = np.array(end) - np.array(prev); n = np.linalg.norm(v)
        v = v / n if n else np.array([1.0, 0])
        perp = np.array([-v[1], v[0]])
        best = None
        for k, dv in enumerate([v * 34, v * 52, v * 72, v * 34 + perp * 26, v * 34 - perp * 26, perp * 30, -perp * 30]):
            c = np.array(end) + dv
            bb = (c[0] - 28, c[1] - 14, c[0] + 28, c[1] + 14)
            near = np.hypot(*(S - c).T)
            cost = 50 * int((near < 26).sum()) + 30 * int((seg_dist(c, seg_A0, seg_B0) < 16).sum() > 1) \
                + 80 * sum(1 for q, _ in badges if abs(q[0] - c[0]) < 58 and abs(q[1] - c[1]) < 28) + k * 2
            if best is None or cost < best[0]:
                best = (cost, c)
        c = best[1]
        badges.append((c, t))
        A(f'<line x1="{end[0]}" y1="{end[1]}" x2="{c[0]}" y2="{c[1]}" stroke="{M.color[t]}" stroke-width="7"/>'
          f'<rect x="{c[0] - 24}" y="{c[1] - 11}" width="48" height="22" rx="5" fill="{M.color[t]}"><title>{E_(t)}</title></rect>'
          f'<text x="{c[0]}" y="{c[1] + 5}" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">{E_(M.codes[t])}</text>')

# ---------- labels ----------
CH = 6.1                                         # px per char at 11px
# Labels are scored, not first-fit. A rotated label is a chain of small boxes along its diagonal,
# so it only blocks the space it really covers.
seg_A = np.array([p for pl in all_polys for p in pl[:-1]], float)
seg_B = np.array([p for pl in all_polys for p in pl[1:]], float)
lab_boxes = np.zeros((0, 4))                       # placed label boxes
badge_boxes = np.array([(c[0] - 27, c[1] - 13, c[0] + 27, c[1] + 13) for c, _ in badges]).reshape(-1, 4)
stn_boxes = np.array([(x - 8, y - 8, x + 8, y + 8) for x, y in S])

def hits(boxes, B):
    if len(B) == 0:
        return 0
    n = 0
    for b in boxes:
        n += int(((b[2] > B[:, 0]) & (b[0] < B[:, 2]) & (b[3] > B[:, 1]) & (b[1] < B[:, 3])).sum())
    return n

def track_hits(boxes):
    n = 0
    for b in boxes:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        for px in np.linspace(b[0] + 1, b[2] - 1, max(2, int((b[2] - b[0]) / 10))):
            for py in (b[1] + 2, cy, b[3] - 2):
                n += int((seg_dist(np.array([px, py]), seg_A, seg_B) < 5).any())
    return n

def local_dir(i):
    js = [j for j, _ in nbr_line[i]]
    if not js:
        return np.array([1.0, 0])
    v = sum((S[j] - S[i]) / (np.linalg.norm(S[j] - S[i]) + 1e-9) * (1 if k == 0 else -1) for k, j in enumerate(js[:2]))
    n = np.linalg.norm(v)
    return v / n if n else np.array([1.0, 0])

def cand_boxes(kind, x, y, w):
    """boxes covering a label of width w placed by `kind` around stop (x, y)"""
    H = 13
    if kind.startswith('rot'):
        k = max(1, int(w * .7 / 9))
        if kind == 'rot-up':
            return [(x + 4 + j * 9 * w * .7 / (k * 9), y - 12 - (j + 1) * 9 * w * .7 / (k * 9), x + 4 + (j + 1) * 9 * w * .7 / (k * 9) + 4,
                     y - 12 - j * 9 * w * .7 / (k * 9)) for j in range(k)]
        return [(x - 4 - (j + 1) * 9 * w * .7 / (k * 9) - 4, y + 12 + j * 9 * w * .7 / (k * 9), x - 4 - j * 9 * w * .7 / (k * 9),
                 y + 12 + (j + 1) * 9 * w * .7 / (k * 9)) for j in range(k)]
    if kind.endswith('w'):                        # two-line label, w = width of the longer line
        o2 = {'rw': (12, -12, 'start'), 'lw': (-12, -12, 'end'), 'uw': (0, -38, 'mid'), 'dw': (0, 12, 'mid')}[kind]
        dx, dy, anc = o2
        x0 = x + dx if anc == 'start' else x + dx - w if anc == 'end' else x - w / 2
        return [(x0, y + dy, x0 + w, y + dy + 26)]
    off = {'r': (12, -6, 'start'), 'l': (-12, -6, 'end'), 'u': (0, -25, 'mid'), 'd': (0, 12, 'mid'),
           'ur': (9, -21, 'start'), 'dr': (9, 8, 'start'), 'ul': (-9, -21, 'end'), 'dl': (-9, 8, 'end'),
           'r2': (22, -6, 'start'), 'l2': (-22, -6, 'end'), 'u2': (0, -38, 'mid'), 'd2': (0, 25, 'mid')}[kind]
    dx, dy, anc = off
    x0 = x + dx if anc == 'start' else x + dx - w if anc == 'end' else x - w / 2
    return [(x0, y + dy, x0 + w, y + dy + H)]

def split2(name, codes_txt):
    words = name.split(' ')
    if len(name) + len(codes_txt) <= 14 or len(words) < 2:
        return None
    best_k = min(range(1, len(words)), key=lambda k: abs(len(' '.join(words[:k])) - len(' '.join(words[k:]) + codes_txt)))
    return ' '.join(words[:best_k]), ' '.join(words[best_k:])

labels = {}
deg_s = collections.Counter(i for e in kept_xfers for i in e)
# hardest first: stops in crowded places, then long names
crowd = np.array([(np.hypot(*(S - S[i]).T) < 90).sum() for i in range(N)])
order = sorted(range(N), key=lambda i: (-crowd[i], -len(M.label(ops[i]))))
for i in order:
    x, y = S[i]
    txt = M.label(ops[i]) + ''.join(' ⇄' + ccode(t) for t in sorted(set(change_for[i])))
    w = len(txt) * CH
    d = local_dir(i); horiz = abs(d[1]) < 0.3; vert = abs(d[0]) < 0.3
    codes_txt = ''.join(' ⇄' + ccode(t) for t in sorted(set(change_for[i])))
    sp = split2(M.label(ops[i]), codes_txt)
    w_wrap = max(len(sp[0]), len(sp[1] + codes_txt)) * CH if sp else w
    kinds = (['rot-up', 'rot-dn', 'u', 'd', 'ur', 'dr', 'ul', 'dl', 'r', 'l', 'u2', 'd2', 'r2', 'l2'] if horiz else
             ['r', 'l', 'ur', 'dr', 'ul', 'dl', 'r2', 'l2', 'u', 'd', 'rot-up', 'rot-dn'] if vert else
             ['r', 'l', 'ur', 'dl', 'ul', 'dr', 'u', 'd', 'r2', 'l2', 'rot-up', 'rot-dn', 'u2', 'd2'])
    if sp:
        kinds = kinds[:4] + (['uw', 'dw', 'rw', 'lw'] if horiz else ['rw', 'lw', 'uw', 'dw']) + kinds[4:]
    best = None
    own = np.ones(N, bool); own[i] = False
    for rank, k in enumerate(kinds):
        bx = cand_boxes(k, x, y, w_wrap if k.endswith('w') else w)
        cost = (100 * hits(bx, lab_boxes) + 60 * hits(bx, badge_boxes) + 40 * hits(bx, stn_boxes[own])
                + 18 * track_hits(bx) + rank * 1.5)
        if best is None or cost < best[0]:
            best = (cost, k, bx)
        if cost < 10:
            break
    labels[i] = (best[1], best[2])
    lab_boxes = np.vstack([lab_boxes, np.array(best[2])])

# ---------- stations ----------
inter = {i for e in kept_xfers for i in e}
ridx = collections.defaultdict(list)
for r in M.routes:
    for s in r['stops']:
        ridx[idx[s]].append(r['code'])
for i, o in enumerate(ops):
    x, y = S[i]; t = o['tag']; c = M.color[t]
    url = f'https://apis.apis.io/apis/{M.slug}/{o["api"]}/#op-{o["opid"]}'
    if o['raw'].get('x-url'):                             # a stop that links somewhere other than apis.io
        url = o['raw']['x-url']
    tip = f'{o["m"].upper()} {o["p"]}\n{t} line · {o["opid"]}' + (f'\nExpress: {", ".join(ridx[i])}' if i in ridx else '') + ('\nDEPRECATED' if o['dep'] else '')
    if o['raw'].get('x-tip'):
        tip = o['raw']['x-tip']
    info = (' data-info="' + E_(json.dumps(o['raw']['x-info'])) + '"') if o['raw'].get('x-info') else ''
    A(f'<a href="{E_(url)}" target="_blank" class="st" data-op="{E_(o["id"])}"{info}><title>{E_(tip)}</title>')
    if i in inter:
        A(f'<circle cx="{x}" cy="{y}" r="8.5" fill="#fff" stroke="#1A1F2C" stroke-width="3"/>')
    elif o['dep']:
        A(f'<circle cx="{x}" cy="{y}" r="6" fill="#fff" stroke="#A0A7B4" stroke-width="2" stroke-dasharray="2 2"/>')
    else:
        fill = c if o['m'] in ('post', 'put', 'patch') else '#fff'
        A(f'<circle cx="{x}" cy="{y}" r="5.5" fill="{fill}" stroke="{c}" stroke-width="2.5"/>')
        if o['m'] == 'delete':
            A(f'<line x1="{x - 4}" y1="{y + 4}" x2="{x + 4}" y2="{y - 4}" stroke="{c}" stroke-width="2"/>')
    if i in ridx:
        A(f'<rect x="{x - 3}" y="{y - 3}" width="6" height="6" transform="rotate(45 {x} {y})" fill="#1A1F2C"/>')
    name, b = labels[i]; col = '#A0A7B4' if o['dep'] else '#1A1F2C'
    txt = E_(M.label(o)) + ''.join(f' <tspan font-weight="700" style="fill:{M.color[t]}">⇄{E_(ccode(t))}</tspan>' for t in sorted(set(change_for[i])))
    b = (min(q[0] for q in b), min(q[1] for q in b), max(q[2] for q in b), max(q[3] for q in b))
    if name.endswith('w'):
        sp = split2(M.label(o), ''.join(' ⇄' + ccode(t) for t in sorted(set(change_for[i]))))
        anchor = 'start' if name == 'rw' else 'end' if name == 'lw' else 'middle'
        tx = b[0] if anchor == 'start' else b[2] if anchor == 'end' else x
        codes = ''.join(f' <tspan font-weight="700" style="fill:{M.color[t]}">⇄{E_(ccode(t))}</tspan>' for t in sorted(set(change_for[i])))
        A(f'<text x="{tx}" y="{b[1] + 10}" text-anchor="{anchor}" style="fill:{col}">{E_(sp[0])}'
          f'<tspan x="{tx}" dy="13">{E_(sp[1])}{codes}</tspan></text>')
    elif name == 'rot-up':
        A(f'<text x="{x + 6}" y="{y - 12}" transform="rotate(-45 {x + 6} {y - 12})" style="fill:{col}">{txt}</text>')
    elif name == 'rot-dn':
        A(f'<text x="{x - 6}" y="{y + 12}" text-anchor="end" transform="rotate(-45 {x - 6} {y + 12})" style="fill:{col}">{txt}</text>')
    else:
        anchor = 'start' if name in ('r', 'ur', 'dr', 'r2') else 'end' if name in ('l', 'ul', 'dl', 'l2') else 'middle'
        tx = b[0] if anchor == 'start' else b[2] if anchor == 'end' else x
        A(f'<text x="{tx}" y="{b[3] - 2}" text-anchor="{anchor}" style="fill:{col}">{txt}</text>')
    A('</a>')

# ---------- sidebar ----------
sx, sy = Wpx - SIDEBAR + 30, TOPH + 40
A(f'<text x="{sx}" y="{sy}" font-size="16" font-weight="700" letter-spacing="2" fill="#1A1F2C">LINES</text>')
yy = sy + 16
for f in fam_names_orig:
    yy += 26
    A(f'<text x="{sx}" y="{yy}" font-size="12" font-weight="700" letter-spacing="1.5" fill="{M.fam_by_name[f]["color"]}">{E_(f.upper())}</text>')
    for t in M.fam_lines[f]:
        yy += 17
        A(f'<rect x="{sx}" y="{yy - 10}" width="34" height="13" rx="3" fill="{M.color[t]}"/><text x="{sx + 17}" y="{yy}" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">{E_(M.codes[t])}</text>'
          f'<text x="{sx + 42}" y="{yy}" font-size="11" fill="#1A1F2C">{E_(t)} <tspan fill="#8A93A6">{len(M.lines[t])}</tspan></text>')
legend_bottom = yy
sx2 = sx + 250; yy = sy
A(f'<text x="{sx2}" y="{yy}" font-size="16" font-weight="700" letter-spacing="2" fill="#1A1F2C">KEY</text>')
key = [('<circle r="5.5" fill="#fff" stroke="#1A1F2C" stroke-width="2.5"/>', 'GET — read'),
       ('<circle r="5.5" fill="#1A1F2C" stroke="#1A1F2C" stroke-width="2.5"/>', 'POST — write'),
       ('<g><circle r="5.5" fill="#fff" stroke="#1A1F2C" stroke-width="2.5"/><line x1="-4" y1="4" x2="4" y2="-4" stroke="#1A1F2C" stroke-width="2"/></g>', 'DELETE'),
       ('<g><line x1="-10" x2="10" stroke="#1A1F2C" stroke-width="11" stroke-linecap="round"/><line x1="-10" x2="10" stroke="#fff" stroke-width="5" stroke-linecap="round"/><circle cx="-10" r="7" fill="#fff" stroke="#1A1F2C" stroke-width="3"/><circle cx="10" r="7" fill="#fff" stroke="#1A1F2C" stroke-width="3"/></g>', 'Interchange'),
       ('<text x="-14" y="4" font-size="11" font-weight="700" fill="#E07B12">⇄ACC</text>', 'Change for another line'),
       ('<rect x="-3" y="-3" width="6" height="6" transform="rotate(45)" fill="#1A1F2C"/>', 'Express stop'),
       ('<circle r="6" fill="#fff" stroke="#A0A7B4" stroke-width="2" stroke-dasharray="2 2"/>', 'Closed (deprecated)'),
       ('<line x1="-14" x2="14" stroke="#8A93A6" stroke-width="7" stroke-dasharray="8 5"/>', 'Depot (test mode)')]
for k, (g, txt) in enumerate(key):
    yy = sy + 30 + k * 24
    A(f'<g transform="translate({sx2 + 18},{yy})">{g}</g><text x="{sx2 + 44}" y="{yy + 4}" font-size="12" fill="#1A1F2C">{E_(txt)}</text>')
if M.retagged:
    yy += 40
    A(f'<text x="{sx2}" y="{yy}" font-size="11" font-weight="700" fill="#5A6275">MERGED TAGS (spec-split artefacts)</text>')
    for (a, b), n in sorted(M.retagged.items()):
        yy += 15
        A(f'<text x="{sx2}" y="{yy}" font-size="10.5" fill="#5A6275">“{E_(a)}” → {E_(b)} line · {n} op{"s" if n > 1 else ""}</text>')
yy += 50
A(f'<text x="{sx2}" y="{yy}" font-size="16" font-weight="700" letter-spacing="2" fill="#1A1F2C">{E_(M.fams_raw.get('express_title', 'EXPRESS'))}</text>')
A(f'<text x="{sx2}" y="{yy + 17}" font-size="10.5" fill="#5A6275">{E_(M.fams_raw.get('express_sub', 'Arazzo workflows · hover to trace'))}</text>')
for k, r in enumerate(M.routes):
    ry = yy + 40 + k * 19
    nm = re.sub(r'^' + re.escape(M.slug.title()) + r' ', '', r['name'])[:int(M.fams_raw.get('express_chars', 30))]
    A(f'<g class="rt" data-stops="{E_(" ".join(r["stops"]))}"><rect x="{sx2}" y="{ry - 11}" width="30" height="15" rx="3" fill="#1A1F2C"/>'
      f'<text x="{sx2 + 15}" y="{ry}" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">{r["code"]}</text>'
      f'<text x="{sx2 + 37}" y="{ry}" font-size="11" fill="#1A1F2C">{E_(nm)}</text></g>')
A(f'<text x="{MARGIN}" y="{Hpx - 30}" font-size="12" fill="#5A6275">{E_(M.fams_raw.get('source') or f'Source: s3://api-evangelist-profiles/providers/{M.slug}/ openapi + arazzo · api-transit-maps/build_network_map.py · every stop links to apis.io')}</text>')
A('''<script><![CDATA[
(function(){var svg=document.documentElement;function all(s){return [].slice.call(svg.querySelectorAll(s));}
function clear(){all('.dim').forEach(function(e){e.classList.remove('dim')});}
all('.rt').forEach(function(rt){rt.addEventListener('mouseenter',function(){var s=rt.getAttribute('data-stops').split(' ');
all('.st').forEach(function(e){if(s.indexOf(e.getAttribute('data-op'))<0)e.classList.add('dim');});
all('.line').forEach(function(e){e.classList.add('dim');});});rt.addEventListener('mouseleave',clear);});})();
]]></script></svg>''')

os.makedirs(args.out, exist_ok=True)
fn = os.path.join(args.out, (f'{M.slug}-atlas-{args.sheet:02d}{args.tag}.svg' if SHEET else f'{M.slug}-network-map{args.tag}.svg'))
open(fn, 'w').write('\n'.join(out).replace('__NXFER__', str(len(kept_xfers))))
# quality metrics, so iterations are measured not eyeballed
octi = sum(1 for i, j, _ in line_edges if oct_dev(P[j] - P[i]) < 1e-6) / len(line_edges)
bends = collections.Counter()
for t, st in [(t, ch) for t in M.lines for ch in M.chains[t]]:
    for a, b, c in zip(st, st[1:], st[2:]):
        u, v, w = P[idx[a['id']]], P[idx[b['id']]], P[idx[c['id']]]
        x1, x2 = v - u, w - v
        if np.hypot(*x1) and np.hypot(*x2):
            ang = round(math.degrees(math.acos(max(-1, min(1, (x1 @ x2) / (np.hypot(*x1) * np.hypot(*x2)))))) / 45) * 45
            bends[ang] += 1
adj = sum(1 for i, j in xfer_edges if np.hypot(*(P[i] - P[j])) <= 1.5) / max(1, len(xfer_edges))
lab_fail = sum(1 for i, (n, b) in labels.items() if track_hits(b))
lab_overlap = sum(1 for i, (n, b) in labels.items()) - 0
ncross = 0
for k, (i, j, _) in enumerate(line_edges):
    m2 = np.array([(a not in (i, j) and b not in (i, j)) for a, b, _ in line_edges]); m2[:k + 1] = False
    ncross += crossings(P[i].astype(float), P[j].astype(float), P[E[m2, 0]].astype(float), P[E[m2, 1]].astype(float))
metrics = dict(size=[Wpx, Hpx], crossings=ncross, octilinear_edges=round(octi, 3), turns=dict(sorted(bends.items())),
               interchanges_adjacent=round(adj, 3), labels_on_track=lab_fail)
json.dump(metrics, open(fn.replace('.svg', '.json'), 'w'), indent=1)
print(fn, metrics)

# crowded sheet: labels are a fixed font size, so a sheet whose labels keep landing on track is
# redrawn at a larger scale (same layout, seed is fixed) — 42-60 px/cell -> 80 -> 100. Optimizely
# atlas p.4: 117 labels on track at the adaptive scale, 56 at 80, 21 at 100.
if lab_fail > 40 and args.umin < 100:
    nxt = 80 if args.umin < 80 else 100
    print(f'{lab_fail} labels on track: redrawing at {nxt} px/cell', file=sys.stderr)
    argv = [a for a in sys.argv if not a.startswith('--umin')]
    os.execv(sys.executable, [sys.executable] + argv + [f'--umin={nxt}'])
