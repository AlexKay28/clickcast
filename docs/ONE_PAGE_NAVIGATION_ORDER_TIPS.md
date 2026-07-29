# One-page navigation & step-order tips for human-legible reels

## What this doc is for

If you want a clickcast reel that a **human can follow without pausing or
scrubbing**, you need to author the scenario differently than if you were
optimizing for an AI-agent parsing the sidecar. Same tool, same output
format, different scenario-design discipline.

The `docs/demo.gif` shipped in this repo went through several iterations
before landing on the current tailwindcss.com scripted tour. This doc
captures why *that* tour reads well to a human, and how to reproduce the
pattern for your own reels.

---

## The one-line rule

> **One page. Every click has a visible reaction under the cursor. Every
> opened thing is closed. Every action is explicit.**

If your scenario obeys that rule, a first-time viewer can watch the reel
end-to-end and narrate what happened. If it breaks the rule, they can't.

---

## Why the tailwindcss.com tour reads well

Concrete comparison against the previous react.dev *auto*-generated tour:

| Property | react.dev auto tour | tailwindcss scripted tour |
|---|---|---|
| Number of pages visited | 3 (landing → /versions → /community/translations) | 1 |
| Scene cuts | ~4 (each nav + each `go_back()`) | 0 |
| Cross-origin bounces | 1 (click "Open on GitHub" → bail → back) | 0 |
| Clicks with visible on-page effect | 1/5 (only dark-mode toggle) | 2/2 (dropdown + overlay) |
| Symmetric open→close pairs | 0 | 3 (dropdown+Esc, overlay+Esc, scroll↓+scroll↑) |
| Label matches what's on screen | frame-off for 2/5 clicks | 7/7 |

Every one of those column-shifts is a step *toward* human legibility. The
principles below are the underlying reasons.

---

## The nine principles

### 1. Stay on one page

Every navigation is a scene cut. A scene cut is exactly the moment where a
viewer loses the through-line. If you must visit multiple pages, make the
navigation feel *deliberate* (see principle 7) instead of a byproduct of
clicking a nav link.

**How:** use scenario YAML, not `clickcast auto`. `auto` clicks whatever
`discover()` finds — usually the top nav — and every top-nav link
navigates. Even with `--max-pages 1`, `auto` will `page.go_back()` between
clicks, which flashes the previous page for a fraction of a second and
reads as a bug.

### 2. Every click must produce a visible on-page reaction under the cursor

If the cursor is at (400, 100) when you click and the reaction happens at
(400, 100)-ish — a dropdown opens, a menu expands, a button highlights, an
icon animates — the viewer's eye is already there. Perfect legibility.

If the reaction happens somewhere else entirely (a modal appears in the
center, a toast pops up in the corner), the viewer has to search for what
changed. Still watchable, but slower.

If the reaction happens on a *different page* (navigation), the viewer
starts over. Bad.

**How:** pick interactions that mutate the DOM in place — dropdowns,
disclosure widgets, tabs, hover panels, keyboard shortcuts that open
overlays, form field focus states.

### 3. Every opened thing must be closed

Symmetric pairs. Open a dropdown → close it. Open an overlay → close it.
Scroll down → scroll back up. Expand an accordion → collapse it.

Without symmetry the reel accumulates "hanging state" — a dropdown that
never closed, a modal still open at the end. Reads as unfinished.

With symmetry the reel has a natural rhythm: outward motion + return
motion. Each interaction feels *complete* before the next starts.

**How:** for every `- click: ...` that opens something, add a
`- press: {key: Escape}` step after the dwell. For every `- scroll: {by: N}`,
add a `- scroll: {by: -N}` later. Label the closing step `"Close (symmetric)"`
so the actions panel narrates the intent.

### 4. Explicit step labels that describe intent, not mechanics

Auto-tour labels look like `click · React`. Mechanically correct, but a
human reading the label banner has to reverse-engineer *why* you clicked.

Scripted labels look like `Open version dropdown` / `Close (symmetric)` /
`Scroll back up`. Instantly tells the viewer what to look for on screen.

**How:** always set `label:` on every scenario step. Write labels the way
you'd narrate the tour aloud.

### 5. Predictable dwell timing — give the eye time to catch up

Every step needs enough dwell for a human to (a) see the cursor arrive,
(b) see the reaction, (c) absorb it before the next step starts.
`dwell: 1.2` is the minimum. `2.0–2.5` is comfortable for opening
overlays that need time to render.

The default `--pace onboarding` (8 fps, 1.2 s dwell) is a good baseline.
Bump individual step dwells higher for anything with animation.

**How:** set `dwell:` per step in the YAML. Slow steps (search overlay
opens with a fade) get `dwell: 2.5`. Fast steps (scroll) get `dwell: 1.5`.

### 6. Bounded, deliberate step count — 5–8 steps is the sweet spot

Fewer than 5 and you're not showing off enough. More than 10 and the
viewer loses the thread. 5–8 gives you room for two or three symmetric
pairs plus a scroll, and keeps the whole reel under ~12 seconds.

**How:** don't try to demo everything in one tour. Pick a specific story
(e.g. "the search + version-switching UX") and cut everything that
doesn't serve it.

### 7. When you MUST navigate, make it deliberate

