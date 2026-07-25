"""Tests for :func:`clickcast.annotate.interpolate.interpolate_cursor_motion`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from clickcast.annotate import CursorStyle, interpolate_cursor_motion


def _write_frame(path: Path, color: tuple[int, int, int] = (10, 10, 10)) -> None:
    Image.new("RGB", (100, 100), color).save(path, format="PNG")


def _manifest(entries: list[dict]) -> dict:
    return {"frames": entries}


def _write_dir(
    tmp_path: Path, entries: list[dict], *, colors: dict[str, tuple[int, int, int]] | None = None
) -> Path:
    colors = colors or {}
    for e in entries:
        _write_frame(tmp_path / e["path"], colors.get(e["path"], (10, 10, 10)))
    (tmp_path / "frames.json").write_text(json.dumps(_manifest(entries)))
    return tmp_path


def _read_frames(frames_dir: Path) -> list[dict]:
    return json.loads((frames_dir / "frames.json").read_text())["frames"]


class TestNoOp:
    def test_missing_manifest_returns_zero(self, tmp_path: Path) -> None:
        assert interpolate_cursor_motion(tmp_path, CursorStyle()) == 0

    def test_empty_manifest_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / "frames.json").write_text(json.dumps(_manifest([])))
        assert interpolate_cursor_motion(tmp_path, CursorStyle()) == 0

    def test_disabled_returns_zero(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [10, 10]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 500]},
            ],
        )
        assert interpolate_cursor_motion(tmp_path, CursorStyle(interpolate=False)) == 0
        assert len(_read_frames(tmp_path)) == 2

    def test_zero_intermediate_frames_returns_zero(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [10, 10]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 500]},
            ],
        )
        assert interpolate_cursor_motion(tmp_path, CursorStyle(interpolate_frames=0)) == 0

    def test_all_pairs_below_min_distance_returns_zero(self, tmp_path: Path) -> None:
        # 20px apart, min is 50
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [100, 100]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [120, 100]},
            ],
        )
        assert interpolate_cursor_motion(tmp_path, CursorStyle(interpolate_min_distance=50)) == 0
        assert len(_read_frames(tmp_path)) == 2


class TestInterpolation:
    def test_inserts_n_frames_between_wide_pair(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [100, 100]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 100]},
            ],
        )
        inserted = interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=4, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        assert inserted == 4
        frames = _read_frames(tmp_path)
        assert len(frames) == 6  # 2 original + 4 inserted

    def test_linear_easing_matches_formula(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [0, 0]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 0]},
            ],
        )
        interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=4, interpolate_easing="linear", interpolate_min_distance=50
            ),
        )
        frames = _read_frames(tmp_path)
        interp = [
            f for f in frames if f["path"].endswith(("i01.png", "i02.png", "i03.png", "i04.png"))
        ]
        # 5 gaps → 100, 200, 300, 400
        xs = [f["cursor_xy"][0] for f in interp]
        assert xs == [100, 200, 300, 400]

    def test_ease_in_out_endpoints_closer_to_edges(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [0, 0]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 0]},
            ],
        )
        interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=4,
                interpolate_easing="ease-in-out",
                interpolate_min_distance=50,
            ),
        )
        frames = _read_frames(tmp_path)
        interp = sorted([f for f in frames if "-i" in f["path"]], key=lambda f: f["path"])
        xs = [f["cursor_xy"][0] for f in interp]
        # smoothstep(0.2)=0.104, smoothstep(0.4)=0.352, smoothstep(0.6)=0.648, smoothstep(0.8)=0.896
        assert xs == [52, 176, 324, 448]

    def test_inserted_frames_inherit_earlier_step_index(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 3, "sub_index": 5, "cursor_xy": [100, 100]},
                {"path": "b.png", "step_index": 4, "sub_index": 1, "cursor_xy": [500, 100]},
            ],
        )
        interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=2, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        frames = _read_frames(tmp_path)
        interp = [f for f in frames if "-i" in f["path"]]
        assert all(f["step_index"] == 3 for f in interp), "interp inherits earlier step_index"

    def test_skips_gaps_with_none_endpoint(self, tmp_path: Path) -> None:
        # Consecutive-pair reading: (A, None) skipped, (None, B) skipped.
        # But (A, B) matched by walking cursor-only frames — this is DESIRED.
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [50, 50]},
                {"path": "n.png", "step_index": 1, "sub_index": 0, "cursor_xy": None},
                {"path": "b.png", "step_index": 1, "sub_index": 1, "cursor_xy": [500, 50]},
            ],
        )
        inserted = interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=3, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        assert inserted == 3
        frames = _read_frames(tmp_path)
        assert len(frames) == 6

    def test_interp_frames_are_png_copies_of_earlier(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [100, 100]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 100]},
            ],
            colors={"a.png": (200, 30, 30), "b.png": (30, 30, 200)},
        )
        interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=2, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        with Image.open(tmp_path / "a-i01.png") as im:
            assert im.getpixel((10, 10)) == (200, 30, 30)  # copy of A, not B

    def test_sub_index_renumbered_dense_per_step(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "s0-a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [100, 100]},
                {"path": "s0-b.png", "step_index": 0, "sub_index": 1, "cursor_xy": [100, 100]},
                {"path": "s1-a.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 500]},
                {"path": "s1-b.png", "step_index": 1, "sub_index": 1, "cursor_xy": [500, 500]},
            ],
        )
        interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=3, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        frames = _read_frames(tmp_path)
        by_step: dict[int, list[int]] = {}
        for f in frames:
            by_step.setdefault(int(f["step_index"]), []).append(int(f["sub_index"]))
        for si, subs in by_step.items():
            assert subs == list(range(len(subs))), f"step {si} sub_index not dense: {subs}"

    def test_multi_gap_run(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [0, 0]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [400, 0]},
                {"path": "c.png", "step_index": 2, "sub_index": 0, "cursor_xy": [400, 400]},
            ],
        )
        inserted = interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=2, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        assert inserted == 4  # 2 gaps * 2 interp
        frames = _read_frames(tmp_path)
        assert len(frames) == 7

    def test_endpoints_preserved(self, tmp_path: Path) -> None:
        _write_dir(
            tmp_path,
            [
                {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [100, 200]},
                {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [500, 300]},
            ],
        )
        interpolate_cursor_motion(
            tmp_path,
            CursorStyle(
                interpolate_frames=3, interpolate_min_distance=50, interpolate_easing="linear"
            ),
        )
        frames = _read_frames(tmp_path)
        assert frames[0]["cursor_xy"] == [100, 200]
        assert frames[-1]["cursor_xy"] == [500, 300]


@pytest.mark.parametrize("mode", ["linear", "ease-in-out"])
def test_manifest_still_parseable(tmp_path: Path, mode: str) -> None:
    _write_dir(
        tmp_path,
        [
            {"path": "a.png", "step_index": 0, "sub_index": 0, "cursor_xy": [10, 10]},
            {"path": "b.png", "step_index": 1, "sub_index": 0, "cursor_xy": [400, 400]},
        ],
    )
    interpolate_cursor_motion(
        tmp_path,
        CursorStyle(interpolate_frames=2, interpolate_easing=mode, interpolate_min_distance=50),  # type: ignore[arg-type]
    )
    # Full round-trip through json parser + explicit shape check.
    data = json.loads((tmp_path / "frames.json").read_text())
    assert "frames" in data
    assert all({"path", "step_index", "sub_index", "cursor_xy"}.issubset(f) for f in data["frames"])
