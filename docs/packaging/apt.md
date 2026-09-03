# apt (Debian / Ubuntu)

`clickcast` ships a `.deb` package so Debian/Ubuntu users can install it with
`dpkg`/`apt` instead of setting up a Python venv first. This is a
**self-hosted apt repository**, not a Launchpad PPA and not a Debian archive
submission -- both of those are explicitly out of scope for this PR per
[#170]'s "Suggested rollout" (Launchpad PPA is a v0.3.x+1mo follow-up,
Debian archive is v0.5.x+).

## What's live today vs. what needs bootstrapping

| Install path | Status |
| --- | --- |
| Build locally: `bash scripts/build_deb.sh <version>` then `dpkg -i dist-deb/clickcast_<version>_amd64.deb` | **Works today** -- no setup required, verified in this PR (see below). |
| Download the `.deb` attached to a GitHub release + `dpkg -i` | **Works today**, unsigned -- `.github/workflows/apt-release.yml` attaches it to every release automatically, with zero manual setup. |
| `apt install clickcast` from a hosted, signed repo (`https://alexkay28.github.io/clickcast/apt`) | **Needs the one-time bootstrap below** -- the signing key and GitHub Pages branch don't exist yet. |

## Packaging design

### A bundled venv under `/opt/clickcast`, not a bare `python3` dependency

Issue #170 raises a real problem: Debian's `python3` alias floats between
releases (bookworm ships 3.11, trixie ships 3.12), so a package that just
`Depends: python3 (>= 3.10)` and calls `pip install clickcast` at postinst
time is fragile -- it depends on whatever's on the target at install time,
which this project doesn't control or test against.

Instead, `scripts/build_deb.sh`:

1. Creates a fresh venv under `/opt/clickcast/venv` and `pip install
   clickcast==<version>` into it -- clickcast's own resolved dependency set
   travels with the package, not whatever happens to be on the target.
2. Symlinks `/usr/bin/clickcast -> /opt/clickcast/venv/bin/clickcast`.
3. **Rewrites every venv console-script's `#!` shebang** from the build-time
   staging path to the real install path (`/opt/clickcast/venv/bin/python3`).
   This is not optional: `python -m venv` bakes the *build-time* absolute
   path into every generated script, so without this fixup `clickcast` would
   fail with "No such file or directory" the moment the `.deb` is installed
   anywhere but the exact build host. This was caught by actually installing
   the built package in a clean container while preparing this PR (see
   "Verification performed" below) -- worth calling out because it's the
   kind of bug a `dpkg-deb --contents` review alone would not catch.

