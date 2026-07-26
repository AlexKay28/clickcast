"""Tests for `WaitForStep(state='stable')` — the bounding-box quiescence poll."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from clickcast.core.actions import WaitForStep, execute
from clickcast.core.session import Session

# HTML that jitters `#target` for a short window, then stops.
# The `data-settle-ms` attribute lets each test set its own settling deadline.
_SETTLING_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><title>settling</title></head>
<body>
  <div id="target"
       style="position:absolute; top:100px; left:100px; width:80px; height:40px; background:#0af;">
    T
  </div>
  <script>
    const el = document.getElementById('target');
    const settleAt = performance.now() + {settle_ms};
    let x = 100;
    function tick() {
      const now = performance.now();
      if (now < settleAt) {
        x = 100 + Math.round(20 * Math.sin(now / 30));
        el.style.left = x + 'px';
        requestAnimationFrame(tick);
      } else {
        el.style.left = '150px';  // final resting position
      }
    }
    requestAnimationFrame(tick);
  </script>
</body></html>
"""


def _html(settle_ms: int) -> str:
    return _SETTLING_HTML_TEMPLATE.replace("{settle_ms}", str(settle_ms))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[Session]:
    async with Session(viewport=(400, 300)) as sess:
        sess.page.set_default_timeout(5000)
        yield sess


@pytest.mark.integration
class TestWaitForStable:
    async def test_returns_after_element_settles(self, session: Session) -> None:
        # Element jitters for 300ms, then holds still.
        await session.page.set_content(_html(300))
        start = time.monotonic()
        result = await execute(
            WaitForStep(selector="#target", state="stable", quiet_ms=200, timeout=5.0),
            session,
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        assert result.ok, f"unexpected failure: {result.error}"
        assert result.status == "ok"
        assert result.action == "wait_for"
        assert result.selector == "#target"
        # Must never return before `quiet_ms` — that's the whole contract.
        assert elapsed_ms >= 200, f"returned in {elapsed_ms:.0f}ms (< quiet_ms=200)"
        # And should not wait the full timeout: it settled after ~300ms so we
        # expect to be back well before 5s. Give a generous ceiling for CI.
        assert elapsed_ms < 3000, f"took {elapsed_ms:.0f}ms — should have settled sooner"

    async def test_static_element_returns_after_quiet_ms(self, session: Session) -> None:
        # No animation at all — bbox is immediately stable.
        await session.page.set_content(
            '<div id="s" style="width:50px;height:50px;background:#0af"></div>'
        )
        start = time.monotonic()
        result = await execute(
            WaitForStep(selector="#s", state="stable", quiet_ms=150, timeout=5.0),
            session,
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        assert result.ok
        assert elapsed_ms >= 150

    async def test_visible_state_delegates_to_playwright(self, session: Session) -> None:
        await session.page.set_content(
            '<div id="v" style="width:20px;height:20px;background:red"></div>'
        )
        result = await execute(
            WaitForStep(selector="#v", state="visible", timeout=2.0),
            session,
        )
        assert result.ok
        assert result.action == "wait_for"
        assert result.selector == "#v"
