# clickcast demo/ folder

Concrete, runnable use cases for `clickcast`. Each subfolder answers a single
question: **when would I reach for this shape?**

Each entry has a README with the specific command + a snippet of the expected
sidecar output. Committed sample reels show what the operation looks like on
a real public site.

| Use case | Reel | When to use |
|----------|------|-------------|
| [`ai-eye-review/`](ai-eye-review/) | [`reel.gif`](ai-eye-review/reel.gif) | Feed a reel + sidecar to an LLM so it can review a live UI (bugs, UX, dead links). |
| [`site-cartography/`](site-cartography/) | [`reel.gif`](site-cartography/reel.gif) | An agent maps an unknown site's structure before deciding what to test. |
| [`regression-visual-diff/`](regression-visual-diff/) | [`reel.gif`](regression-visual-diff/reel.gif) | Nightly reel of a critical flow; diff sidecars between runs to spot regressions. |
| [`bug-report/`](bug-report/) | [`reel.gif`](bug-report/reel.gif) | Reproducible failure capture — attach reel + sidecar to an issue, devs get exact steps. |
| [`onboarding-tutorial/`](onboarding-tutorial/) | [`reel.gif`](onboarding-tutorial/reel.gif) | Reel + auto-generated prose walkthrough from the same recording. |
| [`a-b-comparison/`](a-b-comparison/) | [`reel-control.gif`](a-b-comparison/reel-control.gif) / [`reel-treatment.gif`](a-b-comparison/reel-treatment.gif) | Run the same scenario on two variants; diff timings + errors. |
| [`llm-doc-scraping/`](llm-doc-scraping/) | [`reel.gif`](llm-doc-scraping/reel.gif) | Give an LLM structured knowledge about a docs page — no HTML scraping. |
| [`accessibility-preflight/`](accessibility-preflight/) | [`reel.gif`](accessibility-preflight/reel.gif) | 60-second smoke test for missing ARIA / accessible names. |
| [`spatial-grid-overlay/`](spatial-grid-overlay/) | [`reel.gif`](spatial-grid-overlay/reel.gif) | Click by pixel coordinate instead of guessing — `--grid` burns a coordinate system into every frame. |
| [`pixel-visual-diff/`](pixel-visual-diff/) | [`diff.png`](pixel-visual-diff/diff.png) | Catch visual regressions structural diff can't see — `clickcast diff`, region-highlighted. |
| [`accessible-element-targeting/`](accessible-element-targeting/) | [`shot.png`](accessible-element-targeting/shot.png) | Role/name/state *and* exact pixel coordinates for an element in one `elements --json --grid` call. |
| [`live-mcp-session/`](live-mcp-session/) | [`reel.gif`](live-mcp-session/reel.gif) | An agent drives the browser live, one MCP tool call at a time — not a pre-recorded batch tour. |

## Regenerating reels

Each subfolder's README has the exact command. Reels are captured against
public sites (react.dev, tailwindcss.com, docs.python.org) so anyone can
reproduce them. Small on purpose — short dwells, few clicks, tight viewport.

To regenerate a specific reel:

```bash
# Follow the command in the target subfolder's README, e.g.:
clickcast auto https://react.dev/ \
  --max-pages 3 --max-steps 10 --dwell 0.3 \
  --out demo/ai-eye-review/reel.gif
```
