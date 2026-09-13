# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`clickcast` is a Python CLI + library that drives a real (Playwright) browser through a website and hands back two things: a watchable reel (GIF/MP4/WebP/frames) and a machine-readable JSON "sidecar" describing every step (selector, timing, per-step frames, page state, errors). It's aimed primarily at AI agents that need a visual modality onto a live web UI, secondarily at humans wanting reproducible demo reels. There's also a live-control mode via an MCP server (`clickcast mcp`).

## Commands

```bash
pip install -e ".[dev]"          # editable install + lint/type/test tooling
clickcast install                # downloads chromium (one-time; playwright)

ruff check .                     # lint
ruff format --check .            # format check
mypy                             # strict type check (src/clickcast only, see pyproject.toml)

pytest -m "not integration"      # fast unit tests, no browser (~2s)
pytest -m "not slow"             # what CI runs (includes integration, excludes slow encode/full-tour)
pytest                           # full suite; needs chromium installed
pytest tests/test_actions.py::test_name   # single test
pytest -k "keyword"              # substring match across the suite
```

Test markers (`pyproject.toml`): `unit` (no browser), `integration` (launches a browser against the local fixture site in `tests/fixtures/site/`, served by the session-scoped `fixture_site_url` fixture in `tests/conftest.py`), `slow` (encoding + full-tour reels).

`tests/conftest.py` also provides `stub_environment`, which patches every heavy dependency the `auto` engine touches (`Session`, `Recorder`, `encode`, `annotate_frames_dir`, `_write_sidecar`, `ReportBuilder`) so most unit tests run without a real browser at all. Prefer that over mocking these individually.

Pre-commit hooks (`.pre-commit-config.yaml`) run ruff (lint + format) plus the standard trailing-whitespace/EOF/yaml/toml/large-file checks — CI runs the same ruff commands directly, so a failing pre-commit hook and a failing CI lint job are the same failure.

Releases are cut via git tag (`vX.Y.Z`) which drives `.github/workflows/release.yml` (build → TestPyPI → smoke test → PyPI → GitHub release, all via PyPI Trusted Publishing/OIDC). Full process, including CHANGELOG discipline, is in `RELEASING.md` — don't hand-roll a release.

## Architecture

Pipeline, roughly linear, each stage owning one concern:

```
URL → Session (Playwright) → Actions (auto or YAML) → Recorder (per-step PNGs)
                                                       → Encoder → .gif/.mp4/.webp
                          ↳ PageStateCollector (console/page errors, failed requests)
                                                       → ReportBuilder → <out>.json sidecar
```

- **`core/session.py`** — owns the Playwright browser/context/page lifecycle (launch, viewport/device emulation, proxy, teardown). Everything else operates on a `Session`.
- **`core/actions.py`** — the action engine: pydantic `Step` models (`click`, `type`, `scroll`, `goto`, ...) and `execute(step, session)`, the single dispatcher used by *both* the batch scenario runner and the live MCP server, so action semantics (timeouts, error classification, selector-hint suggestions) never diverge between the two modes.
- **`discovery/`** — auto-discovery of "worth clicking" elements (`discovery.py`, DOM heuristics + accessibility tree via `accessibility.py`), used by `clickcast auto` and `clickcast elements`.
- **`scenario/scenario.py`** — YAML scenario parsing (`meta:` + `steps:`) and the runner that drives `execute()` over a `Session`, wiring in `Recorder` and `ReportBuilder`.
- **`auto.py`** — the `auto` mode: discover elements, decide a click order, drive the same action engine as `run`, with its own config/pacing/deadline logic (BFS-style multi-page traversal).
- **`capture/recorder.py`** — turns the action stream into an ordered set of PNG frames + a manifest (`frames.json`); deterministic filenames, byte-identical copies for padding/dwell frames.
- **`annotate/`** — post-capture overlay pass (`pipeline.py` walks `frames.json` and composites in place): click ripples, cursor trail, caption bar, progress bar, actions panel, coordinate grid overlay. Runs after recording, before encoding.
- **`encode/encoder.py`** — frames directory → final artifact (gif/mp4/webp/frames), via Pillow / `imageio[ffmpeg]`.
- **`feedback/`** — the sidecar subsystem:
  - `collector.py` (`PageStateCollector`) subscribes to `console`/`pageerror`/`requestfailed` events per step.
  - `models.py` — pydantic models for the sidecar schema (v1 fields + v2 `graph` + v3 gates/skip-reasons). `SkipReason` and `ErrorCode` are the enumerated fields agents are meant to gate on instead of parsing prose.
  - `builder.py` (`ReportBuilder`) accumulates the running pipeline into a `Report` and writes `<out>.json`.
  - `graph.py` — pure builder turning the step ledger into a `Graph` (page nodes + navigation edges) — an app-shape summary distinct from the literal step sequence.
  - `assertions.py` — distills a `Report` into a CI-stable, byte-identical-across-runs shape (`clickcast assertions --baseline`) by scrubbing timestamps/durations/frame paths/URLs.
  - `visual_diff.py` — pixel-level companion to `assertions` (`clickcast diff`): pairs steps by index (falls back to label matching on step-count mismatch), pixel-diffs frames, reports percent-changed + bounding regions.
  - `redact.py`, `pointers.py`, `report_bug.py`, `advisories.py` — sidecar redaction, doc-link pointers embedded in output, `clickcast report-bug` support, and the stderr advisory system (`⚠ <message> — see <docs-url>` for known anti-patterns).
