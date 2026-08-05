# clickcast — AI-Agent Skill Guide

> **You are an AI agent.** This document is written for you. It covers every command clickcast ships, the workflows that pull them together, and the failure modes you should learn to recognize. Read this once and you will be able to drive a browser, produce evidence a human can watch, and gate CI on a machine-readable summary.
>
> **Shorter alternative**: `clickcast skill` prints a ~900-word brief embedded in the binary. This file is the long form — read it when the brief left you guessing.

---

## What clickcast is

clickcast drives a real Chromium/Firefox/WebKit browser (via Playwright) through a website and hands back two things:

1. A **watchable reel** (`.gif` / `.mp4` / `.webp` / raw frames) showing what the browser did — for humans and for your own visual debugging.
2. A **machine-readable sidecar** (`<out>.json`) with every step's selector, timings, per-step frame paths, discovered interactive elements, and post-action page state — for you.

Concretely, the sidecar is the answer. The reel is the receipt. If you never watch a single reel, you can still use clickcast productively — you consume the sidecar.

## When to reach for clickcast

Use clickcast when you need to:

- **Prove a UI works.** Post-deploy smoke tests, PR previews, feature-flag rollouts.
- **Report on what a page contains.** Extract the interactive-element list (buttons, links, form fields) without touching the DOM yourself.
- **Turn a natural-language spec into a deterministic run.** Write a YAML scenario, run it, gate CI on the resulting sidecar.
- **Show a human what happened.** Reels are far more legible than logs when a user says "my thing broke."
- **See a page you otherwise can't.** Internal / SSO-protected hosts, self-signed certs, staging deployments — clickcast has flags for these.

Do NOT reach for clickcast when you need to:

- Scrape data at scale — use a scraping library. clickcast records; it doesn't extract.
- Drive a browser in a persistent-session flow — clickcast is one-shot per invocation.
- Automate authentication flows involving TOTP / phishing-resistant WebAuthn — use `--header` for bearer tokens; interactive login is out of scope.

---

## The three main commands, ranked by how often you'll use them

### 1. `auto` — the one-liner tour

`auto` discovers interactive elements on the target page (buttons, links, form fields, tabs) and clicks through them in DFS or BFS order until it hits a budget cap. It's the fastest way to produce evidence that a page loads and its main flows work.

```bash
clickcast auto https://example.com --out tour.gif --with-feedback
```

Produces `tour.gif` + `tour.gif.json`. The sidecar has every clicked element, its selector, the URL you landed on after each click, and post-action page state.

**Key flags** (see `clickcast auto --help` for the full set):

| flag | why you want it |
|---|---|
| `--max-steps 15` | click budget across the whole tour (default 15) |
| `--max-pages 5` | cap on how many distinct pages the tour visits (default 5) |
| `--max-duration 120` | wall-clock budget in seconds — hard stop |
| `--traversal dfs\|bfs` | dfs = coherent narrative, bfs = site-map coverage |
| `--seed-url` (repeatable) | force a specific URL order instead of auto-discovery |
| `--pace fast\|natural\|slow\|onboarding` | speed preset — sets fps + dwell together |
| `--for-humans` | composite: flips pace/zoom/highlight/cards to human-friendly defaults |
| `--emit-events` | print a JSON `tour_complete` line to stdout for JSONL parsers |
| `--with-feedback` | attach a feedback pointer block to the sidecar (issue-report URLs, docs) |
| `--insecure` / `--header` / `--header-host` | internal-host support — see §"Internal / SSO-protected sites" below |

**Idiomatic patterns:**

```bash
# Give a PM a shareable GIF of the login → dashboard flow.
clickcast auto https://staging.example.com --for-humans --out demo.gif

# CI smoke test — cap at 30s, emit a JSON event so the pipeline can grep for it.
clickcast auto https://prod.example.com --max-duration 30 --emit-events --out smoke.gif

# Scripted-order deterministic tour (no random discovery).
clickcast auto https://example.com \
  --seed-url /pricing --seed-url /docs --seed-url /login \
  --out ordered.gif
```

### 2. `run` — the scripted scenario

When you know exactly what you want clicked in what order, write a YAML scenario and run it. Scenarios are reproducible: same inputs → same reel + same sidecar shape → CI can diff them.

