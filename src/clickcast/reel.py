"""Fluent Python API: `Reel`, `AsyncReel`, and a sync `discover` facade.

The API is a thin, chainable wrapper around the shipped subsystems:

- Builder methods append pydantic :class:`Step` models to an internal Scenario.
- :meth:`Reel.save` and :meth:`AsyncReel.save` reuse the scenario runner from
  ``clickcast.scenario`` — the CLI's `run` command and this API share **one**
  executor.

::

    from clickcast import Reel

    Reel("https://example.com", viewport=(1280, 800), fps=12) \\
        .goto(wait="networkidle") \\
        .click("text=Compare", label="Switch view", dwell=2.0) \\
        .scroll(to="footer") \\
        .save("tour.gif")
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from PIL import Image

from clickcast.capture import FrameRef, Recorder
from clickcast.core.actions import (
    BaseStep,
    ClickStep,
    DblClickStep,
    GotoStep,
    HoverStep,
    PressStep,
    ScreenshotStep,
    ScrollStep,
    SelectStep,
    TypeStep,
    WaitStep,
)
from clickcast.core.session import Engine, Session, WaitArg
from clickcast.discovery import Element
from clickcast.discovery import discover as _async_discover
from clickcast.encode import EncodeResult, Format, encode
from clickcast.feedback import Media, ReportBuilder
from clickcast.feedback import write as write_report
from clickcast.scenario import Meta, Scenario
from clickcast.scenario import run as run_scenario

__all__ = ["AsyncReel", "Reel", "discover"]


# --------------------------------------------------------------------------
# Base builder — shared by Reel (sync) and AsyncReel (async)
# --------------------------------------------------------------------------


class _BaseReel:
    """Common builder: assembles a Scenario. Sync/async concerns live on subclasses."""

    def __init__(
        self,
        url: str,
        *,
        viewport: str | tuple[int, int] | None = None,
        engine: Engine = "chromium",
        device: str | None = None,
        headful: bool = False,
        slowmo: int = 0,
        lang: str | None = None,
        dark: bool = False,
        fps: int = 12,
        dwell: float = 1.0,
    ) -> None:
        self._url = url
        vp = self._viewport_str(viewport)
        meta_kwargs: dict[str, Any] = {
            "engine": engine,
            "device": device,
            "headful": headful,
            "slowmo": slowmo,
            "lang": lang,
            "dark": dark,
            "fps": fps,
            "dwell": dwell,
        }
        if vp is not None:
            meta_kwargs["viewport"] = vp
        self._meta = Meta(**meta_kwargs)
        self._steps: list[BaseStep] = []

    @staticmethod
    def _viewport_str(v: str | tuple[int, int] | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, tuple):
            return f"{v[0]}x{v[1]}"
        return v

    # ------------------------------------------------------------------
    # Chainable builder methods — every one returns `self`
    # ------------------------------------------------------------------

    def goto(
        self,
        url: str | None = None,
        *,
        wait: WaitArg | None = None,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(
            GotoStep(
                url=url or self._url,
                wait=wait,
                label=label,
                dwell=dwell,
                optional=optional,
            )
        )
        return self

    def click(
        self,
        selector: str,
        *,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
        repeat: int = 1,
    ) -> Any:
        self._steps.append(
            ClickStep(selector=selector, label=label, dwell=dwell, optional=optional, repeat=repeat)
        )
        return self

    def dblclick(
        self,
        selector: str,
        *,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(
            DblClickStep(selector=selector, label=label, dwell=dwell, optional=optional)
        )
        return self

    def hover(
        self,
        selector: str,
        *,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(
            HoverStep(selector=selector, label=label, dwell=dwell, optional=optional)
        )
        return self

    def type(
        self,
        into: str,
        text: str,
        *,
        delay: float = 0.0,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(
            TypeStep(
                into=into,
                text=text,
                delay=delay,
                label=label,
                dwell=dwell,
                optional=optional,
            )
        )
        return self

    def press(
        self,
        key: str,
        *,
        selector: str | None = None,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(
            PressStep(key=key, selector=selector, label=label, dwell=dwell, optional=optional)
        )
        return self

    def select(
        self,
        into: str,
        value: str | list[str],
        *,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(
            SelectStep(into=into, value=value, label=label, dwell=dwell, optional=optional)
        )
        return self

    def scroll(
        self,
        *,
        to: str | None = None,
        by: int | None = None,
        label: str | None = None,
        dwell: float = 0.0,
        optional: bool = False,
    ) -> Any:
        self._steps.append(ScrollStep(to=to, by=by, label=label, dwell=dwell, optional=optional))
        return self

    def wait(
        self,
        target: WaitArg,
        *,
        label: str | None = None,
    ) -> Any:
        self._steps.append(WaitStep(wait=target, label=label))
        return self

    def screenshot(
        self,
        *,
        path: str | None = None,
        full_page: bool = False,
        label: str | None = None,
    ) -> Any:
        self._steps.append(ScreenshotStep(path=path, full_page=full_page, label=label))
        return self

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def steps(self) -> list[BaseStep]:
        return list(self._steps)

    def build_scenario(self) -> Scenario:
        """Return the Scenario this Reel would execute — useful for testing/inspection."""
        # Steps in the builder are typed as `BaseStep` for storage flexibility,
        # but every concrete instance is a member of the discriminated `Step`
        # union; cast is the same technique the YAML parser uses.
        return Scenario(meta=self._meta, steps=cast("Any", list(self._steps)))


# --------------------------------------------------------------------------
# Async execution shared by both variants
# --------------------------------------------------------------------------


async def _run_and_encode(
    scenario: Scenario,
    out: Path,
    *,
    format_: Format | None,
    quality: int,
    loop: int,
    builder: ReportBuilder | None = None,
) -> tuple[Any, EncodeResult]:
    with Recorder(fps=scenario.meta.fps, default_dwell=scenario.meta.dwell) as rec:
        result = await run_scenario(scenario, recorder=rec, builder=builder)
        rec.flush()
        enc = encode(
            rec.frames_dir,
            out,
            fps=scenario.meta.fps,
            quality=quality,
            loop=loop,
            format=format_,
        )
    return result, enc


def _viewport_list_from_meta(scenario: Scenario) -> list[int] | None:
    vp = scenario.meta.viewport
    if not vp:
        return None
    try:
        w, h = vp.lower().split("x", 1)
        return [int(w), int(h)]
    except ValueError:
        return None


def _select_frame(frames: list[FrameRef], index: int) -> FrameRef:
    """Index into a captured-frame list with negative-index semantics.

    Raises IndexError with a helpful message; callers surface it to users.
    """
    if not frames:
        raise IndexError("no frames were captured — did the scenario run?")
    try:
        return frames[index]
    except IndexError as exc:
        raise IndexError(
            f"frame index {index} out of range for {len(frames)} captured frames"
        ) from exc


def _last_frame_for_step(frames: list[FrameRef], step_index: int) -> FrameRef:
    """Pick the LAST sub-frame recorded for a given step index.

    Per the issue, `save_region_at_step` uses the last sub-frame of the step
    because it reflects the settled post-action state (pre_action captures
    sub_index=0; post_action captures 1..N).
    """
    matching = [f for f in frames if f.step_index == step_index]
    if not matching:
        raise IndexError(
            f"no frames captured for step_index={step_index} "
            f"(recorded steps: {sorted({f.step_index for f in frames})})"
        )
    # Frames are appended in order, and sub_index monotonically increases per
    # step; still, sort defensively so callers get the true last frame.
    matching.sort(key=lambda f: f.sub_index)
    return matching[-1]


def _clip_bbox_to_image(
    bbox: tuple[int, int, int, int],
    padding: int,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Apply padding, clip to image edges, return (left, top, right, bottom).

    ``image_size`` is the frame PNG's ``(width, height)`` — because the frame
    is a viewport screenshot, clipping to the image bounds is equivalent to
    clipping to the viewport.
    """
    x, y, w, h = bbox
    img_w, img_h = image_size
    left = max(0, x - padding)
    top = max(0, y - padding)
    right = min(img_w, x + w + padding)
    bottom = min(img_h, y + h + padding)
    if right <= left or bottom <= top:
        raise ValueError(
            f"cropped region is empty after clipping: bbox={bbox} padding={padding} "
            f"image={image_size}"
        )
    return (left, top, right, bottom)


