# a-b-comparison

**When to use:** you have two variants of a page (feature flag, A/B test,
before/after refactor) and want to compare their behavior side-by-side.

## Command

Run the same scenario twice, once per variant:

```bash
clickcast auto https://app.example.com/?variant=control \
  --max-pages 3 --max-steps 15 --dwell 0.3 \
  --out control.gif

clickcast auto https://app.example.com/?variant=treatment \
  --max-pages 3 --max-steps 15 --dwell 0.3 \
  --out treatment.gif
```

Or with `--seed-url` for a deterministic path in both:

```bash
for variant in control treatment; do
  clickcast auto https://app.example.com/?variant=$variant \
    --seed-url https://app.example.com/checkout?variant=$variant \
    --seed-url https://app.example.com/confirmation?variant=$variant \
    --out $variant.gif
done
```

## Reels

Side-by-side (control | treatment):

![control variant](reel-control.gif) ![treatment variant](reel-treatment.gif)

## What to compare

The sidecars carry the signal. Timing delta is the most common:

```bash
# Compare page-load / step timings between variants:
diff \
  <(jq '.steps[] | {step: .step_index, action, ms: .duration_ms}' control.gif.json) \
  <(jq '.steps[] | {step: .step_index, action, ms: .duration_ms}' treatment.gif.json)
```

Other useful comparisons:
- `discovered` list — did the treatment surface add new interactive
  elements? Remove old ones?
- `page_state.console_errors` — did one variant introduce new JS errors?
- `page_state.network_failed` — did one variant break an API call?

## Compositing the two reels

If you want a single side-by-side GIF for a report:

```bash
# imagemagick approach:
convert control.gif treatment.gif +append side-by-side.gif

# ffmpeg (better GIF quality):
ffmpeg -i control.gif -i treatment.gif \
  -filter_complex "[0:v][1:v]hstack=inputs=2" \
  side-by-side.gif
```

## Why clickcast for A/B testing at all?

Because the sidecar is a structured record of what the browser did in each
variant — much cleaner input to reason about than raw HAR files or screen
recordings. An LLM asked "did the treatment variant hurt the checkout flow?"
can point to specific `duration_ms` deltas per step, not just wave at
"looks slower."
