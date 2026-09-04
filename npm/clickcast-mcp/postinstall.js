#!/usr/bin/env node
// npm/clickcast-mcp/postinstall.js
//
// Provisions an isolated Python venv under this package's own install
// directory (node_modules/clickcast-mcp/.venv) and pip-installs the pinned
// `clickcast[mcp]==<version>` into it. Never touches the user's global
// Python / site-packages -- see docs/packaging/npm.md.

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PKG = require("./package.json");

function loadProvision() {
  const vendored = path.join(__dirname, "vendor", "provision.js");
  if (fs.existsSync(vendored)) {
    return require(vendored);
  }
  // Monorepo-checkout fallback (e.g. running postinstall.js directly during
  // local dev without having packed/vendored yet) -- never hit in a real
  // published install, where `vendor/provision.js` is always baked into the
  // tarball by the `prepack` lifecycle script.
  const shared = path.join(__dirname, "..", "shared", "provision.js");
  if (fs.existsSync(shared)) {
    return require(shared);
  }
  console.error(
    "clickcast-mcp: postinstall could not locate the provisioning module " +
      "(neither vendor/provision.js nor ../shared/provision.js exists). " +
      "This indicates a broken package build -- please file an issue at " +
      "https://github.com/AlexKay28/clickcast/issues."
  );
  process.exit(1);
}

const { provision, ProvisionError } = loadProvision();

// PKG.version tracks the exact PyPI clickcast version this package wraps
// (see docs/packaging/npm.md) -- no floating range, an exact pin.
try {
  provision({
    pkgRoot: __dirname,
    pkgName: "clickcast-mcp",
    clickcastVersion: PKG.version,
    extras: ["mcp"],
  });
} catch (err) {
  if (err instanceof ProvisionError) {
    console.error("");
    console.error(err.message);
    console.error("");
  } else {
    console.error("clickcast-mcp: postinstall failed unexpectedly:");
    console.error(err);
  }
  process.exit(1);
}
