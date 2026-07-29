"""Coverage for the sticky single-arrow mode + configurable panel position.

Both features are opt-in — defaults preserve the shipped v0.2.0 behaviour.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from clickcast.annotate import (
    ActionsPanelStyle,
    AnnotateConfig,
    Annotator,
    CursorStyle,
)
from clickcast.annotate.pipeline import StepAnnotation, annotate_frames_dir


def _make_frame(path: Path, size: tuple[int, int] = (600, 400)) -> Path:
    Image.new("RGB", size, color=(20, 20, 25)).save(path, format="PNG")
    return path


def _has_red(path: Path, box: tuple[int, int, int, int]) -> bool:
    with Image.open(path) as img:
        crop = img.crop(box).convert("RGB")
    return any(r > 150 and g < 100 and b < 100 for r, g, b in crop.getdata())


def _has_white(path: Path, box: tuple[int, int, int, int]) -> bool:
    """The panel bg default is (255, 255, 255, 230) — near-white after compose."""
    with Image.open(path) as img:
        crop = img.crop(box).convert("RGB")
    return any(r > 200 and g > 200 and b > 200 for r, g, b in crop.getdata())


class TestStickyArrow:
    """`CursorStyle.single_arrow=True` keeps the last-move arrow visible
    across every subsequent dwell frame until the next move — vs the default
    chain mode where the arrow disappears once history fills."""

    def _annotate_sequence(
        self,
        tmp_path: Path,
        positions: list[tuple[int, int] | None],
        cursor_style: CursorStyle,
    ) -> list[Path]:
        src = _make_frame(tmp_path / "base.png")
        ann = Annotator(AnnotateConfig(cursor_style=cursor_style))
        outs: list[Path] = []
        for i, pos in enumerate(positions):
            out = tmp_path / f"f{i:02d}.png"
            ann.annotate(src, out_path=out, cursor_xy=pos)
            outs.append(out)
        return outs

    def test_sticky_arrow_persists_across_dwell(self, tmp_path: Path) -> None:
        """Cursor moves A→B once, then dwells at B for many frames — arrow
        must remain visible on every dwell frame (not just the transition)."""
        positions: list[tuple[int, int] | None] = [
            # Dwell at A
            (100, 100), (100, 100), (100, 100),
            # Move to B
            (400, 100),
            # Dwell at B — arrow must persist across all of these
            (400, 100), (400, 100), (400, 100), (400, 100), (400, 100),
        ]  # fmt: skip
        outs = self._annotate_sequence(tmp_path, positions, CursorStyle(single_arrow=True))
        # A→B midway zone: any pixel row=95-110 between x=150 and x=380.
        arrow_box = (150, 95, 380, 110)
        # Dwell at A: no arrow yet (never moved).
        assert not _has_red(outs[2], arrow_box), "no arrow before any move"
        # First B frame: arrow visible.
        assert _has_red(outs[3], arrow_box), "arrow on move-detection frame"
        # Every subsequent dwell frame at B: arrow still visible.
        for i in range(4, len(positions)):
            assert _has_red(outs[i], arrow_box), (
                f"sticky arrow missing on dwell frame {i} — chain mode would drop it here"
            )

    def test_sticky_arrow_updates_on_next_move(self, tmp_path: Path) -> None:
        """Second move A→B→C: arrow endpoints must update to B→C, not stay A→B."""
        positions: list[tuple[int, int] | None] = [
            (100, 100),
            (400, 100),  # first move: A→B
            (400, 100),
            (400, 300),  # second move: B→C (drops vertically)
            (400, 300),
        ]
        outs = self._annotate_sequence(tmp_path, positions, CursorStyle(single_arrow=True))
        # After the B→C move, no red along the ORIGINAL A→B horizontal line
        # (except the segment near the shared point (400, 100)).
        assert not _has_red(outs[-1], (150, 95, 380, 110)), (
            "stale A→B arrow must be replaced when a new move is detected"
        )
        # And red DOES appear along the B→C vertical band.
        assert _has_red(outs[-1], (390, 150, 410, 280)), "expected new B→C arrow after second move"

    def test_default_is_chain_mode_backwards_compat(self, tmp_path: Path) -> None:
        """`single_arrow` defaults to False — chain mode preserved."""
        assert CursorStyle().single_arrow is False

    def test_none_cursor_does_not_clear_sticky_state(self, tmp_path: Path) -> None:
        """A `cursor_xy=None` frame (pre-action) between two cursor frames
        must NOT reset the sticky endpoints — otherwise the arrow would
        flicker off during the pre-action pause."""
        positions: list[tuple[int, int] | None] = [
            (100, 100),
            (400, 100),  # move: A→B — endpoints set
            None,  # pre_action for next step
            (400, 100),  # dwell continues at B
        ]
        outs = self._annotate_sequence(tmp_path, positions, CursorStyle(single_arrow=True))
        assert _has_red(outs[-1], (150, 95, 380, 110)), (
            "sticky arrow must survive a None-cursor frame in the middle"
        )

    def test_reset_cursor_clears_sticky_state(self, tmp_path: Path) -> None:
        """`Annotator.reset_cursor()` must clear all three sticky fields —
        otherwise the arrow would carry across a scenario boundary."""
        src = _make_frame(tmp_path / "base.png")
        ann = Annotator(AnnotateConfig(cursor_style=CursorStyle(single_arrow=True)))
        ann.annotate(src, out_path=tmp_path / "a.png", cursor_xy=(100, 100))
        ann.annotate(src, out_path=tmp_path / "b.png", cursor_xy=(400, 100))
        # Endpoints are now set.
        assert ann._sticky_arrow_from == (100, 100)
        assert ann._sticky_arrow_to == (400, 100)
        assert ann._sticky_last_cursor == (400, 100)
        ann.reset_cursor()
        assert ann._sticky_arrow_from is None
        assert ann._sticky_arrow_to is None
        assert ann._sticky_last_cursor is None


class TestActionsPanelPosition:
    """`ActionsPanelStyle.position` picks the corner. Panel bg is the same
    near-white RGBA across positions — test by looking for white pixels in
    the expected corner and their absence in the opposite corner."""

    def _annotate(self, tmp_path: Path, position: str) -> Path:
        src = _make_frame(tmp_path / "base.png", size=(600, 400))
        cfg = AnnotateConfig(panel=ActionsPanelStyle(position=position))  # type: ignore[arg-type]
        ann = Annotator(cfg)
        out = tmp_path / f"panel-{position}.png"
        ann.annotate(
            src,
            out_path=out,
            step_index=0,
            total_steps=1,
            label="tour",
            all_labels=["click Save"],
        )
        return out

    def test_top_right_default(self, tmp_path: Path) -> None:
        assert ActionsPanelStyle().position == "top-right"

    def test_top_right_panel_lives_in_top_right(self, tmp_path: Path) -> None:
        out = self._annotate(tmp_path, "top-right")
        assert _has_white(out, (450, 10, 590, 60)), "panel should be top-right"
        assert not _has_white(out, (10, 10, 100, 60)), "panel must not be top-left"
        assert not _has_white(out, (10, 340, 100, 390)), "panel must not be bottom-left"

    def test_top_left_panel_lives_in_top_left(self, tmp_path: Path) -> None:
        out = self._annotate(tmp_path, "top-left")
        assert _has_white(out, (10, 10, 150, 60)), "panel should be top-left"
        assert not _has_white(out, (450, 10, 590, 60)), "panel must not be top-right"

    def test_bottom_right_panel_lives_in_bottom_right(self, tmp_path: Path) -> None:
        out = self._annotate(tmp_path, "bottom-right")
        assert _has_white(out, (450, 340, 590, 390)), "panel should be bottom-right"
        assert not _has_white(out, (450, 10, 590, 60)), "panel must not be top-right"

    def test_bottom_left_panel_lives_in_bottom_left(self, tmp_path: Path) -> None:
        out = self._annotate(tmp_path, "bottom-left")
        assert _has_white(out, (10, 340, 150, 390)), "panel should be bottom-left"
        assert not _has_white(out, (10, 10, 150, 60)), "panel must not be top-left"


def test_pipeline_end_to_end_with_new_fields(tmp_path: Path) -> None:
    """`annotate_frames_dir` end-to-end sanity: sticky arrow + bottom-right
    panel work through the manifest pipeline, not just direct Annotator calls."""
    import json

    # Build a tiny frames dir with a manifest — mimics recorder output.
    for i in range(4):
        _make_frame(tmp_path / f"frame-0000-{i:03d}.png")

    manifest = {
        "frames": [
            {
                "path": f"frame-0000-{i:03d}.png",
                "step_index": 0,
                "sub_index": s,
                "cursor_xy": list(c),
            }
            for i, (c, s) in enumerate(
                [((100, 100), 0), ((400, 100), 1), ((400, 100), 2), ((400, 100), 3)]
            )
        ]
    }
    (tmp_path / "frames.json").write_text(json.dumps(manifest))

    cfg = AnnotateConfig(
        cursor_style=CursorStyle(single_arrow=True),
        panel=ActionsPanelStyle(position="bottom-right"),
    )
    n = annotate_frames_dir(
        tmp_path,
        steps={0: StepAnnotation(label="tour", click_at=(400, 100))},
        config=cfg,
    )
    assert n == 4
    # Last frame: sticky arrow visible + panel at bottom-right.
    last = tmp_path / "frame-0000-003.png"
    assert _has_red(last, (150, 95, 380, 110)), "sticky arrow on last dwell frame"
    assert _has_white(last, (450, 340, 590, 390)), "panel anchored bottom-right"
