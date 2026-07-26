"""Action engine — execute a single scenario step atomically."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from playwright.async_api import Locator
from pydantic import BaseModel, ConfigDict, Field

from clickcast.core.session import Session, WaitArg

# Selector shapes whose failures benefit from suggest_candidates hints
# (see #114). We only augment click-shaped step errors — a scroll/wait/goto
# failure is orthogonal and would just add noise.
_HINTABLE_ACTIONS: frozenset[str] = frozenset({"click", "dblclick", "hover", "type"})

# Substrings in Playwright error messages that indicate the selector didn't
# resolve to anything. TimeoutError classes and this text are the two
# canonical "we couldn't find the element" signals.
_LOCATOR_MISSING_MARKERS: tuple[str, ...] = (
    "locator resolved to 0 elements",
    "waiting for locator",
)

# Process-wide flag toggled by the CLI's `--dump-elements` option. When set,
# the failure hook appends the full discover() list to the error string
# (default is just the top-5 candidates). Kept module-scope because the
# CLI layer can't reach into the action executor otherwise — actions.py
# is called deep inside auto/run and doesn't take a CLI-flags param.
_dump_elements_on_failure: bool = False


def set_dump_elements(enabled: bool) -> None:
    """Enable/disable full-element dump on failure (see ``--dump-elements``).

    Called from the CLI; safe to flip on/off between runs. Independent of
    the top-5 hint block, which is always attached.
    """
    global _dump_elements_on_failure
    _dump_elements_on_failure = enabled


def _dump_enabled() -> bool:
    return _dump_elements_on_failure


__all__ = [
    "ActionResult",
    "BaseStep",
    "ClickStep",
    "DblClickStep",
    "GotoStep",
    "HoverStep",
    "PressStep",
    "ScreenshotStep",
    "ScrollStep",
    "SelectStep",
    "Step",
    "TypeStep",
    "WaitStep",
    "execute",
    "set_dump_elements",
]


class BaseStep(BaseModel):
    """Fields common to every step type."""

    model_config = ConfigDict(extra="forbid")

    action: str
    label: str | None = None
    dwell: float = 0.0
    optional: bool = False
    repeat: int = Field(default=1, ge=1)
    # Override Playwright's per-op timeout (default 30_000ms). Kept small in
    # `auto` mode via _explore_page so a stuck click can't burn 30s.
    # `None` = use Playwright default (preserves prior scenario behavior).
    timeout_ms: int | None = None


class GotoStep(BaseStep):
    action: Literal["goto"] = "goto"
    url: str
    wait: WaitArg | None = None


class ClickStep(BaseStep):
    action: Literal["click"] = "click"
    selector: str


class DblClickStep(BaseStep):
    action: Literal["dblclick"] = "dblclick"
    selector: str


class HoverStep(BaseStep):
    action: Literal["hover"] = "hover"
    selector: str


class TypeStep(BaseStep):
    action: Literal["type"] = "type"
    into: str
    text: str
    delay: float = 0.0


class PressStep(BaseStep):
    action: Literal["press"] = "press"
    key: str
    selector: str | None = None


class SelectStep(BaseStep):
    action: Literal["select"] = "select"
    into: str
    value: str | list[str]


class ScrollStep(BaseStep):
    action: Literal["scroll"] = "scroll"
    to: str | None = None
    by: int | None = None


class WaitStep(BaseStep):
    action: Literal["wait"] = "wait"
    wait: WaitArg


class ScreenshotStep(BaseStep):
    action: Literal["screenshot"] = "screenshot"
    full_page: bool = False
    path: str | None = None


Step = Annotated[
    GotoStep
    | ClickStep
    | DblClickStep
    | HoverStep
    | TypeStep
    | PressStep
    | SelectStep
    | ScrollStep
    | WaitStep
    | ScreenshotStep,
    Field(discriminator="action"),
]


@dataclass(slots=True, frozen=True)
class ActionResult:
    ok: bool
    status: Literal["ok", "failed", "skipped"]
    action: str
    selector: str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    screenshot_path: Path | None = None
    cursor_xy: tuple[int, int] | None = None


async def _center_of(locator: Locator, timeout_ms: int | None = None) -> tuple[int, int] | None:
    kwargs = {"timeout": timeout_ms} if timeout_ms is not None else {}
    box = await locator.bounding_box(**kwargs)
    if box is None:
        return None
    return (
        int(box["x"] + box["width"] / 2),
        int(box["y"] + box["height"] / 2),
    )


async def execute(step: BaseStep, session: Session) -> ActionResult:
    """Run one step. Honors `dwell` and `optional`; caller loops for `repeat`."""
    start = time.monotonic()
    selector: str | None = None
    cursor_xy: tuple[int, int] | None = None
    screenshot_path: Path | None = None

    # Playwright typing rejects **kwargs unpacking with timeout, so each
    # action passes an explicit `timeout=...` when the step overrides it.
    # Without an override, Playwright's 30s default applies.
    timeout = step.timeout_ms

    try:
        if isinstance(step, GotoStep):
            await session.goto(step.url, wait=step.wait)
        elif isinstance(step, ClickStep):
            selector = step.selector
            loc = session.page.locator(step.selector)
            cursor_xy = await _center_of(loc, timeout_ms=timeout)
            if timeout is not None:
                await loc.click(timeout=timeout)
            else:
                await loc.click()
        elif isinstance(step, DblClickStep):
            selector = step.selector
            loc = session.page.locator(step.selector)
            cursor_xy = await _center_of(loc, timeout_ms=timeout)
            if timeout is not None:
                await loc.dblclick(timeout=timeout)
            else:
                await loc.dblclick()
        elif isinstance(step, HoverStep):
            selector = step.selector
            loc = session.page.locator(step.selector)
            cursor_xy = await _center_of(loc, timeout_ms=timeout)
            if timeout is not None:
                await loc.hover(timeout=timeout)
            else:
                await loc.hover()
        elif isinstance(step, TypeStep):
            selector = step.into
            loc = session.page.locator(step.into)
            cursor_xy = await _center_of(loc, timeout_ms=timeout)
            if timeout is not None:
                await loc.press_sequentially(step.text, delay=step.delay, timeout=timeout)
            else:
                await loc.press_sequentially(step.text, delay=step.delay)
        elif isinstance(step, PressStep):
            selector = step.selector
            if step.selector:
                if timeout is not None:
                    await session.page.locator(step.selector).press(step.key, timeout=timeout)
                else:
                    await session.page.locator(step.selector).press(step.key)
            else:
                await session.page.keyboard.press(step.key)
        elif isinstance(step, SelectStep):
            selector = step.into
            loc = session.page.locator(step.into)
            cursor_xy = await _center_of(loc, timeout_ms=timeout)
            if timeout is not None:
                await loc.select_option(step.value, timeout=timeout)
            else:
                await loc.select_option(step.value)
        elif isinstance(step, ScrollStep):
            if step.to is not None:
                selector = step.to
                await session.page.locator(step.to).scroll_into_view_if_needed()
            elif step.by is not None:
                await session.page.mouse.wheel(0, step.by)
            else:
                raise ValueError("ScrollStep requires either `to` (selector) or `by` (pixels)")
        elif isinstance(step, WaitStep):
            await session.wait(step.wait)
        elif isinstance(step, ScreenshotStep):
            await session.screenshot(path=step.path, full_page=step.full_page)
            if step.path is not None:
                screenshot_path = Path(step.path)
        else:
            raise TypeError(f"Unknown step type: {type(step).__name__}")

        if step.dwell > 0:
            await asyncio.sleep(step.dwell)

        duration_ms = (time.monotonic() - start) * 1000.0
        return ActionResult(
            ok=True,
            status="ok",
            action=step.action,
            selector=selector,
            duration_ms=duration_ms,
            screenshot_path=screenshot_path,
            cursor_xy=cursor_xy,
        )
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000.0
        message = f"{type(e).__name__}: {e}"
        message = await _augment_with_hints(message, step, session, selector, e)
        if step.optional:
            return ActionResult(
                ok=True,
                status="skipped",
                action=step.action,
                selector=selector,
                error=message,
                duration_ms=duration_ms,
            )
        return ActionResult(
            ok=False,
            status="failed",
            action=step.action,
            selector=selector,
            error=message,
            duration_ms=duration_ms,
        )


async def _augment_with_hints(
    base_message: str,
    step: BaseStep,
    session: Session,
    selector: str | None,
    exc: BaseException,
) -> str:
    """Append a `suggest_candidates` block when a click-shaped step fails
    because the selector didn't resolve. Silent on every other failure
    path — a scroll/goto timeout and a "selector not found" click have
    different remedies and mixing them would just add noise.

    Import-locally so a broken hints module can never take down the
    ordinary error-return path — the base message is always safe to
    return unchanged.
    """
    if step.action not in _HINTABLE_ACTIONS or not selector:
        return base_message
    err_text = str(exc)
    # Only fire on TimeoutError or the explicit "0 elements" message —
    # otherwise a genuine action failure (button clicked but handler
    # raised) would drop misleading hints into the error.
    is_timeout = "Timeout" in type(exc).__name__
    if not is_timeout and not any(m in err_text for m in _LOCATOR_MISSING_MARKERS):
        return base_message

    try:
        from clickcast.discovery import discover
        from clickcast.discovery.hints import format_candidates, suggest_candidates

        candidates = await suggest_candidates(session, selector)
        # Recount without the discover(limit=20) cap for the tail line —
        # in practice the pool is small enough that this second call is
        # cheap and gives an accurate "N interactive elements" number.
        all_elements = await discover(session, limit=200)
        dump = _dump_enabled()
        hint_block = format_candidates(
            selector,
            candidates,
            total_discovered=len(all_elements),
            dump_hint=not dump,
        )
        if dump and all_elements:
            hint_block += "\n\n  Full discover() list:"
            for el in all_elements:
                bbox = f"[{el.bbox[0]}, {el.bbox[1]}, {el.bbox[2]}, {el.bbox[3]}]"
                hint_block += f"\n    {el.selector:<45} bbox={bbox:<28} role={el.role}"
    except Exception:
        # Never let the hint pipeline mask the original failure.
        return base_message
    return f"{base_message}\n{hint_block}"