- **`config/config.py`** — layered `Config` (pydantic-settings): CLI flags → scenario `meta:` → `CLICKCAST_*` env vars → project `./clickcast.toml` → user TOML (`clickcast config path`) → built-in defaults. `config/cli.py` is the `clickcast config` subcommand.
- **`mcp/server.py`** — the live-agent-control MCP server. v1 is single-session/single-process: one `ClickcastSessionState` backs the whole server; each action tool is a thin wrapper that builds the matching `Step` and calls the *same* `execute()` the batch path uses. See `docs/mcp.md` / `docs/mcp-tool-schema.md`.
- **`cli.py`** — the Typer app wiring every subcommand (`auto`, `run`, `shot`, `init`, `elements`, `doctor`, `config`, `install`, `mcp`, `assertions`, `diff`, `skill`, `report-bug`). Command functions stay thin and dispatch into the subsystems above — put business logic in the subsystem module, not here.
- **`skill.py`** — powers `clickcast skill`, a single-message self-introduction for AI agents. Command names are introspected live from the Typer app (so a new subcommand can't silently miss the brief — see `tests/test_skill.py`'s drift guard); narrative fields are hand-authored.
- **`reel.py`** — the fluent Python API (`Reel` / `AsyncReel`), a chainable builder over the same pipeline the CLI uses.
- **`serving.py`** — `serve_directory` / `Reel.serve_dir`, a context-manager static file server for reeling a local static build before pushing.

Sidecar schema is versioned; the JSON Schema itself lives at `src/clickcast/feedback/schema/v1.json` (shipped in the wheel) and is documented in `docs/feedback-schema.md`. A standalone (no-`clickcast`-import) reference consumer is `tests/consumer/read_sidecar.py`.

## Distribution

Three packaging surfaces beyond PyPI, all generated/driven from this repo — don't hand-edit generated artifacts without checking the generator:

- **Homebrew**: `Formula/clickcast.rb`, generated by `scripts/homebrew_formula.py`.
- **apt/.deb**: built via `scripts/build_deb.sh` / `scripts/apt_package.py`.
- **npm**: `npm/clickcast` and `npm/clickcast-mcp` are thin Node wrappers whose `postinstall` provisions an isolated Python venv and pip-installs the pinned PyPI `clickcast` (there's no way to ship the Playwright/Pillow/ffmpeg runtime as pure JS). Shared provisioning logic in `npm/shared/` is vendored into both packages rather than a `file:` dependency — see `docs/packaging/npm.md` for why.

Full rationale and bootstrap steps for each live under `docs/packaging/`.
