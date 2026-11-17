"""Declarative Pillow primitives for highlighting regions on video frames.

Annotations are deliberately independent of the recorder and annotation pipeline:
load them from a small YAML/JSON document, select the annotations for a step, then
composite them onto an RGBA frame. Coordinates use ``(x, y, width, height)`` boxes
and ``(x, y)`` points in the current frame's pixel space.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

import yaml
from PIL import Image, ImageDraw, ImageFont

__all__ = [
    "RegionAnnotation",
    "annotations_for_step",
    "draw_annotations",
    "load_annotations",
]

RGBA = tuple[int, int, int, int]
Point = tuple[int, int]
BBox = tuple[int, int, int, int]
Kind = Literal["rect", "circle", "arrow", "text", "spotlight"]

_DEFAULT_COLORS: dict[str, RGBA] = {
    "rect": (255, 80, 80, 255),
    "circle": (255, 190, 40, 255),
    "arrow": (255, 100, 60, 255),
    "text": (255, 255, 255, 255),
    "spotlight": (0, 0, 0, 255),
}
_DEFAULT_TEXT_FILL: RGBA = (20, 24, 32, 220)


@dataclass(slots=True)
class RegionAnnotation:
    """One region annotation and its inclusive step visibility range.

    ``bbox`` is required for rectangles, circles/ellipses, and spotlights. Arrows
    require both ``start`` and ``end``. Text requires ``text`` plus either a bbox
    (the label is placed nearby) or ``start`` (the label's explicit anchor).

    ``color`` defaults according to ``kind``. For text it is the foreground color;
    ``fill`` controls the label background. For spotlights, the RGB channels of
    ``color`` tint the dimming layer and ``dim_alpha`` controls its opacity.
    """

    kind: Kind
    bbox: BBox | None = None
    start: Point | None = None
    end: Point | None = None
    text: str | None = None
    start_step: int = 0
    end_step: int | None = None
    color: RGBA | None = None
    width: int = 4
    padding: int = 6
    radius: int = 10
    fill: RGBA | None = None
    dim_alpha: int = 120

    def __post_init__(self) -> None:
        """Normalize numeric values and validate geometry for this annotation kind."""
        if self.kind not in _DEFAULT_COLORS:
            raise ValueError(
                f"unknown annotation kind {self.kind!r}; expected rect, circle, arrow, text, or spotlight"
            )
        if self.bbox is not None:
            self.bbox = _bbox(self.bbox)
        if self.start is not None:
            self.start = _point(self.start, "start")
        if self.end is not None:
            self.end = _point(self.end, "end")
        if self.text is not None and not isinstance(self.text, str):
            raise ValueError(f"text must be a string, got {type(self.text).__name__}")

        if self.kind in {"rect", "circle", "spotlight"} and self.bbox is None:
            raise ValueError(f"{self.kind} annotation requires bbox")
        if self.kind == "arrow" and (self.start is None or self.end is None):
            raise ValueError("arrow annotation requires start and end points")
        if self.kind == "text":
            if self.text is None:
                raise ValueError("text annotation requires text")
            if self.bbox is None and self.start is None:
                raise ValueError("text annotation requires bbox or start point")

        self.start_step = _integer(self.start_step, "start_step")
        if self.end_step is not None:
            self.end_step = _integer(self.end_step, "end_step")
            if self.end_step < self.start_step:
                raise ValueError("end_step must be greater than or equal to start_step")
        self.width = _integer(self.width, "width")
        self.padding = _integer(self.padding, "padding")
        self.radius = _integer(self.radius, "radius")
        self.dim_alpha = _integer(self.dim_alpha, "dim_alpha")
        if self.width <= 0:
            raise ValueError("width must be greater than zero")
        if self.padding < 0 or self.radius < 0:
            raise ValueError("padding and radius must be non-negative")
        if not 0 <= self.dim_alpha <= 255:
            raise ValueError("dim_alpha must be between 0 and 255")

        self.color = (
            _rgba(self.color, "color") if self.color is not None else _DEFAULT_COLORS[self.kind]
        )
        if self.fill is not None:
            self.fill = _rgba(self.fill, "fill")


def load_annotations(path: str | Path) -> list[RegionAnnotation]:
    """Load annotations from a YAML or JSON list, or ``annotations: [...]`` mapping.

    Parsing uses :func:`yaml.safe_load` for both formats. Structural errors,
    unknown keys, invalid values, and missing geometry are reported as
    :class:`ValueError` with the annotation index where applicable.
    """
    source = Path(path)
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid annotation YAML in {source}: {exc}") from exc

    if isinstance(data, dict):
        unknown = set(data) - {"annotations"}
        if unknown:
            raise ValueError(
                f"unknown top-level annotation key(s): {', '.join(sorted(map(str, unknown)))}"
            )
        if "annotations" not in data:
            raise ValueError("annotation mapping must contain an 'annotations' key")
        data = data["annotations"]
    if not isinstance(data, list):
        raise ValueError("annotation file must contain a list or a mapping with key 'annotations'")

    allowed = {field.name for field in fields(RegionAnnotation)}
    specs: list[RegionAnnotation] = []
    for index, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"annotation {index} must be a mapping")
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                f"annotation {index} has unknown key(s): {', '.join(sorted(map(str, unknown)))}"
            )
        try:
            specs.append(RegionAnnotation(**raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid annotation {index}: {exc}") from exc
    return specs


def annotations_for_step(
    specs: Iterable[RegionAnnotation], step_index: int
) -> list[RegionAnnotation]:
    """Return annotations visible at ``step_index``, preserving their input order."""
    return [
        spec
        for spec in specs
        if spec.start_step <= step_index and (spec.end_step is None or step_index <= spec.end_step)
    ]


def draw_annotations(
    canvas: Image.Image,
    specs: Iterable[RegionAnnotation],
    *,
    step_index: int,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont | None = None,
    phase: float = 0.0,
) -> Image.Image:
    """Composite annotations active at ``step_index`` onto an RGBA ``canvas``.

    The input image is modified in place and returned. Each primitive is first
    drawn on a transparent overlay so translucent colors blend correctly. Shapes
    are clipped to the frame; empty and wholly offscreen shapes are safe no-ops.
    ``phase`` is accepted for pipeline compatibility and future animation, but
    does not currently alter the deterministic primitives.
    """
    if canvas.mode != "RGBA":
        raise ValueError(f"canvas must be RGBA, got {canvas.mode}")
    if canvas.width <= 0 or canvas.height <= 0:
        return canvas
    _ = phase
    resolved_font = font if font is not None else ImageFont.load_default()

    for spec in annotations_for_step(specs, step_index):
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        if spec.kind == "rect":
            _draw_rect(draw, spec, canvas.size)
        elif spec.kind == "circle":
            _draw_circle(draw, spec, canvas.size)
        elif spec.kind == "arrow":
            _draw_arrow(draw, spec, canvas.size)
        elif spec.kind == "text":
            _draw_text(draw, spec, canvas.size, resolved_font)
        else:
            _draw_spotlight(draw, spec, canvas.size)
        canvas.alpha_composite(overlay)
    return canvas


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError(f"{name} must be an integer (integral floats are accepted)")


def _point(value: object, name: str) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be an (x, y) pair")
    return (_integer(value[0], f"{name} x"), _integer(value[1], f"{name} y"))


def _bbox(value: object) -> BBox:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must be an (x, y, width, height) sequence")
    x, y, width, height = (
        _integer(component, f"bbox {name}")
        for component, name in zip(value, ("x", "y", "width", "height"), strict=True)
    )
    if width < 0 or height < 0:
        raise ValueError("bbox width and height must be non-negative")
    return (x, y, width, height)


def _rgba(value: object, name: str) -> RGBA:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{name} must be an (R, G, B, A) sequence")
    channels = tuple(_integer(channel, f"{name} channel") for channel in value)
    if any(channel < 0 or channel > 255 for channel in channels):
        raise ValueError(f"{name} channels must be between 0 and 255")
    return (channels[0], channels[1], channels[2], channels[3])


def _clip_bbox(bbox: BBox | None, size: tuple[int, int]) -> BBox | None:
    """Clip ``(x, y, w, h)`` to inclusive Pillow coordinates."""
    if bbox is None:
        return None
    x, y, width, height = bbox
    if width <= 0 or height <= 0:
        return None
    image_width, image_height = size
    left = max(0, x)
    top = max(0, y)
    right = min(image_width - 1, x + width - 1)
    bottom = min(image_height - 1, y + height - 1)
    if left > right or top > bottom:
        return None
    return (left, top, right, bottom)


def _draw_rect(draw: ImageDraw.ImageDraw, spec: RegionAnnotation, size: tuple[int, int]) -> None:
    box = _clip_bbox(spec.bbox, size)
    if box is None:
        return
    left, top, right, bottom = box
    radius = min(spec.radius, (right - left) // 2, (bottom - top) // 2)
    draw.rounded_rectangle(
        box,
        radius=max(0, radius),
        fill=spec.fill,
        outline=spec.color,
        width=spec.width,
    )


def _draw_circle(draw: ImageDraw.ImageDraw, spec: RegionAnnotation, size: tuple[int, int]) -> None:
    box = _clip_bbox(spec.bbox, size)
    if box is not None:
        draw.ellipse(box, fill=spec.fill, outline=spec.color, width=spec.width)


def _draw_arrow(draw: ImageDraw.ImageDraw, spec: RegionAnnotation, size: tuple[int, int]) -> None:
    assert spec.start is not None and spec.end is not None  # validated by RegionAnnotation
    line = _clip_line(spec.start, spec.end, size)
    if line is None:
        return
    start, end = spec.start, spec.end
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return

    draw.line(line, fill=spec.color, width=spec.width)
    ux, uy = dx / length, dy / length
    head_length = max(8.0, spec.width * 3.0)
    head_half_width = head_length * 0.55
    base_x = end[0] - ux * head_length
    base_y = end[1] - uy * head_length
    px, py = -uy * head_half_width, ux * head_half_width
    head = [
        end,
        (round(base_x + px), round(base_y + py)),
        (round(base_x - px), round(base_y - py)),
    ]
    draw.polygon(head, fill=spec.color)


def _clip_line(start: Point, end: Point, size: tuple[int, int]) -> tuple[Point, Point] | None:
    """Liang-Barsky clip against the image, returning integer endpoints."""
    image_width, image_height = size
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    lower, upper = 0.0, 1.0
    for p, q in (
        (-dx, x0),
        (dx, image_width - 1 - x0),
        (-dy, y0),
        (dy, image_height - 1 - y0),
    ):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            if ratio > upper:
                return None
            lower = max(lower, ratio)
        else:
            if ratio < lower:
                return None
            upper = min(upper, ratio)
    return (
        (round(x0 + lower * dx), round(y0 + lower * dy)),
        (round(x0 + upper * dx), round(y0 + upper * dy)),
    )


def _draw_text(
    draw: ImageDraw.ImageDraw,
    spec: RegionAnnotation,
    size: tuple[int, int],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
) -> None:
    assert spec.text is not None  # validated by RegionAnnotation
    image_width, image_height = size
    anchor_box: BBox | None = None
    if spec.start is not None:
        x, y = spec.start
        if not (0 <= x < image_width and 0 <= y < image_height):
            return
    else:
        anchor_box = _clip_bbox(spec.bbox, size)
        if anchor_box is None:
            return
        x, y = anchor_box[:2]

    text_box = draw.multiline_textbbox((0, 0), spec.text, font=font, spacing=4)
    text_width = max(0, math.ceil(text_box[2] - text_box[0]))
    text_height = max(0, math.ceil(text_box[3] - text_box[1]))
    label_width = text_width + 2 * spec.padding
    label_height = text_height + 2 * spec.padding

    if anchor_box is not None:
        y = anchor_box[1] - label_height - spec.padding
        if y < 0:
            y = anchor_box[3] + 1 + spec.padding
    x = max(0, min(x, image_width - max(1, label_width)))
    y = max(0, min(y, image_height - max(1, label_height)))
    right = min(image_width - 1, x + max(1, label_width) - 1)
    bottom = min(image_height - 1, y + max(1, label_height) - 1)
    radius = min(spec.radius, max(0, (right - x) // 2), max(0, (bottom - y) // 2))
    draw.rounded_rectangle(
        (x, y, right, bottom),
        radius=radius,
        fill=spec.fill if spec.fill is not None else _DEFAULT_TEXT_FILL,
    )
    draw.multiline_text(
        (x + spec.padding - text_box[0], y + spec.padding - text_box[1]),
        spec.text,
        font=font,
        fill=spec.color,
        spacing=4,
    )


def _draw_spotlight(
    draw: ImageDraw.ImageDraw, spec: RegionAnnotation, size: tuple[int, int]
) -> None:
    image_width, image_height = size
    assert spec.color is not None  # assigned by RegionAnnotation
    draw.rectangle(
        (0, 0, image_width - 1, image_height - 1),
        fill=(*spec.color[:3], spec.dim_alpha),
    )
    cutout = _clip_bbox(spec.bbox, size)
    if cutout is not None:
        # This is a fresh per-primitive overlay: replacing with transparent pixels
        # cuts a hole without erasing any previously composited annotations.
        draw.rectangle(cutout, fill=(0, 0, 0, 0))