def _session_kwargs_for_bbox(scenario: Scenario) -> dict[str, Any]:
    """Mirror :func:`clickcast.scenario.scenario._session_kwargs_from_meta`
    for the private-URL-revisit session used by save_region. Kept local to
    avoid depending on a private symbol from another module.
    """
    meta = scenario.meta
    return {
        "engine": meta.engine,
        "viewport": meta.viewport,
        "device": meta.device,
        "headful": meta.headful,
        "slowmo": meta.slowmo,
        "lang": meta.lang,
        "dark": meta.dark,
    }


async def _bbox_via_fresh_session(
    scenario: Scenario, url: str, selector: str
) -> tuple[int, int, int, int]:
    """Re-navigate to ``url`` in a throwaway session and read the element bbox.

    This is the "simpler path" from the issue — no need to persist per-step
    layout data because the URL alone is enough to reproduce the DOM for
    static pages. Callers that need in-page state must persist bboxes at
    capture time (deferred follow-up).
    """
    async with Session(**_session_kwargs_for_bbox(scenario)) as sess:
        await sess.goto(url, wait="networkidle")
        try:
            box = await sess.bbox(selector)
        except Exception as exc:
            raise LookupError(
                f"selector {selector!r} not found at {url!r} — "
                f"cannot compute region bbox ({exc.__class__.__name__})"
            ) from exc
        if box is None:
            raise LookupError(
                f"selector {selector!r} matched an element with no layout "
                f"box (display:none / detached) at {url!r}"
            )
        return box


