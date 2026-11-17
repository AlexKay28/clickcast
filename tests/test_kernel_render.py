"""The shared render kernel (#256).

Two properties are load-bearing and tested here:

1. **Encode FPS is decided exactly once** — capture rate, or the smoothed rate.
2. **Every render option reaches `encode()`** — the old `clickcast run` path
   dropped `quality`/`loop` because it re-implemented the tail by hand.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clickcast.annotate import AnnotateConfig, CursorStyle, StepAnnotation
from clickcast.encode import EncodeResult
from clickcast.kernel import RenderPlan, render_reel

pytestmark = pytest.mark.unit


def _enc(out: Path) -> EncodeResult:
    return EncodeResult(
        path=out,
        format="gif",
        size_bytes=10,
        duration_s=1.0,
        frame_count=5,
    )


def _plan(tmp_path: Path, **kwargs: object) -> RenderPlan:
    base: dict[str, object] = {"out": tmp_path / "reel.gif", "fps": 12}
    base.update(kwargs)
    return RenderPlan(**base)  # type: ignore[arg-type]


def test_encode_receives_every_render_option(tmp_path: Path) -> None:
    plan = _plan(tmp_path, quality=2, loop=3, format="gif")
    with patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)) as encode:
        render_reel(tmp_path, plan)
    encode.assert_called_once()
    kwargs = encode.call_args.kwargs
    assert kwargs["quality"] == 2
    assert kwargs["loop"] == 3
    assert kwargs["format"] == "gif"
    assert kwargs["fps"] == 12


def test_interpolation_is_default_and_keeps_capture_fps(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    with (
        patch("clickcast.kernel.render.interpolate_cursor_motion", return_value=3) as interpolate,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)) as encode,
    ):
        result = render_reel(tmp_path, plan)
    interpolate.assert_called_once()
    assert result.encode_fps == 12
    assert result.smoothed is False
    assert encode.call_args.kwargs["fps"] == 12


def test_interpolation_can_be_disabled(tmp_path: Path) -> None:
    plan = _plan(tmp_path, interpolate=False)
    with (
        patch("clickcast.kernel.render.interpolate_cursor_motion") as interpolate,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)
    interpolate.assert_not_called()


def test_smooth_supersedes_interpolation_and_sets_video_fps(tmp_path: Path) -> None:
    plan = _plan(tmp_path, smooth=True, video_fps=30)
    smoothed = SimpleNamespace(output_fps=30, inserted=10, total_frames=40)
    with (
        patch("clickcast.kernel.render.smooth_frames_dir", return_value=smoothed) as smooth,
        patch("clickcast.kernel.render.interpolate_cursor_motion") as interpolate,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)) as encode,
    ):
        result = render_reel(tmp_path, plan)
    smooth.assert_called_once()
    interpolate.assert_not_called()
    assert result.encode_fps == 30
    assert result.smoothed is True
    assert encode.call_args.kwargs["fps"] == 30


def test_zoom_is_opt_in(tmp_path: Path) -> None:
    plan = _plan(tmp_path, zoom_factor=1.5, zoom_frames_after_click=2)
    with (
        patch("clickcast.kernel.render.apply_zoom_on_click", return_value=4) as zoom,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)
    zoom.assert_called_once_with(tmp_path, factor=1.5, frames_after_click=2)


def test_zoom_skipped_by_default(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    with (
        patch("clickcast.kernel.render.apply_zoom_on_click") as zoom,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)
    zoom.assert_not_called()


def test_stage_order_is_zoom_smooth_annotate_bookends_encode(tmp_path: Path) -> None:
    calls: list[str] = []
    plan = _plan(
        tmp_path,
        smooth=True,
        video_fps=24,
        zoom_factor=2.0,
        step_annotations={0: StepAnnotation(label="open")},
        annotate=AnnotateConfig(),
    )
    smoothed = SimpleNamespace(output_fps=24, inserted=1, total_frames=2)

    def record(name: str) -> MagicMock:
        return MagicMock(side_effect=lambda *a, **k: calls.append(name))

    def smooth(*_args: object, **_kwargs: object) -> object:
        calls.append("smooth")
        return smoothed

    with (
        patch("clickcast.kernel.render.apply_zoom_on_click", record("zoom")),
        patch("clickcast.kernel.render.smooth_frames_dir", side_effect=smooth),
        patch("clickcast.kernel.render.annotate_frames_dir", record("annotate")),
        patch("clickcast.kernel.render.encode", record("encode")),
    ):
        render_reel(tmp_path, plan, before_encode=lambda _d: calls.append("bookends"))

    assert calls == ["zoom", "smooth", "annotate", "bookends", "encode"]
    assert smoothed.output_fps == 24  # sanity: the fixture is the smoothed shape


def test_annotations_are_forwarded(tmp_path: Path) -> None:
    step = StepAnnotation(label="open")
    config = AnnotateConfig(cursor_style=CursorStyle())
    plan = _plan(tmp_path, step_annotations={0: step}, annotate=config)
    with (
        patch("clickcast.kernel.render.annotate_frames_dir", return_value=1) as annotate,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)
    kwargs = annotate.call_args.kwargs
    assert kwargs["steps"] == {0: step}
    assert kwargs["config"] is config
    assert kwargs["regions"] is None


def test_empty_annotations_pass_none_not_an_empty_mapping(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    with (
        patch("clickcast.kernel.render.annotate_frames_dir", return_value=0) as annotate,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)
    assert annotate.call_args.kwargs["steps"] is None


def test_reel_grammar_is_off_by_default(tmp_path: Path) -> None:
    """Legacy rendering must not touch pixels for the grammar pass (#302)."""
    plan = _plan(tmp_path)
    with (
        patch("clickcast.kernel.render.apply_reel_grammar") as grammar,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)
    grammar.assert_not_called()


def test_reel_grammar_receives_labels_and_urls(tmp_path: Path) -> None:
    """The grammar pass is fed the sidecar's labels/URLs and runs pre-annotation."""
    from clickcast.annotate import GrammarConfig

    plan = _plan(
        tmp_path,
        grammar=GrammarConfig(),
        step_labels=("Open home", None),
        step_urls=("https://example.test/", "https://example.test/about"),
    )
    with (
        patch("clickcast.kernel.render.apply_reel_grammar") as grammar,
        patch("clickcast.kernel.render.annotate_frames_dir", return_value=0) as annotate,
        patch("clickcast.kernel.render.encode", return_value=_enc(plan.out)),
    ):
        render_reel(tmp_path, plan)

    grammar.assert_called_once()
    assert grammar.call_args.args[0] == tmp_path
    assert grammar.call_args.kwargs["step_labels"] == ("Open home", None)
    assert grammar.call_args.kwargs["step_urls"] == (
        "https://example.test/",
        "https://example.test/about",
    )
    # Cursor/label overlays must land on top of the grammar compositing.
    assert grammar.call_count == 1
    annotate.assert_called_once()
