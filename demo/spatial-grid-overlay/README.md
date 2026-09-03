# spatial-grid-overlay

**When to use:** an agent describing "the button near the top-right" to
itself is guessing. `--grid` burns a coordinate system into every rendered
frame — an agent reading the reel can say "click at (450, 300)" instead of
eyeballing it, and the sidecar records the exact pitch so those coordinates
are unambiguous.

## Command

The reel below was generated against react.dev with:

```bash
clickcast auto https://react.dev/ \
  --pace slow \
  --max-pages 1 \
  --max-steps 5 \
  --initial-wait 3 \
  --click-timeout 15 \
  --grid \
  --grid-pitch 150 \
  --traversal dfs \
  --out reel.gif
```

`--grid` alone gives you the default `full` style (major gridlines every
`--grid-pitch` pixels + minor gridlines at a tenth of that + coordinate
labels along the top and left edges). Point it at your own site the same
way — drop `--grid-pitch` to use the 100px default, or add
`--grid-style ruler` for labels only, no gridlines (lighter-weight when an
agent just needs the coordinate system, not the visual density).

## Reel

![spatial-grid-overlay reel](reel.gif)

## Two grid styles

| `--grid-style` | What it draws | When |
|---|---|---|
| `full` (default) | Major + minor gridlines, axis labels, white @ 20% opacity so it doesn't dominate the image | Default choice — visually confirms the coordinate system, not just states it |
| `ruler` | Coordinate labels only, no gridlines | An agent that already trusts the coordinate system and just needs less visual noise |

Layer order is `content → grid → highlights → arrows → labels` — the grid
draws *behind* click highlights, sticky arrows, and cursors, so those stay
legible over it. It composes with `--zoom-on-click` too: the grid renders on
the zoomed frame, so labels always reflect the coordinates of the image the
agent is actually looking at, not the original unzoomed frame.

## What the sidecar carries

```json
{
  "schema_version": 4,
  "annotate": {
    "grid": {
      "pitch": 150,
      "style": "full",
      "color": "#FFFFFF33"
    }
  }
}
```

An agent parsing the sidecar reads `annotate.grid` once and knows the exact
coordinate system every frame in the reel was rendered with — no need to
infer pitch from counting gridlines in the image.

## Related workflows

- **[`../accessible-element-targeting/`](../accessible-element-targeting/)** —
  the grid overlay's coordinate system fused directly with each element's
  accessibility metadata (`role`/`name`/`state` + `grid_cell`) in one
  `elements --json --grid` call, instead of reading coordinates off a
  rendered image.
- **`clickcast shot <url> --grid`** — the same overlay on a single
  screenshot, when you don't need a full tour.
