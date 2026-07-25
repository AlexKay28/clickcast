# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
