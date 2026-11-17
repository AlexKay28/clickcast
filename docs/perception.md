# Perception loop: `observe → act → settle → receipt`

An agent driving a browser needs four answers, in this order:

| Step | Primitive | Question it answers | Where |
|---|---|---|---|
| 1 | `observe` | *What can I act on, and what is it called?* | `perception/observe.py`, MCP `observe` |
| 2 | *act* | *Do the thing.* | `core/actions.py`, MCP `goto`/`click`/… |
| 3 | `settle` | *Has the page stopped changing?* | `perception/settle.py`, MCP `settle` |
| 4 | `receipt` | *Did it work, what broke, where are the details?* | `perception/receipt.py`, MCP `receipt`, `Reel.receipt()` |

Each primitive is token-bounded on purpose: an agent can run dozens of
iterations without its context filling with DOM snapshots, frame paths and
per-step console logs. The bulk of the evidence is written to disk; the
model only reads the summary.

## 1. `observe` — act on handles, not selectors

`observe(session, token_budget=800)` returns an `observe/1` payload of the
page's interactive elements addressed by stable opaque `ref=eN` handles,
ranked by role → in-viewport → discovery score → position. Refs survive
re-renders and layout shifts; refs whose elements disappeared are reported
once in `removed`. See `docs/mcp-tool-schema.md` §`observe` for the shape.

## 2. act — one step, one delta

Every MCP action tool captures the page before and after the action and
returns a `delta` block (schema v5, `perception/delta.py`): added/removed
nodes, text changes, console/network errors, a `layout_shift` flag, and the
`{ok, ms}` settle verdict. Node identity is a 64px `dom_signature` with a
ref fallback, so jitter and remounts do not churn.

## 3. `settle` — quiescence, not a sleep

`settle(session, budget_ms=3000)` polls three independent signals —
in-flight network requests, DOM mutations (`MutationObserver`) and
compositor activity (page-issued `requestAnimationFrame` + running CSS
animations) — each with its own quiet window. A signal that exhausts its
budget is named in `timed_out`; the call still succeeds, because "the page
never stopped moving" is an observation, not an error.

## 4. `receipt` — the closing call

`receipt` summarizes the run into one artifact directory and one small JSON
payload.

```python
from clickcast import Reel

summary = (
    Reel("https://example.com", viewport=(1280, 800))
    .goto(wait="load")
    .click("text=Compare")
    .receipt("out/tour", media=True, baseline="tests/golden/baseline.json")
)
```

```json
{"schema":"receipt/1",
 "media":{"path":"tour.mp4","duration_s":14.2,"frames":170},
 "sidecar":"tour.mp4.json","sidecar_version":5,
 "steps":{"ok":6,"failed":1,"skipped":0},
 "top_error":{"step":4,"error_code":"timeout"},
 "assertions":{"drift":2,"baseline":"baseline.json"},
 "visual_diff":{"changed_steps":[3],"max_changed_pct":6.4},
 "tokens":{"budget":200,"estimated":96}}
```

- **≤200 tokens, enforced here.** `tokens.estimated` is
  `ceil(len(compact_json)/4)` measured on the exact returned payload with
  the same estimator as `observe`. The production code drops optional blocks
  (`visual_diff`, then `assertions`) until the summary fits.
- **Omit, don't pad.** `assertions` appears only when a baseline was
  diffed, `visual_diff` only when a baseline sidecar was compared,
  `top_error` only when a step actually failed.
- **`top_error`** is the 0-based position of the *first* `status: "failed"`
  step plus its enumerated `error_code`. Gate on that code, not on `error`
  text.
- **One directory, relative paths.**

```
out/tour/
  tour.mp4           # media=True: encoded by the render kernel
  tour.mp4.json      # v5 sidecar — the details live here
  assertions.json    # CI-stable distillation of the sidecar
  baseline.json      # copy of the baseline drift was measured against
  visual-diff/       # region-highlighted diff PNGs + summary.json
  receipt.json       # the summary above
```

Media is always encoded through `kernel/render.render_reel` — the one
pipeline (zoom → smooth/interpolate → annotate → encode) the CLI, the SDK
and the MCP server share. `perception/receipt.py` never calls `encode()`
and the architecture test enforces that.

From a live MCP session, `receipt(media=True, out="...")` does the same for
the frames recorded so far, and deliberately does **not** close the session:
call `close_session` when the run is actually over. If `out` is omitted the
artifacts land in the session's temporary frame directory, which
`close_session` removes.
