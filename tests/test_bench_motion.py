"""Guard ``scripts/bench_motion.py`` — the motion-continuity measurement.

The script is the acceptance gate for human-facing reels (#302, #303): it
decides whether output is a real video or a slideshow wearing a frame rate.
These tests build tiny synthetic reels with known motion and assert the
verdict, so the numbers quoted in the issues and docs stay trustworthy.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "bench_motion.py"


def _load_bench_motion():
    spec = importlib.util.spec_from_file_location("bench_motion", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve annotations through ``sys.modules[cls.__module__]``,
    # so the module has to be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench_motion()


def _write_gif(path: Path, frames: list[Image.Image], duration_ms: int = 40) -> None:
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
    )


@pytest.mark.unit
def _near_identical(count: int) -> list[Image.Image]:
    """Frames that differ by one pixel level — below any perceptible threshold.

    A GIF of *byte*-identical frames is collapsed to one frame by Pillow's
    writer, so the fixture nudges a single pixel instead: the reel still reads
    as static to the duplicate detector.
    """
    frames = []
    for index in range(count):
        image = Image.new("RGB", (64, 48), (30, 30, 30))
        image.putpixel((index, 0), (31, 30, 30))
        frames.append(image)
    return frames


@pytest.mark.unit
def test_static_frames_are_all_duplicates(tmp_path: Path) -> None:
    """A reel of effectively identical stills must report ~100% duplicates."""
    path = tmp_path / "static.gif"
    _write_gif(path, _near_identical(8))

    stats = bench.measure(path, threshold=0.5)

    assert stats.frames == 8
    assert stats.dup_pct == 100.0
    assert stats.unique_pct == 0.0


@pytest.mark.unit
def test_alternating_frames_are_all_unique(tmp_path: Path) -> None:
    """A reel where every frame differs must report 0% duplicates."""
    black = Image.new("RGB", (64, 48), (0, 0, 0))
    white = Image.new("RGB", (64, 48), (255, 255, 255))
    path = tmp_path / "alternating.gif"
    _write_gif(path, [black if i % 2 == 0 else white for i in range(8)])

    stats = bench.measure(path, threshold=0.5)

    assert stats.dup_pct == 0.0
    assert stats.unique_pct == 100.0
    assert stats.mean_diff > 100


@pytest.mark.unit
def test_fail_under_gate(tmp_path: Path) -> None:
    """``--fail-under`` turns the measurement into a CI gate."""
    path = tmp_path / "static.gif"
    _write_gif(path, _near_identical(6))

    assert bench.main([str(path), "--fail-under", "90"]) == 1
    assert bench.main([str(path), "--fail-under", "0"]) == 0


@pytest.mark.unit
def test_missing_file_is_a_clean_exit() -> None:
    with pytest.raises(SystemExit):
        bench.measure("/nonexistent/reel.mp4")
