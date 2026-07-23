# 🎞️ clickcast

> Give AI agents visual + structured feedback about live web UIs — and give humans deterministic demo reels while you're at it.

[![PyPI version](https://img.shields.io/pypi/v/clickcast.svg)](https://pypi.org/project/clickcast/)
[![Python](https://img.shields.io/pypi/pyversions/clickcast.svg)](https://pypi.org/project/clickcast/)
[![CI](https://img.shields.io/github/actions/workflow/status/AlexKay28/clickcast/ci.yml?branch=main)](https://github.com/AlexKay28/clickcast/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

> **Not to be confused with [vercel-labs/webreel](https://github.com/vercel-labs/webreel)** — that's a TypeScript tool for authoring polished demo videos. `clickcast` is a Python tool aimed primarily at *AI agents* that need a visual modality onto a live web UI, and secondarily at humans who want reproducible demo reels.

`clickcast` drives a real browser through a website — clicking buttons, toggling views, switching
languages, scrolling, filling forms — and hands back **two things**:

1. A watchable **reel** (GIF / MP4 / WebP) with click ripples, cursor trails, and a progress bar.
2. A machine-readable **JSON sidecar** — every step, every discovered element, every console error,
   every failed request — so an AI agent can *reason* about what it just saw without watching the pixels.

Point it at a URL and it will *auto-discover* the interactive elements and build a tour for you,
or hand it a small YAML **scenario** for a scripted, repeatable walkthrough.

---

## Demo

```bash
clickcast auto https://worldsight-weld.vercel.app --out worldsight.gif
```

<p align="center">
  <img src="docs/demo.gif" alt="clickcast touring a site" width="720">
</p>

```
✔ Launching chromium (1280×800)
✔ Page loaded in 1.9s  ·  title: "WorldSight — World Metrics Map"
✔ Discovered 11 interactive elements
  → toggle 3D view          [button]
  → switch language ES      [button]
  → open Rankings           [link]
  → Compare countries       [button]
  ...
✔ Captured 42 frames
✔ Encoded worldsight.gif (2.3 MB, 18s, 12 fps)
```

---

## Why clickcast

LLM agents today reason about web UIs almost entirely through DOM/HTML. That misses rendered
layout, visual state transitions, regressions that don't change the DOM but ruin the pixels, and
whether a click *appeared* to do anything. `clickcast` closes that gap by producing both a
video artifact and a structured JSON sidecar the agent can parse.

For humans, screen recorders are manual, non-reproducible, and go stale the moment your UI
changes. Existing browser-automation frameworks (Playwright, Selenium) *can* capture screenshots,
but you have to write and maintain glue code for framing, timing, annotation, and encoding every
time. `clickcast` collapses that into one tool with three modes:

| Mode | Command | When to use |
|------|---------|-------------|
| **Auto** | `clickcast auto <url>` | You want a quick visual tour and don't care about the exact script. |
| **Scenario** | `clickcast run tour.yml` | You want a precise, repeatable walkthrough (docs, release notes, CI). |
| **Shot** | `clickcast shot <url>` | You just need one clean, full-page screenshot. |

Everything is deterministic, headless-by-default, CI-friendly, and version-controllable.

---

## Features

- 🧠 **AI-feedback sidecar** — every run writes a versioned JSON report an agent can parse: steps, timings, discovered elements, per-step frames, console errors, failed requests.
- 🤖 **Auto-tour** — heuristically finds buttons, toggles, tabs, and links, then visits them in a sensible order.
- 📝 **Declarative scenarios** — describe a walkthrough in readable YAML; no Python required.
- 🎨 **Annotated output** — click ripples, action labels, cursor trails, and a progress bar (each toggleable).
- 🖼️ **Multiple formats** — `gif`, `mp4`, `webp`, or a raw `frames/` directory.
- 📱 **Device emulation** — desktop, mobile, tablet presets, or a custom viewport & device-scale-factor.
- ⏱️ **Frame control** — set fps, per-step dwell time, loop count, and easing between actions.
- 🌐 **SPA-aware** — waits for network idle and custom selectors so hydration finishes before capture.
- 🔁 **Reproducible** — same scenario + same site = same reel. Great for visual regression.
- 🧩 **Scriptable API** — use it as a Python library, not just a CLI.
- 🚦 **CI-ready** — runs headless in GitHub Actions; ships a reusable action.

---

## Install

```bash
# Recommended: isolated install
pipx install clickcast

# or plain pip
pip install clickcast

# one-time: fetch the browser binaries Playwright needs
clickcast install
```

`clickcast install` is a thin wrapper over `playwright install chromium` (add `--with-deps` on Linux
CI to pull system libraries). Firefox and WebKit are optional: `clickcast install firefox webkit`.

**Requirements:** Python ≥ 3.9. On Linux you'll need the usual headless-Chromium system deps
(handled by `--with-deps`).

---

## Quickstart

```bash
# 1. Auto-tour a site and save a GIF
clickcast auto https://example.com --out tour.gif

# 2. Scaffold a scenario you can edit
clickcast init tour.yml --url https://example.com

# 3. Run the scenario
clickcast run tour.yml

# 4. Grab a single full-page screenshot
clickcast shot https://example.com --full-page --out home.png
```

---

## Commands

### `clickcast auto <url>`

Discover interactive elements and build a tour automatically.

```bash
clickcast auto https://worldsight-weld.vercel.app \
  --out worldsight.gif \
  --max-steps 8 \
  --viewport 1280x800 \
  --fps 12 \
  --dwell 1.2 \
  --lang en
```

| Flag | Default | Description |
|------|---------|-------------|
| `--out PATH` | `reel.gif` | Output file; extension picks the format (`.gif`/`.mp4`/`.webp`). |
| `--max-steps N` | `10` | Cap on how many elements to visit. |
| `--include SELECTOR` | – | Only consider elements matching this CSS selector. |
| `--exclude SELECTOR` | – | Skip elements matching this selector (e.g. `footer a`). |
| `--order MODE` | `dom` | Visit order: `dom`, `visual` (top→bottom, left→right), or `random`. |
| `--dwell SEC` | `1.0` | Seconds to linger after each action. |
| `--scroll` | `on` | Auto-scroll targets into view before interacting. |
| `--dry-run` | – | Print the planned tour without recording. |

### `clickcast run <scenario.yml>`

Execute a declarative scenario (see [Scenario format](#scenario-format)).

```bash
clickcast run tour.yml --out release-notes.mp4 --format mp4 --loop 0
```

Useful flags: `--out`, `--format`, `--headful` (watch it run), `--slowmo MS`, `--var key=value`
(inject variables usable as `{{ key }}` inside the scenario).

### `clickcast shot <url>`

Capture a single screenshot.

```bash
clickcast shot https://example.com --full-page --wait "networkidle" --out home.png
```

Flags: `--full-page`, `--selector CSS` (screenshot just one element), `--wait`, `--viewport`,
`--device`, `--dark` (emulate `prefers-color-scheme: dark`).

### `clickcast init [file]`

Scaffold a starter scenario file, optionally pre-seeded by auto-discovering a URL.

```bash
clickcast init tour.yml --url https://example.com --from-auto
```

`--from-auto` runs discovery once and writes the found steps into the file so you can trim/edit
instead of starting blank.

### `clickcast elements <url>`

Dump the interactive elements clickcast can see — handy for authoring selectors.

```bash
clickcast elements https://example.com --interactive --json > elements.json
```

### `clickcast doctor`

Diagnose the environment: Python version, installed browser engines, ffmpeg availability, and
whether the sandbox can reach the network.

```bash
clickcast doctor
```

### `clickcast config`

Read/write persistent defaults (see [Configuration](#configuration)).

```bash
clickcast config set defaults.viewport 1440x900
clickcast config get defaults.viewport
clickcast config path
```

---

## Scenario format

Scenarios are plain YAML. A scenario is a list of **steps**; each step is one **action**.

```yaml
# tour.yml
meta:
  name: WorldSight broad tour
  engine: chromium          # chromium | firefox | webkit
  viewport: 1280x800
  device: null              # or "iPhone 15", "Pixel 8", "iPad Pro"
  fps: 12
  dwell: 1.0                # default seconds after each step
  format: gif               # gif | mp4 | webp | frames
  out: worldsight.gif
  annotate:
    clicks: true
    labels: true
    cursor: true
    progress: true
    watermark: false

steps:
  - goto: https://worldsight-weld.vercel.app
    wait: networkidle
    label: "Open WorldSight"

  - click: "text=3D"
    label: "Switch to 3D globe"
    dwell: 2.0

  - hover: "[aria-label='Rankings']"
  - click: "[aria-label='Rankings']"
    label: "Open Rankings"

  - click: "text=Español"
    label: "Switch language → ES"

  - scroll: { to: "footer", behavior: smooth }
  - screenshot: { full_page: false }
```

### Supported actions

| Action | Example | Notes |
|--------|---------|-------|
| `goto` | `goto: https://…` | Navigate; pair with `wait`. |
| `click` | `click: "text=Compare"` | CSS or Playwright text selector. |
| `dblclick` | `dblclick: ".cell"` | Double click. |
| `hover` | `hover: ".menu"` | Reveal hover states/tooltips. |
| `type` | `type: { into: "#search", text: "Japan" }` | Types into a field. |
| `press` | `press: "Enter"` | Keyboard key. |
| `select` | `select: { in: "#metric", value: "GDP" }` | Dropdowns. |
| `scroll` | `scroll: { to: "footer" }` or `scroll: { by: 600 }` | Element or pixel scroll. |
| `wait` | `wait: 1.5` or `wait: networkidle` or `wait: ".map-loaded"` | Time, load-state, or selector. |
| `screenshot` | `screenshot: { full_page: true }` | Force a frame capture. |
| `label` | `label: "Human-readable caption"` | Overlay caption for that step. |

Any step also accepts `dwell`, `optional: true` (don't fail the run if the selector is missing),
and `repeat: N`.

---

## Configuration

Precedence (highest first): **CLI flags → scenario `meta:` → project `clickcast.toml` → user config → built-in defaults.**

```toml
# clickcast.toml (project-local)
[defaults]
engine   = "chromium"
viewport = "1280x800"
fps      = 12
dwell    = 1.0
format   = "gif"

[annotate]
clicks   = true
labels   = true
progress = true
watermark = false

[network]
# extra headers, basic-auth, or a proxy for reaching internal sites
proxy = ""
```

Environment overrides: `CLICKCAST_ENGINE`, `CLICKCAST_VIEWPORT`, `CLICKCAST_HEADFUL`, `CLICKCAST_PROXY`.

---

## Output formats

| Format | Best for | Notes |
|--------|----------|-------|
| `gif` | READMEs, chat, quick shares | Widest compatibility; larger files. |
| `mp4` | Docs sites, social, long tours | Smallest for length; needs `ffmpeg`. |
| `webp` | Web embedding | Great size/quality; animated. |
| `frames` | Custom pipelines | Writes numbered PNGs to a directory. |

`--quality 1..30` trades size for fidelity (lower = better). `--loop 0` loops forever; `--loop 1`
plays once.

---

## Recipes

**Keep your README demo fresh in CI**

```yaml
# .github/workflows/reel.yml
name: refresh-demo
on:
  workflow_dispatch:
  schedule: [{ cron: "0 6 * * 1" }]   # every Monday
jobs:
  reel:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: AlexKay28/clickcast-action@v1
        with:
          scenario: docs/tour.yml
          out: docs/demo.gif
      - run: |
          git add docs/demo.gif
          git commit -m "chore: refresh demo reel" || echo "no changes"
          git push
```

**Mobile capture**

```bash
clickcast auto https://example.com --device "iPhone 15" --out mobile.gif
```

**Dark mode shot**

```bash
clickcast shot https://example.com --dark --full-page --out home-dark.png
```

---

## Python API

```python
from clickcast import Reel

reel = (
    Reel("https://worldsight-weld.vercel.app", viewport=(1280, 800), fps=12)
    .goto(wait="networkidle")
    .click("text=3D", label="Switch to 3D globe", dwell=2.0)
    .click("[aria-label='Rankings']", label="Open Rankings")
    .click("text=Español", label="Switch language")
    .scroll(to="footer")
)

reel.save("worldsight.gif")          # or .save("tour.mp4", quality=8)
```

Discovery is available programmatically too:

```python
from clickcast import discover
elements = discover("https://example.com", interactive=True)
```

---

## How it works

```
   URL ──▶ Playwright ──▶ Action engine ──▶ Frame grabber ──▶ Annotator ──▶ Encoder ──▶ .gif/.mp4/.webp
            (chromium)     (auto | scenario)   (per-step PNG)   (Pillow)      (imageio/ffmpeg)
```

1. **Launch** a browser engine (Chromium by default) at the requested viewport/device.
2. **Plan** the tour — either auto-discovered from the accessibility tree + DOM, or read from the scenario.
3. **Execute** each action, waiting for load-states/selectors so SPA hydration completes.
4. **Capture** a frame after every action (plus a few padding frames for smooth playback).
5. **Annotate** frames with click ripples, cursor path, captions, and a progress bar.
6. **Encode** to the target format (Pillow/imageio for GIF/WebP, ffmpeg for MP4).

---

## Troubleshooting

- **Blank frames / nothing rendered** — the page is a SPA; add `wait: networkidle` or a
  `wait: ".loaded-selector"` to your first step.
- **`ffmpeg not found`** — install ffmpeg, or output `gif`/`webp` (which don't require it).
- **Selector not found** — run `clickcast elements <url>` to see what's actually clickable, or mark
  the step `optional: true`.
- **Can't reach an internal site** — set a proxy via `CLICKCAST_PROXY` or `[network].proxy`, or run
  `clickcast doctor` to confirm network egress.
- **Chromium missing** — run `clickcast install` (add `--with-deps` on Linux CI).

---

## Roadmap

- [ ] Voice-over / caption track baked into MP4
- [ ] Side-by-side "before/after" diff reels for visual regression
- [ ] Record real network + console logs alongside the reel
- [ ] Parallel multi-page tours stitched into one output
- [ ] Web UI for authoring scenarios visually

---

## Contributing

Issues and PRs welcome. To set up locally:

```bash
git clone https://github.com/AlexKay28/clickcast
cd clickcast
pip install -e ".[dev]"
clickcast install
pytest
```

Please run `ruff` and `pytest` before opening a PR.

---

## License

MIT © 2026 Your Name. See [LICENSE](./LICENSE).