If your story genuinely requires visiting two pages (e.g. "landing → docs
page → back"), don't let navigation happen as a side effect of
auto-discovery. Script it explicitly with a `goto`, and give the new page
a beat to settle before doing anything on it.

**How:** `- goto: url` + `wait: networkidle` + `dwell: 2.5` on the arrival
step. That way the scene cut is a distinct, labeled beat, not a jump-cut
between two random clicks.

### 8. Pick a target with rich in-place interactions

Not every site suits a one-page tour. Docs sites like react.dev where
every nav link navigates are the *worst* case. Landing pages with
dropdowns / search overlays / demo widgets are the *best* case.

Good targets to consider (all have in-place interactions):

- **tailwindcss.com** — version dropdown, Ctrl-K search overlay, code
  playground demos.
- **stripe.com** — hover-animated dashboard preview cards.
- **linear.app** — interactive scrolling tour of features.
- Your own product's landing page — usually the best story anyway.

### 9. Use annotation overlays that a human can read

- `single_arrow=True` — one big red vector held across each step's dwell
  instead of a chain of small per-hop arrows that flash on and off.
- `panel.position="bottom-right"` on tailwindcss (or top-left on sites
  with hero content in the bottom-right corner) — anywhere but on top
  of the click targets.
- `arrow_color=(255, 0, 0, 255)` + `arrow_thickness=5` + `arrow_head_size=18`
  — bright pure red is unmissable; the shipped defaults are duller for
  everyday use.
- `arrow_max_distance=2000` — the shipped default 600 filters "teleports"
  across page navigations. In a one-page tour there are no teleports, so
  the guard is doing nothing but hiding your long-distance arrows.

---

## A template scenario

Copy, rename, adjust selectors:

```yaml
meta:
  name: "single-page product tour"
  viewport: 1280x800
  fps: 8
  dwell: 1.2
  format: gif
  out: docs/demo.gif

steps:
  # 1. Land + hold long enough for the viewer to see what site this is.
  - goto: https://your-app.example.com/
    wait: networkidle
    dwell: 3.0
    label: "Land on your-app"

  # 2-3. Symmetric pair: open, then close.
  - click: 'role=button[name="Open menu"]'
    dwell: 2.0
    label: "Open the main menu"

  - press:
      key: Escape
    dwell: 1.5
    label: "Close (symmetric)"

  # 4-5. Second symmetric pair.
  - click: 'role=button[name="Search"]'
    dwell: 2.5
    label: "Open search overlay"

  - press:
      key: Escape
    dwell: 1.5
    label: "Close (symmetric)"

  # 6-7. Show the page has more content, then return to the framing shot.
  - scroll:
      by: 500
    dwell: 1.5
    label: "Scroll down to see more"

  - scroll:
      by: -500
    dwell: 1.5
    label: "Scroll back up (symmetric)"
```

Total: 7 steps, ~11 seconds at `--pace onboarding`, 5 symmetric-pair
"actions" the viewer can follow without pausing.

---

## Anti-patterns to avoid

**Anti-pattern 1: `clickcast auto` on a docs site.** Every click will navigate
away, `go_back()` will interstitially flash the previous page, and 3+
scene cuts will land in <10 seconds of reel. The reel is technically
correct but reads as broken.

**Anti-pattern 2: click a link that opens a new tab / cross-origin site.**
`auto` bails on cross-origin, `go_back()` triggers, and the reel shows
"clicked X" while the frame content is not the X-destination. Label vs
scene mismatch — reads as buggy.

**Anti-pattern 3: click something with no visible reaction.** e.g. clicking
"React" on react.dev while already on react.dev/. The frame doesn't
change under the cursor. Viewer sees the click ripple but wonders "what
just happened?"

**Anti-pattern 4: interpolated cursor with single_arrow.** The interpolation
inserts intermediate frames where the arrow endpoints track along the
path. This produces a "walking arrow" that fights with `single_arrow`'s
"one static A→B" semantic. If you want the sticky arrow, set
`CursorStyle(interpolate=False, single_arrow=True)`.

**Anti-pattern 5: default `arrow_max_distance` on cross-nav clicks.** The
default 600 px filters teleports. If your tour clicks something in the
top-left, then something in the top-right (~1200 px apart), the arrow
between them will be silently suppressed. Either shorten the click
spread or bump `arrow_max_distance`.

---

## Companion reads

- [`.github/ISSUE_TEMPLATE/ai-agent-report.yml`](../.github/ISSUE_TEMPLATE/ai-agent-report.yml)
  and [`docs/for-agents.md`](for-agents.md) — the AI-agent counterpart. Same
  tool, different design discipline (parse-density > human-legibility).
- Issue **#129** — the "human-observable demo mode" thread this doc grew
  out of. Tracks the remaining code work (pre-click ring, symmetric-close
  auto-detection, title/summary cards baked into the tour output).
- The [`ai-eye-review`](../demo/ai-eye-review/) demo — the flagship AI-eye
  showcase. Note that its story is different: it's meant to be watched
  alongside the sidecar, not as a standalone artifact.
- The **v0 preview branch** [`preview/human-demo-v0`](https://github.com/AlexKay28/clickcast/tree/preview/human-demo-v0) — comparison GIFs
  and Pillow bookender script for prepending title / appending summary
  cards to any tour output.
