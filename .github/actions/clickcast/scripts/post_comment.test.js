// Unit tests for the pure markdown-building helpers in post_comment.js.
//
// Uses Node's built-in test runner (no extra dependency -- this repo is a
// Python package with no JS toolchain of its own, and these two small
// composite-action helper scripts don't warrant introducing one). Run with:
//
//   node --test .github/actions/clickcast/scripts/post_comment.test.js
//
// Exercised in CI by .github/workflows/clickcast-self-check.yml.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { MARKER, assertionsSection, diffSection, statusIcon } = require("./post_comment.js");

test("MARKER is a stable HTML comment", () => {
  assert.match(MARKER, /^<!--.*-->$/);
});

test("statusIcon maps known statuses", () => {
  assert.equal(statusIcon("ok"), "✅");
  assert.equal(statusIcon("skipped"), "⏭️");
  assert.equal(statusIcon("failed"), "❌");
  assert.equal(statusIcon("bogus"), "❌");
});

test("assertionsSection renders 'did not run' for null data", () => {
  assert.match(assertionsSection(null), /did not run/);
});

test("assertionsSection renders a row per step and surfaces drift", () => {
  const data = {
    current: {
      steps: [
        { action: "goto", label: "Open", status: "ok", console_error_count: 0, page_error_count: 0, network_failed_count: 0 },
        { action: "click", label: "Click 3D", status: "failed", console_error_count: 1, page_error_count: 0, network_failed_count: 0 },
      ],
    },
    drift: ["step 1: status changed 'ok' -> 'failed'"],
  };
  const out = assertionsSection(data);
  assert.match(out, /\| 0 \| `goto` \| Open \| ✅ ok \|/);
  assert.match(out, /\| 1 \| `click` \| Click 3D \| ❌ failed \|/);
  assert.match(out, /console 1 \/ page 0 \/ net 0/);
  assert.match(out, /\*\*Drift:\*\*/);
  assert.match(out, /status changed 'ok' -> 'failed'/);
});

test("diffSection renders 'did not run' for null data", () => {
  assert.match(diffSection(null, ""), /did not run/);
});

test("diffSection marks steps over the fail-above threshold", () => {
  const data = {
    steps: [
      { run_index: 0, label: "Open", changed_pct: 0.5, regions: [] },
      { run_index: 1, label: "Click 3D", changed_pct: 12.3, regions: [{ x: 0, y: 0, width: 10, height: 10 }] },
    ],
    unmatched_steps: [],
  };
  const out = diffSection(data, "5");
  assert.match(out, /\| 0 \| Open \| ✅ 0\.50% \| 0 \|/);
  assert.match(out, /\| 1 \| Click 3D \| ❌ 12\.30% \| 1 \|/);
});

test("diffSection uses a neutral marker in report-only mode (no fail-above)", () => {
  const data = { steps: [{ run_index: 0, label: "Open", changed_pct: 99.0, regions: [] }], unmatched_steps: [] };
  const out = diffSection(data, "");
  assert.match(out, /\| 0 \| Open \| · 99\.00% \| 0 \|/);
});

test("diffSection surfaces unmatched steps", () => {
  const data = {
    steps: [],
    unmatched_steps: [{ side: "run", index: 2, label: "Extra step", reason: "no baseline counterpart" }],
  };
  const out = diffSection(data, "");
  assert.match(out, /\*\*Unmatched steps:\*\*/);
  assert.match(out, /run step 2 \(Extra step\): no baseline counterpart/);
});
