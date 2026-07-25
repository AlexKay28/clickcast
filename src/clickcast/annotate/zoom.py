"""Post-capture zoom-on-click pass. Ships #74 Shape A.

For each recorded frame that immediately follows a click (identified by
``sub_index < frames_after_click`` on frames with ``cursor_xy`` set), crop
the frame around the click point and scale back to viewport size. The reel
jumps to a close-up for a beat, then returns to full-page for the next step.

This runs **before** :func:`clickcast.annotate.pipeline.annotate_frames_dir`
so overlays (ripple, cursor, label bar, actions panel) land at the correct
viewport coordinates for the cropped-and-upscaled frame. Both the annotator
and the encoder read ``frames.json`` and are agnostic to whether the pixels
came from a raw screenshot or a zoomed crop.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

__all__ = ["apply_zoom_on_click"]


def apply_zoom_on_click(
    frames_dir: Path,
    *,
    factor: float,
    frames_after_click: int,
) -> int:
    """Crop-and-upscale the frames captured right after each click.

    Silently no-op if the manifest is missing or empty.

    Returns the number of frames actually zoomed.
    """
    if factor <= 1.0:
        return 0
    manifest_path = frames_dir / "frames.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text())
    frames = manifest.get("frames", [])
    if not frames:
        return 0

    zoomed = 0
    for entry in frames:
        cursor_xy_raw = entry.get("cursor_xy")
        if cursor_xy_raw is None:
            continue
        if entry["sub_index"] >= frames_after_click:
            continue
        frame_path = frames_dir / entry["path"]
        _zoom_frame_in_place(frame_path, cursor_xy_raw, factor)
        zoomed += 1
    return zoomed


def _zoom_frame_in_place(
    frame_path: Path, cursor_xy: list[int] | tuple[int, int], factor: float
) -> None:
    """Crop ``frame_path`` around ``cursor_xy`` at 1/factor of viewport,
    resize back to viewport, overwrite the file. Preserves aspect ratio
    (crop uses same aspect as source). Clamps the crop box at frame edges
    by shifting (not shrinking), so output dimensions stay constant."""
    with Image.open(frame_path) as src:
        src.load()
        w, h = src.size
        cx, cy = int(cursor_xy[0]), int(cursor_xy[1])
        crop_w = max(1, int(w / factor))
        crop_h = max(1, int(h / factor))
        # Center the crop on cursor, then shift to keep within [0,w] by [0,h].
        left = cx - crop_w // 2
        top = cy - crop_h // 2
        left = max(0, min(left, w - crop_w))
        top = max(0, min(top, h - crop_h))
        right = left + crop_w
        bottom = top + crop_h
        cropped = src.crop((left, top, right, bottom))
        upscaled = cropped.resize((w, h), Image.Resampling.LANCZOS)
        upscaled.save(frame_path, format="PNG")
