#!/usr/bin/env node
// npm/clickcast/bin/clickcast.js
//
// General-purpose CLI wrapper. Execs the venv postinstall.js provisioned,
// forwarding argv, stdio, and exit code transparently -- so `npx clickcast
// <anything>` behaves exactly like `clickcast <anything>` run from a real
// pip install, including interactive prompts (e.g. #216's missing-Chromium
// self-heal) passing straight through rather than being captured/buffered.

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
  console.error("clickcast: could not locate the provisioning module.");
  process.exit(1);
}

const { venvBinPath } = loadProvision();

const clickcastBin = venvBinPath(packageRoot, "clickcast");

if (!fs.existsSync(clickcastBin)) {
  console.error(
    "clickcast: the provisioned clickcast install is missing " +
      `(expected ${clickcastBin}). \`npm install\`'s postinstall step ` +
      "should have created it -- try reinstalling: `npm install clickcast`."
  );
  process.exit(1);
}

const args = process.argv.slice(2);

const result = spawnSync(clickcastBin, args, { stdio: "inherit" });

if (result.error) {
  console.error(`clickcast: failed to launch clickcast: ${result.error.message}`);
  process.exit(1);
}

if (result.signal) {
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status === null ? 1 : result.status);
}