def _crop_and_save(
    frame_path: Path,
    bbox: tuple[int, int, int, int],
    padding: int,
    out: Path,
    format_: str,
) -> Path:
    """Load frame_path, crop to bbox±padding (clipped to image), save."""
    with Image.open(frame_path) as im:
        img = im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im.copy()
    crop_box = _clip_bbox_to_image(bbox, padding, img.size)
    cropped = img.crop(crop_box)
    out.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out, format=format_.upper())
    return out


def _write_sidecar_from_builder(
    out: Path,
    no_sidecar: bool,
    builder: ReportBuilder | None,
    enc: EncodeResult,
    fps: int,
) -> Path | None:
    if no_sidecar or builder is None:
        return None
    sidecar = out.with_suffix(out.suffix + ".json")
    media = Media(
        path=str(enc.path),
        format=enc.format,
        size_bytes=enc.size_bytes,
        frame_count=enc.frame_count,
        duration_s=enc.duration_s,
        fps=fps,
    )
    report = builder.build(media)
    write_report(report, sidecar)
    return sidecar


# --------------------------------------------------------------------------
# AsyncReel — for callers already inside a running event loop
# --------------------------------------------------------------------------


class AsyncReel(_BaseReel):
    """Async version of :class:`Reel`. Same builders, awaitable ``save()``."""

    async def _run_and_crop(
        self,
        selector: str,
        out: Path,
        *,
        padding: int,
        format_: str,
        frame_picker: Any,  # Callable[[list[FrameRef]], FrameRef]
    ) -> Path:
        scenario = self.build_scenario()
        with Recorder(fps=scenario.meta.fps, default_dwell=scenario.meta.dwell) as rec:
            await run_scenario(scenario, recorder=rec)
            rec.flush()
            frame = frame_picker(rec.frames)
            bbox = await _bbox_via_fresh_session(scenario, self._url, selector)
            return _crop_and_save(frame.path, bbox, padding, out, format_)

    async def save_region(
        self,
        selector: str,
        out: str | Path,
        *,
        frame: int = -1,
        padding: int = 0,
        format: str = "png",
    ) -> Path:
        """Run the scenario, crop a selector-anchored region from one frame.

        - ``frame`` indexes the flat list of captured frames (negative =
          from end; ``-1`` is the final frame of the last step).
        - ``padding`` grows the crop rect on all sides, clipped to viewport.
        - ``format`` is passed to Pillow's ``Image.save``; default ``png``.

        Raises :class:`LookupError` when ``selector`` is not found on the
        page after re-navigating to the reel's URL (the "simpler" path from
        #109 — no in-page state is reconstructed).
        """
        out_path = Path(out)
        return await self._run_and_crop(
            selector,
            out_path,
            padding=padding,
            format_=format,
            frame_picker=lambda frames: _select_frame(frames, frame),
        )

    async def save_region_at_step(
        self,
        step_index: int,
        selector: str,
        out: str | Path,
        *,
        padding: int = 0,
        format: str = "png",
    ) -> Path:
        """Same as :meth:`save_region` but picks the LAST sub-frame of
        ``step_index`` (i.e. the settled post-action state)."""
        out_path = Path(out)
        return await self._run_and_crop(
            selector,
            out_path,
            padding=padding,
            format_=format,
            frame_picker=lambda frames: _last_frame_for_step(frames, step_index),
        )

    async def save(
        self,
        path: str | Path,
        *,
        format: Format | None = None,
        quality: int = 8,
        loop: int = 0,
        no_sidecar: bool = False,
    ) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        scenario = self.build_scenario()
        builder: ReportBuilder | None = None
        if not no_sidecar:
            builder = ReportBuilder(
                url=self._url,
                engine=scenario.meta.engine,
                viewport=_viewport_list_from_meta(scenario),
            )
        result, enc = await _run_and_encode(
            scenario,
            out,
            format_=format,
            quality=quality,
            loop=loop,
            builder=builder,
        )
        if builder is not None and not result.ok:
            builder.add_warning(f"scenario failed at step {result.failed_at}")
        _write_sidecar_from_builder(out, no_sidecar, builder, enc, scenario.meta.fps)
        return enc.path


