"""Time-budget assertions for `_do_auto` (`--max-duration` + `--click-timeout`)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from clickcast.cli import _do_auto
from tests._stubs import FakeSession, make_element, make_result


class TestClickTimeoutPropagates:
    """`--click-timeout` must land on the ClickStep we hand to `execute`."""

    @pytest.mark.asyncio
    async def test_click_step_has_timeout_ms(self, stub_environment: FakeSession) -> None:
        fake_sess = stub_environment
        seen_steps: list[Any] = []

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            elif step.__class__.__name__ == "ClickStep":
                seen_steps.append(step)
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("Btn")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=5000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert seen_steps, "no ClickStep was executed"
        for s in seen_steps:
            assert s.timeout_ms == 5000, f"expected timeout_ms=5000, got {s.timeout_ms}"


class TestMaxDurationCap:
    """`--max-duration` breaks BFS early when the wall clock exceeds it."""

    @pytest.mark.asyncio
    async def test_deadline_stops_bfs_between_pages(
        self, stub_environment: FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                # Simulate slow goto so wall-time actually elapses
                await asyncio.sleep(0.06)
            elif cls == "ClickStep":
                click_counter["n"] += 1
                # Every click nav's to a new URL to keep the queue full
                fake_sess.page.url = f"https://x.com/page-{click_counter['n']}"
            return make_result()

        elements = [make_element(f"e{i}") for i in range(20)]
        with (
            caplog.at_level(logging.WARNING, logger="clickcast.auto"),
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch("clickcast.auto.discover", AsyncMock(return_value=elements)),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=50,
                max_pages=100,  # generous — deadline should stop us first
                dwell=0.0,
                initial_wait=0.0,
                max_duration=0.1,  # 100ms — trips after ~1-2 pages
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert len(gotos) < 20, (
            f"deadline should have stopped BFS before all pages; got {len(gotos)} gotos"
        )
        messages = [r.message for r in caplog.records]
        assert any("max-duration" in m for m in messages), (
            "missing max-duration warning. Got:\n" + "\n".join(messages)
        )

    @pytest.mark.asyncio
    async def test_max_duration_zero_dies(self, stub_environment: FakeSession) -> None:
        from typer import Exit

        with (
            patch("clickcast.auto.execute", AsyncMock(return_value=make_result())),
            patch("clickcast.auto.discover", AsyncMock(return_value=[make_element("x")])),
            pytest.raises(Exit),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=0.0,
                click_timeout_ms=2000,
                traversal="bfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
