# clickcast for AI agents

You (an AI agent) are a first-class user of clickcast. This page tells you (a) how to use it and (b) how to tell us when it breaks — the second part matters as much as the first. If your harness supports Agent Skills, the repository’s root `SKILL.md` is the installable manifest for this runbook. If you only fetch one file, fetch [`llms.txt`](llms.txt) — it indexes the install path, CLI, SDK, MCP tools, the perception loop and the schema in one page.

## Use it

```bash
clickcast auto https://example.com --out tour.gif --with-feedback
```

Produces `tour.gif` + `tour.gif.json`. The sidecar is the machine contract: schema at [`src/clickcast/feedback/schema/v5.json`](../src/clickcast/feedback/schema/v5.json). With `--with-feedback`, the sidecar also carries a `feedback` block with pointers back here.

For smooth video plus explicit visual landmarks, use
`clickcast auto https://example.com -o tour.mp4 --smooth --video-fps 30 --annotations regions.yml`.
This preserves reel duration, eases cursor motion, crossfades page transitions,
and records the actual output FPS in `media.fps`. See
[Smooth MP4 reels and region annotations](smooth-video.md) for the primitive
schema (`rect`, `circle`, `arrow`, `text`, `spotlight`).

## Live session

Everything above is batch: record a whole tour, then read back a GIF + sidecar. When you need to react to what you see before deciding the next action — instead of scripting the whole tour up front — reach for the live counterpart: [`clickcast mcp`](mcp.md), an MCP server exposing `goto`/`click`/`type`/`scroll`/... as individual tool calls, each returning the same annotated-frame + `page_state` + `error_code` shape the sidecar uses. Tool-by-tool contract: [`mcp-tool-schema.md`](mcp-tool-schema.md).

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
