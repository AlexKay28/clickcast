"""Post-capture smoothing for low-FPS frame sequences.

Timing model
------------
For ``N`` original frames, the target is ``round(N * output_fps / source_fps)``
frames (nearest integer, halves rounded up), subject to ``max_frames`` when
possible. Original frame ``i`` owns ``floor((i + 1) * T / N) - floor(i * T / N)``
of the ``T`` output slots. This Bresenham-style distribution gives every
original at least one slot while approximating ``output_fps / source_fps``
slots per original.

Cursor and cross-dissolve frames consume the original's repeat slots first;
all leftover slots are ordinary PNG-copy holds. Effects therefore never extend
the source duration, no original dwell disappears, and the original target
frame always follows its source frame's complete slot run. If ``max_frames``
is below the duration-preserving target, holds are reduced evenly. Originals
are never removed, even when they alone exceed ``max_frames``.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PIL import Image

__all__ = ["SmoothConfig", "SmoothResult", "smooth_frames_dir"]


@dataclass(slots=True)
class SmoothConfig:
    enabled: bool = True
    output_fps: int = 30
    cursor: bool = True
    transitions: bool = True
    transition_ms: int = 180
    max_frames: int = 10000


@dataclass(slots=True, frozen=True)
class SmoothResult:
    inserted: int
    total_frames: int
    output_fps: int


@dataclass(slots=True, frozen=True)
class _Insertion:
    target: int
    kind: Literal["cursor", "transition"]
    count: int


def smooth_frames_dir(frames_dir: Path, config: SmoothConfig, *, source_fps: int) -> SmoothResult:
    """Smooth a frame directory while preserving its approximate duration.

    ``inserted`` includes ordinary holds as well as cursor and transition
    frames. Missing or empty manifests are ignored. No files or manifest data
    are changed when the sequence is disabled, already fast enough, or needs
    no additional output slots.
    """
    if source_fps <= 0:
        raise ValueError("source_fps must be positive")
    if config.output_fps <= 0:
        raise ValueError("output_fps must be positive")

    manifest_path = frames_dir / "frames.json"
    if not config.enabled or not manifest_path.exists():
        return SmoothResult(0, _frame_count(manifest_path), config.output_fps)

    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames: list[dict[str, Any]] = manifest.get("frames", [])
    original_count = len(frames)
    if not frames or config.output_fps <= source_fps:
        return SmoothResult(0, original_count, config.output_fps)

    # Integer form of floor(x + 0.5), avoiding banker's rounding at halves.
    ideal_total = max(
        original_count,
        (2 * original_count * config.output_fps + source_fps) // (2 * source_fps),
    )
    total_frames = max(original_count, min(ideal_total, config.max_frames))
    if total_frames == original_count:
        return SmoothResult(0, original_count, config.output_fps)

    slots = [
        ((i + 1) * total_frames) // original_count - (i * total_frames) // original_count
        for i in range(original_count)
    ]
    requests = _plan_insertions(frames, config)
    for after, items in requests.items():
        allowed = _fit_to_budget([item.count for item in items], slots[after] - 1)
        requests[after] = [
            _Insertion(item.target, item.kind, count)
            for item, count in zip(items, allowed, strict=True)
            if count
        ]

    used_paths = {str(frame["path"]) for frame in frames}
    name_counters: dict[str, int] = {}

    def new_name(source_path: str) -> str:
        stem = Path(source_path).stem
        serial = name_counters.get(stem, 0) + 1
        while True:
            name = f"{stem}-s{serial:03d}.png"
            serial += 1
            if name not in used_paths and not (frames_dir / name).exists():
                name_counters[stem] = serial - 1
                used_paths.add(name)
                return name

    generated_paths: list[Path] = []
    new_frames: list[dict[str, Any]] = []
    try:
        for index, earlier in enumerate(frames):
            new_frames.append(earlier)
            # Holds come BEFORE effects. Putting them afterwards would make
            # the sequence jump from a late cursor position (or a nearly-B
            # crossfade) back to the original A frame before continuing.
            emitted = sum(item.count for item in requests.get(index, []))
            hold_count = slots[index] - 1 - emitted
            new_frames.extend(
                _make_hold_frames(frames_dir, earlier, hold_count, new_name, generated_paths)
            )
            for item in requests.get(index, []):
                target = frames[item.target]
                if item.kind == "cursor":
                    new_frames.extend(
                        _make_cursor_frames(
                            frames_dir,
                            earlier,
                            target,
                            item.count,
                            new_name,
                            generated_paths,
                        )
                    )
                else:
                    new_frames.extend(
                        _make_transition_frames(
                            frames_dir,
                            earlier,
                            target,
                            item.count,
                            new_name,
                            generated_paths,
                        )
                    )
            # Effects and holds share this original's slots; holds were
            # emitted above so their cursor/pixels cannot regress an effect.

        step_counters: dict[int, int] = {}
        for frame in new_frames:
            step_index = int(frame["step_index"])
            frame["sub_index"] = step_counters.get(step_index, 0)
            step_counters[step_index] = frame["sub_index"] + 1
        manifest["frames"] = new_frames
        _write_manifest_atomic(manifest_path, manifest)
    except Exception:
        for path in generated_paths:
            path.unlink(missing_ok=True)
        raise

    return SmoothResult(total_frames - original_count, total_frames, config.output_fps)


def _frame_count(manifest_path: Path) -> int:
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return len(manifest.get("frames", []))


def _plan_insertions(
    frames: list[dict[str, Any]], config: SmoothConfig
) -> dict[int, list[_Insertion]]:
    """Build requests in cursor-then-transition order for each source frame."""
    requests: dict[int, list[_Insertion]] = {}
    if config.cursor:
        cursor_indices = [i for i, frame in enumerate(frames) if frame.get("cursor_xy") is not None]
        for earlier, target in itertools.pairwise(cursor_indices):
            pa = frames[earlier]["cursor_xy"]
            pb = frames[target]["cursor_xy"]
            distance = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
            count = _cursor_count(distance, config.output_fps)
            if count:
                requests.setdefault(earlier, []).append(_Insertion(target, "cursor", count))

    if config.transitions and config.transition_ms > 0:
        count = _transition_count(config.output_fps, config.transition_ms)
        if count:
            for earlier in range(len(frames) - 1):
                if frames[earlier]["step_index"] != frames[earlier + 1]["step_index"]:
                    requests.setdefault(earlier, []).append(
                        _Insertion(earlier + 1, "transition", count)
                    )
    return requests


def _cursor_count(distance: float, output_fps: int) -> int:
    if distance <= 0:
        return 0
    # Count intervals, then subtract the interval already present between the
    # original endpoints. Thirty pixels gives fluid motion; 180 ms prevents
    # short moves from becoming an abrupt two-frame jump. Cap at half a second.
    distance_intervals = max(1, math.ceil(distance / 30.0))
    duration_intervals = max(2, round(output_fps * 0.180))
    cap_intervals = max(2, round(output_fps * 0.5))
    return min(max(distance_intervals, duration_intervals), cap_intervals) - 1


def _transition_count(output_fps: int, transition_ms: int) -> int:
    intervals = max(2, round(output_fps * transition_ms / 1000.0))
    return min(intervals, max(2, output_fps)) - 1


def _fit_to_budget(counts: list[int], budget: int) -> list[int]:
    """Proportionally reduce requests, distributing rounding leftovers."""
    total = sum(counts)
    if total <= budget:
        return counts
    if budget <= 0:
        return [0] * len(counts)

    fitted = [count * budget // total for count in counts]
    remainder = budget - sum(fitted)
    order = sorted(
        range(len(counts)),
        key=lambda i: (-(counts[i] * budget % total), i),
    )
    for index in order[:remainder]:
        fitted[index] += 1
    return fitted


def _make_cursor_frames(
    frames_dir: Path,
    earlier: dict[str, Any],
    target: dict[str, Any],
    count: int,
    new_name: Callable[[str], str],
    generated_paths: list[Path],
) -> list[dict[str, Any]]:
    pa = earlier["cursor_xy"]
    pb = target["cursor_xy"]
    source = frames_dir / earlier["path"]
    entries: list[dict[str, Any]] = []
    for k in range(1, count + 1):
        t = k / (count + 1)
        eased = 3 * t * t - 2 * t * t * t
        name = new_name(str(earlier["path"]))
        output = frames_dir / name
        shutil.copy2(source, output)
        generated_paths.append(output)
        entry = dict(earlier)
        entry.update(
            path=name,
            sub_index=0,
            cursor_xy=[
                round(pa[0] + (pb[0] - pa[0]) * eased),
                round(pa[1] + (pb[1] - pa[1]) * eased),
            ],
        )
        entries.append(entry)
    return entries


def _make_transition_frames(
    frames_dir: Path,
    earlier: dict[str, Any],
    target: dict[str, Any],
    count: int,
    new_name: Callable[[str], str],
    generated_paths: list[Path],
) -> list[dict[str, Any]]:
    with Image.open(frames_dir / earlier["path"]) as source_image:
        source = source_image.convert("RGB")
    with Image.open(frames_dir / target["path"]) as target_image:
        destination = target_image.convert("RGB")
    if destination.size != source.size:
        destination = destination.resize(source.size, Image.Resampling.LANCZOS)

    entries: list[dict[str, Any]] = []
    for k in range(1, count + 1):
        name = new_name(str(earlier["path"]))
        output = frames_dir / name
        Image.blend(source, destination, k / (count + 1)).save(output, format="PNG")
        generated_paths.append(output)
        entry = dict(earlier)
        entry.update(path=name, sub_index=0, cursor_xy=None)
        entries.append(entry)
    return entries


def _make_hold_frames(
    frames_dir: Path,
    earlier: dict[str, Any],
    count: int,
    new_name: Callable[[str], str],
    generated_paths: list[Path],
) -> list[dict[str, Any]]:
    source = frames_dir / earlier["path"]
    entries: list[dict[str, Any]] = []
    for _ in range(count):
        name = new_name(str(earlier["path"]))
        output = frames_dir / name
        shutil.copy2(source, output)
        generated_paths.append(output)
        entry = dict(earlier)
        entry.update(path=name, sub_index=0)
        entries.append(entry)
    return entries


def _write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".frames-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