# --------------------------------------------------------------------------
# Reel — sync facade; blocks on the async pipeline
# --------------------------------------------------------------------------


class Reel(_BaseReel):
    """Sync version of :class:`AsyncReel`. Raises if called inside a running loop."""

    def _as_async(self) -> AsyncReel:
        """Rebuild an :class:`AsyncReel` sharing this reel's builder state."""
        async_reel = AsyncReel.__new__(AsyncReel)
        async_reel._url = self._url
        async_reel._meta = self._meta
        async_reel._steps = self._steps
        return async_reel

    def save(
        self,
        path: str | Path,
        *,
        format: Format | None = None,
        quality: int = 8,
        loop: int = 0,
        no_sidecar: bool = False,
    ) -> Path:
        _fail_if_running_loop("Reel.save()")
        # Reuse AsyncReel's implementation to avoid drift.
        return asyncio.run(
            self._as_async().save(
                path,
                format=format,
                quality=quality,
                loop=loop,
                no_sidecar=no_sidecar,
            )
        )

    def save_region(
        self,
        selector: str,
        out: str | Path,
        *,
        frame: int = -1,
        padding: int = 0,
        format: str = "png",
    ) -> Path:
        """Sync counterpart to :meth:`AsyncReel.save_region`."""
        _fail_if_running_loop("Reel.save_region()")
        return asyncio.run(
            self._as_async().save_region(selector, out, frame=frame, padding=padding, format=format)
        )

    def save_region_at_step(
        self,
        step_index: int,
        selector: str,
        out: str | Path,
        *,
        padding: int = 0,
        format: str = "png",
    ) -> Path:
        """Sync counterpart to :meth:`AsyncReel.save_region_at_step`."""
        _fail_if_running_loop("Reel.save_region_at_step()")
        return asyncio.run(
            self._as_async().save_region_at_step(
                step_index, selector, out, padding=padding, format=format
            )
        )


# --------------------------------------------------------------------------
# Sync discover facade — the top-level import users get
# --------------------------------------------------------------------------


def discover(
    url: str,
    *,
    interactive: bool = True,
    limit: int = 20,
    viewport: str | tuple[int, int] | None = None,
    engine: Engine = "chromium",
) -> list[Element]:
    """Sync wrapper around :func:`clickcast.discovery.discover`.

    Opens a throwaway :class:`Session`, navigates to ``url``, and returns the
    ranked list of interactive elements. Raises if called from a running
    event loop — use ``clickcast.discovery.discover`` (async) directly there.
    """
    _fail_if_running_loop("discover()")
    vp = _BaseReel._viewport_str(viewport) if viewport else None

    async def _run() -> list[Element]:
        async with Session(engine=engine, viewport=vp) as sess:
            await sess.goto(url, wait="networkidle")
            return await _async_discover(sess, interactive=interactive, limit=limit)

    return asyncio.run(_run())


def _fail_if_running_loop(caller: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        f"{caller} cannot be called from a running event loop — "
        f"use the async variant (AsyncReel / clickcast.discovery.discover) instead."
    )
