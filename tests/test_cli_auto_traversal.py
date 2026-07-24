"""DFS vs BFS URL queue ordering."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from clickcast.cli import _do_auto
from tests._stubs import FakeSession, make_element, make_result


class TestTraversalOrdering:
    """DFS pops from the right (LIFO); BFS pops from the left (FIFO)."""

    @pytest.mark.asyncio
    async def test_dfs_visits_deepest_first(self, stub_environment: FakeSession) -> None:
        """From start, discover 3 nav destinations. DFS visits the LAST-
        discovered one first (LIFO), giving depth-first ordering."""
        fake_sess = stub_environment
        gotos: list[str] = []
        counters: dict[str, int] = {}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                here = fake_sess.page.url
                n = counters.get(here, 0) + 1
                counters[here] = n
                # Only the start page has nav targets. First 3 clicks navigate.
                if here == "https://x.com/" and n <= 3:
                    fake_sess.page.url = f"https://x.com/dest-{n}"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(3)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=20,
                max_pages=4,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="dfs",
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == [
            "https://x.com/",
            "https://x.com/dest-3",
            "https://x.com/dest-2",
            "https://x.com/dest-1",
        ], f"DFS ordering wrong, got {gotos}"

    @pytest.mark.asyncio
    async def test_bfs_visits_earliest_first(self, stub_environment: FakeSession) -> None:
        """Same setup as DFS test, but with `traversal='bfs'`. BFS visits
        the FIRST-discovered destination first (FIFO)."""
        fake_sess = stub_environment
        gotos: list[str] = []
        counters: dict[str, int] = {}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                here = fake_sess.page.url
                n = counters.get(here, 0) + 1
                counters[here] = n
                if here == "https://x.com/" and n <= 3:
                    fake_sess.page.url = f"https://x.com/dest-{n}"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(3)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=20,
                max_pages=4,
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
        assert gotos == [
            "https://x.com/",
            "https://x.com/dest-1",
            "https://x.com/dest-2",
            "https://x.com/dest-3",
        ], f"BFS ordering wrong, got {gotos}"
