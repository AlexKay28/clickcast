---
name: clickcast
description: Record deterministic Playwright browser tours that produce both a human-watchable GIF/MP4 reel and a machine-readable v5 JSON sidecar, plus an optional MCP server for live interactive control. Use this skill when an AI agent must visually verify a live web UI, author reproducible YAML scenario walkthroughs, generate CI regression gates, produce smooth MP4 reels with region annotations, or inspect a site’s interactive structure.
---

# clickcast — AI agent runbook

> This file is the Agent Skills manifest for this repository. Keep it
> self-contained enough that a harness can bootstrap clickcast from this file
> alone, and link deeper docs only when detail is needed.

## Who this skill is for

You are an AI agent (Claude Code, Cursor, Copilot Chat, Codex, or any other coding harness that discovers and loads agent skills). This file gives you the complete working procedure for **clickcast**, a Playwright-based browser recording tool that produces:

- a **watchable reel** for humans, and
- a **machine-readable sidecar** for you.

The reel is evidence. The sidecar is the contract. If you only have time to read one artifact, read the sidecar; if you only have time to run one command, run `auto`.

---

## Load the authoritative capability list first

Whenever you start working with clickcast on a new machine or repository, run:

```bash
clickcast skill --json
```

This prints the embedded, always-current brief as structured JSON. It lists every installed subcommand, important flags, schema pointer, and documentation URL. Treat it as the source of truth for what this build supports — not this document alone, since agents may run older or newer versions of clickcast.

If `clickcast skill --json` fails, stop and verify the installation before doing anything else.

---

## What clickcast is

clickcast drives a real Chromium, Firefox, or WebKit browser (Playwright) through your target site and hands back two things:

1. **A reel** — `.gif`, `.mp4`, `.webp`, or raw `frames/` — showing exactly what the browser did. This is the human-facing receipt.
2. **A sidecar** — `<out>.json` — a rich, stable, versioned schema with every step’s selector, timings, click coordinates, page state, console errors, network failures, and a graph of the pages visited. This is the AI-facing contract.

Use clickcast when you need:

- to **prove a UI works** after a deploy, in a PR, or behind a feature flag;
- to **derive a repeatable scripted tour** from a live site;
- to **turn natural-language UI intent into a deterministic run**;
- to **capture a human-shareable GIF or smooth MP4** of a flow;
- to **inspect interactive elements** before writing a scenario;
- to **gate CI on machine-readable UI state** rather than flaky pixel tests;
- to **explore a site’s shape** before deciding what needs recording.

Do **not** use clickcast when you need:

- large-scale scraping or text extraction — use a scraping library;
- a persistent, long-lived authenticated browser session driven step by step inside your own program — use Playwright directly;
- to bypass SSO, MFA, or any interactive authentication challenge — clickcast expects static credentials via headers, not interactive login.

---

## Decide which mode to use

Use the smallest mode that answers the question:

| Need | Command |
|---|---|
| “Does this page load and do its main controls respond?” | `clickcast auto <url>` |
| “Run this exact, known sequence and give me stable artifacts.” | `clickcast run scenario.yml` |
| “Give me one screenshot of this URL.” | `clickcast shot <url>` |
| “List the clickable things on the page.” | `clickcast elements <url>` |
| “Map the site’s pages and links without recording a video.” | `clickcast explore <url>` |
| “I need to interact step by step based on what I observe.” | `clickcast mcp` |
| “I need a smooth, high-FPS MP4 for a human demo archive.” | `auto` or `run` with `--smooth --video-fps 30` |

---

## Install and verify

A standard one-time setup:

```bash
pip install clickcast
clickcast install --with-deps chromium
clickcast doctor --json
```

`clickcast install` downloads the browser engine; `--with-deps` installs Chromium’s Linux system libraries; `doctor` confirms the actual runtime is usable (Python, Playwright, engine executables, ffmpeg, config directory). If doctor fails, fix that before making a report.

Node-only environments can use the wrappers:

