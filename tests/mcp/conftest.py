"""Shared helpers for ``tests/mcp/``.

``open_client()`` connects an in-process :class:`mcp.client.session.ClientSession`
to a fresh clickcast server via ``mcp.shared.memory`` — no subprocess, no
stdio pipes (see #195's "in-process MCP client" ask).

Deliberately NOT a ``pytest.fixture``: ``create_connected_server_and_client_session``
holds an ``anyio`` task group open across its ``async with`` block, and
anyio's cancel-scope tracking requires that block to enter *and* exit inside
the same asyncio Task. pytest-asyncio runs an async-generator fixture's
setup and its teardown as two separate top-level tasks (one at fixture
resolution, one at finalization), so a fixture wrapping this CM raises
``RuntimeError: Attempted to exit cancel scope in a different task than it
was entered in`` at teardown — confirmed empirically before writing this
comment. Calling ``open_client()`` as a plain ``async with`` *inside* each
test body keeps setup and teardown in the test's single task, which works.

Reuses the session-scoped ``fixture_site_url`` fixture from
``tests/conftest.py`` rather than spinning up a second static server.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult

from clickcast.mcp import create_server


@asynccontextmanager
async def open_client(**server_kwargs: object) -> AsyncIterator[ClientSession]:
    """``async with open_client() as client:`` — a fresh server + in-process client."""
    server = create_server(name="clickcast-test", **server_kwargs)  # type: ignore[arg-type]
    async with create_connected_server_and_client_session(server) as client:
        yield client


def result_json(result: CallToolResult) -> dict:
    """Pull the JSON payload out of a tool result's ``TextContent`` block."""
    for block in result.content:
        if block.type == "text":
            return json.loads(block.text)
    raise AssertionError(f"no text content block in result: {result!r}")


def has_image(result: CallToolResult) -> bool:
    return any(block.type == "image" for block in result.content)


@asynccontextmanager
async def live_session(
    *,
    fixture_site_url: str | None = None,
    goto_home: bool = False,
    **start_kwargs: object,
) -> AsyncIterator[ClientSession]:
    """``async with live_session(...) as client:`` — an open client with an
    already-``start_session``'d session, optionally navigated to
    ``fixture_site_url``'s home page. ``close_session`` runs on exit.

    Composed from :func:`open_client` rather than layered as a separate
    pytest fixture for the same reason ``open_client`` isn't a fixture
    either — see this module's docstring.
    """
    async with open_client() as client:
        await client.call_tool("start_session", dict(start_kwargs))
        if goto_home:
            assert fixture_site_url is not None, "goto_home=True requires fixture_site_url"
            await client.call_tool("goto", {"url": fixture_site_url.rstrip("/") + "/"})
        try:
            yield client
        finally:
            await client.call_tool("close_session", {})
