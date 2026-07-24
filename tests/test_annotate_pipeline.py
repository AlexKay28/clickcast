"""Tests for `clickcast.annotate.pipeline.annotate_frames_dir`."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops

from clickcast.annotate import StepAnnotation, annotate_frames_dir


def _make_frame(path: Path, size: tuple[int, int] = (320, 200)) -> None:
    Image.new("RGB", size, color=(40, 60, 90)).save(path, format="PNG")


def _write_manifest(
    frames_dir: Path,
    entries: list[dict[str, object]],
    *,
    fps: int = 12,
) -> None:
    (frames_dir / "frames.json").write_text(
        json.dumps({"fps": fps, "count": len(entries), "frames": entries})
    )


def _rgb(path: Path) -> bytes:
    with Image.open(path) as img:
        return img.convert("RGB").tobytes()


def _make_recording(tmp_path: Path) -> Path:
    """A 3-step, 5-frame recording: goto (2), click (2), scroll (1)."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frames = [
        {"path": "frame-0000-000.png", "step_index": 0, "sub_index": 0, "cursor_xy": None},
        {"path": "frame-0000-001.png", "step_index": 0, "sub_index": 1, "cursor_xy": None},
        {"path": "frame-0001-000.png", "step_index": 1, "sub_index": 0, "cursor_xy": [100, 80]},
        {"path": "frame-0001-001.png", "step_index": 1, "sub_index": 1, "cursor_xy": [100, 80]},
        {"path": "frame-0002-000.png", "step_index": 2, "sub_index": 0, "cursor_xy": None},
    ]
    for e in frames:
        _make_frame(frames_dir / e["path"])
    _write_manifest(frames_dir, frames)
    return frames_dir


class TestAnnotateFramesDir:
    def test_missing_manifest_is_no_op(self, tmp_path: Path) -> None:
        n = annotate_frames_dir(tmp_path)
        assert n == 0

    def test_empty_manifest_is_no_op(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, [])
        n = annotate_frames_dir(tmp_path)
        assert n == 0

    def test_returns_frame_count(self, tmp_path: Path) -> None:
        frames_dir = _make_recording(tmp_path)
        n = annotate_frames_dir(frames_dir)
        assert n == 5

    def test_frames_are_modified_in_place(self, tmp_path: Path) -> None:
        frames_dir = _make_recording(tmp_path)
        before = _rgb(frames_dir / "frame-0000-000.png")
        annotate_frames_dir(frames_dir)
        after = _rgb(frames_dir / "frame-0000-000.png")
        # Progress bar draws on every frame, so pixels must change.
        assert before != after

    def test_frame_dimensions_preserved(self, tmp_path: Path) -> None:
        frames_dir = _make_recording(tmp_path)
        annotate_frames_dir(frames_dir)
        with Image.open(frames_dir / "frame-0000-000.png") as img:
            assert img.size == (320, 200)

    def test_click_ripple_only_on_click_step(self, tmp_path: Path) -> None:
        frames_dir = _make_recording(tmp_path)
        # step 0 = no click; step 1 = click; step 2 = no click.
        steps = {
            1: StepAnnotation(label="click something", click_at=(100, 80)),
        }
        # Snapshot the goto-step frame before and after — ripple should NOT
        # appear on it. Because progress + label change pixels too, we compare
        # a bounding box around the click coord (which is only touched by the
        # ripple layer).
        annotate_frames_dir(frames_dir, steps=steps)
        goto_frame = frames_dir / "frame-0000-000.png"
        click_frame = frames_dir / "frame-0001-000.png"
        with Image.open(goto_frame) as g, Image.open(click_frame) as c:
            # 60x60 box around (100,80): only the click frame should have a ripple here.
            box = (70, 50, 130, 110)
            g_crop = g.crop(box).convert("RGB")
            c_crop = c.crop(box).convert("RGB")
            diff = ImageChops.difference(g_crop, c_crop).getbbox()
            assert diff is not None, "click ripple did not draw distinguishable pixels"

    def test_label_appears_only_when_provided(self, tmp_path: Path) -> None:
        frames_dir = _make_recording(tmp_path)
        # Only step 0 gets a label; steps 1/2 do not.
        steps = {0: StepAnnotation(label="opening site")}
        annotate_frames_dir(frames_dir, steps=steps)
        # The label banner sits at the bottom — compare bottom strip between
        # a labeled frame and an unlabeled one.
        with (
            Image.open(frames_dir / "frame-0000-000.png") as labeled,
            Image.open(frames_dir / "frame-0002-000.png") as unlabeled,
        ):
            bottom = (0, 140, 320, 200)
            l_strip = labeled.crop(bottom).convert("RGB")
            u_strip = unlabeled.crop(bottom).convert("RGB")
            diff = ImageChops.difference(l_strip, u_strip).getbbox()
            assert diff is not None, "label banner did not draw distinguishable pixels"

    def test_step_annotation_defaults(self) -> None:
        s = StepAnnotation()
        assert s.label is None
        assert s.click_at is None
