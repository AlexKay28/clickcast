# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
[#138]: https://github.com/AlexKay28/clickcast/issues/138
[#124]: https://github.com/AlexKay28/clickcast/issues/124
[#43]: https://github.com/AlexKay28/clickcast/issues/43
[#46]: https://github.com/AlexKay28/clickcast/issues/46
