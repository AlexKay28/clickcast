# npm

`clickcast` ships two npm packages so the Node.js ecosystem -- and
specifically the MCP ecosystem, whose dominant install pattern is
`npx <package>` -- doesn't need a Python-only `pip install` to reach it.
This is day-one distribution per [#222] (the third packaging channel after
[#170]/[#215]'s Homebrew and apt work) -- see that issue for the full
rationale.

| Package | Purpose |
| --- | --- |
| [`clickcast-mcp`](../../npm/clickcast-mcp/) | npx-first MCP entry point. `bin/clickcast-mcp` execs straight into `clickcast mcp` -- the issue's own suggested v1 priority, since it's what unblocks a one-line MCP client config. |
| [`clickcast`](../../npm/clickcast/) | General CLI wrapper. `bin/clickcast` forwards argv/stdio/exit-code into the full wrapped Python CLI. |

## What's live today vs. what needs bootstrapping

| Install path | Status |
| --- | --- |
| `npm pack` either package (clone this repo first) + `npm install -g ./clickcast-mcp-<version>.tgz` (or `./clickcast-<version>.tgz`) | **Works today** -- verified in this PR, see "Verification performed" below. |
| `npx -y clickcast-mcp` / `npm install -g clickcast-mcp` / `npm install -g clickcast` from the real npm registry | **Needs the one-time bootstrap below** -- neither package has been published under the `~alexkay` npm account yet. |

## How it works

clickcast itself is a Python package with real, non-trivial runtime
dependencies (Playwright's browser binaries, `imageio[ffmpeg]`) -- there's
no way to ship it as a pure JS package (same conclusion issue #222 reaches,
citing the same shape most "Python tool via npm" wrappers use). Both
packages are thin Node shims around a Python install:

1. **`postinstall` provisions an isolated venv** under the npm package's own
   install directory -- `node_modules/clickcast-mcp/.venv` or
   `node_modules/clickcast/.venv`, **never** the user's global/system Python
   site-packages. Same isolation principle `pipx` uses: this never fights an
   existing `pip install clickcast` on the same machine, and never needs
   sudo/admin (the venv lives entirely under the npm-managed directory the
   current user already owns).
2. It then runs `pip install clickcast==<pinned-version>` into that venv (an
   **exact pin**, no floating range -- see "Supply-chain note" below), and
   for `clickcast-mcp` specifically, `pip install 'clickcast[mcp]==<version>'`
   (the `mcp` extra).
3. Each package's `bin/` entry (`bin/clickcast.js`, `bin/clickcast-mcp.js`)
   execs the provisioned venv's console-script entry point directly via
   `child_process.spawnSync(..., { stdio: "inherit" })`, forwarding argv,
   stdio, and exit code transparently. `clickcast-mcp`'s bin script always
   prepends `mcp` to the forwarded args (skips the general CLI surface
   entirely) -- everything after that (`--grid`, `--viewport ...`) still
   forwards straight through, matching [`docs/mcp.md`](../mcp.md)'s
   documented `clickcast mcp <flags>` client-config shape.

### Shared provisioning logic: a vendored copy, not a `file:` dependency

Both packages' venv-provisioning code lives in one place,
[`npm/shared/provision.js`](../../npm/shared/provision.js) -- the issue
explicitly asks not to duplicate this logic twice. The two options it
suggests are a `file:` dependency or a relative `require` across the two
package directories. Neither is used directly, because **both packages are
published independently to the npm registry** -- a real end user runs
`npm install clickcast-mcp` in isolation; the sibling `clickcast/` package
directory (and this repo's `npm/shared/`) will not exist on their machine.
A `file:../shared` dependency, or a bare `require("../shared/provision.js")`
in the published `postinstall.js`, would resolve fine inside this monorepo
checkout and then break the moment either package is actually published and
installed on its own.

Instead: each package's `package.json` runs
[`npm/shared/vendor-sync.js`](../../npm/shared/vendor-sync.js) via its
`prepare` (local `npm install`/git-dependency installs) and `prepack`
(`npm pack`/`npm publish`) lifecycle scripts, which copies
`npm/shared/provision.js` verbatim into that package's own
`vendor/provision.js`. `prepack` always runs before the tarball is created,
so every published tarball -- and every `npm pack` output -- is genuinely
self-contained: `postinstall.js` and `bin/*.js` `require("./vendor/provision.js")`,
a real file inside the package, not a reference outside it. Editing the
provisioning logic means editing `npm/shared/provision.js` once; both
packages pick it up automatically the next time either is packed.
(`vendor/` and `node_modules/` under `npm/` are gitignored -- generated, not
hand-maintained; `postinstall.js`/`bin/*.js` also fall back to
`require("../shared/provision.js")` directly if `vendor/` is somehow
missing, purely as a monorepo-dev convenience -- never hit in a real
published install.)

### Postinstall failure modes

`postinstall` fails loudly with a specific, actionable message -- never a
silently broken `bin/` shim -- matching the bar [#216] set for the Python
CLI's own missing-engine UX:

- **No system Python found, or found but < 3.10**: prints exactly which
  candidates (`python3`, `python`, `py -3` on Windows) were tried and what
  each one resolved to (or why it didn't), then a concrete fix command.
  Does **not** attempt to install Python itself -- out of scope per #222,
  same reasoning #170 gave for not bundling Chromium (real footgun to get
  subtly wrong, and versioned independently of clickcast).
- **`pip install` fails** (no network access to PyPI, a corporate proxy
  blocking it, etc.): reports the exact `pip install` spec that failed and
  the manual equivalent command, rather than leaving a half-provisioned
  venv silently in place.
- **`pip install` "succeeds" but no `clickcast` entry point appears** in the
  venv (would indicate a broken clickcast release itself): reported as its
  own explicit error pointing at the issue tracker, not just "command
  failed."

`CLICKCAST_NPM_PYTHON` (an env var, not a public/documented user-facing
knob) forces which Python candidate `postinstall` probes -- set to an empty
string to force "no Python found," or to a fake/old interpreter to force
"too old." This exists purely so both failure paths can be exercised
deterministically in tests/CI without needing to actually uninstall Python
from the test machine; see "Verification performed" below.

### No Chromium, no second ffmpeg

Same reasoning as Homebrew/apt ([#170]): Chromium is a ~180MB download
versioned independently of clickcast, and clickcast already bundles ffmpeg
via `imageio[ffmpeg]`. `postinstall` never touches either. The npm shim
deliberately does not try to detect or pre-empt the underlying Python CLI's
own missing-engine self-heal prompt ([#216]) -- it just forwards stdio
transparently (`stdio: "inherit"`, never captured/buffered), so that prompt
fires exactly as it would from a real `pip install clickcast` and the user
can answer it interactively through `npx clickcast-mcp` / `npx clickcast`
the same as through a native install.

### Windows

`bin/*.js` are plain Node (not shell scripts), so they run unmodified on
Windows via npm's own `.cmd`/`.ps1` shims for `bin` entries. `postinstall.js`
picks Windows-appropriate venv paths (`Scripts\python.exe`,
`Scripts\clickcast.exe`) and probes `py -3` as a Python candidate ahead of
`python3`/`python` (the standard Windows Python launcher convention). This
was reviewed by hand against Node's documented cross-platform `spawnSync`
behavior; it was **not** tested on a real Windows machine in this sandbox
(Linux-only) -- flagged explicitly in "Verification performed" below as the
one meaningfully unverified platform claim in this PR.

## Supply-chain note

A `postinstall` script that shells out to `pip install` is exactly the kind
of behavior `npm audit` / corporate security scanners flag (issue #222
calls this out explicitly). There's nothing hidden: both packages' own
READMEs ([`npm/clickcast-mcp/README.md`](../../npm/clickcast-mcp/README.md),
[`npm/clickcast/README.md`](../../npm/clickcast/README.md)) document the
mechanism plainly, in a "What `npm install` actually does" section, so it's
inspectable *before* running `npm install` -- and the code itself
(`postinstall.js` + `provision.js`) is short, dependency-free plain Node,
not obfuscated or pulled from a third-party install helper. The PyPI
version installed is pinned to each npm package's own exact `version` field
(no `>=`, no `^`) -- never resolved against a floating range.

## Verification performed for this PR

npm/Node were available in this PR's sandbox (unlike Homebrew for #215),
so this was verified further than "looks right on paper" -- against the
**real** system Python and the **real, already-published** PyPI
`clickcast==<version>` (not a mock):

- `npm pack` both `npm/clickcast-mcp/` and `npm/clickcast/` and confirmed
  the tarball's `Tarball Contents` includes a real, non-empty
  `vendor/provision.js` (proving the `prepack` vendor-sync step works, and
  that the published package is self-contained -- not dependent on the
  sibling `shared/` directory existing on the install machine).
- `npm install --prefix <throwaway dir> ./clickcast-mcp-<version>.tgz` and
  the same for `clickcast-<version>.tgz` -- both ran `postinstall`, created
  a real `.venv` under the installed package's own directory (not touching
  this sandbox's own dev install of clickcast from source), and
  `pip install`ed the real, already-published `clickcast==<version>` (and
  `clickcast[mcp]==<version>` for the MCP package) from PyPI.
  `.venv/bin/clickcast --version` printed the correct pinned version, and
  (for `clickcast-mcp`) `.venv/bin/python3 -c "import mcp"` confirmed the
  `mcp` extra actually installed.
- `node bin/clickcast.js --version` (run against the freshly-provisioned
  venv) printed the wrapped Python package's version correctly, proving
  argv-forwarding and exit-code passthrough.
- `node bin/clickcast-mcp.js --help` printed `clickcast mcp`'s own
  `--help` output (proving the bin script prepends `mcp` and forwards
  trailing flags correctly), and a real MCP `initialize` JSON-RPC request
  piped into `bin/clickcast-mcp.js` over stdin got back a correct
  `initialize` response from the real MCP server on stdout -- confirming
  stdio passes through faithfully rather than being captured or buffered
  (the same property the interactive missing-engine prompt from #216
  depends on).
- Re-ran `postinstall.js` against an already-provisioned venv and confirmed
  it's idempotent ("reusing existing venv," short-circuits `python -m venv`,
  still re-runs `pip install` to pick up a version bump).
- **Python-missing / too-old failure path**, forced via
  `CLICKCAST_NPM_PYTHON`:
  - `CLICKCAST_NPM_PYTHON="" npm install ...` (simulates zero Python
    candidates found) failed the `npm install` itself (exit code 1) with
    the "could not find a system Python >= 3.10" message and a concrete fix
    command -- not a stack trace, not a silently broken `bin/` shim.
  - `CLICKCAST_NPM_PYTHON=/nonexistent/python3 npm install ...` (a
    plausible real-world case: a stale PATH entry) failed the same way,
    reporting exactly which candidate was checked and why it didn't work.
  - `CLICKCAST_NPM_PYTHON="python3 <fake-3.8-shim>.py" npm install ...`
    (a fake interpreter reporting `3.8`) failed with "Found Python 3.8,
    which is too old" plus the same fix command -- confirming the
    version-floor check itself, not just the not-found path.
- `actionlint` (downloaded for this PR; not preinstalled in the sandbox)
  against `.github/workflows/npm-release.yml`: clean.

**Not verified in this sandbox** (all require the actual bootstrap below,
or infrastructure this sandbox doesn't have):

- A real `npm publish` / registry install (`npx -y clickcast-mcp` against
  the actual npm registry) -- no `NPM_TOKEN`, no real publish was performed
  per this issue's constraints (repo-owner-only step, see below).
- `npm-release.yml`'s actual publish-triggered run -- it needs a real
  `release: published` event and the `NPM_TOKEN` secret to observe running
  for real; only the YAML shape and the `has_token` gating logic were
  reviewed/lint-checked, mirroring exactly how #215 left
  `apt-release.yml`'s `publish-apt-repo` job unverified live (real GPG key,
  real release event) while still verifying everything before that gate.
- A real Windows machine (see "Windows" above) -- this sandbox is Linux
  only; the Windows code paths were reviewed by hand, not executed.
- The interactive Chromium missing-engine self-heal prompt specifically
  (#216) firing *through* the npm shim end-to-end -- confirmed indirectly
  (stdio passthrough works for a real MCP JSON-RPC exchange, which has the
  same interactivity requirement), but forcing that exact prompt requires a
  clean environment with Chromium genuinely absent, which this sandbox's
  existing clickcast dev install doesn't cleanly provide without disturbing
  it.

## One-time bootstrap (repo owner only)

Nothing below can be done by an agent working in this repo -- it requires
real npm account access and generating a real automation token, the same
kind of manual, owner-only step issue #206/#209 established a precedent for
(and exactly what #215 did for `HOMEBREW_TAP_TOKEN`/`APT_SIGNING_KEY`).

1. **Verify both package names are still unclaimed** (issue #222 notes
   `clickcast` was unclaimed as of filing; `clickcast-mcp` needs the same
   check re-done before publishing, since names can be claimed by anyone in
   the meantime):

   ```bash
   npm view clickcast
   npm view clickcast-mcp
   ```

   Both should return `npm error 404 'clickcast[-mcp]@*' is not in this
   registry` (unclaimed). Confirmed exactly this for both names during this
   PR (2026-09-04) -- re-check again at bootstrap time, since names can be
   claimed by anyone between now and then. If either now exists, this
   bootstrap needs a different package name (e.g. an `@alexkay/` scope) --
   update `npm/*/package.json`'s `name` field and this doc accordingly
   before proceeding.

2. **Ensure npm account access.** Log in (or confirm existing access) to
   the `~alexkay` npm account: https://www.npmjs.com/~alexkay. If it
   doesn't exist yet, create it at https://www.npmjs.com/signup. Enable 2FA
   if not already on (npm requires 2FA or an automation token with
   sufficient scope for publishing as of recent npm policy).

3. **Generate an automation token.** On
   https://www.npmjs.com/settings/alexkay/tokens (or `npm token create`
   locally, once logged in as `~alexkay`), create a token of type
   **Automation** (works in CI without triggering 2FA prompts, and can be
   scoped to publish-only). Granular access tokens (scoped to exactly
   `clickcast` and `clickcast-mcp`, "Read and write" package permission)
   are preferred over a classic token if your npm account has that option
   available.

4. **Add it as a secret on the `clickcast` repo:** Settings -> Secrets and
   variables -> Actions -> New repository secret, name `NPM_TOKEN`, value
   the token from step 3.

5. **First publish must be manual, once, per package** (npm requires the
   *first* version of a new package name to be published via `npm publish`
   by an authenticated human/CI run with publish rights -- there's no
   "reserve the name" step separate from actually publishing something).
   From a machine with the token from step 3 available:

   ```bash
   cd npm/clickcast-mcp && npm publish --access public
   cd ../clickcast && npm publish --access public
   ```

   (`--access public` is required the first time for an unscoped package
   under some npm account configurations; harmless to pass every time.)
   After this, `npm-release.yml` (below) handles every subsequent version
   bump automatically on release.

6. **Verify the next release picks it up.** Cut a release per
   [`RELEASING.md`](../../RELEASING.md) as usual, with the npm package
   `version` fields bumped to match the new PyPI version (see
   `npm-release.yml`'s own version-sync step, or bump them by hand in the
   same PR that bumps `pyproject.toml`). Once `release.yml`'s `gh-release`
   job publishes the GitHub release, `.github/workflows/npm-release.yml`
   fires automatically and publishes both packages.

7. **Smoke-test the real registry install:**

   ```bash
   npx -y clickcast-mcp --help
   npm install -g clickcast
   clickcast --version
   clickcast install --with-deps chromium
   ```

[#170]: https://github.com/AlexKay28/clickcast/issues/170
[#216]: https://github.com/AlexKay28/clickcast/issues/216
[#222]: https://github.com/AlexKay28/clickcast/issues/222
