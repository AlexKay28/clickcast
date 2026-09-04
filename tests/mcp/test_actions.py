"""Live-session tests: an in-process MCP client against a real Chromium
session driving ``tests/fixtures/site/`` (served by the session-scoped
``fixture_site_url`` fixture from ``tests/conftest.py``).

Marked ``integration`` — same marker every other real-browser test in this
suite uses (see ``pyproject.toml``'s ``markers``). Every test opens its own
client + session via ``open_client``/``live_session`` (see
``tests/mcp/conftest.py`` for why these are plain async context managers
rather than pytest fixtures).
"""

from __future__ import annotations

import json

import pytest

from clickcast.core.actions import ActionResult
from tests.mcp.conftest import has_image, live_session, open_client, result_json

pytestmark = pytest.mark.integration


class TestSessionLifecycle:
    async def test_start_session_returns_engine_and_viewport(self) -> None:
        async with open_client() as client:
            result = await client.call_tool("start_session", {"viewport": "800x600"})
            assert result.isError is not True
            payload = result_json(result)
            assert payload["ok"] is True
            assert payload["engine"] == "chromium"
            assert payload["viewport"] == [800, 600]
            assert payload["headful"] is False
            await client.call_tool("close_session", {})

    async def test_double_start_session_errors(self) -> None:
        async with open_client() as client:
            await client.call_tool("start_session", {})
            result = await client.call_tool("start_session", {})
            assert result.isError is True
            assert result_json(result)["error_code"] == "other"
            await client.call_tool("close_session", {})

    async def test_close_session_without_transcript_does_not_write(self) -> None:
        async with open_client() as client:
            await client.call_tool("start_session", {})
            result = await client.call_tool("close_session", {})
        assert result.isError is not True
        payload = result_json(result)
        assert payload["closed"] is True
        assert payload["transcript_path"] is None

    async def test_close_session_writes_transcript(self, fixture_site_url: str, tmp_path) -> None:
        out = tmp_path / "transcript.json"
        async with open_client() as client:
            await client.call_tool("start_session", {})
            await client.call_tool("goto", {"url": fixture_site_url + "/"})
            result = await client.call_tool("close_session", {"save_transcript": str(out)})
        assert result.isError is not True
        payload = result_json(result)
        assert payload["transcript_path"] == str(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["schema_version"] == 4
        assert len(data["steps"]) == 1
        assert data["steps"][0]["action"] == "goto"
        assert data["media"]["format"] == "none"

    async def test_action_after_close_session_errors(self) -> None:
        async with open_client() as client:
            await client.call_tool("start_session", {})
            await client.call_tool("close_session", {})
            result = await client.call_tool("click", {"selector": "#btn-3d"})
        assert result.isError is True
        assert result_json(result)["error_code"] == "other"


class TestActionsHappyPath:
    async def test_goto_returns_frame_and_page_state(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("goto", {"url": fixture_site_url + "/form.html"})
        assert result.isError is not True
        assert has_image(result)
        payload = result_json(result)
        assert payload["ok"] is True
        assert payload["status"] == "ok"
        assert payload["action"] == "goto"
        assert payload["page_state"]["title"] == "Form — Clickcast Fixture"
        assert payload["page_state"]["url_after"].endswith("/form.html")

    async def test_click_returns_cursor_xy_and_frame(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("click", {"selector": "#btn-3d"})
        assert result.isError is not True
        assert has_image(result)
        payload = result_json(result)
        assert payload["ok"] is True
        assert payload["selector"] == "#btn-3d"
        assert payload["cursor_xy"] is not None
        assert len(payload["cursor_xy"]) == 2

    async def test_dblclick(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("dblclick", {"selector": "#btn-compare"})
        assert result.isError is not True
        assert result_json(result)["action"] == "dblclick"

    async def test_click_wait_param_blocks_before_responding(self, fixture_site_url: str) -> None:
        """#226: `click`'s `wait` arg (a duration here) blocks inside the
        tool call, so the response reflects a settled page — same purpose
        as `goto`'s `wait`, for a click that triggers client-side nav."""
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("click", {"selector": "#btn-3d", "wait": 0.2})
        assert result.isError is not True
        payload = result_json(result)
        assert payload["ok"] is True
        assert payload["duration_ms"] >= 190

    async def test_hover(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("hover", {"selector": "#btn-reset"})
        assert result.isError is not True
        assert result_json(result)["action"] == "hover"

    async def test_type_into_and_select_on_form(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            await client.call_tool("goto", {"url": fixture_site_url + "/form.html"})

            result = await client.call_tool("type", {"into": "#name", "text": "Ada"})
            assert result.isError is not True
            payload = result_json(result)
            assert payload["action"] == "type"
            assert payload["selector"] == "#name"

            result = await client.call_tool("select", {"into": "#country", "value": "fr"})
            assert result.isError is not True
            assert result_json(result)["action"] == "select"

    async def test_press_key(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            await client.call_tool("goto", {"url": fixture_site_url + "/form.html"})
            await client.call_tool("type", {"into": "#name", "text": "Ada"})
            result = await client.call_tool("press", {"key": "Enter", "selector": "#name"})
        assert result.isError is not True
        assert result_json(result)["action"] == "press"

    async def test_scroll_to_selector(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("scroll", {"to": "footer"})
        assert result.isError is not True
        assert result_json(result)["action"] == "scroll"

    async def test_scroll_by_pixels(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("scroll", {"by": 200})
        assert result.isError is not True
        assert result_json(result)["action"] == "scroll"

    async def test_wait_seconds(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("wait", {"wait": 0.1})
        assert result.isError is not True
        assert result_json(result)["action"] == "wait"

    async def test_screenshot_returns_frame(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("screenshot", {})
        assert result.isError is not True
        assert has_image(result)
        assert result_json(result)["action"] == "screenshot"


class TestErrorModes:
    async def test_click_missing_selector_returns_timeout_error_code(
        self, fixture_site_url: str
    ) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool(
                "click", {"selector": "#does-not-exist", "timeout_ms": 300}
            )
        assert result.isError is True
        payload = result_json(result)
        assert payload["ok"] is False
        assert payload["status"] == "failed"
        assert payload["error_code"] == "timeout"
        assert payload["error"]

    async def test_locator_missing_error_code_propagates(
        self, fixture_site_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real 0-match Playwright click surfaces as a *TimeoutError* (see
        ``core/actions.py::_classify_error`` — ``isinstance(exc,
        PlaywrightTimeoutError)`` wins classification before any text
        matching runs), so ``locator_missing`` isn't reachable end-to-end
        through real browser timing — ``tests/test_actions.py`` tests that
        classification the same way, with a direct synthetic exception.
        Mirror that here: monkeypatch ``execute`` to return a
        ``locator_missing`` :class:`ActionResult` and assert the MCP layer
        round-trips whatever ``error_code`` ``core.actions.execute``
        produces, byte for byte.
        """
        import clickcast.mcp.server as server_mod

        async def _fake_execute(step, session, *, step_index=None):
            return ActionResult(
                ok=False,
                status="failed",
                action=step.action,
                selector=getattr(step, "selector", None),
                error="locator resolved to 0 elements",
                duration_ms=1.0,
                error_code="locator_missing",
            )

        monkeypatch.setattr(server_mod, "execute", _fake_execute)

        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("click", {"selector": "#nope"})
        assert result.isError is True
        payload = result_json(result)
        assert payload["ok"] is False
        assert payload["error_code"] == "locator_missing"

    async def test_scroll_without_to_or_by_returns_other_error_code(
        self, fixture_site_url: str
    ) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("scroll", {})
        assert result.isError is True
        assert result_json(result)["error_code"] == "other"
