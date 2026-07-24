"""Time-budget assertions for `_do_auto` (`--max-duration` + `--click-timeout`)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clickcast.cli import _do_auto
from clickcast.core.actions import ClickStep
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

    monkeypatch.setattr("clickcast.auto.Session", _SessCtor)

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

    monkeypatch.setattr("clickcast.auto.Recorder", _FakeRecorder)
    monkeypatch.setattr("clickcast.auto.annotate_frames_dir", MagicMock(return_value=0))
    monkeypatch.setattr(
        "clickcast.auto.encode",
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
    monkeypatch.setattr("clickcast.auto._write_sidecar", MagicMock(return_value=None))
    monkeypatch.setattr("clickcast.auto.ReportBuilder", MagicMock)
    return fake_sess


class TestClickTimeoutPropagates:
    """`--click-timeout` must land on the ClickStep we hand to `execute`."""

    @pytest.mark.asyncio
    async def test_click_step_has_timeout_ms(self, _stub_environment: _FakeSession) -> None:
        fake_sess = _stub_environment
        seen_steps: list[ClickStep] = []

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            elif step.__class__.__name__ == "ClickStep":
                seen_steps.append(step)
            return _make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch(
                "clickcast.auto.discover",
                AsyncMock(return_value=[_make_element("Btn")]),
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
        self, _stub_environment: _FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_sess = _stub_environment
        gotos: list[str] = []
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
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
            return _make_result()

        elements = [_make_element(f"e{i}") for i in range(20)]
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
    async def test_max_duration_zero_dies(self, _stub_environment: _FakeSession) -> None:
        from typer import Exit

        with (
            patch("clickcast.auto.execute", AsyncMock(return_value=_make_result())),
            patch("clickcast.auto.discover", AsyncMock(return_value=[_make_element("x")])),
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
