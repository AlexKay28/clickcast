"""Frame capture pipeline — turn a stream of actions into an ordered stream of PNGs.

Two capture backends share one frames directory and one ``frames.json``
manifest, so the rest of the pipeline (annotate → smooth → encode) never
knows which one produced the frames:

``shots`` (default)
    One PNG per action boundary plus held padding copies — the original
    behaviour, byte-for-byte.

``screencast``
    Chromium CDP ``Page.startScreencast`` (see
    :mod:`clickcast.capture.screencast`). The compositor pushes frames while
    the page moves; at :meth:`Recorder.flush` they are resampled onto a fixed
    ``video_fps`` grid over the real capture duration. Step attribution comes
    from the action timestamps this recorder already knows, not from frame
    counts, and every frame carries ``captured_at``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Literal

from clickcast.capture.screencast import (
    DEFAULT_MAX_FRAMES,
    DEFAULT_SCREENCAST_QUALITY,
    CapturedFrame,
    ScreencastSource,
)

if TYPE_CHECKING:
    from clickcast.core.actions import ActionResult, BaseStep
    from clickcast.core.session import Session


__all__ = ["CaptureBackend", "FrameRef", "Recorder"]

log = logging.getLogger(__name__)

CaptureBackend = Literal["shots", "screencast"]


@dataclass(slots=True, frozen=True)
class FrameRef:
    path: Path
    step_index: int
    sub_index: int
    cursor_xy: tuple[int, int] | None
    #: Seconds since the screencast started; ``None`` for the shots backend.
    captured_at: float | None = None


@dataclass(slots=True)
class _StepWindow:
    """When one recorder step was live, used for interval-based attribution."""

    index: int
    started_at: float
    ended_at: float | None = None
    action_done_at: float | None = None
    cursor_xy: tuple[int, int] | None = None


class Recorder:
    """Owns a temp directory of PNG frames and produces a `frames.json` manifest.

    Usage::

        with Recorder(fps=12) as rec:
            for step in scenario.steps:
                await rec.pre_action(session)
                result = await execute(step, session)
                await rec.post_action(session, result, step)
            await rec.stop_capture()
            paths = rec.flush()   # writes frames.json, returns ordered paths

    ``capture="screencast"`` switches to the Chromium CDP frame source. In
    that mode ``pre_action``/``post_action`` only record step intervals; the
    PNGs arrive asynchronously and are attributed, resampled and written by
    :meth:`flush`. ``fps`` is the shots capture rate; ``video_fps`` is the
    screencast output rate.
    """

    def __init__(
        self,
        *,
        fps: int = 12,
        default_dwell: float = 1.0,
        keep: bool = False,
        out_dir: Path | str | None = None,
        capture: CaptureBackend = "shots",
        video_fps: int = 30,
        screencast_quality: int = DEFAULT_SCREENCAST_QUALITY,
        screencast_max_frames: int = DEFAULT_MAX_FRAMES,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        if default_dwell < 0:
            raise ValueError("default_dwell must be non-negative")
        if capture not in ("shots", "screencast"):
            raise ValueError(f"capture must be 'shots' or 'screencast', got {capture!r}")
        if video_fps <= 0:
            raise ValueError("video_fps must be positive")

        self.fps = fps
        self.default_dwell = default_dwell
        self.keep = keep
        self.out_dir: Path = Path(out_dir) if out_dir is not None else Path("frames")
        self.capture = capture
        self.video_fps = video_fps
        self.screencast_quality = screencast_quality
        self.screencast_max_frames = screencast_max_frames

        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._tmp_path: Path | None = None
        self._frames: list[FrameRef] = []
        self._step_index: int = -1
        self._sub_index: int = 0
        self._entered: bool = False
        # Screencast state.
        self._source: ScreencastSource | None = None
        self._captured: list[CapturedFrame] | None = None
        self._capture_started_at: float | None = None
        self._steps: list[_StepWindow] = []
        self._stop_tasks: set[asyncio.Task[None]] = set()
        self._screencast_flushed = False

    def __enter__(self) -> Recorder:
        self._tmp = tempfile.TemporaryDirectory(prefix="clickcast-frames-")
        self._tmp_path = Path(self._tmp.name)
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._source is not None:
                self._freeze_capture()
            if self.keep and self._tmp_path is not None:
                self._persist()
        finally:
            if self._tmp is not None:
                self._tmp.cleanup()
            self._tmp = None
            self._tmp_path = None
            self._entered = False

    @property
    def frames(self) -> list[FrameRef]:
        return list(self._frames)

    @property
    def frames_dir(self) -> Path:
        if self._tmp_path is None:
            raise RuntimeError("Recorder is not open — use `with Recorder(...) as rec:`")
        return self._tmp_path

    async def pre_action(self, session: Session) -> Path | None:
        """Capture one frame BEFORE the action executes, opening a new step.

        In screencast mode this opens a timestamp window instead of taking a
        screenshot and returns ``None``; the frames for the step are resolved
        at :meth:`flush`.
        """
        if self.capture == "screencast":
            await self._ensure_source(session)
            self._open_step()
            return None
        self._step_index += 1
        self._sub_index = 0
        return await self._capture(session, cursor_xy=None)

    async def pre_action_pad(self, count: int) -> list[Path]:
        """Duplicate the most-recent pre-action frame ``count`` extra times.

        Extra copies land at the current ``step_index`` with increasing
        ``sub_index`` values BEFORE any ``post_action`` runs, so the
        annotator sees them as pre-click sub-frames. The
        ``--for-humans`` pipeline uses this to give the pre-click target
        highlight ring several frames of hold time before the ripple fires
        (see #129 Track A).

        Must be called after ``pre_action`` and before ``post_action``.
        Raises ``RuntimeError`` if there is no pre-action frame to
        duplicate — misuse rather than a silent no-op.

        Screencast mode is already continuous: padding would fabricate stills
        that the real stream does not contain, so this is a no-op returning
        ``[]``. The pre-click highlight ring still renders on the real
        pre-action frames.
        """
        if self.capture == "screencast":
            return []
        if count <= 0:
            return []
        if not self._frames:
            raise RuntimeError("pre_action_pad requires a prior pre_action frame to duplicate")
        source = self._frames[-1]
        if source.step_index != self._step_index or source.sub_index != 0:
            raise RuntimeError(
                "pre_action_pad must be called immediately after pre_action "
                "(no other capture may intervene)"
            )
        paths: list[Path] = []
        for _ in range(count):
            copy_path = self._next_path()
            shutil.copyfile(source.path, copy_path)
            self._record(copy_path, cursor_xy=None)
            paths.append(copy_path)
        return paths

    async def post_action(
        self,
        session: Session,
        result: ActionResult,
        step: BaseStep,
    ) -> list[Path]:
        """Capture the post-state frame + padding copies for `dwell * fps`.

        The action's own timing does not produce frames — its dwell period is
        held on the post-state so the reel plays smoothly at `fps`.

        In screencast mode the dwell is already real wall time captured by
        the stream, so this only closes the action interval and returns
        ``[]``; the step's frames are resolved at :meth:`flush`.
        """
        if self.capture == "screencast":
            if self._steps:
                window = self._steps[-1]
                now = time.monotonic()
                # ``execute()`` sleeps ``step.dwell`` after the action, so the
                # action itself landed that long before post_action runs.
                # Attributing the dwell to the post-action side keeps the
                # synthetic cursor + ripple on the transition they belong to.
                settled = step.dwell if step.dwell > 0 else 0.0
                window.action_done_at = max(window.started_at, now - settled)
                window.cursor_xy = result.cursor_xy
            return []
        dwell = step.dwell if step.dwell > 0 else self.default_dwell
        n = max(1, round(dwell * self.fps))
        first = await self._capture(session, cursor_xy=result.cursor_xy)
        paths = [first]
        for _ in range(1, n):
            copy_path = self._next_path()
            shutil.copyfile(first, copy_path)
            self._record(copy_path, cursor_xy=result.cursor_xy)
            paths.append(copy_path)
        return paths

    async def pad(
        self,
        session: Session,
        frames: int,
        cursor_xy: tuple[int, int] | None = None,
    ) -> list[Path]:
        """Force N padding frames of the current page state.

        Screencast mode has no need for fabricated holds: the real stream is
        already at the wall-time frame rate. Returns ``[]`` there.
        """
        if self.capture == "screencast":
            return []
        if frames <= 0:
            return []
        first = await self._capture(session, cursor_xy=cursor_xy)
        paths = [first]
        for _ in range(1, frames):
            copy_path = self._next_path()
            shutil.copyfile(first, copy_path)
            self._record(copy_path, cursor_xy=cursor_xy)
            paths.append(copy_path)
        return paths

    async def stop_capture(self) -> None:
        """Stop the screencast (no-op for shots) and freeze the frame set.

        Call this before :meth:`flush` while the session is still alive; it
        sends ``Page.stopScreencast`` and detaches the CDP session so nothing
        leaks. ``flush`` freezes the stream defensively for callers whose
        session has already been torn down.
        """
        source = self._source
        if source is not None:
            source.freeze()
            await source.stop()
            self._captured = source.frames
            self._capture_started_at = source.started_at
            self._source = None

    def flush(self) -> list[Path]:
        """Write `frames.json` next to the frames and return their ordered paths."""
        if self._tmp_path is None:
            raise RuntimeError("Recorder is not open — use `with Recorder(...) as rec:`")
        if self.capture == "screencast":
            self._flush_screencast()
        manifest = {
            "fps": self.video_fps if self.capture == "screencast" else self.fps,
            "count": len(self._frames),
            "frames": [self._manifest_entry(f) for f in self._frames],
        }
        if self.capture == "screencast":
            manifest["capture"] = "screencast"
        (self._tmp_path / "frames.json").write_text(json.dumps(manifest, indent=2))
        return [f.path for f in self._frames]

    @staticmethod
    def _manifest_entry(frame: FrameRef) -> dict[str, object]:
        entry: dict[str, object] = {
            "path": frame.path.name,
            "step_index": frame.step_index,
            "sub_index": frame.sub_index,
            "cursor_xy": list(frame.cursor_xy) if frame.cursor_xy is not None else None,
        }
        if frame.captured_at is not None:
            # Optional, screencast-only: keeps the shots manifest byte-identical.
            entry["captured_at"] = round(frame.captured_at, 6)
        return entry

    # ------------------------------------------------------------------
    # Screencast plumbing
    # ------------------------------------------------------------------

    async def _ensure_source(self, session: Session) -> None:
        if self._source is not None or self._captured is not None:
            return
        self._source = ScreencastSource(
            self.frames_dir,
            quality=self.screencast_quality,
            max_frames=self.screencast_max_frames,
        )
        await self._source.start(session)
        self._capture_started_at = self._source.started_at

    def _open_step(self) -> None:
        now = time.monotonic()
        if self._steps:
            self._steps[-1].ended_at = now
        self._step_index += 1
        self._sub_index = 0
        self._steps.append(_StepWindow(index=self._step_index, started_at=now))

    def _freeze_capture(self) -> None:
        """Freeze a live source and schedule its async teardown (sync context)."""
        source, self._source = self._source, None
        if source is None:
            return
        source.freeze()
        # Join queued PNG writes before the recorder resamples the frame list.
        source.drain()
        self._captured = source.frames
        self._capture_started_at = source.started_at
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(source.stop())
        self._stop_tasks.add(task)
        task.add_done_callback(self._stop_tasks.discard)

    def _flush_screencast(self) -> None:
        if self._screencast_flushed:
            return
        if self._source is not None:
            self._freeze_capture()
        captured = self._captured or []
        if not captured:
            raise RuntimeError(
                "screencast captured no frames — the page may never have painted; "
                "use --capture shots if this persists"
            )
        if not self._steps:
            self._steps.append(_StepWindow(index=0, started_at=self._capture_started_at or 0.0))
        if self._steps[-1].ended_at is None:
            self._steps[-1].ended_at = time.monotonic()

        started_at = self._capture_started_at or 0.0
        duration = max(
            captured[-1].captured_at,
            (self._steps[-1].ended_at or 0.0) - started_at,
            1.0 / self.video_fps,
        )
        total = max(1, round(duration * self.video_fps))

        frames_dir = self.frames_dir
        frames: list[FrameRef] = []
        sub_counters: dict[int, int] = {}
        # ``cursor`` is only known once the action completed; frames before
        # that point are the pre-action region (cursor_xy=None), exactly like
        # the shots backend's pre-frame.
        for slot in range(total):
            at = slot / self.video_fps
            picked = self._pick_frame(captured, at)
            window = self._window_at(started_at + at)
            step_index = window.index if window is not None else 0
            cursor_xy = None
            if window is not None and window.action_done_at is not None:
                action_at = window.action_done_at - started_at
                if at >= action_at:
                    cursor_xy = window.cursor_xy
            sub_index = sub_counters.get(step_index, 0)
            sub_counters[step_index] = sub_index + 1
            path = frames_dir / f"frame-{max(step_index, 0):04d}-{sub_index:03d}.png"
            shutil.copyfile(picked.path, path)
            frames.append(
                FrameRef(
                    path=path,
                    step_index=step_index,
                    sub_index=sub_index,
                    cursor_xy=cursor_xy,
                    captured_at=picked.captured_at,
                )
            )

        for frame in captured:
            frame.path.unlink(missing_ok=True)
        self._frames = frames
        self._screencast_flushed = True

    @staticmethod
    def _pick_frame(captured: list[CapturedFrame], at: float) -> CapturedFrame:
        """Nearest-previous frame: the last one captured at or before ``at``.

        This is a hold-last-frame resample. Output slots keep the real
        timeline (a 300 ms transition covers ~9 slots at 30 fps); when the
        page is static consecutive slots reference the same source frame and
        are written as separate PNG copies so per-frame overlays composite
        independently.
        """
        low, high = 0, len(captured) - 1
        best = 0
        while low <= high:
            mid = (low + high) // 2
            if captured[mid].captured_at <= at:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return captured[best]

    def _window_at(self, at: float) -> _StepWindow | None:
        if not self._steps:
            return None
        for index, window in enumerate(self._steps):
            end = window.ended_at
            if end is None:
                end = float("inf")
            if window.started_at <= at < end:
                return window
            if index == len(self._steps) - 1:
                # Past the last window (final dwell): keep the last step.
                return window
        return self._steps[0]

    # ------------------------------------------------------------------
    # Shots plumbing (unchanged behaviour)
    # ------------------------------------------------------------------

    async def _capture(self, session: Session, *, cursor_xy: tuple[int, int] | None) -> Path:
        path = self._next_path()
        await session.screenshot(path=path)
        self._record(path, cursor_xy=cursor_xy)
        return path

    def _record(self, path: Path, *, cursor_xy: tuple[int, int] | None) -> None:
        self._frames.append(
            FrameRef(
                path=path,
                step_index=self._step_index,
                sub_index=self._sub_index,
                cursor_xy=cursor_xy,
            )
        )
        self._sub_index += 1

    def _next_path(self) -> Path:
        if self._tmp_path is None:
            raise RuntimeError("Recorder is not open — use `with Recorder(...) as rec:`")
        step_idx = max(self._step_index, 0)
        return self._tmp_path / f"frame-{step_idx:04d}-{self._sub_index:03d}.png"

    def _persist(self) -> None:
        assert self._tmp_path is not None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for entry in self._tmp_path.iterdir():
            dst = self.out_dir / entry.name
            shutil.copy2(entry, dst)
