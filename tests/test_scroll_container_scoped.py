"""Tests for container-scoped `ScrollStep` — the new `selector` field."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import TypeAdapter, ValidationError

from clickcast.core.actions import ScrollStep, Step, execute
from clickcast.core.session import Session

FIXTURE_HTML = """<!DOCTYPE html>
<html><body style="margin:0">
  <div id="container" style="width:200px;height:200px;overflow:auto;">
    <div style="width:2000px;height:2000px" id="big">big</div>
  </div>
  <div id="tall" style="height:3000px"></div>
</body></html>
"""


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(600, 400)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


class TestScrollStepModelExtension:
    def test_selector_and_dx_default_none_zero(self) -> None:
        s = ScrollStep(by=100)
        assert s.selector is None
        assert s.dx == 0

    def test_scoped_scroll_parses(self) -> None:
        adapter = TypeAdapter(Step)
        parsed = adapter.validate_python(
            {"action": "scroll", "by": 400, "selector": "#container", "dx": 20}
        )
        assert isinstance(parsed, ScrollStep)
        assert parsed.selector == "#container"
        assert parsed.dx == 20
        assert parsed.by == 400

    def test_extras_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ScrollStep(by=1, nonsense=True)  # type: ignore[call-arg]


@pytest.mark.integration
class TestScopedScrollExecution:
    async def test_container_scoped_scroll_by_moves_container_not_window(
        self, loaded_session: Session
    ) -> None:
        r = await execute(ScrollStep(by=150, selector="#container"), loaded_session)
        assert r.ok
        assert r.action == "scroll"
        assert r.selector == "#container"
        top = await loaded_session.page.evaluate(
            "() => document.getElementById('container').scrollTop"
        )
        window_y = await loaded_session.page.evaluate("() => window.scrollY")
        assert top == 150
        # Window itself should NOT have moved.
        assert window_y == 0

    async def test_container_scoped_scroll_supports_dx(self, loaded_session: Session) -> None:
        r = await execute(ScrollStep(by=100, dx=50, selector="#container"), loaded_session)
        assert r.ok
        top = await loaded_session.page.evaluate(
            "() => document.getElementById('container').scrollTop"
        )
        left = await loaded_session.page.evaluate(
            "() => document.getElementById('container').scrollLeft"
        )
        assert top == 100
        assert left == 50

    async def test_window_scroll_still_default_without_selector(
        self, loaded_session: Session
    ) -> None:
        # No selector → wheel path (window / whatever's under the mouse).
        # Key guarantee: our container is NOT programmatically scrolled.
        r = await execute(ScrollStep(by=200), loaded_session)
        assert r.ok
        top = await loaded_session.page.evaluate(
            "() => document.getElementById('container').scrollTop"
        )
        assert top == 0

    async def test_container_scroll_missing_selector_fails(self, loaded_session: Session) -> None:
        r = await execute(ScrollStep(by=100, selector="#nope"), loaded_session)
        assert not r.ok
        assert r.status == "failed"
        assert r.selector == "#nope"

    async def test_scroll_to_still_works_with_selector_ignored(
        self, loaded_session: Session
    ) -> None:
        # When `to` is set, the container `selector` is ignored (scroll-into-view path).
        r = await execute(ScrollStep(to="#big", selector="#container"), loaded_session)
        assert r.ok
        assert r.selector == "#big"
