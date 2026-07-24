"""Encoder — turn a frames directory into gif / mp4 / webp or a copy of the dir."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import imageio.v2 as iio
from PIL import Image

__all__ = ["EncodeResult", "Format", "encode"]

Format = Literal["gif", "mp4", "webp", "frames"]

_EXT_TO_FORMAT: dict[str, Format] = {
    ".gif": "gif",
    ".mp4": "mp4",
    ".webp": "webp",
}


@dataclass(slots=True, frozen=True)
class EncodeResult:
    path: Path
    format: Format
    size_bytes: int
    duration_s: float
    frame_count: int


def encode(
    frames_dir: Path | str,
    out_path: Path | str,
    *,
    fps: int = 12,
    quality: int = 8,
    loop: int = 0,
    format: Format | None = None,
) -> EncodeResult:
    """Encode ``frames_dir`` into ``out_path``.

    Format is inferred from the extension of ``out_path`` (``.gif`` / ``.mp4`` /
    ``.webp``) or from the ``format`` kwarg (which wins). Passing a directory-
    like ``out_path`` with no suffix, or ``format="frames"``, copies the source
    directory into place instead of encoding a video.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    if not 1 <= quality <= 30:
        raise ValueError("quality must be between 1 and 30")

    src = Path(frames_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"Frames directory not found: {src}")

    out = Path(out_path)
    fmt: Format = format if format is not None else _infer_format(out)
    frames = _load_frames(src)
    if not frames:
        raise ValueError(f"No frames found in {src}")

    if fmt == "frames":
        return _encode_frames_dir(src, out, frames, fps)
    if fmt == "gif":
        return _encode_gif(frames, out, fps, quality, loop)
    if fmt == "webp":
        return _encode_webp(frames, out, fps, quality, loop)
    if fmt == "mp4":
        return _encode_mp4(frames, out, fps, quality)
    raise ValueError(f"Unknown format: {fmt}")


def _infer_format(out: Path) -> Format:
    ext = out.suffix.lower()
    if ext in _EXT_TO_FORMAT:
        return _EXT_TO_FORMAT[ext]
    if ext == "":
        return "frames"
    raise ValueError(
        f"Cannot infer format from {out.name!r}; pass format=... "
        "or use a .gif / .mp4 / .webp extension"
    )


def _load_frames(src: Path) -> list[Path]:
    """Ordered frame paths — respects ``frames.json`` if present, else sorted glob."""
    manifest = src / "frames.json"
    if manifest.exists():
        data = json.loads(manifest.read_text())
        return [src / entry["path"] for entry in data["frames"]]
    return sorted(src.glob("frame-*.png"))


def _per_frame_ms(fps: int) -> int:
    return max(1, round(1000 / fps))


def _sample_frames_for_palette(frames: list[Path], *, max_samples: int = 8) -> Image.Image:
    """Concatenate up to ``max_samples`` evenly-spaced frames vertically so
    quantizing the resulting strip yields a palette representative of the
    whole animation, not just its first frame.

    This matters when the reel starts on an `about:blank` white screen (or
    any transient loading state) and the interesting content is dark — the
    naive "first-frame palette" collapses everything to white.
    """
    sample_count = min(max_samples, len(frames))
    step = max(1, len(frames) // sample_count)
    picks = frames[::step][:sample_count]

    # Open + convert with a `with` block so the file-backed handle from
    # `Image.open()` is released as soon as we've copied pixels into `strip`.
    samples: list[Image.Image] = []
    for f in picks:
        with Image.open(f) as src:
            samples.append(src.convert("RGB"))
    width = samples[0].width
    total_h = sum(s.height for s in samples)
    strip = Image.new("RGB", (width, total_h))
    y = 0
    for s in samples:
        strip.paste(s, (0, y))
        y += s.height
        s.close()
    return strip


def _encode_gif(frames: list[Path], out: Path, fps: int, quality: int, loop: int) -> EncodeResult:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Palette from a sample strip across the animation so the file stays
    # small AND the animation doesn't collapse when the first frame is a
    # blank/loading state.
    strip = _sample_frames_for_palette(frames)
    palette = strip.quantize(colors=256)
    strip.close()

    images: list[Image.Image] = []
    for f in frames:
        with Image.open(f) as src:
            images.append(src.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE))

    images[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=_per_frame_ms(fps),
        loop=loop,
        optimize=True,
        disposal=2,
    )
    _verify_animation(out)
    del images  # release before returning
    return EncodeResult(
        path=out,
        format="gif",
        size_bytes=out.stat().st_size,
        duration_s=len(frames) / fps,
        frame_count=len(frames),
    )


def _encode_webp(frames: list[Path], out: Path, fps: int, quality: int, loop: int) -> EncodeResult:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Open + convert with a `with` block so the file-backed handle from
    # `Image.open()` is released as soon as we've copied pixels into memory.
    images: list[Image.Image] = []
    for f in frames:
        with Image.open(f) as src:
            images.append(src.convert("RGB"))
    # Map quality 1..30 → Pillow WebP quality 98..40 (higher WebP = better).
    pillow_quality = max(1, min(100, 100 - (quality - 1) * 2))
    images[0].save(
        out,
        format="WEBP",
        save_all=True,
        append_images=images[1:],
        duration=_per_frame_ms(fps),
        loop=loop,
        quality=pillow_quality,
        method=4,
    )
    for img in images:
        img.close()
    _verify_animation(out)
    return EncodeResult(
        path=out,
        format="webp",
        size_bytes=out.stat().st_size,
        duration_s=len(frames) / fps,
        frame_count=len(frames),
    )


def _encode_mp4(frames: list[Path], out: Path, fps: int, quality: int) -> EncodeResult:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Roadmap: quality 1 → crf 15, quality 30 → crf 32 (linear).
    crf = round(15 + (quality - 1) * (32 - 15) / (30 - 1))
    writer = iio.get_writer(
        str(out),
        fps=fps,
        codec="libx264",
        pixelformat="yuv420p",
        macro_block_size=1,
        output_params=["-crf", str(crf), "-preset", "medium"],
    )
    try:
        for f in frames:
            writer.append_data(iio.imread(str(f)))
    finally:
        writer.close()

    if out.stat().st_size < 100:
        raise RuntimeError(f"MP4 encode produced a suspiciously small file: {out}")

    return EncodeResult(
        path=out,
        format="mp4",
        size_bytes=out.stat().st_size,
        duration_s=len(frames) / fps,
        frame_count=len(frames),
    )


def _encode_frames_dir(src: Path, out: Path, frames: list[Path], fps: int) -> EncodeResult:
    if src.resolve() != out.resolve():
        out.mkdir(parents=True, exist_ok=True)
        for f in frames:
            shutil.copy2(f, out / f.name)
        manifest = src / "frames.json"
        if manifest.exists():
            shutil.copy2(manifest, out / manifest.name)
    total = sum(f.stat().st_size for f in frames)
    return EncodeResult(
        path=out,
        format="frames",
        size_bytes=total,
        duration_s=len(frames) / fps,
        frame_count=len(frames),
    )


def _verify_animation(out: Path) -> None:
    """Cheap header-only sanity check that the encoded animation is readable."""
    with Image.open(out) as img:
        img.seek(0)
        _ = img.size
