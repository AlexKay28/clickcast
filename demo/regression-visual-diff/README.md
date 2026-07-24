# regression-visual-diff

**When to use:** you have a critical user flow that must not break.
Run the same scenario nightly; diff the sidecar between runs to catch
regressions before users do.

## Scenario

See [`login-flow.yml`](login-flow.yml) — a canonical example. Point it at
your own app by overriding `base_url`:

```bash
clickcast run demo/regression-visual-diff/login-flow.yml \
  --var base_url=https://app.example.com \
  --var test_email=qa@example.com \
  --var test_password=$QA_PASSWORD \
  --out nightly.gif
```

## What to diff

The sidecar has two fields that matter most for regression detection:

1. **`steps[].status`** — was every step OK?
2. **`steps[].page_state.console_errors`** and **`network_failed`** — did
   the browser log anything new?

Simple bash diff between two runs:

```bash
# Extract just the fields that matter for regression:
jq '.steps[] | {step: .step_index, status, errors: .page_state.console_errors}' \
   yesterday.gif.json > /tmp/y.txt
jq '.steps[] | {step: .step_index, status, errors: .page_state.console_errors}' \
   today.gif.json > /tmp/t.txt
diff /tmp/y.txt /tmp/t.txt
```

Any non-empty diff is a regression candidate.

## CI integration

Cron-triggered GitHub Actions workflow — copy this into
`.github/workflows/nightly-regression.yml`:

```yaml
name: Nightly regression tour

on:
  schedule:
    - cron: '0 3 * * *'  # 03:00 UTC daily
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
            --var base_url=https://app.example.com \
            --var test_password="$QA_PASSWORD" \
            --out nightly.gif
      - name: Compare against yesterday's baseline
        run: |
          # Fetch yesterday's sidecar from an artifact cache, S3, etc.
          # If any step's status flipped from ok → failed, fail the workflow.
          jq -e '.steps | all(.status == "ok")' nightly.gif.json
      - uses: actions/upload-artifact@v4
        with:
          name: nightly-reel
          path: |
            nightly.gif
            nightly.gif.json
          retention-days: 30
```

Store yesterday's `nightly.gif.json` somewhere the workflow can fetch —
artifact from the previous run, S3 bucket, or a dedicated regression-history
branch. The diff logic is up to you; the sidecar's JSON structure is the
stable interface.

## Why not just visual pixel-diff?

Because that flags too much noise: font rendering differences, ad content,
timestamps. The sidecar diff is **semantic** — it flags "the Sign in button
no longer navigates to /dashboard" or "step 4 now has a `TypeError` in
console_errors". Actionable, not just noisy.
