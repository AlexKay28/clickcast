"""Tests for the deterministic reel-grammar pass (#302)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from clickcast.annotate.reel_grammar import (
    GrammarConfig,
    GrammarResult,
    apply_reel_grammar,
)

OFF = GrammarConfig(
    click_feedback=False,
    transitions=False,
    captions=False,
    timeline=False,
)


def _entry(
    path: str,
    step: int,
    sub: int,
    cursor: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "step_index": step,
        "sub_index": sub,
        "cursor_xy": cursor,
    }


def _build(
    frames_dir: Path,
    entries: list[dict[str, Any]],
    *,
    colors: dict[str, tuple[int, int, int]] | None = None,
    size: tuple[int, int] = (240, 160),
) -> Path:
    frames_dir.mkdir(parents=True, exist_ok=True)
    colors = colors or {}
    for index, entry in enumerate(entries):
        Image.new(
            "RGB",
            size,
            colors.get(entry["path"], (20 + index * 7, 30, 40)),
        ).save(frames_dir / entry["path"], format="PNG")
    (frames_dir / "frames.json").write_text(
        json.dumps({"fps": 12, "count": len(entries), "frames": entries}),
        encoding="utf-8",
    )
    return frames_dir


def _click_entries() -> list[dict[str, Any]]:
    return [
        _entry("frame-0000-000.png", 0, 0),
        _entry("frame-0000-001.png", 0, 1),
        _entry("frame-0000-002.png", 0, 2, [120, 80]),
        _entry("frame-0000-003.png", 0, 3, [120, 80]),
        _entry("frame-0000-004.png", 0, 4, [120, 80]),
        _entry("frame-0000-005.png", 0, 5, [120, 80]),
    ]


def _two_step_entries() -> list[dict[str, Any]]:
    return [
        _entry("frame-0000-000.png", 0, 0),
        _entry("frame-0000-001.png", 0, 1),
        _entry("frame-0000-002.png", 0, 2),
        _entry("frame-0001-000.png", 1, 0),
        _entry("frame-0001-001.png", 1, 1),
        _entry("frame-0001-002.png", 1, 2),
    ]


def _two_step_colors() -> dict[str, tuple[int, int, int]]:
    return {
        "frame-0000-000.png": (200, 30, 30),
        "frame-0000-001.png": (190, 40, 40),
        "frame-0000-002.png": (180, 50, 50),
        "frame-0001-000.png": (30, 30, 200),
        "frame-0001-001.png": (40, 40, 190),
        "frame-0001-002.png": (50, 50, 180),
    }


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


def _diff_bbox(first: Path, second: Path) -> tuple[int, int, int, int] | None:
    with Image.open(first) as a, Image.open(second) as b:
        return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()


def _diff_mass(first: Path, second: Path) -> int:
    with Image.open(first) as a, Image.open(second) as b:
        diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    return int(sum(ImageStat.Stat(diff).sum))


class TestNoOp:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        assert apply_reel_grammar(tmp_path) == GrammarResult()

    def test_empty_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "frames.json").write_text(json.dumps({"frames": []}), encoding="utf-8")
        assert apply_reel_grammar(tmp_path) == GrammarResult()

    def test_all_disabled_is_byte_identical(self, tmp_path: Path) -> None:
        frames_dir = _build(tmp_path / "frames", _click_entries())
        manifest_before = _bytes(frames_dir / "frames.json")
        before = {path.name: _bytes(path) for path in frames_dir.glob("*.png")}

        result = apply_reel_grammar(frames_dir, OFF)

        assert result == GrammarResult(frames=6)
        assert {path.name: _bytes(path) for path in frames_dir.glob("*.png")} == before
        assert _bytes(frames_dir / "frames.json") == manifest_before


class TestClickFeedback:
    def test_ripple_and_press_change_frames(self, tmp_path: Path) -> None:
        baseline = _build(tmp_path / "baseline", _click_entries())
        graded = _build(tmp_path / "graded", _click_entries())
        apply_reel_grammar(baseline, OFF)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=True,
                transitions=False,
                captions=False,
                timeline=False,
            ),
        )

        assert result.ripple_frames == 3
        assert result.press_frames == 1
        # Press cue on the frame before the cursor appears; ripple on the
        # three frames after it; the rest is untouched.
        for name in ("frame-0000-000.png", "frame-0000-005.png"):
            assert _bytes(graded / name) == _bytes(baseline / name)
        for name in (
            "frame-0000-001.png",
            "frame-0000-002.png",
            "frame-0000-003.png",
            "frame-0000-004.png",
        ):
            assert _bytes(graded / name) != _bytes(baseline / name)

    def test_ripple_is_concentrated_around_cursor(self, tmp_path: Path) -> None:
        baseline = _build(tmp_path / "baseline", _click_entries())
        graded = _build(tmp_path / "graded", _click_entries())
        apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=True,
                transitions=False,
                captions=False,
                timeline=False,
            ),
        )
        cx, cy = 120, 80
        for name in ("frame-0000-002.png", "frame-0000-003.png", "frame-0000-004.png"):
            bbox = _diff_bbox(baseline / name, graded / name)
            assert bbox is not None
            left, top, right, bottom = bbox
            assert cx - 64 <= left and right <= cx + 64
            assert cy - 64 <= top and bottom <= cy + 64

    def test_no_cursor_no_feedback(self, tmp_path: Path) -> None:
        entries = _click_entries()
        for entry in entries:
            entry["cursor_xy"] = None
        baseline = _build(tmp_path / "baseline", entries)
        graded = _build(tmp_path / "graded", entries)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=True,
                transitions=False,
                captions=False,
                timeline=False,
            ),
        )
        assert result.ripple_frames == 0
        assert result.press_frames == 0
        assert {p.name: _bytes(p) for p in graded.glob("*.png")} == {
            p.name: _bytes(p) for p in baseline.glob("*.png")
        }


class TestTransitionGrammar:
    def test_same_url_matches_dissolve_baseline(self, tmp_path: Path) -> None:
        baseline = _build(tmp_path / "baseline", _two_step_entries(), colors=_two_step_colors())
        graded = _build(tmp_path / "graded", _two_step_entries(), colors=_two_step_colors())
        apply_reel_grammar(baseline, OFF)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=False,
                transitions=True,
                captions=False,
                timeline=False,
            ),
            step_urls=["https://example.test/docs", "https://example.test/docs"],
        )
        assert result.slide_transitions == 0
        assert result.dissolve_transitions == 1
        assert {p.name: _bytes(p) for p in graded.glob("*.png")} == {
            p.name: _bytes(p) for p in baseline.glob("*.png")
        }

    def test_changed_url_slides_boundary(self, tmp_path: Path) -> None:
        baseline = _build(tmp_path / "baseline", _two_step_entries(), colors=_two_step_colors())
        graded = _build(tmp_path / "graded", _two_step_entries(), colors=_two_step_colors())
        apply_reel_grammar(baseline, OFF)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=False,
                transitions=True,
                captions=False,
                timeline=False,
            ),
            step_urls=["https://example.test/docs", "https://example.test/pricing"],
        )

        assert result.slide_transitions == 1
        assert result.dissolve_transitions == 0
        # Two slide stages (step length 3 → count = 2); the last frame of the
        # step is the hold and must stay untouched.
        assert _bytes(graded / "frame-0001-000.png") != _bytes(baseline / "frame-0001-000.png")
        assert _bytes(graded / "frame-0001-001.png") != _bytes(baseline / "frame-0001-001.png")
        assert _bytes(graded / "frame-0001-002.png") == _bytes(baseline / "frame-0001-002.png")
        assert _bytes(graded / "frame-0000-000.png") == _bytes(baseline / "frame-0000-000.png")

    def test_no_url_information_falls_back_to_dissolve(self, tmp_path: Path) -> None:
        baseline = _build(tmp_path / "baseline", _two_step_entries(), colors=_two_step_colors())
        graded = _build(tmp_path / "graded", _two_step_entries(), colors=_two_step_colors())
        apply_reel_grammar(baseline, OFF)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=False,
                transitions=True,
                captions=False,
                timeline=False,
            ),
        )
        assert result.slide_transitions == 0
        assert result.dissolve_transitions == 1
        assert {p.name: _bytes(p) for p in graded.glob("*.png")} == {
            p.name: _bytes(p) for p in baseline.glob("*.png")
        }


class TestCaptions:
    def test_caption_only_on_labelled_step(self, tmp_path: Path) -> None:
        size = (320, 240)
        baseline = _build(tmp_path / "baseline", _two_step_entries(), size=size)
        graded = _build(tmp_path / "graded", _two_step_entries(), size=size)
        apply_reel_grammar(baseline, OFF)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=False,
                transitions=False,
                captions=True,
                timeline=False,
            ),
            step_labels=["Open settings", None],
        )

        assert result.caption_frames == 3
        for name in (
            "frame-0000-000.png",
            "frame-0000-001.png",
            "frame-0000-002.png",
        ):
            bbox = _diff_bbox(baseline / name, graded / name)
            assert bbox is not None
            # Lower third: the plate begins below 2/3 of the frame height.
            assert bbox[1] >= 2 * 240 // 3
        for name in (
            "frame-0001-000.png",
            "frame-0001-001.png",
            "frame-0001-002.png",
        ):
            assert _bytes(graded / name) == _bytes(baseline / name)

    def test_caption_fades_in(self, tmp_path: Path) -> None:
        size = (320, 240)
        baseline = _build(tmp_path / "baseline", _two_step_entries(), size=size)
        graded = _build(tmp_path / "graded", _two_step_entries(), size=size)
        apply_reel_grammar(baseline, OFF)
        apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=False,
                transitions=False,
                captions=True,
                timeline=False,
            ),
            step_labels=["Open settings", None],
        )
        first = _diff_mass(baseline / "frame-0000-000.png", graded / "frame-0000-000.png")
        settled = _diff_mass(baseline / "frame-0000-002.png", graded / "frame-0000-002.png")
        assert 0 < first < settled

    def test_empty_label_is_ignored(self, tmp_path: Path) -> None:
        baseline = _build(tmp_path / "baseline", _two_step_entries())
        graded = _build(tmp_path / "graded", _two_step_entries())
        apply_reel_grammar(baseline, OFF)
        result = apply_reel_grammar(
            graded,
            GrammarConfig(
                click_feedback=False,
                transitions=False,
                captions=True,
                timeline=False,
            ),
            step_labels=["   ", None],
        )
        assert result.caption_frames == 0
        assert {p.name: _bytes(p) for p in graded.glob("*.png")} == {
            p.name: _bytes(p) for p in baseline.glob("*.png")
        }


class TestTimeline:
    def test_filled_width_grows_with_frame_index(self, tmp_path: Path) -> None:
        entries = [_entry(f"frame-0000-{i:03d}.png", 0, i) for i in range(8)]
        frames_dir = _build(
            tmp_path / "frames",
            entries,
            colors={entry["path"]: (0, 0, 0) for entry in entries},
        )
        result = apply_reel_grammar(
            frames_dir,
            GrammarConfig(
                click_feedback=False,
                transitions=False,
                captions=False,
                timeline=True,
                timeline_height=4,
            ),
        )
        assert result.timeline_frames == 8

        widths: list[int] = []
        with Image.open(frames_dir / entries[0]["path"]) as probe:
            width, height = probe.size
        for entry in entries:
            with Image.open(frames_dir / entry["path"]) as image:
                row = image.convert("L").crop((0, height - 1, width, height))
                widths.append(sum(1 for value in row.tobytes() if value > 150))
        assert widths == sorted(widths)
        assert widths[0] < widths[-1]
        assert widths[-1] == width


class TestContract:
    def test_names_and_frame_count_unchanged(self, tmp_path: Path) -> None:
        frames_dir = _build(tmp_path / "frames", _two_step_entries(), colors=_two_step_colors())
        manifest_before = _bytes(frames_dir / "frames.json")
        names_before = sorted(path.name for path in frames_dir.glob("*.png"))

        apply_reel_grammar(
            frames_dir,
            step_urls=["https://example.test/a", "https://example.test/b"],
            step_labels=["Home", "Pricing"],
        )

        assert sorted(path.name for path in frames_dir.glob("*.png")) == names_before
        assert _bytes(frames_dir / "frames.json") == manifest_before
        assert len(json.loads(_bytes(frames_dir / "frames.json"))["frames"]) == 6

    def test_two_runs_are_byte_identical(self, tmp_path: Path) -> None:
        first = _build(tmp_path / "first", _two_step_entries(), colors=_two_step_colors())
        second = _build(tmp_path / "second", _two_step_entries(), colors=_two_step_colors())

        for frames_dir in (first, second):
            apply_reel_grammar(
                frames_dir,
                GrammarConfig(),
                step_urls=["https://example.test/a", "https://example.test/b"],
                step_labels=["Home", "Pricing"],
            )

        first_bytes = {p.name: _bytes(p) for p in sorted(first.glob("*"))}
        second_bytes = {p.name: _bytes(p) for p in sorted(second.glob("*"))}
        assert first_bytes == second_bytes

    def test_full_result_counts(self, tmp_path: Path) -> None:
        frames_dir = _build(tmp_path / "frames", _click_entries())
        result = apply_reel_grammar(
            frames_dir,
            GrammarConfig(),
            step_labels=["Click me"],
        )
        assert result.frames == 6
        assert result.ripple_frames == 3
        assert result.press_frames == 1
        assert result.caption_frames == 6
        assert result.timeline_frames == 6


def test_missing_frame_files_are_a_no_op(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "frames.json").write_text(
        json.dumps({"frames": [_entry("missing.png", 0, 0, [10, 10])]}),
        encoding="utf-8",
    )
    assert apply_reel_grammar(frames_dir) == GrammarResult(frames=1)
    assert not (frames_dir / "missing.png").exists()
