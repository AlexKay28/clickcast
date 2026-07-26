# bug-report

**When to use:** a user hit a bug. You want to attach something to the
issue that's **reproducible** — devs can extract the exact steps from the
sidecar, not squint at a fuzzy screen recording.

## Three shapes, in order of preference

### Shape 1: Turn any existing sidecar into a filed issue in one command

`clickcast report-bug` (added in v0.2.0) is the fastest path. Point it at
any sidecar you already have; it prints diagnostics plus a **prefilled
GitHub-issue URL** with the environment, failing step, and sidecar
excerpt already baked in.

```bash
clickcast report-bug bug.gif.json
# prints:
#   clickcast 0.2.0 · python 3.12.3 · Linux
#   playwright 1.44.0
#   command: clickcast auto https://app.example.com/... --out <path>
#   expected: Step 4 (click) succeeds.
#   actual:   Step 4 (click) — TimeoutError: Locator.click: Timeout 5000ms exceeded.
#   redacted: True
#
#   Open this URL to file (title + body prefilled):
#   https://github.com/AlexKay28/clickcast/issues/new?template=...&title=...&body=...
```

Flags: `--json` (emit the machine-fillable
[Track-C payload](../../docs/agent-report-schema/v1.json) instead of prose),
`--open` (launch the URL in a browser), `--redact/--no-redact` (default on
— sanitizes URLs, selectors, and visible text while preserving structure),
`--note "TLS interception on"`.

If the target site is behind a preview auth token, sidecars from `auto` or
`run` will bake the token into recorded URLs. Add
`--redact-pattern "x-vercel-protection-bypass=[^&]+"` when generating the
sidecar, OR `--strip-query-strings` for a coarse-but-safe default.

### Shape 2: The user knows the flow — capture with a scenario, then report-bug

Write a scenario YAML that walks the exact steps that broke:

```bash
clickcast run demo/bug-report/reproduce-bug-42.yml \
  --url https://app.example.com \
  --out bug-42.gif

clickcast report-bug bug-42.gif.json
```

See [`reproduce-bug-42.yml`](reproduce-bug-42.yml) for a template. The
new `--url` flag on `run` overrides the scenario's entry URL — handy
when swapping between prod / staging / a PR preview.

### Shape 3: The user only knows the entry URL — let `auto` explore, then report-bug

```bash
clickcast auto https://app.example.com/that-broken-page \
  --max-pages 1 --max-steps 10 --with-feedback \
  --redact-pattern 'x-vercel-protection-bypass=[^&]+' \
  --out bug.gif

clickcast report-bug bug.gif.json
```

The reel + sidecar capture whatever the tool encountered. `report-bug`
narrates the first failed step; `--with-feedback` also attaches the
[feedback pointer block](../../docs/for-agents.md) to the sidecar so
downstream agent consumers know where to file their own reports.

## What ends up in the filed issue

The prefilled body carries:

- **Environment**: clickcast version, playwright version, Python, OS.
- **Command or API call** that produced the sidecar.
- **Expected / Actual** narrated from the failed step.
- **Reproduction**: kind + content.
- **Sidecar excerpt** (redacted by default): the failed step + its
  `page_state` (console errors, network failures, page title), tour-level
  warnings/errors.

The template at
[`.github/ISSUE_TEMPLATE/ai-agent-report.yml`](../../.github/ISSUE_TEMPLATE/ai-agent-report.yml)
maps 1:1 to the payload — human maintainers get a structured report, no
"what were you doing exactly?" back-and-forth.

## Sidecar fields devs read first

Even without `report-bug`, these are the fields that carry ground truth:

```json
{
  "steps": [
    {
      "step_index": 4,
      "action": "click",
      "selector": "role=button[name=\"Save\"]",
      "status": "failed",
      "error": "TimeoutError: Locator.click: Timeout 5000ms exceeded.\n\nCandidates that might be what you meant (top 5 by similarity):\n  role=button[name=\"Save changes\"]  bbox=[...]  score=0.87\n  ...",
      "duration_ms": 5017.0,
      "page_state": {
        "console_errors": [
          "TypeError: Cannot read properties of null (reading 'user')"
        ],
        "network_failed": [
          "https://api.example.com/save (500)"
        ]
      }
    }
  ]
}
```

Notes:

- `steps[].status == "failed"` + `error` — the click that broke.
- `page_state.console_errors` — JS runtime errors as the user saw them.
- `page_state.network_failed` — 4xx/5xx responses.
- The `error` field also carries a top-5
  [candidate-selector hint block](../../src/clickcast/discovery/hints.py)
  when the selector resolved to zero elements — pastes cleanly into the
  filed issue so devs can see what actually was on the page.

## Devs' next step

```bash
# Re-run the exact same scenario locally against staging:
clickcast run reproduce-bug-42.yml --url https://staging.example.com

# Or extract just the failing selector for a quick focused test:
jq '.steps[] | select(.status == "failed") | .selector' bug-42.gif.json
```

## Why not just a screen recording?

Screen recordings show WHAT the user saw. The sidecar shows WHAT THE
BROWSER DID — DOM selector paths, exact error text, precise timings,
console errors, network failures. `report-bug` turns all of that into a
filed issue in one command. Dev time drops dramatically when there's no
guessing involved.
