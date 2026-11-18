"""Cursor speed cap — the fix for teleporting pointers in screencast reels."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from clickcast.annotate.annotator import AnnotateConfig, CursorStyle
from clickcast.annotate.cursor_speed import cap_cursor_speed
from clickcast.kernel.render import RenderPlan, render_reel


def _frame_dir(tmp_path: Path, positions: list[Any], *, fps: int = 10) -> Path:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frames = []
    for index, cursor in enumerate(positions):
        name = f"frame-{index:04d}-000.png"
        Image.new("RGB", (8, 8), (index, index, index)).save(frames_dir / name)
        frames.append(
            {
                "path": name,
                "step_index": 0,
                "sub_index": index,
                "cursor_xy": list(cursor) if cursor is not None else None,
                "captured_at": index / fps,
            }
        )
    (frames_dir / "frames.json").write_text(
        json.dumps({"fps": fps, "count": len(frames), "frames": frames})
    )
    return frames_dir


def _manifest(frames_dir: Path) -> dict[str, Any]:
    return json.loads((frames_dir / "frames.json").read_text())


def _peak_speed(frames: list[dict[str, Any]], fps: int) -> float:
    peak = 0.0
    for earlier, target in itertools.pairwise(frames):
        pa, pb = earlier["cursor_xy"], target["cursor_xy"]
        if pa is None or pb is None:
            continue
        peak = max(peak, math.hypot(pb[0] - pa[0], pb[1] - pa[1]) * fps)
    return peak


@pytest.mark.unit
class TestCapCursorSpeed:
    def test_none_disables_the_pass(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (500, 0)])
        assert cap_cursor_speed(frames_dir, max_speed_px_s=None, fps=10) == 0
        assert _manifest(frames_dir)["count"] == 2

    def test_non_positive_disables_the_pass(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (500, 0)])
        assert cap_cursor_speed(frames_dir, max_speed_px_s=0, fps=10) == 0

    def test_short_hop_needs_no_insertion(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (10, 0)], fps=10)
        assert cap_cursor_speed(frames_dir, max_speed_px_s=1600, fps=10) == 0
        assert _manifest(frames_dir)["count"] == 2

    def test_long_hop_is_split_under_the_peak_speed(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (900, 0)], fps=30)
        inserted = cap_cursor_speed(frames_dir, max_speed_px_s=1600, fps=30)
        assert inserted > 0
        frames = _manifest(frames_dir)["frames"]
        # Every hop, including the eased midpoint, stays within the cap.
        assert _peak_speed(frames, 30) <= 1600 + 1e-6
        # It still arrives at the recorded target.
        assert frames[-1]["cursor_xy"] == [900, 0]
        # Inserted frames carry a distinct cursor position, not a still.
        assert len({tuple(f["cursor_xy"]) for f in frames}) == len(frames)

    def test_none_frames_inherit_the_last_known_position(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), None, None, (100, 0)], fps=10)
        cap_cursor_speed(frames_dir, max_speed_px_s=1600, fps=10)
        frames = _manifest(frames_dir)["frames"]
        # The pointer never blinks out mid-reel.
        assert all(f["cursor_xy"] is not None for f in frames)

    def test_leading_none_stays_untracked(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [None, (0, 0), None], fps=10)
        cap_cursor_speed(frames_dir, max_speed_px_s=1600, fps=10)
        frames = _manifest(frames_dir)["frames"]
        assert frames[0]["cursor_xy"] is None

    def test_pass_is_idempotent(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (900, 0)], fps=30)
        first = cap_cursor_speed(frames_dir, max_speed_px_s=1600, fps=30)
        count = _manifest(frames_dir)["count"]
        assert first > 0
        assert cap_cursor_speed(frames_dir, max_speed_px_s=1600, fps=30) == 0
        assert _manifest(frames_dir)["count"] == count
        assert _manifest(frames_dir)["cursor_speed_capped"] is True

    def test_baseline_is_byte_identical_when_disabled(self, tmp_path: Path) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (900, 0)], fps=30)
        before = (frames_dir / "frames.json").read_bytes()
        cap_cursor_speed(frames_dir, max_speed_px_s=None, fps=30)
        assert (frames_dir / "frames.json").read_bytes() == before


@pytest.mark.unit
class TestKernelWiring:
    def test_screencast_plan_caps_cursor_speed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (900, 0)], fps=30)

        import clickcast.kernel.render as render_module

        monkeypatch.setattr(render_module, "annotate_frames_dir", lambda *_a, **_k: 0)
        monkeypatch.setattr(
            render_module,
            "encode",
            lambda _dir, out, **kwargs: _fake_encode_result(Path(out), kwargs["fps"]),
        )

        result = render_reel(
            frames_dir,
            RenderPlan(
                out=tmp_path / "out.mp4",
                fps=30,
                capture="screencast",
                video_fps=30,
                cursor_style=CursorStyle(max_speed_px_s=1600),
                annotate=AnnotateConfig(),
            ),
        )

        frames = _manifest(frames_dir)["frames"]
        assert len(frames) > 2
        assert _peak_speed(frames, result.encode_fps) <= 1600 + 1e-6

    def test_shots_plan_does_not_run_the_screencast_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frames_dir = _frame_dir(tmp_path, [(0, 0), (900, 0)], fps=10)

        import clickcast.kernel.render as render_module

        monkeypatch.setattr(render_module, "annotate_frames_dir", lambda *_a, **_k: 0)
        monkeypatch.setattr(
            render_module,
            "encode",
            lambda _dir, out, **kwargs: _fake_encode_result(Path(out), kwargs["fps"]),
        )

        render_reel(
            frames_dir,
            RenderPlan(
                out=tmp_path / "out.gif",
                fps=10,
                capture="shots",
                interpolate=False,
                annotate=AnnotateConfig(),
            ),
        )

        assert _manifest(frames_dir)["count"] == 2


def _fake_encode_result(path: Path, fps: int) -> Any:
    from clickcast.encode import EncodeResult

    return EncodeResult(path=path, format="mp4", size_bytes=1, duration_s=1 / fps, frame_count=1)
