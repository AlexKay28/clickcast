from __future__ import annotations

import pytest

from clickcast.core.session import Session

pytestmark = pytest.mark.unit

# `_parse_viewport` was removed in the #96 refactor — parsing lives in
# `clickcast.core.viewport.Viewport.parse` now, covered by
# `tests/test_viewport.py`. The session-side behaviour is exercised in
# `test_context_kwargs_*` below.


def test_page_property_raises_when_closed() -> None:
    sess = Session()
    with pytest.raises(RuntimeError, match="not open"):
        _ = sess.page


def test_context_kwargs_viewport_string() -> None:
    sess = Session(viewport="640x480")
    assert sess._context_kwargs() == {"viewport": {"width": 640, "height": 480}}


def test_context_kwargs_dark_and_lang_and_headers() -> None:
    sess = Session(
        dark=True,
        lang="fr-FR",
        extra_http_headers={"X-Foo": "bar"},
    )
    kwargs = sess._context_kwargs()
    assert kwargs == {
        "color_scheme": "dark",
        "locale": "fr-FR",
        "extra_http_headers": {"X-Foo": "bar"},
    }


def test_context_kwargs_proxy_string_and_dict() -> None:
    assert Session(proxy="http://proxy.example:8080")._context_kwargs() == {
        "proxy": {"server": "http://proxy.example:8080"}
    }
    assert Session(proxy={"server": "http://p", "username": "u"})._context_kwargs() == {
        "proxy": {"server": "http://p", "username": "u"}
    }


@pytest.mark.integration
class TestSessionIntegration:
    """Requires `playwright install chromium`."""

    pytestmark = pytest.mark.integration

    async def test_open_screenshot_close(self) -> None:
        async with Session(viewport=(400, 300)) as sess:
            await sess.goto("data:text/html,<h1>hi</h1>", wait="load")
            png = await sess.screenshot()
        assert png.startswith(b"\x89PNG\r\n\x1a\n"), "expected PNG magic bytes"
        assert len(png) > 100

    async def test_wait_numeric_and_selector(self) -> None:
        async with Session(viewport=(400, 300)) as sess:
            await sess.goto(
                "data:text/html,<div id=x>hi</div>",
                wait="#x",
            )
            await sess.goto("data:text/html,<p>y</p>", wait=0.05)

    async def test_teardown_on_exception(self) -> None:
        with pytest.raises(ValueError, match="boom"):
            async with Session(viewport=(320, 240)) as sess:
                await sess.goto("data:text/html,<p>hi</p>", wait="load")
                raise ValueError("boom")
