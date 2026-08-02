"""`--emit-events` (#151 AI-4) tests.

The auto engine and the scenario runner both grow a `--emit-events` flag
that prints one JSON `{"event": "tour_complete", ...}` line to stdout
after the shipped prose summary. JSONL-friendly so future event types
(per-step, advisories) can share the channel; off by default so shipped
scrapers stay unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from clickcast.auto import _emit_tour_complete
from clickcast.cli import _do_auto, app
from tests._stubs import FakeSession, make_element, make_result


class TestEmitTourCompleteHelper:
    """The stdout-emit helper is small enough to exercise directly."""

    def test_prints_single_json_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        _emit_tour_complete(
            gif_path="tour.gif",
            frames=59,
            duration_s=7.4,
            pages=1,
            clicks=5,
            wall_s=29.0,
            sidecar_path="tour.gif.json",
        )
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        payload = json.loads(out[0])
        # Every documented key must be present so JSONL parsers can gate
        # on a stable shape (see #151 AI-4).
        assert payload == {
            "event": "tour_complete",
            "gif_path": "tour.gif",
            "frames": 59,
            "duration_s": 7.4,
            "pages": 1,
            "clicks": 5,
            "wall_s": 29.0,
            "sidecar_path": "tour.gif.json",
        }

    def test_null_sidecar_survives_serialisation(self, capsys: pytest.CaptureFixture[str]) -> None:
        _emit_tour_complete(
            gif_path="reel.gif",
            frames=10,
            duration_s=1.0,
            pages=1,
            clicks=0,
            wall_s=2.0,
            sidecar_path=None,
        )
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["sidecar_path"] is None

    def test_rounds_seconds_to_one_decimal(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Precision matches the human-readable summary so a caller diffing
        # the two lines sees consistent numbers.
        _emit_tour_complete(
            gif_path="x",
            frames=1,
            duration_s=7.4321,
            pages=1,
            clicks=1,
            wall_s=29.876,
            sidecar_path=None,
        )
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["duration_s"] == 7.4
        assert payload["wall_s"] == 29.9


class TestAutoEmitsEventsFlag:
    """`clickcast auto ... --emit-events` — stdout must carry the JSON line."""

    @pytest.mark.asyncio
    async def test_flag_off_prints_no_json(
        self, stub_environment: FakeSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_sess = stub_environment

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch("clickcast.auto.discover", AsyncMock(return_value=[make_element("Home")])),
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
                emit_events=False,
            )
        stdout = capsys.readouterr().out
        # Prose summary present, no JSON line follows.
        assert "✔" in stdout
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("{"):
                pytest.fail(f"unexpected JSON on stdout: {stripped!r}")

    @pytest.mark.asyncio
    async def test_flag_on_prints_tour_complete_json(
        self, stub_environment: FakeSession, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_sess = stub_environment

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            return make_result()

        with (
            patch("clickcast.auto.execute", side_effect=_fake_execute),
            patch("clickcast.auto.discover", AsyncMock(return_value=[make_element("Home")])),
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
                emit_events=True,
            )
        stdout = capsys.readouterr().out
        # Find the last JSON line (JSONL-friendly — future event types
        # may add more, we assert on the tour_complete terminator).
        json_lines = [
            line for line in stdout.splitlines() if line.strip().startswith("{")
        ]
        assert json_lines, f"no JSON line in stdout:\n{stdout}"
        payload = json.loads(json_lines[-1])
        # Every documented key must appear.
        assert payload["event"] == "tour_complete"
        for key in (
            "gif_path",
            "frames",
            "duration_s",
            "pages",
            "clicks",
            "wall_s",
            "sidecar_path",
        ):
            assert key in payload, f"missing key {key!r} in event payload"


class TestCliEmitEventsFlag:
    """Both `auto` and `run` expose `--emit-events` as a CLI flag."""

    runner = CliRunner()

    def test_auto_help_shows_emit_events(self) -> None:
        result = self.runner.invoke(app, ["auto", "--help"])
        assert result.exit_code == 0
        assert "--emit-events" in result.stdout

    def test_run_help_shows_emit_events(self) -> None:
        result = self.runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--emit-events" in result.stdout
