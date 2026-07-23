# Integrating clickcast into an AI agent

Clickcast is a Python CLI + library that drives a real browser through a
website and hands back two things every LLM agent needs:

1. A **watchable reel** (`.gif` / `.mp4` / `.webp`) showing what the
   browser did.
2. A **machine-readable sidecar** (`<out>.json`) with every step's
   selector, timings, per-step frame paths, discovered elements, and
   post-action page state.

Your agent doesn't need to load Playwright, watch pixels, or scrape the
DOM. It calls one function, receives one JSON, and reasons over it.

## The two-line integration

```python
from clickcast import Reel
from clickcast.feedback import load

# Drive the browser
reel_path = (
    Reel("https://example.com", viewport=(1280, 800))
    .goto(wait="networkidle")
    .click("text=Get started", dwell=1.5)
    .save("tour.gif")
)

# Read the report
report = load(str(reel_path) + ".json")

failed = [s for s in report.steps if s.status == "failed"]
for step in failed:
    print(f"step {step.index} ({step.action}) failed: {step.error}")
    print("  see frames:", step.frames)
    if step.page_state:
        print("  console errors:", step.page_state.console_errors)
```

That's the whole loop. `report` is a typed `Report` (pydantic v2) with
everything from the schema in [feedback-schema.md](feedback-schema.md).

## Reading the sidecar without the package

The sidecar is plain JSON validated against
`src/clickcast/feedback/schema/v1.json`. See
[`tests/consumer/read_sidecar.py`](../tests/consumer/read_sidecar.py) for
a standalone consumer that doesn't import `clickcast`.
