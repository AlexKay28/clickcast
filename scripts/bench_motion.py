#!/usr/bin/env python3
"""Measure motion continuity in a rendered reel.

The headline question for human-facing output is not "how many frames?" but
"how many of those frames are actually new?". A 30 fps file that holds every
frame for half a second is a slideshow with a frame rate.

This script decodes any GIF/MP4/WebP via ffmpeg and reports, over consecutive
frame pairs:

* ``unique_pct``   — share of frames that differ from their predecessor
* ``dup_pct``      — share that are (near-)identical, i.e. held frames
* ``mean_diff``    — mean absolute luminance difference (0-255 scale)

``--threshold`` sets the per-pixel-mean cutoff below which a pair counts as a
duplicate (default 0.5/255, i.e. imperceptible).

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


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # imageio-ffmpeg ships a static binary; clickcast already depends on it
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment specific
        raise SystemExit(f"ffmpeg is required to measure motion: {exc}") from exc


def _ffprobe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if exe:
        out = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,avg_frame_rate,nb_frames",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0:
            return json.loads(out.stdout)

    # Fallback: derive geometry from the decoded stream itself (no ffprobe).
    return {}


def _dims(path: Path) -> tuple[int, int]:
    """Frame geometry, from ffprobe when available and a 1-frame decode otherwise."""
    probe = _ffprobe(path)
    stream = (probe.get("streams") or [{}])[0]
    if stream.get("width") and stream.get("height"):
        return int(stream["width"]), int(stream["height"])

    # No ffprobe: ask ffmpeg for one frame and infer the aspect from the raw
    # size by trying the most common viewport widths (the caller only needs a
    # shape that splits the buffer evenly, and the encode side is always the
    # same canvas as the capture viewport).
    out = subprocess.run(
        [
            _ffmpeg(),
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
    )
    size = len(out.stdout)
    for width, height in ((1280, 800), (1280, 720), (1920, 1080), (800, 600)):
        if width * height == size:
            return width, height
    raise SystemExit("cannot determine frame geometry (install ffprobe or use a standard viewport)")


def measure(path: str | Path, *, threshold: float = 0.5) -> MotionStats:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"no such file: {path}")

    probe = _ffprobe(path)
    stream = (probe.get("streams") or [{}])[0]
    width, height = _dims(path)

    raw = subprocess.run(
        [
            _ffmpeg(),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
    ).stdout

    size = width * height
    count = len(raw) // size
    if count < 2:
        raise SystemExit(f"{path}: needs at least two frames")

    diffs: list[float] = []
    prev = raw[0:size]
    for i in range(1, count):
        cur = raw[i * size : (i + 1) * size]
        # Mean absolute difference, accumulated on a coarse grid: a full-width
        # row sum is enough for a duplicate/no-duplicate verdict and keeps the
        # script stdlib-only (no numpy).
        total = 0
        for off in range(0, size, 7):
            a = prev[off]
            b = cur[off]
            total += a - b if a > b else b - a
        samples = len(range(0, size, 7))
        diffs.append(total / samples)
        prev = cur

    duplicates = sum(1 for d in diffs if d < threshold)
    mean_diff = sum(diffs) / len(diffs)

    fps = 0.0
    if stream.get("avg_frame_rate") and stream["avg_frame_rate"] != "0/0":
        num, _, den = stream["avg_frame_rate"].partition("/")
        fps = float(num) / float(den or 1)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)

    return MotionStats(
        path=str(path),
        frames=count,
        duplicates=duplicates,
        unique_pct=round(100 * (count - 1 - duplicates) / (count - 1), 1),
        dup_pct=round(100 * duplicates / (count - 1), 1),
        mean_diff=round(mean_diff, 3),
        fps=round(fps, 2),
        duration_s=round(duration, 3),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("media", nargs="+", help="GIF/MP4/WebP reel(s) to measure")
    parser.add_argument(
        "--threshold", type=float, default=0.5, help="duplicate cutoff, 0-255 (default 0.5)"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--fail-under", type=float, default=None, help="exit 1 below this unique_pct"
    )
    args = parser.parse_args(argv)

    results = [measure(p, threshold=args.threshold) for p in args.media]

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
