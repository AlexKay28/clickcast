# site-cartography

**When to use:** an agent (or human) doesn't know a site's structure yet.
Run a shallow, broad tour to build a map before deciding what to probe next.

## Command

```bash
clickcast auto https://unknown-site.example.com/ \
  --max-pages 10 \
  --max-steps 30 \
  --traversal bfs \
  --dwell 0.2 \
  --out map.gif \
  --verbose
```

Note `--traversal bfs`: for cartography we want breadth over depth. Every
top-level nav destination gets visited before the tour follows a deeper link.

## Why not the default DFS?

DFS is great for a coherent viewing narrative but bad for coverage under a
tight budget — it'll drill into one section (say `/docs/`) and miss others
(`/pricing`, `/community`, `/blog`). BFS with a generous `--max-pages`
guarantees each top-level route gets a slide.

## What to do with the output

The reel is secondary — the **sidecar's `steps[].page_state.url_after`** is
the map. Extract it:

```bash
jq -r '.steps[] | select(.action == "goto") | .page_state.url_after' \
   map.gif.json | sort -u
```

Gives you the URL tree the tool actually reached. Compare against your
sitemap.xml or your own expectations. The `discovered` list per step also
gives you the interactive surface of each page — the raw material for
planning follow-on `--seed-url` tours that target specific pages.

## Feed into a follow-up tour

```bash
clickcast auto https://unknown-site.example.com/ \
  --seed-url https://unknown-site.example.com/pricing \
  --seed-url https://unknown-site.example.com/docs/api \
  --seed-url https://unknown-site.example.com/community/forum \
  --out targeted.gif
```

`--seed-url` disables auto-discovery of new nav destinations, so this tour
visits exactly the pages you named. Order is preserved (FIFO).
