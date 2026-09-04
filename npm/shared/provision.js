// npm/shared/provision.js
//
// Shared provisioning logic for the clickcast / clickcast-mcp npm packages.
// Both packages' `postinstall.js` (and `bin/*.js`) require a *vendored copy*
// of this exact file rather than requiring it across the package boundary --
// see docs/packaging/npm.md ("Why a vendored copy, not a `file:` dependency")
// for why: the two packages are published independently to the npm
// registry, and a `file:../shared` dependency only resolves for someone who
// has this monorepo checked out next to the installed package -- it breaks
// for a real end user who just ran `npm install clickcast-mcp`. Each
// package's `prepack`/`prepare` script copies this file verbatim into its
// own `vendor/provision.js` before packing/publishing, so the published
// tarball is self-contained. Edit this file; never hand-edit a vendored
// copy (they're regenerated, not maintained separately).
//
// No dependencies beyond Node's stdlib -- this runs inside `postinstall`,
// before any npm dependency of the package itself (there are none) would be
// available, and must work offline-detection-wise even when pip/PyPI is
// unreachable (it should fail with a clear message, not a stack trace).

"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const MIN_PYTHON = [3, 10];

/**
 * Candidate system Python executables to probe, in priority order.
 * `CLICKCAST_NPM_PYTHON`, when set, short-circuits this entirely (used by
 * the test suite / docs/packaging/npm.md's verification steps to force the
 * "missing/too old Python" failure path deterministically).
 */
function pythonCandidates() {
  const override = process.env.CLICKCAST_NPM_PYTHON;
  if (override !== undefined) {
    // Even an empty string is an explicit "pretend nothing is installed".
    return override === "" ? [] : [override];
  }
  if (process.platform === "win32") {
    return ["py -3", "python3", "python"];
  }
  return ["python3", "python"];
}

function runCapture(cmd, args, opts) {
  return spawnSync(cmd, args, { encoding: "utf8", ...opts });
}

/**
 * Split a candidate like "py -3" into { cmd: "py", args: ["-3"] }.
 */
function splitCandidate(candidate) {
  const parts = candidate.split(" ").filter(Boolean);
  return { cmd: parts[0], args: parts.slice(1) };
}

/**
 * Probe one candidate. Returns { executable, version: [major, minor] } on
 * success, or null if the candidate isn't runnable / isn't real Python.
 */
function probePython(candidate) {
  const { cmd, args } = splitCandidate(candidate);
  const result = runCapture(cmd, [
    ...args,
    "-c",
    "import sys; print('%d.%d' % sys.version_info[:2])",
  ]);
  if (result.error || result.status !== 0) {
    return null;
  }
  const out = (result.stdout || "").trim();
  const match = /^(\d+)\.(\d+)$/.exec(out);
  if (!match) {
    return null;
  }
  return {
    candidate,
    version: [Number(match[1]), Number(match[2])],
  };
}

function versionAtLeast(version, min) {
  if (version[0] !== min[0]) {
    return version[0] > min[0];
  }
  return version[1] >= min[1];
}

/**
 * Find a usable system Python (>= MIN_PYTHON). Returns the probe result, or
 * throws a ProvisionError with a specific, actionable message -- never a
 * silent failure or a bare stack trace. Mirrors the bar #216 set for the
 * Python CLI's own missing-engine UX.
 */
function findSystemPython(pkgName) {
  const candidates = pythonCandidates();
  const tried = [];
  let newestFound = null;

  for (const candidate of candidates) {
    const probe = probePython(candidate);
    if (!probe) {
      tried.push(`${candidate} (not found or not runnable)`);
      continue;
    }
    tried.push(`${candidate} (found Python ${probe.version.join(".")})`);
    if (versionAtLeast(probe.version, MIN_PYTHON)) {
      return probe;
    }
    if (!newestFound || probe.version.join(".") > newestFound.version.join(".")) {
      newestFound = probe;
    }
  }

  const minStr = MIN_PYTHON.join(".");
  const lines = [
    `${pkgName}: could not find a system Python >= ${minStr}.`,
    "",
    "This package provisions an isolated Python environment for clickcast",
    "(under this package's own install directory -- it never touches your",
    "global/system site-packages), but it needs a Python interpreter already",
    `installed on your system (>= ${minStr}) to build that environment from.`,
    "",
    tried.length
      ? `Checked: ${tried.join(", ")}.`
      : "No Python candidates were checked (CLICKCAST_NPM_PYTHON=\"\" forces this).",
    "",
    newestFound
      ? `Found Python ${newestFound.version.join(".")}, which is too old.`
      : "",
    "Fix: install Python " + minStr + "+ from https://www.python.org/downloads/",
    "(or your OS package manager -- e.g. `apt install python3.12`,",
    "`brew install python@3.12`), make sure it's on PATH, then re-run",
    "`npm install`.",
    "",
    "This package deliberately does not install Python itself -- see",
    "docs/packaging/npm.md in the clickcast repo for why.",
  ].filter((l) => l !== "");

  throw new ProvisionError(lines.join("\n"));
}

