"""One media pipeline, one place where encode FPS is decided.

Before this module, the record -> zoom -> smooth/interpolate -> annotate ->
bookends -> encode sequence existed twice (`auto.py` inline, `cli.py::_do_run`
inline) and not at all in the SDK or the MCP server. The copies drifted:

* `clickcast run` silently dropped ``render.quality`` and ``render.loop``
  because its ``encode()`` call never forwarded them (#256);
* `clickcast run` never interpolated cursor motion while ``auto`` always did;
* encode FPS was computed in three different places with three rules.

Every surface now builds a :class:`RenderPlan` and calls
:func:`render_reel`. The FPS decision lives in exactly one statement inside
this function, and ``encode()`` is called from exactly one place outside
``clickcast.encode``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from clickcast.annotate import (
    AnnotateConfig,
    CursorStyle,
    RegionAnnotation,
    StepAnnotation,
    annotate_frames_dir,
    apply_zoom_on_click,
    interpolate_cursor_motion,
)
from clickcast.annotate.smooth import SmoothConfig, smooth_frames_dir
from clickcast.encode import EncodeResult, Format, encode

__all__ = ["RenderPlan", "RenderResult", "render_reel"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderPlan:
    """Everything the render stage needs — nothing about how the tour was run.

    Built by each surface (CLI flags, scenario ``render:`` block, SDK kwargs),
    so a missing field is a type error at construction rather than a silently
    dropped option at encode time.
    """

    out: Path
    fps: int
    #: Duration-preserving retiming; supersedes cursor interpolation.
    smooth: bool = False
    video_fps: int = 30
    #: Interpolate cursor motion between captured frames (no-op for smooth).
    interpolate: bool = True
    cursor_style: CursorStyle = field(default_factory=CursorStyle)
    quality: int = 8
    loop: int = 0
    format: Format | None = None
    annotate: AnnotateConfig | None = None
    #: recorder step_index -> overlay; see `annotate.pipeline`.
    step_annotations: dict[int, StepAnnotation] = field(default_factory=dict)
    regions: Sequence[RegionAnnotation] = ()
    #: `auto --zoom-on-click`; None disables the crop-and-upscale pass.
    zoom_factor: float | None = None
    zoom_frames_after_click: int = 0


@dataclass(frozen=True)
class RenderResult:
    enc: EncodeResult
    #: The one encode rate the plan resolved to (capture fps, or the smoothed rate).
    encode_fps: int
    smoothed: bool


def render_reel(
    frames_dir: Path,
    plan: RenderPlan,
    *,
    before_encode: Callable[[Path], None] | None = None,
) -> RenderResult:
    """Run the whole media pipeline over ``frames_dir`` and encode ``plan.out``.

    ``before_encode`` is the seam for surface-specific frame mutation that is
    not part of the shared pipeline yet — today only ``auto``'s title/summary
    bookend cards, which need tour statistics (#256 tracks folding them in).
    It runs after annotation and before the single ``encode()`` call, exactly
    where the inline code used to do it.
    """
    if plan.zoom_factor is not None:
        zoomed = apply_zoom_on_click(
            frames_dir,
            factor=plan.zoom_factor,
            frames_after_click=plan.zoom_frames_after_click,
        )
        log.info(
            "zoomed %d frames (factor=%.1fx, %d frames after each click)",
            zoomed,
            plan.zoom_factor,
            plan.zoom_frames_after_click,
        )

    # The single encode-FPS decision in clickcast.
    encode_fps = plan.fps
    smoothed = False

    if plan.smooth:
        result = smooth_frames_dir(
            frames_dir,
            SmoothConfig(output_fps=plan.video_fps),
            source_fps=plan.fps,
        )
        encode_fps = result.output_fps
        smoothed = True
        log.info(
            "smoothed reel: +%d frames, %d total @ %d fps",
            result.inserted,
            result.total_frames,
            encode_fps,
        )
    elif plan.interpolate:
        inserted = interpolate_cursor_motion(frames_dir, plan.cursor_style)
        if inserted:
            log.info(
                "interpolated %d frames (n=%d per gap, easing=%s, min_distance=%d)",
                inserted,
                plan.cursor_style.interpolate_frames,
                plan.cursor_style.interpolate_easing,
                plan.cursor_style.interpolate_min_distance,
            )

    log.info("annotating frames...")
    annotate_frames_dir(
        frames_dir,
        steps=plan.step_annotations or None,
        config=plan.annotate,
        regions=list(plan.regions) or None,
    )

    if before_encode is not None:
        before_encode(frames_dir)

    log.info("encoding %s...", plan.out)
    enc = encode(
        frames_dir,
        plan.out,
        fps=encode_fps,
        quality=plan.quality,
        loop=plan.loop,
        format=plan.format,
    )
    return RenderResult(enc=enc, encode_fps=encode_fps, smoothed=smoothed)
