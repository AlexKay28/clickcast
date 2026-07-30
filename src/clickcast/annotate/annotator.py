"""Frame annotator — composite click ripples, labels, cursor trail, progress bar."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

__all__ = [
    "ActionsPanelStyle",
    "AnnotateConfig",
    "Annotator",
    "CursorStyle",
    "LabelStyle",
    "ProgressStyle",
    "RippleStyle",
    "TargetHighlightStyle",
]

_BUNDLED_FONT = "DejaVuSans.ttf"


@dataclass(slots=True)
class LabelStyle:
    """Style + layout for the bottom/top label bar.

    ``style="light"`` (default) swaps to a white background with near-black
    text — more legible on dark-mode sites (react.dev in dark mode was the
    smoking gun; the dark label blended into the page). Explicit ``bg_color``/
    ``fg_color`` still win if set, otherwise ``style`` picks a sensible pair.
    """

    style: Literal["dark", "light"] = "light"
    max_chars: int = 60
    padding_x: int = 24
    padding_y: int = 12
    bg_color: tuple[int, int, int, int] | None = None  # None → pick from style
    fg_color: tuple[int, int, int, int] | None = None
    radius: int = 8
    position: Literal["top", "bottom"] = "bottom"
    margin: int = 32

    def resolved_colors(self) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        """Return (bg, fg) for the label bar, respecting explicit overrides."""
        if self.bg_color is not None and self.fg_color is not None:
            return self.bg_color, self.fg_color
        if self.style == "light":
            bg = self.bg_color or (245, 245, 245, 230)
            fg = self.fg_color or (30, 30, 30, 255)
        else:  # "dark"
            bg = self.bg_color or (20, 20, 20, 192)
            fg = self.fg_color or (255, 255, 255, 255)
        return bg, fg


@dataclass(slots=True)
class RippleStyle:
    """Click-ripple animation tunables."""

    stages: int = 3
    radius_min: int = 12
    radius_max: int = 48
    color: tuple[int, int, int] = (255, 255, 255)
    alpha_start: int = 128
    width: int = 3


@dataclass(slots=True)
class CursorStyle:
    """Cursor + trail appearance.

    ``arrows`` (default on) draws a red line + arrowhead between each pair of
    consecutive tracked cursor positions instead of the fading trail of dots.
    Arrows read as motion vectors ("cursor went here → then here"), which is
    a stronger signal for both human viewers and LLMs consuming the reel.
    Set to ``False`` to fall back to the original dots trail.

    ``arrow_min_distance`` / ``arrow_max_distance`` guard against clutter
    (tiny jitter) and misleading teleports (cursor "jumps" between clicks on
    different pages after a goto — the recorder does not reset history at
    page boundaries).
    """

    color: tuple[int, int, int, int] = (255, 220, 100, 240)
    size: int = 14
    trail_length: int = 6
    trail_alpha_max: int = 160
    arrows: bool = True
    arrow_color: tuple[int, int, int, int] = (220, 60, 60, 220)
    arrow_thickness: int = 3
    arrow_head_size: int = 10
    arrow_min_distance: int = 10
    arrow_max_distance: int = 600
    # When True, draw ONE arrow that persists across every dwell frame of a
    # step (from the previous distinct cursor position to the current one),
    # instead of a chain of per-hop arrows that flashes on during
    # transitions and disappears once history fills. Reads as a single
    # held A→B vector — easier to follow for human viewers. Default False
    # for backwards compatibility with the chain-of-arrows shape shipped
    # in v0.2.0.
    single_arrow: bool = False
    # Smooth cursor interpolation between recorded positions. See #75.
    # Consumed by :func:`clickcast.annotate.interpolate.interpolate_cursor_motion`
    # which runs before the annotator pass; the annotator itself only reads
    # these when the pipeline threads them through.
    interpolate: bool = True
    interpolate_frames: int = 4
    interpolate_easing: Literal["linear", "ease-in-out"] = "ease-in-out"
    interpolate_min_distance: int = 50


@dataclass(slots=True)
class ProgressStyle:
    """Bottom progress-bar appearance."""

    height: int = 4
    color: tuple[int, int, int, int] = (100, 200, 255, 220)
    bg_color: tuple[int, int, int, int] = (255, 255, 255, 40)


@dataclass(slots=True)
class ActionsPanelStyle:
    """Actions-panel style + layout.

    Small side panel showing the last N actions with the current one
    highlighted — gives viewers (human and LLM alike) an at-a-glance sense of
    "where we are in the tour" that the progress bar alone can't convey.

    ``position`` picks which corner the panel anchors to. The default
    ``top-right`` matches shipped behaviour; ``bottom-right`` /
    ``bottom-left`` are useful for tours where the click targets live in the
    top nav (typical docs sites) — the panel used to sit exactly where the
    tour was clicking.
    """

    max_rows: int = 6
    font_size: int = 16
    padding_x: int = 14
    padding_y: int = 10
    margin: int = 20
    max_width: int = 340
    row_spacing: int = 6
    bg_color: tuple[int, int, int, int] = (255, 255, 255, 230)
    fg_color: tuple[int, int, int, int] = (30, 30, 30, 255)
    current_bg_color: tuple[int, int, int, int] = (80, 160, 255, 60)
    current_fg_color: tuple[int, int, int, int] = (10, 40, 100, 255)
    done_marker: str = "· "
    current_marker: str = "▶ "
    radius: int = 8
    max_label_chars: int = 32
    position: Literal["top-right", "top-left", "bottom-right", "bottom-left"] = "top-right"


@dataclass(slots=True)
class TargetHighlightStyle:
    """Pre-click target-highlight ring appearance.

    A soft, pulsing outline that appears around the resolved click target on
    the pre-click frame(s) so a human viewer's eye locks onto the target
    BEFORE the ripple fires. See #129 Track A.

    ``padding`` inflates the bbox outward so the ring never sits on top of
    the target's own edge (which reads as a border, not a highlight).
    ``pulse_count`` divides the ring's alpha modulation into N cycles across
    however many highlight frames the recorder emits — 1 is a steady ring;
    2-3 gives a gentle breathing effect.
    """

    color: tuple[int, int, int] = (255, 200, 40)
    width: int = 4
    padding: int = 8
    radius: int = 12
    alpha_min: int = 90
    alpha_max: int = 230
    pulse_count: int = 2


@dataclass(slots=True)
class AnnotateConfig:
    """Toggles + tunables for every annotation layer.

    Field groups are exposed as small style dataclasses (``label``, ``ripple``,
    ``cursor_style``, ``progress_style``, ``panel``) so callers can tweak one
    concern without a wall of ``label_*`` kwargs. The top-level layer toggles
    (``clicks``/``labels``/``cursor``/``progress``/``actions_panel``) keep
    their bare names because they're on the hot path for "just turn this off"
    use cases.
    """

    # Layer toggles ------------------------------------------------------
    clicks: bool = True
    labels: bool = True
    cursor: bool = True
    progress: bool = True
    actions_panel: bool = True
    # Pre-click target-highlight ring around the resolved click bbox — draws
    # ONLY when the pipeline passes a ``target_bbox`` for the frame's step
    # (typically set on pre-click sub-frames). Off by default so shipped
    # tours don't gain the extra overlay; ``--for-humans`` flips it on.
    # See #129 Track A.
    target_highlight: bool = False

    # Font ---------------------------------------------------------------
    font_path: str | Path | None = None  # None → bundled DejaVuSans.ttf
    font_size: int = 20

    # Sub-styles ---------------------------------------------------------
    # `cursor_style`/`progress_style` (rather than bare `cursor`/`progress`)
    # avoid clashing with the layer-toggle booleans above.
    label: LabelStyle = field(default_factory=LabelStyle)
    ripple: RippleStyle = field(default_factory=RippleStyle)
    cursor_style: CursorStyle = field(default_factory=CursorStyle)
    progress_style: ProgressStyle = field(default_factory=ProgressStyle)
    panel: ActionsPanelStyle = field(default_factory=ActionsPanelStyle)
    target: TargetHighlightStyle = field(default_factory=TargetHighlightStyle)


def _load_font(config: AnnotateConfig) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _load_font_with_size(config, config.font_size)


def _load_font_with_size(
    config: AnnotateConfig, size: int
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if config.font_path:
        return ImageFont.truetype(str(config.font_path), size)
    resource = files("clickcast.annotate").joinpath("fonts").joinpath(_BUNDLED_FONT)
    try:
        data = resource.read_bytes()
    except (FileNotFoundError, OSError):
        # Last-resort fallback — bitmap font, tiny. Signals a packaging bug.
        return ImageFont.load_default()
    return ImageFont.truetype(io.BytesIO(data), size)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


class Annotator:
    """Overlay annotations on captured frames.

    Never mutates the input frame in place — `annotate()` writes to a new
    file (`out_path` if given, otherwise `<stem>.annotated.png` next to the
    input). Cursor trail state is maintained across calls; use
    :meth:`reset_cursor` when starting a new scenario.
    """

    def __init__(self, config: AnnotateConfig | None = None) -> None:
        self.config = config or AnnotateConfig()
        self._font = _load_font(self.config)
        self._panel_font = _load_font_with_size(self.config, self.config.panel.font_size)
        self._cursor_history: list[tuple[int, int]] = []
        # Sticky arrow state (consumed only when
        # :attr:`CursorStyle.single_arrow` is True). Endpoints are updated
        # when the cursor MOVES by at least ``arrow_min_distance``, then
        # persist across every subsequent dwell frame until the next move.
        self._sticky_arrow_from: tuple[int, int] | None = None
        self._sticky_arrow_to: tuple[int, int] | None = None
        self._sticky_last_cursor: tuple[int, int] | None = None

    def reset_cursor(self) -> None:
        self._cursor_history.clear()
        self._sticky_arrow_from = None
        self._sticky_arrow_to = None
        self._sticky_last_cursor = None

    def annotate(
        self,
        frame_path: str | Path,
        *,
        out_path: str | Path | None = None,
        step_index: int = 0,
        total_steps: int = 1,
        label: str | None = None,
        cursor_xy: tuple[int, int] | None = None,
        click_at: tuple[int, int] | None = None,
        ripple_stage: int = 0,
        all_labels: list[str] | None = None,
        target_bbox: tuple[int, int, int, int] | None = None,
        target_pulse_phase: float = 0.0,
    ) -> Path:
        """Composite the enabled layers onto ``frame_path``; return output Path.

        ``ripple_stage`` is 1..``ripple.stages`` for the N frames after a
        click; pass 0 when there was no click on this frame.

        ``all_labels`` is the ordered list of every step's label — indexed by
        ``step_index``. When set (and ``actions_panel=True`` in config), the
        actions-list side panel renders the last N labels with the current one
        highlighted.

        ``target_bbox`` is ``(x, y, width, height)`` for the resolved click
        target on this frame; when set (and ``target_highlight=True``),
        draws a soft pulsing ring around it. ``target_pulse_phase`` is
        0.0..1.0 across the ring's lifetime — the caller advances it per
        frame so successive frames render at slightly different alphas.
        """
        src = Path(frame_path)
        dst = Path(out_path) if out_path else src.with_name(f"{src.stem}.annotated.png")
        dst.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(src) as base:
            canvas = base.convert("RGBA")

        if cursor_xy is not None:
            self._cursor_history.append(cursor_xy)
            # Sticky-arrow bookkeeping: whenever the cursor arrives at a
            # position sufficiently different from the last known one, treat
            # that as a MOVE and update the sticky endpoints. The arrow then
            # persists across every subsequent frame until the next move —
            # so a human viewer sees a single held A→B vector per step
            # rather than a brief flash during the transition frames only.
            move_thresh = self.config.cursor_style.arrow_min_distance
            if self._sticky_last_cursor is None:
                self._sticky_last_cursor = cursor_xy
            else:
                dx = cursor_xy[0] - self._sticky_last_cursor[0]
                dy = cursor_xy[1] - self._sticky_last_cursor[1]
                if dx * dx + dy * dy >= move_thresh * move_thresh:
                    self._sticky_arrow_from = self._sticky_last_cursor
                    self._sticky_arrow_to = cursor_xy
                    self._sticky_last_cursor = cursor_xy
            history_cap = max(self.config.cursor_style.trail_length + 1, 1)
            while len(self._cursor_history) > history_cap:
                self._cursor_history.pop(0)

        if self.config.progress:
            self._draw_progress(canvas, step_index, total_steps)
        if self.config.target_highlight and target_bbox is not None:
            self._draw_target_highlight(canvas, target_bbox, target_pulse_phase)
        if self.config.clicks and click_at is not None and ripple_stage > 0:
            self._draw_ripple(canvas, click_at, ripple_stage)
        if self.config.cursor and self._cursor_history:
            self._draw_cursor(canvas)
        if self.config.actions_panel and all_labels:
            self._draw_actions_panel(canvas, all_labels, step_index)
        if self.config.labels and label:
            self._draw_label(canvas, label)

        canvas.convert("RGB").save(dst, format="PNG")
        return dst

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    def _draw_progress(self, canvas: Image.Image, step_index: int, total_steps: int) -> None:
        style = self.config.progress_style
        w, h = canvas.size
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        y = h - style.height
        od.rectangle([0, y, w, h], fill=style.bg_color)
        frac = (step_index + 1) / max(total_steps, 1)
        od.rectangle([0, y, int(w * frac), h], fill=style.color)
        canvas.alpha_composite(overlay)

    def _draw_ripple(
        self,
        canvas: Image.Image,
        at: tuple[int, int],
        stage: int,
    ) -> None:
        style = self.config.ripple
        # stage 1..N — radius grows, alpha fades
        t = min(1.0, stage / max(style.stages, 1))
        radius = int(style.radius_min + t * (style.radius_max - style.radius_min))
        alpha = max(0, int(style.alpha_start * (1 - t)))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.ellipse(
            [at[0] - radius, at[1] - radius, at[0] + radius, at[1] + radius],
            outline=(*style.color, alpha),
            width=style.width,
        )
        canvas.alpha_composite(overlay)

    def _draw_target_highlight(
        self,
        canvas: Image.Image,
        bbox: tuple[int, int, int, int],
        phase: float,
    ) -> None:
        """Composite a soft, pulsing rounded rectangle around ``bbox``.

        ``phase`` is 0.0..1.0 across the highlight's lifetime; alpha
        modulates as a sine wave with ``style.pulse_count`` cycles so a
        series of frames "breathes." A single frame renders at alpha_max
        (phase 0 → sin(0) = 0 → alpha = alpha_max ... but the formula uses
        1 - abs(sin), so phase 0 renders at alpha_max cleanly).

        The bbox is inflated by ``style.padding`` outward before drawing —
        the ring never sits on top of the target's own edge.
        """
        style = self.config.target
        x, y, w, h = bbox
        pad = style.padding
        x0 = x - pad
        y0 = y - pad
        x1 = x + w + pad
        y1 = y + h + pad
        # Clip to canvas so a partially off-screen target still gets the ring.
        cw, ch = canvas.size
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(cw, x1)
        y1 = min(ch, y1)
        if x1 <= x0 or y1 <= y0:
            return
        # Pulsing alpha: |sin(phase * pulse_count * pi)| gives N cycles across
        # phase 0..1, and 1 - that gives a value that peaks at phase 0 (the
        # first pre-click frame — brightest — attention-grabbing) and dips at
        # the midpoints. A single frame (phase = 0) lands at alpha_max cleanly.
        modulation = 1.0 - abs(math.sin(phase * max(style.pulse_count, 1) * math.pi))
        alpha = int(style.alpha_min + (style.alpha_max - style.alpha_min) * modulation)
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=style.radius,
            outline=(*style.color, alpha),
            width=style.width,
        )
        canvas.alpha_composite(overlay)

    def _draw_cursor(self, canvas: Image.Image) -> None:
        style = self.config.cursor_style
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)

        if style.arrows:
            if (
                style.single_arrow
                and self._sticky_arrow_from is not None
                and self._sticky_arrow_to is not None
            ):
                # Sticky arrow: held from move-detection until the NEXT
                # move-detection, so the viewer sees one static A→B vector
                # for the full duration of every step rather than a brief
                # flash during the transition frames.
                older = self._sticky_arrow_from
                newer = self._sticky_arrow_to
                dx = newer[0] - older[0]
                dy = newer[1] - older[1]
                dist_sq = dx * dx + dy * dy
                if (
                    style.arrow_min_distance * style.arrow_min_distance
                    <= dist_sq
                    <= style.arrow_max_distance * style.arrow_max_distance
                ):
                    self._draw_arrow(od, older, newer, style.arrow_color, style)
            else:
                # Motion vectors between consecutive tracked positions. See #73.
                pairs = list(zip(self._cursor_history, self._cursor_history[1:], strict=False))
                for i, (older, newer) in enumerate(pairs):
                    dx = newer[0] - older[0]
                    dy = newer[1] - older[1]
                    dist_sq = dx * dx + dy * dy
                    if dist_sq < style.arrow_min_distance * style.arrow_min_distance:
                        continue  # jitter — skip
                    if dist_sq > style.arrow_max_distance * style.arrow_max_distance:
                        continue  # teleport (cross-page) — skip
                    fade = (i + 1) / len(pairs)
                    r, g, b, a = style.arrow_color
                    faded = (r, g, b, int(a * fade))
                    self._draw_arrow(od, older, newer, faded, style)
        else:
            trail = self._cursor_history[:-1]
            for i, pos in enumerate(trail):
                # Older = fainter
                fade = (i + 1) / len(trail)
                alpha = int(style.trail_alpha_max * fade)
                r = max(2, style.size // 3)
                od.ellipse(
                    [pos[0] - r, pos[1] - r, pos[0] + r, pos[1] + r],
                    fill=(*style.color[:3], alpha),
                )

        cx, cy = self._cursor_history[-1]
        s = style.size
        od.polygon(
            [
                (cx, cy - s // 2),
                (cx + s // 2, cy),
                (cx, cy + s // 2),
                (cx - s // 2, cy),
            ],
            fill=style.color,
            outline=(0, 0, 0, 220),
        )
        canvas.alpha_composite(overlay)

    @staticmethod
    def _draw_arrow(
        od: ImageDraw.ImageDraw,
        older: tuple[int, int],
        newer: tuple[int, int],
        color: tuple[int, int, int, int],
        style: CursorStyle,
    ) -> None:
        """Line from older→newer + filled triangular arrowhead at newer."""
        od.line([older, newer], fill=color, width=style.arrow_thickness)
        # Arrowhead: rotate a triangle to align with (older→newer).
        dx = newer[0] - older[0]
        dy = newer[1] - older[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return
        ux, uy = dx / length, dy / length  # unit vector along the arrow
        px, py = -uy, ux  # perpendicular (rotate 90°)
        head = style.arrow_head_size
        tip = (int(newer[0]), int(newer[1]))
        base_cx = newer[0] - ux * head
        base_cy = newer[1] - uy * head
        left = (int(base_cx + px * head * 0.5), int(base_cy + py * head * 0.5))
        right = (int(base_cx - px * head * 0.5), int(base_cy - py * head * 0.5))
        od.polygon([tip, left, right], fill=color)

    def _draw_label(self, canvas: Image.Image, text: str) -> None:
        style = self.config.label
        bg_color, fg_color = style.resolved_colors()
        wrapped = _wrap(text, style.max_chars)

        measure = ImageDraw.Draw(canvas)
        bbox = measure.multiline_textbbox((0, 0), wrapped, font=self._font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        box_w = text_w + 2 * style.padding_x
        box_h = text_h + 2 * style.padding_y

        img_w, img_h = canvas.size
        x = max(0, (img_w - box_w) // 2)
        y = img_h - box_h - style.margin if style.position == "bottom" else style.margin

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            radius=style.radius,
            fill=bg_color,
        )
        # Draw text on the same overlay for correct compositing
        od.multiline_text(
            (x + style.padding_x, y + style.padding_y - bbox[1]),
            wrapped,
            font=self._font,
            fill=fg_color,
        )
        canvas.alpha_composite(overlay)

    def _draw_actions_panel(
        self,
        canvas: Image.Image,
        all_labels: list[str],
        step_index: int,
    ) -> None:
        """Composite a top-right panel showing the last N step labels."""
        style = self.config.panel
        # Window into the labels: last N up to and including current.
        window_end = min(step_index + 1, len(all_labels))
        window_start = max(0, window_end - style.max_rows)
        rows = all_labels[window_start:window_end]
        if not rows:
            return
        # Truncate long labels — panel is space-constrained.
        rows = [_truncate(row, style.max_label_chars) for row in rows]
        current_local_idx = window_end - 1 - window_start

        # Measure the panel.
        measure = ImageDraw.Draw(canvas)
        row_heights: list[int] = []
        row_widths: list[int] = []
        for i, text in enumerate(rows):
            marker = style.current_marker if i == current_local_idx else style.done_marker
            line = f"{marker}{text}"
            bbox = measure.textbbox((0, 0), line, font=self._panel_font)
            row_widths.append(int(bbox[2] - bbox[0]))
            row_heights.append(int(bbox[3] - bbox[1]))
        text_w = min(style.max_width, max(row_widths))
        text_h = sum(row_heights) + style.row_spacing * max(len(rows) - 1, 0)
        box_w = text_w + 2 * style.padding_x
        box_h = text_h + 2 * style.padding_y

        img_w, img_h = canvas.size
        if style.position == "top-right":
            x = img_w - box_w - style.margin
            y = style.margin
        elif style.position == "top-left":
            x = style.margin
            y = style.margin
        elif style.position == "bottom-right":
            x = img_w - box_w - style.margin
            y = img_h - box_h - style.margin
        else:  # bottom-left
            x = style.margin
            y = img_h - box_h - style.margin

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            radius=style.radius,
            fill=style.bg_color,
        )

        row_y = y + style.padding_y
        for i, text in enumerate(rows):
            is_current = i == current_local_idx
            marker = style.current_marker if is_current else style.done_marker
            line = f"{marker}{text}"
            row_h = row_heights[i]
            # Current row gets a subtle highlight strip behind it.
            if is_current:
                od.rounded_rectangle(
                    [
                        x + style.padding_x // 2,
                        row_y - 2,
                        x + box_w - style.padding_x // 2,
                        row_y + row_h + 2,
                    ],
                    radius=4,
                    fill=style.current_bg_color,
                )
            od.text(
                (x + style.padding_x, row_y),
                line,
                font=self._panel_font,
                fill=style.current_fg_color if is_current else style.fg_color,
            )
            row_y += row_h + style.row_spacing

        canvas.alpha_composite(overlay)


def _wrap(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)
