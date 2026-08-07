"""Pixel-grid overlay for agent spatial understanding (see #171).

Draws a light gridline + coordinate-label overlay onto a captured frame so
LLM agents consuming reels/screenshots can measure spatial distances by
reading label coordinates instead of counting pixels by eye.

Two visual styles:

- ``"full"`` — major gridlines every ``pitch`` pixels + minor gridlines
  every ``pitch // 10`` pixels + coordinate labels along the top and left
  edges at every major-line intersection.
- ``"ruler"`` — only the top+left coordinate label strips; no gridlines.
  Useful when overlay clutter over content matters more than the ability
  to visually check alignment against a line.

Pure Pillow: no imports from the CLI, Config, or feedback layer so this
module stays trivially composable and unit-testable. The layer order in
the pipeline is content → grid → highlights → arrows → labels — the grid
draws BEHIND click ripples / sticky arrows / cursors / action labels so
those signals stay legible.

Zoom-on-click compatibility: this module operates on the CURRENT pixel
buffer. The pipeline is responsible for invoking :func:`draw_grid` AFTER
the zoom pass so the coordinates that appear on screen match what a user
sees on the zoomed image (an agent reading "x=100" on a zoomed frame is
measuring 100 zoomed pixels from the left edge, not 100 pre-zoom pixels).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

__all__ = ["GridConfig", "draw_grid", "parse_rgba_hex"]

_BUNDLED_FONT = "DejaVuSans.ttf"


@dataclass(slots=True)
class GridConfig:
    """Configuration for the pixel-grid overlay.

    Attributes:
        enabled: When False, :func:`draw_grid` is a no-op — the caller
            can build a config unconditionally and let this flag decide
            whether to render.
        pitch: Major-line spacing in pixels. Minor lines are drawn at
            ``pitch // 10`` (rounded down; disabled when the quotient is
            less than 2). Must be > 0; :func:`draw_grid` raises
            :class:`ValueError` otherwise.
        color: RGBA hex string for the MAJOR gridline color. Also drives
            the default minor-line color (with alpha scaled down) and the
            label color. Accepts ``#RRGGBB`` (assumes alpha=0xFF) and
            ``#RRGGBBAA``; case-insensitive; leading ``#`` optional.
        style: ``"full"`` for gridlines + labels; ``"ruler"`` for a
            top+left label strip only (no gridlines drawn across the
            image body).
        label_font_size: Point size for the coordinate labels. Small
            enough by default (12) to sit unobtrusively along the edges;
            callers rendering onto very large frames may want to bump it.
    """

    enabled: bool = False
    pitch: int = 100
    color: str = "#FFFFFF33"
    style: Literal["full", "ruler"] = "full"
    label_font_size: int = 12


def parse_rgba_hex(value: str) -> tuple[int, int, int, int]:
    """Parse an ``#RRGGBB`` / ``#RRGGBBAA`` string into an ``(R, G, B, A)`` tuple.

    Case-insensitive. Leading ``#`` optional. ``#RRGGBB`` implies alpha=0xFF.
    Raises :class:`ValueError` on any malformed input (wrong length, non-hex
    characters) so callers get a clear signal rather than a silent default.
    """
    if not isinstance(value, str):
        raise ValueError(f"grid color must be a string, got {type(value).__name__}")
    s = value.strip().lstrip("#")
    if len(s) not in (6, 8):
        raise ValueError(f"invalid RGBA hex {value!r}: expected #RRGGBB or #RRGGBBAA")
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        a = int(s[6:8], 16) if len(s) == 8 else 0xFF
    except ValueError as e:
        raise ValueError(f"invalid RGBA hex {value!r}: {e}") from e
    return (r, g, b, a)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the bundled DejaVuSans truetype at ``size`` points.

    Mirrors :func:`clickcast.annotate.annotator._load_font_with_size` so
    both modules resolve the same font file the same way. Falls back to
    the Pillow bitmap default if the packaged font is missing (which
    signals a packaging bug — the font ships with the wheel).
    """
    resource = files("clickcast.annotate").joinpath("fonts").joinpath(_BUNDLED_FONT)
    try:
        data = resource.read_bytes()
    except (FileNotFoundError, OSError):
        return ImageFont.load_default()
    return ImageFont.truetype(io.BytesIO(data), size)


