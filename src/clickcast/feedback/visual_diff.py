"""Pixel-level visual diff between two runs' frames (#201).

``clickcast assertions --baseline`` (#112) answers "did the same steps run
with the same outcomes" — it's a STRUCTURAL diff (step count, action,
label, status, error counters) and deliberately never looks at a pixel.
This module is its companion: given two sidecars, pair up their steps,
pixel-diff the paired frames, and report a percent-changed + a list of
changed bounding regions per step. Structural diff catches "the flow
broke"; this catches "the flow ran fine but the button moved / the color
changed / the layout shifted."

Design note (#202)
===================

Frame pairing
-------------
Match run vs. baseline steps by **index first** — the common case, since
:func:`~clickcast.feedback.assertions.build_assertions` already assumes
the same scenario produces the same step sequence run-to-run. When the two
sidecars have a DIFFERENT step count, fall back to pairing by ``label``:
walk the run steps in order, matching each to the first not-yet-used
baseline step with the same label (preferring the same index among
candidates, to keep the common "one step got inserted/removed in the
middle" case sane). A step on either side left unpaired is **not** silently
dropped — it lands in :attr:`VisualDiffReport.unmatched_steps` with a
``reason`` ("no baseline counterpart" / "no run counterpart"), so an agent
reading the report can tell "this step never got compared" apart from "this
step compared clean." A step that DOES pair up but has no captured frame on
one side (frame file missing on disk, or the step recorded 0 frames) is
also flagged unmatched (reason "missing frame") rather than crashing or
reporting a fake 0%.

Pixel diff + threshold + region grouping
-----------------------------------------
Pillow-only (matches ``annotate/grid.py``'s "pure-Pillow module, no
dependency changes" pattern (#171) — no numpy, no OpenCV):

1. ``ImageChops.difference`` between the two RGB frames, then the per-pixel
   MAX across the R/G/B difference bands (``ImageChops.lighter`` chained
   pairwise) collapses to a single ``"L"`` image — using max rather than
   the luminance-weighted average of a plain ``.convert("L")`` so a change
   in a single channel (e.g. a pure-red button turning pure-blue, which
   luminance conversion can nearly cancel out) isn't underweighted.
2. Threshold that ``"L"`` image to a binary mask: pixels with a max-channel
   delta above ``threshold`` (0-255 scale) count as "changed." This is the
   noise floor — anti-aliasing edges, subpixel font hinting, and lossy
   re-encoding artifacts produce small deltas that would otherwise flag
   every single frame pair as "100% different, one region per pixel."
   ``DEFAULT_THRESHOLD`` (24 / 255, ~9%) is a starting point per #201's
   explicit "start with a straightforward pixel diff + tolerance, revisit
   if it's too noisy in practice" — not a perceptual/SSIM-grade metric.
3. Connected-component grouping WITHOUT a pure-Python per-pixel scan (which
   would be too slow for a ~1280x800 frame at CI scale): the binary mask is
   tiled into fixed-size cells (:data:`_TILE_SIZE` px), and each cell's
   "has a changed pixel" test is a single ``Image.crop().getbbox()`` call —
   Pillow's C implementation, not a Python loop. Adjacent (8-connected)
   changed cells are then flood-filled into components using a small,
   in-memory grid (thousands of cells, not millions of pixels), and each
   component's final bbox is the union of its member cells' own tight
   ``getbbox()`` rectangles (not the coarse tile grid) — so a region is
   pixel-accurate at its edges even though *which* cells belong to which
   component is decided at tile granularity. Components below
   :data:`_MIN_REGION_AREA` px² are dropped as stray noise.

Annotator-overlay exclusion
----------------------------
Every reel produced by ``clickcast run`` / ``clickcast auto`` gets
:class:`~clickcast.annotate.annotator.Annotator` overlays baked into the
frame PNGs by default (progress bar, action label, actions panel, cursor +
ripple + arrow) — see ``annotate/annotator.py``. Diffing two runs of an
otherwise-identical page would therefore flag clickcast's OWN chrome as a
"regression" on every single step: the actions-panel label text differs
step-to-step, the progress bar fill differs, and the cursor is essentially
never in the exact same sub-pixel spot twice. That's the false-positive
#202 exists to design away.

The sidecar does not persist the full :class:`AnnotateConfig` used at
render time (only the pixel-grid overlay's params, via
:class:`~clickcast.feedback.models.AnnotateMetadata`) — so exact overlay
geometry can't be reconstructed. Instead, :func:`visual_diff` computes
GENEROUS exclusion boxes from ``AnnotateConfig()``'s DEFAULT layout
constants (imported directly from ``annotate.annotator``, not duplicated,
so they can't drift out of sync with the real renderer):

- **Progress bar** — a full-width strip along the bottom edge, sized to the
  default bar height plus a small pad.
- **Label bar** — a full-width band at the label's default margin/position
  (bottom by default), sized generously for up to a couple of wrapped
  lines at the default font size.
- **Actions panel** — a box in the default's screen corner, sized to the
  default max width / row count.
- **Cursor + ripple + arrowhead** — unlike the three boxes above, this one
  uses REAL per-step data: each paired step's ``cursor_xy`` (both run's and
  baseline's, since a real layout shift can also move where the cursor
  ends up) is available straight off :class:`~clickcast.feedback.models.StepReport`
  (#151). A square exclusion box is centered on each ``cursor_xy`` sized
  to cover the cursor glyph, the ripple's max radius, and the arrowhead —
  NOT the full arrow shaft back to the previous position (that isn't
  reconstructable from the sidecar alone, and excluding a shaft-length
  swath of the frame would hide real content changes along the cursor's
  path). This is a known, deliberate v1 approximation.

This is a best-effort geometric approximation, not an exact mask — a
custom :class:`AnnotateConfig` (different margins, panel position, a
disabled layer, etc.) will make the boxes over- or under-cover the real
overlay. ``exclude_overlays=True`` is still the default because the
common case (default-annotated reels) is what #202's acceptance criterion
targets: two runs of an otherwise-identical page must NOT show clickcast's
own cursor/label/panel/progress chrome as a visual regression. Callers who
need strict raw-pixel diffing (or who know their reels were NOT
annotated with the defaults) pass ``exclude_overlays=False`` (CLI:
``--no-exclude-overlays``).

Explicitly NOT handled in v1 (see #201 "out of scope" + revisit-if-noisy):
the pixel-grid overlay (#171) is not excluded — it's off by default and,
when on, draws thin low-alpha lines that mostly fall under the default
threshold; the pre-click target-highlight ring is not excluded — it's off
by default (`--for-humans`-only) and its bbox isn't persisted to the
sidecar. Both are candidates for a follow-up if they prove noisy in
practice.

Which frame per step
---------------------
A step can carry several sub-frames (pre-click, click, dwell...). This
module diffs the LAST frame of each paired step — the settled post-action
state — mirroring :func:`clickcast.reel._last_frame_for_step`'s existing
"last sub-frame reflects the settled state" convention used by
``Reel.save_region_at_step``.

Frame availability
-------------------
Per-step ``frames`` entries in the sidecar are bare filenames (see
``docs/feedback-schema.md``), not full paths — recorded frames live in
whatever directory the caller persisted them to (e.g. ``clickcast run
--format frames``, or a frames directory kept alongside the sidecar).
:func:`visual_diff` resolves each filename against a short list of
plausible locations (see :func:`_resolve_frame`) and treats a step whose
frame can't be found on disk the same as a pairing failure — flagged in
``unmatched_steps``, never a crash.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from clickcast.annotate.annotator import AnnotateConfig
from clickcast.feedback.models import (
    BBox,
    Report,
    StepReport,
    StepVisualDiff,
    UnmatchedStep,
    VisualDiffReport,
)

__all__ = [
    "DEFAULT_THRESHOLD",
    "VISUAL_DIFF_SCHEMA_VERSION",
    "load_report",
    "load_visual_diff_report",
    "max_changed_pct",
    "visual_diff",
]

VISUAL_DIFF_SCHEMA_VERSION = 1

# Per-channel absolute pixel delta (0-255 scale) above which a pixel counts
# as "changed." See the module docstring's "Pixel diff + threshold + region
# grouping" section for rationale.
DEFAULT_THRESHOLD = 24.0

# Tile size (px) for the coarse connected-component grid — see "Connected
# component grouping" above. Small enough to keep region bboxes reasonably
# tight, large enough that a ~1280x800 frame is a few thousand cells, not a
# million pixels.
_TILE_SIZE = 20

# Components smaller than this (bbox area, px²) are dropped as stray noise
# that slipped through the per-pixel threshold (isolated anti-aliased
# pixels, single-pixel rounding artifacts).
_MIN_REGION_AREA = 36

# Default overlay geometry, computed once from AnnotateConfig's own
# defaults so it can't silently drift out of sync with the real renderer.
_DEFAULT_ANNOTATE = AnnotateConfig()

# Generous radius (px) around a step's cursor_xy to exclude the cursor
# glyph + ripple + arrowhead. Derived from the default cursor/ripple style
# constants plus a small anti-aliasing pad.
_CURSOR_EXCLUSION_RADIUS = (
    _DEFAULT_ANNOTATE.cursor_style.size
    + _DEFAULT_ANNOTATE.ripple.radius_max
    + _DEFAULT_ANNOTATE.cursor_style.arrow_head_size
    + 8
)


def visual_diff(
    run_sidecar_path: str | Path,
    baseline_sidecar_path: str | Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    out_dir: str | Path | None = None,
    exclude_overlays: bool = True,
) -> VisualDiffReport:
    """Pixel-diff ``run_sidecar_path``'s frames against ``baseline_sidecar_path``'s.

    Reads both sidecars from disk, pairs their steps (see the module
    docstring for the pairing algorithm), and pixel-diffs each paired
    step's last frame. Writes a region-highlighted diff PNG for every step
    with at least one changed region into ``out_dir`` (default: a
    ``<run-sidecar-stem>.diff/`` directory next to ``run_sidecar_path``),
    plus a ``summary.json`` containing the returned report.

    ``threshold`` is the per-pixel channel-delta cutoff (0-255 scale, see
    :data:`DEFAULT_THRESHOLD`) — NOT a percent-of-frame cutoff (that's the
    CLI's ``--fail-above``, which this function has no opinion on: it
    always returns the full per-step ``changed_pct`` values and never
    raises on them).

    ``exclude_overlays=True`` (default) excludes clickcast's own annotator
    chrome (progress bar / label / actions panel / cursor+ripple) from the
    diff — see the module docstring's "Annotator-overlay exclusion"
    section. Does not re-run either scenario — this is pure post-hoc
    analysis of already-captured frames.
    """
    run_path = Path(run_sidecar_path)
    baseline_path = Path(baseline_sidecar_path)
    run_report = load_report(run_path)
    baseline_report = load_report(baseline_path)

    resolved_out_dir = Path(out_dir) if out_dir is not None else _default_out_dir(run_path)

    pairs, unmatched = _pair_steps(run_report.steps, baseline_report.steps)

    steps: list[StepVisualDiff] = []
    any_diff_image = False
    for run_idx, base_idx, run_step, base_step in pairs:
        run_frame = _resolve_frame(run_path, run_report, run_step)
        base_frame = _resolve_frame(baseline_path, baseline_report, base_step)
        if run_frame is None or base_frame is None:
            unmatched.append(
                UnmatchedStep(
                    side="run" if run_frame is None else "baseline",
                    index=run_idx if run_frame is None else base_idx,
                    label=run_step.label if run_frame is None else base_step.label,
                    reason="missing frame",
                )
            )
            continue

        changed_pct, regions = _diff_frames(
            run_frame,
            base_frame,
            threshold=threshold,
            exclude_overlays=exclude_overlays,
            run_step=run_step,
            base_step=base_step,
        )

        diff_image_path: str | None = None
        if regions:
            any_diff_image = True
            image_path = resolved_out_dir / f"step-{run_idx:04d}.diff.png"
            _write_diff_image(run_frame, regions, image_path)
            diff_image_path = str(image_path)

        steps.append(
            StepVisualDiff(
                run_index=run_idx,
                baseline_index=base_idx,
                label=run_step.label,
                changed_pct=changed_pct,
                regions=regions,
                diff_image_path=diff_image_path,
                run_frame=str(run_frame),
                baseline_frame=str(base_frame),
            )
        )

    report = VisualDiffReport(
        schema_version=VISUAL_DIFF_SCHEMA_VERSION,
        threshold=threshold,
        exclude_overlays=exclude_overlays,
        steps=steps,
        unmatched_steps=unmatched,
    )

    if steps or unmatched or any_diff_image:
        resolved_out_dir.mkdir(parents=True, exist_ok=True)
        (resolved_out_dir / "summary.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2)
        )

    return report


def load_report(path: str | Path) -> Report:
    """Load + validate a sidecar JSON from disk.

    Thin, local convenience so this module doesn't reach across to
    :func:`clickcast.feedback.load` for a one-line wrapper — kept as a
    module-level function (not inlined) so tests can stub it if needed.
    """
    text = Path(path).read_text()
    return Report.model_validate_json(text)


def load_visual_diff_report(path: str | Path) -> VisualDiffReport:
    """Load a previously-written ``summary.json`` back into a :class:`VisualDiffReport`."""
    text = Path(path).read_text()
    return VisualDiffReport.model_validate_json(text)


def max_changed_pct(report: VisualDiffReport) -> float:
    """Highest ``changed_pct`` across every paired step, or ``0.0`` if there are none."""
    return max((s.changed_pct for s in report.steps), default=0.0)


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------


def _pair_steps(
    run_steps: list[StepReport],
    baseline_steps: list[StepReport],
) -> tuple[list[tuple[int, int, StepReport, StepReport]], list[UnmatchedStep]]:
    """Pair run steps with baseline steps. See module docstring for the algorithm.

    Returns ``(pairs, unmatched)`` where each pair is
    ``(run_index, baseline_index, run_step, baseline_step)``.
    """
    if len(run_steps) == len(baseline_steps):
        # Common case: same step count → pair straight by index. (Labels
        # may legitimately differ step-to-step even in the common case —
        # that's not a pairing failure, just something the diff itself will
        # surface as a real content change if it manifests visually.)
        return (
            [(i, i, r, b) for i, (r, b) in enumerate(zip(run_steps, baseline_steps, strict=False))],
            [],
        )

    unmatched: list[UnmatchedStep] = []
    baseline_by_label: dict[str | None, list[int]] = {}
    for i, b in enumerate(baseline_steps):
        baseline_by_label.setdefault(b.label, []).append(i)

    used_baseline: set[int] = set()
    pairs: list[tuple[int, int, StepReport, StepReport]] = []
    for i, r in enumerate(run_steps):
        candidates = baseline_by_label.get(r.label, [])
        match_idx: int | None = None
        if i in candidates and i not in used_baseline:
            match_idx = i
        else:
            for c in candidates:
                if c not in used_baseline:
                    match_idx = c
                    break
        if match_idx is None:
            unmatched.append(
                UnmatchedStep(
                    side="run",
                    index=i,
                    label=r.label,
                    reason="no baseline counterpart",
                )
            )
            continue
        used_baseline.add(match_idx)
        pairs.append((i, match_idx, r, baseline_steps[match_idx]))

    for j, b in enumerate(baseline_steps):
        if j not in used_baseline:
            unmatched.append(
                UnmatchedStep(
                    side="baseline",
                    index=j,
                    label=b.label,
                    reason="no run counterpart",
                )
            )

    return pairs, unmatched


# --------------------------------------------------------------------------
# Frame resolution
# --------------------------------------------------------------------------


def _resolve_frame(sidecar_path: Path, report: Report, step: StepReport) -> Path | None:
    """Return the on-disk path of ``step``'s LAST frame, or ``None`` if unavailable."""
    if not step.frames:
        return None
    filename = step.frames[-1]
    base = sidecar_path.parent

    candidates: list[Path] = []
    media_path = Path(report.media.path) if report.media.path else None
    if media_path is not None and report.media.format == "frames":
        # `media.path` is stored exactly as the `--out` argument was given at
        # capture time (e.g. "demo/pixel-visual-diff/run.gif" if that's what
        # `--out` was) -- relative to whatever the CWD was THEN, not
        # necessarily relative to the sidecar's own directory. But the
        # frames dir is always a sibling of the sidecar on disk (the CLI
        # writes `<out>.json` right next to `<out>/`), so for a relative
        # path use only its final component against the sidecar's real
        # directory rather than re-joining the whole (possibly stale) path,
        # which double-nests when `--out` had directory components and
        # `diff` runs from a different CWD than the original capture.
        media_dir = media_path if media_path.is_absolute() else base / media_path.name
        candidates.append(media_dir / filename)
    candidates.append(base / filename)
    candidates.append(base / "frames" / filename)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _default_out_dir(run_sidecar_path: Path) -> Path:
    name = run_sidecar_path.name
    if name.endswith(".json"):
        name = name[: -len(".json")]
    for ext in (".gif", ".mp4", ".webp"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return run_sidecar_path.parent / f"{name}.diff"


# --------------------------------------------------------------------------
# Pixel diff
# --------------------------------------------------------------------------


def _diff_frames(
    run_frame: Path,
    base_frame: Path,
    *,
    threshold: float,
    exclude_overlays: bool,
    run_step: StepReport,
    base_step: StepReport,
) -> tuple[float, list[BBox]]:
    with Image.open(run_frame) as ri, Image.open(base_frame) as bi:
        run_img = ri.convert("RGB")
        base_img = bi.convert("RGB")

    if run_img.size != base_img.size:
        # Can't meaningfully pixel-diff mismatched dimensions (different
        # viewport / crop) — report the whole run frame as changed rather
        # than silently resizing one image to match the other (which would
        # itself introduce diff noise from the resampling).
        w, h = run_img.size
        return 100.0, [BBox(x=0, y=0, width=w, height=h)]

    w, h = run_img.size
    diff = ImageChops.difference(run_img, base_img)
    r, g, b = diff.split()
    diff_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = diff_max.point(lambda p: 255 if p > threshold else 0)

    if exclude_overlays:
        exclusions = _overlay_exclusions((w, h), run_step, base_step)
        _apply_exclusions(mask, exclusions)

    changed = mask.histogram()[255]
    total = w * h
    changed_pct = (100.0 * changed / total) if total else 0.0
    regions = _connected_components(mask)

    return changed_pct, regions


def _apply_exclusions(mask: Image.Image, boxes: list[BBox]) -> None:
    if not boxes:
        return
    draw = ImageDraw.Draw(mask)
    for box in boxes:
        draw.rectangle([box.x, box.y, box.x + box.width, box.y + box.height], fill=0)


def _connected_components(mask: Image.Image) -> list[BBox]:
    """Group changed pixels in ``mask`` into bounding-box regions.

    See the module docstring's "Connected component grouping" section for
    why this tiles the mask rather than scanning pixels in pure Python.
    """
    w, h = mask.size
    tile = _TILE_SIZE
    cols = (w + tile - 1) // tile
    rows = (h + tile - 1) // tile

    # (tile_x, tile_y) -> tight (x0, y0, x1, y1) bbox of THAT tile's changed
    # pixels, in whole-image coordinates.
    cell_bbox: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for ty in range(rows):
        y0 = ty * tile
        y1 = min(y0 + tile, h)
        for tx in range(cols):
            x0 = tx * tile
            x1 = min(x0 + tile, w)
            crop = mask.crop((x0, y0, x1, y1))
            bbox = crop.getbbox()
            if bbox is None:
                continue
            cx0, cy0, cx1, cy1 = bbox
            cell_bbox[(tx, ty)] = (x0 + cx0, y0 + cy0, x0 + cx1, y0 + cy1)

    if not cell_bbox:
        return []

    visited: set[tuple[int, int]] = set()
    regions: list[BBox] = []
    for start in cell_bbox:
        if start in visited:
            continue
        visited.add(start)
        stack = [start]
        minx = miny = None
        maxx = maxy = None
        while stack:
            cx, cy = stack.pop()
            bx0, by0, bx1, by1 = cell_bbox[(cx, cy)]
            minx = bx0 if minx is None else min(minx, bx0)
            miny = by0 if miny is None else min(miny, by0)
            maxx = bx1 if maxx is None else max(maxx, bx1)
            maxy = by1 if maxy is None else max(maxy, by1)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbor = (cx + dx, cy + dy)
                    if neighbor in cell_bbox and neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
        assert minx is not None and miny is not None and maxx is not None and maxy is not None
        width = maxx - minx
        height = maxy - miny
        if width * height < _MIN_REGION_AREA:
            continue
        regions.append(BBox(x=minx, y=miny, width=width, height=height))

    regions.sort(key=lambda b: (b.y, b.x))
    return regions


# --------------------------------------------------------------------------
# Overlay exclusion — see module docstring's "Annotator-overlay exclusion"
# --------------------------------------------------------------------------


def _overlay_exclusions(
    size: tuple[int, int],
    run_step: StepReport,
    base_step: StepReport,
) -> list[BBox]:
    w, h = size
    boxes = [_progress_bbox(w, h), _label_bbox(w, h), _panel_bbox(w, h)]
    for step in (run_step, base_step):
        cursor_box = _cursor_bbox(w, h, step.cursor_xy)
        if cursor_box is not None:
            boxes.append(cursor_box)
    return boxes


def _progress_bbox(w: int, h: int) -> BBox:
    style = _DEFAULT_ANNOTATE.progress_style
    pad = 4
    height = min(h, style.height + pad)
    y = max(0, h - height)
    return BBox(x=0, y=y, width=w, height=height)


def _label_bbox(w: int, h: int) -> BBox:
    style = _DEFAULT_ANNOTATE.label
    line_h = int(_DEFAULT_ANNOTATE.font_size * 1.4)
    box_h = min(h, 2 * style.padding_y + 2 * line_h + 8)
    y = max(0, h - style.margin - box_h) if style.position == "bottom" else max(0, style.margin - 4)
    box_h = min(box_h, h - y)
    return BBox(x=0, y=y, width=w, height=max(1, box_h))


def _panel_bbox(w: int, h: int) -> BBox:
    style = _DEFAULT_ANNOTATE.panel
    row_h = style.font_size + 4
    box_w = min(w, style.max_width + 2 * style.padding_x)
    box_h = min(
        h,
        2 * style.padding_y + style.max_rows * row_h + (style.max_rows - 1) * style.row_spacing + 8,
    )
    if style.position == "top-right":
        x = max(0, w - box_w - style.margin)
        y = style.margin
    elif style.position == "top-left":
        x = style.margin
        y = style.margin
    elif style.position == "bottom-right":
        x = max(0, w - box_w - style.margin)
        y = max(0, h - box_h - style.margin)
    else:  # bottom-left
        x = style.margin
        y = max(0, h - box_h - style.margin)
    x = min(x, max(0, w - 1))
    y = min(y, max(0, h - 1))
    return BBox(x=x, y=y, width=min(box_w, w - x), height=min(box_h, h - y))


def _cursor_bbox(w: int, h: int, cursor_xy: list[int] | None) -> BBox | None:
    if not cursor_xy or len(cursor_xy) < 2:
        return None
    cx, cy = cursor_xy[0], cursor_xy[1]
    r = _CURSOR_EXCLUSION_RADIUS
    x0 = max(0, cx - r)
    y0 = max(0, cy - r)
    x1 = min(w, cx + r)
    y1 = min(h, cy + r)
    if x1 <= x0 or y1 <= y0:
        return None
    return BBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)


# --------------------------------------------------------------------------
# Diff image
# --------------------------------------------------------------------------


def _write_diff_image(run_frame: Path, regions: list[BBox], out_path: Path) -> None:
    """Composite red region outlines over the run frame; save to ``out_path``."""
    with Image.open(run_frame) as im:
        canvas = im.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    for box in regions:
        draw.rectangle(
            [box.x, box.y, box.x + box.width, box.y + box.height],
            outline=(255, 40, 40, 255),
            width=3,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, format="PNG")
