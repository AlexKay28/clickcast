# clickcast MCP tool schema

Design doc for `clickcast mcp` (#191, this sub-issue #192). No server code
lives here — this is the contract the implementation in `src/clickcast/mcp/`
is built against.

## Why

Batch mode (`auto` / `run`) records a whole tour, then hands back a GIF +
sidecar. An MCP server lets an agent drive one action at a time and react to
what it sees — matching `playwright-mcp`'s live-interactivity model, but with
clickcast's richer per-call payload (annotated frame, structured
`page_state`, grid coordinates, enumerated `error_code`) instead of a bare
screenshot.

## Tool ↔ action parity

Every action tool below is a thin wrapper around the matching `Step` model
in [`core/actions.py`](../src/clickcast/core/actions.py) — the MCP handler
constructs the step and calls the same `execute(step, session)` dispatcher
the CLI's `run`/`auto` commands use. This guarantees 1:1 parity: nothing in
`core/actions.py`'s step set is silently dropped, and a selector/timeout/
error behaves identically whether it ran inside a scenario or inside a live
MCP call.

| `core/actions.py` step | MCP tool | Notes |
|---|---|---|
| `GotoStep` | `goto` | |
| `ClickStep` | `click` | |
| `DblClickStep` | `dblclick` | |
| `HoverStep` | `hover` | |
| `TypeStep` | `type` | |
| `PressStep` | `press` | |
| `SelectStep` | `select` | |
| `ScrollStep` | `scroll` | |
| `WaitStep` | `wait` | |
| `ScreenshotStep` | `screenshot` | |
| `WaitForStep`, `EvaluateStep`, `WheelStep` | *(not exposed in v1)* | Scenario-only for now — no agent-facing use case surfaced yet; adding a tool is a small, additive follow-up if one does. Noted as an explicit scope decision, not an oversight. |

Two session-lifecycle tools that have no `core/actions.py` counterpart
(they manage the `Session` itself, not a step inside one):

- `start_session` — opens the Playwright session (mirrors
  [`core/opts.py`](../src/clickcast/core/opts.py)'s `BrowserOpts` fields:
  engine/viewport/device/headful/lang/dark, plus the grid overlay knobs).
  `settle_budget_ms` (0–30000, default 1500) caps the `settle()` embedded in
  every action's delta; `0` disables it (`delta.settled` is then absent). The
  explicit `settle` tool keeps its own full 3000 ms default — see below.
  `RenderOpts` (fps/quality/loop/format) is **not** mirrored here — v1 does
  not encode a reel from a live session (no video/GIF is produced), so
  frame-encoding options don't apply. See "Transcript" below for what *is*
  captured.
- `close_session` — closes the Playwright session and optionally flushes
  the accumulated sidecar-shaped transcript to disk.

`settle` and `observe` (below) are non-step tools: perception primitives
from `perception/`, not `Step` wrappers. `receipt` is the loop's closing
call — it summarizes the live transcript and writes artifacts, and does
**not** close the session.

Selector-shaped action tools also accept `ref` (an `eN` handle from
`observe`) in place of `selector`/`into` — see "`ref=` — act on an observed
element" under the per-call response shape.

## Session lifecycle

