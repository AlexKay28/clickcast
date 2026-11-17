# Security Policy

`clickcast` drives a real browser with your credentials, so its own defaults
matter. This policy covers the Python package, the `clickcast` / `clickcast-mcp`
npm wrappers, and the bundled media toolchain.

## Supported versions

The latest released minor line gets security fixes. Older lines are unsupported.

| Version | Supported |
| ------- | --------- |
| 0.5.x   | Yes       |
| < 0.5   | No        |

Current release: **0.5.0** (see `pyproject.toml`, `CHANGELOG.md`). Fixes ship
as a new patch/minor; there are no backports to older minors.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting:
[Report a vulnerability](https://github.com/AlexKay28/clickcast/security/advisories/new)
(repo **Security** tab → **Report a vulnerability**). That opens a private
GitHub Security Advisory visible only to the maintainer.

Maintainer note: private vulnerability reporting must be enabled once in
**Settings → Code security → Private vulnerability reporting**. It cannot be
toggled from this repository's files or API, so if the link above is not
available, open a minimal public issue asking for a private channel — without
exploit details.

This is a single-maintainer project. Expect an acknowledgement within **7
days**; a fix or mitigation timeline follows once the report is triaged. Good
reports include the affected version (`clickcast --version`), the command or
scenario that triggers it, and the impact you believe it has.

## In scope

- **npm `postinstall` provisioning** — `npm/shared/provision.js` (vendored into
  each package's `vendor/`). At install time it creates an isolated venv under
  `node_modules/<pkg>/.venv` and runs `pip install clickcast==<pinned
  version>` from PyPI. Report command/argument injection, venv-path traversal,
  or ways a hostile package/registry response could escape that contract.
- **Bundled ffmpeg** — delivered by the `imageio[ffmpeg]` dependency
  (`imageio_ffmpeg.get_ffmpeg_exe()`), not downloaded from a project server.
  Report unsafe handling of encoder inputs/outputs or of the resolved binary
  path.
- **Credential handling** — `--header` / `-H` and `--header-host`
  (`src/clickcast/core/session.py`). `--header` values must not contain
  control characters; with `--header-host` they are injected by a route
  interceptor only for matching hostnames (exact, or dotted subdomain when the
  target has an internal dot), so tokens do not reach CDN/analytics origins.
  Leaks across that boundary are in scope, as is header redaction in
  invocation history.
- **`--insecure`** — sets Playwright's `ignore_https_errors`. Report cases
  where it weakens more than the intended connection, or is applied where the
  user did not ask for it.
- **Redaction guarantees** — `src/clickcast/feedback/redact.py` and
  `clickcast report-bug` (redaction is **on** by default). URLs, selectors,
  visible text, errors, and diagnostics must be sanitized before they reach a
  report or shared sidecar; `--redact-pattern` / `--strip-query-strings` scrub
  auth-bypass tokens. Report any value that survives redaction and leaks a
  secret, hostname, or query token.

## Out of scope

- The behaviour, content, or vulnerabilities of the site you point clickcast
  at. That is the target's security, not this tool's.
- User-authored scenario content: YAML/JSON scenarios, selectors, notes, and
  free-form text you pass in (`--note`, step text, annotation labels). If you
  put a secret there, that is your input — though a redaction bug that fails to
  scrub it is still in scope.

## No telemetry

`clickcast` sends no analytics, crash reports, or usage data to the project or
any third party. Browser traffic is whatever the target site and its
subresources receive; the only other outbound call is `pip install` during npm
provisioning. Verify it with:

```bash
grep -rInE "^(import|from) (requests|httpx|urllib3|aiohttp)" src/clickcast
grep -rInE "sentry|posthog|telemetry|analytics\.|mixpanel|amplitude|datadog" src
```

Both return nothing (the only `urllib` imports are
`urllib.parse` for URL handling). The sidecar/reel pipeline is local, and the
optional `serving.py` dev server binds `127.0.0.1` only. ffmpeg is the static
binary shipped inside `imageio-ffmpeg`, so encoding does not phone home either.