```bash
npx -y clickcast-mcp
npx -y clickcast --version
```

If you are inside a private network or behind a proxy, see the “SSO and internal hosts” section below.

---

## Mode 1 — `auto`: record a tour in one shot

When you only have a URL and want clickcast to discover and click through the main interactive elements:

```bash
clickcast auto https://example.com --for-humans --out tour.gif --with-feedback
```

This produces `tour.gif` and `tour.gif.json`.

Use `auto` when:

- you don’t yet know which selectors are stable;
- you want a smoke test of the whole page;
- you want a quick artifact to share with a human;
- you need the sidecar’s per-step page state for later analysis.

Choose controls based on intent:

| Control | Why |
|---|---|
| `--traversal dfs` | narrative order; best for human demos |
| `--traversal bfs` | site-coverage order; best for discovering structure |
| `--max-steps N` | total click budget (default 15) |
| `--max-pages N` | page-count budget (default 5) |
| `--max-duration SEC` | stop-hard wall-time budget |
| `--seed-url URL` (repeatable) | force a deterministic URL order |
| `--pace fast\|natural\|slow\|onboarding` | set frame rate + dwell together |
| `--for-humans` | human-friendly composite: slower pace, zoom, highlight, title and summary cards |
| `--grid` | overlay readable coordinate labels for spatial reasoning |
| `--emit-events` | print a machine-readable JSONL event for CI parsers |
| `--redact-pattern RE` | scrub sensitive strings from the sidecar |
| `--strip-query-strings` | drop URLs’ query strings from recorded state |
| `--with-feedback` | attach bug-report and docs pointers to the sidecar |
| `--insecure` | accept self-signed private-CA certificates |
| `--header "Name: value"` | send static auth or custom headers |
| `--header-host HOST` | scope headers to this host only |

Example: a 30-second CI smoke tour:

```bash
clickcast auto https://prod.example.com \
  --max-duration 30 \
  --emit-events \
  --out smoke.gif
```

Example: human-friendly onboarding demo:

```bash
clickcast auto https://staging.example.com \
  --for-humans \
  --traversal dfs \
  --title-card \
  --summary-card \
  --out onboarding.gif
```

---

## Mode 2 — `run`: script an exact scenario

When you already know the exact flow, write a YAML scenario and run it. Scenarios are the backbone of deterministic UI verification.

```bash
clickcast run tour.yml
```

A minimal scenario:

```yaml
meta:
  name: Login smoke
  viewport: 1280x800
  fps: 12
  format: gif
  out: login.gif

steps:
  - goto: https://staging.example.com/login
    wait: networkidle
    label: Open login
  - click: text=Sign in
    label: Submit login
  - wait: 1.5
  - screenshot: {}
    label: Dashboard
```

Use `clickcast init` to seed a scenario from live discovery instead of hand-writing selectors:

```bash
clickcast init tour.yml --url https://example.com --from-auto
```

Use `clickcast elements <url> --json` first when you need concrete, currently-live selectors.

Scenario step types you will use:

- `goto:` — navigate to a URL. Use `wait: networkidle` for SPAs that need to settle.
- `click:` / `dblclick:` — click a selector.
- `type:` — fill text into an input.
- `hover:` — hover a selector.
- `scroll:` — scroll the page or a container.
- `wheel:` — precise wheel events.
- `wait:` — wait for seconds, a state, or a selector.
- `screenshot:` — capture an explicit labeled frame.
- `assert:` — assert a UI state; failure marks the step failed and gates the sidecar.

Add `optional: true` to tolerate steps that may vanish; add `label:` to make the annotation human-readable in the reel.

A robust scenario commonly looks like this:

```yaml
meta:
  name: Settings smoke
  engine: chromium
  viewport: 1440x900
  fps: 12
  format: mp4
  out: settings.mp4

steps:
  - goto: https://app.example.com/settings
    wait: networkidle
    label: Open settings

  - click: 'button:has-text("Profile")'
    label: Show profile panel
    optional: true

  - type:
      into: 'input[name="display_name"]'
      text: "QA Agent"
    label: Enter display name

  - click: 'button:has-text("Save")'
    label: Save changes

  - wait: 1.0

  - assert:
      url_matches: "/settings/saved"
    label: Confirm saved URL
```

