"""Tests for the title + summary card renderers (#129 Track E)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from clickcast.annotate import (
    CardStyle,
    SummaryStats,
    render_summary_card,
    render_title_card,
)


class TestRenderTitleCard:
    def test_writes_requested_frame_count(self, tmp_path: Path) -> None:
        paths = render_title_card(
            tmp_path,
            title="clickcast tour · example.com",
            subtitle="https://example.com",
            frame_count=8,
        )
        assert len(paths) == 8
        for p in paths:
            assert p.exists()
            assert p.parent == tmp_path

    def test_all_frames_have_requested_size(self, tmp_path: Path) -> None:
        size = (960, 540)
        paths = render_title_card(
            tmp_path,
            title="hi",
            size=size,
            frame_count=3,
        )
        for p in paths:
            with Image.open(p) as img:
                assert img.size == size

    def test_frames_are_byte_identical_copies(self, tmp_path: Path) -> None:
        # We intentionally hold a static card for N frames — no animation.
        paths = render_title_card(
            tmp_path,
            title="hello",
            frame_count=4,
        )
        first = paths[0].read_bytes()
        for p in paths[1:]:
            assert p.read_bytes() == first, "card frames must be identical copies"

    def test_rejects_nonpositive_frame_count(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError):
            render_title_card(tmp_path, title="hi", frame_count=0)

    def test_bg_color_visible_at_corner(self, tmp_path: Path) -> None:
        # The corner pixel should be exactly the requested bg color — text
        # only renders near the centre, and there's no anti-aliasing at the
        # frame edges.
        style = CardStyle(bg_color=(11, 22, 33))
        paths = render_title_card(
            tmp_path,
            title="hi",
            size=(200, 100),
            frame_count=1,
            style=style,
        )
        with Image.open(paths[0]) as img:
            assert img.convert("RGB").getpixel((0, 0)) == (11, 22, 33)
            assert img.convert("RGB").getpixel((199, 99)) == (11, 22, 33)


class TestRenderSummaryCard:
    def test_writes_requested_frame_count(self, tmp_path: Path) -> None:
        stats = SummaryStats(pages=3, clicks=7, duration_s=12.4)
        paths = render_summary_card(tmp_path, stats=stats, frame_count=10)
        assert len(paths) == 10

    def test_singular_vs_plural_labels(self, tmp_path: Path) -> None:
        # Just exercise both branches — the assertion is that the renderer
        # doesn't raise on either singular or plural counts.
        for pages, clicks in [(1, 1), (2, 2), (1, 5), (5, 1)]:
            stats = SummaryStats(pages=pages, clicks=clicks, duration_s=1.0)
            paths = render_summary_card(tmp_path, stats=stats, frame_count=1)
            assert paths and paths[0].exists()

    def test_all_frames_have_requested_size(self, tmp_path: Path) -> None:
        stats = SummaryStats(pages=1, clicks=1, duration_s=1.0)
        size = (800, 450)
        paths = render_summary_card(tmp_path, stats=stats, size=size, frame_count=2)
        for p in paths:
            with Image.open(p) as img:
                assert img.size == size

    def test_watermark_does_not_change_frame_count(self, tmp_path: Path) -> None:
        # Watermark is a rendering concern — frame count is caller-driven.
        stats = SummaryStats(pages=1, clicks=1, duration_s=1.0, watermark="clickcast v0.2.1")
        paths = render_summary_card(tmp_path, stats=stats, frame_count=4)
        assert len(paths) == 4

    def test_extra_lines_render_without_error(self, tmp_path: Path) -> None:
        stats = SummaryStats(
            pages=1,
            clicks=1,
            duration_s=1.0,
            extra=("0 errors", "chromium/1280x800"),
        )
        paths = render_summary_card(tmp_path, stats=stats, frame_count=1)
        assert paths and paths[0].exists()
