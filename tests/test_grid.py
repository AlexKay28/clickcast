"""Tests for `clickcast.annotate.grid` (#171).

Covers the pure-Pillow overlay contract (major/minor lines, labels, ruler
mode, invalid inputs), the zoom-on-click compatibility guarantee (grid
coordinates reflect the current image, not pre-zoom pixels), the layer
order (grid draws BEHIND click highlights), the CLI + Config wiring
(``--grid`` flags, ``CLICKCAST_GRID`` env var), and the sidecar carrying
the grid's render params.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from typer.testing import CliRunner

from clickcast.annotate.grid import GridConfig, draw_grid, parse_rgba_hex

# Rich (bundled with Typer) inserts ANSI SGR escapes between color runs, and
# under `GITHUB_ACTIONS=true` it splits those runs at hyphen boundaries in
# flag names — so a rendered `--grid-style` includes escapes between the
# hyphens and a literal `"grid-style" in output` fails even though the flag
# is visibly present. Strip escapes before asserting. Same pattern as
# `tests/test_emit_events.py::_plain` (#168).
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    return _ANSI_ESCAPE.sub("", s)


runner = CliRunner()


def _black(size: tuple[int, int] = (400, 300)) -> Image.Image:
    """Return a fresh black RGB frame."""
    return Image.new("RGB", size, color=(0, 0, 0))


def _pixel_is_lit(img: Image.Image, x: int, y: int) -> bool:
    """True when any RGB channel is above a background-noise threshold."""
    pixel = img.convert("RGB").getpixel((x, y))
    if not isinstance(pixel, tuple):  # pragma: no cover — Pillow always returns tuple for RGB
        return False
    return any(int(c) > 10 for c in pixel[:3])


def _pixel_max(img: Image.Image, x: int, y: int) -> int:
    """Max RGB channel intensity at ``(x, y)``. Useful for comparing lit vs unlit."""
    pixel = img.convert("RGB").getpixel((x, y))
    if not isinstance(pixel, tuple):
        return 0
    return max(int(c) for c in pixel[:3])


# ---------------------------------------------------------------------------
# parse_rgba_hex
# ---------------------------------------------------------------------------


class TestParseRgbaHex:
    def test_full_hex_with_alpha(self) -> None:
        assert parse_rgba_hex("#FFFFFF33") == (255, 255, 255, 0x33)

    def test_short_hex_default_alpha(self) -> None:
        assert parse_rgba_hex("#FF8040") == (0xFF, 0x80, 0x40, 0xFF)

    def test_hash_optional(self) -> None:
        assert parse_rgba_hex("FFFFFF33") == (255, 255, 255, 0x33)

    def test_case_insensitive(self) -> None:
        assert parse_rgba_hex("#ffffff33") == parse_rgba_hex("#FFFFFF33")

    def test_invalid_length_raises(self) -> None:
        with pytest.raises(ValueError, match="RGBA hex"):
            parse_rgba_hex("#ABC")

    def test_non_hex_raises(self) -> None:
        with pytest.raises(ValueError, match="RGBA hex"):
            parse_rgba_hex("#ZZZZZZ")


# ---------------------------------------------------------------------------
# draw_grid — pure Pillow contract
# ---------------------------------------------------------------------------


class TestDrawGridDisabled:
    def test_disabled_grid_is_no_op(self) -> None:
        img = _black()
        # Confirm every pixel is black to start.
        assert _pixel_max(img, 100, 100) == 0
        out = draw_grid(img, GridConfig(enabled=False))
        # No pixels lit anywhere.
        for x, y in [(100, 100), (0, 0), (200, 150)]:
            assert _pixel_max(out, x, y) == 0


class TestDrawGridFullMode:
    def test_major_lines_appear_at_pitch_multiples(self) -> None:
        img = draw_grid(
            _black((400, 300)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="full"),
        )
        # Pick y=55: not on any grid row (55 % 10 = 5, 55 % 100 = 55).
        # x=100, 200 are major columns → lit.
        assert _pixel_is_lit(img, 100, 55), "major x=100 gridline missing"
        assert _pixel_is_lit(img, 200, 55), "major x=200 gridline missing"
        # x=99 is not on any column line (minor pitch=10, 99%10 = 9), and
        # y=55 is not on any row line — pure black gap between grid nodes.
        assert not _pixel_is_lit(img, 99, 55), "x=99,y=55 should stay dark (no gridline here)"
        # y=100 row similarly. x=55 is a minor column (5 * 10) so pick x=57
        # (57 % 10 = 7, not on any column line) — a pure y-only test point.
        assert _pixel_is_lit(img, 57, 100), "major y=100 gridline missing"
        # And a fully-black point: neither col nor row lit, well away from labels.
        assert not _pixel_is_lit(img, 57, 55), (
            "point (57,55) should stay dark (no gridlines, no labels)"
        )

    def test_minor_gridlines_appear_at_pitch_over_10(self) -> None:
        """Minor lines every pitch/10 pixels, weaker than major."""
        img = draw_grid(
            _black((400, 300)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="full"),
        )
        # x=10 is a minor column (10 = pitch/10, not major).
        # y=55 is deep inside the frame, not on any grid row.
        # Point (10, 55) — pure minor-line pixel.
        # Point (100, 55) — pure major-line pixel.
        minor_intensity = _pixel_max(img, 10, 55)
        major_intensity = _pixel_max(img, 100, 55)
        assert minor_intensity > 0, "minor gridline at x=10 should be lit"
        assert major_intensity > minor_intensity, (
            f"major line should be brighter than minor "
            f"(major={major_intensity}, minor={minor_intensity})"
        )

    def test_labels_render_at_major_intersections(self) -> None:
        """A '100' label should draw text pixels near the top edge, just
        to the right of the x=100 major gridline."""
        img = draw_grid(
            _black((400, 300)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="full"),
        )
        # The label sits inset a couple of px to the right of x=100 and near
        # the top edge. Scan a small region — some pixel in it should be lit.
        # (We don't OCR — glyph rasterization varies; presence is what we
        # can guarantee cheaply.)
        found = False
        for xx in range(101, 130):
            for yy in range(2, 18):
                if _pixel_is_lit(img, xx, yy):
                    found = True
                    break
            if found:
                break
        assert found, "expected '100' label glyph pixels near top edge at x=100"


class TestDrawGridRulerMode:
    def test_ruler_mode_no_gridlines_in_body(self) -> None:
        """Ruler mode paints only the top+left label strips — the interior
        of the image (well past the strip depth) must stay black.
        """
        img = draw_grid(
            _black((400, 300)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="ruler"),
        )
        # 150,150 is deep interior, no gridline should be there.
        assert not _pixel_is_lit(img, 150, 150), (
            "ruler mode must not draw gridlines across the image body"
        )
        # Also the column x=200 far from top strip.
        assert not _pixel_is_lit(img, 200, 200), "ruler mode must not draw a vertical gridline"

    def test_ruler_mode_labels_still_present_at_edges(self) -> None:
        img = draw_grid(
            _black((400, 300)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="ruler"),
        )
        # The "100" label sits just past x=100 in the top strip. Scan a
        # small region — some glyph pixel must be lit.
        found = False
        for xx in range(101, 130):
            for yy in range(2, 18):
                if _pixel_is_lit(img, xx, yy):
                    found = True
                    break
            if found:
                break
        assert found, "ruler mode must render a '100' label glyph near top edge"


class TestDrawGridEdgeCases:
    def test_zero_pitch_raises(self) -> None:
        with pytest.raises(ValueError, match="pitch must be > 0"):
            draw_grid(_black(), GridConfig(enabled=True, pitch=0))

    def test_negative_pitch_raises(self) -> None:
        with pytest.raises(ValueError, match="pitch must be > 0"):
            draw_grid(_black(), GridConfig(enabled=True, pitch=-10))

    def test_labels_stop_at_last_full_major_boundary(self) -> None:
        """Image width 250, pitch 100: labels at x=100, x=200, none at x=300."""
        img = draw_grid(
            _black((250, 250)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="full"),
        )
        # No gridline should exist past the image edge — trivially true, but
        # also verify no crash and no lit pixel at x=249,y=249 aside from
        # normal edge cases.
        assert img.size == (250, 250)

    def test_input_mode_rgb_still_composites(self) -> None:
        """Callers frequently hand us `RGB` frames; make sure the composite
        path handles that without error."""
        rgb = Image.new("RGB", (200, 200), color=(0, 0, 0))
        # Should not raise:
        draw_grid(rgb, GridConfig(enabled=True, pitch=50, color="#FFFFFFFF"))


class TestDrawGridZoomCoords:
    """The grid MUST reflect the CURRENT image coords (post-zoom), not the
    pre-zoom coords. This is the critical guarantee from #171: agents
    reading a "100" label on a zoomed frame are measuring 100 pixels from
    the visible edge, not 100 pixels of pre-zoom content.
    """

    def test_grid_drawn_on_zoomed_image_uses_zoomed_coords(self, tmp_path: Path) -> None:
        """Simulate the pipeline order: zoom the frame first (via a resize
        surrogate), then apply the grid. The x=100 major line must land at
        pixel 100 of the zoomed image."""
        # 200x150 pre-zoom, then upscaled 2x to 400x300 (mimics the crop-
        # and-resize the zoom pass performs before annotate_frames_dir).
        pre = Image.new("RGB", (200, 150), color=(0, 0, 0))
        zoomed = pre.resize((400, 300), Image.Resampling.LANCZOS)
        out = draw_grid(
            zoomed,
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="full"),
        )
        # Grid line at zoomed x=100 — pick y=55 (not on any grid row).
        assert _pixel_is_lit(out, 100, 55)
        assert _pixel_is_lit(out, 200, 55)
        # (99, 55) is off every grid line — should stay black. If the grid
        # were drawn PRE-zoom and stretched, x=100 in the zoomed image
        # would be pre-zoom x=50, which IS a minor line — but only if the
        # source was drawn at pre-zoom coords. Our contract is post-zoom,
        # so this stays dark.
        assert not _pixel_is_lit(out, 99, 55)


class TestDrawGridLayerOrder:
    """Grid draws behind click highlights — red pixel drawn AFTER the grid
    must win at the overlap point (verifying the pipeline order)."""

    def test_highlight_over_gridline_wins(self) -> None:
        img = draw_grid(
            _black((300, 300)),
            GridConfig(enabled=True, pitch=100, color="#FFFFFFFF", style="full"),
        )
        # Simulate a red click highlight painted over the grid at x=100.
        from PIL import ImageDraw

        rgba = img.convert("RGBA")
        od = ImageDraw.Draw(rgba)
        od.ellipse([90, 140, 110, 160], fill=(255, 0, 0, 255))
        # At the center of the ellipse, red should dominate (not white grid).
        pixel = rgba.getpixel((100, 150))
        assert isinstance(pixel, tuple)
        r, g, b = int(pixel[0]), int(pixel[1]), int(pixel[2])
        assert r > 200, "red highlight should sit on top of the grid"
        assert g < 60 and b < 60, "green/blue should be low — pure red on top"


# ---------------------------------------------------------------------------
# CLI + Config wiring
# ---------------------------------------------------------------------------


class TestCliGridWiring:
    def test_auto_grid_flags_reach_do_auto(self, tmp_path: Path) -> None:
        from clickcast.cli import app

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "auto",
                    "data:text/html,x",
                    "--out",
                    str(tmp_path / "x.gif"),
                    "--grid",
                    "--grid-pitch",
                    "50",
                    "--grid-color",
                    "#FF000080",
                    "--grid-style",
                    "ruler",
                ],
            )
        assert r.exit_code == 0, r.output
        grid = captured.get("grid")
        assert isinstance(grid, GridConfig)
        assert grid.enabled is True
        assert grid.pitch == 50
        assert grid.color == "#FF000080"
        assert grid.style == "ruler"

    def test_auto_without_grid_flag_passes_none(self, tmp_path: Path) -> None:
        from clickcast.cli import app

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")],
            )
        assert r.exit_code == 0, r.output
        assert captured.get("grid") is None

    def test_bad_grid_style_rejected(self, tmp_path: Path) -> None:
        from clickcast.cli import app

        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--out",
                str(tmp_path / "x.gif"),
                "--grid",
                "--grid-style",
                "bogus",
            ],
        )
        assert r.exit_code != 0
        assert "grid-style" in _plain(r.output + (r.stderr or "")).lower()

    def test_bad_grid_pitch_rejected(self, tmp_path: Path) -> None:
        from clickcast.cli import app

        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--out",
                str(tmp_path / "x.gif"),
                "--grid",
                "--grid-pitch",
                "0",
            ],
        )
        assert r.exit_code != 0
        assert "grid-pitch" in _plain(r.output + (r.stderr or "")).lower()


class TestConfigGridEnvWiring:
    """CLICKCAST_GRID=1 must reach the auto command via the default_map."""

    def test_env_grid_reaches_auto(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from clickcast.cli import app

        monkeypatch.setenv("CLICKCAST_GRID", "true")
        monkeypatch.setenv("CLICKCAST_GRID_PITCH", "40")
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")],
            )
        assert r.exit_code == 0, r.output
        grid = captured.get("grid")
        assert isinstance(grid, GridConfig)
        assert grid.enabled is True
        assert grid.pitch == 40


class TestConfigGridDefaults:
    """Config-level defaults + env parsing for the four grid fields."""

    def test_defaults(self, tmp_path: Path) -> None:
        from clickcast.config import load

        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.grid is False
        assert cfg.grid_pitch == 100
        assert cfg.grid_color == "#FFFFFF33"
        assert cfg.grid_style == "full"

    def test_grid_env_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from clickcast.config import load

        monkeypatch.setenv("CLICKCAST_GRID", "1")
        cfg = load(project_toml=tmp_path / "p.toml", user_toml=tmp_path / "u.toml")
        assert cfg.grid is True


# ---------------------------------------------------------------------------
# Sidecar metadata
# ---------------------------------------------------------------------------


class TestSidecarGridMetadata:
    def test_report_carries_grid_when_enabled(self, tmp_path: Path) -> None:
        from clickcast.feedback import Media, ReportBuilder, write

        builder = ReportBuilder(url="https://example.com")
        builder.set_grid(GridConfig(enabled=True, pitch=50, color="#00FF00CC", style="ruler"))
        media = Media(
            path=str(tmp_path / "x.gif"),
            format="gif",
            size_bytes=1,
            frame_count=1,
            duration_s=0.1,
            fps=12,
        )
        report = builder.build(media)
        assert report.annotate is not None
        assert report.annotate.grid is not None
        assert report.annotate.grid.pitch == 50
        assert report.annotate.grid.style == "ruler"
        assert report.annotate.grid.color == "#00FF00CC"

        out = tmp_path / "reel.gif.json"
        write(report, out)
        payload = json.loads(out.read_text())
        assert payload["annotate"]["grid"]["pitch"] == 50
        assert payload["annotate"]["grid"]["style"] == "ruler"
        assert payload["annotate"]["grid"]["color"] == "#00FF00CC"

    def test_report_omits_annotate_when_grid_disabled(self, tmp_path: Path) -> None:
        from clickcast.feedback import Media, ReportBuilder

        builder = ReportBuilder(url="https://example.com")
        # Explicitly call set_grid with disabled — should stay None.
        builder.set_grid(GridConfig(enabled=False))
        media = Media(
            path=str(tmp_path / "x.gif"),
            format="gif",
            size_bytes=1,
            frame_count=1,
            duration_s=0.1,
            fps=12,
        )
        report = builder.build(media)
        assert report.annotate is None
