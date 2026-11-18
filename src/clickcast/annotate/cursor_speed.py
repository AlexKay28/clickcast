"""Cap how fast the synthetic cursor may travel between recorded positions.

Screencast captures are continuous in *page* time, but ``cursor_xy`` is only
known per step: the recorder stamps the target point once the action
completes. Consecutive frames therefore differ by a whole step's travel —
a click-to-click jump of several hundred pixels can land in a single
``1 / fps`` frame, which reads as the pointer teleporting (the "speed of
light" problem) rather than moving.

This pass makes the cursor legible without touching the page footage:

1. **Fill forward.** A frame with ``cursor_xy=None`` inherits the last known
   position. Pre-action frames of a step currently drop the cursor entirely;
   carrying it forward keeps a stable pointer on screen while the viewer
   waits for the next action.
2. **Insert glide frames.** Where consecutive frames still exceed
   :attr:`~clickcast.annotate.annotator.CursorStyle.max_speed_px_s` pixels
   per second, copy the earlier frame and ease the cursor through the gap.
   Inserted frames reuse the earlier frame's pixels — the page is static
   during a pointer move, so only cursor metadata changes.

The duration grows by the inserted glide frames. That is deliberate: the
alternative is either a teleport or moving the pointer *before* the dwell it
belongs to. Added frames are drag-free (each carries a distinct cursor
position), so they do not regress the duplicate-frame metric.

The pass is idempotent (``cursor_speed_capped`` in the manifest) and a strict
no-op when ``max_speed_px_s`` is ``None`` or the cursor is not tracked.
"""

from __future__ import annotations

import itertools
import json
import math
import shutil
from pathlib import Path
from typing import Any

from clickcast.annotate.annotator import DEFAULT_MAX_SPEED_PX_S
from clickcast.annotate.smooth import _write_manifest_atomic

__all__ = ["DEFAULT_MAX_SPEED_PX_S", "cap_cursor_speed"]

# Peak derivative of ``3t² - 2t³`` — sizing the gap by the average would let
# the midpoint of the glide run 1.5x over the stated bound.
_SMOOTHSTEP_PEAK = 1.5


def cap_cursor_speed(
    frames_dir: Path,
    *,
    max_speed_px_s: float | None,
    fps: int,
) -> int:
    """Bound cursor motion to ``max_speed_px_s``. Returns inserted frame count.

    Silently no-ops when the manifest is missing/empty, when
    ``max_speed_px_s`` is ``None`` or non-positive, when ``fps`` is not
    positive, or when the pass already ran on this directory.
    """
    if max_speed_px_s is None or max_speed_px_s <= 0 or fps <= 0:
        return 0
    manifest_path = frames_dir / "frames.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text())
    frames: list[dict[str, Any]] = manifest.get("frames", [])
    if len(frames) < 2:
        return 0
    if manifest.get("cursor_speed_capped"):
        return 0

    # 1. Carry the last known pointer position through untracked frames.
    last: list[int] | None = None
    filled = False
    for frame in frames:
        cursor = frame.get("cursor_xy")
        if cursor is None:
            if last is not None:
                frame["cursor_xy"] = list(last)
                filled = True
        else:
            last = cursor

    # 2. Glide across any remaining over-speed hop.
    budget = max_speed_px_s / fps
    # Leave headroom for per-frame integer rounding of the eased positions.
    step_budget = budget * 0.95
    insertions: dict[int, list[dict[str, Any]]] = {}
    for i, (earlier, target) in enumerate(itertools.pairwise(frames)):
        pa = earlier.get("cursor_xy")
        pb = target.get("cursor_xy")
        if pa is None or pb is None:
            continue
        distance = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
        if distance <= budget:
            continue
        count = math.ceil(distance * _SMOOTHSTEP_PEAK / step_budget) - 1
        if count <= 0:
            continue
        source = frames_dir / earlier["path"]
        stem = Path(earlier["path"]).stem
        entries: list[dict[str, Any]] = []
        for k in range(1, count + 1):
            t = k / (count + 1)
            eased = 3 * t * t - 2 * t * t * t
            name = f"{stem}-c{k:02d}.png"
            shutil.copy2(source, frames_dir / name)
            entry = dict(earlier)
            entry.update(
                path=name,
                sub_index=0,
                cursor_xy=[
                    round(pa[0] + (pb[0] - pa[0]) * eased),
                    round(pa[1] + (pb[1] - pa[1]) * eased),
                ],
            )
            entries.append(entry)
        insertions[i] = entries

    inserted = sum(len(entries) for entries in insertions.values())
    if not filled and not inserted:
        return 0

    new_frames: list[dict[str, Any]] = []
    for i, entry in enumerate(frames):
        new_frames.append(entry)
        new_frames.extend(insertions.get(i, ()))

    # Dense per-step sub-index, matching interpolate_cursor_motion so ripple
    # stages still key off the original post-action frames.
    counters: dict[int, int] = {}
    for frame in new_frames:
        step = int(frame["step_index"])
        frame["sub_index"] = counters.get(step, 0)
        counters[step] = frame["sub_index"] + 1

    manifest["frames"] = new_frames
    manifest["count"] = len(new_frames)
    manifest["cursor_speed_capped"] = True
    manifest["cursor_max_speed_px_s"] = max_speed_px_s
    _write_manifest_atomic(manifest_path, manifest)
    return inserted
