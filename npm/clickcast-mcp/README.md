# clickcast-mcp

An npx-first [MCP](https://modelcontextprotocol.io) server for
[clickcast](https://github.com/AlexKay28/clickcast) -- gives AI agents a live
browser session (goto/click/type/scroll/...) with per-action structured
feedback (annotated frame, `page_state`, grid coordinates, `error_code`),
instead of clickcast's usual whole-tour GIF + JSON sidecar output.

This package exists because the MCP ecosystem's dominant install pattern is
`npx <package>` -- every MCP client config example (Claude Desktop, Claude
Code, Cursor) is written that way, and clickcast is otherwise a Python-only
`pip install`. `clickcast-mcp` closes that gap with a one-line config:

```json
{
  "mcpServers": {
    "clickcast": {
      "command": "npx",
      "args": ["-y", "clickcast-mcp"]
    }
  }
}
```

That's the entire client-side setup. No `pip install`, no venv activation,
no PATH wrangling.

## What `npm install` actually does

`clickcast-mcp` is a thin Node wrapper around the real Python package
([`clickcast`](https://pypi.org/project/clickcast/) on PyPI) -- there's no
way to ship clickcast's runtime (Playwright, Pillow, `imageio[ffmpeg]`) as
pure JS. On `npm install`, a `postinstall` script
([`postinstall.js`](postinstall.js), provisioning logic in
`vendor/provision.js` -- a vendored copy of the shared
[`npm/shared/provision.js`](https://github.com/AlexKay28/clickcast/blob/main/npm/shared/provision.js),
baked into the published tarball at publish time):

1. Looks for a system Python **>= 3.10** already on your machine (`python3`
   / `python` / `py -3` on Windows). If none is found (or it's too old), the
   install **fails loudly** with a specific fix command -- it never leaves
   you with a silently broken `clickcast-mcp` command. This package does
   **not** install Python itself.
2. Creates an **isolated venv** at `node_modules/clickcast-mcp/.venv` --
   never your global/system Python site-packages. This is the same
   isolation principle `pipx` uses: it never fights an existing
   `pip install clickcast` you might already have on the same machine.
3. Runs `pip install 'clickcast[mcp]==<exact-version>'` into that venv --
   an exact pin (no floating range), matching this npm package's own
   `version` field. See "Supply-chain note" below.

`bin/clickcast-mcp.js` then execs that venv's `clickcast mcp` entry point
directly, forwarding argv, stdio, and exit code transparently -- including
Chromium's interactive missing-engine self-heal prompt (the underlying
Python CLI's own #216 behavior), which the shim never buffers or swallows.

Chromium itself (~180MB, versioned independently of clickcast) is **not**
bundled by this package or by `pip install clickcast[mcp]`. First run:

```bash
npx -y clickcast-mcp
# or, once installed:
node_modules/.bin/clickcast-mcp
```

will prompt to install it interactively in a real terminal (same self-heal
UX every other clickcast entry point has), or run it explicitly first via
the underlying Python install:

```bash
node_modules/clickcast-mcp/.venv/bin/clickcast install --with-deps chromium
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

If you already have `pip install 'clickcast[mcp]'` globally **and**
`npm install -g clickcast-mcp`, you end up with two independent installs of
clickcast -- same as any language's package-manager overlap (e.g. a Python
dev with both `pip install black` and an npm `black` wrapper). This isn't a
bug to solve; each stays in its own isolated environment.

## Alternative: `pip install` directly

If you're already in a Python project, skip this package entirely:

```bash
pip install 'clickcast[mcp]'
clickcast install --with-deps chromium
```

```json
{
  "mcpServers": {
    "clickcast": {
      "command": "clickcast",
      "args": ["mcp"]
    }
  }
}
```

See [`docs/mcp.md`](https://github.com/AlexKay28/clickcast/blob/main/docs/mcp.md)
and [`docs/mcp-tool-schema.md`](https://github.com/AlexKay28/clickcast/blob/main/docs/mcp-tool-schema.md)
in the main repo for the full per-tool contract, and
[`docs/packaging/npm.md`](https://github.com/AlexKay28/clickcast/blob/main/docs/packaging/npm.md)
for this package's full design rationale.

## Flags

Any flag `clickcast mcp --help` lists forwards straight through as a default
for `start_session`:

```json
{
  "mcpServers": {
    "clickcast": {
      "command": "npx",
      "args": ["-y", "clickcast-mcp", "--grid", "--viewport", "1440x900"]
    }
  }
}
```

## License

MIT, same as [clickcast](https://github.com/AlexKay28/clickcast) itself.
