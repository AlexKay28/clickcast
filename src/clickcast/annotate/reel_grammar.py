"""Deterministic reel-grammar pass — ships the compositing half of #302.

A reel produced from screenshots is a slideshow unless the frames carry the
visual grammar of someone *operating* the product: a press, a ripple, a
directional transition, a caption and a sense of position. This module adds
that grammar as a post-capture pass over a frame directory:

* **Click feedback** — for every frame where the cursor appears or jumps
  (``cursor_xy`` goes from ``None`` to a point, or moves at least
  :data:`_CLICK_MIN_MOVE` pixels), an anti-aliased ring expands over the next
  :data:`_RIPPLE_STAGES` frames. The frame immediately before the action gets
  a small dimmed press dot plus a tighter ring.
* **Transition grammar** — each step boundary is classified by its URL. A
  same-URL boundary (SPA state change) stays a dissolve, exactly as
  :mod:`clickcast.annotate.smooth` renders it — this pass leaves those pixels
  alone. A URL change (cross-document navigation) becomes a short,
  leftwards slide with a hold on the destination frame. When the manifest
  carries no URL information and the caller passes no ``step_urls``, every
  boundary falls back to a dissolve; the pass never guesses a navigation.
* **Captions** — a step's label becomes a lower-third caption: a
  semi-transparent rounded plate plus text, fading in over the first frames
  of the step. Label-less steps are untouched.
* **Timeline** — a thin progress bar along the bottom edge, filled by global
  frame position.

Pixels are rewritten **in place**; file names, frame count and the manifest
are untouched, so duration and step attribution are unchanged. Every effect
is a pure function of the manifest and the input pixels — no randomness, no
wall-clock — so two runs over identical input produce byte-identical output.
Disabling every toggle (:class:`GrammarConfig`) is a strict no-op: no file is
opened for writing.

This module deliberately does **not** insert frames (that is ``smooth.py``'s
job) and does **not** read the sidecar itself. The maintainer wires it with a
single call:

.. code-block:: python

    apply_reel_grammar(frames_dir, step_urls=urls, step_labels=labels)

where ``urls`` / ``labels`` are indexed by ``step_index``.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

__all__ = ["GrammarConfig", "GrammarResult", "apply_reel_grammar"]

# Click feedback -----------------------------------------------------------
_RIPPLE_STAGES = 3
_RIPPLE_RADIUS_MIN = 12
_RIPPLE_RADIUS_MAX = 48
_RIPPLE_WIDTH = 3
_CLICK_MIN_MOVE = 32.0
_PRESS_RADIUS = 7

# Transition grammar -------------------------------------------------------
_SLIDE_FRAMES = 3

# Captions / timeline ------------------------------------------------------
_CAPTION_MARGIN_X = 32
_CAPTION_PAD_X = 16
_CAPTION_PAD_Y = 10
_CAPTION_RADIUS = 10
_CAPTION_FADE = (0.5, 0.8, 1.0)
_TIMELINE_BG = (255, 255, 255, 44)
_TIMELINE_FILL = (255, 180, 70, 235)

_DrawOp = Callable[[Image.Image], None]


@dataclass(frozen=True)
class GrammarConfig:
    """Toggles and tunables for the reel-grammar pass."""

    click_feedback: bool = True
    transitions: bool = True
    captions: bool = True
    timeline: bool = True
    caption_font_px: int = 22
    timeline_height: int = 4


@dataclass(frozen=True)
class GrammarResult:
    """Per-effect counts from one :func:`apply_reel_grammar` run."""

    frames: int = 0
    ripple_frames: int = 0
    press_frames: int = 0
    slide_transitions: int = 0
    dissolve_transitions: int = 0
    caption_frames: int = 0
    timeline_frames: int = 0


@dataclass(frozen=True)
class _Cue:
    index: int
    at: tuple[int, int]


_DEFAULT_CONFIG = GrammarConfig()


def apply_reel_grammar(
    frames_dir: Path,
    config: GrammarConfig = _DEFAULT_CONFIG,
    *,
    step_urls: Sequence[str | None] | None = None,
    step_labels: Sequence[str | None] | None = None,
) -> GrammarResult:
    """Composite the reel grammar onto ``frames_dir``'s PNGs, in place.

    ``frames_dir`` must contain ``frames.json`` with ``frames[]`` entries
    carrying ``path``, ``step_index`` and ``sub_index`` (``cursor_xy`` is used
    when present). Missing or empty manifests are a silent no-op.

    ``step_urls`` is indexed by ``step_index``; a boundary between two steps
    is a slide when both URLs are present and differ, otherwise a dissolve.
    When it is ``None`` the pass looks for a per-frame ``url`` field in the
    manifest; if that is absent too, every boundary is a dissolve.

    ``step_labels`` is indexed by ``step_index``; only non-empty labels get a
    caption.
    """
    manifest_path = frames_dir / "frames.json"
    if not manifest_path.exists():
        return GrammarResult()
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames: list[dict[str, Any]] = manifest.get("frames", [])
    total = len(frames)
    if not total:
        return GrammarResult()

    if not (config.click_feedback or config.transitions or config.captions or config.timeline):
        # Strict no-op: never open a frame for writing.
        return GrammarResult(frames=total)

    paths = [frames_dir / str(entry["path"]) for entry in frames]
    if any(not path.is_file() for path in paths):
        return GrammarResult(frames=total)

    result = GrammarResult(frames=total)

    # Transitions first: they rewrite whole frames, so every later layer
    # composites on top of the settled boundary pixels.
    if config.transitions:
        result = _apply_transitions(frames, paths, step_urls, result)

    if config.click_feedback:
        result = _apply_click_feedback(frames, paths, result)

    if config.captions and step_labels:
        result = _apply_captions(frames, paths, step_labels, config, result)

    if config.timeline:
        for index, path in enumerate(paths):
            _rewrite(path, _timeline_op(index, total, config.timeline_height))
        result = replace(result, timeline_frames=total)

    return result


# ---------------------------------------------------------------------------
# Click feedback
# ---------------------------------------------------------------------------


def _click_cues(frames: list[dict[str, Any]]) -> list[_Cue]:
    """Frames where the cursor appears after an absence or makes a real jump.

    A tiny motion threshold (``_CLICK_MIN_MOVE``) keeps interpolated cursor
    glides — which move a few pixels per frame — from firing a ripple on
    every frame.
    """
    cues: list[_Cue] = []
    last: tuple[int, int] | None = None
    gap = True
    for index, frame in enumerate(frames):
        raw = frame.get("cursor_xy")
        if not raw or len(raw) < 2:
            gap = True
            continue
        current = (int(raw[0]), int(raw[1]))
        moved = (
            last is not None
            and math.hypot(current[0] - last[0], current[1] - last[1]) >= _CLICK_MIN_MOVE
        )
        if gap or last is None or moved:
            cues.append(_Cue(index, current))
        last = current
        gap = False
    return cues


def _apply_click_feedback(
    frames: list[dict[str, Any]], paths: list[Path], result: GrammarResult
) -> GrammarResult:
    cues = _click_cues(frames)
    if not cues:
        return result
    ripple_frames = 0
    press_frames = 0
    triggered: set[int] = set()

    for cue in cues:
        step_index = frames[cue.index]["step_index"]
        stage = 0
        cursor = cue.index
        while (
            cursor < len(frames)
            and stage < _RIPPLE_STAGES
            and frames[cursor]["step_index"] == step_index
        ):
            progress = (stage + 1) / _RIPPLE_STAGES
            radius = round(
                _RIPPLE_RADIUS_MIN + progress * (_RIPPLE_RADIUS_MAX - _RIPPLE_RADIUS_MIN)
            )
            alpha = round(200 - 140 * progress)
            _rewrite(paths[cursor], _ripple_op(cue.at, radius, alpha))
            ripple_frames += 1
            triggered.add(cursor)
            cursor += 1
            stage += 1

    for cue in cues:
        before = cue.index - 1
        if before < 0 or before in triggered:
            continue
        if frames[before]["step_index"] != frames[cue.index]["step_index"]:
            continue
        _rewrite(paths[before], _press_op(cue.at))
        press_frames += 1

    return replace(result, ripple_frames=ripple_frames, press_frames=press_frames)


def _ripple_op(at: tuple[int, int], radius: int, alpha: int) -> _DrawOp:
    def draw(canvas: Image.Image) -> None:
        # A dark under-ring keeps the light ring legible on white pages.
        _draw_ring(canvas, at, radius, (15, 15, 15, max(0, alpha // 3)), _RIPPLE_WIDTH + 2)
        _draw_ring(canvas, at, radius, (255, 255, 255, alpha), _RIPPLE_WIDTH)

    return draw


def _press_op(at: tuple[int, int]) -> _DrawOp:
    def draw(canvas: Image.Image) -> None:
        _draw_ring(canvas, at, _PRESS_RADIUS + 5, (255, 255, 255, 170), 2)
        _draw_disc(canvas, at, _PRESS_RADIUS, (25, 25, 25, 120))

    return draw


def _draw_ring(
    canvas: Image.Image,
    at: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
    width: int,
    *,
    supersample: int = 4,
) -> None:
    """Anti-aliased ellipse outline drawn at ``supersample``x then box-filtered."""

    def paint(draw: ImageDraw.ImageDraw, left: int, top: int, scale: int) -> None:
        draw.ellipse(
            [
                (at[0] - radius - left) * scale,
                (at[1] - radius - top) * scale,
                (at[0] + radius - left) * scale,
                (at[1] + radius - top) * scale,
            ],
            outline=color,
            width=max(1, width * scale),
        )

    _draw_aa(
        canvas,
        at[0] - radius,
        at[1] - radius,
        at[0] + radius,
        at[1] + radius,
        paint,
        width + 2,
        supersample,
    )


def _draw_disc(
    canvas: Image.Image,
    at: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
    *,
    supersample: int = 4,
) -> None:
    def paint(draw: ImageDraw.ImageDraw, left: int, top: int, scale: int) -> None:
        draw.ellipse(
            [
                (at[0] - radius - left) * scale,
                (at[1] - radius - top) * scale,
                (at[0] + radius - left) * scale,
                (at[1] + radius - top) * scale,
            ],
            fill=color,
        )

    _draw_aa(
        canvas,
        at[0] - radius,
        at[1] - radius,
        at[0] + radius,
        at[1] + radius,
        paint,
        2,
        supersample,
    )


def _draw_aa(
    canvas: Image.Image,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    paint: Callable[[ImageDraw.ImageDraw, int, int, int], None],
    pad: int,
    supersample: int,
) -> None:
    """Rasterise ``paint`` into a clipped tile at ``supersample``x and composite."""
    frame_w, frame_h = canvas.size
    left = max(0, math.floor(x0) - pad)
    top = max(0, math.floor(y0) - pad)
    right = min(frame_w, math.ceil(x1) + pad)
    bottom = min(frame_h, math.ceil(y1) + pad)
    if right <= left or bottom <= top:
        return
    tile = Image.new(
        "RGBA", ((right - left) * supersample, (bottom - top) * supersample), (0, 0, 0, 0)
    )
    paint(ImageDraw.Draw(tile), left, top, supersample)
    tile = tile.resize((right - left, bottom - top), Image.Resampling.BOX)
    canvas.alpha_composite(tile, dest=(left, top))


# ---------------------------------------------------------------------------
# Transition grammar
# ---------------------------------------------------------------------------


def _resolve_step_urls(
    frames: list[dict[str, Any]], step_urls: Sequence[str | None] | None
) -> dict[int, str]:
    urls: dict[int, str] = {}
    if step_urls is not None:
        for step_index, url in enumerate(step_urls):
            if url:
                urls[step_index] = url
        return urls
    # Fallback: a manifest produced by newer recorders may carry ``url`` on
    # each frame. Older manifests carry nothing, which leaves this empty and
    # makes every boundary a dissolve.
    for frame in frames:
        if frame.get("url"):
            urls.setdefault(int(frame["step_index"]), str(frame["url"]))
    return urls


def _apply_transitions(
    frames: list[dict[str, Any]],
    paths: list[Path],
    step_urls: Sequence[str | None] | None,
    result: GrammarResult,
) -> GrammarResult:
    urls = _resolve_step_urls(frames, step_urls)
    slides = 0
    dissolves = 0
    for boundary in range(1, len(frames)):
        previous_step = int(frames[boundary - 1]["step_index"])
        new_step = int(frames[boundary]["step_index"])
        if previous_step == new_step:
            continue
        before = urls.get(previous_step)
        after = urls.get(new_step)
        if before is None or after is None or before == after:
            dissolves += 1
            continue

        step_length = 0
        cursor = boundary
        while cursor < len(frames) and int(frames[cursor]["step_index"]) == new_step:
            step_length += 1
            cursor += 1
        # Never overwrite the last frame of the step: it is the hold, and it
        # must stay available as the source of the next boundary.
        count = min(_SLIDE_FRAMES, step_length - 1)
        if count <= 0:
            dissolves += 1
            continue

        with Image.open(paths[boundary - 1]) as source_image:
            source = source_image.convert("RGBA")
        with Image.open(paths[boundary]) as target_image:
            target = target_image.convert("RGBA")
        if target.size != source.size:
            target = target.resize(source.size, Image.Resampling.LANCZOS)
        for offset in range(count):
            progress = (offset + 1) / (count + 1)
            _slide(source, target, progress).convert("RGB").save(
                paths[boundary + offset], format="PNG"
            )
        slides += 1

    return replace(result, slide_transitions=slides, dissolve_transitions=dissolves)


def _slide(source: Image.Image, target: Image.Image, progress: float) -> Image.Image:
    """Leftward slide: ``source`` exits left while ``target`` enters from right."""
    width, height = source.size
    canvas = Image.new("RGBA", (width, height))
    offset = round(width * progress)
    canvas.paste(source, (-offset, 0))
    canvas.paste(target, (width - offset, 0))
    return canvas


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def _apply_captions(
    frames: list[dict[str, Any]],
    paths: list[Path],
    step_labels: Sequence[str | None],
    config: GrammarConfig,
    result: GrammarResult,
) -> GrammarResult:
    labels = {
        step_index: label.strip()
        for step_index, label in enumerate(step_labels)
        if label and label.strip()
    }
    if not labels:
        return result
    font = _load_font(config.caption_font_px)
    positions: dict[int, int] = {}
    captioned = 0
    for index, frame in enumerate(frames):
        step_index = int(frame["step_index"])
        label = labels.get(step_index)
        if label is None:
            continue
        position = positions.get(step_index, 0)
        positions[step_index] = position + 1
        alpha = _CAPTION_FADE[min(position, len(_CAPTION_FADE) - 1)]
        _rewrite(
            paths[index],
            _caption_op(label, font, alpha, config.timeline_height),
        )
        captioned += 1
    return replace(result, caption_frames=captioned)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Bundled DejaVu first (deterministic), then the system font, then bitmap."""
    resource = files("clickcast.annotate").joinpath("fonts").joinpath("DejaVuSans.ttf")
    try:
        data = resource.read_bytes()
    except (FileNotFoundError, OSError):
        data = b""
    if data:
        try:
            return ImageFont.truetype(io.BytesIO(data), size)
        except OSError:
            pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _caption_op(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    alpha: float,
    timeline_height: int,
) -> _DrawOp:
    def draw(canvas: Image.Image) -> None:
        frame_w, frame_h = canvas.size
        measure = ImageDraw.Draw(canvas)
        max_text_w = frame_w - 2 * _CAPTION_MARGIN_X - 2 * _CAPTION_PAD_X
        rendered = _truncate_to_width(measure, text, font, max_text_w)
        bbox = measure.textbbox((0, 0), rendered, font=font)
        text_w = int(bbox[2] - bbox[0])
        text_h = int(bbox[3] - bbox[1])
        box_w = text_w + 2 * _CAPTION_PAD_X
        box_h = text_h + 2 * _CAPTION_PAD_Y
        x = _CAPTION_MARGIN_X
        y = frame_h - box_h - max(24, timeline_height + 20)
        if y < 0:
            y = 0
        right_limit = max(0, frame_w - box_w)
        if x > right_limit:
            x = right_limit
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            radius=_CAPTION_RADIUS,
            fill=(12, 16, 24, round(190 * alpha)),
        )
        od.text(
            (x + _CAPTION_PAD_X - bbox[0], y + _CAPTION_PAD_Y - bbox[1]),
            rendered,
            font=font,
            fill=(250, 250, 250, round(255 * alpha)),
        )
        canvas.alpha_composite(overlay)

    return draw


def _truncate_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if max_width <= 0 or draw.textlength(text, font=font) <= max_width:
        return text
    if max_width <= draw.textlength("…", font=font):
        return "…"
    low, high = 0, len(text)
    best = "…"
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip() + "…"
        if draw.textlength(candidate, font=font) <= max_width:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def _timeline_op(index: int, total: int, height: int) -> _DrawOp:
    def draw(canvas: Image.Image) -> None:
        frame_w, frame_h = canvas.size
        if height <= 0 or frame_w <= 0 or frame_h <= 0:
            return
        top = max(0, frame_h - height)
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([0, top, frame_w, frame_h], fill=_TIMELINE_BG)
        filled = round(frame_w * (index + 1) / max(total, 1))
        if filled > 0:
            od.rectangle([0, top, filled, frame_h], fill=_TIMELINE_FILL)
        canvas.alpha_composite(overlay)

    return draw


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _rewrite(path: Path, op: _DrawOp) -> None:
    with Image.open(path) as base:
        canvas = base.convert("RGBA")
    op(canvas)
    canvas.convert("RGB").save(path, format="PNG")
