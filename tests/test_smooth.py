"""Tests for post-capture duration-preserving frame smoothing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from clickcast.annotate.smooth import SmoothConfig, SmoothResult, smooth_frames_dir


def _frame(
    path: str,
    step: int,
    cursor: list[int] | None = None,
    *,
    sub_index: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "path": path,
        "step_index": step,
        "sub_index": sub_index,
        "cursor_xy": cursor,
        **extra,
    }


def _write_dir(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    *,
    colors: dict[str, tuple[int, int, int]] | None = None,
    sizes: dict[str, tuple[int, int]] | None = None,
) -> Path:
    colors = colors or {}
    sizes = sizes or {}
    for index, entry in enumerate(entries):
        path = tmp_path / entry["path"]
        Image.new(
            "RGB",
            sizes.get(entry["path"], (4, 4)),
            colors.get(entry["path"], (10 + index, 20, 30)),
        ).save(path, format="PNG")
    manifest = tmp_path / "frames.json"
    manifest.write_text(json.dumps({"capture": "test", "frames": entries}), encoding="utf-8")
    return manifest


def _read_manifest(frames_dir: Path) -> dict[str, Any]:
    return json.loads((frames_dir / "frames.json").read_text(encoding="utf-8"))


def _read_frames(frames_dir: Path) -> list[dict[str, Any]]:
    return _read_manifest(frames_dir)["frames"]


class TestNoOp:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        assert smooth_frames_dir(tmp_path, SmoothConfig(), source_fps=5) == SmoothResult(0, 0, 30)

    def test_empty_manifest(self, tmp_path: Path) -> None:
        manifest = _write_dir(tmp_path, [])
        before = manifest.read_bytes()
        result = smooth_frames_dir(tmp_path, SmoothConfig(), source_fps=5)
        assert result == SmoothResult(0, 0, 30)
        assert manifest.read_bytes() == before

    def test_disabled_preserves_manifest(self, tmp_path: Path) -> None:
        manifest = _write_dir(tmp_path, [_frame("a.png", 0, [0, 0]), _frame("b.png", 1, [300, 0])])
        before = manifest.read_bytes()
        result = smooth_frames_dir(tmp_path, SmoothConfig(enabled=False), source_fps=5)
        assert result == SmoothResult(0, 2, 30)
        assert manifest.read_bytes() == before

    @pytest.mark.parametrize(("source_fps", "output_fps"), [(30, 30), (60, 30)])
    def test_output_not_faster_than_source(
        self, tmp_path: Path, source_fps: int, output_fps: int
    ) -> None:
        manifest = _write_dir(tmp_path, [_frame("a.png", 0, [0, 0]), _frame("b.png", 1, [300, 0])])
        before = manifest.read_bytes()
        result = smooth_frames_dir(
            tmp_path, SmoothConfig(output_fps=output_fps), source_fps=source_fps
        )
        assert result == SmoothResult(0, 2, output_fps)
        assert manifest.read_bytes() == before

    def test_rounding_to_one_slot_does_not_rewrite(self, tmp_path: Path) -> None:
        manifest = _write_dir(tmp_path, [_frame("a.png", 0)])
        before = manifest.read_bytes()
        result = smooth_frames_dir(tmp_path, SmoothConfig(output_fps=21), source_fps=20)
        assert result == SmoothResult(0, 1, 21)
        assert manifest.read_bytes() == before

    @pytest.mark.parametrize(("source_fps", "output_fps"), [(0, 30), (-1, 30), (5, 0), (5, -30)])
    def test_fps_must_be_positive(self, tmp_path: Path, source_fps: int, output_fps: int) -> None:
        with pytest.raises(ValueError, match="fps must be positive"):
            smooth_frames_dir(tmp_path, SmoothConfig(output_fps=output_fps), source_fps=source_fps)


class TestDurationAndHolds:
    def test_integer_rate_conversion_preserves_duration(self, tmp_path: Path) -> None:
        entries = [_frame(f"{i}.png", i, cursor=None) for i in range(5)]
        _write_dir(tmp_path, entries)
        result = smooth_frames_dir(
            tmp_path,
            SmoothConfig(output_fps=30, cursor=False, transitions=False),
            source_fps=10,
        )
        frames = _read_frames(tmp_path)
        assert result == SmoothResult(inserted=10, total_frames=15, output_fps=30)
        assert result.total_frames / result.output_fps == pytest.approx(len(entries) / 10)
        assert [f["path"] for f in frames[::3]] == [e["path"] for e in entries]
        assert len({f["path"] for f in frames}) == len(frames)

    def test_fractional_rate_uses_bresenham_holds_without_dropping_dwells(
        self, tmp_path: Path
    ) -> None:
        entries = [_frame(f"{i}.png", i, cursor=None) for i in range(4)]
        _write_dir(tmp_path, entries)
        result = smooth_frames_dir(
            tmp_path,
            SmoothConfig(output_fps=25, cursor=False, transitions=False),
            source_fps=10,
        )
        frames = _read_frames(tmp_path)
        assert result.total_frames == 10
        assert result.total_frames / result.output_fps == pytest.approx(len(entries) / 10)
        # Slot runs are [2, 3, 2, 3], so every original survives and no long
        # dwell collapses merely because 25/10 is not an integer.
        assert [f["path"] for f in frames] == [
            "0.png",
            "0-s001.png",
            "1.png",
            "1-s001.png",
            "1-s002.png",
            "2.png",
            "2-s001.png",
            "3.png",
            "3-s001.png",
            "3-s002.png",
        ]
        for original in entries:
            original_position = next(
                i for i, f in enumerate(frames) if f["path"] == original["path"]
            )
            assert original_position in {0, 2, 5, 7}


class TestCursor:
    def test_eased_cursor_frames_replace_holds_and_extend_frame_sequence(
        self, tmp_path: Path
    ) -> None:
        _write_dir(
            tmp_path,
            [_frame("a.png", 0, [0, 0]), _frame("b.png", 1, [300, 0])],
            colors={"a.png": (200, 30, 30)},
        )
        result = smooth_frames_dir(
            tmp_path, SmoothConfig(output_fps=30, transitions=False), source_fps=5
        )
        frames = _read_frames(tmp_path)
        assert result == SmoothResult(inserted=10, total_frames=12, output_fps=30)
        assert result.total_frames / result.output_fps == pytest.approx(2 / 5)
        assert frames[0]["path"] == "a.png"
        assert frames[6]["path"] == "b.png"
        # Five repeat slots are available after A. Their smoothstep positions
        # use t=1/6..5/6; B's five remaining frames are ordinary holds.
        assert [f["cursor_xy"][0] for f in frames[1:6]] == [22, 78, 150, 222, 278]
        assert all(f["step_index"] == 0 for f in frames[1:6])
        assert all(f["cursor_xy"] == [300, 0] for f in frames[7:])
        with Image.open(tmp_path / frames[1]["path"]) as generated:
            assert generated.getpixel((0, 0)) == (200, 30, 30)

    def test_none_frames_are_skipped_as_endpoints_but_insertion_follows_earlier(
        self, tmp_path: Path
    ) -> None:
        _write_dir(
            tmp_path,
            [
                _frame("a.png", 0, [0, 0]),
                _frame("none.png", 1, None),
                _frame("b.png", 1, [300, 0]),
            ],
        )
        smooth_frames_dir(tmp_path, SmoothConfig(output_fps=30, transitions=False), source_fps=5)
        frames = _read_frames(tmp_path)
        assert [frames[i]["path"] for i in (0, 6, 12)] == ["a.png", "none.png", "b.png"]
        assert len(frames) == 18
        assert frames[1]["cursor_xy"] != [0, 0]
        assert all(f["cursor_xy"] is None for f in frames[7:12])

    def test_stationary_cursor_only_gets_ordinary_holds(self, tmp_path: Path) -> None:
        _write_dir(tmp_path, [_frame("a.png", 0, [20, 20]), _frame("b.png", 0, [20, 20])])
        smooth_frames_dir(tmp_path, SmoothConfig(output_fps=30, transitions=False), source_fps=5)
        assert all(f["cursor_xy"] == [20, 20] for f in _read_frames(tmp_path))


class TestTransitions:
    def test_crossfade_midpoint_and_original_target(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [_frame("a.png", 0), _frame("b.png", 1)],
            colors={"a.png": (255, 0, 0), "b.png": (0, 0, 255)},
        )
        smooth_frames_dir(
            tmp_path,
            SmoothConfig(output_fps=30, cursor=False, transition_ms=40),
            source_fps=5,
        )
        frames = _read_frames(tmp_path)
        assert len(frames) == 12
        assert frames[5]["cursor_xy"] is None
        assert frames[5]["step_index"] == 0
        with Image.open(tmp_path / frames[5]["path"]) as midpoint:
            assert midpoint.mode == "RGB"
            assert midpoint.getpixel((0, 0)) == (127, 0, 127)
        assert frames[6]["path"] == "b.png"
        with Image.open(tmp_path / frames[6]["path"]) as target:
            assert target.getpixel((0, 0)) == (0, 0, 255)

    def test_holds_precede_crossfade_instead_of_flashing_back_to_source(
        self, tmp_path: Path
    ) -> None:
        _write_dir(
            tmp_path,
            [_frame("a.png", 0), _frame("b.png", 1)],
            colors={"a.png": (255, 0, 0), "b.png": (0, 0, 255)},
        )
        smooth_frames_dir(
            tmp_path,
            SmoothConfig(output_fps=30, cursor=False, transition_ms=40),
            source_fps=5,
        )
        frames = _read_frames(tmp_path)
        assert [f["path"] for f in frames[1:5]] == [
            "a-s001.png",
            "a-s002.png",
            "a-s003.png",
            "a-s004.png",
        ]
        assert frames[5]["path"] == "a-s005.png"
        with Image.open(tmp_path / frames[5]["path"]) as last_before_target:
            assert last_before_target.getpixel((0, 0)) == (127, 0, 127)
        assert frames[6]["path"] == "b.png"

    def test_dimension_mismatch_resizes_target_to_source(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [_frame("a.png", 0), _frame("b.png", 1)],
            colors={"a.png": (255, 0, 0), "b.png": (0, 0, 255)},
            sizes={"a.png": (6, 4), "b.png": (2, 2)},
        )
        smooth_frames_dir(
            tmp_path,
            SmoothConfig(output_fps=30, cursor=False, transition_ms=40),
            source_fps=5,
        )
        generated = _read_frames(tmp_path)[5]["path"]
        with Image.open(tmp_path / generated) as midpoint:
            assert midpoint.size == (6, 4)
            assert midpoint.getpixel((3, 2)) == (127, 0, 127)

    def test_same_step_path_change_does_not_crossfade(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [_frame("a.png", 0), _frame("b.png", 0)],
            colors={"a.png": (255, 0, 0), "b.png": (0, 0, 255)},
        )
        smooth_frames_dir(tmp_path, SmoothConfig(output_fps=30, cursor=False), source_fps=5)
        frames = _read_frames(tmp_path)
        with Image.open(tmp_path / frames[1]["path"]) as hold:
            assert hold.getpixel((0, 0)) == (255, 0, 0)


class TestManifestAndLimits:
    def test_manifest_fields_unique_paths_subindices_and_name_collisions(
        self, tmp_path: Path
    ) -> None:
        originals = [
            _frame("a.png", 0, [0, 0], sub_index=8, label="first"),
            _frame("b.png", 0, [300, 0], sub_index=9, label="second"),
            _frame("c.png", 1, [300, 300], sub_index=4, label="third"),
        ]
        _write_dir(tmp_path, originals)
        Image.new("RGB", (4, 4), (1, 2, 3)).save(tmp_path / "a-s001.png")
        result = smooth_frames_dir(tmp_path, SmoothConfig(output_fps=30), source_fps=5)
        data = _read_manifest(tmp_path)
        frames = data["frames"]
        assert result.total_frames == len(frames) == 18
        assert data["capture"] == "test"
        assert len({f["path"] for f in frames}) == len(frames)
        assert [f for f in frames if f["path"] in {"a.png", "b.png", "c.png"}] == [
            {**originals[0], "sub_index": 0},
            {**originals[1], "sub_index": 6},
            {**originals[2], "sub_index": 0},
        ]
        generated = [f for f in frames if f["path"] not in {"a.png", "b.png", "c.png"}]
        assert all(re.fullmatch(r".+-s\d{3}\.png", f["path"]) for f in generated)
        assert "a-s001.png" not in {f["path"] for f in frames}
        assert all((tmp_path / f["path"]).is_file() for f in frames)
        with Image.open(tmp_path / "a-s001.png") as collision:
            assert collision.getpixel((0, 0)) == (1, 2, 3)
        by_step: dict[int, list[int]] = {}
        for frame in frames:
            by_step.setdefault(frame["step_index"], []).append(frame["sub_index"])
        assert all(subs == list(range(len(subs))) for subs in by_step.values())
        assert not list(tmp_path.glob(".frames-*.tmp"))

    def test_frame_cap_reduces_effects_and_holds_consistently(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                _frame("a.png", 0, [0, 0]),
                _frame("b.png", 1, [300, 0]),
                _frame("c.png", 2, [300, 300]),
            ],
        )
        result = smooth_frames_dir(
            tmp_path, SmoothConfig(output_fps=30, max_frames=7), source_fps=5
        )
        frames = _read_frames(tmp_path)
        assert result == SmoothResult(inserted=4, total_frames=7, output_fps=30)
        assert len(frames) == 7
        assert len({f["path"] for f in frames}) == 7
        assert [f["path"] for f in frames if not re.search(r"-s\d+", f["path"])] == [
            "a.png",
            "b.png",
            "c.png",
        ]
        assert all((tmp_path / f["path"]).is_file() for f in frames)

    def test_cap_with_no_repeat_slots_leaves_manifest_untouched(self, tmp_path: Path) -> None:
        manifest = _write_dir(
            tmp_path,
            [_frame("a.png", 0, [0, 0]), _frame("b.png", 1, [300, 0])],
        )
        before = manifest.read_bytes()
        result = smooth_frames_dir(
            tmp_path, SmoothConfig(output_fps=30, max_frames=2), source_fps=5
        )
        assert result == SmoothResult(inserted=0, total_frames=2, output_fps=30)
        assert manifest.read_bytes() == before
