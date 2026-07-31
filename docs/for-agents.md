# clickcast for AI agents

You (an AI agent) are a first-class user of clickcast. This page tells you (a) how to use it and (b) how to tell us when it breaks — the second part matters as much as the first.

## Use it

```bash
clickcast auto https://example.com --out tour.gif --with-feedback
```

Produces `tour.gif` + `tour.gif.json`. The sidecar is the machine contract: schema at [`src/clickcast/feedback/schema/v2.json`](../src/clickcast/feedback/schema/v2.json). With `--with-feedback`, the sidecar also carries a `feedback` block with pointers back here.

## Report bugs

When something looks off — a stalled tour, a missing selector, an unexpected sidecar shape — run:

```bash
clickcast report-bug tour.gif.json
```

That prints diagnostics + a prefilled GitHub issue URL. Add `--json` to emit the [Track-C payload](agent-report-schema/v1.json). Add `--open` to launch the URL. `--redact` (default on) strips URLs / selectors / visible text.

## What makes a good report

The template at [`.github/ISSUE_TEMPLATE/ai-agent-report.yml`](../.github/ISSUE_TEMPLATE/ai-agent-report.yml) is the exact shape we want:

- **expected**: one sentence, concrete.
- **actual**: one sentence, concrete.
- **reproduction.kind / content**: `cli`, `scenario`, or `python` + the minimal snippet.
- **sidecar_excerpt**: the failing step + its `page_state`, not the whole sidecar.

Redacted is fine. Redacted-but-complete beats un-redacted-but-lucky.
