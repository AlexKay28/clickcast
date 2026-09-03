// Builds and upserts the clickcast CI PR comment.
//
// Loaded by action.yml's "Post / update PR comment" step (an
// `actions/github-script` step) via `require(...)`, rather than inlined as
// a YAML script block, so it can be linted/tested like a normal JS file
// instead of a string embedded in YAML.
//
// Upserts by a hidden HTML-comment marker (MARKER below) so re-running the
// workflow on the same PR updates the existing comment instead of piling up
// duplicates (#208's acceptance criterion).
"use strict";

const fs = require("fs");

const MARKER = "<!-- clickcast-ci-comment -->";

function readJson(path) {
  if (!path) return null;
  try {
    return JSON.parse(fs.readFileSync(path, "utf8"));
  } catch {
    // Missing/unreadable file (gate didn't run, or produced no output) --
    // render as "did not run" rather than crashing the comment step.
    return null;
  }
}

function statusIcon(status) {
  if (status === "ok") return "✅";
  if (status === "skipped") return "⏭️";
  return "❌";
}

function assertionsSection(data) {
  if (!data) return "_(assertions gate did not run)_";
  const steps = (data.current && data.current.steps) || [];
  const lines = ["| # | Action | Label | Status | Errors |", "| --- | --- | --- | --- | --- |"];
  steps.forEach((s, i) => {
    const errStr = `console ${s.console_error_count || 0} / page ${s.page_error_count || 0} / net ${
      s.network_failed_count || 0
    }`;
    lines.push(
      `| ${i} | \`${s.action}\` | ${s.label || ""} | ${statusIcon(s.status)} ${s.status} | ${errStr} |`
    );
  });
  let out = lines.join("\n");
  if (data.drift && data.drift.length) {
    out += "\n\n**Drift:**\n" + data.drift.map((d) => `- ${d}`).join("\n");
  }
  return out;
}

function diffSection(data, failAbove) {
  if (!data) return "_(visual diff gate did not run)_";
  const steps = data.steps || [];
  const unmatched = data.unmatched_steps || [];
  const threshold = failAbove ? parseFloat(failAbove) : null;
  const lines = ["| # | Label | Changed % | Regions |", "| --- | --- | --- | --- |"];
  for (const s of steps) {
    const pct = s.changed_pct.toFixed(2);
    const over = threshold !== null && s.changed_pct > threshold;
    const icon = threshold === null ? "·" : over ? "❌" : "✅";
    lines.push(`| ${s.run_index} | ${s.label || ""} | ${icon} ${pct}% | ${(s.regions || []).length} |`);
  }
  let out = lines.join("\n");
  if (unmatched.length) {
    out +=
      "\n\n**Unmatched steps:**\n" +
      unmatched.map((u) => `- ${u.side} step ${u.index} (${u.label || "n/a"}): ${u.reason}`).join("\n");
  }
  return out;
}

module.exports = async ({ github, context, core }) => {
  const pr = context.payload.pull_request;
  if (!pr) {
    core.info("clickcast: not a pull_request event, skipping PR comment.");
    return;
  }

  const reelUrl = process.env.REEL_IMAGE_URL || "";
  const runUrl = process.env.RUN_URL || "";

  const assertionsSet = process.env.ASSERTIONS_BASELINE_SET === "true";
  const assertionsPassed = process.env.ASSERTIONS_PASSED || "";
  const assertionsData = readJson(process.env.ASSERTIONS_RESULT_PATH);

  const diffSet = process.env.DIFF_BASELINE_SET === "true";
  const diffPassed = process.env.DIFF_PASSED || "";
  const diffFailAbove = process.env.DIFF_FAIL_ABOVE || "";
  const diffData = readJson(process.env.DIFF_SUMMARY_PATH);

  const lines = [MARKER, "## 🎬 clickcast CI"];

  if (reelUrl) {
    lines.push(`![clickcast reel](${reelUrl})`);
  } else if (runUrl) {
    lines.push(
      `_Reel image not published inline (fork PR, or \`publish-reel-image: false\`) — [download it from the workflow run](${runUrl})._`
    );
  }

  // `overall` starts `null` ("nothing to gate on") and only ever gets
  // pulled to `false` -- one failing gate fails the whole summary, even if
  // the other one passed.
  let overall = null;
  if (assertionsSet) {
    if (assertionsPassed === "false") overall = false;
    else if (overall === null) overall = true;
    lines.push("", "### Structural assertions", assertionsSection(assertionsData));
  }
  if (diffSet) {
    if (diffPassed === "false") overall = false;
    else if (diffPassed === "true" && overall === null) overall = true;
    lines.push("", "### Visual diff", diffSection(diffData, diffFailAbove));
  }

  if (overall === null) {
    lines.push("", "_No baseline configured — reel captured, nothing to gate against._");
  } else {
    lines.push("", overall ? "**Result: ✅ passed**" : "**Result: ❌ failed — see details above**");
  }

  const body = lines.join("\n");
  const { owner, repo } = context.repo;
  const issue_number = pr.number;

  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number,
    per_page: 100,
  });
  const existing = comments.find((c) => c.body && c.body.includes(MARKER));

  if (existing) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
  } else {
    await github.rest.issues.createComment({ owner, repo, issue_number, body });
  }
};

// Exposed for unit tests (scripts/post_comment.test.js) -- the exported
// value is still the callable main function above, these are just extra
// properties on it so the markdown-building logic can be tested without
// mocking the octokit/`github` client.
module.exports.MARKER = MARKER;
module.exports.assertionsSection = assertionsSection;
module.exports.diffSection = diffSection;
module.exports.statusIcon = statusIcon;
