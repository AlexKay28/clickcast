"""``clickcast mcp`` — expose clickcast sessions as an MCP server (#191).

Public surface:

- :func:`create_server` — build a configured ``FastMCP`` instance (used by
  both the CLI entrypoint and tests, which want an in-process client rather
  than a subprocess).
- :func:`serve_stdio` — blocking stdio-transport entrypoint; what
  ``clickcast mcp`` actually runs.

See ``docs/mcp-tool-schema.md`` for the tool contract this module
implements.
"""

from __future__ import annotations

from clickcast.mcp.server import ClickcastSessionState, create_server, serve_stdio

__all__ = ["ClickcastSessionState", "create_server", "serve_stdio"]
