"""DFS vs BFS URL queue ordering."""

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


class TestTraversalOrdering:
    """DFS pops from the right (LIFO); BFS pops from the left (FIFO)."""

    @pytest.mark.asyncio
    async def test_dfs_visits_deepest_first(self, _stub_environment: _FakeSession) -> None:
        """From start, discover 3 nav destinations. DFS visits the LAST-
        discovered one first (LIFO), giving depth-first ordering."""
        fake_sess = _stub_environment
        gotos: list[str] = []
        counters: dict[str, int] = {}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                here = fake_sess.page.url
                n = counters.get(here, 0) + 1
                counters[here] = n
                # Only the start page has nav targets. First 3 clicks navigate.
                if here == "https://x.com/" and n <= 3:
                    fake_sess.page.url = f"https://x.com/dest-{n}"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element(f"L{i}") for i in range(3)]),
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
    async def test_bfs_visits_earliest_first(self, _stub_environment: _FakeSession) -> None:
        """Same setup as DFS test, but with `traversal='bfs'`. BFS visits
        the FIRST-discovered destination first (FIFO)."""
        fake_sess = _stub_environment
        gotos: list[str] = []
        counters: dict[str, int] = {}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                fake_sess.page.url = step.url
                return _make_result()
            if cls == "ClickStep":
                here = fake_sess.page.url
                n = counters.get(here, 0) + 1
                counters[here] = n
                if here == "https://x.com/" and n <= 3:
                    fake_sess.page.url = f"https://x.com/dest-{n}"
            return _make_result()

        with (
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element(f"L{i}") for i in range(3)]),
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