```yaml
# tour.yml
meta:
  name: Login smoke
  viewport: 1280x800
  fps: 12
  format: gif
  out: login.gif

steps:
  - goto: https://staging.example.com/login
    wait: networkidle
    label: Open login
  - click: text=Sign in with Google
    label: Trigger SSO
  - wait: 2.5
  - screenshot: {}
    label: Confirm dashboard
```

```bash
clickcast run tour.yml
```

**Key flags** (see `clickcast run --help`):

| flag | why you want it |
|---|---|
| `--out PATH` | override the scenario's `meta.out` |
| `--url URL` | override the FIRST `goto` step's URL — retarget staging vs prod |
| `--var key=value` (repeatable) | inject a scenario variable |
| `--emit-events` | JSON `tour_complete` line on stdout |
| `--insecure` / `--header` / `--header-host` | internal-host support (explicit CLI flags win over scenario `meta.browser`) |

**Scenario steps you'll actually use:**

- `goto:` — navigate to a URL. Supports `wait: networkidle | load | domcontentloaded | <selector> | <float seconds>`.
- `click:` / `dblclick:` — click a Playwright selector. `optional: true` skips cleanly if the element isn't present.
- `type: {into: 'input[name=q]', text: 'hello'}` — fill an input.
- `hover:` — hover a selector.
- `scroll:` — scroll a page or container. `wheel:` for granular dispatch.
- `wait: 1.5` — hold for N seconds.
- `screenshot: {}` — capture a labeled frame at the current state.
- `assert:` — assert a page state (URL match, element visible, text present); failure marks the step failed and gates the sidecar.

Steps support `repeat: N`, `optional: true` (skip on failure), and `label: "..."` (overrides the auto-generated label in the reel overlay).

### 3. `shot` — the one-frame receipt

When you just want one image and no navigation:

```bash
clickcast shot https://example.com --out landing.png --full-page --wait networkidle
```

`--wait` accepts the same values as scenarios: `load`, `domcontentloaded`, `networkidle`, a CSS selector, or a float. `--full-page` captures beyond the viewport (long-page screenshot).

Same internal-host flags apply: `--insecure`, `--header`, `--header-host`.

---

## Supporting commands (use them, but less often)

### `elements` — inspect what's clickable

Before writing a scenario, dump what clickcast can see on a page:

```bash
clickcast elements https://example.com --limit 20 --json
```

Emits a JSON array of `{role, text, selector, score}` objects. Use this to pick reliable selectors instead of guessing.

### `init` — scaffold a scenario

```bash
clickcast init tour.yml --url https://example.com --from-auto
```

`--from-auto` runs discovery once and seeds the scenario with real selectors — faster than writing YAML from scratch.

### `assertions` — CI-stable distillation

Turn a sidecar into a byte-identical assertion set, then diff a committed baseline in CI:

```bash
# Once — commit this to your repo.
clickcast run tour.yml
clickcast assertions tour.gif.json > baseline.json

# In CI — nonzero exit on drift.
clickcast run tour.yml
clickcast assertions tour.gif.json --baseline baseline.json
```

The distillation strips timings and file paths so runs are reproducible even across machines. See [`docs/assertions-schema/v1.json`](docs/assertions-schema/v1.json).

### `report-bug` — actionable failure reports

When a tour looks wrong, don't just paste the sidecar into an issue — turn it into a filled-out report:

```bash
clickcast report-bug tour.gif.json --json
```

Emits a payload matching [`docs/agent-report-schema/v1.json`](docs/agent-report-schema/v1.json) with a prefilled GitHub issue URL. `--open` launches it. `--redact` (default on) strips URLs, selectors, and visible text so you can share the report without leaking a customer's data.

### `doctor` — diagnose the environment

```bash
clickcast doctor --json
```

Checks Python version, Playwright import, per-engine executables, ffmpeg, config dir. Run this first when anything fails early with a mystery error.

### `install` — install browser engines

```bash
clickcast install chromium
clickcast install --with-deps chromium firefox webkit
```

Thin wrapper around `playwright install`. `--with-deps` also installs system libraries (needs sudo on Linux).

### `config` — persistent defaults

```bash
clickcast config set pace fast          # persistent user default
clickcast config get engine             # effective value after all layers
clickcast config list                   # every field with its current value
clickcast config path                   # where the user TOML lives
```

Precedence (first wins): CLI flag → `CLICKCAST_*` env var → `./clickcast.toml` → user TOML → default.

