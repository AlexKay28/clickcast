"""`--seed-url` — agent-controllable BFS.

When any seed URL is provided, the tour visits exactly `[start] + seeds`,
in the order given, and does NOT auto-enqueue navigation destinations
discovered during clicks. The `--traversal` flag becomes moot (order is
deterministic).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from clickcast.cli import _do_auto
from tests._stubs import FakeSession, make_element, make_result


class TestSeededTours:
    @pytest.mark.asyncio
    async def test_seed_urls_visited_in_order(self, stub_environment: FakeSession) -> None:
        """Agent seeds 3 URLs; tour visits start + those 3 in the exact order."""
        fake_sess = stub_environment
        gotos: list[str] = []

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch("clickcast.auto.discover", AsyncMock(return_value=[make_element("Btn")])),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=10,
                max_pages=10,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="dfs",  # should be ignored when seeded
                seed_urls=["https://x.com/pricing", "https://x.com/docs", "https://x.com/contact"],
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == [
            "https://x.com/",
            "https://x.com/pricing",
            "https://x.com/docs",
            "https://x.com/contact",
        ], f"seeded order wrong, got {gotos}"

    @pytest.mark.asyncio
    async def test_seeded_tour_ignores_auto_discovered_navs(
        self, stub_environment: FakeSession
    ) -> None:
        """A click that navigates during a seeded tour must NOT get enqueued
        — the caller specified the exact path."""
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # Every click navigates to a "surprise" URL that would have
                # been enqueued in unseeded mode.
                fake_sess.page.url = f"https://x.com/surprise-{click_counter['n']}"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"Btn{i}") for i in range(5)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=10,
                max_pages=10,  # generous
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="dfs",
                seed_urls=["https://x.com/agent-choice"],
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        # Only 2 gotos: initial + seed. Surprise-N destinations must NOT appear.
        assert gotos == ["https://x.com/", "https://x.com/agent-choice"], (
            f"seeded tour surprised us with extra gotos: {gotos}"
        )

    @pytest.mark.asyncio
    async def test_seeds_still_visited_when_click_budget_exhausted(
        self, stub_environment: FakeSession
    ) -> None:
        """Regression from the manual smoke test: with `--seed-url` set and a
        tight `--max-steps`, the tour used to exit after page 1 (loop guard
        `clicks_remaining > 0`) — silently dropping the seeds the caller had
        committed to. Now seeded tours continue even at zero budget; remaining
        seeds get goto + scroll (no clicks) so they still appear in the reel."""
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                click_counter["n"] += 1
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"E{i}") for i in range(3)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=3,  # tight — page 1 alone will exhaust it
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="dfs",
                seed_urls=[
                    "https://x.com/promised-1",
                    "https://x.com/promised-2",
                ],
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == [
            "https://x.com/",
            "https://x.com/promised-1",
            "https://x.com/promised-2",
        ], f"seeded pages must be visited even at budget=0; got {gotos}"
        # Sanity: budget was 3, page 1 used it all.
        assert click_counter["n"] == 3

    @pytest.mark.asyncio
    async def test_empty_seed_urls_falls_back_to_normal(
        self, stub_environment: FakeSession
    ) -> None:
        """`seed_urls=None` or empty must behave exactly like the default (no seed).
        Auto-discovery of nav destinations should work as before."""
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] <= 2:
                    fake_sess.page.url = f"https://x.com/nav-{click_counter['n']}"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"Btn{i}") for i in range(5)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=30,  # generous — must be enough for all 3 pages
                max_pages=3,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",
                seed_urls=[],  # empty
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        # BFS + no seeds → initial + auto-discovered navs (nav-1 first via FIFO).
        assert gotos == [
            "https://x.com/",
            "https://x.com/nav-1",
            "https://x.com/nav-2",
        ], f"empty seed_urls should behave like default, got {gotos}"
