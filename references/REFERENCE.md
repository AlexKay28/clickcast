# clickcast — full AI agent runbook

This file extends the root `SKILL.md` with the deeper contract, workflow, and troubleshooting detail that makes clickcast work reliably as an installed agent skill. Read it after the manifest, when you need the exact shapes, gates, and failure recovery paths.

---

## Read the sidecar like a contract

Every reel-producing command emits `<out>.json`.

Important fields:

| Field | Meaning |
|---|---|
| `schema_version` | Stable schema version marker, currently 4 |
| `clickcast_version`, `engine`, `viewport`, `url`, `started_at`, `duration_s` | Run metadata |
| `steps[]` | One entry per executed step |
| `steps[].status` | `ok`, `failed`, or `skipped` |
| `steps[].action` | `goto`, `click`, `type`, `hover`, `scroll`, … |
| `steps[].selector` | Selector attempted |
| `steps[].label` | Human-readable label used in the overlay |
| `steps[].cursor_xy` | Click position, useful for spatial reasoning |
| `steps[].frame_path` | Path to the PNG for that step |
| `steps[].page_state` | Post-action title, URL, visible text, console errors, page errors, network failures |
| `steps[].error_code` | Stable enum: `timeout`, `locator_missing`, `cross_origin`, `navigation_error`, `selector_ambiguous`, `other` |
| `steps[].skip_reason` | If skipped: `optional_no_reaction`, `pre_action_failed`, `element_vanished`, `cross_origin_bounce` |
| `graph` | Nodes and edges describing pages visited and navigation between them |
| `media` | Path, format, size, frame count, duration, FPS |
| `feedback` (opt-in) | Docs / bug-report pointers, plus a prefilled GitHub issue URL |
| `redacted` | Whether redaction was applied to this sidecar |
| `annotate` | Grid and annotation configuration, if applied |

Read it programmatically:

```python
from clickcast.feedback import load

report = load("tour.gif.json")
for step in report.steps:
    if step.status == "failed":
        print(step.index, step.action, step.error_code, step.error)
```

Prefer `error_code` over parsing `error` strings — it is a stable enum.

---

## Build a CI regression gate with `assertions`

clickcast can turn a sidecar into a deterministic assertion set:

```bash
clickcast auto https://prod.example.com --max-pages 3 --out tour.gif
clickcast assertions tour.gif.json > baseline.json
```

Then in CI:

```bash
clickcast auto https://prod.example.com --max-pages 3 --out tour.gif
clickcast assertions tour.gif.json --baseline baseline.json
```

If the run drifts from the committed baseline, the command exits nonzero and avoids noisy timing or path differences.

Use this when you want to gate a PR on UI behavior, not just on logs.

---

## Handle internal / SSO-protected hosts

Use static configuration instead of interactive login:

```bash
export CLICKCAST_INSECURE=1
export CLICKCAST_HEADER_HOST=internal.example.com
export CLICKCAST_HEADER='Authorization: Bearer '"$INTERNAL_TOKEN"

clickcast auto https://internal.example.com/admin --out admin.gif
```

You can also write them in the scenario:

```yaml
meta:
  insecure: true
  header_host: internal.example.com
  extra_headers:
    Authorization: Bearer YOUR_TOKEN
```

`--header-host` scopes header delivery to the exact host or dotted suffix. This prevents leaking credentials to third-party origins loaded on the page.

---

## Turn failures into useful reports

If a tour fails, do three things:

1. Inspect the failing step in the sidecar, especially `error_code`.
2. Run `clickcast report-bug <sidecar>.json --json`.
3. Share the resulting GitHub issue URL or paste the JSON payload where asked.

```bash
clickcast report-bug tour.gif.json --json
```

`report-bug` automatically:

- summarizes the environment,
- extracts the failing step,
- preserves structure and diagnostics,
- redacts sensitive strings by default,
- and emits a prefilled GitHub issue URL.

Do not paste raw full sidecars into chat unless the user explicitly asks. Prefer redacted, structured summaries.

---

## Troubleshooting quick map

