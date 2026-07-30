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

    ``target_bbox`` is the ``(x, y, width, height)`` of the resolved click
    target, captured pre-click. When set (and ``AnnotateConfig.target_highlight``
    is on), the annotator draws a pulsing ring around the bbox on the pre-click
    frame(s) of this step — the sub-frames BEFORE the ripple fires. See
    #129 Track A.
    """

    label: str | None = None
    click_at: tuple[int, int] | None = None
    target_bbox: tuple[int, int, int, int] | None = None


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
    # Build the ordered list of every step's label — the actions-panel needs
    # the full history to render "the last N with current highlighted". Steps
    # without a label render as an empty row (rare in practice — auto pipeline
    # always sets one).
    all_labels: list[str] = [
        (steps.get(i, StepAnnotation()).label or "…") for i in range(total_steps)
    ]

    # Per-step: how many pre-click sub-frames precede the first post-click
    # frame. Pre-click frames are those where the recorder wrote
    # ``cursor_xy=None`` (pre_action + pre_action_pad); the first frame with
    # a non-None cursor_xy is the actual click frame. Post-click sub-indices
    # start at that offset. Ripple stages are counted from the click frame,
    # not from sub_index=0 — otherwise pre-click padding would consume every
    # ripple stage silently.
    pre_click_count: dict[int, int] = {}
    for f in frames:
        idx = f["step_index"]
        if idx in pre_click_count:
            continue
        # Walk the step's sub-frames in order; count leading Nones.
        if f["cursor_xy"] is None:
            pre_click_count[idx] = pre_click_count.get(idx, 0) + 1

    # Second pass to correctly count LEADING Nones per step (the naive
    # increment above overcounts if cursor_xy is None post-click too — e.g.
    # scroll/goto steps). Reset and walk deterministically:
    pre_click_count.clear()
    for idx in range(total_steps):
        count = 0
        for f in frames:
            if f["step_index"] != idx:
                continue
            if f["cursor_xy"] is not None:
                break
            count += 1
        pre_click_count[idx] = count

    for entry in frames:
        step_index = entry["step_index"]
        sub_index = entry["sub_index"]
        cursor_xy_raw = entry.get("cursor_xy")
        cursor_xy = tuple(cursor_xy_raw) if cursor_xy_raw else None
        step_ann = steps.get(step_index, StepAnnotation())
        pre_n = pre_click_count.get(step_index, 0)
        # Ripple only fires on the first N POST-CLICK sub-frames (offset by
        # pre-click padding count so highlight-ring frames don't consume it).
        if step_ann.click_at is not None:
            post_offset = sub_index - pre_n
            ripple_stage = post_offset + 1 if 0 <= post_offset < ann.config.ripple.stages else 0
        else:
            ripple_stage = 0
        # Target highlight — draw on pre-click sub-frames only (sub_index
        # < pre_n), which is where the recorder held the target still.
        target_bbox: tuple[int, int, int, int] | None = None
        target_pulse_phase = 0.0
        if step_ann.target_bbox is not None and step_ann.click_at is not None and sub_index < pre_n:
            target_bbox = step_ann.target_bbox
            # Distribute pulse phase evenly across the pre-click frames.
            target_pulse_phase = sub_index / max(pre_n - 1, 1) if pre_n > 1 else 0.0
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
            all_labels=all_labels,
            target_bbox=target_bbox,
            target_pulse_phase=target_pulse_phase,
        )
    return len(frames)
