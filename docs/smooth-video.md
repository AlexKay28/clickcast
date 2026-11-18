# Smooth MP4 reels and region annotations

`clickcast` captures at a deliberately low frame rate (browser screenshots are
expensive), then can retime the result into a smooth, high-FPS MP4. The capture
sidecar keeps its action-level detail while `media.fps` reports the actual
encoded frame rate.

## Capture backends

Two frame sources feed the same render kernel. Pick one with `--capture` on
`auto` / `run`, or `render.capture` in a scenario:

```bash
clickcast auto https://example.com -o tour.mp4 --format mp4 \
  --capture screencast --video-fps 30

clickcast run demo.yml -o demo.mp4 --capture screencast
```

```yaml
meta:
  render:
    format: mp4
    capture: screencast   # default: shots
    video_fps: 30
```

| | `shots` (default) | `screencast` |
|---|---|---|
| Frame source | one PNG per action boundary + held padding | Chromium CDP `Page.startScreencast`; the compositor pushes a frame when the page changes |
| Shows | settled states before/after each action | continuous motion: CSS transitions, route cross-fades, charts, spinners, typed characters |
| Cursor | synthetic overlay | same synthetic overlay (`cursor_xy`) on real footage, speed-capped with inserted glide frames |
| Engines | Chromium, Firefox, WebKit | **Chromium only** |
| Determinism | frame count is a function of `fps` × steps — reproducible | frame count follows wall time and compositor updates — **not** byte-reproducible |
| `--smooth` | retimes held stills into 30/60 fps | **retiming is a no-op**: frames are already continuous (a warning says so). It still opts into reel grammar |
| Cost | cheap: a few screenshots per step | roughly real time while recording, plus PNG writes |

### Screencast details

- Frames arrive as JPEG from `Page.startScreencast` (`--screencast-quality`,
  default 80) and are converted to PNG on a small worker pool so the CDP ACK
  is never stuck behind disk I/O. Every frame is ACKed, otherwise Chromium
  stops after one frame.
- At flush, frames are **resampled onto a fixed `video_fps` grid over the real
  capture duration**: each output slot uses the most recent captured frame at
  or before its timestamp (hold-last-frame). A 300 ms transition therefore
  spans ~9 distinct frames at 30 fps, and a static page correctly produces
  held frames rather than invented motion.
- Step attribution is by **timestamp interval**, not frame count: the recorder
  knows when each step started and when its action completed, and
  `frames.json` gains an optional per-frame `captured_at` (seconds since the
  screencast started) alongside `path` / `step_index` / `sub_index` /
  `cursor_xy`.
- `screencast_max_frames` (default 6000) is a hard disk guard for runaway
  animations. Hitting it logs a warning and drops the remaining frames.
- Cursor motion is capped at `CursorStyle.max_speed_px_s` (default 1600
  px/s, `None` disables it). Screencast frames are continuous, but
  `cursor_xy` only changes when an action completes, so without the cap the
  pointer teleports across the viewport in one frame. The pass carries the
  last known position through untracked frames and inserts eased glide frames
  where the distance exceeds one frame's budget. Those frames extend the reel
  slightly; the recorded page footage is untouched.
- `--smooth` and the shots-backend cursor interpolation are otherwise skipped
  for screencast reels: retiming would hold or cross-dissolve frames, which
  destroys the real timing the stream recorded.

### Engine fallback

`Page.startScreencast` is part of the Chromium DevTools Protocol; Firefox and
WebKit do not expose an equivalent. Rather than silently recording a slideshow
(or adding a second ffmpeg extraction + encode path), clickcast **fails fast**
with a documented error:

```
ScreencastUnavailable: screencast capture requires Chromium: CDP
Page.startScreencast is not available on 'firefox'. Use --capture shots (the
default) for Firefox/WebKit. See docs/smooth-video.md.
```

Use `--capture shots` (the default) on those engines.

### Measured on the 300 ms transition fixture

Tour: `clickcast auto .../slow-transition.html --max-steps 2`, 30 fps MP4,
`scripts/bench_motion.py`. A frame pair counts as a duplicate when fewer than
0.01% of its pixels changed by more than 4/255 levels — a whole-frame average
would call a click ripple static, which is how this metric was first measured
wrong (#306).

| backend | frames | fps | unique % | duplicate % | MP4 |
|---|---|---|---|---|---|
| `shots --smooth --no-grammar` | 60 | 30 | 27.1 | 72.9 | 136 KB |
| `shots --smooth` (grammar on) | 45 | 30 | 43.2 | 56.8 | 139 KB |
| `screencast` | 126 | 30 | **99.2** | 0.8 | 1056 KB |
| `screencast --smooth` (grammar on) | 126 | 30 | **99.2** | 0.8 | 1074 KB |

`unique%` over a whole reel that ends in idle dwells cannot reach 100 even with
perfect capture: a static page is legitimately a held frame. During a 300 ms
transition the screencast backend records at least 8 distinct frames at 30 fps,
which is what the test asserts.

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
actually changed. Scripted scenarios get the same pass (labels expanded by
`repeat`); they have no post-step URL history, so their boundaries dissolve.

The grammar pass also applies on top of screencast footage — pass `--smooth`
alongside `--capture screencast` to opt in. That combination is the
human-facing preset: real page motion plus synthesized cursor, click and
caption cues.

```bash
# What a demo reel should look like
clickcast auto https://example.com -o tour.mp4 --format mp4 \
  --capture screencast --smooth --video-fps 30
```

A runnable, self-contained example lives in
[`demo/screencast-reel/`](../demo/screencast-reel/): its own page with 300 ms
transitions and live typing, the scenario, and both the screencast and stills
renders for comparison.

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
