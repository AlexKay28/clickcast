"""Frame annotator — composite click ripples, labels, cursor trail, progress bar."""

from __future__ import annotations

import io
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

__all__ = ["AnnotateConfig", "Annotator"]

_BUNDLED_FONT = "DejaVuSans.ttf"


@dataclass(slots=True)
class AnnotateConfig:
    """Toggles + tunables for every annotation layer.

    Every field is safe to override in isolation; defaults produce a
    legible-on-any-background overlay at ~1280x800.
    """

    # Layer toggles ------------------------------------------------------
    clicks: bool = True
    labels: bool = True
    cursor: bool = True
    progress: bool = True
    actions_panel: bool = True

    # Font ---------------------------------------------------------------
    font_path: str | Path | None = None  # None → bundled DejaVuSans.ttf
    font_size: int = 20

    # Label bar ----------------------------------------------------------
    # `label_style="light"` swaps to a white background with near-black text —
    # more legible on dark-mode sites (react.dev in dark mode was the smoking
    # gun; the dark label blended into the page). Explicit color fields still
    # win if set, otherwise `label_style` picks a sensible default pair.
    label_style: Literal["dark", "light"] = "light"
    label_max_chars: int = 60
    label_padding_x: int = 24
    label_padding_y: int = 12
    label_bg_color: tuple[int, int, int, int] | None = None  # None → pick from style
    label_fg_color: tuple[int, int, int, int] | None = None
    label_radius: int = 8
    label_position: Literal["top", "bottom"] = "bottom"
    label_margin: int = 32

    # Click ripple -------------------------------------------------------
    ripple_stages: int = 3
    ripple_radius_min: int = 12
    ripple_radius_max: int = 48
    ripple_color: tuple[int, int, int] = (255, 255, 255)
    ripple_alpha_start: int = 128
    ripple_width: int = 3

    # Cursor + trail -----------------------------------------------------
    cursor_color: tuple[int, int, int, int] = (255, 220, 100, 240)
    cursor_size: int = 14
    cursor_trail_length: int = 6
    cursor_trail_alpha_max: int = 160

    # Progress bar -------------------------------------------------------
    progress_height: int = 4
    progress_color: tuple[int, int, int, int] = (100, 200, 255, 220)
    progress_bg_color: tuple[int, int, int, int] = (255, 255, 255, 40)

    # Actions panel ------------------------------------------------------
    # Small side panel in the top-right showing the last N actions with the
    # current one highlighted — gives viewers (human and LLM alike) an at-a-
    # glance sense of "where we are in the tour" that the progress bar alone
    # can't convey.
    panel_max_rows: int = 6
    panel_font_size: int = 16
    panel_padding_x: int = 14
    panel_padding_y: int = 10
    panel_margin: int = 20
    panel_max_width: int = 340
    panel_row_spacing: int = 6
    panel_bg_color: tuple[int, int, int, int] = (255, 255, 255, 230)
    panel_fg_color: tuple[int, int, int, int] = (30, 30, 30, 255)
    panel_current_bg_color: tuple[int, int, int, int] = (80, 160, 255, 60)
    panel_current_fg_color: tuple[int, int, int, int] = (10, 40, 100, 255)
    panel_done_marker: str = "· "
    panel_current_marker: str = "▶ "
    panel_radius: int = 8
    panel_max_label_chars: int = 32

    def resolved_label_colors(self) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        """Return (bg, fg) for the label bar, respecting explicit overrides."""
        if self.label_bg_color is not None and self.label_fg_color is not None:
            return self.label_bg_color, self.label_fg_color
        if self.label_style == "light":
            bg = self.label_bg_color or (245, 245, 245, 230)
            fg = self.label_fg_color or (30, 30, 30, 255)
        else:  # "dark"
            bg = self.label_bg_color or (20, 20, 20, 192)
            fg = self.label_fg_color or (255, 255, 255, 255)
        return bg, fg


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
        self._panel_font = _load_font_with_size(self.config, self.config.panel_font_size)
        self._cursor_history: list[tuple[int, int]] = []

    def reset_cursor(self) -> None:
        self._cursor_history.clear()

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
    ) -> Path:
        """Composite the enabled layers onto ``frame_path``; return output Path.

        ``ripple_stage`` is 1..``ripple_stages`` for the N frames after a
        click; pass 0 when there was no click on this frame.

        ``all_labels`` is the ordered list of every step's label — indexed by
        ``step_index``. When set (and ``actions_panel=True`` in config), the
        actions-list side panel renders the last N labels with the current one
        highlighted.
        """
        src = Path(frame_path)
        dst = Path(out_path) if out_path else src.with_name(f"{src.stem}.annotated.png")
        dst.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(src) as base:
            canvas = base.convert("RGBA")

        if cursor_xy is not None:
            self._cursor_history.append(cursor_xy)
            history_cap = max(self.config.cursor_trail_length + 1, 1)
            while len(self._cursor_history) > history_cap:
                self._cursor_history.pop(0)

        if self.config.progress:
            self._draw_progress(canvas, step_index, total_steps)
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
        cfg = self.config
        w, h = canvas.size
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        y = h - cfg.progress_height
        od.rectangle([0, y, w, h], fill=cfg.progress_bg_color)
        frac = (step_index + 1) / max(total_steps, 1)
        od.rectangle([0, y, int(w * frac), h], fill=cfg.progress_color)
        canvas.alpha_composite(overlay)

    def _draw_ripple(
        self,
        canvas: Image.Image,
        at: tuple[int, int],
        stage: int,
    ) -> None:
        cfg = self.config
        # stage 1..N — radius grows, alpha fades
        t = min(1.0, stage / max(cfg.ripple_stages, 1))
        radius = int(cfg.ripple_radius_min + t * (cfg.ripple_radius_max - cfg.ripple_radius_min))
        alpha = max(0, int(cfg.ripple_alpha_start * (1 - t)))
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.ellipse(
            [at[0] - radius, at[1] - radius, at[0] + radius, at[1] + radius],
            outline=(*cfg.ripple_color, alpha),
            width=cfg.ripple_width,
        )
        canvas.alpha_composite(overlay)

    def _draw_cursor(self, canvas: Image.Image) -> None:
        cfg = self.config
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)

        trail = self._cursor_history[:-1]
        if trail:
            for i, pos in enumerate(trail):
                # Older = fainter
                fade = (i + 1) / len(trail)
                alpha = int(cfg.cursor_trail_alpha_max * fade)
                r = max(2, cfg.cursor_size // 3)
                od.ellipse(
                    [pos[0] - r, pos[1] - r, pos[0] + r, pos[1] + r],
                    fill=(*cfg.cursor_color[:3], alpha),
                )

        cx, cy = self._cursor_history[-1]
        s = cfg.cursor_size
        od.polygon(
            [
                (cx, cy - s // 2),
                (cx + s // 2, cy),
                (cx, cy + s // 2),
                (cx - s // 2, cy),
            ],
            fill=cfg.cursor_color,
            outline=(0, 0, 0, 220),
        )
        canvas.alpha_composite(overlay)

    def _draw_label(self, canvas: Image.Image, text: str) -> None:
        cfg = self.config
        bg_color, fg_color = cfg.resolved_label_colors()
        wrapped = _wrap(text, cfg.label_max_chars)

        measure = ImageDraw.Draw(canvas)
        bbox = measure.multiline_textbbox((0, 0), wrapped, font=self._font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        box_w = text_w + 2 * cfg.label_padding_x
        box_h = text_h + 2 * cfg.label_padding_y

        img_w, img_h = canvas.size
        x = max(0, (img_w - box_w) // 2)
        y = img_h - box_h - cfg.label_margin if cfg.label_position == "bottom" else cfg.label_margin

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            radius=cfg.label_radius,
            fill=bg_color,
        )
        # Draw text on the same overlay for correct compositing
        od.multiline_text(
            (x + cfg.label_padding_x, y + cfg.label_padding_y - bbox[1]),
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
        cfg = self.config
        # Window into the labels: last N up to and including current.
        window_end = min(step_index + 1, len(all_labels))
        window_start = max(0, window_end - cfg.panel_max_rows)
        rows = all_labels[window_start:window_end]
        if not rows:
            return
        # Truncate long labels — panel is space-constrained.
        rows = [_truncate(row, cfg.panel_max_label_chars) for row in rows]
        current_local_idx = window_end - 1 - window_start

        # Measure the panel.
        measure = ImageDraw.Draw(canvas)
        row_heights: list[int] = []
        row_widths: list[int] = []
        for i, text in enumerate(rows):
            marker = cfg.panel_current_marker if i == current_local_idx else cfg.panel_done_marker
            line = f"{marker}{text}"
            bbox = measure.textbbox((0, 0), line, font=self._panel_font)
            row_widths.append(int(bbox[2] - bbox[0]))
            row_heights.append(int(bbox[3] - bbox[1]))
        text_w = min(cfg.panel_max_width, max(row_widths))
        text_h = sum(row_heights) + cfg.panel_row_spacing * max(len(rows) - 1, 0)
        box_w = text_w + 2 * cfg.panel_padding_x
        box_h = text_h + 2 * cfg.panel_padding_y

        img_w, _ = canvas.size
        x = img_w - box_w - cfg.panel_margin
        y = cfg.panel_margin

        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            radius=cfg.panel_radius,
            fill=cfg.panel_bg_color,
        )

        row_y = y + cfg.panel_padding_y
        for i, text in enumerate(rows):
            is_current = i == current_local_idx
            marker = cfg.panel_current_marker if is_current else cfg.panel_done_marker
            line = f"{marker}{text}"
            row_h = row_heights[i]
            # Current row gets a subtle highlight strip behind it.
            if is_current:
                od.rounded_rectangle(
                    [
                        x + cfg.panel_padding_x // 2,
                        row_y - 2,
                        x + box_w - cfg.panel_padding_x // 2,
                        row_y + row_h + 2,
                    ],
                    radius=4,
                    fill=cfg.panel_current_bg_color,
                )
            od.text(
                (x + cfg.panel_padding_x, row_y),
                line,
                font=self._panel_font,
                fill=cfg.panel_current_fg_color if is_current else cfg.panel_fg_color,
            )
            row_y += row_h + cfg.panel_row_spacing

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
