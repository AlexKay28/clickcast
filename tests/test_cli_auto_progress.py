"""Live-progress logging assertions for `_do_auto`.

Regression: a 9-minute react.dev demo was completely silent with `--verbose`,
so from the terminal it looked hung. #59 traced the fix: per-click, per-nav,
per-go_back, per-page-summary INFO lines. These tests lock the trace in place.
"""

from __future__ import annotations

import logging
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


class TestAutoProgressLogging:
    @pytest.mark.asyncio
    async def test_emits_per_click_info_lines(
        self, _stub_environment: _FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression: silent-during-work symptom. Every click must produce an
        INFO log line so the user can see progress in a long run."""
        fake_sess = _stub_environment
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                click_counter["n"] += 1
            return _make_result()

        elements = [_make_element(f"Btn{i}") for i in range(5)]
        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch("clickcast.cli.discover", AsyncMock(return_value=elements)),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=5,
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

        click_lines = [r for r in caplog.records if "· click " in r.message]
        assert len(click_lines) == 5, (
            f"expected 5 per-click INFO lines, got {len(click_lines)}: "
            f"{[r.message for r in click_lines]}"
        )

    @pytest.mark.asyncio
    async def test_emits_nav_and_go_back_lines(
        self, _stub_environment: _FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Every same-origin navigation must log both the nav and the go_back
        (this is where the demo used to silently spend 5-30 seconds per click)."""
        fake_sess = _stub_environment
        click_counter = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                click_counter["n"] += 1
                if click_counter["n"] == 1:
                    fake_sess.page.url = "https://x.com/inner"
            return _make_result()

        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch(
                "clickcast.cli.discover",
                AsyncMock(return_value=[_make_element("Nav"), _make_element("Other")]),
            ),
        ):
            await _do_auto(
                url="https://x.com/",
                out="reel.gif",
                max_steps=3,
                max_pages=1,  # single page so we exercise go_back path
                dwell=0.0,
                initial_wait=0.0,
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
        self, _stub_environment: _FakeSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Each page ends with a `page N/M · done in Ns (X clicks used, ...)` line —
        the summary that used to be missing between pages."""
        fake_sess = _stub_environment

        async def _fake_execute(step: Any, _sess: Any) -> MagicMock:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            return _make_result()

        with (
            caplog.at_level(logging.INFO, logger="clickcast.auto"),
            patch("clickcast.cli.execute", side_effect=_fake_execute),
            patch("clickcast.cli.discover", AsyncMock(return_value=[_make_element("Btn")])),
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

        messages = [r.message for r in caplog.records]
        assert any("done in" in m and "clicks used" in m for m in messages), (
            "missing page-summary line. Got:\n" + "\n".join(messages)
        )