class ProvisionError extends Error {}

function venvDir(pkgRoot) {
  return path.join(pkgRoot, ".venv");
}

function venvPythonPath(pkgRoot) {
  return process.platform === "win32"
    ? path.join(venvDir(pkgRoot), "Scripts", "python.exe")
    : path.join(venvDir(pkgRoot), "bin", "python3");
}

/**
 * Path to a console-script entry point installed into the provisioned venv
 * (e.g. "clickcast"). Used by both packages' bin/*.js shims.
 */
function venvBinPath(pkgRoot, name) {
  const exe = process.platform === "win32" ? `${name}.exe` : name;
  return process.platform === "win32"
    ? path.join(venvDir(pkgRoot), "Scripts", exe)
    : path.join(venvDir(pkgRoot), "bin", exe);
}

function run(cmd, args, opts) {
  const result = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new ProvisionError(
      `command failed (exit ${result.status}): ${cmd} ${args.join(" ")}`
    );
  }
}

/**
 * Provision an isolated venv under `pkgRoot/.venv` and pip-install a pinned
 * `clickcast==<version>` (plus optional extras) into it. Idempotent: safe to
 * call on every `npm install` (re-run/upgrade), skips venv creation if one
 * already exists.
 *
 * @param {object} opts
 * @param {string} opts.pkgRoot - directory to provision the venv under
 *   (each npm package's own install dir -- never the user's global Python).
 * @param {string} opts.pkgName - for error/log messages, e.g. "clickcast-mcp".
 * @param {string} opts.clickcastVersion - exact PyPI version to pin, e.g. "0.3.1".
 * @param {string[]} [opts.extras] - optional-dependency extras, e.g. ["mcp"].
 */
function provision({ pkgRoot, pkgName, clickcastVersion, extras = [] }) {
  if (process.env.CLICKCAST_NPM_SKIP_POSTINSTALL === "1") {
    console.log(`${pkgName}: CLICKCAST_NPM_SKIP_POSTINSTALL=1 set, skipping provisioning.`);
    return;
  }

  console.log(`${pkgName}: locating a system Python >= ${MIN_PYTHON.join(".")}...`);
  const python = findSystemPython(pkgName);
  console.log(
    `${pkgName}: using "${python.candidate}" (Python ${python.version.join(".")})`
  );

  const venv = venvDir(pkgRoot);
  const venvPython = venvPythonPath(pkgRoot);

  if (fs.existsSync(venvPython)) {
    console.log(`${pkgName}: reusing existing venv at ${venv}`);
  } else {
    console.log(`${pkgName}: creating isolated venv at ${venv} (not your system Python)...`);
    fs.rmSync(venv, { recursive: true, force: true });
    const { cmd, args } = splitCandidate(python.candidate);
    run(cmd, [...args, "-m", "venv", venv]);
  }

  const spec =
    extras.length > 0
      ? `clickcast[${extras.join(",")}]==${clickcastVersion}`
      : `clickcast==${clickcastVersion}`;

  console.log(`${pkgName}: pip install ${spec} (into the isolated venv only)...`);
  try {
    run(venvPython, ["-m", "pip", "install", "--disable-pip-version-check", "--quiet", spec]);
  } catch (err) {
    throw new ProvisionError(
      [
        `${pkgName}: \`pip install ${spec}\` failed inside the provisioned venv.`,
        "",
        "Common causes: no network access to PyPI at install time, or a",
        "corporate proxy blocking pypi.org. This package does not fall back",
        "to a partially-working install -- fix network/proxy access and",
        "re-run `npm install`, or install clickcast directly with:",
        "",
        `    python3 -m venv .venv && .venv/bin/pip install ${spec}`,
        "",
        `(original error: ${err.message})`,
      ].join("\n")
    );
  }

  const clickcastBin = venvBinPath(pkgRoot, "clickcast");
  if (!fs.existsSync(clickcastBin)) {
    throw new ProvisionError(
      `${pkgName}: pip install reported success but no clickcast entry point ` +
        `was found at ${clickcastBin}. This indicates a broken clickcast ` +
        "release -- please file an issue at " +
        "https://github.com/AlexKay28/clickcast/issues."
    );
  }

  console.log(`${pkgName}: provisioned clickcast==${clickcastVersion} at ${venv}`);
}

module.exports = {
  ProvisionError,
  provision,
  venvDir,
  venvPythonPath,
  venvBinPath,
  findSystemPython,
  MIN_PYTHON,
  _internal: { probePython, splitCandidate, versionAtLeast, pythonCandidates },
};
