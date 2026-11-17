"""Chromium CDP screencast frame source — continuous page motion, not stills.

The shots recorder (:mod:`clickcast.capture.recorder`) takes one PNG per
action boundary. Anything that moved *between* two screenshots — a modal
sliding in, a route cross-fade, a chart animating, typed characters
appearing — is simply missing from the reel.

This module is the second frame source behind the same kernel: it opens a
Chromium DevTools Protocol session on the page and runs
``Page.startScreencast``. The compositor pushes a JPEG whenever the page
changes; every frame is acknowledged with ``Page.screencastFrameAck`` so the
stream keeps flowing. Frames are decoded and written as PNGs straight into the
recorder's frames directory, so :mod:`clickcast.kernel.render` and
:mod:`clickcast.encode` consume them unchanged (one encode path).

Engine coverage
---------------
``Page.startScreencast`` is Chromium-only. Firefox and WebKit have no CDP
equivalent, so this module fails fast with :class:`ScreencastUnavailable`
instead of silently producing a slideshow. Use ``--capture shots`` (the
default) for those engines. See ``docs/smooth-video.md``.

Determinism
-----------
Frame count depends on wall time and on how much the page actually changes,
so screencast reels are not byte-reproducible. Tests assert properties
(unique-frame share, duration bounds, step attribution), never pixels.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:
    from clickcast.core.session import Session

__all__ = ["CapturedFrame", "ScreencastSource", "ScreencastUnavailable"]

#: JPEG quality handed to ``Page.startScreencast``. 80 is visually clean for
#: UI text at viewport resolution while keeping the intermediate stream small.
DEFAULT_SCREENCAST_QUALITY = 80

#: Hard cap on captured frames. A pathological page (or a runaway animation)
#: cannot fill the disk: once this many frames have been written the source
#: stops storing frames and logs one warning. 6000 frames is roughly two
#: minutes of 60 fps compositor output (~0.6 GB of PNGs at 1280x800).
DEFAULT_MAX_FRAMES = 6000


class ScreencastUnavailable(RuntimeError):
    """Raised when the active engine cannot serve a CDP screencast."""


@dataclass(slots=True, frozen=True)
class CapturedFrame:
    """One screencast frame, queued or written to disk as PNG."""

    index: int
    #: Arrival time in seconds relative to the start of the screencast.
    captured_at: float
    path: Path
    #: Pending PNG write. ``drain()`` blocks on these before resampling.
    write: Future[None] | None = field(default=None, repr=False, compare=False)


def _write_png(payload: bytes, path: Path) -> None:
    """Decode one JPEG frame and store it as a PNG (runs on a worker thread)."""
    with Image.open(BytesIO(payload)) as image:
        image.load()
        image.convert("RGB").save(path, format="PNG")


class ScreencastSource:
    """Push-based frame source backed by CDP ``Page.startScreencast``.

    Usage is owned by :class:`clickcast.capture.recorder.Recorder`::

        source = ScreencastSource(frames_dir, quality=80, max_frames=6000)
        await source.start(session)
        ...  # frames accumulate in the background
        await source.stop()
        frames = source.frames   # ordered, each a PNG on disk

    The CDP handler only base64-decodes the payload (cheap) and hands the
    PNG write to a small thread pool, then ACKs immediately. Writing PNGs
    inline would cap capture at ~20 fps at 1280x800 and starve the stream of
    frames, which is exactly the motion we are trying to record. ``drain()``
    joins the bounded write queue before the recorder resamples.
    """

    def __init__(
        self,
        frames_dir: Path,
        *,
        quality: int = DEFAULT_SCREENCAST_QUALITY,
        max_frames: int = DEFAULT_MAX_FRAMES,
    ) -> None:
        if not 0 <= quality <= 100:
            raise ValueError("screencast quality must be between 0 and 100")
        if max_frames <= 0:
            raise ValueError("screencast max_frames must be positive")
        self.frames_dir = Path(frames_dir)
        self.quality = quality
        self.max_frames = max_frames
        self._frames: list[CapturedFrame] = []
        self._pending: deque[Future[None]] = deque()
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="clickcast-screencast"
        )
        self._cdp: Any | None = None
        self._started_at: float | None = None
        self._stopped = False
        self._truncated = False
        self._executor_closed = False

    @property
    def frames(self) -> list[CapturedFrame]:
        return list(self._frames)

    @property
    def started_at(self) -> float:
        """``time.monotonic()`` at which the stream started (step attribution)."""
        return self._started_at if self._started_at is not None else time.monotonic()

    async def start(self, session: Session) -> None:
        """Attach to the page and start pushing frames.

        Raises :class:`ScreencastUnavailable` for non-Chromium engines.
        """
        engine = getattr(session, "engine", "chromium")
        if engine != "chromium":
            raise ScreencastUnavailable(
                "screencast capture requires Chromium: CDP Page.startScreencast "
                f"is not available on {engine!r}. Use --capture shots (the default) "
                "for Firefox/WebKit. See docs/smooth-video.md."
            )
        page = session.page
        self._cdp = await page.context.new_cdp_session(page)
        self._cdp.on("Page.screencastFrame", self._on_frame)
        params: dict[str, Any] = {
            "format": "jpeg",
            "quality": self.quality,
            "everyNthFrame": 1,
        }
        viewport = getattr(page, "viewport_size", None) or {}
        if viewport.get("width") and viewport.get("height"):
            params["maxWidth"] = int(viewport["width"])
            params["maxHeight"] = int(viewport["height"])
        self._started_at = time.monotonic()
        try:
            await self._cdp.send("Page.startScreencast", params)
        except Exception as exc:  # pragma: no cover - transport specific
            await self._detach()
            self.freeze()
            self._shutdown_executor()
            raise ScreencastUnavailable(f"could not start Chromium screencast: {exc}") from exc

    def freeze(self) -> None:
        """Stop accepting frames without awaiting CDP teardown.

        Called from the recorder's synchronous ``flush``/``__exit__`` path so
        the frame list is stable before it is resampled. Late CDP events are
        acknowledged but not stored.
        """
        self._stopped = True

    def drain(self) -> None:
        """Block until every queued PNG write has landed on disk."""
        while self._pending:
            self._pending.popleft().result()

    async def stop(self) -> None:
        """Stop the screencast and detach the CDP session (idempotent)."""
        self.freeze()
        self.drain()
        self._shutdown_executor()
        cdp, self._cdp = self._cdp, None
        if cdp is None:
            return
        # The page/context may already be gone; detaching below is the part
        # that matters for not leaking a protocol session.
        with contextlib.suppress(Exception):
            await cdp.send("Page.stopScreencast")
        await self._detach(cdp)

    async def _detach(self, cdp: Any | None = None) -> None:
        session = cdp if cdp is not None else self._cdp
        if session is None:
            return
        with contextlib.suppress(Exception):
            await session.detach()
        if session is self._cdp:
            self._cdp = None

    async def _on_frame(self, params: dict[str, Any]) -> None:
        """``Page.screencastFrame`` handler: store then ACK.

        The ACK is required for the stream to keep flowing — without it
        Chromium sends exactly one frame and waits.
        """
        if not self._stopped:
            self._store(params)
        await self._ack(params)

    def _store(self, params: dict[str, Any]) -> None:
        if len(self._frames) >= self.max_frames:
            if not self._truncated:
                self._truncated = True
                logging.getLogger("clickcast.capture.screencast").warning(
                    "screencast frame cap reached (%d frames) — dropping the rest; "
                    "raise max_frames or shorten the tour",
                    self.max_frames,
                )
            return
        started = self._started_at if self._started_at is not None else time.monotonic()
        captured_at = max(0.0, time.monotonic() - started)
        index = len(self._frames)
        path = self.frames_dir / f"capture-{index:05d}.png"
        try:
            payload = base64.b64decode(params.get("data", ""))
        except Exception:  # pragma: no cover - malformed frame from the browser
            return
        future = self._executor.submit(_write_png, payload, path)
        self._pending.append(future)
        self._frames.append(
            CapturedFrame(index=index, captured_at=captured_at, path=path, write=future)
        )
        # Bounded backpressure: at most ~200 frames of JPEG payloads are in
        # flight, so a slow disk throttles the handler instead of growing
        # memory without limit.
        while len(self._pending) > 192:
            self._pending.popleft().result()

    def _shutdown_executor(self) -> None:
        if self._executor_closed:
            return
        self._executor_closed = True
        self._executor.shutdown(wait=True)

    async def _ack(self, params: dict[str, Any]) -> None:
        cdp = self._cdp
        if cdp is None:
            return
        # Teardown race: the session is gone, nothing left to ACK.
        with contextlib.suppress(Exception):
            await cdp.send(
                "Page.screencastFrameAck",
                {"sessionId": params.get("sessionId")},
            )
