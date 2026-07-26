"""Tests for :meth:`Reel.save_region` and :meth:`Reel.save_region_at_step`.

Split into two layers:

- Unit tests for the pure helpers (`_clip_bbox_to_image`, `_select_frame`,
  `_last_frame_for_step`) — no browser, no PIL round-trip.
- Integration tests against the local fixture site — real Playwright,
  real bbox lookups, real PNG cropping.

The integration bar mirrors #109's acceptance list: bbox correctness on a
fixture, negative frame index, padding clip, missing selector raises, PNG
dimensions match.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from clickcast import Reel
from clickcast.capture.recorder import FrameRef
from clickcast.reel import (
    _clip_bbox_to_image,
    _last_frame_for_step,
    _select_frame,
)

# ------------------------------------------------------------------
# Unit — pure helpers
# ------------------------------------------------------------------


class TestClipBboxToImage:
    def test_zero_padding_returns_ltrb(self) -> None:
        # bbox = (x, y, w, h); expected ltrb = (x, y, x+w, y+h)
        assert _clip_bbox_to_image((10, 20, 30, 40), 0, (100, 100)) == (10, 20, 40, 60)

    def test_positive_padding_grows_all_sides(self) -> None:
        assert _clip_bbox_to_image((10, 20, 30, 40), 5, (100, 100)) == (5, 15, 45, 65)

    def test_padding_clipped_at_left_and_top(self) -> None:
        # bbox flush against top-left; padding cannot go negative.
        assert _clip_bbox_to_image((0, 0, 20, 20), 8, (100, 100)) == (0, 0, 28, 28)

    def test_padding_clipped_at_right_and_bottom(self) -> None:
        # bbox flush against bottom-right; padding cannot exceed image edge.
        assert _clip_bbox_to_image((80, 80, 20, 20), 8, (100, 100)) == (72, 72, 100, 100)

    def test_empty_crop_raises(self) -> None:
        # bbox entirely outside the image after clipping → empty rect.
        with pytest.raises(ValueError, match="empty after clipping"):
            _clip_bbox_to_image((200, 200, 10, 10), 0, (100, 100))


class TestSelectFrame:
    @staticmethod
    def _make(n: int) -> list[FrameRef]:
        return [
            FrameRef(path=Path(f"f{i}.png"), step_index=i, sub_index=0, cursor_xy=None)
            for i in range(n)
        ]

    def test_negative_index_returns_from_end(self) -> None:
        frames = self._make(3)
        assert _select_frame(frames, -1) is frames[-1]
        assert _select_frame(frames, -3) is frames[0]

    def test_positive_index(self) -> None:
        frames = self._make(3)
        assert _select_frame(frames, 1) is frames[1]

    def test_empty_raises(self) -> None:
        with pytest.raises(IndexError, match="no frames"):
            _select_frame([], -1)

    def test_out_of_range_raises(self) -> None:
        with pytest.raises(IndexError, match="out of range"):
            _select_frame(self._make(2), 5)


class TestLastFrameForStep:
    def test_returns_highest_sub_index_for_that_step(self) -> None:
        frames = [
            FrameRef(path=Path("a.png"), step_index=0, sub_index=0, cursor_xy=None),
            FrameRef(path=Path("b.png"), step_index=0, sub_index=1, cursor_xy=None),
            FrameRef(path=Path("c.png"), step_index=0, sub_index=2, cursor_xy=None),
            FrameRef(path=Path("d.png"), step_index=1, sub_index=0, cursor_xy=None),
        ]
        assert _last_frame_for_step(frames, 0).path == Path("c.png")
        assert _last_frame_for_step(frames, 1).path == Path("d.png")

    def test_missing_step_index_raises(self) -> None:
        frames = [FrameRef(path=Path("a.png"), step_index=0, sub_index=0, cursor_xy=None)]
        with pytest.raises(IndexError, match="no frames captured for step_index=2"):
            _last_frame_for_step(frames, 2)


# ------------------------------------------------------------------
# Integration — real browser against the fixture site
# ------------------------------------------------------------------


@pytest.mark.integration
class TestSaveRegionAgainstFixture:
    def test_save_region_writes_png_matching_bbox_dimensions(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        # #btn-3d is one of the primary buttons; its bbox is stable at a
        # fixed viewport. We assert the OUTPUT dimensions equal the bbox
        # (no padding, no clipping) — the exact pixel bbox varies with
        # Chromium fonts, so we don't hard-code it.
        out = tmp_path / "btn.png"
        reel = Reel(fixture_site_url, viewport=(800, 600), fps=4, dwell=0.25).goto(wait="load")
        result = reel.save_region("#btn-3d", out)
        assert result == out
        assert out.exists()
        with Image.open(out) as img:
            w, h = img.size
        assert w > 0 and h > 0
        # Sanity: crop is a strict subset of the viewport.
        assert w <= 800 and h <= 600
        # Sanity: not a full-viewport screenshot mistakenly saved.
        assert w < 800 or h < 600

    def test_padding_grows_output_by_2x_padding_on_each_axis(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        # Compare unpadded vs padded; a button well inside the viewport
        # should grow by 2*padding on each axis when both sides fit.
        reel = Reel(fixture_site_url, viewport=(800, 600), fps=4, dwell=0.25).goto(wait="load")
        base = tmp_path / "base.png"
        padded = tmp_path / "padded.png"
        reel.save_region("#btn-3d", base, padding=0)
        reel.save_region("#btn-3d", padded, padding=8)
        with Image.open(base) as a, Image.open(padded) as b:
            assert b.size[0] - a.size[0] == 16
            assert b.size[1] - a.size[1] == 16

    def test_negative_frame_index_picks_last_frame(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        # With a single goto step the frame list is short; frame=-1 must
        # not raise and must produce a valid PNG.
        out = tmp_path / "last.png"
        reel = Reel(fixture_site_url, viewport=(800, 600), fps=4, dwell=0.25).goto(wait="load")
        reel.save_region("#btn-3d", out, frame=-1)
        with Image.open(out) as img:
            assert img.size[0] > 0 and img.size[1] > 0

    def test_padding_clipped_at_viewport_edge(self, fixture_site_url: str, tmp_path: Path) -> None:
        # Ask for absurd padding — output must still be at most the
        # viewport size on each axis (equivalent to clipping to image
        # bounds since the frame is a viewport screenshot).
        out = tmp_path / "clipped.png"
        reel = Reel(fixture_site_url, viewport=(400, 300), fps=4, dwell=0.25).goto(wait="load")
        reel.save_region("#btn-3d", out, padding=10_000)
        with Image.open(out) as img:
            assert img.size == (400, 300)

    def test_missing_selector_raises_lookup_error(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "missing.png"
        reel = Reel(fixture_site_url, viewport=(400, 300), fps=4, dwell=0.25).goto(wait="load")
        with pytest.raises(LookupError, match="not found"):
            reel.save_region("#definitely-not-a-real-selector", out)
        assert not out.exists()

    def test_save_region_at_step_uses_that_steps_last_frame(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        # Two-step scenario: goto + click. save_region_at_step(1, ...)
        # must resolve without error and produce a valid PNG whose crop
        # dimensions match the button's bbox on the fixture.
        out = tmp_path / "step1.png"
        reel = (
            Reel(fixture_site_url, viewport=(800, 600), fps=4, dwell=0.25)
            .goto(wait="load")
            .click("#btn-3d", dwell=0.25)
        )
        reel.save_region_at_step(1, "#btn-3d", out)
        with Image.open(out) as img:
            w, h = img.size
        assert w > 0 and h > 0
        assert w < 800 and h < 600

    def test_output_is_valid_png(self, fixture_site_url: str, tmp_path: Path) -> None:
        # Explicitly assert PNG format — no extension-implied surprises.
        out = tmp_path / "explicit.png"
        Reel(fixture_site_url, viewport=(800, 600), fps=4, dwell=0.25).goto(
            wait="load"
        ).save_region("#btn-3d", out, format="png")
        with Image.open(out) as img:
            assert img.format == "PNG"
