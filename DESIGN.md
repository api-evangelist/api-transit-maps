# API Transit Maps — design notes

An OpenAPI rendered as a metro map: every **operation is a stop**, every **tag is a line**.
Rules below are taken from the world corpus in `~/Downloads/subway-maps/` (Berlin, Tokyo,
Shanghai, London were the reference set) and say which API fact each one encodes.

| Transit convention (where seen) | API meaning |
|---|---|
| Line = one colour, one name, badge at both termini (Berlin `U6`) | Tag, with a short code badge |
| Lines grouped by operator / colour family (Tokyo Metro vs Toei) | Product family (Payments, Billing, Connect…) |
| Station order follows the street, not the alphabet | Lifecycle order: list → create → retrieve → update → delete, then nested resources |
| Long line snakes with rounded hairpins (Seoul Line 2, Shanghai L11) | Lines with more than 22 operations wrap |
| Interchange = white circle with black ring (London, Berlin) | Operation whose path reaches into another line's resource (`/customers/{id}/subscriptions`) |
| Out-of-station interchange, drawn as a dotted passage (London Bank–Monument) | Link from that stop to the other line's home stop (the resource's retrieve operation) |
| Through-service / express pattern (Tokyo "Thru to…", RER) | Arazzo workflow: a named route that stops only at the operations it calls |
| Closed / not-in-service station, hollow and grey | `deprecated: true` |
| Depot / non-revenue track, dashed | Test Helpers: operations that exist only in test mode |
| Station symbol varies by service (London ♿, ⇌ rail) | HTTP method: GET hollow, POST filled, DELETE crossed |
| Octilinear geometry, rounded corners, generous label space | Same; labels set at 45° like the 2026-05 Stripe map |

Every stop is a link to `https://apis.apis.io/apis/<provider>/<api>/#op-<operationId>`.

Generator: `build_transit_map.py <provider-dir> <out-dir>`.

## Network layout (`build_network_map.py`)

Pipeline, each step fixing what the one before could not:

1. **Model** (`transit_model.py`): stops, lines, families, colours. `retag` in the families file
   re-homes split-artefact tags (`Create`, `Lists`, `Get`…) and the key lists every merge.
2. **Interchanges**: from paths (`/customers/{id}/subscriptions`) and from request fields named
   after another line's resource (`customer`, `payment_method`, `price`). One per pair of lines.
3. **Skeleton**: the `circle:` line is pinned to an octagon at the centre; each family's anchor is
   pinned outside it at the bearing of the ring stops it connects to.
4. **Stress majorisation** places everything else. Only *path* interchanges pull; parameter
   interchanges are drawn, not pulled (pulling all 80 collapsed the map — v10).
5. **Relaxation**: springs + personal-space repulsion open dense hubs before snapping.
6. **Grid hill-climb**: octilinear edges, straight running, bend penalties, clearance from
   other track, crossing penalty.
7. **Insets**: families with `inset: true` (Test Depot) leave the network for a framed box.
8. **Labels**: scored placement (labels, badges, stations, track), 45° on horizontal track,
   two-line wrapping for long names, "⇄CODE" change-for marks where an interchange is far.

Conventions added during iteration: circle line (London Circle / Moscow Koltsevaya); far
interchanges as "change for" text, not passages; one interchange mark per line pair; hue spread
within a family so neighbouring lines are distinguishable; insets for non-revenue track.

### Stripe iteration log (metrics written next to every SVG)

| v | change | crossings | octilinear | labels on track |
|---|---|---|---|---|
| 1 | stress + hill-climb | – | 99.8% | 168 |
| 3 | relaxation opens hubs | 23 | 92.8% | 88 |
| 4 | retag artefacts, change-for text | 17 | 95.2% | 88 |
| 6 | Customers as pinned octagon ring, districts by bearing | 17 | 93.8% | 59 |
| 8 | scored label placement | 17 | 93.8% | 4* |
| 9 | integer ring, badge search | 14 | 96.7% | 30 |
| 10 | 80 param interchanges pulling — collapsed | 61 | 89.0% | 112 |
| 11 | param interchanges drawn, not pulled | 15 | 95.6% | 55 |
| 15 | two-line label wrapping | 15 | 95.6% | 45 |
| 16 | scale 34 → 42 px/cell | 15 | 95.6% | 17 |