### `feedback` — long-running session summaries

For days-long agent runs where you want a deterministic post-hoc summary of failed invocations:

```bash
clickcast feedback start
# ... many clickcast invocations ...
clickcast feedback summary --json
clickcast feedback stop
```

Zero-network. Local JSONL storage. Useful when you want to review a batch of failures without re-running each.

### `skill` — the shorter brief

```bash
clickcast skill            # human-readable markdown
clickcast skill --json     # structured payload for tool discovery
```

The 900-word brief. This document is the long version; that command is the elevator pitch.

---

## Workflow patterns

### Pattern A — Automated tour → CI baseline

Nightly regression on a critical flow. Uses `auto` for discovery, `assertions` for CI-stable diff.

```bash
# First run — commit the baseline.
clickcast auto https://prod.example.com --max-pages 3 --out tour.gif
clickcast assertions tour.gif.json > tests/baselines/prod.tour.json
git add tests/baselines/prod.tour.json tour.gif tour.gif.json
git commit -m "add prod tour baseline"

# In CI (runs nightly / on merge to main).
clickcast auto https://prod.example.com --max-pages 3 --out tour.gif
clickcast assertions tour.gif.json --baseline tests/baselines/prod.tour.json
# Nonzero exit → tour drifted → PR blocks. Investigate.
```

### Pattern B — Scripted scenario → deterministic PR gate

You know the exact user flow that matters (login → create thing → confirm success). Use `run`, gate on both exit code and assertion drift.

```bash
clickcast run tour.yml --url https://pr-preview-123.example.com
if [[ $? -ne 0 ]]; then
  clickcast report-bug tour.gif.json --json > report.json
  # Post `report` to the PR as a check comment.
fi
```

### Pattern C — Internal / SSO-protected hosts