v1 is single-session, single-process (see #191 "Out of scope"): one
`clickcast mcp` process holds at most one live `Session` at a time.

1. `start_session` opens a `Session` (via the same `core/session.py` class
   every other clickcast entrypoint uses) and keeps it open across tool
   calls — it is **not** re-opened per action. Calling `start_session`
   again while a session is already open is an error (`error_code: "other"`,
   `"a session is already active — call close_session first"`).
2. Each action tool (`goto`/`click`/.../`screenshot`) drives that same
   session: it builds the matching `Step`, calls
   `clickcast.core.actions.execute(step, session, step_index=N)`, and
   returns the result (see "Per-call response shape" below). Calling an
   action tool before `start_session` returns `error_code: "other"`,
   `"no active session — call start_session first"`.
3. `close_session` tears the session down. If `save_transcript` was passed,
   the accumulated transcript (see below) is written to that path in the
   same JSON shape `feedback/builder.py` produces for a batch run.
   `redact_patterns` / `strip_query_strings` / `with_feedback` behave exactly
   as they do for `clickcast run`: an agent that drove an authenticated URL
   can scrub tokens before the transcript hits disk.

### Transcript accumulation

Decision (per this issue's "decide whether..." prompt): **yes, always
accumulate**. `start_session` attaches a `feedback.builder.ReportBuilder` to
the session exactly like `auto`/`run` do, so every action tool call is
recorded as a `StepReport` (same shape, same `error_code`/`skip_reason`
enums) with zero extra cost — the collector is already wired for the
per-call `page_state` in the tool response, so building the full sidecar
list is free. Whether it hits disk is controlled by `close_session`'s
optional `save_transcript` argument — most live sessions are exploratory
and don't need one, so nothing is written unless asked. When a path is
given, `close_session` writes through the shared kernel sidecar writer
(`clickcast.kernel.sidecar`), so the artifact is a first-class sidecar — just
without an accompanying media file (no GIF/video is recorded in a live
session; `media.format` is `"none"`, `media.path` is empty, and
`media.frame_count` is the number of recorded steps).

Transcripts carry the same fields a batch sidecar does: the report-level
`url` is the first `goto` target, and each step's `frames` list points at the
PNG that tool call returned.

## Per-call response shape

Every action tool (`goto` through `screenshot`) returns an MCP
`CallToolResult` with two content blocks on success:

1. `ImageContent` (`mimeType: "image/png"`) — the just-captured frame,
   annotated via the same [`annotate/annotator.py`](../src/clickcast/annotate/annotator.py)
   `Annotator` the batch pipeline uses (cursor marker + trail, click ripple
   on the action's target, action label, and the pixel-grid overlay when
   enabled). The per-tour-only layers (`progress`, `actions_panel`) are off
   — they need whole-tour context a single live call doesn't have.
2. `TextContent` (`text/plain`, JSON-encoded) — the structured result:

```jsonc
{
  "ok": true,               // mirrors ActionResult.ok
  "status": "ok",           // "ok" | "failed" (skipped steps don't occur here — MCP
                             // tool calls never set `optional: true`)
  "action": "click",        // step.action
  "selector": "text=Buy",   // resolved selector/target, when applicable
  "duration_ms": 42.1,
  "cursor_xy": [412, 208],  // resolved click/hover/type target center, or null
  "error": null,
  "error_code": null,       // see "Error modes" below
  "skip_reason": null,      // always null in v1 (no optional steps)
  "page_state": {           // same shape as feedback/models.py PageState
    "title": "Example",
    "url_after": "https://example.com/",
    "console_errors": [],
    "page_errors": [],
    "network_failed": []
  },
  "delta": {                // v5 (#264) — what this action changed; null on
                            // capture failure, never absent
    "url": "https://example.com/",
    "title": "Example",
    "added": [{"ref": "e7", "role": "button", "name": "Details"}],
    "text_changes": ["+ Details content — hidden until Details was clicked."],
    "settled": {"ok": true, "ms": 812}
  },
  "grid": {"pitch": 100, "style": "full", "color": "#FFFFFF33"}  // present only when the grid overlay is on
}
```

### `ref=` — act on an observed element

Every selector-shaped action tool (`click`, `dblclick`, `hover`, `type`,
`press`, `select`, `scroll`) accepts `ref` as an alternative to its
`selector`/`into` argument:

```jsonc
{"ref": "e7"}            // instead of {"selector": "role=button[name=\"Details\"]"}
```

The handle is resolved through `perception/refs.py` (the same registry
`observe` populates). Passing both `selector` and `ref` is an `other`
error; an unknown ref is an `other` error (`unknown ref 'e9' — call observe
for current refs`); a ref whose element disappeared is an `other` error
that says it was removed. Neither ever surfaces a traceback.

### `delta` — what changed

`_run_step` observes the page before the action, runs it, waits for
`settle`, then observes again and diffs the pair (see
`perception/delta.py`). The result is returned as `delta` on every action
tool response **and** written to the matching `steps[].delta` entry when
`close_session` saves a transcript (schema v5). Batch `auto`/`run` sidecars
carry `steps[].delta: null` — they do not capture a before-observation.

Caps and semantics (see `docs/feedback-schema.md` §`steps[].delta`): at most
5 `added`/`removed`/`text_changes` entries and 3 `console_errors` /
`network_failed`; empty blocks are omitted; node identity is the 64px
`dom_signature` with a ref fallback, so jitter and remounts do not churn;
the serialized block is ≤300 estimated tokens. `settled` is the compact
`{ok, ms}` verdict; the full per-signal breakdown remains on `settle`.

On failure, `CallToolResult.isError` is `true`, the `ImageContent` block is
still included when a frame could be captured (best-effort — a crashed page
may not yield one), and the `TextContent` JSON carries `ok: false`,
`status: "failed"`, `error` (human-readable), and a populated `error_code`.
Only genuinely unexpected server bugs bypass this and fall back to a bare
`error_code: "other"` text-only error result — no raw Python traceback is
ever handed to the client.

`start_session` / `close_session` return a single `TextContent` block (no
frame — there is no page yet / the page is gone) with a small JSON payload
(`{"ok": true, "engine": ..., "viewport": [...], "headful": ...}` and
`{"ok": true, "closed": true, "transcript_path": ...}` respectively).

## Error modes

`error_code` is the exact enum `feedback/models.py` already defines —
reused verbatim, not re-implemented:

| `error_code` | When |
|---|---|
| `timeout` | Playwright timeout waiting for a selector / navigation / load state. |
| `locator_missing` | Selector parsed but resolved to 0 elements. |
| `cross_origin` | Cross-origin bounce / detached frame / closed target mid-action. |
| `navigation_error` | `goto` failed for a reason other than timeout (DNS, connection refused, non-2xx that raises). |
| `selector_ambiguous` | Selector resolved to >1 candidate where clickcast's hint layer refuses to guess. |
| `other` | Anything else — including the two session-lifecycle usage errors (no active session / session already active) and any genuinely unexpected server-side exception. |

Classification comes from `core/actions._classify_error`, the same function
the sidecar builder relies on — one classification table, two consumers.

## Full tool reference

### `start_session`

| | |
|---|---|
| Args | `engine` (`str`, default `"chromium"`), `viewport` (`str` `"WxH"`, default from CLI/config), `device` (`str \| null`), `headful` (`bool`, default `false`), `lang` (`str \| null`), `dark` (`bool`, default `false`), `grid` (`bool`, default from CLI/config), `grid_pitch` (`int`), `grid_color` (`str` RGBA hex), `grid_style` (`"full" \| "ruler"`) |
| Returns | `{"ok": true, "engine", "viewport": [w, h], "headful"}` |
| Errors | `other` — a session is already active; `other` — Playwright failed to launch (missing browser install, bad device preset, etc). |

### `close_session`

| | |
|---|---|
| Args | `save_transcript` (`str \| null` — filesystem path; when set, writes the accumulated sidecar-shaped transcript there) |
| Returns | `{"ok": true, "closed": true, "transcript_path": str \| null}` |
| Errors | `other` — no active session. |

### `goto`

| | |
|---|---|
| Args | `url` (`str`), `wait` (`str \| float \| null`, e.g. `"networkidle"` or seconds), `retries` (`int`, default `0`), `label` (`str \| null`) |
| Returns | Per-call response shape above; `selector` is `null`. |
| Errors | `timeout`, `navigation_error`, `cross_origin`, `other`. |

### `click` / `dblclick`

| | |
|---|---|
| Args | `selector` (`str`), `wait` (`str \| float \| null`, e.g. `"networkidle"` or seconds — blocks after the click, same semantics as `goto`'s `wait`; use for a click that triggers client-side/SPA navigation with no full page load, #226), `timeout_ms` (`int \| null`), `label` (`str \| null`) |
| Returns | Per-call response shape; `cursor_xy` is the resolved target's center. |
| Errors | `timeout`, `locator_missing`, `cross_origin`, `selector_ambiguous`, `other`. |

### `hover`

| | |
|---|---|
| Args | `selector` (`str`), `timeout_ms` (`int \| null`), `label` (`str \| null`) |
| Returns | Per-call response shape; `cursor_xy` is the resolved target's center. |
| Errors | `timeout`, `locator_missing`, `cross_origin`, `selector_ambiguous`, `other`. |

### `type`

| | |
|---|---|
| Args | `into` (`str`, selector), `text` (`str`), `delay` (`float`, default `0.0`, per-key ms delay), `timeout_ms` (`int \| null`), `label` (`str \| null`) |
| Returns | Per-call response shape; `selector` is `into`. |
| Errors | `timeout`, `locator_missing`, `cross_origin`, `other`. |

### `press`

| | |
|---|---|
| Args | `key` (`str`, e.g. `"Enter"`), `selector` (`str \| null` — page-level keyboard when omitted), `timeout_ms` (`int \| null`), `label` (`str \| null`) |
| Returns | Per-call response shape. |
| Errors | `timeout`, `locator_missing`, `other`. |

### `select`

| | |
|---|---|
| Args | `into` (`str`, selector), `value` (`str \| list[str]`), `timeout_ms` (`int \| null`), `label` (`str \| null`) |
| Returns | Per-call response shape; `selector` is `into`. |
| Errors | `timeout`, `locator_missing`, `other`. |

### `scroll`

| | |
|---|---|
| Args | `to` (`str \| null`, selector to scroll into view), `by` (`int \| null`, pixels), `selector` (`str \| null`, container to scroll when using `by`), `dx` (`int`, default `0`), `label` (`str \| null`) |
| Returns | Per-call response shape. |
| Errors | `timeout`, `locator_missing`, `other` (including "neither `to` nor `by` given"). |

### `wait`

| | |
|---|---|
| Args | `wait` (`str \| float` — a load state, a selector, or seconds), `label` (`str \| null`) |
| Returns | Per-call response shape; `selector`/`cursor_xy` are `null`. |
| Errors | `timeout`, `other`. |

### `screenshot`

| | |
|---|---|
| Args | `full_page` (`bool`, default `false`), `label` (`str \| null`) |
| Returns | Per-call response shape — the primary payload IS the frame; `status` is `"ok"` unless the page itself is unreachable. `delta` is always `null`: a screenshot cannot change the page, so it skips the per-action observations and settle (#297). |
| Errors | `other`. |

### `settle`

Not a `core/actions.py` step — a perception primitive (#265) exposed directly
from [`perception/settle.py`](../src/clickcast/perception/settle.py).

| | |
|---|---|
| Args | `budget_ms` (`int \| null`, default `null` → 3000 ms overall) |
| Returns | `{"ok": bool, "ms": int, "signals": {name: {"ok", "ms", "budget_ms", ...}}, "timed_out": [name, ...]}` — no frame. |
| Errors | `other` — no active session (a timed-out signal is **not** an error; see below). |

Three independent signals are polled concurrently, each with its own quiet
window and budget:

| Signal | Quiet window | Budget |
|---|---|---|
| `network` | 500 ms without a request event (own in-flight counter — not `networkidle`) | 3000 ms |
| `dom` | 250 ms without a `MutationObserver` record (`window.__ccMut`) | 2000 ms |
| `raf` | 250 ms without a page-issued `requestAnimationFrame` and no `document.getAnimations()` entries | 2000 ms |

`budget_ms` is a hard ceiling on the whole call: each per-signal budget is
clamped to it. `ok` is `true` iff `timed_out` is empty. A signal that
exhausts its budget is reported in `timed_out` and the tool still returns
successfully (`isError` is `false`) — an infinite rAF loop therefore answers
`{"ok": false, ..., "timed_out": ["raf"]}`, which is an observation the agent
can act on, not a broken call. A page that mutated for ~1 s and stopped
settles with `ok: true` around 1.3 s. Earlier engines without
`document.getAnimations` lose only the CSS-animation half of the `raf`
signal (`signals.raf.supported` is `false`); `ok` can still be `true`.


### `observe`

| | |
|---|---|
| Args | `token_budget` (`int`, default `800`) |
| Returns | **Text only** (no image/screenshot) — one `TextContent` block containing the `observe/1` snapshot below. |
| Errors | `other` — no active session; `other` — a non-positive budget. |

`observe` is the perception counterpart to the image-returning action tools:
it answers "what can I act on, compactly?" without an annotated PNG and
without asking the agent to carry raw selectors. It has no `core/actions.py`
step counterpart (it does not mutate the page), so it is listed separately
from the parity table above.

```jsonc
{
  "schema": "observe/1",
  "url": "https://example.com/",
  "title": "Example",
  "viewport": [1280, 800],
  "nodes": [
    {"ref": "e3", "role": "button", "name": "Buy now",
     "bbox": [890, 612, 120, 40], "state": {"disabled": false}}
  ],
  "text_hash": "a1b2c3d4e5f60718",   // sha256(body.innerText)[:16]
  "truncated": {"omitted": 6, "total": 9},
  "tokens": {"budget": 800, "estimated": 312},
  "removed": ["e2"]                   // refs whose elements disappeared since the last observe
}
```

- **Ranking**: interactive role → visible + fully in viewport → the
  discovery `_score` → y, then x. Node names are truncated to 40 characters
  and at most 20 nodes ship.
- **Budget**: `tokens.estimated` is `ceil(len(compact_json)/4)` measured on
  the exact serialized payload the tool returns (the fixed point is
  computed in `perception/tokens.py`), with a hard `3200`-character ceiling
  for the default 800 budget. Nodes are dropped lowest-ranked-first until
  both limits hold. `len(nodes) + truncated.omitted == truncated.total`.
- **Refs**: session-scoped opaque handles (`eN`). Identity is
  role + accessible name, tie-broken by the 64px bbox bucket from
  `feedback/graph.py` (`dom_signature`), with a document-order ordinal for
  otherwise-identical duplicates. Refs are stable across observations and
  across in-page layout shifts.
- **`removed`**: a ref that was alive in the previous observation but whose
  element no longer appears anywhere in the page's interactive candidate set
  transitions to removed and is listed **once**. Hidden-but-present elements
  are simply absent from `nodes`; they are not `removed`.
- **Latency (#297).** The per-action `settle()` uses
  `start_session(settle_budget_ms=…)` (default **1500 ms**, configurable,
  `0` disables) rather than the explicit tool's 3000 ms: it only has to answer
  "had the page settled?" for the delta, and a timed-out embedded settle is
  still information (`settled.ok: false`). `screenshot` performs **no**
  observation and **no** settle — a screenshot cannot change the page, so its
  response carries `delta: null` instead of paying for an always-empty diff.
- Playwright 1.64's native `page.get_by_ref()` is **not** used —
  `playwright>=1.49` remains the supported floor. Native refs are documented
  as a future fast-path behind the same `ref=eN` handles.

`observe` does not append a step to the transcript (it is a read, not an
action), so it never changes the sidecar shape written by `close_session`.

### `receipt`

The closing call of the perception loop (#271): `observe → act → settle →
receipt`. Summarizes the steps recorded so far into a ≤200-token `receipt/1`
payload and writes one artifact directory.

| | |
|---|---|
| Args | `media` (`bool`, default `false` — encode the recorded frames as a playable `tour.mp4` through the shared render kernel), `out` (`str \| null` — artifact directory; defaults to `<session frames>/receipt`, which `close_session` deletes, so pass a path to keep the artifacts), `baseline` (`str \| null` — assertions JSON to measure drift against), `baseline_sidecar` (`str \| null` — sidecar to pixel-diff against, adding `visual_diff`) |
| Returns | **Text only** — one `TextContent` block with the `receipt/1` summary below. |
| Errors | `other` — no active session. |

```jsonc
{
  "schema": "receipt/1",
  "media": {"path": "tour.mp4", "duration_s": 14.2, "frames": 170},  // only with media=True
  "sidecar": "tour.mp4.json",        // relative to the artifact directory
  "sidecar_version": 5,
  "steps": {"ok": 6, "failed": 1, "skipped": 0},
  "top_error": {"step": 4, "error_code": "timeout"},  // omitted when nothing failed
  "assertions": {"drift": 2, "baseline": "baseline.json"},  // only when baseline was given
  "visual_diff": {"changed_steps": [3], "max_changed_pct": 6.4},  // only with baseline_sidecar
  "tokens": {"budget": 200, "estimated": 96}
}
```

- **Token budget**: `tokens.estimated` is `ceil(len(compact_json)/4)` of the
  exact returned payload (the `observe` estimator in `perception/tokens.py`),
  and is enforced in production code — optional blocks are dropped
  (`visual_diff` first, then `assertions`) until the summary fits 200.
- **Omissions**: blocks with nothing to report are absent. `assertions` and
  `visual_diff` only appear when a baseline was supplied; `top_error` only
  when a step failed.
- **`top_error`**: `step` is the 0-based position of the **first**
  `status: "failed"` step; `error_code` is that step's enumerated code (the
  same values as "Error modes"), never the free-text `error`.
- **Artifacts**: the sidecar (schema v5), `assertions.json`, a copy of the
  baseline, `visual-diff/`, and `receipt.json` all live in `out`. Details
  (per-step counters, page state, deltas, frames) live in the sidecar, not
  the summary.
- **No session change**: `receipt` is a read. It does not append a step and
  does **not** close the session — call `close_session` when done.

## Confirmed against `core/actions.py`

- Every `Step` subclass except `WaitForStep`, `EvaluateStep`, `WheelStep` has
  a 1:1 tool (explicit scope decision above, not an omission).
- `error_code` values are imported from the same enum
  (`feedback/models.py::ErrorCode`) — no parallel definition.
- `page_state` is `feedback/models.py::PageState`, populated by the same
  `feedback/collector.py::PageStateCollector` batch mode uses — no
  duplicate model.
- The annotated frame reuses `annotate/annotator.py::Annotator` and
  `annotate/grid.py::GridConfig` directly — no new image-compositing code.
