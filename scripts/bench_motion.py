#!/usr/bin/env python3
"""Measure motion continuity in a rendered reel.

The headline question for human-facing output is not "how many frames?" but
"how many of those frames are actually new?". A 30 fps file that holds every
frame for half a second is a slideshow with a frame rate.

This script decodes any GIF/MP4/WebP via imageio (Pillow for GIF, the bundled
ffmpeg for video) and reports, over consecutive frame pairs:

* ``unique_pct``   — share of frames that differ from their predecessor
* ``dup_pct``      — share that are held frames (essentially nothing changed)
* ``mean_diff``    — mean absolute luminance difference (0-255 scale)

A pair counts as a duplicate when fewer than ``--min-changed`` of its pixels
changed by more than ``--pixel-delta`` levels (defaults: 0.01% of the frame,
4/255 — above H.264 noise on a held frame, below a drawn ring). The whole-frame
mean is deliberately *not* the criterion: a cursor
move or a click ripple repaints a few hundred pixels, which is real motion but
averages away to ~0.07/255, so averaging calls that static and quietly rewards
slideshows.

Usage::

    python scripts/bench_motion.py tour.mp4
    python scripts/bench_motion.py a.mp4 b.gif --json

Exit code is 0 unless ``--fail-under`` is given and ``unique_pct`` misses it,
which makes the script usable as an acceptance gate.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import imageio.v3 as iio
import numpy as np

__all__ = ["MotionStats", "measure"]


@dataclass(frozen=True)
class MotionStats:
    path: str
    frames: int
    duplicates: int
    unique_pct: float
    dup_pct: float
    mean_diff: float
    fps: float
    duration_s: float


def _timing(path: Path) -> tuple[float, float]:
    """``(fps, duration_s)`` from ffprobe, or ``(0, 0)`` when it is absent."""
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0, 0.0
    out = subprocess.run(
        [
            exe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,nb_frames",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return 0.0, 0.0
    probe = json.loads(out.stdout)
    stream = (probe.get("streams") or [{}])[0]

    fps = 0.0
    rate = stream.get("avg_frame_rate") or "0/0"
    num, _, den = rate.partition("/")
    if float(den or 1) > 0:
        fps = float(num) / float(den or 1)

    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    return fps, duration


def _luma(frame: np.ndarray) -> np.ndarray:
    """Collapse an RGB/RGBA/grayscale frame to float luminance."""
    if frame.ndim == 2:
        return frame.astype(np.float32)
    if frame.shape[-1] >= 3:
        return frame[..., :3].astype(np.float32).mean(axis=2)
    return frame[..., 0].astype(np.float32)


def measure(path: str | Path, *, min_changed: float = 0.0001, pixel_delta: int = 2) -> MotionStats:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"no such file: {path}")

    count = 0
    diffs: list[float] = []
    moved: list[float] = []
    previous: np.ndarray | None = None
    try:
        for frame in iio.imiter(path):
            gray = _luma(frame)
            count += 1
            if previous is not None:
                delta = np.abs(gray - previous)
                diffs.append(float(delta.mean()))
                moved.append(float((delta > pixel_delta).mean()))
            previous = gray
    except Exception as exc:  # pragma: no cover - codec/plugin specific
        raise SystemExit(f"{path}: cannot decode frames: {exc}") from exc

    if count < 2:
        raise SystemExit(f"{path}: needs at least two frames")

    duplicates = sum(1 for share in moved if share < min_changed)
    fps, duration = _timing(path)

    return MotionStats(
        path=str(path),
        frames=count,
        duplicates=duplicates,
        unique_pct=round(100 * (count - 1 - duplicates) / (count - 1), 1),
        dup_pct=round(100 * duplicates / (count - 1), 1),
        mean_diff=round(sum(diffs) / len(diffs), 3),
        fps=round(fps, 2),
        duration_s=round(duration, 3),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("media", nargs="+", help="GIF/MP4/WebP reel(s) to measure")
    parser.add_argument(
        "--min-changed",
        type=float,
        default=0.0001,
        help="share of changed pixels below which a pair is a duplicate (default 0.01%%)",
    )
    parser.add_argument(
        "--pixel-delta",
        type=int,
        default=4,
        help="per-pixel luminance change that counts as motion, 0-255 (default 4)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--fail-under", type=float, default=None, help="exit 1 below this unique_pct"
    )
    args = parser.parse_args(argv)

    results = [
        measure(p, min_changed=args.min_changed, pixel_delta=args.pixel_delta) for p in args.media
    ]

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        header = (
            f"{'file':<28} {'frames':>7} {'fps':>6} {'unique%':>8} {'dup%':>7} {'mean diff':>10}"
        )
        print(header)
        print("-" * len(header))
        for r in results:
            print(
                f"{Path(r.path).name:<28} {r.frames:>7} {r.fps:>6.1f} "
                f"{r.unique_pct:>8.1f} {r.dup_pct:>7.1f} {r.mean_diff:>10.3f}"
            )

    if args.fail_under is not None:
        worst = min(r.unique_pct for r in results)
        if worst < args.fail_under:
            print(f"\nFAIL: unique_pct {worst} < {args.fail_under}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
