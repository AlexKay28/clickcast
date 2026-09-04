# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **npm distribution: `clickcast` (general CLI) + `clickcast-mcp` (npx-first
  MCP entry point)** (closes [#222]). `pip install clickcast` was the only
  fully-live install path; the MCP ecosystem's dominant client-config
  pattern is `npx -y <package>`, so a Python-only install was real friction
  for exactly the audience #212's `clickcast mcp` server targets. Two new
  npm packages under `npm/`, day-one distribution per this issue (same
  "verify what's live vs. what needs owner bootstrap" structure #215's
  Homebrew/apt work established):
  - **`npm/clickcast-mcp/`** — `bin/clickcast-mcp` execs straight into
    `clickcast mcp`, forwarding any trailing flags (`--grid`,
    `--viewport ...`) through, for a one-line MCP client config:
    `{"command": "npx", "args": ["-y", "clickcast-mcp"]}` — matching the
    exact shape every other MCP server's README shows.
  - **`npm/clickcast/`** — `bin/clickcast` forwards argv/stdio/exit-code
    into the full wrapped Python CLI.
  - Both share one provisioning module, `npm/shared/provision.js`: on
    `npm install`, `postinstall.js` looks for a system Python >= 3.10
    (`python3`/`python`/`py -3` on Windows), fails loudly with a specific
    fix command if none is found or it's too old (mirrors #216's
    missing-engine UX bar — never a silently broken `bin/` shim), then
    provisions an **isolated venv** under the npm package's own install
    directory (`node_modules/<pkg>/.venv`, never the user's global Python
    — same isolation principle `pipx` uses) and pip-installs an exact-pinned
    `clickcast==<version>` (`clickcast[mcp]==<version>` for the MCP
    package) into it. Chromium is still never bundled (~180MB, versioned
    independently, same reasoning as #170) — the `bin/*.js` shims use
    `stdio: "inherit"` so the underlying CLI's own #216 self-heal prompt
    passes through untouched rather than being captured or buffered.
  - Since both packages are published independently to the npm registry, a
    `file:` dependency between them would resolve fine in this monorepo but
    break for a real end user installing either package alone. Each
    package's `prepare`/`prepack` lifecycle script instead vendors a copy of
    `npm/shared/provision.js` into its own `vendor/provision.js` before
    packing/publishing, so every published tarball is genuinely
    self-contained — see `docs/packaging/npm.md` for the full rationale.
  - New `.github/workflows/npm-release.yml` builds, real-installs, and
    smoke-tests both packages against the real, already-published PyPI
    `clickcast==<version>` on every GitHub release (attaching the `.tgz`s to
    the release as a zero-setup fallback), and publishes both to the npm
    registry once an `NPM_TOKEN` secret exists — same
    `secrets`-not-available-in-`if:` workaround `apt-release.yml` and
    `homebrew-tap.yml` already use, validated clean with `actionlint`.
  - New `docs/packaging/npm.md` (mirrors `docs/packaging/homebrew.md`'s
    structure) plus a README "npm" install subsection.

## [0.3.1] — 2026-09-04

Patch release. Backwards-compatible with 0.3.0. One real bug fix in
`clickcast diff`'s frame resolution, plus 4 new `demo/` examples (not
shipped in the package itself) that found it.

### Fixed
- **`clickcast diff` couldn't find real `--format frames` output whenever
  `--out` had directory components** (e.g. `--out demo/pixel-visual-diff/run.gif`)
  — `_resolve_frame` double-joined the sidecar's `media.path` against its
  own directory, producing a stale nonexistent path and silently reporting
  every step as `unmatched (missing frame)` instead of diffing anything.
  Every existing test used a bare single-segment `media.path` (`"tour.gif"`),
  so the bug was invisible until a real multi-segment `--out` path was
  exercised — found while building the `demo/pixel-visual-diff/` example
  below. Fixed by resolving the frames directory from the sidecar's real
  on-disk location plus `media.path`'s basename, not its full (possibly
  stale) relative path. New `TestFramesFormatResolution` regression tests
  cover both the multi-segment case and the pre-existing bare-filename case.

