"""Timeout path for `WaitForStep(state='stable')`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from clickcast.core.actions import WaitForStep, execute
from clickcast.core.session import Session

# Element that jitters forever — never settles.
_NEVER_SETTLES_HTML = """<!DOCTYPE html>
<html><head><title>never</title></head>
<body>
  <div id="jumpy"
       style="position:absolute; top:100px; left:100px; width:60px; height:30px; background:#f80;">
  </div>
  <script>
    const el = document.getElementById('jumpy');
    let t = 0;
    function tick() {
      t += 1;
      el.style.left = (100 + (t % 20)) + 'px';
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  </script>
</body></html>
"""


@pytest_asyncio.fixture
async def session() -> AsyncIterator[Session]:
    async with Session(viewport=(400, 300)) as sess:
        sess.page.set_default_timeout(5000)
        yield sess


@pytest.mark.integration
class TestWaitForTimeout:
    async def test_never_settling_element_times_out(self, session: Session) -> None:
        await session.page.set_content(_NEVER_SETTLES_HTML)
        result = await execute(
            WaitForStep(selector="#jumpy", state="stable", quiet_ms=150, timeout=0.5),
            session,
        )
        assert not result.ok
        assert result.status == "failed"
        assert result.selector == "#jumpy"
        assert result.error is not None
        # Error should include the selector and give the user something to debug.
        assert "#jumpy" in result.error
        assert "stable" in result.error
        # A useful diagnostic hint — the recent bbox history in the message.
        assert "bboxes" in result.error.lower() or "bbox" in result.error.lower()

    async def test_missing_selector_times_out_with_state_visible(self, session: Session) -> None:
        await session.page.set_content("<div>no target here</div>")
        result = await execute(
            WaitForStep(
                selector="#does-not-exist",
                state="visible",
                timeout=0.5,
            ),
            session,
        )
        assert not result.ok
        assert result.status == "failed"
        assert result.selector == "#does-not-exist"

    async def test_missing_selector_stable_times_out(self, session: Session) -> None:
        await session.page.set_content("<div>no target</div>")
        result = await execute(
            WaitForStep(
                selector="#missing",
                state="stable",
                quiet_ms=100,
                timeout=0.5,
            ),
            session,
        )
        assert not result.ok
        assert result.status == "failed"
        assert result.error is not None
        assert "#missing" in result.error
