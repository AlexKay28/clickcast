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

from clickcast.capture import Recorder
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
        async_reel = AsyncReel.__new__(AsyncReel)
        async_reel._url = self._url
        async_reel._meta = self._meta
        async_reel._steps = self._steps
        return asyncio.run(
            async_reel.save(
                path,
                format=format,
                quality=quality,
                loop=loop,
                no_sidecar=no_sidecar,
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
