# CI: the `clickcast` GitHub Action

`.github/actions/clickcast/` packages the shell recipe from the main
[README's "CI: 2-line regression gate"](../../README.md#ci-2-line-regression-gate)
section into a reusable, versioned GitHub Action: `install` -> `run`/`auto`
-> `assertions --baseline` -> `diff` -> a single PR comment with the reel
GIF and a summary table. See [#206](https://github.com/AlexKay28/clickcast/issues/206)
(and sub-issues [#207](https://github.com/AlexKay28/clickcast/issues/207),
[#208](https://github.com/AlexKay28/clickcast/issues/208),
[#209](https://github.com/AlexKay28/clickcast/issues/209),
[#210](https://github.com/AlexKay28/clickcast/issues/210)) for the full spec
this implements.

> **Depends on [#211](https://github.com/AlexKay28/clickcast/pull/211)
> (`feat/visual-diff`).** The `clickcast diff` command this Action's visual
> gate shells out to doesn't exist on `main` yet. Until #211 merges and a
> release ships with it, `baseline-sidecar` / `diff-*` inputs are
> non-functional against a real PyPI install — `scenario`/`url` + `baseline`
> (the structural `assertions` gate) work standalone today.

## How it relates to the manual shell recipe

The main README's 2-line gate (`clickcast run` + `clickcast assertions
--baseline`) is still the right answer for non-GitHub-Actions CI (GitLab CI,
CircleCI, Jenkins, a plain pre-push hook, ...) — it's two shell lines with no
platform lock-in. This Action is that same recipe, plus:

- Playwright/Chromium caching so you're not re-downloading ~180MB of browser
  on every run.
- The pixel-level `clickcast diff` gate wired up alongside `assertions`
  (optional — omit `baseline-sidecar` to run structural-only, exactly like
  the shell recipe).
- The reel GIF and a markdown summary table posted directly on the PR,
  instead of something you have to go dig out of CI logs / a build artifact.

Reach for the Action on GitHub Actions; reach for the shell recipe
everywhere else (or if you'd rather not depend on a third-party Action at
all — it's two lines, after all).

## Quick start

```yaml
# .github/workflows/clickcast.yml
name: clickcast

on:
  pull_request:

permissions:
  contents: write       # only needed if publish-reel-image (default) is on
  pull-requests: write  # only needed if post-comment (default) is on

jobs:
  reel:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: AlexKay28/clickcast/.github/actions/clickcast@main  # pin to a tag once one exists, see "Versioning" below
        with:
          scenario: scenarios/tour.yml
          out: reel.gif
          baseline: tests/golden-tour.json          # from `clickcast assertions reel.gif.json > tests/golden-tour.json`
          baseline-sidecar: tests/golden-tour.raw.json  # raw sidecar + committed frames/ — see below; omit to skip the visual gate
          diff-fail-above: '5'
          clickcast-version: '0.3.0'                # pin — see "Versioning"
```

That's the whole thing a downstream repo needs to write. Everything else
(installing clickcast, caching Chromium, running the scenario, gating,
commenting) is inside the Action.

If your scenario is written against a `{{ base_url }}` variable (e.g. to
point it at a PR preview, or a fixture served over `serve_directory` in an
earlier step), pass it via `vars`:

```yaml
      - uses: AlexKay28/clickcast/.github/actions/clickcast@main
        with:
          scenario: scenarios/tour.yml
          vars: |
            base_url=https://pr-${{ github.event.pull_request.number }}.preview.example.com
```

For `clickcast auto`'s URL-discovery mode instead of a scenario, set `url`
instead of `scenario`.

## Inputs and outputs

`.github/actions/clickcast/action.yml` is the source of truth — every input
and output is documented inline there with its default. In summary:

| Input | Purpose |
| --- | --- |
| `scenario` / `url` | What to run — a scenario YAML (`clickcast run`) or a bare URL (`clickcast auto`). Set exactly one. |
| `vars` | Newline-separated `KEY=VALUE` pairs for `clickcast run --var` (scenario mode only). |
| `out` | Output reel path. Default `reel.gif`. |
| `baseline` | Committed, **distilled** assertions baseline (`clickcast assertions <sidecar> > baseline.json`). Enables the structural gate. |
| `baseline-sidecar` | Committed **raw** baseline sidecar, with its frame PNGs committed alongside it. Enables the pixel-diff gate. See "Baseline frames" below. |
| `diff-threshold` / `diff-fail-above` | Passed straight to `clickcast diff --threshold` / `--fail-above`. |
| `clickcast-version` | Pinned clickcast version to `pip install`. |
| `engine` | Browser engine (`chromium` / `firefox` / `webkit`). |
| `install` | Set `false` to skip the Action's own install/cache steps when a prior step already put `clickcast` on PATH (this repo's own `clickcast-self-check.yml` uses this to dogfood the unreleased branch under test — see below). |
| `post-comment` | Post/update the PR comment. Default `true`. |
| `publish-reel-image` | Publish the reel to an orphan `clickcast-media` branch so it renders inline in the comment. Default `true`; see "How the reel gets embedded". |

Outputs: `sidecar-path`, `reel-path`, `assertions-passed`, `diff-summary-path`,
`diff-worst-pct`, `diff-passed` — for a downstream step that wants to
consume the result without re-parsing anything.

## Baseline frames — why `assertions` and `diff` need two different baseline files

`clickcast assertions --baseline` and `clickcast diff` are structural vs.
pixel-level gates with genuinely different inputs (see the main README's
["Its visual companion: `clickcast diff`"](../../README.md#its-visual-companion-clickcast-diff)
section):

- **`baseline`** (assertions) is the small, **distilled** JSON that
  `clickcast assertions <sidecar> > baseline.json` produces — step
  count/action/label/status/error-counters only, no pixels, no filenames.
- **`baseline-sidecar`** (diff) is a **raw** run sidecar — the actual
  `<name>.json` a `clickcast run`/`auto` invocation writes — whose frame
  PNGs must ALSO be committed, either in a `frames/` directory next to the
  sidecar JSON or wherever the sidecar's own `media.path` points (relative
  to the sidecar's directory). The JSON alone has no pixels to diff
  against.

Because a GIF/MP4/WebP encode consumes and discards clickcast's in-memory
frame buffer (only `--format frames` writes real frame files to disk — see
`clickcast run --format frames`), the Action makes a **second** capture of
the current run with `--format frames` whenever `baseline-sidecar` is set,
purely so it has real pixels to diff against. This means diff-enabled runs
pay for two browser passes, not one — a deliberate trade-off to keep the
primary `out` reel exactly what you asked for (a GIF, by default) while
still giving `clickcast diff` real frames, without changing `clickcast
run`/`feedback/visual_diff.py`'s existing contracts.

**Bootstrapping both baseline files**, against a target where the UI is in
its known-good state:

```bash
clickcast run scenario.yml --out golden.gif                          # -> golden.gif.json (distilled below)
clickcast assertions golden.gif.json > tests/golden-tour.json        # commit: assertions baseline
clickcast run scenario.yml --format frames --out tests/golden-frames # -> tests/golden-frames.json + tests/golden-frames/*.png
                                                                       # commit both: diff baseline (`baseline-sidecar: tests/golden-frames.json`)
```

This repo's own `tests/fixtures/ci-baseline/` (see "Dogfooding" below) is a
worked example of exactly this layout.

## How the reel gets embedded in the comment

GitHub renders `.gif` links in a comment body inline, but there is **no
public REST/GraphQL endpoint** that lets a workflow's `GITHUB_TOKEN` attach a
binary file to an issue/PR comment the way the web UI's drag-and-drop does.
So `publish-reel-image: true` (the default) pushes the reel to an orphan
`clickcast-media` branch in your repo and links it via a
`raw.githubusercontent.com` URL, which GitHub *does* render inline. This
only works for same-repo PRs — a fork PR's `GITHUB_TOKEN` is read-only, so
the push is skipped with a `::warning::`, not a failure, and the comment
falls back to linking the workflow's uploaded artifact instead. Set
`publish-reel-image: false` to always use the artifact link (e.g. if you'd
rather not have a `clickcast-media` branch in your repo at all).

## Versioning / Marketplace

This Action lives in-repo, under `.github/actions/clickcast/`, and is
consumed as a subdirectory action:
`uses: AlexKay28/clickcast/.github/actions/clickcast@<ref>`. **This PR does
not list it on GitHub Marketplace** — Marketplace requires the action
definition at the repo root, and the listing flow itself is a manual,
repository-owner-only step on github.com (repo settings -> "Draft a release"
-> "Publish this Action to the GitHub Marketplace") that no amount of CI
tooling can complete on the owner's behalf. Splitting into a dedicated
`clickcast-action` repo purely to satisfy the root-only requirement, before
the Action has had any real-world mileage, would mean maintaining two repos
in lockstep during the part of its life it's most likely to still be
changing shape. Recommendation (see #209): keep it here, tag releases
(`action-v1`, `action-v1.0.0`, ...) once this stabilizes so downstream
workflows can pin, and revisit splitting out + Marketplace listing — a
`git subtree split` preserves this directory's history if/when that
happens — once it's proven itself. That decision is the repo owner's to
make; this PR only sets up the substrate for it.

## Dogfooding

`.github/workflows/clickcast-self-check.yml` runs this Action against
`docs/scenarios/spa.yml` (driving `tests/fixtures/site/`, served locally so
the check has no external-network dependency) on every PR, gated against
the committed baseline in `tests/fixtures/ci-baseline/` — exercising both
the `assertions` and `diff` paths, not just a bare run. It installs
clickcast **from source** (`pip install -e .`, via `install: 'false'`)
rather than from PyPI, since this branch's `clickcast diff` command hasn't
shipped in a release yet. This is the regression guard for the Action
itself: a future `cli.py`/`feedback` change that breaks the pipeline the
Action wraps fails this workflow before it reaches a downstream user.
