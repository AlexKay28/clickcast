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
]


class BaseStep(BaseModel):
    """Fields common to every step type."""

    model_config = ConfigDict(extra="forbid")

    action: str
    label: str | None = None
    dwell: float = 0.0
    optional: bool = False
    repeat: int = Field(default=1, ge=1)


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


async def _center_of(locator: Locator) -> tuple[int, int] | None:
    box = await locator.bounding_box()
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

    try:
        if isinstance(step, GotoStep):
            await session.goto(step.url, wait=step.wait)
        elif isinstance(step, ClickStep):
            selector = step.selector
            loc = session.page.locator(step.selector)
            cursor_xy = await _center_of(loc)
            await loc.click()
        elif isinstance(step, DblClickStep):
            selector = step.selector
            loc = session.page.locator(step.selector)
            cursor_xy = await _center_of(loc)
            await loc.dblclick()
        elif isinstance(step, HoverStep):
            selector = step.selector
            loc = session.page.locator(step.selector)
            cursor_xy = await _center_of(loc)
            await loc.hover()
        elif isinstance(step, TypeStep):
            selector = step.into
            loc = session.page.locator(step.into)
            cursor_xy = await _center_of(loc)
            await loc.press_sequentially(step.text, delay=step.delay)
        elif isinstance(step, PressStep):
            selector = step.selector
            if step.selector:
                await session.page.locator(step.selector).press(step.key)
            else:
                await session.page.keyboard.press(step.key)
        elif isinstance(step, SelectStep):
            selector = step.into
            loc = session.page.locator(step.into)
            cursor_xy = await _center_of(loc)
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
