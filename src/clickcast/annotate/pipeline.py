"""Post-capture annotation pass — read ``frames.json`` and composite overlays in place.

The recorder produces raw PNG frames + a manifest. The annotator draws overlays
onto individual frames. This module walks the manifest and applies the
annotator to every frame, overwriting the file so the encoder picks up the
annotated version on its next pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from clickcast.annotate.annotator import AnnotateConfig, Annotator

__all__ = ["StepAnnotation", "annotate_frames_dir"]


@dataclass(slots=True, frozen=True)
class StepAnnotation:
    """Per-step annotation inputs.

    ``label`` shows in the bottom banner while the step is on screen. ``click_at``
    (if set) draws a fading ripple over the first ``ripple_stages`` sub-frames
    after the click.
    """

    label: str | None = None
    click_at: tuple[int, int] | None = None


def annotate_frames_dir(
    frames_dir: Path,
    *,
    steps: dict[int, StepAnnotation] | None = None,
    config: AnnotateConfig | None = None,
) -> int:
    """Composite overlays onto every frame in ``frames.json``, in place.

    Returns the number of frames annotated. Silently no-op if the manifest is
    missing or empty (the encoder will still work — it falls back to a sorted
    glob — just without overlays).
    """
    manifest_path = frames_dir / "frames.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text())
    frames = manifest.get("frames", [])
    if not frames:
        return 0

    total_steps = max(f["step_index"] for f in frames) + 1
    ann = Annotator(config)
    steps = steps or {}

    for entry in frames:
        step_index = entry["step_index"]
        sub_index = entry["sub_index"]
        cursor_xy_raw = entry.get("cursor_xy")
        cursor_xy = tuple(cursor_xy_raw) if cursor_xy_raw else None
        step_ann = steps.get(step_index, StepAnnotation())
        # Ripple only fires on the first N sub-frames after a click.
        if step_ann.click_at is not None and sub_index < ann.config.ripple_stages:
            ripple_stage = sub_index + 1
        else:
            ripple_stage = 0
        frame_path = frames_dir / entry["path"]
        ann.annotate(
            frame_path,
            out_path=frame_path,
            step_index=step_index,
            total_steps=total_steps,
            label=step_ann.label,
            cursor_xy=cursor_xy,
            click_at=step_ann.click_at,
            ripple_stage=ripple_stage,
        )
    return len(frames)
