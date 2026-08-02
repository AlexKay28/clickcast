# AI-feedback sidecar — schema v3

Every non-`--no-sidecar` reel writes a sidecar JSON next to the media
file (`tour.gif` → `tour.gif.json`). This is the primary contract an AI
consumer reads. The canonical JSON Schema lives at
[`src/clickcast/feedback/schema/v3.json`](../src/clickcast/feedback/schema/v3.json).
The prior [`v2.json`](../src/clickcast/feedback/schema/v2.json) and
[`v1.json`](../src/clickcast/feedback/schema/v1.json) are preserved
verbatim for downstream consumers that bookmarked them — v3 is strictly
additive over v2 (two new optional per-step fields: `skip_reason` and
`error_code`; see the "v2 → v3 additive contract" section below).

## Top-level shape

```jsonc
{
  "schema_version": 3,          // this document's version
  "clickcast_version": "0.2.4", // the package version that wrote it
  "url": "https://example.com", // seed URL (nullable — YAML runs may not have one)
  "engine": "chromium",         // playwright engine
  "viewport": [1280, 800],
  "started_at": "2026-07-23T...Z",
  "duration_s": 12.4,
  "media": {...},               // encoded reel metadata
  "discovered_elements": [...], // ranked elements from discover()
  "steps": [...],               // one entry per step iteration (v3 rows
                                // carry optional `skip_reason` + `error_code`)
  "warnings": [],
  "errors": [],
  "graph": {...}                // v2 additive: app-shape summary (nullable)
}
```

`schema_version` and `clickcast_version` let consumers verify
compatibility before parsing. Bump `schema_version` on any breaking
change; the current stable release is **v2**.

## `media`

```jsonc
{
  "path": "tour.gif",
  "format": "gif",              // gif | mp4 | webp | frames
  "size_bytes": 2400000,
  "frame_count": 120,
  "duration_s": 10.0,
  "fps": 12
}
```

## `discovered_elements`

Only populated by commands that call auto-discovery (`clickcast auto`,
`Reel.discover()`). Each entry mirrors the roadmap for #6:

```jsonc
{
  "selector": "role=button[name=\"3D\"]",
  "role": "button",
  "text": "3D",
  "bbox": [x, y, width, height],
  "score": 3,                   // higher = more likely worth clicking
  "source": "dom-heuristic"     // "dom-heuristic" | "ax-tree"
}
```

## `steps[]`

One entry per step iteration (a `repeat: N` step produces `N` entries).

```jsonc
{
  "index": 0,
  "action": "goto",             // action verb — see clickcast.core.actions
  "args": {"url": "https://x"}, // action-specific fields (verb-dependent)
  "status": "ok",               // "ok" | "failed" | "skipped"
  "duration_ms": 1900,          // monotonic-clock timing
  "frames": ["frame-0000-000.png", "..."],
  "label": "Open site",         // user-authored caption, if any
  "cursor_xy": [640, 400],      // pixel center of the target, if any
  "page_state": {               // post-action snapshot; may be null
    "title": "Example",
    "url_after": "https://example.com/",
    "console_errors": [],       // up to 50 entries
    "page_errors": [],          // up to 50 entries
    "network_failed": []        // up to 50 URLs
  },
  "error": null                 // string when status != "ok"
}
```

`optional: true` steps that fail get `status: "skipped"` with `ok: true`
in `ActionResult` terms — the sidecar reports `"skipped"` and keeps the
error message.

## `graph` — v2 additive block

Populated by [`clickcast.feedback.graph.build_graph`] when the tour
produced at least one recorded `page_state.url_after`. Absent (or
`null`) for tours that never left the discovery pass and for v1
sidecars written before #107 landed. See
[#29 Track C](https://github.com/AlexKay28/clickcast/issues/29) /
[#107](https://github.com/AlexKay28/clickcast/issues/107) for the
motivation — the LLM planning surface that consumes the sidecar wants
to reason about "the shape of this app" rather than "what happened in
this specific sequence".

```jsonc
{
  "nodes": [
    {
      "id": "n1",
      "kind": "page",
      "url": "https://example.com/pricing",
      "title": "Pricing — Example",
      "dom_signature": "",
      "first_seen_step": 0,
      "last_seen_step": 3,
      "components": []            // ids of component nodes on this page
    }
    // "kind": "component" nodes ship empty in this release — the
    // landmark-detection pass (role + aria-label + bbox fingerprint) is
    // a follow-up. `dom_signature` helper is exported today so the
    // follow-up plugs straight in.
  ],
  "edges": [
    {
      "from": "n1",
      "to": "n2",
      "via_step": 2,
      "selector": "a:has-text('Docs')",
      "transition_kind": "navigation"    // only kind shipped in v2-first
    }
  ]
}
```

Deferred to follow-ups (tracked separately):

- `transition_kind: "reveal"` / `"dismiss"` — modal / drawer open+close
  detection requires DOM diffing across step boundaries.
- `ComponentNode` extraction — landmark fingerprinting from `discovery/`
  output plus dedup across pages via `dom_signature`.

## v1 → v2 additive contract

v2 is **strictly additive** over v1:

- All v1 fields are unchanged (same names, same types, same defaults).
- `schema_version` default bumped from 1 → 2.
- One new optional top-level field: `graph` (defaults to `null`).

Consequences:

- **v1 files load through v2**: `graph` is optional, so an old sidecar
  parses cleanly and `report.graph is None`.
- **v2 files parsed by v1 consumers**: the top-level `Report` never
  forbade unknown keys precisely so this works. A v1 parser that
  ignores unknown fields keeps working; a v1 parser that reads
  `schema_version` and refuses > 1 will (correctly) opt out.

## v2 → v3 additive contract

v3 is **strictly additive** over v2 (see #151 AI-2, AI-5):

- All v2 fields are unchanged (same names, same types, same defaults).
- `schema_version` default bumped from 2 → 3.
- Two new optional per-step fields on `StepReport`:
  - `skip_reason: "optional_no_reaction" | "pre_action_failed" |
    "element_vanished" | "cross_origin_bounce" | null` — categorises
    the four distinct causes that all currently render as
    `status="skipped"`. Populated by the action engine when a step
    optional-fails; a CI baseline can pin the exact reason.
  - `error_code: "timeout" | "locator_missing" | "cross_origin" |
    "navigation_error" | "selector_ambiguous" | "other" | null` —
    stable categorisation of the exception kind on failed / skipped
    steps. Consumers gate on error KIND, not drifting message text.

Consequences:

- **v2 files load through v3**: both new fields default to `null`, so
  an old sidecar parses cleanly under the v3 model.
- **v3 files parsed by v2 consumers**: `StepReport` DOES forbid extras
  (`extra="forbid"`), so a strict v2 parser will REJECT a v3 sidecar
  that carries a non-null `skip_reason` / `error_code`. A lenient v2
  consumer that ignores unknown per-step keys keeps working; the
  strict path is the reason v3 bumps `schema_version` rather than
  silently landing.

The nested sub-models (`Media`, `DiscoveredElement`, `StepReport`,
`PageState`, `PageNode`, `ComponentNode`, `Edge`, `Graph`) DO forbid
extras — those shapes are stable within a major schema version.

## Reading the sidecar

- **In Python**: `from clickcast.feedback import load; report = load("tour.gif.json")`.
- **Without importing the package**: parse the JSON directly. A worked
  example that lists failed steps and their frames lives at
  [`tests/consumer/read_sidecar.py`](../tests/consumer/read_sidecar.py).

See [ai-integration.md](ai-integration.md) for a two-line agent
integration example.
