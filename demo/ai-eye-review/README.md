# ai-eye-review

**When to use:** you want an LLM to review a live UI — spot bugs, UX issues,
dead links, missing feedback. Feed it the reel + sidecar; the reel gives
visual context, the sidecar gives structured facts.

## Command

```bash
clickcast auto https://your-site.example.com/ \
  --max-pages 5 \
  --max-steps 25 \
  --dwell 0.4 \
  --initial-wait 3 \
  --traversal dfs \
  --out review.gif \
  --verbose
```

Produces:
- `review.gif` — annotated reel showing the tour (click ripples, actions
  panel, progress bar).
- `review.gif.json` — the sidecar (schema at
  [`docs/feedback-schema.md`](../../docs/feedback-schema.md)).

## Feed to an LLM

Attach both files to the model. Prompt shape:

```
Here's a visual tour (reel) and structured tour log (sidecar) of a live
web app. Please review it as a QA engineer would.

Focus on:
- UX friction: dead-end clicks, unclear affordances, missing feedback.
- Bugs: console_errors or network_failed entries in any step.
- Missing accessibility: interactive elements with empty accessible names.
- Coverage gaps: what important UI wasn't reached that should have been.

For each issue, cite the specific step_index and quote the sidecar field
that supports the finding.
```

## What the sidecar carries

The parts that matter for AI-eye review, from `review.gif.json`:

```json
{
  "schema_version": 1,
  "url": "https://your-site.example.com/",
  "media": {
    "path": "review.gif",
    "frame_count": 264,
    "duration_s": 22.0
  },
  "discovered": [
    {"selector": "role=link[name=\"Docs\"]", "text": "Docs", "role": "link"},
    {"selector": "role=button[name=\"Sign in\"]", "text": "Sign in", "role": "button"}
  ],
  "steps": [
    {
      "step_index": 3,
      "action": "click",
      "selector": "role=link[name=\"Docs\"]",
      "status": "ok",
      "duration_ms": 143.0,
      "page_state": {
        "title": "Docs — example",
        "url_after": "https://your-site.example.com/docs",
        "console_errors": [],
        "network_failed": []
      }
    }
  ]
}
```

`page_state.console_errors` and `network_failed` are the highest-signal
fields for finding real bugs. `discovered[*].text == ""` flags accessibility
issues.

## Why DFS

DFS gives a coherent narrative — the reel reads like a user journey (Home →
Docs → Docs/Getting-Started → …) rather than jumping between unrelated
top-level pages. Related pages sit adjacent in the sidecar's `steps` array,
which makes the model's job easier: "what happened in the Docs section?"
becomes a contiguous slice.
