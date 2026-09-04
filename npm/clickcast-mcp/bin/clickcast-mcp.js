#!/usr/bin/env node
// npm/clickcast-mcp/bin/clickcast-mcp.js
//
// npx-first MCP entry point. Execs straight into `clickcast mcp` inside the
// venv postinstall.js provisioned -- skips the general CLI surface entirely
// (that's the `clickcast` npm package's job). Forwards argv, stdio, and
// exit code transparently, so:
//
//   { "mcpServers": { "clickcast": { "command": "npx", "args": ["-y", "clickcast-mcp"] } } }
//
// behaves exactly like `clickcast mcp` run directly -- including passing
// through the interactive missing-Chromium self-heal prompt (#216) rather
// than swallowing/buffering it: stdio is `"inherit"`, never captured.

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const packageRoot = path.join(__dirname, "..");

function loadProvision() {
  const vendored = path.join(packageRoot, "vendor", "provision.js");
  if (fs.existsSync(vendored)) {
    return require(vendored);
  }
  const shared = path.join(packageRoot, "..", "shared", "provision.js");
  if (fs.existsSync(shared)) {
    return require(shared);
  }
  console.error("clickcast-mcp: could not locate the provisioning module.");
  process.exit(1);
}

const { venvBinPath } = loadProvision();

const clickcastBin = venvBinPath(packageRoot, "clickcast");

if (!fs.existsSync(clickcastBin)) {
  console.error(
    "clickcast-mcp: the provisioned clickcast install is missing " +
      `(expected ${clickcastBin}). \`npm install\`'s postinstall step ` +
      "should have created it -- try reinstalling: `npm install clickcast-mcp`."
  );
  process.exit(1);
}

// "mcp" is not user-overridable here on purpose -- this package's entire
// point is a fixed, narrow surface (`clickcast mcp`); flags after it
// (e.g. --grid, --viewport) still forward straight through, matching
// docs/mcp.md's documented `clickcast mcp <flags>` client-config shape.
const args = ["mcp", ...process.argv.slice(2)];

const result = spawnSync(clickcastBin, args, { stdio: "inherit" });

if (result.error) {
  console.error(`clickcast-mcp: failed to launch clickcast: ${result.error.message}`);
  process.exit(1);
}

if (result.signal) {
  // Terminated by a signal (e.g. Ctrl-C) rather than exiting normally --
  // re-raise the same signal on ourselves instead of guessing an exit code.
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status === null ? 1 : result.status);
}
