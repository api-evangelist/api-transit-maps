# API Transit Maps

Every operation of a public API, drawn as a metro map: **operations are stops, tags are lines,** and
the places where one resource reaches into another are interchanges. Every stop links to its
documentation on [APIs.io](https://apis.io).

**Gallery:** https://transit.apievangelist.com

## How it works

`build_network_map.py` reads a provider's OpenAPI descriptions and Arazzo workflows (as published in
the API Evangelist catalog) and lays them out the way transit agencies draw their networks:
octilinear track, a backbone circle line, families of lines in their own colours, interchanges,
express routes, closed stations for deprecated operations, and depot track for test-mode helpers.
Small and mid-size APIs become one woven city; APIs of more than ~60 lines become a conurbation of
boroughs; APIs too big for one sheet (Cloudflare, Harness, Optimizely) become an atlas.

[`DESIGN.md`](DESIGN.md) says which transit convention encodes which API fact, how every layout stage
works, and what each provider taught the generator, with measured iteration logs.

| File | What it does |
|---|---|
| `transit_model.py` | Stops, lines, families, interchanges, express routes, names |
| `build_network_map.py` | Layout and SVG for one sheet |
| `atlas.py` | Plans and builds a multi-sheet atlas with an index page |
| `build_gallery.py` | Builds this gallery (`index.html`, `thumbs/`) |
| `<slug>-families.yml` | Optional overrides; most providers need none |

```
python3 -m venv .venv && .venv/bin/pip install numpy scipy networkx pyyaml
.venv/bin/python build_network_map.py work/<slug> <slug> auto
```

Provider descriptions are pulled from the API Evangelist profile store into `work/` (not committed).

## License

Code Apache-2.0 (`LICENSE`); maps CC BY-NC-SA 4.0 (`LICENSE-MAPS.md`).
