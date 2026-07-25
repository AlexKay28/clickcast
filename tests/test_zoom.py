"""Tests for `apply_zoom_on_click`. Ships #74 Shape A."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from clickcast.annotate.zoom import apply_zoom_on_click


def _make_frame(path: Path, size: tuple[int, int] = (400, 300)) -> None:
    """Grid-patterned frame — makes it obvious when a region got upscaled."""
    img = Image.new("RGB", size, color=(60, 60, 60))
    # Bright pixel at (100, 100) so we can detect where it lands after zoom.
    img.putpixel((100, 100), (255, 0, 0))
    img.save(path, format="PNG")


def _write_manifest(frames_dir: Path, entries: list[dict[str, object]]) -> None:
    (frames_dir / "frames.json").write_text(
        json.dumps({"fps": 12, "count": len(entries), "frames": entries})
    )


class TestApplyZoomOnClick:
    def test_missing_manifest_is_no_op(self, tmp_path: Path) -> None:
        n = apply_zoom_on_click(tmp_path, factor=2.0, frames_after_click=3)
        assert n == 0

    def test_empty_manifest_is_no_op(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, [])
        n = apply_zoom_on_click(tmp_path, factor=2.0, frames_after_click=3)
        assert n == 0

    def test_factor_at_or_below_one_is_no_op(self, tmp_path: Path) -> None:
        """factor <= 1.0 = no zoom, no work."""
        f = tmp_path / "frame-0000-000.png"
        _make_frame(f)
        _write_manifest(
            tmp_path,
            [
                {
                    "path": "frame-0000-000.png",
                    "step_index": 0,
                    "sub_index": 0,
                    "cursor_xy": [100, 100],
                }
            ],
        )
        n = apply_zoom_on_click(tmp_path, factor=1.0, frames_after_click=3)
        assert n == 0

    def test_zooms_only_frames_with_cursor_xy(self, tmp_path: Path) -> None:
        """Frames without cursor_xy (e.g. goto/scroll post-frames) stay
        byte-identical after the pass."""
        (tmp_path / "goto.png").write_bytes(b"")  # never touched
        _make_frame(tmp_path / "goto.png")
        _make_frame(tmp_path / "click.png")
        _write_manifest(
            tmp_path,
            [
                {"path": "goto.png", "step_index": 0, "sub_index": 0, "cursor_xy": None},
                {"path": "click.png", "step_index": 1, "sub_index": 0, "cursor_xy": [100, 100]},
            ],
        )
        goto_before = (tmp_path / "goto.png").read_bytes()
        n = apply_zoom_on_click(tmp_path, factor=2.5, frames_after_click=3)
        assert n == 1  # only click.png zoomed
        assert (tmp_path / "goto.png").read_bytes() == goto_before, "goto frame must not change"

    def test_skips_sub_frames_past_window(self, tmp_path: Path) -> None:
        """`sub_index >= frames_after_click` — leave alone (the ripple has faded,
        no reason to keep zooming)."""
        for i in range(5):
            _make_frame(tmp_path / f"c-{i}.png")
        _write_manifest(
            tmp_path,
            [
                {"path": f"c-{i}.png", "step_index": 0, "sub_index": i, "cursor_xy": [100, 100]}
                for i in range(5)
            ],
        )
        n = apply_zoom_on_click(tmp_path, factor=2.5, frames_after_click=2)
        # Only sub_index 0 and 1 fall inside the window.
        assert n == 2

    def test_dimensions_preserved_after_zoom(self, tmp_path: Path) -> None:
        f = tmp_path / "c.png"
        _make_frame(f, size=(400, 300))
        _write_manifest(
            tmp_path,
            [{"path": "c.png", "step_index": 0, "sub_index": 0, "cursor_xy": [100, 100]}],
        )
        apply_zoom_on_click(tmp_path, factor=2.0, frames_after_click=3)
        with Image.open(f) as img:
            assert img.size == (400, 300), "output dimensions must equal input"

    def test_crop_clamps_at_edges(self, tmp_path: Path) -> None:
        """Click near the corner: the crop should shift (not shrink) so
        output dimensions stay constant."""
        f = tmp_path / "c.png"
        _make_frame(f, size=(400, 300))
        # Click at (10, 10) — crop of 200x150 centered there would go negative;
        # implementation must shift the crop box to fit within the frame.
        _write_manifest(
            tmp_path,
            [{"path": "c.png", "step_index": 0, "sub_index": 0, "cursor_xy": [10, 10]}],
        )
        apply_zoom_on_click(tmp_path, factor=2.0, frames_after_click=3)
        with Image.open(f) as img:
            assert img.size == (400, 300)
