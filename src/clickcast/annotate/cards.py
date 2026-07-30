"""Title + summary card renderers — bookends for a human-observable tour.

Two functions, one shape: :func:`render_title_card` and :func:`render_summary_card`
each produce N identical PNG frames of the requested size, held for a caller-
supplied duration. The caller (:func:`clickcast.auto.run_tour` for the auto
tour) prepends the title frames and appends the summary frames to the
recorder's output directory, then rewrites ``frames.json`` so the encoder
picks them up transparently.

The cards are intentionally minimalist — dark background, a big centred
title line, and one or two smaller subtitle/body lines. No animation (each
of the N frames is a byte-identical copy of the first — that keeps the
encoder happy and matches how :meth:`Recorder.post_action` pads dwell time
for regular steps). See #129 Track E.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

__all__ = [
    "CardStyle",
    "SummaryStats",
    "render_summary_card",
    "render_title_card",
]

_BUNDLED_FONT = "DejaVuSans.ttf"


@dataclass(slots=True, frozen=True)
class CardStyle:
    """Look-and-feel for title/summary cards.

    Everything is a value type so callers can override piecewise without a
    wall of keyword arguments. The defaults are tuned for the 1280x800
    viewport shipped in most of clickcast's demos and READMEs — high-
    contrast dark background so any subsequent light-mode page transition
    reads as an intentional cut, not a flash of white (Track G of #129).
    """

    bg_color: tuple[int, int, int] = (18, 22, 32)
    title_color: tuple[int, int, int] = (240, 240, 250)
    subtitle_color: tuple[int, int, int] = (170, 180, 200)
    title_font_size: int = 56
    subtitle_font_size: int = 26
    line_spacing: int = 24


@dataclass(slots=True, frozen=True)
class SummaryStats:
    """The numeric facts a summary card should render.

    Every field carries its own noun so the summary line reads as prose —
    "4 pages · 8 clicks · 22 s". Extra fields (e.g. errors) can be added as
    a follow-up without changing the caller shape thanks to
    ``@dataclass(frozen=True)`` field defaults.
    """

    pages: int
    clicks: int
    duration_s: float
    frame_count: int = 0
    watermark: str = ""
    extra: tuple[str, ...] = field(default_factory=tuple)


def render_title_card(
    out_dir: Path,
    *,
    title: str,
    subtitle: str = "",
    size: tuple[int, int] = (1280, 800),
    frame_count: int = 12,
    filename_prefix: str = "title",
    style: CardStyle | None = None,
) -> list[Path]:
    """Write ``frame_count`` identical PNG frames to ``out_dir``.

    Returns the ordered list of paths. The caller is responsible for
    inserting these into any ``frames.json`` manifest so the encoder picks
    them up.
    """
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    st = style or CardStyle()
    frame = _compose_card(size, title=title, subtitle=subtitle, style=st)
    return _persist_frames(frame, out_dir, filename_prefix, frame_count)


def render_summary_card(
    out_dir: Path,
    *,
    stats: SummaryStats,
    title: str = "Tour complete",
    size: tuple[int, int] = (1280, 800),
    frame_count: int = 16,
    filename_prefix: str = "summary",
    style: CardStyle | None = None,
) -> list[Path]:
    """Write ``frame_count`` identical summary-card frames to ``out_dir``.

    The subtitle is composed from ``stats`` — one dot-separated line under
    the title. Extra strings from ``stats.extra`` become additional
    subtitle lines. ``stats.watermark`` renders on the very last line as a
    small trailing footer.
    """
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    st = style or CardStyle()
    parts: list[str] = [
        f"{stats.pages} page{'s' if stats.pages != 1 else ''}",
        f"{stats.clicks} click{'s' if stats.clicks != 1 else ''}",
        f"{stats.duration_s:.1f} s",
    ]
    if stats.frame_count:
        parts.append(f"{stats.frame_count} frames")
    subtitle_lines: list[str] = [" · ".join(parts)]
    subtitle_lines.extend(stats.extra)
    if stats.watermark:
        subtitle_lines.append(stats.watermark)
    subtitle = "\n".join(subtitle_lines)
    frame = _compose_card(size, title=title, subtitle=subtitle, style=st)
    return _persist_frames(frame, out_dir, filename_prefix, frame_count)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_bundled_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    resource = files("clickcast.annotate").joinpath("fonts").joinpath(_BUNDLED_FONT)
    try:
        data = resource.read_bytes()
    except (FileNotFoundError, OSError):
        return ImageFont.load_default()
    import io

    return ImageFont.truetype(io.BytesIO(data), size)


def _compose_card(
    size: tuple[int, int],
    *,
    title: str,
    subtitle: str,
    style: CardStyle,
) -> Image.Image:
    """Return one composed RGB frame (not persisted)."""
    w, h = size
    canvas = Image.new("RGB", (w, h), style.bg_color)
    draw = ImageDraw.Draw(canvas)
    title_font = _load_bundled_font(style.title_font_size)
    subtitle_font = _load_bundled_font(style.subtitle_font_size)

    # Measure + centre. multiline_textbbox handles newlines correctly.
    title_bbox = draw.multiline_textbbox((0, 0), title, font=title_font, align="center")
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]

    subtitle_h = 0
    # Pillow's multiline_textbbox returns tuple[float, float, float, float];
    # the y offsets we pull out of it feed into Draw.multiline_text which
    # accepts floats. Keep the type wide so mypy doesn't complain about the
    # float→int narrowing that isn't needed.
    subtitle_bbox: tuple[float, float, float, float] | None = None
    if subtitle:
        subtitle_bbox = draw.multiline_textbbox(
            (0, 0), subtitle, font=subtitle_font, align="center"
        )
        subtitle_h = int(subtitle_bbox[3] - subtitle_bbox[1])

    total_h = title_h + (style.line_spacing + subtitle_h if subtitle else 0)
    title_x = (w - title_w) // 2
    title_y = (h - total_h) // 2 - title_bbox[1]
    draw.multiline_text(
        (title_x, title_y),
        title,
        font=title_font,
        fill=style.title_color,
        align="center",
    )
    if subtitle and subtitle_bbox is not None:
        subtitle_w = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_x = (w - subtitle_w) // 2
        subtitle_y = title_y + title_h + style.line_spacing - subtitle_bbox[1]
        draw.multiline_text(
            (subtitle_x, subtitle_y),
            subtitle,
            font=subtitle_font,
            fill=style.subtitle_color,
            align="center",
        )
    return canvas


def _persist_frames(
    frame: Image.Image,
    out_dir: Path,
    prefix: str,
    count: int,
) -> list[Path]:
    """Save the first frame, then copy it ``count - 1`` more times."""
    out_dir.mkdir(parents=True, exist_ok=True)
    first_path = out_dir / f"{prefix}-000.png"
    frame.save(first_path, format="PNG")
    paths = [first_path]
    for i in range(1, count):
        path = out_dir / f"{prefix}-{i:03d}.png"
        shutil.copyfile(first_path, path)
        paths.append(path)
    return paths