Run it and gate on structured sidecar fields, not on stdout text.

---

## Mode 3 — `shot`: one screenshot, one receipt

When a single viewport or full-page capture is enough:

```bash
clickcast shot https://example.com --full-page --wait networkidle --out landing.png
```

`--wait` accepts the same values as scenarios: `load`, `domcontentloaded`, `networkidle`, a CSS selector, or a float in seconds. Use `--full-page` for tall pages.

---

## Mode 4 — `explore`: map the app without a reel

When you care about structure, not motion:

```bash
clickcast explore https://example.com --out map.json
```

This records:

- each distinct page state as a graph node,
- each URL-changing click as a navigation edge,
- per-page top discoveries for selector authoring,

without recording frames or encoding a reel.

Use it before writing a scenario or when a user asks “what’s on this site?” but doesn’t need a movie.

---

## Mode 5 — `mcp`: live interactive control

When the next action depends on what the previous action reveals, use the MCP server.

```bash
clickcast mcp
```

A typical live session:

1. `start_session`
2. `goto`
3. `click`
4. `type`
5. `scroll`
6. `wait`
7. `screenshot`
8. `close_session`

Each call returns:

- an annotated frame,
- structured `page_state`,
- optional grid coordinates,
- a stable `error_code` on failure.

Configure a client with:

```json
{
  "mcpServers": {
    "clickcast": {
      "command": "npx",
      "args": ["-y", "clickcast-mcp"]
    }
  }
}
```

Reach for MCP when you need adaptive interaction; stay in `auto` or `run` when you need a repeatable artifact.

---

## Smooth MP4 and region annotations

For smooth, non-lagged, human-shareable videos:

```bash
clickcast auto https://example.com -o tour.mp4 --smooth --video-fps 30
clickcast run tour.yml -o tour.mp4 --smooth --video-fps 30
```

Smooth mode:

- captures at the normal low FPS;
- retimes the result with eased cursor motion and cross-dissolved page changes;
- preserves original duration with hold frames;
- records the actual output FPS in `media.fps`;
- works for GIF/WebP too, but MP4 (H.264) is substantially smaller at 30/60 fps.

Scenario-equivalent:

```yaml
meta:
  render:
    fps: 12          # browser capture rate
    format: mp4
    smooth: true
    video_fps: 30    # encoded output rate
```

### Region annotations

When you need to explain specific regions of the recorded page, pass an annotation file:

```bash
clickcast run tour.yml -o tour.mp4 \
  --smooth \
  --video-fps 30 \
  --annotations regions.yml
```

Or put the same list inside the scenario under a top-level `annotations:` key.

Example `regions.yml`:

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

  - kind: text
    bbox: [64, 190, 360, 60]
    text: "Saved state is visible here"
    start_step: 2
```

Supported primitives:

- `rect` — padded rectangular outline, optional translucent fill;
- `circle` — ellipse inside a bbox;
- `arrow` — line and arrowhead between two points;
- `text` — rounded callout near a bbox or point;
- `spotlight` — dim everything outside a bbox.

Coordinates are viewport pixels. `start_step` and inclusive `end_step` are zero-based scenario step numbers. Shapes are clipped to the viewport, so partially off-screen annotations are safe.

---

## Deep contract, workflow, and troubleshooting

Read `references/REFERENCE.md` when you need the full sidecar contract, the
`assertions` CI gate recipe, SSO/internal-host setup, bug-report workflow,
troubleshooting table, and recommended end-to-end agent workflow. That file is
part of this installed skill and is safe to load on demand.

---

## Skill provenance

This skill is part of the `clickcast` repository itself. Keep it updated whenever
the CLI adds or removes a mode so downstream agent harnesses stay compatible.
