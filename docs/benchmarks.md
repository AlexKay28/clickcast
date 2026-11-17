# Benchmarks

Two numbers decide whether clickcast is worth an agent's context window and a
human's first five minutes:

| Metric | Question it answers | Budget |
|---|---|---|
| **M1 — install → verified report** | how long from `pip install` to a sidecar a machine can gate on? | < 90 s, warm pip cache |
| **M2 — context cost per step** | how many tokens must an agent hold to know what happened? | sidecar ≈ 1–2 KB/step, `observe()` ≤ 800, delta ≤ 300, receipt ≤ 200 |

Both are measured, not asserted. `scripts/bench_metrics.py` is the only
measurement path; if a change moves a number, the number in this file changes
with it in the same PR.

## Run it

```bash
# one full run (creates a fresh venv, installs clickcast from this checkout)
python scripts/bench_metrics.py

# three tours on one install, artifacts kept for inspection
python scripts/bench_metrics.py --repeat 3 --keep --workdir /tmp/cc-bench

# cold pip (network, no cache) — needs an explicit flag so it is never accidental
python scripts/bench_metrics.py --no-cache-dir

# re-time tours without reinstalling (fast iteration)
python scripts/bench_metrics.py --reuse-venv --workdir /tmp/cc-bench
```

The script is stdlib-only, so it runs before clickcast is installed. It serves
`tests/fixtures/site/` on an ephemeral port, tours `index.html` with
`clickcast auto --max-steps 2`, then **loads the sidecar through the shipped
schema in the venv under test** — "verified report" means schema-valid, not just
"a file exists".

Output is a markdown table on stdout plus optional `--json-out` /
`--markdown-out`. Exit code is non-zero when the tour fails, the sidecar does
not validate, or a regression ceiling is crossed (`--no-ceiling-check` to
report only).

## Baseline

Linux x86-64, 2026-11-17 (sidecar v5), Python 3.12, chromium already
installed, `--repeat 3 --no-cache-dir`:

| Stage | Wall |
|---|---|
| `python -m venv` | 3.2 s |
| `pip install` (cold pip, the stricter reading) | 18.2 s |
| `clickcast doctor --json` | 0.6 s |
| first `auto` tour (4 steps: goto, 2 clicks, scroll) | 9.5 s |
| **M1: install → verified report** | **31.4 s** |
| M2: sidecar bytes/step (p50) | 1,530 B |
| M2: estimated tokens/step (p50) | 382 |
| tour wall p50 / p95 | 9.51 s / 9.55 s |

Earlier run on sidecar v4 (warm pip cache, `--repeat 2`): **32.4 s** and
**1,510 B/step (378 tokens)**. The v5 delta field is `null` on batch runs, so
the per-step cost moved by ~20 bytes — within run-to-run noise.

Deliberately excluded from M1:

- **pip cache misses** — `--no-cache-dir` measures them; they are a PyPI
  property, not a clickcast property.
- **browser download** (~180 MB) — reported on its own row (`engine_install_s`).
  It dominates a truly cold machine and is a Playwright cost, not a clickcast
  one.

### Reading M2

`estimated_tokens_per_step` uses the same deterministic estimator the
perception API ships: `ceil(chars / 4)`. It is model-agnostic on purpose — the
contract is a stable budget, not a vendor-specific count. A real tokenizer is
an optional cross-check, never a runtime dependency.

The batch sidecar is still the honest per-step baseline. The live-loop
payloads now have measured ceilings enforced in tests:

| Primitive | Budget | Measured |
|---|---|---|
| `observe()` | ≤ 800 est. tokens, ≤ 20 nodes | 246 on `index.html`; 559 on a 203-element stress page |
| `act()` delta | ≤ 300 est. tokens | 36–39 on plain pages, 70 (tabs click), 95 (menu open) |
| `receipt()` | ≤ 200 est. tokens | 38 (no media), 52 (media), 65 (media + assertions baseline) |

## Regression ceilings

| Ceiling | Value | Rationale |
|---|---|---|
| M1 total (warm) | 180 s | a real regression is a 2× jump, not runner noise |
| sidecar bytes/step | 8,000 B | ~5× the current payload; catches accidental accumulation |

Ceilings live in `scripts/bench_metrics.py` and are intentionally loose: the
job is to catch a 2× regression, never to fail on a slow shared runner.
