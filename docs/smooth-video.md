# Smooth MP4 reels and region annotations

`clickcast` captures at a deliberately low frame rate (browser screenshots are
expensive), then can retime the result into a smooth, high-FPS MP4. The capture
sidecar keeps its action-level detail while `media.fps` reports the actual
encoded frame rate.

## Smooth MP4

```bash
clickcast auto https://example.com -o tour.mp4 --smooth --video-fps 30
# Scripted tours use the same switches:
clickcast run docs/scenarios/form.yml -o form.mp4 --smooth --video-fps 30
```

Smooth mode uses cubic ease-in-out cursor paths instead of teleporting between
clicks and short cross-dissolves between settled page states. Original frames
are held as needed so retiming preserves the source reel's duration. It also
works for WebP/GIF, but MP4 (H.264) is substantially smaller at 30/60 fps.

For a scenario, the equivalent YAML is:

```yaml
meta:
  render:
    fps: 12          # browser capture rate
    format: mp4
    smooth: true
    video_fps: 30    # encoded/output rate
```

Explicit `--smooth` / `--no-smooth` and `--video-fps` override scenario values.
Without smooth mode, existing capture and encoding behaviour is unchanged.

### Reel grammar

In smooth mode `clickcast` also composites a small amount of **reel grammar**
over the retimed frames, so the result reads as someone operating the product
rather than a slideshow:

| Effect | What it does |
|---|---|
| Click feedback | a press cue on the frame before an action, then a short expanding ripple at the cursor |
| Transition grammar | cross-dissolve for same-URL (SPA) changes, a short directional slide for cross-document navigation |
| Captions | a lower-third caption for every step that carries a label |
| Timeline | a thin progress bar along the bottom edge |

All four are post-capture, deterministic and pixel-only: frame count, duration
and the sidecar's step attribution are untouched. Turn the whole pass off with
`--no-grammar` (or `render.grammar: false` in a scenario) to get the plain
retimed output:

```bash
clickcast auto https://example.com -o tour.mp4 --smooth --video-fps 30 --no-grammar
```

Captions come from `StepReport.label` and the slide/dissolve decision from the
step's recorded URL, so a scenario only gets slide transitions where the URL
actually changed. `scripts/bench_motion.py` measures the effect: the pass adds
real new frames, but the headline jump in `unique%` comes from the screencast
capture backend, which records page motion instead of holding stills.

## Region annotations

Annotations draw attention to stable page regions with simple, deterministic
primitives. They are applied after capture, so no extra browser passes are
needed. Coordinates are viewport pixels. `start_step` and inclusive `end_step`
refer to recorder steps (zero-based); omitting them shows the annotation for
the whole reel.

Pass a YAML or JSON file to either command:

```bash
clickcast run scenario.yml -o tour.mp4 --smooth --annotations annotations.yml
```

Or put the same list under the scenario's top-level `annotations:` key.

```yaml
annotations:
  - kind: spotlight
    bbox: [40, 80, 520, 360]
    start_step: 0
    end_step: 1
    dim_alpha: 130
  - kind: rect
    bbox: [64, 110, 300, 54]
    color: [255, 200, 40, 240]
    width: 4
    start_step: 0
    end_step: 1
  - kind: arrow
    start: [380, 150]
    end: [350, 150]
    text: "Edit the title"
    start_step: 0
    end_step: 0
  - kind: circle
    bbox: [620, 90, 42, 42]
    start_step: 1
    end_step: 1
  - kind: text
    bbox: [64, 190, 360, 60]
    text: "Saved state is visible here"
    start_step: 2
    end_step: 2
```

Supported primitives:

- **`rect`** — padded rounded-rectangle outline, with optional `fill` RGBA.
- **`circle`** — ellipse inside `bbox`.
- **`arrow`** — line and arrowhead from `start` to `end`.
- **`text`** — readable rounded callout at `bbox` (or `start`).
- **`spotlight`** — dims everything outside `bbox`, leaving the selected area
  untouched.

Colors are `[red, green, blue, alpha]` with channels in `0..255`. Shapes are
clipped to the viewport; partially off-screen annotations are safe. Keep labels
short and use a small number of simultaneous primitives: the goal is an
AI- and human-readable report, not an overlay that hides the UI.
