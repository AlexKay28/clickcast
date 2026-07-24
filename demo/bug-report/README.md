# bug-report

**When to use:** a user hit a bug. You want to attach something to the
issue that's **reproducible** — devs can extract the exact steps from the
sidecar, not squint at a fuzzy screen recording.

## Two shapes

### Shape 1: The user already knows the flow — capture with a scenario

Write a scenario YAML that walks the exact steps that broke:

```bash
clickcast run demo/bug-report/reproduce-bug-42.yml \
  --var base_url=https://app.example.com \
  --out bug-42.gif
```

See [`reproduce-bug-42.yml`](reproduce-bug-42.yml) for a template.

### Shape 2: The user only knows the entry URL — let `auto` explore

```bash
clickcast auto https://app.example.com/that-broken-page \
  --max-pages 1 \
  --max-steps 10 \
  --out bug.gif \
  --verbose
```

The reel + sidecar capture whatever the tool encountered. Dev filters the
sidecar for the failing step.

## What to attach to the issue

- **The GIF** — human-readable evidence.
- **The sidecar** — machine-readable ground truth. Devs use these fields:

```json
{
  "steps": [
    {
      "step_index": 4,
      "action": "click",
      "selector": "role=button[name=\"Save\"]",
      "status": "failed",
      "error": "TimeoutError: Locator.click: Timeout 5000ms exceeded.",
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

- `steps[].status == "failed"` + `error` — the click that broke, with the
  exact selector and Playwright error.
- `page_state.console_errors` — JS runtime errors as the user saw them.
- `page_state.network_failed` — 4xx/5xx responses.

A dev now has enough to reproduce locally without asking the reporter for
"what were you doing exactly?"

## Why not just a screen recording?

Screen recordings show WHAT the user saw. The sidecar shows WHAT THE
BROWSER DID — DOM selector paths, exact error text, precise timings. Dev
time drops dramatically when there's no guessing involved.

## Devs' next step

```bash
# Re-run the exact same scenario locally against staging:
clickcast run reproduce-bug-42.yml --var base_url=https://staging.example.com

# Or extract just the failing selector for a quick focused test:
jq '.steps[] | select(.status == "failed") | .selector' bug-42.gif.json
```
