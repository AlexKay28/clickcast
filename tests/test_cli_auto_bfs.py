"""Unit tests for the BFS URL queue behavior of `_do_auto`.

These stub out Playwright / recorder / encoder so we can assert the pure
orchestration logic — which URLs get visited, in what order, and how the
``--max-pages`` cap interacts with same-origin dedup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clickcast.cli import _do_auto
from clickcast.discovery import Element


def _make_element(text: str, x: int = 100, y: int = 80) -> Element:
    return Element(
        selector=f'text="{text}"',
        role="link",
        text=text,
        bbox=(x, y, 100, 30),
        score=3,
        source="dom-heuristic",
    )


class _FakePage:
    """Just enough of playwright.Page to satisfy `_explore_page`.

    Assigning to ``url`` is tracked in a stack so ``go_back()`` can restore
    the previous URL — the click-and-observe fix relies on go_back to keep
    clicking after a nav. ``go_back_history`` records the URLs go_back was
    called from (tests assert on this).
    """

    def __init__(self) -> None:
        self._url_stack: list[str] = [""]
        self.go_back_history: list[str] = []

    @property
    def url(self) -> str:
        return self._url_stack[-1]

    @url.setter
    def url(self, new: str) -> None:
        self._url_stack.append(new)

    async def go_back(self, **_kwargs: Any) -> None:
        self.go_back_history.append(self._url_stack[-1])
        if len(self._url_stack) > 1:
            self._url_stack.pop()


class _FakeSession:
    def __init__(self) -> None:
        self.page = _FakePage()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def wait(self, _seconds: float) -> None:
        return None


def _make_result(*, ok: bool = True, cursor: tuple[int, int] | None = (100, 80)) -> MagicMock:
    r = MagicMock()
    r.ok = ok
    r.status = "ok" if ok else "failed"
    r.error = None if ok else "boom"
    r.cursor_xy = cursor
    return r


@pytest.fixture
def _stub_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch every heavy dependency `_do_auto` touches; return the mocks so
    each test can shape per-scenario behavior on `discover` and `execute`."""

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
            return self.frames_dir / "pre.png"

        async def post_action(self, *_a: Any, **_kw: Any) -> list[Path]:
            return [self.frames_dir / "post.png"]

        def flush(self) -> list[Path]:
            return []

    monkeypatch.setattr("clickcast.cli.Recorder", _FakeRecorder)

    monkeypatch.setattr("clickcast.cli.annotate_frames_dir", MagicMock(return_value=0))

    fake_enc = MagicMock(
        path=tmp_path / "reel.gif",
        format="gif",
        size_bytes=1024,
        duration_s=1.0,
        frame_count=10,
    )
    monkeypatch.setattr("clickcast.cli.encode", MagicMock(return_value=fake_enc))
    monkeypatch.setattr("clickcast.cli._write_sidecar", MagicMock(return_value=None))

    # No sidecar builder needed for these tests.
    monkeypatch.setattr("clickcast.cli.ReportBuilder", MagicMock)

    return {"session": fake_sess}


class TestBfsQueue:
    @pytest.mark.asyncio
    async def test_max_pages_1_visits_only_start(
        self, _stub_environment: dict[str, MagicMock]
    ) -> None:
        fake_sess: _FakeSession = _stub_environment["session"]

        gotos: list[str] = []

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            if step.__class__.__name__ == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("Home")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=1,
                dwell=0.0,
                initial_wait=0.0,
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
        self, _stub_environment: dict[str, MagicMock]
    ) -> None:
        fake_sess: _FakeSession = _stub_environment["session"]
        gotos: list[str] = []
        click_counter = {"n": 0}

        # First click on start → navigate to /about; then start-page discovery
        # is done. On the /about page the same-shape click is a no-op so the
        # click loop finishes without a further nav.
        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/about"
                return _make_result()
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("About"), _make_element("Docs")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=2,
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
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
        self, _stub_environment: dict[str, MagicMock]
    ) -> None:
        fake_sess: _FakeSession = _stub_environment["session"]
        gotos: list[str] = []
        first_click = {"done": False}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep" and not first_click["done"]:
                first_click["done"] = True
                fake_sess.page.url = "https://other.example.com/land"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("External")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
        assert gotos == ["https://x.com/"], "cross-origin destination should not have been visited"

    @pytest.mark.asyncio
    async def test_visited_dedup_prevents_re_goto(
        self, _stub_environment: dict[str, MagicMock]
    ) -> None:
        fake_sess: _FakeSession = _stub_environment["session"]
        gotos: list[str] = []
        click_counter = {"n": 0}

        # Every click navigates back to a page we've already visited (/about);
        # dedup must prevent the second goto.
        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                click_counter["n"] += 1
                # click #1 on start → navigate to /about
                # click #2 on /about → navigate back to start (already visited)
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/about"
                elif click_counter["n"] == 2:
                    fake_sess.page.url = "https://x.com/"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("Nav")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=5,
                dwell=0.0,
                initial_wait=0.0,
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
        self, _stub_environment: dict[str, MagicMock]
    ) -> None:
        """Regression: `break`-on-first-nav starved BFS. First click was a
        nav-to-already-visited (like clicking the site logo) → BFS enqueued
        nothing new and exited after 1 page. With go_back-and-continue, we
        return to the start page and click the next element, which navigates
        to a *new* URL that gets enqueued."""
        fake_sess: _FakeSession = _stub_environment["session"]
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
                # Click #1: logo → same URL (already visited)
                # Click #2: nav to /about → new URL (should be enqueued)
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/"  # same as start
                elif click_counter["n"] == 2:
                    fake_sess.page.url = "https://x.com/about"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("Logo"), _make_element("About")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,  # generous — we want to see both clicks happen
                max_pages=3,
                dwell=0.0,
                initial_wait=0.0,
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
    async def test_cross_origin_nav_bails_no_go_back(
        self, _stub_environment: dict[str, MagicMock]
    ) -> None:
        """Cross-origin nav is a stronger signal that we shouldn't drive on
        the site (privacy, TOS, whatever). Bail without go_back."""
        fake_sess: _FakeSession = _stub_environment["session"]
        clicked = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                clicked["n"] += 1
                if clicked["n"] == 1:
                    fake_sess.page.url = "https://other.example.com/"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("External"), _make_element("Other")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,
                max_pages=3,
                dwell=0.0,
                initial_wait=0.0,
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
    async def test_max_pages_zero_dies(self, _stub_environment: dict[str, MagicMock]) -> None:
        from typer import Exit

        with (
            patch("clickcast.cli.execute", AsyncMock(return_value=_make_result())),
            patch("clickcast.cli.discover", AsyncMock(return_value=[_make_element("x")])),
            pytest.raises(Exit),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=1,
                max_pages=0,
                dwell=0.0,
                initial_wait=0.0,
                session_kwargs={"engine": "chromium"},
                fps=12,
                format_=None,
                quality=8,
                loop=0,
                no_sidecar=True,
            )