**Known limitation:** the venv's own `bin/python3` is a symlink to the
*system* Python used to build it (`/usr/bin/python3` at build time), not a
copied interpreter binary -- `python -m venv` never copies the interpreter
itself. So `Depends: python3 (>= 3.10), python3-venv` is still declared, and
the package assumes the target has a `/usr/bin/python3` in roughly the same
place the build host did. Building on a current Ubuntu LTS runner
(`ubuntu-latest` in CI) keeps this aligned with the widest realistic set of
Debian/Ubuntu targets for v1. If this turns out to bite in practice, the
follow-up options are: `venv --copies` (copies the interpreter binary,
larger package), a fully static/relocatable build (PyInstaller/shiv, as
issue #170 mentions for Homebrew too), or `dh-virtualenv`.

### No `ffmpeg` in `Depends:`

Same reasoning as the Homebrew formula: `imageio[ffmpeg]` already bundles one
inside the venv. A system `ffmpeg` `Depends:` would install a second copy for
no benefit -- issue #170 flags this explicitly ("pick one to avoid two
ffmpegs on disk").

### No Chromium anywhere in the package

Same reasoning as Homebrew: it's a large (~180MB), independently-versioned
download. `DEBIAN/postinst` prints the same caveat the Homebrew formula's
`caveats` block does, pointing at `clickcast install --with-deps chromium`.

### `amd64` only for v1

Issue #170 asks for `amd64` "at minimum" and `arm64` "if it's not
meaningfully more work." Cross-building an `arm64` `.deb` correctly from an
`amd64` CI runner needs either an actual `arm64` runner/QEMU emulation (slow,
adds real CI time and complexity) or careful `pip install --platform
manylinux_2_17_aarch64 --only-binary=:all:` wheel selection for every
dependency (untested here, and Playwright's own manylinux wheel coverage for
aarch64 would need verifying). That's meaningfully more work than the rest
of this PR, so it's deferred -- tracked as a follow-up, not attempted here.

### Build tool: hand-rolled `debian/`-style control tree + `dpkg-deb`, not `fpm`

`dpkg-deb` is preinstalled on every `ubuntu-latest` GitHub Actions runner (it
ships with `dpkg` itself, a base package) and was already present in this
PR's sandbox with no extra install step. `fpm` requires a Ruby gem install
first, which is an extra moving part on every CI run for no real benefit
here -- the control file and postinst script are simple enough that
generating them directly (via `scripts/apt_package.py`) and calling
`dpkg-deb --build --root-owner-group` is the more reliably buildable option
per issue #170's own framing ("whichever is more reliably buildable in a CI
runner without extra system packages").

## Verification performed for this PR

This was verified further than "looks right on paper":

- **Built the real `.deb`** with `bash scripts/build_deb.sh 0.2.9 amd64` in
  this PR's sandbox, using the actual published `clickcast==0.2.9` from
  PyPI. `dpkg-deb --info` / `--contents` confirmed the control fields,
  symlink, and file layout.
- **Installed it in a throwaway Ubuntu 24.04 Docker container** (`dpkg -i`),
  confirmed `postinst` printed the Chromium caveat, and confirmed `clickcast
  --version` and `clickcast --help` both ran correctly from the installed
  package -- this is what caught the shebang-relocation bug described above.
- **Ran `lintian`** against the built package inside the same container.
  Remaining findings (`dir-or-file-in-opt`, `unstripped-binary-or-object`,
  `wrong-path-for-interpreter`, `package-installs-python-pycache-dir`, and
  similar) are all inherent, *expected* consequences of the "bundle a whole
  venv wholesale" v1 design documented above -- they're the kind of findings
  the actual Debian archive's mentors would object to (which is exactly why
  archive submission is out of scope for this PR / a v0.5.x+ follow-up per
  [#170]), not indications of a broken package for a self-hosted repo. Two
  real, cheap findings *were* fixed during this PR: `synopsis-too-long`
  (control file's one-line summary was too long) and `non-standard-file-perm`
  (pip installs wheel contents group-writable; `scripts/build_deb.sh`
  normalizes permissions before packaging).
- `scripts/apt_package.py`'s pure rendering functions (control file,
  postinst, apt repo `Packages`/`Release` stanzas) have unit tests
  (`tests/test_apt_package.py`), including a syntax check of the generated
  postinst via `sh -n`.

Not verified in this sandbox: the actual signed-repo publish path
(`publish-apt-repo` job in `.github/workflows/apt-release.yml`) -- it needs
a real GPG key and a real `release:` event, neither of which exist yet. The
YAML was validated with `actionlint` (clean) and reviewed by hand.

## One-time bootstrap (repo owner only)

None of this can be done by an agent working in this repo -- it requires
generating and safely storing a real GPG private key and enabling GitHub
Pages, both owner-only actions (same precedent as issue #206/#209's
GitHub Marketplace listing).

1. **Generate a signing key.** On a machine you trust:

   ```bash
   gpg --full-generate-key
   # Kind: RSA and RSA, 4096 bits, key does not expire (or a long expiry you'll rotate)
   # Real name: clickcast release signing key
   # Email: (your address, or a repo-scoped alias)
   ```

   Note the key ID: `gpg --list-secret-keys --keyid-format long`.

2. **Export the private key for CI** (armored, so it survives as a GitHub
   Actions secret string):

   ```bash
   gpg --export-secret-keys --armor <KEYID> > clickcast-signing-key.asc
   ```

   Treat this file as highly sensitive -- anyone with it can sign packages as
   "clickcast." Delete it from disk once it's in the GitHub secret (step 3).

3. **Add it as a secret on the `clickcast` repo:** Settings -> Secrets and
   variables -> Actions -> New repository secret, name `APT_SIGNING_KEY`,
   value the full contents of `clickcast-signing-key.asc`.

4. **Enable GitHub Pages for the `apt-repo` branch.** The
   `publish-apt-repo` job in `.github/workflows/apt-release.yml` pushes a
   full apt repository tree (`dists/`, `pool/`) to a branch named
   `apt-repo` (created automatically on first push once the signing key
   secret exists). Once that branch exists:
   Settings -> Pages -> Source: "Deploy from a branch" -> Branch:
   `apt-repo`, folder `/ (root)`. This publishes the repo at
   `https://alexkay28.github.io/clickcast/`.

5. **Verify the next release picks it up.** Cut a release per
   [`RELEASING.md`](../../RELEASING.md). Once `release.yml`'s `gh-release`
   job publishes it, `.github/workflows/apt-release.yml`'s `build-deb` job
   builds + smoke-tests + attaches the unsigned `.deb` (this always runs),
   and `publish-apt-repo` signs and pushes the full repo tree to
   `apt-repo` (this runs once the secret from step 3 exists).

6. **Smoke-test the real hosted repo:**

   ```bash
   curl -fsSL https://alexkay28.github.io/clickcast/clickcast-archive-keyring.asc \
     | sudo gpg --dearmor -o /usr/share/keyrings/clickcast-archive-keyring.gpg
   echo "deb [signed-by=/usr/share/keyrings/clickcast-archive-keyring.gpg] \
     https://alexkay28.github.io/clickcast/ stable main" \
     | sudo tee /etc/apt/sources.list.d/clickcast.list
   sudo apt update
   sudo apt install clickcast
   clickcast --version
   clickcast install --with-deps chromium
   ```

7. **(Optional, later) Launchpad PPA / Debian archive submission.** Explicitly
   out of scope for this PR -- see [#170]'s rollout plan (v0.3.x+1mo and
   v0.5.x+ respectively).

[#170]: https://github.com/AlexKay28/clickcast/issues/170
