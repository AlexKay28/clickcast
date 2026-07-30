"""Tests for the pre-click target-highlight ring (#129 Track A).

Covers:

- The annotator draws a distinguishable ring at (roughly) the target bbox
  when ``AnnotateConfig.target_highlight=True`` and ``target_bbox`` is set.
- The ring is absent when the flag is off (default), even if the bbox is
  passed through — proves the flag actually gates the draw.
- The pipeline routes ``target_bbox`` only onto pre-click sub-frames, not
  onto the post-click (ripple) frames of the same step.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops

from clickcast.annotate import (
    AnnotateConfig,
    Annotator,
    StepAnnotation,
    TargetHighlightStyle,
    annotate_frames_dir,
)


def _make_frame(path: Path, size: tuple[int, int] = (400, 300)) -> Path:
    Image.new("RGB", size, color=(50, 80, 120)).save(path, format="PNG")
    return path


def _write_manifest(frames_dir: Path, entries: list[dict[str, object]], fps: int = 12) -> None:
    (frames_dir / "frames.json").write_text(
        json.dumps({"fps": fps, "count": len(entries), "frames": entries})
    )


class TestAnnotatorTargetHighlight:
    def test_ring_appears_at_bbox_when_flag_on(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(
            clicks=False,
            labels=False,
            cursor=False,
            progress=False,
            actions_panel=False,
            target_highlight=True,
            target=TargetHighlightStyle(padding=6, width=4),
        )
        bbox = (150, 100, 80, 40)
        out = Annotator(cfg).annotate(
            src,
            step_index=0,
            total_steps=1,
            target_bbox=bbox,
        )
        with Image.open(src) as base, Image.open(out) as anno:
            diff = ImageChops.difference(base.convert("RGB"), anno.convert("RGB")).getbbox()
        # Ring inflates bbox outward by padding=6 and draws a 4-px stroke, so
        # the diff bbox should straddle the target's centre and roughly cover
        # its inflated area.
        assert diff is not None, "expected ring pixels but frame was unchanged"
        # Ring center should land inside the diff bbox.
        cx, cy = bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2
        assert diff[0] <= cx <= diff[2]
        assert diff[1] <= cy <= diff[3]

    def test_ring_absent_when_flag_off(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(
            clicks=False,
            labels=False,
            cursor=False,
            progress=False,
            actions_panel=False,
            target_highlight=False,  # default
        )
        bbox = (150, 100, 80, 40)
        out = Annotator(cfg).annotate(
            src,
            step_index=0,
            total_steps=1,
            target_bbox=bbox,
        )
        with Image.open(src) as base, Image.open(out) as anno:
            diff = ImageChops.difference(base.convert("RGB"), anno.convert("RGB")).getbbox()
        # No layers on + flag off = pixel-identical, even with bbox given.
        assert diff is None, "target_highlight=False should NOT draw a ring"

    def test_no_bbox_no_ring(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(
            clicks=False,
            labels=False,
            cursor=False,
            progress=False,
            actions_panel=False,
            target_highlight=True,
        )
        out = Annotator(cfg).annotate(
            src,
            step_index=0,
            total_steps=1,
            target_bbox=None,
        )
        with Image.open(src) as base, Image.open(out) as anno:
            diff = ImageChops.difference(base.convert("RGB"), anno.convert("RGB")).getbbox()
        assert diff is None, "flag on but no bbox: nothing to draw"

    def test_pulse_phase_modulates_alpha(self, tmp_path: Path) -> None:
        # Phase 0 and phase 0.5 land at different points of the sine-based
        # alpha modulation. Rendering both onto the same frame should
        # produce visibly different pixel intensities in the ring region.
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(
            clicks=False,
            labels=False,
            cursor=False,
            progress=False,
            actions_panel=False,
            target_highlight=True,
            target=TargetHighlightStyle(pulse_count=1, alpha_min=40, alpha_max=250, padding=6),
        )
        bbox = (150, 100, 80, 40)
        a = Annotator(cfg).annotate(
            src,
            step_index=0,
            total_steps=1,
            target_bbox=bbox,
            target_pulse_phase=0.0,
            out_path=tmp_path / "a.png",
        )
        b = Annotator(cfg).annotate(
            src,
            step_index=0,
            total_steps=1,
            target_bbox=bbox,
            target_pulse_phase=0.5,
            out_path=tmp_path / "b.png",
        )
        # Same base frame, same bbox, different phase → different pixels.
        with Image.open(a) as ai, Image.open(b) as bi:
            diff = ImageChops.difference(ai.convert("RGB"), bi.convert("RGB")).getbbox()
        assert diff is not None, "pulse phase should change ring alpha"


class TestPipelineRoutesTargetBbox:
    def test_ring_only_on_pre_click_frames(self, tmp_path: Path) -> None:
        """One click step with 3 pre-click sub-frames + 3 post-click sub-frames.

        The pipeline should route ``target_bbox`` onto sub_indices 0-2 (pre-
        click) and NOT onto sub_indices 3-5 (post-click).
        """
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        # 3 pre-click frames (cursor_xy=None) then 3 post-click frames
        # (cursor_xy set). This mirrors the recorder's shape after a
        # pre_action + pre_action_pad(2) + post_action(dwell*fps=3).
        entries = [
            {"path": "frame-0000-000.png", "step_index": 0, "sub_index": 0, "cursor_xy": None},
            {"path": "frame-0000-001.png", "step_index": 0, "sub_index": 1, "cursor_xy": None},
            {"path": "frame-0000-002.png", "step_index": 0, "sub_index": 2, "cursor_xy": None},
            {
                "path": "frame-0000-003.png",
                "step_index": 0,
                "sub_index": 3,
                "cursor_xy": [200, 150],
            },
            {
                "path": "frame-0000-004.png",
                "step_index": 0,
                "sub_index": 4,
                "cursor_xy": [200, 150],
            },
            {
                "path": "frame-0000-005.png",
                "step_index": 0,
                "sub_index": 5,
                "cursor_xy": [200, 150],
            },
        ]
        for e in entries:
            _make_frame(frames_dir / e["path"])
        _write_manifest(frames_dir, entries)
        # Snapshot pre-annotation pixels in the ring region for each frame.
        bbox = (150, 100, 80, 40)
        cfg = AnnotateConfig(
            clicks=False,
            labels=False,
            cursor=False,
            progress=False,
            actions_panel=False,
            target_highlight=True,
        )
        # Take a copy of one clean frame (all frames are identical pre-annotate)
        # so we can compare each annotated frame back against it.
        clean = Image.open(frames_dir / "frame-0000-000.png").convert("RGB").copy()
        annotate_frames_dir(
            frames_dir,
            steps={0: StepAnnotation(label="click", click_at=(200, 150), target_bbox=bbox)},
            config=cfg,
        )

        def ring_present(name: str) -> bool:
            with Image.open(frames_dir / name) as a:
                diff = ImageChops.difference(clean, a.convert("RGB")).getbbox()
            return diff is not None

        assert ring_present("frame-0000-000.png"), "pre-click frame 0 should have ring"
        assert ring_present("frame-0000-001.png"), "pre-click frame 1 should have ring"
        assert ring_present("frame-0000-002.png"), "pre-click frame 2 should have ring"
        assert not ring_present("frame-0000-003.png"), "post-click frame 3 should NOT have ring"
        assert not ring_present("frame-0000-004.png"), "post-click frame 4 should NOT have ring"
        assert not ring_present("frame-0000-005.png"), "post-click frame 5 should NOT have ring"
