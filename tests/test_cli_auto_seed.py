"""`--seed-url` — agent-controllable BFS.

When any seed URL is provided, the tour visits exactly `[start] + seeds`,
in the order given, and does NOT auto-enqueue navigation destinations
discovered during clicks. The `--traversal` flag becomes moot (order is
deterministic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clickcast.cli import _do_auto
from clickcast.discovery import Element


def _make_element(text: str) -> Element:
    return Element(
        selector=f'text="{text}"',
        role="link",
        text=text,
        bbox=(100, 80, 100, 30),
        score=3,
        source="dom-heuristic",
    )


class _FakePage:
    def __init__(self) -> None:
        self._url_stack: list[str] = [""]

    @property
    def url(self) -> str:
        return self._url_stack[-1]

    @url.setter
    def url(self, new: str) -> None:
        self._url_stack.append(new)

    async def go_back(self, **_kwargs: Any) -> None:
        if len(self._url_stack) > 1:
            self._url_stack.pop()


class _FakeSession:
    def __init__(self) -> None:
        self.page = _FakePage()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def wait(self, _s: float) -> None:
        return None


def _make_result() -> MagicMock:
    r = MagicMock()
    r.ok = True
    r.status = "ok"
    r.error = None
    r.cursor_xy = (100, 80)
    return r


@pytest.fixture
def _stub_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    fake_sess = _FakeSession()

    class _SessCtor:
        def __init__(self, **_kwargs: Any) -> None:
            self._sess = fake_sess

        async def __aenter__(self) -> _FakeSession:
            return self._sess

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("clickcast.cli.Session", _SessCtor)

    class _FakeRecorder:
        def __init__(self, **_kwargs: Any) -> None:
            self.frames_dir = tmp_path / "frames"
            self.frames_dir.mkdir(exist_ok=True)

        def __enter__(self) -> _FakeRecorder:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        async def pre_action(self, *_a: Any, **_kw: Any) -> Path:
            return self.frames_dir / "p.png"

        async def post_action(self, *_a: Any, **_kw: Any) -> list[Path]:
            return [self.frames_dir / "q.png"]

        def flush(self) -> list[Path]:
            return []

    monkeypatch.setattr("clickcast.cli.Recorder", _FakeRecorder)
    monkeypatch.setattr("clickcast.cli.annotate_frames_dir", MagicMock(return_value=0))
    monkeypatch.setattr(
        "clickcast.cli.encode",
        MagicMock(
            return_value=MagicMock(
                path=tmp_path / "reel.gif",
                format="gif",
                size_bytes=1024,
                duration_s=1.0,
                frame_count=10,
            )
        ),
    )
    monkeypatch.setattr("clickcast.cli._write_sidecar", MagicMock(return_value=None))
    monkeypatch.setattr("clickcast.cli.ReportBuilder", MagicMock)
    return fake_sess


class TestSeededTours:
    @pytest.mark.asyncio
    async def test_seed_urls_visited_in_order(self, _stub_environment: _FakeSession) -> None:
        """Agent seeds 3 URLs; tour visits start + those 3 in the exact order."""
        fake_sess = _stub_environment
        gotos: list[str] = []

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            if step.__class__.__name__ == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch("clickcast.cli.discover", AsyncMock(return_value=[_make_element("Btn")])),
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
        self, _stub_environment: _FakeSession
    ) -> None:
        """A click that navigates during a seeded tour must NOT get enqueued
        — the caller specified the exact path."""
        fake_sess = _stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # Every click navigates to a "surprise" URL that would have
                # been enqueued in unseeded mode.
                fake_sess.page.url = f"https://x.com/surprise-{click_counter['n']}"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element(f"Btn{i}") for i in range(5)]),
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
        self, _stub_environment: _FakeSession
    ) -> None:
        """Regression from the manual smoke test: with `--seed-url` set and a
        tight `--max-steps`, the tour used to exit after page 1 (loop guard
        `clicks_remaining > 0`) — silently dropping the seeds the caller had
        committed to. Now seeded tours continue even at zero budget; remaining
        seeds get goto + scroll (no clicks) so they still appear in the reel."""
        fake_sess = _stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                click_counter["n"] += 1
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element(f"E{i}") for i in range(3)]),
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
        self, _stub_environment: _FakeSession
    ) -> None:
        """`seed_urls=None` or empty must behave exactly like the default (no seed).
        Auto-discovery of nav destinations should work as before."""
        fake_sess = _stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] <= 2:
                    fake_sess.page.url = f"https://x.com/nav-{click_counter['n']}"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element(f"Btn{i}") for i in range(5)]),
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
