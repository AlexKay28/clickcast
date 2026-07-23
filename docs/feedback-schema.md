# AI-feedback sidecar — schema v1

Every non-`--no-sidecar` reel writes a sidecar JSON next to the media
file (`tour.gif` → `tour.gif.json`). This is the primary contract an AI
consumer reads. The canonical JSON Schema lives at
[`src/clickcast/feedback/schema/v1.json`](../src/clickcast/feedback/schema/v1.json).

## Top-level shape

```jsonc
{
  "schema_version": 1,          // this document's version
  "clickcast_version": "0.1.0", // the package version that wrote it
  "url": "https://example.com", // seed URL (nullable — YAML runs may not have one)
  "engine": "chromium",         // playwright engine
  "viewport": [1280, 800],
  "started_at": "2026-07-23T...Z",
  "duration_s": 12.4,
  "media": {...},               // encoded reel metadata
  "discovered_elements": [...], // ranked elements from discover()
  "steps": [...],               // one entry per step iteration
  "warnings": [],
  "errors": []
}
```

`schema_version` and `clickcast_version` let consumers verify
compatibility before parsing. Bump `schema_version` on any breaking
change; the current stable release is **v1**.

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

## Forward compatibility

The top-level `Report` deliberately does **not** forbid unknown keys. A
future `schema_version: 2` (tracked in [#29 Track C](https://github.com/AlexKay28/clickcast/issues/29))
adds a top-level `graph` block for BFS-style exploration. v1 consumers
that ignore unknown fields will keep working; consumers that need the
graph should switch to the v2 schema when it ships.

The nested sub-models (`Media`, `DiscoveredElement`, `StepReport`,
`PageState`) DO forbid extras — those shapes are stable within a
major schema version.

## Reading the sidecar

- **In Python**: `from clickcast.feedback import load; report = load("tour.gif.json")`.
- **Without importing the package**: parse the JSON directly. A worked
  example that lists failed steps and their frames lives at
  [`tests/consumer/read_sidecar.py`](../tests/consumer/read_sidecar.py).

See [ai-integration.md](ai-integration.md) for a two-line agent
integration example.
