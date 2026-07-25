"""Post-capture cursor-motion interpolation. Ships #75.

Between any two consecutive frames whose ``cursor_xy`` differs by at least
:attr:`~clickcast.annotate.annotator.CursorStyle.interpolate_min_distance`
pixels, insert :attr:`~clickcast.annotate.annotator.CursorStyle.interpolate_frames`
intermediate frames with the cursor at eased positions between the two. The
inserted frames are physical PNG copies of the earlier frame (page pixels are
identical — the ACTION has already happened; only the cursor position is
metadata), with the manifest rewritten so the annotator and encoder pick up
the expanded sequence transparently.

Runs **after** :func:`clickcast.annotate.zoom.apply_zoom_on_click` and
**before** :func:`clickcast.annotate.pipeline.annotate_frames_dir` in the
:func:`clickcast.auto.run_tour` pipeline.

Design notes:

- Frames with ``cursor_xy=None`` (pre-action screenshots, goto/scroll steps)
  are stepped over when locating pair endpoints — the point is to smooth the
  cursor's visual motion, and None frames represent moments where the cursor
  is not tracked.
- Inserted frames inherit the **earlier** frame's ``step_index``. This keeps
  the actions-panel highlight (which reads ``step_index``) stable during the
  glide instead of jumping ahead to the next step before its action has run.
- After insertion, ``sub_index`` is renumbered densely per step in traversal
  order, so ripple-stage checks (``sub_index < ripple.stages``) still fire on
  the original post-click frames rather than the interp tail.
"""

from __future__ import annotations

import itertools
import json
import shutil
from pathlib import Path
from typing import Any, Literal

from clickcast.annotate.annotator import CursorStyle

__all__ = ["interpolate_cursor_motion"]


def interpolate_cursor_motion(frames_dir: Path, config: CursorStyle) -> int:
    """Insert intermediate frames to smooth cursor motion. Returns count inserted.

    Silently no-op when the manifest is missing/empty, when interpolation is
    disabled on ``config``, or when no pair of frames exceeds the minimum
    distance threshold.
    """
    if not config.interpolate or config.interpolate_frames <= 0:
        return 0
    manifest_path = frames_dir / "frames.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text())
    frames: list[dict[str, Any]] = manifest.get("frames", [])
    if len(frames) < 2:
        return 0

    cursor_indices = [i for i, f in enumerate(frames) if f.get("cursor_xy")]
    if len(cursor_indices) < 2:
        return 0

    min_dist_sq = config.interpolate_min_distance * config.interpolate_min_distance
    insertions: dict[int, list[dict[str, Any]]] = {}
    for a, b in itertools.pairwise(cursor_indices):
        prev = frames[a]
        curr = frames[b]
        pa = prev["cursor_xy"]
        pb = curr["cursor_xy"]
        dx = pb[0] - pa[0]
        dy = pb[1] - pa[1]
        if dx * dx + dy * dy < min_dist_sq:
            continue
        src_path = frames_dir / prev["path"]
        stem = Path(prev["path"]).stem
        new_entries: list[dict[str, Any]] = []
        n = config.interpolate_frames
        for k in range(1, n + 1):
            t = k / (n + 1)
            t_eased = _ease(t, config.interpolate_easing)
            ix = round(pa[0] + (pb[0] - pa[0]) * t_eased)
            iy = round(pa[1] + (pb[1] - pa[1]) * t_eased)
            new_name = f"{stem}-i{k:02d}.png"
            shutil.copy2(src_path, frames_dir / new_name)
            new_entries.append(
                {
                    "path": new_name,
                    "step_index": prev["step_index"],
                    "sub_index": 0,
                    "cursor_xy": [ix, iy],
                }
            )
        insertions[a] = new_entries

    if not insertions:
        return 0

    new_frames: list[dict[str, Any]] = []
    for i, entry in enumerate(frames):
        new_frames.append(entry)
        if i in insertions:
            new_frames.extend(insertions[i])

    step_counters: dict[int, int] = {}
    for f in new_frames:
        si = int(f["step_index"])
        f["sub_index"] = step_counters.get(si, 0)
        step_counters[si] = f["sub_index"] + 1

    manifest["frames"] = new_frames
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return sum(len(v) for v in insertions.values())


def _ease(t: float, mode: Literal["linear", "ease-in-out"]) -> float:
    if mode == "linear":
        return t
    return 3 * t * t - 2 * t * t * t
