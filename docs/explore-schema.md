# `clickcast explore` output — `explore.json`

> Schema v1 · `kind: clickcast/explore` · emitted by `clickcast explore <url>`
> companion docs: [README](../README.md) · [feedback schema](feedback-schema.md)

`explore` is the **shape-only** counterpart to `auto`: the same
`Session` → `discover` → click-and-observe loop with BFS traversal, minus the recording.
No frames, no annotator, no encoding — so mapping an app costs seconds
instead of a full reel shoot. The output is a JSON file (default
`explore.json`) describing the app as a state graph, plus each visited
page's top discoveries for selector authoring.

```bash
clickcast explore https://example.com --out map.json
```

## The payload

```jsonc
{
  "schema_version": 1,           // this document's version
  "kind": "clickcast/explore",    // discriminate vs. reel sidecars
  "clickcast_version": "0.4.4",   // the package version that wrote it

  "url": "https://example.com/",  // the start URL
  "traversal": "bfs",             // queue policy: bfs | dfs
  "wall_s": 8.3,                  // wall-clock seconds (excluded from identity)

  "pages": [ /* one entry per VISITED page, in visit order */ ],
  "graph":  { /* the app's shape: nodes + edges */ }
}
```

`schema_version` and `clickcast_version` let consumers verify
compatibility before parsing. `schema_version` will only ever bump
**additively** — the same contract the reel sidecar holds
([v1 → v2 additive contract](feedback-schema.md)).

## `pages[]` — what was explored

One entry per page the engine actually **visited** (cross-origin
destinations are recorded as graph nodes but never driven):

```jsonc
{
  "node": "n1",                    // the matching graph node id
  "url": "https://example.com/",   // as visited (fragments preserved)
  "title": "Example Domain",       // document.title at visit time
  "clicks_ok": 3,                  // successful clicks on this page
  "clicks_failed": 1,              // timed-out / missing targets
  "discovered": [                   // top-N discovered elements (see --per-page)
    { "selector": "text=\"More information...\"",
      "role": "link", "text": "More information...",
      "bbox": [100, 80, 100, 30],  "score": 3, "source": "dom-heuristic" }
  ]
}
```

`discovered[]` entries are the same shape `clickcast elements --json`
emits (`Element.to_dict()`), clipped to `--per-page N` (default 10). The
full pool is still *clicked* during exploration — the cap only bounds the
JSON's size.

## `graph` — the app's shape

Exactly the `Graph` block a reel sidecar carries (v4 shape, see
[feedback schema](feedback-schema.md#the-graph-block)) — nodes and
edges, nothing else — so a consumer that already parses sidecar graphs
parses this with zero new code:

```jsonc
"graph": {
  "nodes": [
    { "id": "n1", "kind": "page", "url": "https://example.com/",
      "title": "Example Domain", "dom_signature": "",
      "first_seen_step": 0, "last_seen_step": 0, "components": [] }
  ],
  "edges": [
    { "from": "n1", "to": "n2", "via_step": 0,
      "selector": "text=\"More information...\"",
      "transition_kind": "navigation" }
  ]
}
```

- **Node ids** (`"n1"`, `"n2"`, …) follow **first-seen** order: the start
  URL is `n1`; every distinct URL that appeared through a navigation gets
  the next id — even a cross-origin or never-visited destination.
- **Step indices are page-granular.** Exploration keeps no per-step
  ledger, so `first_seen_step`/`last_seen_step` and an edge's
  `via_step` all carry the **visit ordinal** — "found while exploring the
  page at that index in `pages[]`".
- `transition_kind` is `"navigation"` for every edge: the click changed
  the URL. (`reveal`/`dismiss` classification needs DOM diffing and is
  deferred, matching the sidecar graph.)

## Determinism

The same click outcomes produce the same nodes, edges, and ordering —
`wall_s` is the only wall-clock-dependent field, reported but excluded
from identity. `pages[]` is in visit order; `nodes`/`edges` in
first-seen / discovery order.

## Bounds

Exploration is bounded by all three of `--max-steps` (total successful
clicks, default 15), `--max-pages` (visited pages, default 10) and
`--max-duration` (wall-clock, default 120s) — like the auto tour, the
first bound hit stops the map where it stands.

## Versioning & drift

- v1 (this document) — first shipped in the v0.5 line; nodes, edges,
  per-page discoveries.
- Additive bumps (v2+) will follow the sidecar graph's precedent: new
  optional fields only; a strict v1 parser that refuses `> 1` opts out
  correctly.
