"""Live-progress logging assertions for `_do_auto`.

Regression: a 9-minute react.dev demo was completely silent with `--verbose`,
so from the terminal it looked hung. #59 traced the fix: per-click, per-nav,
per-go_back, per-page-summary INFO lines. These tests lock the trace in place.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clickcast.cli import _do_auto
from tests._stubs import FakeSession, make_element, make_result


class TestAutoProgressLogging:
    @pytest.mark.asyncio
    async def test_emits_per_click_info_lines(
        self, stub_environment: FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression: silent-during-work symptom. Every click must produce an
        INFO log line so the user can see progress in a long run."""
        fake_sess = stub_environment
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                click_counter["n"] += 1
            return make_result()

        elements = [make_element(f"Btn{i}") for i in range(5)]
        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch("clickcast.auto.discover", AsyncMock(return_value=elements)),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )

        click_lines = [
            r for r in caplog.records if "· attempt " in r.message and "clicked" in r.message
        ]
        assert len(click_lines) == 5, (
            f"expected 5 per-attempt INFO lines, got {len(click_lines)}: "
            f"{[r.message for r in click_lines]}"
        )

    @pytest.mark.asyncio
    async def test_emits_nav_and_go_back_lines(
        self, stub_environment: FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Every same-origin navigation must log both the nav and the go_back
        (this is where the demo used to silently spend 5-30 seconds per click)."""
        fake_sess = stub_environment
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/inner"
            return make_result()

        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("Nav"), make_element("Other")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=3,
                max_pages=1,  # single page so we exercise go_back path
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )

        messages = [r.message for r in caplog.records]
        assert any("nav to https://x.com/inner" in m for m in messages), (
            "missing nav-detected INFO line. Got:\n" + "\n".join(messages)
        )
        assert any("going back" in m for m in messages), (
            "missing go_back INFO line. Got:\n" + "\n".join(messages)
        )

    @pytest.mark.asyncio
    async def test_emits_page_summary_line(
        self, stub_environment: FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Each page ends with a `page N/M · done in Ns (X clicks used, ...)` line —
        the summary that used to be missing between pages."""
        fake_sess = stub_environment

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            return make_result()

        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch("clickcast.auto.discover", AsyncMock(return_value=[make_element("Btn")])),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )

        messages = [r.message for r in caplog.records]
        assert any("done in" in m and "clicks used" in m for m in messages), (
            "missing page-summary line. Got:\n" + "\n".join(messages)
        )

    @pytest.mark.asyncio
    async def test_attempt_counter_advances_even_on_failed_clicks(
        self, stub_environment: FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression: with the old `click N/max` log using `clicked + 1`,
        the number froze at the last successful click's index whenever
        clicks failed. Users saw `click 8/15` repeated 20 times. New log
        uses an `attempt N` counter that always advances."""
        fake_sess = stub_environment
        click_counter = {"n": 0}

        def _failing_result() -> Any:
            r = MagicMock()
            r.ok = False
            r.status = "failed"
            r.error = "not clickable"
            r.cursor_xy = None
            return r

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # Clicks 1 and 3 succeed; click 2 fails.
                if click_counter["n"] == 2:
                    return _failing_result()
                return make_result()
            return make_result()

        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"E{i}") for i in range(3)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=3,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )

        # 3 attempt lines, one per click try, with distinct attempt numbers.
        attempt_lines = [
            r.message
            for r in caplog.records
            if "· attempt " in r.message and "clicked" in r.message
        ]
        assert len(attempt_lines) == 3, "expected 3 attempt lines, got:\n" + "\n".join(
            attempt_lines
        )
        # Each `attempt N` value should be unique and ascending.
        numbers = [int(m.split("attempt ")[1].split(" ")[0]) for m in attempt_lines]
        assert numbers == [1, 2, 3], f"attempt counter should always advance, got {numbers}"

    @pytest.mark.asyncio
    async def test_breaks_after_consecutive_click_failures(
        self, stub_environment: FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A page whose discovered elements all fail used to burn the entire
        pool (20+ elements x ~30s Playwright default). Now we break after 3
        consecutive failures."""
        fake_sess = stub_environment
        click_counter = {"n": 0}

        def _failing_result() -> Any:
            r = MagicMock()
            r.ok = False
            r.status = "failed"
            r.error = "not clickable"
            r.cursor_xy = None
            return r

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                return _failing_result()  # everything fails
            return make_result()

        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"E{i}") for i in range(20)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=15,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )

        # 20 elements in the pool, but we should stop after 3 failures.
        assert click_counter["n"] == 3, (
            f"expected 3 click attempts before bailing, got {click_counter['n']}"
        )
        # And the reason should be in the log.
        messages = [r.message for r in caplog.records]
        assert any("consecutive click failures" in m for m in messages), (
            "missing consecutive-failures bail-out log line. Got:\n" + "\n".join(messages)
        )
