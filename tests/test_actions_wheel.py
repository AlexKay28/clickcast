"""Tests for `WheelStep` — schema + executor behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import TypeAdapter, ValidationError

from clickcast.core.actions import Step, WheelStep, execute
from clickcast.core.session import Session

FIXTURE_HTML = """<!DOCTYPE html>
<html><body style="margin:0">
  <div id="tall" style="height:4000px"></div>
  <div id="widget" style="width:200px;height:200px;overflow:auto;position:fixed;top:300px;left:300px;">
    <div style="height:1000px" id="widget-inner">inner</div>
  </div>
  <div id="log" style="position:fixed;bottom:0">idle</div>
  <script>
    window._wheelLog = [];
    // Record every wheel event on window (fires for all deliveries).
    window.addEventListener('wheel', (e) => {
      window._wheelLog.push({
        dy: Math.round(e.deltaY),
        dx: Math.round(e.deltaX),
        target: e.target && e.target.id ? e.target.id : (e.target ? e.target.tagName : ''),
      });
    }, { passive: true });
    document.getElementById('widget').addEventListener('wheel', (e) => {
      document.getElementById('log').textContent =
        'widget:' + Math.round(e.deltaY) + ',' + Math.round(e.deltaX);
    });
  </script>
</body></html>
"""


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(600, 400)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


class TestWheelStepModel:
    def test_defaults(self) -> None:
        s = WheelStep(dy=120)
        assert s.action == "wheel"
        assert s.dy == 120
        assert s.dx == 0
        assert s.selector is None

    def test_dy_required(self) -> None:
        with pytest.raises(ValidationError):
            WheelStep()  # type: ignore[call-arg]

    def test_extras_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            WheelStep(dy=1, nonsense=True)  # type: ignore[call-arg]

    def test_discriminated_union_parses(self) -> None:
        adapter = TypeAdapter(Step)
        parsed = adapter.validate_python({"action": "wheel", "dy": 200, "dx": 10, "selector": "#x"})
        assert isinstance(parsed, WheelStep)
        assert parsed.dy == 200
        assert parsed.dx == 10
        assert parsed.selector == "#x"


@pytest.mark.integration
class TestWheelIntegration:
    async def test_wheel_without_selector_dispatches_event(self, loaded_session: Session) -> None:
        # Hover the tall div so the mouse has a valid target for the wheel
        # event; Playwright's mouse.wheel dispatches at the current position.
        await loaded_session.page.locator("#tall").hover()
        r = await execute(WheelStep(dy=500), loaded_session)
        assert r.ok
        assert r.action == "wheel"
        events = await loaded_session.page.evaluate("() => window._wheelLog")
        assert events
        assert events[-1]["dy"] == 500

    async def test_wheel_with_selector_targets_widget(self, loaded_session: Session) -> None:
        r = await execute(WheelStep(dy=120, selector="#widget"), loaded_session)
        assert r.ok
        assert r.selector == "#widget"
        log = await loaded_session.page.locator("#log").text_content()
        assert log is not None
        assert log.startswith("widget:")

    async def test_wheel_with_missing_selector_fails(self, loaded_session: Session) -> None:
        r = await execute(WheelStep(dy=100, selector="#nope"), loaded_session)
        assert not r.ok
        assert r.status == "failed"
        assert r.selector == "#nope"

    async def test_wheel_missing_selector_optional_is_skipped(
        self, loaded_session: Session
    ) -> None:
        r = await execute(WheelStep(dy=100, selector="#nope", optional=True), loaded_session)
        assert r.ok
        assert r.status == "skipped"

    async def test_wheel_dx_dispatches_horizontal(self, loaded_session: Session) -> None:
        r = await execute(WheelStep(dy=10, dx=25, selector="#widget"), loaded_session)
        assert r.ok
        log = await loaded_session.page.locator("#log").text_content()
        assert log == "widget:10,25"
