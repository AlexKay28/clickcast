"""`--emit-events` (#151 AI-4) tests.

The auto engine and the scenario runner both grow a `--emit-events` flag
that prints one JSON `{"event": "tour_complete", ...}` line to stdout
after the shipped prose summary. JSONL-friendly so future event types
(per-step, advisories) can share the channel; off by default so shipped
scrapers stay unchanged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from clickcast.auto import _emit_tour_complete
from clickcast.cli import _do_auto, _do_run, app
from clickcast.core.actions import (
    ActionResult,
    ClickStep,
    GotoStep,
    ScrollStep,
)
from clickcast.scenario.scenario import RunResult, Scenario
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
        json_lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
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


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    """Strip ANSI SGR escape codes from ``s``.

    Rich (bundled with Typer) breaks color runs at hyphen boundaries when
    rendering flag names to a color-capable terminal — so under
    ``GITHUB_ACTIONS=true``, the rendered ``--emit-events`` includes ANSI
    escapes between the hyphens, and a literal ``"--emit-events" in
    stdout`` check fails even though the flag is visibly present. Local
    dev terminals happened not to trigger it, so the tests below looked
    green for months while every CI run has been red. Strip escapes
    before asserting.
    """
    return _ANSI_ESCAPE.sub("", s)


class TestCliEmitEventsFlag:
    """Both `auto` and `run` expose `--emit-events` as a CLI flag."""

    runner = CliRunner()

    def test_auto_help_shows_emit_events(self) -> None:
        result = self.runner.invoke(app, ["auto", "--help"])
        assert result.exit_code == 0
        assert "--emit-events" in _plain(result.stdout)

    def test_run_help_shows_emit_events(self) -> None:
        result = self.runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--emit-events" in _plain(result.stdout)


class TestRunEmitEventsCountsExecutedStepsOnly:
    """Regression for #172.

    ``clickcast run --emit-events`` used to count ``pages``/``clicks``
    from the parsed scenario source, so a 5-step scenario that failed at
    step 3 still reported 5-worth of steps. Count from ``result.results``
    (executed) instead, filtering on ``status == "ok"`` — consistent with
    the ``auto`` engine which already reports executed totals.
    """

    @pytest.mark.asyncio
    async def test_pages_and_clicks_count_only_executed_ok_steps(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # 5-step scenario: goto, click, click, click, goto.
        # Simulate a failure at index 2 (the second click) — only the
        # first goto + first click actually executed successfully. The
        # third-through-fifth steps never ran.
        scenario = Scenario(
            steps=[
                GotoStep(url="https://x.example/"),
                ClickStep(selector="#a"),
                ClickStep(selector="#b-boom"),
                ClickStep(selector="#c"),
                GotoStep(url="https://x.example/next"),
            ]
        )

        # Fake results: 2 ok, 1 failed, and nothing after (matches
        # RunResult semantics — the runner returns on first failure).
        fake_results = [
            ActionResult(ok=True, status="ok", action="goto"),
            ActionResult(ok=True, status="ok", action="click", selector="#a"),
            ActionResult(
                ok=False,
                status="failed",
                action="click",
                selector="#b-boom",
                error="boom",
            ),
        ]
        fake_run_result = RunResult(results=fake_results, failed_at=2)

        # Stub the heavy dependencies inside _do_run so the test doesn't
        # need a real browser or ffmpeg — but keep _do_run itself under
        # test (per #172: assert the emit-events block, don't stub it).
        fake_enc = MagicMock()
        fake_enc.path = tmp_path / "reel.gif"
        fake_enc.format = "gif"
        fake_enc.size_bytes = 1024
        fake_enc.frame_count = 10
        fake_enc.duration_s = 1.0

        class _FakeRecorder:
            def __init__(self, *_a: Any, **_kw: Any) -> None:
                self.frames_dir = tmp_path / "frames"
                self.frames_dir.mkdir(exist_ok=True)

            def __enter__(self) -> _FakeRecorder:
                return self

            def __exit__(self, *_a: Any) -> None:
                return None

            def flush(self) -> list[Any]:
                return []

        with (
            patch(
                "clickcast.cli.run_scenario",
                AsyncMock(return_value=fake_run_result),
            ),
            patch("clickcast.cli.Recorder", _FakeRecorder),
            patch("clickcast.cli.annotate_frames_dir"),
            patch("clickcast.cli.encode", return_value=fake_enc),
            pytest.raises(typer.Exit),
        ):
            # _do_run raises typer.Exit(1) on a failed scenario.
            # Not what we're testing; we care about the emitted JSON,
            # which is printed BEFORE the raise.
            await _do_run(
                scenario=scenario,
                out=str(tmp_path / "reel.gif"),
                format_=None,
                no_sidecar=True,
                emit_events=True,
            )

        stdout = capsys.readouterr().out
        json_lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
        assert json_lines, f"no JSON line in stdout:\n{stdout}"
        payload = json.loads(json_lines[-1])
        assert payload["event"] == "tour_complete"
        # ONLY 1 goto and 1 click actually ran successfully — the failed
        # click and the never-executed remaining steps must NOT be counted.
        assert payload["pages"] == 1, (
            f"pages should count only executed 'goto' steps with status=='ok', "
            f"got {payload['pages']} (bug #172)"
        )
        assert payload["clicks"] == 1, (
            f"clicks should count only executed 'click'/'dblclick' steps with "
            f"status=='ok', got {payload['clicks']} (bug #172)"
        )

    @pytest.mark.asyncio
    async def test_skipped_optional_steps_not_counted(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A scenario with an optional click that got skipped (status=='skipped').
        # Skipped steps must not inflate the clicks total either — same fix.
        scenario = Scenario(
            steps=[
                GotoStep(url="https://x.example/"),
                ClickStep(selector="#missing", optional=True),
                ScrollStep(by=200),
            ]
        )
        fake_results = [
            ActionResult(ok=True, status="ok", action="goto"),
            ActionResult(
                ok=True,
                status="skipped",
                action="click",
                selector="#missing",
                skip_reason="locator_missing",
            ),
            ActionResult(ok=True, status="ok", action="scroll"),
        ]
        fake_run_result = RunResult(results=fake_results, failed_at=None)

        fake_enc = MagicMock()
        fake_enc.path = tmp_path / "reel.gif"
        fake_enc.format = "gif"
        fake_enc.size_bytes = 1024
        fake_enc.frame_count = 10
        fake_enc.duration_s = 1.0

        class _FakeRecorder:
            def __init__(self, *_a: Any, **_kw: Any) -> None:
                self.frames_dir = tmp_path / "frames"
                self.frames_dir.mkdir(exist_ok=True)

            def __enter__(self) -> _FakeRecorder:
                return self

            def __exit__(self, *_a: Any) -> None:
                return None

            def flush(self) -> list[Any]:
                return []

        with (
            patch(
                "clickcast.cli.run_scenario",
                AsyncMock(return_value=fake_run_result),
            ),
            patch("clickcast.cli.Recorder", _FakeRecorder),
            patch("clickcast.cli.annotate_frames_dir"),
            patch("clickcast.cli.encode", return_value=fake_enc),
        ):
            await _do_run(
                scenario=scenario,
                out=str(tmp_path / "reel.gif"),
                format_=None,
                no_sidecar=True,
                emit_events=True,
            )

        stdout = capsys.readouterr().out
        json_lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
        assert json_lines, f"no JSON line in stdout:\n{stdout}"
        payload = json.loads(json_lines[-1])
        # 1 goto ok, 1 click SKIPPED, 1 scroll ok → clicks should be 0.
        assert payload["pages"] == 1
        assert payload["clicks"] == 0, (
            f"skipped optional click must not count toward clicks total, "
            f"got {payload['clicks']} (bug #172)"
        )
