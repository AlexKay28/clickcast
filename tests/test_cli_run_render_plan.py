"""`clickcast run` forwards every render option into the shared kernel (#256).

Regression: `_do_run` used to call `encode(...)` itself and silently dropped
`render.quality` and `render.loop`, and never interpolated cursor motion, so
the same scenario rendered differently depending on which command ran it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clickcast.cli import _do_run
from clickcast.scenario import Meta, Scenario

pytestmark = pytest.mark.unit


def _scenario(**render: object) -> Scenario:
    return Scenario(meta=Meta(render=render), steps=[])


class _FakeRecorder:
    def __init__(self, **_kwargs: object) -> None:
        self.frames_dir = Path("/fake/frames")

    def __enter__(self) -> _FakeRecorder:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def flush(self) -> None:
        return None


async def test_run_forwards_quality_loop_smooth_and_video_fps(tmp_path: Path) -> None:
    encoded = SimpleNamespace(
        path=tmp_path / "reel.gif",
        format="gif",
        size_bytes=1,
        duration_s=1.0,
        frame_count=2,
    )
    with (
        patch("clickcast.cli.Recorder", _FakeRecorder),
        patch(
            "clickcast.cli.run_scenario",
            AsyncMock(return_value=SimpleNamespace(ok=True, failed_at=None, results=[])),
        ),
        patch(
            "clickcast.cli.render_reel",
            MagicMock(return_value=SimpleNamespace(enc=encoded, encode_fps=24)),
        ) as render_reel,
    ):
        await _do_run(
            scenario=_scenario(quality=2, loop=3, smooth=True, video_fps=24),
            out=str(tmp_path / "reel.gif"),
            format_="gif",
            no_sidecar=True,
            with_feedback=False,
            redact_patterns=None,
            strip_query_strings=False,
            emit_events=False,
            grid=None,
            annotations_path=None,
        )

    plan = render_reel.call_args.args[1]
    assert plan.quality == 2
    assert plan.loop == 3
    assert plan.smooth is True
    assert plan.video_fps == 24
    assert plan.format == "gif"
    assert plan.interpolate is True


async def test_run_defaults_come_from_render_opts(tmp_path: Path) -> None:
    encoded = SimpleNamespace(
        path=tmp_path / "reel.gif",
        format="gif",
        size_bytes=1,
        duration_s=1.0,
        frame_count=2,
    )
    with (
        patch("clickcast.cli.Recorder", _FakeRecorder),
        patch(
            "clickcast.cli.run_scenario",
            AsyncMock(return_value=SimpleNamespace(ok=True, failed_at=None, results=[])),
        ),
        patch(
            "clickcast.cli.render_reel",
            MagicMock(return_value=SimpleNamespace(enc=encoded, encode_fps=12)),
        ) as render_reel,
    ):
        await _do_run(
            scenario=_scenario(),
            out=str(tmp_path / "reel.gif"),
            format_=None,
            no_sidecar=True,
            with_feedback=False,
            redact_patterns=None,
            strip_query_strings=False,
            emit_events=False,
            grid=None,
            annotations_path=None,
        )

    plan = render_reel.call_args.args[1]
    assert plan.quality == 8
    assert plan.loop == 0
    assert plan.smooth is False
    assert plan.format is None
