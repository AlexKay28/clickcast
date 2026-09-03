"""The tool list itself — no browser needed, so ``unit``-marked."""

from __future__ import annotations

import pytest

from tests.mcp.conftest import open_client

pytestmark = pytest.mark.unit

_EXPECTED_TOOLS = {
    "start_session",
    "close_session",
    "goto",
    "click",
    "dblclick",
    "hover",
    "type",
    "press",
    "select",
    "scroll",
    "wait",
    "screenshot",
}


async def test_tool_list_matches_schema_doc() -> None:
    """One tool per docs/mcp-tool-schema.md entry — nothing extra, nothing missing."""
    async with open_client() as client:
        tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert names == _EXPECTED_TOOLS


async def test_every_tool_has_a_description() -> None:
    async with open_client() as client:
        tools = await client.list_tools()
    for tool in tools.tools:
        assert tool.description, f"{tool.name} has no description"
