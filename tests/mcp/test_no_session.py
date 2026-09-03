"""Usage errors that don't require a live browser: calling an action tool
before ``start_session``, or ``close_session`` with nothing open. These
short-circuit before ``Session`` is ever constructed, so no Chromium install
is needed — kept in the ``unit`` marker group (see ``tests/mcp/test_tool_schema.py``
for the other browser-free MCP test)."""

from __future__ import annotations

import pytest

from tests.mcp.conftest import open_client, result_json

pytestmark = pytest.mark.unit


async def test_click_before_start_session_errors() -> None:
    async with open_client() as client:
        result = await client.call_tool("click", {"selector": "#btn-3d"})
    assert result.isError is True
    payload = result_json(result)
    assert payload["ok"] is False
    assert payload["error_code"] == "other"
    assert "start_session" in payload["error"]


async def test_goto_before_start_session_errors() -> None:
    async with open_client() as client:
        result = await client.call_tool("goto", {"url": "http://example.invalid"})
    assert result.isError is True
    assert result_json(result)["error_code"] == "other"


async def test_screenshot_before_start_session_errors() -> None:
    async with open_client() as client:
        result = await client.call_tool("screenshot", {})
    assert result.isError is True
    assert result_json(result)["error_code"] == "other"


async def test_close_session_with_nothing_open_errors() -> None:
    async with open_client() as client:
        result = await client.call_tool("close_session", {})
    assert result.isError is True
    payload = result_json(result)
    assert payload["error_code"] == "other"
    assert "no active session" in payload["error"]


async def test_start_session_with_missing_engine_returns_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent can't answer an interactive install prompt, so `start_session`
    must never try one — the missing-engine case degrades to the same
    `_safe_tool` error-payload channel every other MCP failure uses, with
    the fix command in the message text (see EngineNotInstalledError)."""
    monkeypatch.setattr("clickcast.core.session.find_installed_engine", lambda engine: None)
    async with open_client() as client:
        result = await client.call_tool("start_session", {})
    assert result.isError is True
    payload = result_json(result)
    assert payload["error_code"] == "other"
    assert "chromium isn't installed" in payload["error"]
    assert "clickcast install --with-deps chromium" in payload["error"]