*v8's metric under-sampled; v9 onward samples the whole label box.

## Twilio: what a federated provider needed (2026-11-14)

Stripe is one product with a backbone; Twilio is ~14 products on their own hosts. New, all opt-in
from the families file so the Stripe map is unchanged:

- **`hosts:` per family** — a line joins the family whose hosts serve most of its operations.
- **`split_by_host:`** — a tag spanning products (Twilio's `Services`, 265 ops on 10 hosts) becomes
  one line per product (Verify Services, Sync Services…).
- **Unique internal ids** — operationIds repeat across specs (56 in Twilio); `opid` keeps the real
  one for the apis.io anchor.
- **District layout** (any map without a `circle:`): each family laid out alone, districts placed by
  a stress layout of the *product graph* (products that share interchanges sit together), pushed
  apart, then gravity-compacted. `center: auto` picks the product with most cross links (Messaging).
- **Intercity links** — one light dashed connector per product pair with its interchange count.
- **District names** set above each district.
- **`branching: true`** — a sub-resource tree of ≥ `branch_min` (6) stops leaves its parent's item
  stop as a branch, ≤ `branch_max` (3) per junction; branch ends get a terminus bar, not a badge.
- **Badge codes** from candidates (initials → first letters → consonants), big lines claim first;
  no more `CON6`.

| v | change | crossings | octilinear | labels on track | size |
|---|---|---|---|---|---|
| 1 | Stripe pipeline as-is | 127 | 87.1% | 111 | 7.1k×5.3k |
| 2 | per-family district layouts | 45 | 86.2% | 16 | 17k×15k |
| 3 | intra-family pulls only, gravity compaction | 8 | 99.1% | 11 | 12.1k×10.5k |
| 4 | districts placed by product graph, intercity links | 11 | 98.1% | 10 | 11.1k×11.6k |
| 5 | every sub-collection a branch — starbursts | 12 | 92.1% | 33 | 7.8k×8.2k |
| 6 | branch only subtrees ≥ 6 stops, ≤ 3 per junction | 10 | 98.5% | 8 | 10.7k×10.4k |
| 7 | badge codes, lighter connectors | 10 | 98.5% | 7 | 10.7k×10.4k |

## Ten exemplar providers: what the generator learned (2026-11-14)

Goal: no hand-written families file. Anthropic, MessageBird, OpenAI, Shopify, Salesforce, Plaid,
Xero, HubSpot and GitHub are built with **no families file at all**; Cloudflare is an atlas.

**Reading the API**
- One path cleaner: query strings, `.json`, and version segments (`v1`, `2026-03`, `unstable`,
  `api`, `admin`) are not places.
- **Collapse duplicates** — Shopify publishes every operation once per version: 1,540 → 312.
  Xero lists 87 operations in two specs; Anthropic 64 `?beta=true` twins.
- **Judge the tags before trusting them**:
  - *paths* — tags are verbs/capabilities (Salesforce `Get`/`Create`, HubSpot `Basic`/`Batch`) or
    fragmented (> 50% of tags have 1–2 ops) → lines come from path groups;
  - *coarse* — a few huge tags that are products (Xero Accounting/Payroll, Plaid) → tags become
    families, path groups become lines;
  - *tags* — giant tags split where their paths diverge, generic tags (`All`) re-homed by path.
- **Scopes** — `/accounts/{id}`, `/zones/{id}`, `/repos/{o}/{r}`, `/orgs/{org}`, and any first segment
  covering half the API (`/data/v64.0`) say whose resource, not which product; product = next segment.
  Interchanges with a scope's home never pull and never count as affinity.
- **RPC naming** — Plaid's `POST /transfer/get` is "Get transfer"; an API where ≥ 40% of paths end
  in a verb reads every final segment as the verb.
- **Local lines** — a family's 1–2 stop resources ride one thin "Local" line (DLR / tram).
- **Families** — seeded by product namespace (or a split tag's parent), then merged by interchange
  affinity until ≤ 12; small APIs (≤ 20 lines) are one city with fully distinct line colours.

**Composition — the user's correction.** Optimising crossings to zero drifted maps into islands
joined by dashed links. Kin: *"the lines aren't all separate … somewhat intertwined into a
thoughtful metro design"*, like the first Stripe map. Crossings are not the enemy; a real metro
crosses constantly. What matters is one consolidated city.
- **City mode (≤ 60 lines)** — an automatic backbone ring (the line with most real interchanges,
  ≥ 12 stops; else the biggest), families pinned around it by bearing, and **weave**: every line
  without a real interchange is tied to the ring at its family's side, so lines pass the core.
- **Conurbation mode (> 60 lines)** — one ring cannot hold 100 lines (GitHub: 906 crossings, and a
  fisheye did not help — the tangle was topological). Families of > 16 lines split into boroughs by
  community detection; each borough weaves into its own backbone line; boroughs packed 1.5 hops apart.
- **Atlas (Cloudflare, 4,531 ops)** — sheets of ≤ 450 operations, products that interchange most
  share a sheet, each sheet its own city with its own backbone, "⇄CODE p.N" for off-sheet changes,
  and an HTML index.

Two explicit-file maps are untouched by all of this: Stripe (circle) and Twilio (districts).

## 25 more exemplars (2026-11-14)

Harness, Tray.ai, Ada, Nylas, ZoomInfo, Telnyx, SendGrid, Confluent, Infobip, Dynatrace, Zoho CRM,
Customer.io, Lightspeed, Airwallex, WSO2, AfterShip, BigCommerce, Kinde, Gainsight, Appwrite,
Optimizely, Algolia, Snowflake, GitLab, LaunchDarkly — all with no families file except the two
atlases (Harness 2,838 ops, Optimizely 3,504), which only set `atlas.sheet_max_ops`.

Generator changes this round:
- **Specs are read without date parsing** — WSO2 has an example date `2019-02-31` that crashed YAML.
- **Compaction slides around obstacles** — a district blocked on the straight line to the centre now
  tries 8 directions; Infobip's Calls district was stranded across an empty third of the sheet
  (16.7k → 9.5k wide).
- **Adaptive scale** — px per cell grows with the densest neighbourhood (42 → up to 60). Labels are a
  fixed size, so a crowded core gets room without changing the layout: Snowflake labels on track
  44 → 14, Salesforce 49 → 21, Xero 20 → 2, Cloudflare atlas sheet 1 112 → 46.
- **Names** — 2-3 letter path codes are acronyms (ng → NG, ai → AI); path syntax is stripped
  (`Products({key})` → Products); an atlas sheet of leftovers is titled by its biggest lines, not "More".

Still weak: Optimizely atlas sheets 7–8 and Harness sheets 1–2 (dense leftovers), and long flat lines
(Infobip Calls ~100 stops) that have no sub-resources to branch on.

## Batch 1 of the exemplar run (50 providers, 2026-11-14)

Built end to end with `run_next.sh 50` (≈ 19 min): no load failures; Azure AD (12 sheets) and Adobe
Suite (7) became atlases automatically; six thin catalogs (< 25 operations) mapped as small cities.

- **Dominant family → distinct colours.** MongoDB's 48 lines all fell in one family (every path is
  `/api/atlas/v2/groups/{groupId}/…`), so the map was 48 shades of pink. When one family holds > 60% of
  the lines, its lines now take distinct palette colours.
- **Tried and reverted:** nested scopes + size-capped affinity merging + community-detected families.
  Each fixed MongoDB's grouping and broke someone else's (HubSpot's CRM vanished, families
  snowballed into one, or "More" swallowed half the API). Family inference changes ripple across every
  map — change it only with a before/after table of families for the existing maps.

### Batch 2 (2026-11-14)
- **Stray stops are docked.** Microsoft Azure had a one-stop line ("List operations") with no ties, left ~30 cells from the network; it doubled the canvas height (5910 -> 2970 px after the fix). Any stop more than 10 cells from its nearest neighbour now moves to the nearest free cell beside the network.
- Map links on the gallery open in a new tab, through `view.html` (zoom in/out, pan, fit).

### Batch 3 (2026-11-14)
- **Flat RPC catalogs split by leaf word.** Delx.ai is ~1,000 tools under `/api/v1/x402/<name>`; path grouping bottomed out in singletons and one line held 987 stops (a 78,636 px sheet). When a group of > 2 x max_line has a next segment that is > 60% distinct leaves, lines come from the leaf's first word (json, evm, csv), then its last word (check, sum), then alphabetical runs of <= max_line ("X402 A–C"). Delx.ai: 3 -> 106 lines, 12,610 x 11,540 px. Regression check over all mapped providers: only oracle-hospitality changed (430 -> 467 lines; worst sheet 124 -> 101 labels, sheet 3 14,660 -> 6,170 px).
- Providers with < 2 operations are not built (amazon-vpn); they and atlassian-confluence (no operations) are in skip.yml.

### Batch 4 (2026-11-14)
- **Local lines are capped at 30 stops.** A family's 1-2 stop resources ride one Local line; Kong had 122 of them on one tram that snaked across the sheet. Locals now cut into path-ordered runs of <= 30 ("More Local 1..5"). Rebuilt the 20 published maps it changed: worst lines adyen 131 -> 44, adobe 147 -> 64, oracle-hospitality 142 -> 120, scvd-store 94 -> 35, linkedin 64 -> 22, kong 122 -> 29 (12,281 x 8,051 -> 6,876 x 9,884 px).

### Batch 6 (2026-11-14)
- **One malformed spec no longer sinks a provider.** Tricentis (duplicate YAML anchor in one of its specs) and transcriptdock.com (broken flow mapping) failed to load; the reader now skips an unparseable file with a warning and maps the rest (tricentis 456 ops).
- **JSON specs are read.** The reader globbed only .yml/.yaml, so atlassian-confluence (two .json specs, 348 ops) mapped as empty. Three other .json files in the catalog (delinea, logicmonitor, anysphere-cursor-ai) are HTML pages saved under a spec name; they are skipped as unparseable.

## How to do APIs (the method map) — 2026-11-14

`howto.py` draws the API Evangelist method itself, from the five catalogs in `~/Github/api-evangelist/{lifecycle,policies,strategies,experiences,rules}/_data/`:

| Transit convention | What it encodes |
|---|---|
| **Lifecycle circle line** (black) | the 30 lifecycle stages in order, Domains → Deletion — the core every other line meets |
| Stage stations (★) | a lifecycle stage; links to lifecycle.apievangelist.com/store/<slug>/ |
| **Lines**, coloured by phase | one per stage, carrying the policies whose first lifecycle stage it is; stages with > 30 policies (Design 87, Governance 51) ride one line per policy scope (API / Technical / Business Contract), cut into runs of ≤ 30 |
| Stops | policies (283) — link to policies.apievangelist.com/store/<slug>/ |
| Changes | a policy that applies at a second stage changes to that stage's line |
| **Express routes** | strategies (62 of 75 route through ≥ 2 policies), in lifecycle order; hover in the sidebar to trace |
| Click panel (view.html) | every stop opens its policy, lifecycle stages, strategies, experiences and rules, each linked |

It writes a stand-in provider (`work/how-to-do-apis/`: an OpenAPI whose operations are the stops, an Arazzo file per strategy) so the same engine lays it out; per-stop `x-station`, `x-url`, `x-tip`, `x-info` and per-map `title`, `subtitle`, `express_title`, `source` are the hooks it uses. Rebuild after the catalogs change: `.venv/bin/python howto.py`, then publish. The gallery features it above the provider cards.

First draft lessons: the engine's automatic line source re-derived lines from paths (every policy its own line) — the families file pins `lines: tags`; without the circle line the six phases drew as islands, so the Lifecycle line is the backbone (`circle: [Lifecycle]`, `weave: true`).

### Dense-sheet pass (2026-11-14)
- **Crowded sheets are redrawn larger.** Labels are a fixed font size, so a sheet with > 40 labels on track re-runs at 80 px/cell, then 100 (same layout; `--umin`). Worst atlas sheets: palo-alto 170 -> 48, oracle-hospitality 125 -> 41, optimizely 117 -> 39; maps: malwarebytes 124 -> 38, cloudera 64 -> 12. Splaying stops further apart (`--radius`) made it worse (117 -> 134).
- `BAND=strong ./run_next.sh 50` maps the next band.
- **Stranded groups dock as a unit.** Single strays were docked, but pairs and trios (Atlassian Jira) stayed 100+ cells out; detached components smaller than 5% of stops now slide toward the main body. atlassian-jira 15,818 x 12,702 -> 9,856 x 10,686 px.
