# clickcast

`clickcast` drives a browser through a website (Playwright under the hood)
and hands back a watchable GIF/video reel plus a structured JSON "sidecar"
of feedback for AI agents: per-step selectors, timings, page state, error
codes, and a graph of the app's pages.

This is the npm distribution of the CLI -- see the main project at
[github.com/AlexKay28/clickcast](https://github.com/AlexKay28/clickcast) for
the full README, command reference, and sidecar schema.

```bash
npx clickcast --version
npx clickcast install --with-deps chromium   # first time only, ~180MB
npx clickcast doctor
npx clickcast auto https://example.com --for-humans --emit-events --out tour.gif
```

If you'll be running clickcast repeatedly, install it once instead of using
`npx` every time:

```bash
npm install -g clickcast
clickcast --version
```

For clickcast's MCP server specifically (`clickcast mcp`, for AI agent
client config), see the sibling
[`clickcast-mcp`](https://www.npmjs.com/package/clickcast-mcp) package
instead -- narrower surface, purpose-built for `npx -y clickcast-mcp` in an
MCP client config.

## What `npm install` actually does

`clickcast` (the npm package) is a thin Node wrapper around the real Python
package ([`clickcast`](https://pypi.org/project/clickcast/) on PyPI) --
there's no way to ship clickcast's runtime (Playwright, Pillow,
`imageio[ffmpeg]`) as pure JS. On `npm install`, a `postinstall` script
([`postinstall.js`](postinstall.js), provisioning logic in
`vendor/provision.js` -- a vendored copy of the shared
[`npm/shared/provision.js`](https://github.com/AlexKay28/clickcast/blob/main/npm/shared/provision.js),
baked into the published tarball at publish time):

1. Looks for a system Python **>= 3.10** already on your machine (`python3`
   / `python` / `py -3` on Windows). If none is found (or it's too old), the
   install **fails loudly** with a specific fix command -- it never leaves
   you with a silently broken `clickcast` command. This package does **not**
   install Python itself.
2. Creates an **isolated venv** at `node_modules/clickcast/.venv` -- never
   your global/system Python site-packages. This is the same isolation
   principle `pipx` uses: it never fights an existing `pip install
   clickcast` you might already have on the same machine.
3. Runs `pip install clickcast==<exact-version>` into that venv -- an exact
   pin (no floating range), matching this npm package's own `version`
   field. See "Supply-chain note" below.

`bin/clickcast.js` then execs that venv's `clickcast` entry point directly,
forwarding argv, stdio, and exit code transparently -- including any
interactive prompt (e.g. `clickcast auto`/`run`/`shot`/`elements`/`mcp`
detecting a missing browser engine and offering to install it, #216's
self-heal UX), which the shim never buffers or swallows.

Chromium itself (~180MB, versioned independently of clickcast) is **not**
bundled by this package or by `pip install clickcast`:

```bash
npx clickcast install --with-deps chromium
```

## Supply-chain note

A `postinstall` script that shells out to `pip install` is exactly the kind
of behavior `npm audit` / corporate security scanners flag. There is nothing
hidden here: [`postinstall.js`](postinstall.js) and `vendor/provision.js`
(present once the package is installed; source at
[`npm/shared/provision.js`](https://github.com/AlexKay28/clickcast/blob/main/npm/shared/provision.js))
are the entire mechanism, in plain readable Node with zero npm dependencies
of their own -- inspect them before running `npm install`. The PyPI package
installed is pinned to this npm package's exact `version` (no `>=`, no `^`),
and installed into an isolated venv, not your system Python.

## Double-install awareness

If you already have `pip install clickcast` globally **and**
`npm install -g clickcast`, you end up with two independent installs of
clickcast -- same as any language's package-manager overlap (e.g. a Python
dev with both `pip install black` and an npm `black` wrapper). This isn't a
bug to solve; each stays in its own isolated environment.

Full design rationale for this package (and `clickcast-mcp`):
[`docs/packaging/npm.md`](https://github.com/AlexKay28/clickcast/blob/main/docs/packaging/npm.md)
in the main repo.

## License

MIT, same as [clickcast](https://github.com/AlexKay28/clickcast) itself.
