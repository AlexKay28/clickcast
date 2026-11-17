# Contributing to clickcast

Thanks for taking the time to contribute. This document covers the development
loop, what CI checks, how work is organised, and where the deeper design context
lives. It assumes you already know what the project does — if not, start with
[`README.md`](README.md).

- [Development setup](#development-setup)
- [The loop: lint, format, types, tests](#the-loop-lint-format-types-tests)
- [Tests and markers](#tests-and-markers)
- [Before you open a PR](#before-you-open-a-pr)
- [Issues, labels, and finding work](#issues-labels-and-finding-work)
- [Where design context lives](#where-design-context-lives)
- [Reporting bugs and security issues](#reporting-bugs-and-security-issues)
- [Releasing](#releasing)

## Development setup

`clickcast` requires **Python ≥ 3.10** (see `requires-python` in
[`pyproject.toml`](pyproject.toml)). CI runs the suite on 3.10–3.13; developing
on any of those is fine, and it is worth checking a change against the lowest
supported version when it touches typing or stdlib APIs.

```bash
git clone https://github.com/AlexKay28/clickcast
cd clickcast
pip install -e ".[dev]"
clickcast install                # one-time Chromium download (~180 MB)
clickcast doctor                 # confirms Python, Chromium, ffmpeg, config path
pre-commit install               # wire the hooks into .git/hooks
```

A few notes on the extras:

- `.[dev]` is the one-stop contributor install: it pulls in the `test` extra
  (pytest, pytest-asyncio, pytest-cov), the `mcp` extra (needed only for
  `clickcast mcp`), plus ruff, mypy, pre-commit, `types-PyYAML`, and `build`.
- `.[test]` is the lean subset used by CI test shards if you only need pytest.
- CI-level Python compatibility is enforced by the matrix, not by mypy — mypy
  is configured against 3.12 because of upstream stub syntax. Don't "fix" that
  by raising `requires-python`.

## The loop: lint, format, types, tests

These are the exact commands CI runs
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), and the ones
[`.pre-commit-config.yaml`](.pre-commit-config.yaml) enforces locally (ruff
lint + format, trailing whitespace, end-of-file, YAML/TOML validity,
large-file check):

```bash
ruff check .            # lint (pycodestyle, pyflakes, isort, bugbear, pyupgrade, simplify, ruff)
ruff format --check .   # formatting; use `ruff format .` to apply
mypy                    # strict type check over src/clickcast (config in pyproject.toml)
pytest -m "not slow"    # what CI runs: unit + integration, no slow encode/full-tour reels
```

`mypy` with no arguments type-checks `src/clickcast` with `strict = true`; the
target is set in `[tool.mypy]`, so you don't need to pass paths. Ruff's line
length is 100 and `E501` is intentionally ignored — the formatter owns line
length, so don't hand-wrap to satisfy a linter.

Fixing the common ones:

```bash
ruff check . --fix      # apply safe autofixes
ruff format .           # rewrite files in place
pre-commit run --all-files
```

Because CI invokes the same ruff commands directly, a failing pre-commit hook
and a failing CI lint job are the same failure. Run the loop above before
pushing and you will not be surprised.

## Tests and markers

Pytest markers are declared in `[tool.pytest.ini_options]`:

| Marker | Meaning |
| --- | --- |
| `unit` | Fast, no browser. Most tests. |
| `integration` | Launches a real browser against the local fixture site in `tests/fixtures/site/`, served by the session-scoped `fixture_site_url` fixture in `tests/conftest.py`. |
| `slow` | Encoding and full-tour reels — the expensive end-to-end paths. |

Marker selection is how you keep the loop fast:

```bash
pytest -m "not integration"               # unit-only, no browser (~seconds)
pytest -m "not slow"                      # CI's selection: units + integration
pytest -m integration                     # only browser-backed tests
pytest tests/test_actions.py              # one file
pytest tests/test_actions.py::test_name   # one test
pytest -k "keyword"                       # substring match across the suite
pytest                                    # full suite; needs Chromium installed
```

`tests/conftest.py` also provides `stub_environment`, which patches the heavy
dependencies the `auto` engine touches (`Session`, `Recorder`, `encode`,
`annotate_frames_dir`, `_write_sidecar`, `ReportBuilder`). Prefer it over
mocking those pieces individually — it keeps unit tests browser-free and keeps
the stubs in one place.

The fixture site is the intended target for integration tests: it is local,
deterministic, and doesn't depend on the public internet.

## Before you open a PR

Open an issue first for anything larger than a small fix — especially new
commands, sidecar schema changes, or behaviour changes to the action engine.
It is much cheaper to agree on the shape before the code exists.

Then:

1. Branch off `main`.
2. Make the change, with a test that would fail without it.
3. Run the loop above (`ruff check .`, `ruff format --check .`, `mypy`,
   `pytest -m "not slow"`).
4. Add a `CHANGELOG.md` entry under `## [Unreleased]` for anything
   user-facing. Internal refactors with no observable behaviour change don't
   need one.
5. Open the PR and fill in the template.

Guidelines that keep review cheap:

- **One concern per PR.** Avoid unrelated refactors in a bug-fix branch; they
  make the diff harder to review and harder to revert.
- **Test the behaviour, not the implementation.** Prefer a test that fails for
  the right reason.
- **Keep command functions thin.** Business logic belongs in the subsystem
  module (see the architecture section of [`CLAUDE.md`](CLAUDE.md)), not in
  `src/clickcast/cli.py`.
- **Sidecar schema changes are versioned and strictly additive.** If you touch
  `src/clickcast/feedback/schema/`, say so explicitly in the PR — downstream
  agents gate on those fields.

## Issues, labels, and finding work

Issues live at
<https://github.com/AlexKay28/clickcast/issues>. The label taxonomy currently in
use:

| Label | Use |
| --- | --- |
| `bug` | Something is broken. |
| `enhancement` | New feature or capability. |
| `documentation` | Docs, examples, templates. |
| `question` | Needs clarification rather than a code change. |
| `good first issue` | Scoped, self-contained, and we are prepared to mentor it end-to-end. |
| `help wanted` | We want it done and would welcome an outside PR. |
| `agent-experience` | Feedback loops, docs, and tooling for AI agents using clickcast. |
| `agent-report` | Auto-applied to machine-filed reports from `clickcast report-bug`. |
| `refactor` / `refactoring` / `ci` | Code health, restructuring, build/release pipelines. |
| `roadmap`, `mvp` | High-level scope markers; not usually actionable on their own. |
| `epic:kernel`, `epic:agent-api`, `epic:oss-readiness`, `epic:trust-safety` | Multi-issue workstreams. Pick a sub-issue, not the epic. |
| `priority:P0` / `P1` / `P2` | Blocking next milestone / current milestone / backlog. |
| `size:S` / `size:M` / `size:L` | Under a day / a few days / a week or more. |
| `duplicate`, `invalid`, `wontfix` | Closed without action. |

**What `good first issue` means here:** the issue has a bounded scope, points
at the files involved, has an acceptance criterion you can check locally, and
a maintainer has committed to reviewing promptly and answering questions. It
does not mean "trivial" — it means "no hidden design decisions". If you get
stuck, ask in the issue rather than guessing; unblocking you is part of the
deal.

If an issue is unlabelled or ambiguous, comment on it before writing code.

## Where design context lives

The detailed architecture and pipeline guide is
[`CLAUDE.md`](CLAUDE.md) — despite the filename it is the most complete
description of the codebase: the linear
`URL → Session → Actions → Recorder → Encoder → sidecar` pipeline, what each
subsystem owns, and where new logic belongs. Humans are the intended audience
too.

Deeper material under [`docs/`](docs/):

- [`docs/for-agents.md`](docs/for-agents.md) — how AI agents use clickcast and
  what makes a good report.
- [`docs/feedback-schema.md`](docs/feedback-schema.md) — the sidecar contract;
  the shipped JSON Schema is `src/clickcast/feedback/schema/v4.json`.
- [`docs/mcp.md`](docs/mcp.md) and
  [`docs/mcp-tool-schema.md`](docs/mcp-tool-schema.md) — the live-agent MCP
  server.
- [`docs/scenarios/`](docs/scenarios/) — scenario authoring.
- [`docs/ci/README.md`](docs/ci/README.md) — the GitHub Action, plus the plain
  shell CI recipe.
- [`docs/packaging/`](docs/packaging/) — Homebrew, apt, and npm distribution,
  each with its rationale.

## Reporting bugs and security issues

For ordinary bugs, use the issue forms:

- **Something broken** → the *Bug report* template
  ([`.github/ISSUE_TEMPLATE/bug_report.yml`](.github/ISSUE_TEMPLATE/bug_report.yml)).
- **New capability** → the *Feature request* template
  ([`.github/ISSUE_TEMPLATE/feature_request.yml`](.github/ISSUE_TEMPLATE/feature_request.yml)).
- **Filed by an AI agent / `clickcast report-bug`** → the *AI-agent bug report*
  template ([`.github/ISSUE_TEMPLATE/ai-agent-report.yml`](.github/ISSUE_TEMPLATE/ai-agent-report.yml)),
  which maps 1:1 to `docs/agent-report-schema/v1.json`.

Include your clickcast version (`clickcast --version`), Python version, OS,
install method, and — where possible — a minimal reproduction and the relevant
sidecar excerpt. Redact URLs and visible text if the target is private.

**Do not file security issues publicly.** Report them privately through the
process in [`.github/SECURITY.md`](.github/SECURITY.md). If that file is not
present in your checkout yet, contact the maintainer directly rather than
opening a public issue with exploit details.

## Releasing

Releases are cut by maintainers from a `vX.Y.Z` git tag, which drives
[`.github/workflows/release.yml`](.github/workflows/release.yml) (build →
TestPyPI → smoke test → PyPI → GitHub release, via PyPI Trusted Publishing).
The full process — version bumps across `pyproject.toml`, `server.json`, and the
npm packages, CHANGELOG discipline, and how to fix a broken tag — is in
[`RELEASING.md`](RELEASING.md). Don't hand-roll a release.

Contributors do not need to touch release machinery; just make sure the
CHANGELOG entry for your change is under `## [Unreleased]`.
