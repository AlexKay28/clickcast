# clickcast MCP server

`clickcast mcp` starts an MCP ([Model Context Protocol](https://modelcontextprotocol.io))
server on stdio that wraps clickcast's browser-session engine — the same
`core/session.py` / `core/actions.py` every other clickcast command uses.
Instead of recording a whole tour and reading back a GIF + sidecar, an agent
drives one action at a time (`goto`/`click`/`type`/`scroll`/...) and gets
back clickcast's per-action payload immediately: an annotated PNG frame,
structured `page_state`, grid coordinates (when enabled), and an enumerated
`error_code` on failure — the same vocabulary the batch sidecar uses.

This is clickcast's answer to [`playwright-mcp`](https://github.com/microsoft/playwright-mcp)'s
live interactivity, without giving up clickcast's differentiators.

For the full per-tool contract (args, return shape, error modes), see
[`mcp-tool-schema.md`](mcp-tool-schema.md). This page is about *using* the
server, not its internals.

## When to reach for `mcp` vs. batch `auto`/`run`

| | `clickcast mcp` | `clickcast auto` / `run` |
|---|---|---|
| Shape | Live, one action per call | One-shot, whole tour |
| Best for | Exploring a page and reacting to what you see (debugging a flow, filling a form whose next field depends on the last, "click around until X appears") | A repeatable artifact — CI regression gate, docs screenshot, release-notes reel |
| Output | Per-call frame + JSON, optionally an accumulated sidecar-shaped transcript on `close_session` | A GIF/MP4 + a `.json` sidecar, every time |
| Session lifetime | Held open across many tool calls (`start_session` → N actions → `close_session`) | One session per invocation, torn down at the end |

If you already know the exact steps, `run` a YAML scenario — it's
deterministic and cheaper to reason about in CI. Reach for `mcp` when the
NEXT step depends on what the PREVIOUS one revealed.

## Install

**Recommended — `npx` (no local Python setup):** the
[`clickcast-mcp`](https://www.npmjs.com/package/clickcast-mcp) npm package
is an npx-first entry point built specifically for this — every MCP client
config example (Claude Desktop, Claude Code, Cursor) is already written as
`npx -y <package>`, so this needs no separate install step at all (see
`args` below). Its `postinstall` provisions clickcast into an isolated venv
under its own install directory automatically, the first time it's used —
see [`docs/packaging/npm.md`](packaging/npm.md) for how.

**Alternative — `pip`, if you're already in a Python project:**

```bash
pip install 'clickcast[mcp]'
```

The base `pip install clickcast` does **not** pull in the `mcp` package —
it's an optional extra so agents that never touch MCP don't pay for the
dependency. `clickcast mcp` prints a clear "install the `mcp` extra" error
(not an `ImportError` traceback) if you run it without the extra installed.

Either way, you also need a Chromium install, same as every other clickcast
command:

```bash
clickcast install --with-deps chromium
```

## Client config

### Claude Code

```bash
claude mcp add clickcast -- npx -y clickcast-mcp
```

Or, in `.mcp.json`:

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

Using a `pip install 'clickcast[mcp]'` install instead:
`claude mcp add clickcast -- clickcast mcp`, or
`{"command": "clickcast", "args": ["mcp"]}`.

### Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

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

Restart Claude Desktop after editing. Using a `pip install` instead:
`{"command": "clickcast", "args": ["mcp"]}` — if `clickcast` isn't on the
`PATH` Claude Desktop launches with (common on macOS — GUI apps don't
inherit your shell's `PATH`), point `command` at the absolute path from
`which clickcast` instead.

### Passing default browser options

Any flag `clickcast mcp --help` lists becomes the default for
`start_session` when the connecting agent's call doesn't override it —
append it after `clickcast-mcp` (npx) or `mcp` (pip):

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

Now every action tool's returned frame carries the pixel-grid overlay by
default, without the agent having to pass `grid: true` on every
`start_session` call.

## Using it

Once configured, an agent (or you, via any MCP client) calls:

1. `start_session` — opens one live browser session. Optional args mirror
   `clickcast`'s `--engine`/`--viewport`/`--device`/`--headful`/`--lang`/
   `--dark`/`--grid*` flags.
2. Any of `goto`, `click`, `dblclick`, `hover`, `type`, `press`, `select`,
   `scroll`, `wait`, `screenshot` — as many times as needed, reacting to
   each call's returned frame + `page_state` + `error_code` before choosing
   the next one.
3. `close_session` — closes the browser. Pass `save_transcript: "path.json"`
   to flush the accumulated sidecar-shaped transcript of every action taken
   (same schema-v3 shape `auto`/`run` write, minus `media` — no reel is
   encoded from a live session).

v1 is single-session, single-process: one `clickcast mcp` process holds at
most one live session at a time (see #191's "Out of scope"). Calling
`start_session` while one is already open, or calling an action tool before
`start_session`, both return `error_code: "other"` with a clear message —
not a crash.

## Verifying the config actually starts the server

```bash
clickcast mcp --help
```

should print the flag list. To confirm a client config is wired correctly
end-to-end without opening a full chat client, run the server directly and
watch it accept stdio:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0"}}}' | clickcast mcp
```

A well-formed JSON-RPC response on stdout means the server is reachable —
Ctrl-C to stop it (it otherwise waits for more input, exactly like a real
client's stdio session would supply).
