# Releasing `clickcast`

Publishing is driven by [`.github/workflows/release.yml`](.github/workflows/release.yml). The
workflow fires on any tag matching `v*` and does:

1. **build** — verifies the tag matches `pyproject.toml` version, then
   `python -m build` produces `dist/*.tar.gz` + `dist/*.whl`.
2. **test-release** — uploads to TestPyPI via
   [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC —
   no stored API tokens).
3. **smoke-test** — spins up a fresh venv on 8 combos (Linux/macOS × Python
   3.10/3.11/3.12/3.13), installs from TestPyPI, and asserts
   `clickcast --version` matches the tag.
4. **release** (only for non-prerelease tags) — uploads to real PyPI.
5. **gh-release** (only for non-prerelease tags) — `gh release create` with
   the built artifacts attached and auto-generated notes.

## Pre-flight (once, before the first tag)

Both accounts, both projects, both trusted publishers.

### 1. PyPI + TestPyPI accounts
Existing or new — this repo's owner is the account that needs to configure
Trusted Publishing.

### 2. Configure Trusted Publishing on PyPI
Go to https://pypi.org/manage/account/publishing/ and add a **pending publisher**
(the project doesn't exist yet — the first successful upload creates it):

- PyPI project name: `clickcast`
- Owner: `AlexKay28`
- Repository name: `clickcast`
- Workflow filename: `release.yml`
- Environment: `release`

### 3. Configure Trusted Publishing on TestPyPI
Same page at https://test.pypi.org/manage/account/publishing/, same fields,
except:

- Environment: `test-release`

### 4. Create the two GitHub environments
Settings → Environments → New environment:

- `test-release` — no protection rules
- `release` — optional: require a reviewer, restrict to `main`

Trusted Publishing is scoped by the environment name, so these must match
the ones configured on (Test)PyPI in steps 2/3.

### 5. Verify the repo path in `release.yml`
The workflow assumes `AlexKay28/clickcast`. If the repo moves, update the URL
comments and TP config on both PyPI sides.

## Cutting a release

1. **Bump `version` in `pyproject.toml`** — PEP 440 format:
   - `0.1.0` → final release
   - `0.1.0rc1` → release candidate (TestPyPI only)
   - `0.1.0a1` / `0.1.0b1` → alpha / beta (TestPyPI only)
   - `0.1.0.dev1` → dev release (TestPyPI only)
2. `git commit -am "chore: v0.1.0"`
3. `git tag -a v0.1.0 -m "v0.1.0"` — annotated tag; the leading `v` matters.
4. `git push origin main --tags`
5. Watch → https://github.com/AlexKay28/clickcast/actions

The workflow refuses to run if the tag and `pyproject.toml` version disagree,
so mismatch errors surface early rather than as a bad PyPI upload.

## Fixing a broken tag

**Before it was pushed:**
```
git tag -d v0.1.0
```

**After it was pushed but before PyPI upload succeeded:**
```
git tag -d v0.1.0
git push origin :refs/tags/v0.1.0
# fix the issue, re-tag, re-push
```

**After it was uploaded to PyPI:** PyPI does **not** allow re-uploading the
same version. Bump to `0.1.1` (or `0.1.0.post1` if the code is unchanged and
this is a metadata-only fix) and cut a new release. Optionally
[yank](https://pypi.org/help/#yanked) the broken version on PyPI's UI.

## Local dry run

`python -m build` from the repo root reproduces the workflow's build step. The
`build` package is in the `dev` extra:

```
pip install -e ".[dev]"
python -m build
ls dist/
```

Unpack the wheel and confirm the shipped `.ttf` + `schema/v1.json` are inside:

```
python -c "
import zipfile, glob
w = glob.glob('dist/*.whl')[0]
with zipfile.ZipFile(w) as z:
    print('\n'.join(n for n in z.namelist() if n.endswith(('.ttf', '.json'))))
"
```
