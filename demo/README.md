# clickcast demo/ folder

Concrete, runnable use cases for `clickcast`. Each subfolder answers a single
question: **when would I reach for this shape?**

| Subfolder | When to use |
|-----------|-------------|
| [`ai-eye-review/`](ai-eye-review/) | Feed a reel + sidecar to an LLM so it can review a live UI (bugs, UX, dead links). |
| [`site-cartography/`](site-cartography/) | An agent maps an unknown site's structure before deciding what to test. |
| [`regression-visual-diff/`](regression-visual-diff/) | Nightly reel of a critical flow; diff sidecars between runs to spot regressions. |
| [`bug-report/`](bug-report/) | Reproducible failure capture — attach reel + sidecar to an issue, devs get exact steps. |

Each subfolder has a `README.md` with the specific command + a snippet of the
expected sidecar output. No committed GIFs (they'd bloat the repo); run the
commands to see your own.

## Not here yet

Issue [#66](https://github.com/AlexKay28/clickcast/issues/66) lists four more
use cases planned for a follow-up: `onboarding-tutorial/`, `a-b-comparison/`,
`llm-doc-scraping/`, `accessibility-preflight/`. If one of those is what you
came for, open a comment on the issue with what you'd like to see.
