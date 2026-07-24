"""Unit tests for the BFS URL queue behavior of `_do_auto`.

These stub out Playwright / recorder / encoder (via `stub_environment` in
`tests/conftest.py`) so we can assert the pure orchestration logic — which
URLs get visited, in what order, and how `--max-pages` interacts with
same-origin dedup.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from clickcast.cli import _do_auto
from tests._stubs import FakeSession, make_element, make_result


class TestBfsQueue:
    @pytest.mark.asyncio
    async def test_max_pages_1_visits_only_start(self, stub_environment: FakeSession) -> None:
        fake_sess = stub_environment

        gotos: list[str] = []

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("Home")]),
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
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == ["https://x.com/"]

    @pytest.mark.asyncio
    async def test_same_origin_click_navigations_are_enqueued(
        self, stub_environment: FakeSession
    ) -> None:
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        # First click on start → navigate to /about; then start-page discovery
        # is done. On the /about page the same-shape click is a no-op so the
        # click loop finishes without a further nav.
        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/about"
                return make_result()
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("About"), make_element("Docs")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=10,  # global budget; must be enough to also visit /about
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        # Start page + the discovered /about destination.
        assert gotos == ["https://x.com/", "https://x.com/about"]

    @pytest.mark.asyncio
    async def test_cross_origin_navigation_not_enqueued(
        self, stub_environment: FakeSession
    ) -> None:
        fake_sess = stub_environment
        gotos: list[str] = []
        first_click = {"done": False}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep" and not first_click["done"]:
                first_click["done"] = True
                fake_sess.page.url = "https://other.example.com/land"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("External")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == ["https://x.com/"], "cross-origin destination should not have been visited"

    @pytest.mark.asyncio
    async def test_visited_dedup_prevents_re_goto(self, stub_environment: FakeSession) -> None:
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        # Every click navigates back to a page we've already visited (/about);
        # dedup must prevent the second goto.
        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # click #1 on start → navigate to /about
                # click #2 on /about → navigate back to start (already visited)
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/about"
                elif click_counter["n"] == 2:
                    fake_sess.page.url = "https://x.com/"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("Nav")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=10,  # global budget; enough to also visit /about
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == ["https://x.com/", "https://x.com/about"], (
            "start page should not have been visited twice"
        )

    @pytest.mark.asyncio
    async def test_go_back_lets_bfs_enqueue_multiple_destinations(
        self, stub_environment: FakeSession
    ) -> None:
        """Regression: `break`-on-first-nav starved BFS. First click was a
        nav-to-already-visited (like clicking the site logo) → BFS enqueued
        nothing new and exited after 1 page. With go_back-and-continue, we
        return to the start page and click the next element, which navigates
        to a *new* URL that gets enqueued."""
        fake_sess = stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # Click #1: logo → same URL (already visited)
                # Click #2: nav to /about → new URL (should be enqueued)
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/"  # same as start
                elif click_counter["n"] == 2:
                    fake_sess.page.url = "https://x.com/about"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("Logo"), make_element("About")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,  # generous — we want to see both clicks happen
                max_pages=3,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        # go_back should have been called after click #1 (logo → same URL is
        # still a "nav" event for us since URL was pushed onto stack).
        assert len(fake_sess.page.go_back_history) >= 1, (
            "go_back never called — BFS is still exiting on first nav"
        )
        # Both start page + /about must have been goto'd; the whole point of
        # the fix is that /about got enqueued after the useless first click.
        assert gotos == ["https://x.com/", "https://x.com/about"], (
            f"expected [/, /about], got {gotos}"
        )

    @pytest.mark.asyncio
    async def test_cross_origin_nav_bails_no_go_back(self, stub_environment: FakeSession) -> None:
        """Cross-origin nav is a stronger signal that we shouldn't drive on
        the site (privacy, TOS, whatever). Bail without go_back."""
        fake_sess = stub_environment
        clicked = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                clicked["n"] += 1
                if clicked["n"] == 1:
                    fake_sess.page.url = "https://other.example.com/"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("External"), make_element("Other")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,
                max_pages=3,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert fake_sess.page.go_back_history == [], "cross-origin nav should NOT trigger go_back"
        # Only one click landed before we bailed out.
        assert clicked["n"] == 1

    @pytest.mark.asyncio
    async def test_go_back_uses_domcontentloaded_with_hard_timeout(
        self, stub_environment: FakeSession
    ) -> None:
        """Regression: PR #56 originally used `wait_until="networkidle"`, which
        hangs on sites with WebSockets / SSE / HMR (react.dev burned 30+ min
        of CI). Fix in PR #74: `domcontentloaded` + 5s hard timeout."""
        fake_sess = stub_environment
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/inner"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element("Nav1"), make_element("Nav2")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,
                max_pages=1,  # cap so we only exercise page 1 (which does go_back)
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert len(fake_sess.page.go_back_kwargs) >= 1, "expected at least one go_back call"
        # Every go_back must pass wait_until="domcontentloaded" and a timeout.
        for kw in fake_sess.page.go_back_kwargs:
            assert kw.get("wait_until") == "domcontentloaded", (
                f"expected wait_until='domcontentloaded', got {kw}"
            )
            assert isinstance(kw.get("timeout"), int) and kw["timeout"] <= 5000, (
                f"expected hard timeout <= 5000ms, got {kw}"
            )

    @pytest.mark.asyncio
    async def test_max_steps_is_a_global_click_budget(self, stub_environment: FakeSession) -> None:
        """`--max-steps` used to be per-page, so a `max-pages=3 max-steps=2`
        tour could click 6 times total. Now it's global: at most `max-steps`
        clicks across the whole tour, no matter how many pages."""
        fake_sess = stub_environment
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # Every click navigates to a new URL (so BFS wants to keep going)
                fake_sess.page.url = f"https://x.com/page-{click_counter['n']}"
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[make_element(f"e{i}") for i in range(10)]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=3,  # global cap: total clicks across all pages
                max_pages=5,  # generous — budget should stop us before this
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert click_counter["n"] == 3, (
            f"expected exactly 3 clicks (global max_steps), got {click_counter['n']}"
        )

    @pytest.mark.asyncio
    async def test_max_pages_zero_dies(self, stub_environment: FakeSession) -> None:
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
                max_pages=0,
                dwell=0.0,
                initial_wait=0.0,
                max_duration=60.0,
                click_timeout_ms=2000,
                traversal="bfs",  # existing tests were written assuming BFS ordering
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
