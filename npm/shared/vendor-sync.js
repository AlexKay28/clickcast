#!/usr/bin/env node
// npm/shared/vendor-sync.js
//
// Copies provision.js (this directory) into the *calling* package's own
// `vendor/provision.js`. Run from inside a package directory as:
//
//   node ../shared/vendor-sync.js
//
// Both npm/clickcast/package.json and npm/clickcast-mcp/package.json call
// this from their `prepare` (local `npm install` / git-dependency installs)
// and `prepack` (`npm pack` / `npm publish`) lifecycle scripts, so the
// published tarball always contains a real, self-contained copy of the
// provisioning module -- see docs/packaging/npm.md for why this is a build
// step instead of a `file:` dependency.

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const src = path.join(__dirname, "provision.js");
const destDir = path.join(process.cwd(), "vendor");
const dest = path.join(destDir, "provision.js");

if (!fs.existsSync(src)) {
  // Defensive no-op: this only fires for maintainers building from a full
  // monorepo checkout. An end user installing the published package never
  // runs this script (npm's `prepare` lifecycle only runs for local/git
  // installs, not registry dependency installs) -- if it somehow did run
  // with no sibling `shared/` present, do nothing rather than crash the
  // install; postinstall.js's own vendor/provision.js (already baked into
  // the published tarball by this same script at publish time) is what
  // actually matters at that point.
  console.log(`vendor-sync: ${src} not found, nothing to sync (not a monorepo checkout)`);
  process.exit(0);
}

fs.mkdirSync(destDir, { recursive: true });
fs.copyFileSync(src, dest);
console.log(`vendor-sync: copied ${src} -> ${dest}`);
