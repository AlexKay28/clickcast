"""Tests for the Session narrow-seam methods added in #98.

These tests use ``MagicMock`` for the underlying ``page`` so they run without
Playwright — the narrow methods are thin proxies whose job is to keep
``playwright.*`` imports out of the rest of the codebase.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from clickcast.core.session import Session


def _mock_session() -> Session:
    """Build a Session without opening Playwright, then swap its ``_page``
    for a ``MagicMock`` we can assert against."""
    sess = Session()
    mock_page = MagicMock()
    sess._page = mock_page  # type: ignore[assignment]
    return sess


class TestLocator:
    def test_delegates_to_page_locator(self) -> None:
        sess = _mock_session()
        result = sess.locator(".btn")
        sess._page.locator.assert_called_once_with(".btn")  # type: ignore[union-attr]
        assert result is sess._page.locator.return_value  # type: ignore[union-attr]


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_no_args_passes_script_only(self) -> None:
        sess = _mock_session()
        sess._page.evaluate = AsyncMock(return_value=42)  # type: ignore[union-attr]
        result = await sess.evaluate("() => 42")
        assert result == 42
        sess._page.evaluate.assert_awaited_once_with("() => 42")  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_single_arg_passed_directly(self) -> None:
        sess = _mock_session()
        sess._page.evaluate = AsyncMock(return_value=None)  # type: ignore[union-attr]
        await sess.evaluate("([a, b]) => a+b", [1, 2])
        sess._page.evaluate.assert_awaited_once_with("([a, b]) => a+b", [1, 2])  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_multiple_args_packed_into_list(self) -> None:
        sess = _mock_session()
        sess._page.evaluate = AsyncMock(return_value=None)  # type: ignore[union-attr]
        await sess.evaluate("(args) => args[0] + args[1]", 1, 2)
        sess._page.evaluate.assert_awaited_once_with(  # type: ignore[union-attr]
            "(args) => args[0] + args[1]", [1, 2]
        )


class TestKeyboardAndMouse:
    @pytest.mark.asyncio
    async def test_press_key_delegates_to_keyboard(self) -> None:
        sess = _mock_session()
        sess._page.keyboard.press = AsyncMock()  # type: ignore[union-attr]
        await sess.press_key("Escape")
        sess._page.keyboard.press.assert_awaited_once_with("Escape")  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_wheel_delegates_to_mouse(self) -> None:
        sess = _mock_session()
        sess._page.mouse.wheel = AsyncMock()  # type: ignore[union-attr]
        await sess.wheel(0, 400)
        sess._page.mouse.wheel.assert_awaited_once_with(0, 400)  # type: ignore[union-attr]


class TestTitleAndUrl:
    @pytest.mark.asyncio
    async def test_title_returns_page_title(self) -> None:
        sess = _mock_session()
        sess._page.title = AsyncMock(return_value="Home")  # type: ignore[union-attr]
        assert await sess.title() == "Home"

    @pytest.mark.asyncio
    async def test_title_swallows_exception(self) -> None:
        sess = _mock_session()
        sess._page.title = AsyncMock(side_effect=RuntimeError("mid-navigation"))  # type: ignore[union-attr]
        assert await sess.title() == ""

    def test_url_now_returns_current_page_url(self) -> None:
        sess = _mock_session()
        sess._page.url = "https://example.com/x"  # type: ignore[union-attr]
        assert sess.url_now == "https://example.com/x"


class TestEventSubscriptions:
    def test_on_delegates_to_page_on(self) -> None:
        sess = _mock_session()
        cb = MagicMock()
        sess.on("console", cb)
        sess._page.on.assert_called_once_with("console", cb)  # type: ignore[union-attr]

    def test_off_delegates_to_page_remove_listener(self) -> None:
        sess = _mock_session()
        cb = MagicMock()
        sess.off("pageerror", cb)
        sess._page.remove_listener.assert_called_once_with("pageerror", cb)  # type: ignore[union-attr]


class TestReExports:
    """Callers should be able to import Locator + PlaywrightTimeoutError
    from ``clickcast.core.session`` without touching Playwright directly.
    Guards the acceptance criterion from #98."""

    def test_locator_reexported(self) -> None:
        from clickcast.core.session import Locator

        assert Locator is not None

    def test_playwright_timeout_error_reexported(self) -> None:
        from clickcast.core.session import PlaywrightTimeoutError

        assert issubclass(PlaywrightTimeoutError, Exception)