### Added
- **4 new `demo/` examples ablating v0.3.0 capabilities** the existing 8
  demos (#66) never covered:
  - `demo/spatial-grid-overlay/` — `--grid` pixel-coordinate targeting.
  - `demo/pixel-visual-diff/` — `clickcast diff` against a real dark-mode
    toggle regression on react.dev (81% changed, 2 highlighted regions),
    including the click step correctly landing in `unmatched_steps` rather
    than mis-pairing against the wrong baseline step.
  - `demo/accessible-element-targeting/` — the accessibility+grid fusion
    (`elements --json --grid`), contrasted with the audit-focused
    `accessibility-preflight/`.
  - `demo/live-mcp-session/` — a real live `clickcast mcp` session (four
    real tool calls against react.dev via the in-process MCP test harness),
    with the reel assembled from the session's own returned frames and
    `close_session`'s `save_transcript` producing a genuine schema-v4
    sidecar from a live session.

## [0.3.0] — 2026-09-03

Agent-autonomy release. Backwards-compatible with 0.2.9. The biggest
release yet: live agent control via a new MCP server, a pixel-level
visual diff engine, accessibility semantics fused with the pixel-grid
overlay (sidecar bumps v3 → v4, additive), a packaged CI GitHub Action,
day-one Homebrew/apt distribution tooling, and a self-healing first-run
experience so a fresh `pip install clickcast` gets you working
immediately instead of dead-ending on a missing-browser traceback.

### Added
- **Pixel-level visual diff between two runs** (closes [#201], [#202],
  [#203], [#204], [#205]). `clickcast assertions --baseline` ([#112]) is
  structural-only — it never looks at a pixel. This adds a real
  pixel-level companion: pair up two sidecars' steps, pixel-diff the
  paired frames, and report a percent-changed + a list of changed
  bounding regions per step, plus region-highlighted diff images.
  - New `src/clickcast/feedback/visual_diff.py`:
    `visual_diff(run_sidecar_path, baseline_sidecar_path, *, threshold=,
    out_dir=, exclude_overlays=) -> VisualDiffReport`. Pairs steps by
    index first, falling back to label matching when the two sidecars'
    step counts differ; a step that still can't be paired (or whose
    frame is missing from disk) is flagged in `unmatched_steps` rather
    than silently skipped. Pixel diff is Pillow's
    `ImageChops.difference` collapsed to a per-pixel max across R/G/B,
    thresholded (`--threshold`, default 24/255) to ignore
    anti-aliasing/re-encoding noise, then grouped into bounding regions
    via a tiled connected-component pass (no numpy — matches the
    `annotate/grid.py` "pure-Pillow, no dependency changes" pattern from
    [#171]).
  - New `BBox` / `UnmatchedStep` / `StepVisualDiff` / `VisualDiffReport`
    pydantic models (`extra="forbid"`) in `feedback/models.py`, mirroring
    `Assertions`' contract-freezing style.
  - Clickcast's own annotator overlays (progress bar, action label,
    actions panel, cursor + ripple) are excluded from the diff by
    default, computed from `AnnotateConfig`'s default layout constants
    plus each paired step's real `cursor_xy` — otherwise diffing two
    runs of an identical page would flag clickcast's own chrome as a
    regression on every step. `--no-exclude-overlays` opts out for
    strict raw-pixel diffing.
  - `clickcast diff <run>.gif.json <baseline>.gif.json` CLI command:
    `--out DIR` (diff images + `summary.json`, default a `.diff/`
    directory next to the run sidecar), `--threshold FLOAT`,
    `--no-exclude-overlays`, `--fail-above PCT` (nonzero exit when any
    step's `changed_pct` exceeds the gate, or when a step is
    unmatched — omit the flag to report only). This CLI contract (name,
    `--out`, `--fail-above`) is depended on by the forthcoming CI Action
    ([#206]/[#208]).
  - `Reel.visual_diff(run_sidecar_path, baseline_sidecar_path, ...)` —
    `assertions_diff()`'s sibling for Python API callers. Unlike
    `assertions()`, it reads both sidecars from disk rather than the
    in-memory last report, since pixel diffing needs the frame PNGs the
    recorder's temp directory no longer holds once `save()` returns.
  - README's CI regression-gate section now covers `diff` alongside
    `assertions` — when to reach for structural vs. visual, and how the
    two compose.
- **`clickcast mcp`** (closes [#191], [#192], [#193], [#194], [#195]).
  Live-agent-control, backwards-compatible and additive-only: an MCP
  (Model Context Protocol) server wrapping clickcast's existing
  session/action engine so an AI agent can drive a live browser one
  action at a time and react to what it sees, instead of only
  recording a whole tour up front.
  New optional extra `clickcast[mcp]` (the official `mcp` Python SDK,
  pinned `<2` — see below) and a new `clickcast mcp` command that starts
  a stdio MCP server exposing 12 tools:
  - `start_session` / `close_session` — open/close the one live
    `Session` the server process holds (v1 is single-session,
    single-process; see #191's "Out of scope"). `start_session` mirrors
    `core/opts.py`'s `BrowserOpts` fields (engine/viewport/device/
    headful/lang/dark) plus the grid overlay knobs, defaulting to
    whatever `clickcast mcp --engine ...` was started with.
    `close_session` optionally flushes the session's accumulated
    sidecar-shaped transcript to disk via `save_transcript: PATH`.
  - `goto`, `click`, `dblclick`, `hover`, `type`, `press`, `select`,
    `scroll`, `wait`, `screenshot` — one tool per `core/actions.py` step
    type, each a thin wrapper around the same `execute(step, session)`
    dispatcher scenarios and `auto` already call. Nothing about how an
    action resolves, times out, or classifies its failure differs
    between a batch scenario and a live MCP call.

  Every action tool returns clickcast's per-action payload instead of a
  bare screenshot: an annotated PNG frame (cursor + click ripple +
  label + the `--grid` overlay when enabled, reusing
  `annotate/annotator.py` directly), a `page_state` block (same fields
  as the sidecar's `feedback/models.py::PageState`), and — on failure —
  an `error_code` from the exact same enum the sidecar uses
  (`timeout` | `locator_missing` | `cross_origin` | `navigation_error`
  | `selector_ambiguous` | `other`). One classification table, two
  consumers; no parallel model, no drifting error-text regex.

  New docs: [`docs/mcp-tool-schema.md`](docs/mcp-tool-schema.md) (the
  full per-tool design contract — args, return shape, error modes) and
  [`docs/mcp.md`](docs/mcp.md) (what it is, when to reach for it vs.
  batch `auto`/`run`, copy-pasteable Claude Code / Claude Desktop client
  config — verified against a real stdio MCP round-trip, not just
  described). README gained a "Live agent control (MCP)" section;
  `docs/for-agents.md` gained a "Live session" pointer;
  `clickcast skill --json` gained an `mcp` command entry.

  Tests under `tests/mcp/`: an in-process MCP client
  (`mcp.shared.memory.create_connected_server_and_client_session`)
  against a real Chromium session driving the existing
  `tests/fixtures/site/` fixture — happy path for all 12 tools, plus
  failure-mode coverage for `timeout` (a real unresolved-selector
  click) and `locator_missing` (mirrors how `tests/test_actions.py`
  already tests that classification — a direct, non-timeout exception,
  since a real browser 0-match click universally times out first) and
  usage errors (`other`) for calling an action tool before
  `start_session` / double-`start_session` / acting after
  `close_session`.

  The `mcp` extra is pinned `mcp>=1.9.0,<2` — the `mcp` package's 2.x
  line renamed `FastMCP` to `MCPServer` with a breaking API change;
  `src/clickcast/mcp/` targets the widely-deployed 1.x `FastMCP` API
  every current Claude Code / Claude Desktop MCP config is written
  against. The base `pip install clickcast` is unaffected — `mcp` is
  opt-in via `pip install 'clickcast[mcp]'`.
- **Accessibility-node capture fused with grid coordinates** (closes
  [#196], [#197], [#198], [#199], [#200]). Fuses Playwright's own
  accessibility tree (ARIA role, accessible name, interactive state)
  with the pixel-grid overlay's coordinate system ([#171]) for every
  element clickcast discovers, so an agent reading `elements --json`
  or the sidecar gets "this is a `button` named 'Get started',
  disabled=false, at grid cell (4, 2)" in one payload instead of
  stitching together a separate accessibility-tree call and a
  screenshot. Sidecar bumps v3 → v4 additively: new optional
  `discovered_elements[].accessibility` block.
  - `discovery/accessibility.py`: `capture_accessibility` /
    `capture_accessibility_batch` resolve each discovered element's
    `role`, accessible `name`, and interactive `state`
    (`disabled` / `checked` / `expanded` / `pressed` / `selected`) via
    Playwright's `Locator.aria_snapshot()` — the modern, still-supported
    replacement for the removed `page.accessibility` tree API. Any
    Playwright resolution failure (vanished selector, ambiguous match,
    timeout) degrades to a fully-null role/name/state rather than
    failing discovery; `discover()`'s own selector/score output is
    unchanged.
  - New `AccessibleElement` model carries the existing
    selector/bbox/score fields plus the resolved role/name/state and a
    `grid_cell` (`[col, row]`) computed via a new
    `annotate.grid.grid_cell()` helper — the same pitch math `draw_grid`
    renders gridlines/labels with, so an element's cell matches what a
    human reads off the rendered `--grid` overlay image. `grid_cell` is
    `null` unless a grid config was passed, matching how the grid
    overlay itself is opt-in (#171).
  - `discovery.discover_with_accessibility()`: one-call convenience that
    runs `discover()` then fuses accessibility for the resulting pool.
  - Sidecar schema bumped v3 → v4 (strictly additive): new optional
    `discovered_elements[].accessibility: {role, name, state, grid_cell}`
    block on `feedback/models.py`'s `DiscoveredElement`, wired through
    `ReportBuilder.set_discovered(elements, accessibility=...)` and
    `clickcast auto`'s per-page discovery pass (first page only, mirroring
    the existing `discovered_elements` pin). `v3.json` / `v2.json` /
    `v1.json` stay frozen verbatim; `v4.json` is the new committed
    snapshot at `src/clickcast/feedback/schema/v4.json`.
  - `clickcast elements` gains `--grid` / `--grid-pitch` / `--grid-color`
    / `--grid-style` (mirroring `auto` / `run` / `shot`) and every
    `--json` entry gains the same `accessibility` block; text-mode
    output appends a compact `a11y(role=... name=... cell=...)` summary
    per element.
  - Docs: `README.md`'s `elements` flag table and
    `docs/feedback-schema.md` document the new block at the same depth
    as existing fields; a new
    `tests/consumer/read_accessibility.py` (mirroring #99's
    `read_sidecar.py`) proves the block is parseable by a standalone
    consumer that never imports `clickcast`.
- **Official GitHub Action** wrapping `install` -> `run`/`auto` ->
  `assertions --baseline` -> `diff` -> a PR comment (closes [#206],
  [#207], [#208], [#209], [#210]). The README's "CI: 2-line regression
  gate" was copy-paste shell with no packaged, versioned, reusable form
  and no way to see the reel without leaving CI logs; downstream repos
  now get `uses: AlexKay28/clickcast/.github/actions/clickcast@<ref>`
  instead. Depends on the pixel-diff CLI added above ([#201]-[#205],
  PR #211) for its visual-diff half.
  - New composite action `.github/actions/clickcast/action.yml`:
    installs a pinned `clickcast` version, caches Playwright/Chromium
    (keyed on the resolved playwright version, mirroring `ci.yml`'s own
    strategy — see its #43 item 3 comment), runs a scenario (`run`) or
    URL tour (`auto`), and gates on a committed `assertions` baseline
    and/or a committed raw baseline sidecar + frames (`diff`). A
    nonzero gate doesn't abort the job outright — the result is
    captured so the PR comment can still report it before a final step
    fails the Action for real. Outputs: `sidecar-path`, `reel-path`,
    `assertions-passed`, `diff-summary-path`, `diff-worst-pct`,
    `diff-passed`.
  - Posts/updates a single PR comment (`actions/github-script` +
    `.github/actions/clickcast/scripts/post_comment.js`, upserted by a
    hidden HTML-comment marker so re-runs update it in place rather
    than spamming new comments) with the reel GIF and a markdown table
    of per-step assertions status and visual-diff percent-changed.
    Degrades gracefully to whichever of assertions/diff was actually
    configured. Since there is no public REST/GraphQL endpoint to
    attach a binary file to a comment the way the web UI's
    drag-and-drop does, the reel is published to an orphan
    `clickcast-media` branch and linked via a raw-content URL, which
    GitHub renders inline — falling back to a workflow-artifact link
    (with a `::warning::`, not a failure) on fork PRs, whose
    `GITHUB_TOKEN` can't push.
  - `docs/ci/README.md`: full example workflow, an inputs/outputs
    reference, why `assertions`' distilled baseline and `diff`'s raw
    baseline-sidecar-plus-frames are different committed files, how the
    reel gets embedded, and the versioning/Marketplace decision (stays
    in-repo under `.github/actions/clickcast/` for now — Marketplace
    listing needs the action at a repo root and is a manual,
    owner-only step no CI tooling can complete).
  - Dogfooded in this repo's own CI:
    `.github/workflows/clickcast-self-check.yml` runs the Action
    against `docs/scenarios/spa.yml` (served locally via
    `tests/fixtures/site/`, no external-network dependency) on every
    PR against a committed baseline + frames
    (`tests/fixtures/ci-baseline/`), installing clickcast from source
    (`install: 'false'`) rather than PyPI since `diff` hasn't shipped
    in a release yet. Exercises both the `assertions` and `diff` gates,
    not just a bare run — this is the regression guard for the Action
    itself.
- **Homebrew tap + self-hosted apt repo — day-one native package-manager
  distribution** (closes [#170]). Until now `pip install clickcast` was the
  only install path; this adds two more for AI-agent-operator/DevOps and
  non-Python-engineer audiences, scoped to exactly the "v1/day-one" step of
  #170's rollout plan (homebrew-core submission, Launchpad PPA, Debian
  archive submission, Scoop, conda-forge, and a Docker image are explicitly
  out of scope, phased for v0.4.x+/follow-up).
  - **Homebrew**: `Formula/clickcast.rb`, generated by the new
    `scripts/homebrew_formula.py` from a version + the sdist URL/sha256
    looked up from PyPI's JSON API. Uses `Language::Python::Virtualenv` with
    a single `venv.pip_install "clickcast==#{version}"` rather than a
    `resource` block per transitive dependency — the documented
    simplification #170 allows for v1 ("pick one after prototyping"). No
    `depends_on "ffmpeg"` (already bundled via `imageio[ffmpeg]`) and no
    Chromium (`caveats` points at the existing `clickcast install
    --with-deps chromium`). New `.github/workflows/homebrew-tap.yml` renders
    the formula and pushes it to `AlexKay28/homebrew-clickcast` on every
    published release, gated on a `HOMEBREW_TAP_TOKEN` secret that doesn't
    exist yet — see `docs/packaging/homebrew.md`'s bootstrap section.
  - **apt**: `scripts/build_deb.sh` builds a self-contained `.deb` — a venv
    staged under `/opt/clickcast`, symlinked to `/usr/bin/clickcast`,
    control/postinst rendered by the new `scripts/apt_package.py`. Bundling
    the venv (rather than a bare `Depends: python3`) sidesteps #170's own
    concern that Debian's `python3` alias varies by release (bookworm=3.11,
    trixie=3.12). Same no-ffmpeg/no-Chromium choices as Homebrew, mirrored
    in `postinst`. New `.github/workflows/apt-release.yml` always builds,
    smoke-tests (installs in a clean container and runs `clickcast
    --version`), and attaches an unsigned `.deb` to the GitHub release —
    working day-one even before bootstrap — and additionally signs +
    publishes a proper apt repo (`Packages`/`Release`/`InRelease`) to a
    `apt-repo` branch served by GitHub Pages once an `APT_SIGNING_KEY`
    secret exists — see `docs/packaging/apt.md`'s bootstrap section.
  - Verified beyond a paper review: the `.deb` was actually built, installed
    in a throwaway Ubuntu 24.04 container, and run (`clickcast --version` /
    `--help`) — this caught a real bug (venv console-script shebangs baking
    in the build-time staging path instead of the final `/opt/clickcast`
    path) that a `dpkg-deb --contents` review alone would have missed.
    `lintian` was also run against the built package; remaining findings are
    documented as expected consequences of bundling a whole venv, not
    defects. `brew` itself wasn't available to verify the formula end to
    end — see `docs/packaging/homebrew.md` for what was checked instead.
  - README's Install section gains both paths, each marked with what works
    today (local `--build-from-source` / local `.deb` build) vs. what needs
    the one-time, owner-only bootstrap (the hosted tap / apt repo).
- **Self-healing pre-flight when a browser engine isn't installed.** Before
  this, any browser-launching command (`auto`, `run`, `shot`, `elements`,
  `mcp`) run before `clickcast install` surfaced Playwright's raw
  `Executable doesn't exist` traceback — the #1 "why doesn't this work"
  failure mode right after a fresh `pip install clickcast`.
  - New `clickcast.core.engines` module (extracted from the detection logic
    `doctor` already had) + `EngineNotInstalledError`. `Session.__aenter__`
    now checks it before Playwright even starts — the one place every entry
    point (CLI, the Python `Reel`/`AsyncReel` API, the MCP server) funnels
    through, so one check covers all of them.
  - CLI commands catch that error once (`_run`, wrapping `asyncio.run`) and,
    on an interactive terminal, offer to install the missing engine right
    then — say yes once and the original command retries automatically.
    Non-interactive contexts (CI, piped input) never prompt: they fail fast
    with the exact fix command instead of hanging on stdin.
  - The MCP server can't prompt mid-tool-call (an agent isn't a human at a
    terminal), so `start_session` degrades to the same `error_code: "other"`
    payload every other MCP failure uses, with the fix command in the
    message text.
  - README's Install section leads with a single copy-paste line
    (`pip install clickcast && clickcast install --with-deps chromium &&
    clickcast doctor`) instead of three separate steps.

## [0.2.9] — 2026-08-07

Agent-spatial-reasoning release. Backwards-compatible. Adds a
pixel-grid annotation layer so AI agents consuming reels and
screenshots can measure distances in the image instead of counting
pixels by eye or hitting the DOM. Sidecar bumps v3 additively:
new optional `annotate` block records the grid params the reel was
rendered with.

### Added
- **Pixel-grid overlay** (closes [#171]). New `--grid` flag on
  `shot` / `auto` / `run`, plus `--grid-pitch`, `--grid-color`,
  `--grid-style`. Two styles:
  - `full` (default): major gridlines every N pixels (default 100)
    + minor gridlines every N/10 + coordinate labels along the top
    and left edges. White @ 20% opacity by default so the grid
    doesn't dominate the image.
  - `ruler`: coordinate labels only, no gridlines — for agents that
    just need the coordinate system without the visual density.

  Wired through `Config` (`CLICKCAST_GRID`, `CLICKCAST_GRID_PITCH`,
  `CLICKCAST_GRID_COLOR`, `CLICKCAST_GRID_STYLE`) so an agent can
  export the settings once and every command picks them up.

  Layer order: `content → grid → highlights → arrows → labels` —
  grid draws BEHIND click highlights / sticky arrows / cursors so
  they stay legible over it. Compatible with `--zoom-on-click`:
  grid renders on the zoomed frame, so labels reflect the current
  image coordinates (test locks this contract).

  Sidecar now carries an optional `annotate.grid` block
  (`{pitch, style, color}`) when the grid was rendered — agents
  parsing a reel know the coordinate system without inspecting the
  image.

  Implementation is a pure-Pillow module at
  `src/clickcast/annotate/grid.py`; no dependency changes.

## [0.2.8] — 2026-08-07

Follow-up polish over 0.2.7. Backwards-compatible. Single change: closes
the last flag-parity gap left over from the CLI review in 0.2.7 so every
session-producing command accepts the same browser-behaviour +
observability flags.

### Changed
- **`shot` and `elements` now accept `--headful`, `--lang`, `--slowmo`,
  `--verbose`** (closes [#178]). `elements` additionally gains
  `--device` and `--dark`. Before this release, both commands hardcoded
  those args to defaults, so a user hitting a flaky page with either
  command had no way to flip to headful, crank slowmo, or get verbose
  traces without switching to `auto` / `run`. Now the four
  session-producing commands (`auto`, `run`, `shot`, `elements`) share
  the same flag surface. `_setup_logging(verbose)` fires at the top of
  each command body, matching the `auto` pattern. No changes to
  `_session_kwargs` or `BrowserOpts` — the pre-existing plumbing
  already accepted these fields; the commands just weren't exposing
  them.

## [0.2.7] — 2026-08-06

CLI review batch. Backwards-compatible with 0.2.6. Six issues resolved
from a top-to-bottom review of `src/clickcast/cli.py` (five real bugs,
one refactor with a UX follow-up). Nothing surface-visible changes for
existing invocations — every fix either corrects a wrong value that was
being emitted, unblocks a case that used to fail silently, or moves the
CLI onto more idiomatic Typer plumbing.

### Fixed
- **`clickcast install` was picking up a system-wide `playwright` binary
  over the venv's** (closes [#176]). The old
  `shutil.which("playwright") or {sys.executable} -m playwright` dispatch
  meant that a global `playwright` on `PATH` (e.g. from a separate
  `pip install playwright` or a future brew/apt install per [#170]) would
  fetch browsers built for a different playwright version than the venv's
  `import playwright` uses — producing mystery "Executable doesn't exist
  at ..." errors at runtime. Always uses `[sys.executable, "-m",
  "playwright", ...]` now; `import shutil` dropped as unused.
- **`clickcast config list` printed list-typed fields as Python repr**
  (closes [#175]). The `header` field introduced in 0.2.6 rendered as
  `['Authorization: Bearer x', 'X-Trace: 1']` and unset optionals rendered
  as `None`. New `_format_value` helper: non-empty lists render as
  `"; "`-joined (matches the friendlier env-var syntax `_parse_header`
  accepts), empty lists as `(none)`, `None` as `(unset)`. Key column
  auto-widths to the longest field name so `header_host` no longer
  misaligns.
- **`clickcast run --emit-events` counted `pages`/`clicks` from the
  scenario source, not from steps that actually executed** (closes
  [#172]). A scenario failing at step 3 of 5 still reported 5-worth of
  pages/clicks in the `tour_complete` JSON event. Counts from
  `result.results` filtered on `status == "ok"` now. Bonus semantics
  improvement: `repeat: 3` on a click step now counts as 3 clicks (was
  1). Cross-checked with `auto`'s emit path.
- **`_setup_logging(force=True)` destroyed the root-logger config for
  library callers** (closes [#174]). Any application that imported
  clickcast alongside its own logging setup (JSON / structured / Sentry)
  had its handlers silently replaced on first CLI-adjacent code path.
  Scoped to the `"clickcast"` logger tree; a new
  `_ensure_cli_root_handler()` runs from `main()` and only attaches a
  stderr handler if root has none yet. CLI users still see logs;
  library users keep their handlers.
- **`clickcast doctor` labeled the Playwright cache DIRECTORY as the
  browser's "executable path"** (closes [#173]). Users trying to run
  the printed value got "is a directory" errors. New
  `_ENGINE_EXECUTABLE_PARTS` mapping (mirrored from Playwright's
  upstream `EXECUTABLE_PATHS`) resolves the actual binary per engine ×
  OS (chromium/firefox/webkit on linux/darwin/win32, including
  chromium's Chrome-for-Testing variant). Fallback labels the install
  directory as `"install dir"` so novel layouts don't regress doctor to
  "not installed". Tightened the cache glob to `<prefix>-<numeric>` so
  `chromium-headless-shell-*` no longer shadows the real chromium
  install.

### Changed
- **`clickcast config` converted from a string-dispatched command to a
  Typer sub-app** (closes [#177]). `config` was the only command using
  a positional `action` argument with hand-rolled arg-requirement
  checks per branch. Now:
  - `clickcast config <TAB>` autocomplete resolves `path` / `list` /
    `get` / `set`.
  - Each subcommand has its own `--help` with per-arg docs.
  - `clickcast config set` gains a **`--scope user|project`** flag —
    `--scope project` writes to `./clickcast.toml` so a repo can pin a
    shared default that overrides individual users in the precedence
    stack (useful for teams without asking each collaborator to edit
    their user TOML). Default is `user` so every existing invocation
    keeps working.
  - New `set_project_value()` peer of `set_user_value()`, both routed
    through a shared `_write_value_to_toml()` helper so their tomlkit
    round-trip and coercion semantics can't drift.

  Every existing invocation (`clickcast config path`,
  `clickcast config get engine`, `clickcast config set engine firefox`)
  works unchanged.

## [0.2.6] — 2026-08-05

Internal-host unblock release. Backwards-compatible with 0.2.5. Adds
three CLI flags on `shot` / `auto` / `run` / `elements` so agents can
capture pages behind private-CA TLS or SSO-guarded bearer-token auth —
the same wall corporate wikis, trackers, admin consoles, and staging
deployments hit. Plus a comprehensive AI-agent skill guide (`skill.md`)
that every subcommand's `--help` epilog now points at, and one
long-hidden test-hygiene fix that had every main-branch CI run failing
since Aug 2 without anyone noticing.

### Added
- **`skill.md` — long-form AI-agent usage guide.** Covers every command
  with real examples, five recurring workflow patterns (auto → CI
  baseline, scripted-scenario PR gate, internal-host access, bug
  reporting loop, env-var-driven config), the sidecar contract, and
  failure recovery. Every subcommand's `--help` epilog now leads with a
  pointer to this file — agents that hit any `clickcast X --help` see
  it first. Complements the shorter `clickcast skill` CLI-embedded
  brief (which stays capped at ~900 words for tool-discovery messages).
  New `SKILL_URL` constant in `clickcast.feedback.pointers` so
  downstream code has one place to reference the URL.
- **`--insecure` / `--header` / `--header-host` for internal / SSO-protected
  sites** (closes [#166]). Three flags on `shot`, `auto`, `run`, and
  `elements`:
  - `--insecure` passes `ignore_https_errors=True` to Playwright so
    private-CA / self-signed hosts load. Same idea as `curl --insecure`.
  - `--header "Name: value"` (repeatable, also `-H`) attaches request
    headers.
  - `--header-host HOST` scopes headers to one origin by installing a
    `context.route` interceptor. Hostname match is exact or dotted-suffix
    (`.example.com` matches `a.example.com` and `example.com`) with a
    guard: bare labels like `.net` only exact-match, so an accidentally
    over-broad flag can't scope a bearer token to a whole TLD. Without
    `--header-host`, headers apply globally — same as Playwright's
    built-in `extra_http_headers`, but a real credential leak risk for
    subresources fetched from CDNs / analytics endpoints.
  - Same three fields wired through `Config`, so `CLICKCAST_INSECURE`,
    `CLICKCAST_HEADER`, `CLICKCAST_HEADER_HOST`, and `clickcast.toml`
    all work. `CLICKCAST_HEADER` accepts a friendlier scalar or
    `;`- / newline-separated form (not just the pydantic-settings default
    JSON list) so agents don't have to type `'["Auth: Bearer x"]'` in a
    shell.
  - On `run`, the flags follow the same "explicit CLI flag wins over
    scenario meta" pattern as `--headful` / `--slowmo`. Scenario YAML
    can also carry the fields flat (`meta.insecure: true`) via the
    existing flat-to-nested migration shim.

### Fixed
- **`BrowserOpts.to_session_kwargs()` silently dropped `proxy`** — noticed
  while touching the same file for [#166]. `BrowserOpts.proxy` existed
  as a field but the shape method never included it in the dict, so
  `--proxy` and `CLICKCAST_PROXY` never actually reached Playwright.
  `_session_kwargs_from_meta` (scenario) and `_session_kwargs_for_bbox`
  (reel) now also delegate through `BrowserOpts.to_session_kwargs()`
  rather than maintaining parallel dicts, so any future field added to
  `BrowserOpts` propagates automatically.
- **`test_emit_events` CLI-help assertions were failing on every CI run
  since Aug 2** (closes [#168]). Rich (bundled with Typer) breaks color
  runs at hyphen boundaries when rendering flag names under
  `GITHUB_ACTIONS=true`, so `"--emit-events" in result.stdout` failed
  literally even though the flag was visibly present in the panel.
  Local dev terminals didn't trigger the split, so the tests looked
  green locally while every CI run stayed red — the v0.2.5 release
  commit merged with red CI for exactly this reason. Fixed with a
  small `_plain(s)` helper that strips SGR escapes before asserting.

## [0.2.5] — 2026-08-02

AI-agent ergonomics + refactor release — closes all 15 findings from the
[#151] audit bundle. Backwards-compatible with 0.2.4. Sidecar bumps
v2 → v3 (strictly additive: `skip_reason`, `error_code`; old v2 sidecars
validate unchanged). Three internal refactors (`explore_page`,
`execute`, `_normalize_step`) landed as zero-behaviour-change splits;
three perf/QoL cleanups (CLI defaults caching, collector logging,
discover cache, deferred var-substitution) tighten hot paths without
touching the public API. Two new advisories catch UI-legibility
anti-patterns the guide documented but the tool didn't emit. Test suite
grew 730 → 777 (+47 across all 15 findings and their coverage).

### Added
- **Sidecar schema v3: `skip_reason` + `error_code` + `--emit-events`**
  (partial closes [#151] — AI-2, AI-4, AI-5 of the 15-finding audit).
  Three additive changes that lift agent gate-ability without breaking
  the shipped v2 contract:
  - **AI-2** `StepReport.skip_reason: SkipReason | None = None` — a
    ``Literal["optional_no_reaction", "pre_action_failed",
    "element_vanished", "cross_origin_bounce"]`` on skipped steps.
    ``clickcast.core.actions.execute`` populates it from the classified
    error kind on optional failures (locator_missing → element_vanished;
    cross_origin → cross_origin_bounce; else → pre_action_failed).
    ``optional_no_reaction`` is reserved for a follow-up that lifts the
    advisories layer's DOM-non-reaction check into an in-tour skip signal
    — enumerated now so downstream baselines pin the reason.
  - **AI-5** `StepReport.error_code: ErrorCode | None = None` — a
    ``Literal["timeout", "locator_missing", "cross_origin",
    "navigation_error", "selector_ambiguous", "other"]`` on failed and
    skipped steps. A new ``_classify_error(exc) -> str`` helper in
    ``actions.py`` is the single source of truth for the mapping so the
    enum and the classification table can't drift.
    ``feedback/assertions.py::build_assertions`` grows both fields on
    every ``StepAssertion`` row so a CI baseline pins the KIND of skip /
    failure, not just the ``status`` verb — a step going from
    ``skipped(pre_action_failed)`` to ``skipped(element_vanished)`` is
    now real drift.
  - **AI-4** `--emit-events` on `clickcast auto` and `clickcast run` —
    off by default; on prints one
    ``{"event": "tour_complete", "gif_path": ..., "frames": ...,
    "duration_s": ..., "pages": ..., "clicks": ..., "wall_s": ...,
    "sidecar_path": ...}`` JSON object on its own line to stdout after
    the shipped prose summary. JSONL-friendly so future event types
    (per-step, advisories) can share the channel.
- **Schema bump: v2 → v3.** Additive. v1 and v2 sidecars validate
  cleanly under the v3 model (both new fields default to ``None``).
  ``src/clickcast/feedback/schema/v3.json`` is the new committed
  snapshot; ``v2.json`` and ``v1.json`` stay verbatim for downstream
  consumers that bookmarked those URLs. ``SIDECAR_SCHEMA_URL`` in
  ``skill.py`` points at v3.
- **Two new post-tour advisories + two missing skill-brief entries**
  (partial closes [#151] — AI-3 and AI-7 of the 15-finding audit).
  ``clickcast.feedback.advisories.build_advisories`` grows two ids that
  cover the last two anti-patterns documented in
  ``docs/ONE_PAGE_NAVIGATION_ORDER_TIPS.md`` but previously unwarned:
  ``interpolate-single-arrow-conflict`` (``CursorStyle.interpolate=True`` +
  ``single_arrow=True`` smears the sticky arrow through the interpolated
  path) and ``arrow-distance-vs-cross-nav`` (a cross-origin edge with
  ``arrow_max_distance`` at its shipped default 600 px silently
  suppresses the closing arrow of the previous scene). ``build_advisories``
  gains a backward-compatible ``annotate_cfg: AnnotateConfig | None = None``
  kwarg — hand-fixtured callers that don't pass one skip both new checks;
  ``auto.py::run_tour`` wires ``cfg.annotate`` through. On the skill-brief
  side, ``clickcast skill``'s ``auto`` command now surfaces
  ``--dump-elements`` (the primary agent debugging aid for selector
  failures) and ``run``'s brief mentions ``optional: true`` step support,
  closing the two undiscoverability gaps AI-7 called out. Word-count cap
  in ``tests/test_skill.py`` unchanged at 900 (897 after additions).

### Changed
- **Per-page discovery cache in `auto.explore_page`** (part of [#151] —
  PERF-1 of the 15-finding audit; zero-behaviour-change). ``discover()``
  used to run exactly once per page in ``_goto_and_discover``, but the
  architecture had no defence against a click-retry-loop re-fetch: any
  future path that re-asked for the element pool would pay the full DOM
  walk again (100 ms+ on slow sites, dominated by the score-and-dedup
  step). Fixed by introducing a URL-keyed page-scope cache dict in
  ``explore_page``, threaded through ``_goto_and_discover`` (via a new
  ``_ensure_discovered(sess, url, click_budget, cache)`` helper) and
  ``_click_loop`` (accepted for orchestrator symmetry; unused today, but
  documented as the route any future in-loop re-discovery should take).
  Invalidation is trivial — the cache lives only for one ``explore_page``
  call, so every new page starts cold; the URL key guards against ever
  returning stale results for a different page. Verified via the full
  774-test suite unchanged + three new ``test_auto_discovery_cache.py``
  cases that lock the cold-fetch / cache-hit / URL-key behaviours.
- **Scenario variable substitution deferred to post-parse** (part of
  [#151] — PERF-2 of the 15-finding audit). ``scenario/scenario.py::loads``
  used to run ``_substitute_vars`` on the raw YAML dict/list tree BEFORE
  pydantic constructed typed models — a full-tree traversal that visited
  every dict/list/scalar node even for scenarios that don't reference
  any ``{{ }}`` placeholder. Substitution now runs post-parse via
  ``_substitute_in_scenario(scenario, variables)``, which walks only
  string-carrying fields on the typed :class:`Scenario` (``str``,
  ``list[str]``, free-form ``dict[str, Any]``) using pydantic's
  ``model_fields`` and dataclass ``__dataclass_fields__`` for
  introspection. For steps like ``ScrollStep`` / ``WheelStep`` /
  ``WaitForStep`` whose payload is mostly ``int`` / ``float``, non-string
  fields are skipped entirely instead of visiting each numeric leaf.
  The raw-dict walker (:func:`_substitute_vars`) stays exported so
  existing test imports and any ad-hoc callers keep working; the AI-6
  undefined-variable error text (PR #153) is preserved verbatim via a
  shared ``_make_repl`` closure so the raw and typed walkers can't
  drift. Zero behaviour change: byte-identical typed output on every
  scenario shape currently in the test suite, same
  ``ScenarioError`` messages, same public ``load`` / ``loads``
  signatures. All 774 tests pass unchanged.
- **Log collector detach failures instead of silent suppress** (part of
  [#151] — REF-5 of the 15-finding audit). The three
  ``contextlib.suppress(Exception)`` blocks around
  ``PageStateCollector.detach``'s ``session.off(...)`` calls in
  ``src/clickcast/feedback/collector.py`` masked real lifecycle bugs
  (listener never registered, page already closed, playwright version
  mismatch). Each is now an explicit ``except Exception as exc`` that
  emits ``logger.debug("collector detach failed for %s: %r", event, exc)``
  against a new module-level ``logger = logging.getLogger(__name__)``.
  Production behaviour is byte-identical (exceptions still swallowed at
  INFO/WARNING and above); debug-level logs surface the failure when a
  developer enables them. No public API change.
- **CLI: cache command → Config-key introspection at import time**
  (part of [#151] — REF-4 of the 15-finding audit; zero-behaviour-change).
  ``cli._config_default_map`` used to walk ``app.registered_commands``
  and call ``inspect.signature`` on every subcommand callback on every
  CLI invocation, even though the {command → Config-relevant param
  names} table never changes at runtime (commands are registered via
  decorators before ``main()`` runs). The introspection now happens
  exactly once at module load in a new ``_build_cli_command_params``
  helper, populating a module-level ``_CLI_COMMAND_PARAMS: dict[str,
  frozenset[str]]`` after every ``@app.command`` and ``app.add_typer``
  has run. ``_config_default_map`` shrinks to a per-invocation
  ``Config`` load + a dict-comprehension projection onto the frozen
  table — no signature walk, no command-registry iteration. Preserves
  every observable behaviour: ``clickcast --help`` output is
  byte-identical, every default value is unchanged, AI-6's TOML-parse
  ⚠ warning still fires, PERF-3's config-load fallback still returns
  ``{}`` on any other exception. All 774 tests pass unchanged.
- **`auto.explore_page` split into orchestrator + helpers** (part of
  [#151] — REF-1 of the 15-finding audit). The 179-line function that
  fused (a) goto + discover, (b) click-retry with backoff, and
  (c) frame-capture orchestration is now three focused units:
  ``_goto_and_discover`` (goto + hydration hold + discover +
  set_discovered on the first page), ``_click_loop`` (click-budget
  loop, consecutive-failure bail, deadline early-exit, same-origin
  ``go_back`` restore, cross-origin bail), and a thin
  ``explore_page`` orchestrator that glues them together and finishes
  with the per-page scroll (extracted as ``_scroll_page``). Zero
  behaviour change: same signature, same ``StepReport`` shapes, same
  stderr lines, same builder call order, same
  ``skip_reason``/``error_code`` wiring from #154, same advisories
  wiring from #152. Verified via the full 774-test suite unchanged.
- **Scenario normalizer: per-action factory dispatch** (part of [#151] —
  REF-3 of the 15-finding audit). `scenario/scenario.py::_normalize_step`
  was a ~108-line nested if/elif chain that mixed dispatch, common-key
  copy, and per-action shape coercion — adding a new action verb meant
  editing the same block for the third or fourth time. It is now a thin
  dispatcher (~30 lines including docstring) that resolves the action
  verb, copies common keys, and delegates to a per-action normalizer
  from a module-level `_NORMALIZERS: dict[str, Callable[...]]` table.
  Twelve per-action normalizer functions (`_normalize_goto`,
  `_normalize_click_like` shared by click/dblclick/hover,
  `_normalize_type`, `_normalize_press`, `_normalize_select`,
  `_normalize_scroll`, `_normalize_wait`, `_normalize_wait_for`,
  `_normalize_screenshot`, `_normalize_evaluate`, `_normalize_wheel`)
  each own their shape and their `ScenarioError` message, and are
  unit-testable in isolation without pydantic. Zero behaviour change:
  every `ScenarioError` text is preserved verbatim (including AI-6's
  improved undefined-variable message from PR #153), and the test
  suite passes unchanged at 774.
- **Refactor: `core/actions.py::execute` dispatch dict** (part of [#151]
  — REF-2 from the 15-finding audit; zero-behaviour-change). The 150-line
  ``isinstance`` chain that dispatched 13 step types inside a single
  ``try`` block became a module-level ``_STEP_HANDLERS:
  dict[type[BaseStep], StepHandler]`` mapping to 13 focused
  ``_handle_<action>`` coroutines. The dispatcher body dropped to ~30
  lines and now owns only the shared envelope: timing, dwell,
  ``_step_context`` (AI-1), ``_classify_error`` (AI-5),
  ``_augment_with_hints``, and ``ActionResult`` assembly (including the
  ``skip_reason`` mapping from AI-2). Handlers exchange per-step
  side-channel state (``selector``, ``cursor_xy``, ``screenshot_path``)
  via a small ``_StepOutcome`` dataclass so the assembled
  ``ActionResult`` is byte-identical to the pre-refactor output. Adding
  a new step type is now one handler function + one dict entry rather
  than an ``elif`` branch inside a 150-line ``try``. All 774 tests pass
  unchanged; no public-signature changes; ``_augment_with_hints``,
  ``_step_context``, and ``_classify_error`` are preserved verbatim.
- **Error-message clarity: step context + friendlier scenario-var + visible
  TOML-parse warning** (partial closes [#151] — AI-1, AI-6, PERF-3 from
  the 15-finding audit; other findings ship in follow-up PRs). Three
  string-only improvements targeted at agents parsing sidecars and
  stderr:
  - **AI-1** `execute()` failures now carry a `step N (<label-or-action>): `
    prefix on `StepReport.error`, sourced from a new `_step_context()`
    helper in `core/actions.py`. Callers (`scenario/scenario.py`,
    `auto.py`) thread their loop index via a new
    `execute(..., step_index=int)` kwarg. Preserves the shipped
    `_augment_with_hints` block, which now appends after the prefix.
    Agents reading a sidecar can correlate a failure to a scenario line
    without regex-scraping.
  - **AI-6** The undefined-variable message in `scenario/scenario.py`
    was rewritten from `"undefined variable {{ foo }}"` (leaked
    template-syntax jargon, no fix hint) to
    `"undefined scenario variable 'foo' — pass '--var foo=<value>' on
    the CLI or declare it under 'variables:' in the scenario YAML"` —
    both remediation paths are now spelled out.
  - **PERF-3** A broken `clickcast.toml` used to fall back silently
    (`warnings.warn` is off by default; users had no idea their config
    was ignored). `config/_read_toml` now propagates
    `tomllib.TOMLDecodeError` with the file path prepended; the CLI's
    `_config_default_map` catches it and prints a single-line
    `⚠ clickcast.toml: TOML parse error — using defaults (…)`
    warning to stderr, matching the shipped `⚠` prefix from
    `feedback/advisories.py`. Missing config files stay silent
    (that's the normal "no config yet" case).
  - **Test suite: 730 → 741.** Eleven new tests (`TestStepContext` +
    four AI-1 integration cases in `test_actions.py`,
    `test_undefined_variable_message_includes_fix_hint` in
    `test_scenario.py`, two `TestConfigTomlParseErrorSurfaces` cases
    in `test_cli_config_wiring.py`, and reworked `TestMalformedTomlRaises`
    in `test_config.py`).

## [0.2.4] — 2026-07-31

CI hygiene + additive sidecar-schema-v2 release. Backwards-compatible
with 0.2.3. No user-visible behaviour change for shipped API callers;
the schema-v2 `graph` block is additive (v1 sidecars validate
unchanged under the v2 model). Test suite grew 707 → 730 across the
five closed issues (#43, #46, #99, #100, and #107 partial).

### Added
- **`tests/test_read_sidecar_e2e.py`: real-pipeline coverage for the
  AI-consumer script** (closes [#99]). Two integration tests drive the
  fluent `Reel(...).goto().click().save()` API against the shared fixture
  site, then invoke `tests/consumer/read_sidecar.py` as a subprocess to
  prove the sidecar shape the pipeline writes is exactly what a
  downstream agent parses. Happy path asserts clean exit + empty stderr;
  the failed-selector variant asserts the sidecar carries a
  `status != "ok"` step and that the consumer surfaces it on stdout.
  Closes the two residual gaps from #45's audit: schema validation
  (`test_fixture_site.py`) never chained through `read_sidecar.py`, and
  the consumer contract (`test_feedback.py::TestConsumerExample`) only
  ran against a hand-built `Report`. A silent drift between the writer
  and the consumer now fails these tests instead of shipping.
- **Sidecar schema v2 — additive `graph` block** (partial closes [#107] —
  Track C of the [#29] roadmap; Tracks A + B shipped previously). The
  sidecar records what happened during a tour, not what shape the app
  has. LLMs consuming the sidecar could reason about "this specific
  action sequence" but not "the shape of this app" — a much richer
  planning surface. Bumps `Report.schema_version` default 1 → 2 and
  adds an optional top-level `graph: Graph | None` field. New pydantic
  models: `PageNode` (kind `page`, url/title/first_seen_step/
  last_seen_step/components), `ComponentNode` (kind `component`,
  role/selector/bbox/dom_signature/seen_on_nodes), `Edge`
  (from/to/via_step/selector/transition_kind), `Graph`. New
  `clickcast.feedback.graph.build_graph(steps)` extracts `PageNode`
  entries from distinct `page_state.url_after` values in step order
  and emits one `transition_kind: "navigation"` `Edge` per URL change,
  returning `None` for empty tours to keep sidecars small. Wired into
  `ReportBuilder.build` best-effort — a bad graph never blocks the
  sidecar from writing. `dom_signature(role, aria_label, bbox)`
  helper computes a 16-char hex fingerprint with a 64px bbox bucket so
  same-position-across-pages nav dedupes and a genuinely relocated
  sidebar doesn't. `clickcast report-bug` renders a
  `graph: N pages, M components, K edges` line in the excerpt when
  present; skill brief for `report-bug` notes the v2 block for LLM
  planning. Schema regenerated to
  `src/clickcast/feedback/schema/v2.json`; `v1.json` preserved verbatim
  for downstream consumers that bookmarked it (v2 is strictly additive
  — old sidecars validate cleanly with `graph == None`). Deferred to
  follow-ups: `transition_kind: reveal` / `dismiss` (requires DOM
  diffing across step boundaries), `ComponentNode` extraction (needs
  a landmark-detection pass on `discovery/` output — dedup helper
  already exported so the follow-up plugs straight in), image /
  interactive graph rendering (non-goal), cross-tour graph
  persistence (non-goal).

### Changed
- **CI + release workflow hardening** (workflow-YAML slice of [#46] +
  [#43]). Six changes across `.github/workflows/{ci,release,demo}.yml`
  that together cut typical PR-feedback latency, close a supply-chain
  hole in the release path, and turn the release smoke-test from an
  import check into a real invocation:
  - `ci.yml`: dropped `test: needs: lint` so a format nit no longer
    blocks the ~10-min test matrix — lint and test run in parallel,
    branch protection still gates the merge on both ([#46] item 1).
  - `ci.yml`: symmetric `playwright install` branches per OS — on
    cache-hit we run `install-deps` alone (fast); on cache-miss we
    run `install --with-deps`. Previously Linux paid the apt cost on
    every job ([#46] item 4).
  - `release.yml`: `twine check dist/*` runs immediately after
    `python -m build`, so a wheel with broken `long_description` or an
    unknown classifier is caught pre-upload instead of bricking the
    project page ([#46] item 3).
  - `release.yml`: `pypa/gh-action-pypi-publish` SHA-pinned to
    `dc37677b` (v1.14.2) at both call sites. The prior `@release/v1`
    was a moving ref that could publish arbitrary code under our
    Trusted-Publishing OIDC identity if compromised ([#46] item 5,
    SHA-pin half only — action-version bumps for `checkout`/
    `setup-python`/`upload-artifact` deferred).
  - `ci.yml` + `demo.yml`: playwright cache-key now keys on the
    resolved version (`importlib.metadata.version('playwright')`),
    not just `hashFiles('pyproject.toml')`. A 1.44.0 → 1.44.1 resolver
    bump now invalidates the stale browser bundle; previously it kept
    serving the wrong `chromium-<rev>` directory and produced
    "Executable doesn't exist at ..." errors that looked intermittent
    ([#43] item 3).
  - `release.yml`: real smoke-test — after `--version`/`--help`, the
    smoke-test job now installs chromium, runs `clickcast shot` against
    a `data:` URL, verifies the PNG magic bytes, and imports
    `Annotator()` + loads `feedback/schema/v1.json` via
    `importlib.resources`. Catches four wheel-shipped-resource failure
    modes (missing font, missing schema, broken CLI dispatch, missing
    browsers) that the prior import-only check would have missed —
    same class of "packaged, but not really usable" bug the v0.2.2 →
    v0.2.3 `datetime.UTC` hotfix caught ([#43] item 2).
    Deferred to follow-ups: `actionlint` / `pip-audit` / `codespell`
    / `hatch-vcs` from [#46]'s "optional but recommended" list.
- **`pyproject.toml`: split `[project.optional-dependencies]` into
  `test` / `dev` / `publish` extras** (partial [#46]). `test` is the
  minimal `pytest{,-asyncio,-cov}` set every CI test-matrix job needs;
  `dev` is `clickcast[test]` plus lint / type-check / hooks / build;
  `publish` is `build + twine` for the release workflow. Contributors
  doing pytest work can now install a leaner `.[test]` extra. All
  currently-pinned deps preserved; this is a partition, not a rewrite.
  Workflow-YAML consumers of the new extras land in a sibling PR.

### Fixed
- **Test hygiene: monotonic timing + real eviction paths** (closes
  [#100]). Two residual coverage gaps from the sharpened audit:
  - `tests/test_actions.py::test_wait_number` and
    `::test_dwell_extends_duration` previously asserted absolute
    duration floors (`>= 50` / `>= 100` ms) with no epsilon or
    monotonicity check — sub-millisecond runner overhead on slow CI
    could flake them. Rewritten to compare two adjacent invocations
    and assert monotonicity with tolerance
    (`r_short.duration_ms + 30 <= r_long.duration_ms` for 50ms/10ms
    waits, and `r_no_dwell.duration_ms + 100 <= r_dwell.duration_ms`
    for a warmed-up 200ms dwell) — the property the tests were meant
    to prove.
  - `tests/test_annotator.py::test_trail_length_bounded_by_config`
    was manually calling `ann._cursor_history.pop(0)` inside the test
    loop, re-implementing the eviction it claimed to test — so
    removing the `while`-loop from `Annotator.annotate()` would not
    have broken it. Rewritten to feed 10 cursor positions through the
    real `Annotator.annotate()` and assert
    `len(ann._cursor_history) == trail_length + 1`. Verified locally
    by commenting out the eviction loop and confirming the test
    fails.
- **`tests/conftest.py`: fixture site now uses `ThreadingHTTPServer`**
  (closes [#43] item 1). `HTTPServer` serves one request at a time;
  Chromium fires many parallel requests on a real page load (favicon,
  subresources, HMR probes), which caused request queuing and
  intermittent 30-second Playwright timeouts under CI load. Switched
  to `ThreadingHTTPServer` with `daemon_threads = True` so the server
  handles concurrent requests and tests can shut down cleanly on
  failure. No test changes required — no existing test relied on
  request serialization.

## [0.2.3] — 2026-07-30

Hotfix over 0.2.2 — same content, plus one Python-3.10 compatibility fix
that the smoke-test caught before 0.2.2 could reach real PyPI. Tag
`v0.2.2` was pushed, blocked at the smoke-test stage on all four
Python-3.10 combos, and deleted; 0.2.2 was never on `pypi.org/clickcast`.

### Fixed
- **`from datetime import UTC` broke on Python 3.10** (introduced by
  [#124]). `datetime.UTC` is a 3.11+ alias for `datetime.timezone.utc`;
  the project's `requires-python = ">=3.10"` says 3.10 must work.
  Import replaced with `from datetime import datetime, timezone` +
  a module-level `UTC = timezone.utc`. mypy passed locally because
  mypy's `python_version` is pinned to 3.12 in `pyproject.toml` (see
  the existing comment there) — the release smoke-test on 3.10 is
  the guard that catches this class of bug. The full 0.2.2 change
  list is unchanged.

## [0.2.2] — 2026-07-30

*Tag pushed and deleted; never on PyPI. See 0.2.3.*

Agent-experience release. Three new opt-in features (a `--for-humans`
composite for human-legible reels, a `clickcast feedback` capture-session
substrate, and post-tour stderr advisories) built on top of three
opt-in refactors (a single `Viewport` value type, grouped `BrowserOpts`
+ `RenderOpts` dataclasses, and a narrow `Session→Page` seam that
hides Playwright from business-logic modules). Backwards-compatible
with 0.2.1 — every refactor keeps its old public API via property
accessors or dataclass composition; every new feature is off by default.

### Added
- **`clickcast feedback ...` capture-session substrate** (closes [#124] —
  partial v1; heuristics engine + `feedback file` emitter deferred).
  New Typer sub-app with `start [--label NAME]` / `stop` / `status` /
  `list` / `summary [--json] [--session ID]`. A thin `main()` wrapper
  around the Typer app records one JSONL event per CLI invocation
  (argv, exit_code, wall_time_ms, cwd, git_rev-if-present) into
  `${XDG_STATE_HOME:-~/.local/state}/clickcast/feedback/<session>/`
  whenever a session is active. `summary` renders a deterministic
  Markdown or JSON report — invocation count, top argv patterns,
  failed-invocation list, session duration. Zero network by default;
  storage is fully local. Recording is best-effort — a corrupt or
  missing session file NEVER breaks a CLI invocation, the recorder
  just no-ops. Skill brief gains a `feedback` command entry so agents
  discover the loop from message one. Heuristics/pattern-library
  (repeated-same-command, wrapper-script detection, etc.) and the
  `feedback file` GH-issue emitter deferred to follow-ups; this PR
  ships the substrate so the observable loop (start → use tool →
  `summary` shows real evidence) works today.
- **Skill brief word-count cap bumped 800 → 900** (piggybacked on
  [#124]). The 800 ceiling in `tests/test_skill.py` was set for 11
  commands; adding the `feedback` sub-app pushed the total past 800
  legitimately. Trimming the other 11 briefs to make room would have
  hurt information density for AI agents that rely on the brief. The
  spirit of the guard (\"don\'t let it become a manual\") is preserved
  at 900.
- **`clickcast auto --for-humans` composite flag** (closes [#129] —
  partial; Tracks A/E/F, follow-ups filed for B/C/D). One flag flips
  five sub-flags to human-friendly defaults (`--pace onboarding`,
  `--zoom-on-click 2.5`, `--highlight-target`, `--title-card`,
  `--summary-card`) so the reel is legible to a person watching without
  the sidecar — the first-run README-hero pain point the issue was
  filed against. Each sub-flag also stands alone; explicit flags always
  win over the composite (same `_is_explicit` precedence used by
  `--pace`), so `--for-humans --pace fast` gives a fast-paced human
  tour instead of the default onboarding pace.
- **Pre-click target-highlight ring** (Track A of #129). New
  `AnnotateConfig.target_highlight` + `TargetHighlightStyle`
  sub-dataclass draws a soft, pulsing rounded rectangle around the
  resolved click bbox on the pre-click sub-frame(s), so a human eye
  locks onto the target BEFORE the ripple fires. `AutoConfig`
  gains a matching `target_highlight` toggle plus
  `pre_click_highlight_frames` (default 4) — when on, `explore_page`
  resolves the click bbox via `Session.bbox()`, pads the pre-click
  frame that many extra times, and forwards the bbox on
  `StepAnnotation.target_bbox`. The annotator pipeline routes the
  ring onto pre-click sub-frames (identified by `cursor_xy=None` in
  the manifest) and offsets ripple stages by the pre-click count so
  the two never fight for the same frames. Bbox lookup is best-
  effort — a missing/hidden target just means "no ring this step",
  the click still fires.
- **Title + summary card renderers** (Track E of #129). New
  `clickcast.annotate.cards` module with `render_title_card` and
  `render_summary_card`, plus `CardStyle` and `SummaryStats` value
  types. `AutoConfig` gains `title_card` / `summary_card` toggles
  (plus per-card frame counts, watermark, style) which
  `run_tour` picks up AFTER the annotator pass to prepend/append
  N identical card frames to the reel via a small `frames.json`
  splice — the encoder picks them up transparently, and cards
  don't gain progress bars / cursor trails / action-panel overlays.
  Card size auto-matches the recorded frames' pixel dims so zoomed
  tours still line up. The dark-bg title card also masks any
  pre-first-paint white frame (Track G interaction; #68 still open
  as the root fix).
- **`Recorder.pre_action_pad(count)`** — new helper that duplicates
  the most-recent pre-action frame N extra times at the current
  `step_index`, before any `post_action` runs. Backs the pre-click
  hold time the highlight ring needs. Fails loudly (not silently)
  if called out of order.
- **`clickcast skill` brief mentions `--for-humans`, `--highlight-target`,
  and `--title-card` / `--summary-card`** on the `auto` command so
  agents discovering clickcast for the first time see the human-demo
  path from message one. The drift-guard test in `tests/test_skill.py`
  catches new subcommands missing from the brief; the new
  `TestSkillMentionsForHumans` test locks the specific composite-flag
  callout.
- **Post-tour advisories** (closes [#138] — Track A). New
  `clickcast.feedback.advisories` module scores a completed `clickcast auto`
  tour against four known anti-patterns and prints each finding to stderr
  with a `⚠ ` marker + deep-link to
  `docs/ONE_PAGE_NAVIGATION_ORDER_TIPS.md`. Every advisory carries a stable
  kebab-case `id` so downstream agents / CI can match, dedupe, or gate.
  Shipped ids: `nav-heavy-tour` (>50% of clicks caused a navigation —
  suggests `clickcast run` with a scripted scenario), `click-no-dom-reaction`
  (a click whose `page_state` URL + title match the prior step — the click
  may have been a no-op), `very-short-reel` (encoded reel < 20 frames —
  suggests more steps or higher `--dwell`), and `cross-origin-bounce`
  (an `auto` `go_back` after a cross-origin nav — reads as a jump cut).
  Pure function, no I/O, hand-fixture-testable — no Playwright required.
  Hooked into `auto.run_tour` at the tail of the summary print with a
  single ~7-line block; no schema change, no CLI change, no behaviour
  change for reels that don't trip a rule. Tracks B-G from #138 (sidecar
  `quality` block, `clickcast lint`, skill `pitfalls`, inline error hints,
  post-run summary integration, `doctor --for-agent`) deferred to
  follow-ups; Track A alone delivers the observable "AI running clickcast
  is told the reel is bad" signal.

### Deferred
- **#129 Tracks B/C/D** land in follow-ups so this PR stays
  observationally scoped. B (signal-aware post-click dwell) and C
  (symmetric close) form a chain — C needs B's "expanded step"
  signal. D (pending-state actions-panel row) depends on Track A
  landing (this PR). Track G (pre-first-paint white frame) is #68,
  which the title card partially masks in the meantime.

### Changed
- **Narrowed Session→Page seam** (closes [#98]). `Session` gains a
  small set of methods that hide Playwright's Page under a stable
  interface: `locator(selector)`, `evaluate(script, *args)`,
  `press_key(key)`, `wheel(dx, dy)`, `title()`, `url_now`, `on(event,
  cb)`, `off(event, cb)`. Playwright's `Locator` and `TimeoutError`
  types are re-exported from `clickcast.core.session` so downstream
  code never imports from `playwright.*` directly. Migrated:
  `clickcast.core.actions`, `clickcast.discovery.discovery`, and
  `clickcast.feedback.collector` all switch from
  `session.page.locator/evaluate/keyboard/mouse` (20 call sites) and
  `Page.on/remove_listener` to the new narrow surface. `collector`
  now takes a `Session` instead of a bare `Page`. Zero user-visible
  behavior change; unblocks session-swap for CI, session-level policy
  (retry, tracing), and cleaner dependency reasoning.
- **Grouped `BrowserOpts` + `RenderOpts` dataclasses** (closes [#97]).
  New `clickcast.core.opts` module holds the 8 browser-behaviour fields
  (`engine`, `viewport`, `device`, `headful`, `lang`, `dark`, `slowmo`,
  `proxy`) and 4 render-output fields (`fps`, `quality`, `loop`,
  `format`) as the single source of truth. `Meta` embeds them as nested
  fields; a pydantic `model_validator(mode="before")` accepts both the
  new nested YAML shape AND the legacy flat shape (flat wins over
  nested in a conflict, matching natural override intuition). `Meta`
  keeps `@property` accessors for the flat field names so shipped
  callers (`meta.engine`, `meta.viewport`, ...) don't have to change —
  writers migrate to the nested form (`meta.browser.headful = True`).
  `BrowserOpts.viewport` uses the `Viewport` value type from #96.
  `Session._session_kwargs` is now `BrowserOpts.to_session_kwargs()`.
  Config-side migration (env vars, TOML) deferred to a small follow-up
  so this PR stays scoped; existing env-var behaviour is unchanged.
- **Single `Viewport` value type** (closes [#96]). New
  `clickcast.core.viewport.Viewport` frozen dataclass replaces the six
  ad-hoc `"WxH"` parsers previously scattered across `session.py`,
  `cli.py`, `reel.py`, and `scripts/generate_demo.py`. Public API:
  `Viewport.parse(raw)` accepts `str | tuple[int, int] | Viewport`
  idempotently; `__str__` returns `"WxH"`; `as_tuple()` and `as_list()`
  for the downstream shapes Playwright and the sidecar respectively want.
  All viewport-accepting APIs (`Session.__init__`, `Reel.__init__`,
  `clickcast.discover(viewport=...)`) now accept `Viewport` instances too.
  Backwards-compatible for callers passing strings or tuples.
- **CLI type-alias rename**: `Viewport = Annotated[str, typer.Option(...)]`
  in `cli.py` is now `ViewportArg` to avoid shadowing the new value type.
  No user-facing change (only internal typing).

## [0.2.1] — 2026-07-29

A small-but-visible polish release focused on human-legible reels.
Backwards-compatible with 0.2.0 (all new fields default to shipped
behaviour).

### Added
- **`CursorStyle.single_arrow`** (opt-in, default `False` — chain mode
  preserved) — draws one arrow from the previous distinct cursor position
  to the current one that persists across every subsequent dwell frame
  until the next move, instead of a chain of per-hop arrows that flashes
  on during transitions and disappears once history fills. Reads as a
  single held A→B vector — much easier to follow for a human watching
  the reel without a sidecar. Backed by three sticky-state fields on
  `Annotator` (`_sticky_arrow_from` / `_to` / `_last_cursor`), reset by
  `Annotator.reset_cursor()`.
- **`ActionsPanelStyle.position: "top-right" | "top-left" |
  "bottom-right" | "bottom-left"`** (default `"top-right"` — shipped
  behaviour preserved) — anchors the actions panel to the chosen corner.
  The top-right default frequently sat exactly on a site's top-nav
  icons — the very click targets the tour was pointing at; `bottom-right`
  or `bottom-left` is safer for docs-style targets. See #129 for the
  broader "human-observable demo mode" context.

### Docs
- **New guide `docs/ONE_PAGE_NAVIGATION_ORDER_TIPS.md`** — nine
  principles for authoring scenarios that a human can watch without
  pausing (one-page tours, symmetric open/close pairs, explicit intent
  labels, ready-to-copy template + anti-patterns).
- **`docs/demo.gif` rebuilt as a scripted single-page tour of
  tailwindcss.com** — every click has a visible on-page reaction under
  the cursor, symmetric open/close pairs, no scene cuts. Replaces the
  previous auto-tour of react.dev.

### CI
- **`.github/workflows/demo.yml` defaults** — target changed from
  `worldsight-weld.vercel.app` (was producing 0-click reels) to
  `react.dev`, click-timeout bumped from 5 s to 15 s to survive
  cold-serverless first-paint. The auto-regenerate-after-release job
  won't overwrite the hero GIF with a broken one on the next release.

## [0.2.0] — 2026-07-26

Massive AI-agent-experience release. Three new CLI subcommands
(`report-bug`, `skill`, `assertions`), six new `Reel` methods
(`evaluate`, `wheel`, `wait_for`, `save_region`, `assertions`,
`serve_dir`), three new scenario step types (`evaluate`, `wheel`,
`wait_for`), the AI-agent feedback loop end-to-end, and a
security-tagged sidecar redaction feature. One flagged breaking change
(`AnnotateConfig` regrouped into sub-dataclasses); everything else is
additive.

### Added
- **`Reel.evaluate()`, `Reel.wheel()`, container-scoped `Reel.scroll()`**
  (closes [#108]). Three long-missing fluent-API primitives:
  `evaluate(js_expression, *args)` for arbitrary JS in the page;
  `wheel(dy, *, dx=0, selector=None)` for wheel-driven UIs; and extending
  `scroll` with an optional `selector` so container-scoped scroll works.
  New `EvaluateStep` / `WheelStep` in `actions.py`; scenario YAML gains
  matching step kinds.
- **`Reel.wait_for(selector, state='stable', ...)` + `goto(retries=N)`**
  (closes [#111]). `state='stable'` polls the element's bounding box every
  50ms and returns once it hasn't moved for `quiet_ms` — replaces the
  `dwell: 3.0`/`dwell: 5.0` guessing. `visible`/`hidden`/`attached`/
  `detached` delegate to Playwright's `locator.wait_for(state=...)`.
  `GotoStep.retries` wraps `session.goto` in a retry loop on
  `TimeoutError` with exponential backoff — smooths over cold-serverless
  first-load flakes.
- **`Reel.assertions()` + `assertions_diff()` + `clickcast assertions`
  CLI** (closes [#112]). CI-stable subset of the sidecar (same input →
  byte-identical output). `clickcast assertions <sidecar.json> [--baseline
  PATH] [--json]` exits nonzero on drift so `.github/workflows/*.yml`
  regression recipes collapse from ~50 lines to ~2. New
  `docs/assertions-schema/v1.json` versioned contract.
- **`Reel.serve_dir(path)` context manager + `serve_directory()` helper**
  (closes [#113]). Own the local static server's lifetime with a `with`
  block instead of `python3 -m http.server 8091 &`-and-hope. Loopback-only
  bind, `ThreadingHTTPServer`, auto-picks a free port. New
  `clickcast.serving` module.
- **`clickcast run --url <URL>` first-class flag** (closes [#115]).
  Overrides the first `goto` step's URL after scenario load. Precedence:
  `--url` > `--var URL=...` > scenario `meta.url` > `steps[0].url`.
- **Selector-not-found diagnostics with candidate hints** (closes [#114]).
  When a `click`/`hover`/`type`/`dblclick` selector resolves to 0
  elements, the executor scores every discovered element by role match
  (+0.4), name similarity (+0.6 × SequenceMatcher ratio), and an
  exact-name-match bonus (+0.5); top-5 attached to the step's `error`
  field. `--dump-elements` on `auto` / `run` echoes the full pool to
  stderr. New `clickcast.discovery.hints` module.
- **Sidecar URL / query-string redaction** (closes [#110]).
  `Reel(redact_patterns=[...], strip_query_strings=False)` (also
  `--redact-pattern` / `--strip-query-strings` on `auto` and `run`). Fixes
  a token-leak footgun for Vercel/Cloudflare/Netlify auth-bypass tokens
  baked into recorded URLs. Patterns applied to every string in the
  sidecar; matches replaced with `«redacted»`.
- **`Reel.save_region(selector, out, ...)` + `save_region_at_step`** (closes
  [#109]). Element-anchored crops from any captured frame — no more
  hand-picked pixel coords that rot with every layout tweak. Runs the
  scenario, re-navigates to the reel's URL in a throwaway Session to read
  the current bbox, then crops the target frame's PNG (bbox ± padding,
  clipped to viewport) and writes it. Signature:
  ``save_region(selector, out, *, frame=-1, padding=0, format='png')``;
  ``save_region_at_step(step_index, selector, out, ...)`` picks the last
  sub-frame of the given step. Missing selectors raise ``LookupError``
  with the URL + selector for actionable debugging. Available on both
  ``Reel`` (sync) and ``AsyncReel`` (async). Adds ``Session.bbox(selector)``
  helper as a byproduct.
- **`clickcast skill`** (closes [#103]). New subcommand that prints a single
  self-contained AI-friendly brief covering every command, when to use each,
  its key flags with examples, the machine-contract URLs (reel sidecar +
  agent-report schemas + docs), and the four #40 feedback pointers. Default
  Markdown output; `--json` emits a structured payload matching
  `docs/skill-schema/v1.json`. A drift-guard test fails if a newly added
  subcommand doesn't get a brief entry.
- **AI-agent feedback loop — all three tracks** (closes [#40]). Extends the
  `--with-feedback` sidecar block with the four #40-spec pointers
  (`report_url`, `schema_url`, `docs_url`, `diagnostics_command`), adds the
  same pointers to `clickcast doctor` (human + `--json`) and to every
  subcommand's `--help` epilog, and ships:
  - **`clickcast report-bug <sidecar.json>`** — new subcommand that turns
    a sidecar into an actionable bug report. Prints diagnostics + a
    prefilled GitHub issue URL (title + body ready to submit). Flags:
    `--json` (emit the Track-C payload), `--open` (launch in browser),
    `--redact/--no-redact` (default on — sanitize URLs, selectors, visible
    text while preserving structure and counts), `--note` (freeform
    environment context).
  - **`docs/agent-report-schema/v1.json`** — JSON Schema for the Track-C
    payload; downstream agents can populate it deterministically.
  - **`.github/ISSUE_TEMPLATE/ai-agent-report.yml`** — GitHub Issues form
    matching 1:1 to the schema.
  - **`docs/for-agents.md`** — <200-word walkthrough for AI agents on how
    to use clickcast and how to file feedback.
  - **`agent-report` GitHub label** — filed reports are auto-labelled by
    the template.
- **`--with-feedback` on `auto` and `run`** (initial slice of [#40]). The
  sidecar block now carries both the four spec pointers AND the additive
  human-friendly context (repo, issues URL, prefilled new-issue URL,
  message, template). Opt-in.
- **Smooth cursor interpolation** (closes [#75]). Between any two consecutive
  frames whose `cursor_xy` differs by at least `interpolate_min_distance` (50
  px default), insert `interpolate_frames` (4 default) intermediate PNG frames
  with the cursor at eased positions (`ease-in-out` smoothstep default;
  `linear` opt-in). Inserted frames physically copy the earlier frame (page
  pixels are identical between two cursor moves) and inherit the earlier
  frame's `step_index` so the actions-panel highlight stays stable during the
  glide. Removes the "teleport" feel of prior reels — the cursor now visibly
  travels between clicks. New `clickcast.annotate.interpolate` module with
  `interpolate_cursor_motion(frames_dir, config)`. Runs after zoom and before
  annotate in the `auto` pipeline. Default on; disable via
  `CursorStyle(interpolate=False)`.
- **`--zoom-on-click <factor>` on `auto`** (closes [#74], Shape A). For the
  first `zoom_frames_after_click` sub-frames after each click, crop the
  frame around the click point and scale back to viewport size. The reel
  jumps to a close-up for a beat, then returns to full-page. Applies
  pre-annotate so the ripple / cursor / label bar / actions panel land at
  the correct coords for the zoomed image. New `clickcast.annotate.zoom`
  module with `apply_zoom_on_click(frames_dir, *, factor, frames_after_click)`.
  Default off (`0.0` = disabled); typical usage is `--zoom-on-click 2.5`.
- **Directional cursor arrows** (closes [#73]). `CursorStyle.arrows`
  (default `True`) replaces the fading trail of dots with red arrows
  drawn between consecutive tracked cursor positions. Reads as motion
  vectors ("cursor went here → then here") — stronger signal for both
  human viewers and LLMs consuming the reel. `arrow_min_distance=10` skips
  jitter; `arrow_max_distance=600` skips misleading teleports (the
  recorder doesn't reset cursor history across page navigations). Set
  `arrows=False` to fall back to the original dots trail.

### Changed
- **`AnnotateConfig` regrouped into sub-dataclasses** (closes [#80]). The
  46 flat fields are now grouped by responsibility: `LabelStyle`,
  `RippleStyle`, `CursorStyle`, `ProgressStyle`, `ActionsPanelStyle` — all
  composed onto `AnnotateConfig` via `field(default_factory=...)`. Access
  moves from `config.label_bg_color` → `config.label.bg_color`. The 5
  layer toggles (`clicks`/`labels`/`cursor`/`progress`/`actions_panel`)
  and `font_path`/`font_size` stay on the top-level dataclass. Breaking
  change; the annotator's internal `_draw_*` methods and both annotator
  test files updated. Field-name collision resolved by naming the sub-
  dataclass fields `cursor_style` / `progress_style` (the shorter names
  are already the layer toggles).
- **CLI's per-command Config map is auto-derived from Typer signatures**
  (closes [#84]). Deleted the manual `_CONFIG_KEYS_PER_COMMAND` dict;
  `_config_default_map` now introspects `app.registered_commands` and
  intersects each command's parameter names with `Config` field names.
  Every new Config field automatically reaches its same-named CLI option
  — no more "add field, forget to update the map, config silently
  swallowed" class of bug (which bit us on `pace` in PR #77's first
  draft). Zero behavior change for the four commands the old map
  covered.

## [0.1.3] — 2026-07-24

A polish release. New `--pace` flag, scenario reels finally get overlays,
a fully populated `demo/` folder, and a big internal refactor that pulls
the auto engine into its own module + centralizes test infrastructure.

### Changed
- **Scenario reels now get overlays too** (closes [#83]). `clickcast run
  <scenario.yml>` used to produce plain unannotated screenshots because
  `_do_run` never called `annotate_frames_dir` — the annotator was wired
  into `_do_auto` in PR #53 but never `_do_run`. Now: after the scenario
  finishes, `_scenario_step_annotations` walks the scenario steps + the
  RunResult in lockstep with the recorder's step_index (repeat counts
  handled), synthesizes labels (`click: #save`) when the scenario doesn't
  set one, and adds click ripples for successful click/dblclick actions.
  `annotate_frames_dir` then paints the same overlays scenario users see
  from auto. `demo/bug-report/reel.gif` visibly grew from 13 KB → 48 KB
  (overlays add pixel variance).
- **Centralized test fixtures** (closes [#79]). Five `test_cli_auto_*.py`
  files used to redefine near-identical `_FakePage` / `_FakeSession` /
  `_make_element` / `_make_result` / `_stub_environment` stubs — ~500 LOC
  of duplicated infrastructure. Moved to `tests/_stubs.py` (dataclasses)
  + `tests/conftest.py` (`stub_environment` fixture). Test files use
  the shared pieces via imports + fixture injection. Test count unchanged
  (287); combined test-file LOC drops from 1935 to 1500.
- **Extracted shared auto engine** (closes [#78], closes [#81]). The BFS/DFS
  orchestration and per-page click loop moved from `clickcast.cli` (as
  `_do_auto` / `_explore_page`) to a new `clickcast.auto` module (as
  `run_tour` / `explore_page`) with a typed `AutoConfig` for inputs. Both
  the CLI and `scripts/generate_demo.py` now call `run_tour(AutoConfig(...))`
  — future bug fixes and features land once, not twice. `clickcast.cli._do_auto`
  remains as a thin shim for test-patch backwards-compat. Magic constants
  (`_GOTO_BACK_TIMEOUT_MS`, `_SCROLL_DISTANCE_PX`, `_INTER_CLICK_WAIT_S`,
  `_MIN_DISCOVERY_POOL`, `_MAX_CONSECUTIVE_FAILURES`) named at module scope.
  Net LOC: 1568 → 1501 across the three files, with the auto engine now in
  one place instead of two.

### Added
- **`--pace={fast,natural,slow,onboarding}` on `auto`** (closes [#76]). One
  flag sets `--fps` and `--dwell` together so users don't have to think
  about frame math. Explicit `--fps` / `--dwell` still win when set.
  Preset table: `fast` (15/0.15), `natural` (12/0.4, default), `slow`
  (10/0.7), `onboarding` (8/1.2). Plumbed into the layered `Config` so
  `CLICKCAST_PACE=slow` works. Also exposed on `scripts/generate_demo.py`.
- **`demo/` folder complete** (closes [#66]). All 8 use cases from the
  original issue now have subfolders with README + committed sample reel:
  `ai-eye-review/`, `site-cartography/`, `regression-visual-diff/`,
  `bug-report/`, `onboarding-tutorial/`, `a-b-comparison/`,
  `llm-doc-scraping/`, `accessibility-preflight/`. Reels are ~9 MB
  total, all generated against public sites so anyone can regenerate.

### Fixed
- **Demo scenario YAMLs used wrong shape.** `login-flow.yml` and
  `reproduce-bug-42.yml` (added in the initial demo folder PR) used
  `action: goto` style, but the scenario parser expects the action verb
  as the YAML key (`goto: url`). Fixed both to match `docs/scenarios/*.yml`.

## [0.1.2] — 2026-07-24

Big `auto`-mode release: multi-page BFS/DFS tours, hardened time budgets,
richer overlays for AI-eye consumption, and live progress logging.

### Added
- **Multi-page BFS tour in `auto`** ([#55]). `--max-pages` flag (default 5).
  Starting from the URL you pass, `auto` discovers elements, clicks them,
  and if a click navigates to a same-origin destination, that URL is queued
  for a follow-up tour. Cross-origin destinations ignored; visited URLs
  deduped by scheme/host/port/path (fragment stripped). New
  `clickcast.discovery.urlutil` module and page-labelled overlays.
- **`--traversal={dfs,bfs}` on `auto`** ([#65], default `dfs`). Changed the
  default URL queue policy from FIFO (BFS) to LIFO (DFS). DFS gives a
  coherent narrative reel that follows one link tree at a time — e.g.
  `Home → click Docs → Docs → click Getting-Started`. Better for both
  human viewers and AI-eye consumption. Pass `--traversal=bfs` for the
  old site-map style coverage.
- **`--seed-url` on `auto`** ([#67], repeatable). Agent-controllable BFS.
  When set, the tour visits exactly the initial URL + your seeds, in the
  order given, and does NOT auto-enqueue navigation destinations
  discovered during clicks. For AI agents that want a deterministic path.
- **`--max-duration` on `auto`** ([#63], default 120s). Hard wall-time cap
  on the whole tour. When hit, BFS stops and encodes whatever frames were
  captured. No more silent 30-minute hangs.
- **`--click-timeout` on `auto`** ([#63], default 5s). Overrides Playwright's
  30s per-op default so one stuck click can't stall the tour. Also plumbed
  `timeout_ms` through `BaseStep` for scenario authors.
- **Overlays on `auto` recordings** ([#53]). `auto` now composites the
  existing `Annotator` overlays (click ripples, per-step label banner,
  progress bar, cursor trail) onto captured frames before encoding. New
  `annotate_frames_dir()` helper + `StepAnnotation` dataclass under
  `clickcast.annotate`.
- **AI-eye overlays** ([#69], closes [#57]).
  - **Light-mode label banner** — `AnnotateConfig.label_style="light"`
    (now default) puts step labels on white with dark text. Old dark
    banner blended into dark-mode sites (react.dev); light reads on both.
  - **Actions panel** — `AnnotateConfig.actions_panel` (default on)
    composites a top-right side panel listing recent step labels with the
    current one highlighted. Gives viewers an at-a-glance map of "where
    we are in the tour."
- **Live progress logging during `auto` tours** ([#60], closes [#59]).
  With `-v` you now get an INFO line for every click, every same-origin
  nav → go_back, per-page summary, and wall-clock time in the success
  line. `-vv` adds DEBUG traces.

### Fixed
- **`auto` BFS no longer starves on nav-heavy sites** ([#56]). The previous
  click-loop `break`-on-first-nav meant one useless click (e.g. clicking
  the site logo, which nav's to an already-visited URL) exited the loop
  and left the queue empty. Now: after a same-origin nav, `page.go_back()`
  and continue clicking the remaining discovered elements.
- **`auto` go_back no longer hangs on WebSocket / HMR sites** ([#58]).
  `wait_until="networkidle"` hung indefinitely on sites that keep
  WebSockets / SSE / dev-server HMR channels open (react.dev burned 30+
  min of CI). Switched to `wait_until="domcontentloaded"` with a hard 5s
  timeout.
- **`--max-steps` is now a global click budget** ([#58]), not per-page. Old
  semantics could fire 125 clicks (25 × 5) and overrun even modest CI
  budgets. Default bumped to 15.
- **`auto` click loop bails after 3 consecutive failures** ([#61]).
  Post-hydration DOM drift meant a page's discovered selectors could all
  fail — the loop then walked the whole 20-element pool clicking dead
  targets (~30s each, ~500s wasted per page).
- **Progress counter now reflects attempts, not successes** ([#61]). Old
  log froze at the last successful click number. New format
  `attempt N (X/max clicked)` — attempt counter always advances.
- **Discovery disambiguates non-unique selectors** ([#64], closes [#62]).
  Real doc sites use the same accessible name in header and footer nav,
  so `role=link[name="Community"]` matches 2+ elements and Playwright's
  strict mode blocks the click. `_discover_on_page` now checks
  `page.locator(sel).count()` per element; when >1, appends `>> nth=0` so
  the locator picks the first match.
- **Seeded tours honor URL commitment past click budget** ([#67]). With
  `--seed-url` set and a tight `--max-steps`, the outer BFS loop used to
  exit after page 1, silently dropping the seeds the caller committed to.
  Now seeded tours continue past budget exhaustion (remaining seeds get
  goto + scroll only).

### CI
- **Descriptive workflow names** ([#53]). `ci` → `CI (lint + test matrix)`,
  `release` → `Publish release (TestPyPI → PyPI → GitHub release)`,
  `demo` → `Regenerate README demo GIF`. Makes the Actions tab readable.
- **Demo GIF auto-regenerates after each release** ([#53]). `demo.yml`
  gains a `workflow_run` trigger fired on successful release, so
  `docs/demo.gif` stays in sync with the published version.

## [0.1.1] — 2026-07-24

### Fixed
- **Config actually reaches subcommand defaults.** `CLICKCAST_*` env vars and
  values in `~/.config/clickcast/config.toml` or `./clickcast.toml` were being
  loaded into `Config` but never applied to CLI options, so
  `CLICKCAST_ENGINE=firefox clickcast auto ...` silently ran chromium. Wired
  the resolved config into Typer's `default_map`; explicit CLI flags still win.
  ([#41], [#48])
- **`config set` preserves user input.** The hand-rolled TOML writer reordered
  keys, dropped comments, and silently rewrote `[defaults]`-wrapped files as
  flat. Switched to `tomlkit`; whitespace, comments, key order, and the
  wrapper table now round-trip cleanly. ([#42], [#50])
- **Malformed user TOML no longer silent.** A typo in `config.toml` used to
  revert every setting without a peep. `_read_toml` now emits a `UserWarning`
  and still falls back to defaults so the CLI keeps working. ([#42], [#50])
- **GIF encoder no longer leaks file handles.** `Image.open(...).convert(...)`
  never released the underlying file handle; refactored to `with Image.open(f)
  as src:`. ([#42], [#47])
- **Page listeners detach after `ReportBuilder.build`.** `PageStateCollector`
  attached `console`/`pageerror`/`requestfailed` listeners but never removed
  them, so repeated `Session` use in one process leaked listeners. Added
  `PageStateCollector.detach()` and call it from `ReportBuilder.build()`.
  ([#42], [#47])

### Added
- `tomlkit>=0.12` runtime dependency (for structure-preserving `config set`).

### CI
- Test matrix now skips on docs-only pushes (`**.md`, `LICENSE`, `.gitignore`,
  `docs/*.md`) — trivial README edits no longer burn the 8-job matrix. ([#49])

## [0.1.0] — 2026-07-23

Initial public release.

- Drive a browser (Playwright — chromium / firefox / webkit) through a
  scenario YAML or a bare URL and produce a reel (`.gif`) plus an
  AI-readable feedback sidecar (`.json` matching `schema/v1.json`).
- CLI: `clickcast auto | run | shot | elements | config | ...` (Typer-based).
- Layered configuration: CLI flags → `CLICKCAST_*` env → project
  `./clickcast.toml` → user `~/.config/clickcast/config.toml` → defaults.
  (Note: in 0.1.0 the CLI did not consume the resolved config — see the 0.1.1
  fix above.)
- Automated release pipeline: tag `v*` → TestPyPI → smoke matrix (Linux/macOS
  × Python 3.10–3.13) → PyPI → GitHub release, all via Trusted Publishing.

[Unreleased]: https://github.com/AlexKay28/clickcast/compare/v0.1.3...HEAD
[0.2.1]: https://github.com/AlexKay28/clickcast/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AlexKay28/clickcast/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/AlexKay28/clickcast/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/AlexKay28/clickcast/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/AlexKay28/clickcast/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/AlexKay28/clickcast/releases/tag/v0.1.0

[#41]: https://github.com/AlexKay28/clickcast/issues/41
[#42]: https://github.com/AlexKay28/clickcast/issues/42
[#47]: https://github.com/AlexKay28/clickcast/pull/47
[#48]: https://github.com/AlexKay28/clickcast/pull/48
[#49]: https://github.com/AlexKay28/clickcast/pull/49
[#50]: https://github.com/AlexKay28/clickcast/pull/50
[#53]: https://github.com/AlexKay28/clickcast/pull/53
[#55]: https://github.com/AlexKay28/clickcast/pull/55
[#56]: https://github.com/AlexKay28/clickcast/pull/56
[#57]: https://github.com/AlexKay28/clickcast/issues/57
[#58]: https://github.com/AlexKay28/clickcast/pull/58
[#59]: https://github.com/AlexKay28/clickcast/issues/59
[#60]: https://github.com/AlexKay28/clickcast/pull/60
[#61]: https://github.com/AlexKay28/clickcast/pull/61
[#62]: https://github.com/AlexKay28/clickcast/issues/62
[#63]: https://github.com/AlexKay28/clickcast/pull/63
[#64]: https://github.com/AlexKay28/clickcast/pull/64
[#65]: https://github.com/AlexKay28/clickcast/pull/65
[#67]: https://github.com/AlexKay28/clickcast/pull/67
[#69]: https://github.com/AlexKay28/clickcast/pull/69
[#66]: https://github.com/AlexKay28/clickcast/issues/66
[#76]: https://github.com/AlexKay28/clickcast/issues/76
[#78]: https://github.com/AlexKay28/clickcast/issues/78
[#79]: https://github.com/AlexKay28/clickcast/issues/79
[#80]: https://github.com/AlexKay28/clickcast/issues/80
[#81]: https://github.com/AlexKay28/clickcast/issues/81
[#83]: https://github.com/AlexKay28/clickcast/issues/83
[#84]: https://github.com/AlexKay28/clickcast/issues/84
[#73]: https://github.com/AlexKay28/clickcast/issues/73
[#74]: https://github.com/AlexKay28/clickcast/issues/74
[#75]: https://github.com/AlexKay28/clickcast/issues/75
[#40]: https://github.com/AlexKay28/clickcast/issues/40
[#103]: https://github.com/AlexKay28/clickcast/issues/103
[#109]: https://github.com/AlexKay28/clickcast/issues/109
[#108]: https://github.com/AlexKay28/clickcast/issues/108
[#110]: https://github.com/AlexKay28/clickcast/issues/110
[#111]: https://github.com/AlexKay28/clickcast/issues/111
[#112]: https://github.com/AlexKay28/clickcast/issues/112
[#113]: https://github.com/AlexKay28/clickcast/issues/113
[#114]: https://github.com/AlexKay28/clickcast/issues/114
[#115]: https://github.com/AlexKay28/clickcast/issues/115
[#129]: https://github.com/AlexKay28/clickcast/issues/129
[#96]: https://github.com/AlexKay28/clickcast/issues/96
[#97]: https://github.com/AlexKay28/clickcast/issues/97
[#98]: https://github.com/AlexKay28/clickcast/issues/98
[#99]: https://github.com/AlexKay28/clickcast/issues/99
[#138]: https://github.com/AlexKay28/clickcast/issues/138
[#124]: https://github.com/AlexKay28/clickcast/issues/124
[#43]: https://github.com/AlexKay28/clickcast/issues/43
[#46]: https://github.com/AlexKay28/clickcast/issues/46
[#151]: https://github.com/AlexKay28/clickcast/issues/151
[#166]: https://github.com/AlexKay28/clickcast/issues/166
[#168]: https://github.com/AlexKay28/clickcast/pull/168
[#170]: https://github.com/AlexKay28/clickcast/issues/170
[#172]: https://github.com/AlexKay28/clickcast/issues/172
[#173]: https://github.com/AlexKay28/clickcast/issues/173
[#174]: https://github.com/AlexKay28/clickcast/issues/174
[#175]: https://github.com/AlexKay28/clickcast/issues/175
[#176]: https://github.com/AlexKay28/clickcast/issues/176
[#177]: https://github.com/AlexKay28/clickcast/issues/177
[#178]: https://github.com/AlexKay28/clickcast/issues/178
[#171]: https://github.com/AlexKay28/clickcast/issues/171
[#191]: https://github.com/AlexKay28/clickcast/issues/191
[#192]: https://github.com/AlexKay28/clickcast/issues/192
[#193]: https://github.com/AlexKay28/clickcast/issues/193
[#194]: https://github.com/AlexKay28/clickcast/issues/194
[#195]: https://github.com/AlexKay28/clickcast/issues/195
[#196]: https://github.com/AlexKay28/clickcast/issues/196
[#197]: https://github.com/AlexKay28/clickcast/issues/197
[#198]: https://github.com/AlexKay28/clickcast/issues/198
[#199]: https://github.com/AlexKay28/clickcast/issues/199
[#200]: https://github.com/AlexKay28/clickcast/issues/200
[#201]: https://github.com/AlexKay28/clickcast/issues/201
[#202]: https://github.com/AlexKay28/clickcast/issues/202
[#203]: https://github.com/AlexKay28/clickcast/issues/203
[#204]: https://github.com/AlexKay28/clickcast/issues/204
[#205]: https://github.com/AlexKay28/clickcast/issues/205
[#206]: https://github.com/AlexKay28/clickcast/issues/206
[#207]: https://github.com/AlexKay28/clickcast/issues/207
[#208]: https://github.com/AlexKay28/clickcast/issues/208
[#209]: https://github.com/AlexKay28/clickcast/issues/209
[#210]: https://github.com/AlexKay28/clickcast/issues/210
[#222]: https://github.com/AlexKay28/clickcast/issues/222