The `--insecure` / `--header` / `--header-host` trio (added in v0.2.6 — see [issue #166](https://github.com/AlexKay28/clickcast/issues/166)) unblocks any private-CA or bearer-token-guarded host.

```bash
# Corporate wiki behind SSO that accepts Authorization: Bearer <token>.
export CLICKCAST_INSECURE=1                       # private CA
export CLICKCAST_HEADER_HOST=wiki.internal.example.com
export CLICKCAST_HEADER='Authorization: Bearer '"$WIKI_TOKEN"

clickcast shot https://wiki.internal.example.com/team/onboarding --out onboarding.png
```

**Why `--header-host`?** Playwright's context-wide `extra_http_headers` sends the header to EVERY origin the page pulls from — CDNs, analytics, third-party fonts. Handing a bearer token to those is a credential leak. `--header-host` scopes delivery to one origin via route interception. Hostname match is exact or dotted-suffix (`.example.com` matches `a.example.com` and `example.com`), but bare labels like `.net` only exact-match to prevent whole-TLD scoping accidents.

**Applies to** `shot`, `auto`, `run`, and `elements`. In scenarios you can also write them flat in meta:

```yaml
meta:
  insecure: true
  header_host: internal.example.com
  extra_headers:
    Authorization: Bearer XYZ
```

### Pattern D — Bug reporting loop

When something goes wrong, the loop is: run → sidecar → `report-bug` → GitHub issue.

```bash
# Something failed in prod.
clickcast auto https://prod.example.com --out tour.gif --with-feedback
# Tour hit an error — inspect the sidecar step that failed.
clickcast report-bug tour.gif.json --open
# Browser opens with the issue title, body, and environment prefilled.
```

The sidecar's `error_code` field (v3 schema) is a stable enum: `timeout | locator_missing | cross_origin | navigation_error | selector_ambiguous | other`. Gate your CI on `error_code`, not on freeform error strings.

### Pattern E — Deterministic env variable driven config

Agents typically want to configure once and forget. Precedence-aware config:

```bash
# Set once in your agent's env-file / secrets store.
export CLICKCAST_ENGINE=chromium
export CLICKCAST_VIEWPORT=1440x900
export CLICKCAST_PACE=fast
export CLICKCAST_INSECURE=1
export CLICKCAST_HEADER_HOST=internal.example.com
export CLICKCAST_HEADER='Authorization: Bearer '"$INTERNAL_TOKEN"
```

Now every `clickcast auto|shot|elements` call picks these up without flags. `run` respects scenario meta first — override only what the flag was typed for.

---

## The sidecar contract

Every reel-producing command emits `<out>.json`. Schema at [`src/clickcast/feedback/schema/v3.json`](src/clickcast/feedback/schema/v3.json). You should think of the reel as the artefact for humans and the sidecar as the artefact for you.

Top-level fields you'll actually read:

- `schema_version`: `3` at time of writing. Additive changes only (v2 sidecars validate against v3).
- `clickcast_version`, `engine`, `viewport`, `url`, `started_at`, `duration_s` — run metadata.
- `steps`: array of `StepReport` — one per step executed. Each has:
  - `status`: `ok` | `failed` | `skipped`.
  - `action`: `goto` | `click` | `type` | ... — same verbs as scenario steps.
  - `selector`, `label`, `url_before`, `url_after`, `duration_ms`.
  - `frame_path`: relative path to the captured PNG for this step.
  - `page_state`: post-action DOM snapshot (title, URL, visible text summary).
  - `error`: freeform message (human-readable).
  - `error_code`: stable enum (see above) — **gate on this, not on `error`**.
  - `skip_reason`: enum for skipped steps: `optional_no_reaction | pre_action_failed | element_vanished | cross_origin_bounce`.
- `feedback` (opt-in via `--with-feedback`): pointer block with issue URL, schema URL, docs URL.
- `graph` (v2+): nodes/edges for LLM planning — the URLs the tour visited and the transitions between them.

**Reading the sidecar in Python:**

```python
from clickcast.feedback import load

report = load("tour.gif.json")
for step in report.steps:
    if step.status == "failed":
        print(f"step {step.label} failed: {step.error_code} — {step.error}")
```

---

## Failure recovery — what to do when things go wrong

### The tour stops early

**Symptom:** `duration_s < max_duration`, `steps[-1].status == 'failed'`.

**Fixes:**
- If `error_code == 'timeout'`: try a slower `--pace` or bump `--click-timeout`. The default is 5s; a JS-heavy SPA may need 10-15s.
- If `error_code == 'locator_missing'`: your selector went stale. Re-run `clickcast elements <url>` to see current selectors.
- If `error_code == 'cross_origin'`: the tour navigated off the target host and the traversal stopped. That's usually correct — cross-origin means outside the app-under-test. Add `--seed-url` to force a return.
- If `error_code == 'navigation_error'`: TLS or auth. See Pattern C above.

### The GIF looks blank / white

**Symptom:** reel opens but is 1-3 seconds of white before any real content.

**Fixes:**
- SPA hydration. Bump `--initial-wait` (default 2s) or use `--title-card` to mask the first frame.
- `wait: networkidle` on the first goto step in your scenario — the default `load` fires before subresources settle.

### The sidecar looks empty

**Symptom:** `steps` array has one entry, then nothing.

**Fixes:**
- Check `error` on that step. Usually a bad URL, TLS error, or auth wall.
- Run `clickcast doctor --json` — playwright / browser install issues surface here.

### Something feels off but no error

**Symptom:** everything says `ok` but the recorded flow doesn't match your intent.

**Fixes:**
- Watch the reel. Really. Two minutes with your own eyes beats an hour of sidecar-staring.
- Add `assert:` steps to your scenario — `assert: {url_matches: '/dashboard'}` makes drift loud instead of silent.
- File a bug: `clickcast report-bug tour.gif.json --json`. Your report becomes an actionable issue with reproduction context prefilled.

---

## Where to go from here

- **CLI-embedded brief:** `clickcast skill` (~900 words). Use for tool-discovery messages.
- **Python library integration:** [`docs/ai-integration.md`](docs/ai-integration.md) — how to call clickcast from your agent's code.
- **Sidecar schema:** [`src/clickcast/feedback/schema/v3.json`](src/clickcast/feedback/schema/v3.json).
- **Assertions schema:** [`docs/assertions-schema/v1.json`](docs/assertions-schema/v1.json).
- **Scenario examples:** [`docs/scenarios/`](docs/scenarios/) — real YAML you can copy.
- **Human docs:** [`docs/for-agents.md`](docs/for-agents.md) — the shorter intro.
- **Issues & feedback:** [github.com/AlexKay28/clickcast/issues](https://github.com/AlexKay28/clickcast/issues). File one when something feels off — even a one-sentence "this failed on X" is useful.
