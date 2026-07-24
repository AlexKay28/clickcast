# llm-doc-scraping

**When to use:** you want to give an LLM structured knowledge about a
documentation page or feature reference — without fighting client-side
JS rendering, dynamic routing, or the pain of parsing HTML.

## Command

```bash
clickcast auto https://docs.example.com/pricing \
  --max-pages 1 \
  --max-steps 10 \
  --dwell 0.4 \
  --initial-wait 3 \
  --out pricing.gif \
  --verbose
```

Or for a whole docs section:

```bash
clickcast auto https://docs.example.com/api/ \
  --max-pages 8 \
  --max-steps 40 \
  --traversal bfs \
  --dwell 0.3 \
  --out api-docs.gif
```

## Reel

![llm-doc-scraping reel](reel.gif)

## Why not scrape HTML directly?

Three reasons:

1. **Client-side rendering.** Modern docs sites are often SPAs. Raw
   `curl` returns a shell with `<div id="root"></div>`. Playwright
   actually renders the JS.
2. **Interactive elements matter.** Toggle expandable sections, hover
   for tooltips, click "Show more" — HTML alone misses these.
3. **Semantic structure via `discovered`.** clickcast's discovery
   collects role + accessible name for every interactive element. That
   gives the LLM a cleaner map of the page's affordances than raw DOM.

## Feed to the LLM

The sidecar's `discovered` array is the highest-signal input. Snippet:

```json
{
  "discovered": [
    {"role": "heading", "text": "API Reference"},
    {"role": "link", "text": "Authentication", "selector": "role=link[name=\"Authentication\"]"},
    {"role": "link", "text": "Rate limits", "selector": "role=link[name=\"Rate limits\"]"},
    {"role": "button", "text": "Copy code sample"}
  ]
}
```

Prompt shape:

```
Here is the interactive surface of a docs page (from clickcast's
discovery). Answer the user's question by citing the specific
`selector` and `text` values you'd use to navigate to the answer.
```

## Bonus: extract per-page discovered content

```bash
jq -r '.steps[] | select(.action == "goto") |
       "## \(.page_state.url_after)\n" +
       (.page_state.discovered // [] | map("- \(.role): \(.text)") | join("\n"))' \
   api-docs.gif.json
```

Produces markdown per page — a text-only tree of every interactive element
in the section, easy to feed to a small model or paste into a spec.