| Problem | Likely cause | Fix |
|---|---|---|
| `clickcast skill --json` fails | clickcast not installed or older version | reinstall/upgrade and ensure Python ≥3.10 |
| Doctor fails on engine | browser engine missing | `clickcast install --with-deps chromium` |
| Page is blank for 2-3 seconds | hydration or slow load | use `--initial-wait`, `--title-card`, or `wait: networkidle` |
| A step is `skipped` | optional element vanished or cross-origin bounce | read `skip_reason`; decide if intentional |
| `locator_missing` | selector stale or dynamic DOM changed | run `clickcast elements <url> --json`, update selector |
| `timeout` | slow page or JS-heavy load | use `--pace slow`, longer `--click-timeout`, or `wait: networkidle` |
| `navigation_error` | TLS, auth, or offline | use `--insecure`, `--header`, or verify reachability |
| Reel too large | high FPS/full quality | use MP4, lower quality number, or `--format webp` |
| White blank full-page shot | page did not paint before capture | use `--wait networkidle` or a longer explicit `wait` |
| Smooth video feels too fast/slow | wrong `--fps` or `--video-fps` | set `--fps` for capture and `--video-fps 30` for output |
| Annotations overlap content | too many primitives on one range | decrease simultaneous annotations, shorten `end_step` |
| MCP won’t start | missing `mcp` optional dependency | install with `pip install 'clickcast[mcp]'` |
| Browser prompt blocks CI | non-interactive environment | preinstall `clickcast install`; clickcast then fails fast instead of hanging |

---

## Recommended AI-agent workflow

1. **Verify the environment.**
   ```bash
   clickcast skill --json
   clickcast doctor --json
   ```

2. **Ask what outcome the user needs.**
   - Human demo? Produce a GIF or smooth MP4.
   - Machine verdict? Produce a sidecar.
   - Both? Use `auto` or `run`, then `assertions`.

3. **If the flow is known, author a YAML scenario.**
   - Use `clickcast elements` to find selectors.
   - Use `wait: networkidle` after navigation on SPAs.
   - Add `label:` to every important step.
   - Add `assert:` to make expected outcomes explicit.

4. **If the flow is not known, use `auto` first.**
   - Keep `--max-steps` and `--max-duration` bounded.
   - Inspect the sidecar to identify the actual flow.
   - Convert the result into a YAML scenario for repeatability.

5. **If the user wants a smooth, human-shareable video, use `--smooth --video-fps 30`.**
   - Keep `--fps 12` for capture.
   - Use MP4 for smallest large-quality output.
   - Add `--annotations regions.yml` only when the annotations improve comprehension.

6. **Always report both artifacts.**
   - To the user: “I recorded this tour as `video` and the sidecar is `video` + `.json`.”
   - To a machine: validate the sidecar with the schema.
   - Attach only redacted excerpts by default.

7. **If anything fails, run `report-bug`.**
   - Use the generated JSON and issue URL.
   - Never invent a bug report format.

---

## Do not do these

Do not:

- scrape sites at scale with clickcast;
- use clickcast as a proxying or packet-capture tool;
- attempt interactive SSO or MFA login;
- use a blank GIF as evidence that something works;
- run `auto` with unbounded `--max-steps` or `--max-duration`;
- parse human-readable error prose when `error_code` exists;
- embed bare secrets in a sidecar, raw YAML, or a public issue report;
- rely on frames alone when the sidecar is the actual contract;
- pass unrelated auth headers to third-party origins.

---

## Further reading in this repository

- Root README overview: `README.md`
- Full long-form guide: `skill.md`
- Human docs for agents: `docs/for-agents.md`
- Smooth MP4 and annotation guide: `docs/smooth-video.md`
- MCP server guide: `docs/mcp.md`
- Schema v4: `src/clickcast/feedback/schema/v4.json`
- Explore schema: `docs/explore-schema.md`
- Assertions schema: `docs/assertions-schema/v1.json`
- Scenario examples: `docs/scenarios/`
- Demo reels and sidecars: `demo/`
- Package metadata: `pyproject.toml`
- MCP registry: `server.json`

When in doubt, run the corresponding subcommand with `--help`; clickcast’s help is already agent-oriented.