def draw_grid(image: Image.Image, cfg: GridConfig) -> Image.Image:
    """Composite the configured grid overlay onto ``image``.

    Returns the composited image. When the input is already ``"RGBA"``,
    mutates in place and returns the same reference (matching how the
    annotator's other layer helpers behave). When the input is any other
    mode (typically ``"RGB"``), an RGBA copy is built for the composite
    and returned — the caller is responsible for converting back if they
    want RGB on disk.

    No-op when ``cfg.enabled`` is False (returns the input unchanged).

    Raises :class:`ValueError` when ``cfg.pitch <= 0`` — a nonsensical
    input we prefer to surface loudly rather than coerce silently. The
    RGBA hex is validated eagerly too, via :func:`parse_rgba_hex`.
    """
    if not cfg.enabled:
        return image
    if cfg.pitch <= 0:
        raise ValueError(f"grid pitch must be > 0, got {cfg.pitch}")

    major_color = parse_rgba_hex(cfg.color)
    # Minor lines: same RGB, alpha scaled to ~40% of major so they read as
    # a weaker background hint rather than competing with major lines.
    minor_alpha = max(0, min(255, int(major_color[3] * 0.4)))
    minor_color = (*major_color[:3], minor_alpha)
    # Labels: keep full opacity of the requested RGB so numbers are always
    # readable. The label text piggybacks on the color the user chose so
    # a red / cyan / bespoke grid stays visually cohesive.
    label_color = (*major_color[:3], 0xFF)

    # Pillow only blends RGBA-onto-RGBA via alpha_composite; RGB targets
    # need an intermediate conversion. Preserve in-place semantics for
    # the common (annotator) path where the caller has already converted.
    canvas = image if image.mode == "RGBA" else image.convert("RGBA")

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    w, h = canvas.size

    if cfg.style == "full":
        _draw_full(od, w, h, cfg.pitch, major_color, minor_color)
    # Baseline strip for the ruler style — a soft dark background behind
    # the label strips so pale labels don't wash out over a white page.
    if cfg.style == "ruler":
        _draw_ruler_strip(od, w, h, cfg.label_font_size)

    # Labels last so they always sit on top of the gridlines.
    _draw_labels(od, w, h, cfg.pitch, label_color, font_size=cfg.label_font_size, style=cfg.style)

    canvas.alpha_composite(overlay)
    return canvas


def _draw_full(
    od: ImageDraw.ImageDraw,
    w: int,
    h: int,
    pitch: int,
    major_color: tuple[int, int, int, int],
    minor_color: tuple[int, int, int, int],
) -> None:
    """Draw minor gridlines first, then major on top.

    Minor lines land at multiples of ``pitch // 10``; skipped entirely when
    the quotient falls below 2 (would draw a line on every pixel — noise).
    Major lines land at multiples of ``pitch``. Both loops start at ``pitch``
    (not 0) — the image edges themselves already visually bound the frame.
    """
    minor_pitch = pitch // 10
    if minor_pitch >= 2:
        x = minor_pitch
        while x < w:
            if x % pitch != 0:  # major lines drawn separately, on top
                od.line([(x, 0), (x, h - 1)], fill=minor_color, width=1)
            x += minor_pitch
        y = minor_pitch
        while y < h:
            if y % pitch != 0:
                od.line([(0, y), (w - 1, y)], fill=minor_color, width=1)
            y += minor_pitch

    x = pitch
    while x < w:
        od.line([(x, 0), (x, h - 1)], fill=major_color, width=1)
        x += pitch
    y = pitch
    while y < h:
        od.line([(0, y), (w - 1, y)], fill=major_color, width=1)
        y += pitch


def _draw_ruler_strip(od: ImageDraw.ImageDraw, w: int, h: int, font_size: int) -> None:
    """Draw a thin translucent-dark strip along the top and left edges.

    Guarantees ruler-mode labels have contrast even over a bright page
    background. Sized to just cover the label glyphs plus 2px padding so
    it never eats significantly into the content.
    """
    strip = font_size + 4
    # Fill uses a low alpha so page content shows through — this is a
    # backdrop, not a visual blocker.
    fill = (0, 0, 0, 96)
    od.rectangle([0, 0, w, strip], fill=fill)
    od.rectangle([0, 0, strip, h], fill=fill)


def _draw_labels(
    od: ImageDraw.ImageDraw,
    w: int,
    h: int,
    pitch: int,
    color: tuple[int, int, int, int],
    *,
    font_size: int,
    style: Literal["full", "ruler"],
) -> None:
    """Draw coordinate labels along top (x=pitch, 2*pitch, ...) and left.

    Labels appear at every major-line coordinate that lies strictly inside
    the frame (``coord < w`` / ``coord < h``). The first tick (0,0) is
    skipped — a "0" label at the corner adds no information and clutters
    the anchor. When the image dimension is not divisible by pitch, labels
    only appear up to the last full major boundary.

    Text sits inset by a couple of pixels from each edge — enough to keep
    labels legible without pushing them noticeably into the content area.
    In ruler mode the label backdrop (see :func:`_draw_ruler_strip`) makes
    labels legible over any background; in full mode labels sit on top of
    the gridline they annotate.
    """
    font = _load_font(font_size)
    inset = 2

    # Top: x labels
    x = pitch
    while x < w:
        text = str(x)
        od.text((x + inset, inset), text, font=font, fill=color)
        x += pitch

    # Left: y labels. Ruler mode already has a left backdrop strip; full
    # mode draws them next to the major line. Either way the label sits a
    # couple of pixels right of the gridline / edge.
    y = pitch
    while y < h:
        text = str(y)
        od.text((inset, y + inset), text, font=font, fill=color)
        y += pitch

    # In full mode, an explicit corner "0" is redundant with the visible
    # frame edge and would sit under the actions panel more often than
    # not; leave it off in both modes. See docstring rationale.
    _ = style  # accepted for future style-specific label decisions
