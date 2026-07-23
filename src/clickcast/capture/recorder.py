"""Frame capture pipeline — turn a stream of actions into an ordered stream of PNGs."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clickcast.core.actions import ActionResult, BaseStep
    from clickcast.core.session import Session


__all__ = ["FrameRef", "Recorder"]


@dataclass(slots=True, frozen=True)
class FrameRef:
    path: Path
    step_index: int
    sub_index: int
    cursor_xy: tuple[int, int] | None


class Recorder:
    """Owns a temp directory of PNG frames and produces a `frames.json` manifest.

    Usage::

        with Recorder(fps=12) as rec:
            for step in scenario.steps:
                await rec.pre_action(session)
                result = await execute(step, session)
                await rec.post_action(session, result, step)
            paths = rec.flush()   # writes frames.json, returns ordered paths
            encoder.encode(rec.frames_dir, out="tour.gif")   # (in #9)
    """

    def __init__(
        self,
        *,
        fps: int = 12,
        default_dwell: float = 1.0,
        keep: bool = False,
        out_dir: Path | str | None = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        if default_dwell < 0:
            raise ValueError("default_dwell must be non-negative")

        self.fps = fps
        self.default_dwell = default_dwell
        self.keep = keep
        self.out_dir: Path = Path(out_dir) if out_dir is not None else Path("frames")

        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._tmp_path: Path | None = None
        self._frames: list[FrameRef] = []
        self._step_index: int = -1
        self._sub_index: int = 0
        self._entered: bool = False

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

    async def pre_action(self, session: Session) -> Path:
        """Capture one frame BEFORE the action executes, opening a new step."""
        self._step_index += 1
        self._sub_index = 0
        return await self._capture(session, cursor_xy=None)

    async def post_action(
        self,
        session: Session,
        result: ActionResult,
        step: BaseStep,
    ) -> list[Path]:
        """Capture the post-state frame + padding copies for `dwell * fps`.

        The action's own timing does not produce frames — its dwell period is
        held on the post-state so the reel plays smoothly at `fps`.
        """
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
        """Force N padding frames of the current page state."""
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

    def flush(self) -> list[Path]:
        """Write `frames.json` next to the frames and return their ordered paths."""
        if self._tmp_path is None:
            raise RuntimeError("Recorder is not open — use `with Recorder(...) as rec:`")
        manifest = {
            "fps": self.fps,
            "count": len(self._frames),
            "frames": [
                {
                    "path": f.path.name,
                    "step_index": f.step_index,
                    "sub_index": f.sub_index,
                    "cursor_xy": list(f.cursor_xy) if f.cursor_xy is not None else None,
                }
                for f in self._frames
            ],
        }
        (self._tmp_path / "frames.json").write_text(json.dumps(manifest, indent=2))
        return [f.path for f in self._frames]

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
