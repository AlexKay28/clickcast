# onboarding-tutorial

**When to use:** you want a reel + sidecar of your product's first-time-user
flow, and you want the prose walkthrough to stay in sync with the reel
without hand-writing it.

## Command

Explicit path via `--seed-url` so the tour matches your onboarding script:

```bash
clickcast auto https://your-app.example.com/welcome \
  --seed-url https://your-app.example.com/tour/step-1 \
  --seed-url https://your-app.example.com/tour/step-2 \
  --seed-url https://your-app.example.com/tour/step-3 \
  --seed-url https://your-app.example.com/dashboard \
  --dwell 0.6 \
  --initial-wait 2 \
  --out onboarding.gif \
  --verbose
```

`--seed-url` is critical here: it guarantees the tour follows your intended
narrative rather than wandering through auto-discovered nav.

## Reel

![onboarding-tutorial reel](reel.gif)

## Auto-generate prose from the sidecar

The sidecar has one entry per step with a `label` field — that's your
tutorial's caption list. Extract it:

```bash
jq -r '.steps[] | "\(.step_index + 1). **\(.action | ascii_upcase)** — \(.selector // .url // "-")"' \
   onboarding.gif.json
```

Produces markdown like:

```
1. **GOTO** — https://your-app.example.com/welcome
2. **GOTO** — https://your-app.example.com/tour/step-1
3. **CLICK** — role=button[name="Next"]
4. **GOTO** — https://your-app.example.com/tour/step-2
...
```

Paste into your docs. Every time the flow changes, re-run the tour → the
list regenerates. No drift between the reel and the text.

## Why not a hand-authored scenario YAML?

You can. `clickcast run tutorial.yml` gives you finer control over dwell,
labels, and click ordering. Use it when the flow has non-obvious steps
(dismiss a modal, wait for a specific selector). Use `auto --seed-url`
when the flow is "open these pages, click whatever's on top" — cheaper
to maintain.
