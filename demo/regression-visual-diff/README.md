# regression-visual-diff

**When to use:** you have a critical user flow that must not break. Run
the same scenario nightly; diff the sidecar between runs to catch
regressions before users do.

## Scenario

See [`login-flow.yml`](login-flow.yml) — a canonical example. Point it at
your own app by overriding the URL:

```bash
clickcast run demo/regression-visual-diff/login-flow.yml \
  --url https://app.example.com \
  --var test_email=qa@example.com \
  --var test_password=$QA_PASSWORD \
  --out nightly.gif
```

The `--url` flag (added in v0.2.0) rewrites the first `goto` step so you
can retarget scenarios per environment without editing the YAML.

## The 2-line CI gate: `clickcast assertions`

Since v0.2.0, `clickcast assertions` (backed by `Reel.assertions()` +
`Reel.assertions_diff()`) is the built-in regression-diff primitive.
It emits a **stable subset** of the sidecar — no timestamps, no wall-clock
durations, no frame filenames, no URL query strings — so two clean runs
produce byte-identical output. Any diff is a real regression signal.

Commit a **baseline** once:

```bash
clickcast run demo/regression-visual-diff/login-flow.yml \
  --url https://app.example.com --out baseline.gif
clickcast assertions baseline.gif.json --json > tests/baselines/login-flow.json
```

Then gate every subsequent run against it:

```bash
clickcast assertions nightly.gif.json --baseline tests/baselines/login-flow.json
# exit 0  → clean; exit 1 → drift detected, with a human-readable list of
#   differences printed to stdout (e.g. "step 2: status changed ok → failed",
#   "step 3: console_error_count 0 → 2").
```

That's the whole gate. No hand-rolled `jq` needed.

## What's in the "assertions" shape

Deliberately narrow — only the fields whose changes indicate real drift:

- `step_count`
- per step: `action`, `label`, `status`, `console_error_count`,
  `page_error_count`, `network_failed_count`

Timestamps, wall-clock durations, `duration_ms`, frame filenames, cursor
coords, and resolved URLs are excluded so re-runs produce byte-identical
output. Full contract at
[`docs/assertions-schema/v1.json`](../../docs/assertions-schema/v1.json).

## Reducing noise on cold-serverless targets

Cold Vercel / Cloudflare previews can time out an initial `goto` even
when the app is fine — real signal for first-user latency, false positive
for a regression gate. Two v0.2.0 additions help:

- `goto(retries=2)` in the scenario (or `--retries` on `run`) — retries
  transient `TimeoutError`s with exponential backoff (500ms, 1s, 2s...).
- `wait_for(selector, state='stable', quiet_ms=200)` — replaces the
  `dwell: 3.0`/`dwell: 5.0` guessing that used to be sprinkled through
  every UI-verification scenario. Waits until the element's bounding box
  hasn't moved for `quiet_ms`.

## Programmatic use (Python)

Same primitive, no CLI:

```python
from clickcast import Reel

reel = Reel("https://app.example.com").goto().click(".login").save("nightly.gif")

drift, is_clean = reel.assertions_diff("tests/baselines/login-flow.json")
if not is_clean:
    for line in drift:
        print(line)
    raise SystemExit(1)
```

## CI integration (GitHub Actions)

Cron-triggered — copy into `.github/workflows/nightly-regression.yml`:

```yaml
name: Nightly regression tour

on:
  schedule:
    - cron: '0 3 * * *'   # 03:00 UTC daily
  workflow_dispatch: {}

jobs:
  tour:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install clickcast
      - run: playwright install --with-deps chromium
      - name: Run tour
        env:
          QA_PASSWORD: ${{ secrets.QA_PASSWORD }}
        run: |
          clickcast run demo/regression-visual-diff/login-flow.yml \
            --url https://app.example.com \
            --var test_password="$QA_PASSWORD" \
            --out nightly.gif
      - name: Gate against committed baseline
        run: clickcast assertions nightly.gif.json --baseline tests/baselines/login-flow.json
      - uses: actions/upload-artifact@v4
        if: always()      # keep the reel even on failure — you'll want it for triage
        with:
          name: nightly-reel
          path: |
            nightly.gif
            nightly.gif.json
          retention-days: 30
```

The baseline lives in the repo (`tests/baselines/login-flow.json`). When
you intentionally change the flow, regenerate the baseline and commit it
alongside the code change — same review discipline as any other test
snapshot.

## Why not just visual pixel-diff?

Because that flags too much noise: font rendering differences, ad content,
timestamps, cursor position. The sidecar diff is **semantic** — it flags
"the Sign in button no longer navigates to /dashboard" or "step 4 now has
a `TypeError` in `console_errors`". Actionable, not just noisy.

## Escalating a drift into a bug report

Once `clickcast assertions` flips red, pipe the same sidecar through
`clickcast report-bug` to file a well-formed issue:

```bash
clickcast report-bug nightly.gif.json --note "nightly regression on 2026-07-27"
```

See [`../bug-report/`](../bug-report/) for the full report-bug workflow.
