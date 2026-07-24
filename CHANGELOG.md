# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/AlexKay28/clickcast/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/AlexKay28/clickcast/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/AlexKay28/clickcast/releases/tag/v0.1.0

[#41]: https://github.com/AlexKay28/clickcast/issues/41
[#42]: https://github.com/AlexKay28/clickcast/issues/42
[#47]: https://github.com/AlexKay28/clickcast/pull/47
[#48]: https://github.com/AlexKay28/clickcast/pull/48
[#49]: https://github.com/AlexKay28/clickcast/pull/49
[#50]: https://github.com/AlexKay28/clickcast/pull/50
