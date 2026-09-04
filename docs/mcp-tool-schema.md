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
  `RenderOpts` (fps/quality/loop/format) is **not** mirrored here — v1 does
  not encode a reel from a live session (no video/GIF is produced), so
  frame-encoding options don't apply. See "Transcript" below for what *is*
  captured.
- `close_session` — closes the Playwright session and optionally flushes
  the accumulated sidecar-shaped transcript to disk.

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
given, `close_session` calls the same `feedback.write()` used by every
other clickcast entrypoint, so the artifact this produces is a first-class
sidecar (schema v3) — just without an accompanying `media` (no GIF/video is
recorded in a live session; `media.format` is `"none"` and `media.path` is
empty in that written sidecar).

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
  "grid": {"pitch": 100, "style": "full", "color": "#FFFFFF33"}  // present only when the grid overlay is on
}
```

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
| Returns | Per-call response shape — the primary payload IS the frame; `status` is `"ok"` unless the page itself is unreachable. |
| Errors | `other`. |

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
