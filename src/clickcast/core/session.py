"""Browser session core: owns Playwright launch, context, and page lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

__all__ = ["Engine", "LoadState", "Session", "WaitArg"]

Engine = Literal["chromium", "firefox", "webkit"]
LoadState = Literal["load", "domcontentloaded", "networkidle"]
WaitArg = int | float | str

_LOAD_STATES: frozenset[str] = frozenset({"load", "domcontentloaded", "networkidle"})


def _parse_viewport(v: str | tuple[int, int] | None) -> tuple[int, int] | None:
    if v is None:
        return None
    if isinstance(v, tuple):
        return (int(v[0]), int(v[1]))
    w, h = v.lower().split("x", 1)
    return (int(w), int(h))


class Session:
    """Playwright browser session with deterministic async teardown.

    Use as an async context manager::

        async with Session(engine="chromium", viewport="1280x800") as sess:
            await sess.goto("https://example.com", wait="networkidle")
            png = await sess.screenshot()
    """

    def __init__(
        self,
        *,
        engine: Engine = "chromium",
        viewport: str | tuple[int, int] | None = None,
        device: str | None = None,
        headful: bool = False,
        slowmo: int = 0,
        proxy: str | dict[str, str] | None = None,
        lang: str | None = None,
        dark: bool = False,
        extra_http_headers: dict[str, str] | None = None,
    ) -> None:
        self.engine: Engine = engine
        self.viewport = viewport
        self.device = device
        self.headful = headful
        self.slowmo = slowmo
        self.proxy = proxy
        self.lang = lang
        self.dark = dark
        self.extra_http_headers = extra_http_headers

        self._stack: AsyncExitStack | None = None
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> Session:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            self._pw = await stack.enter_async_context(async_playwright())
            browser_type = getattr(self._pw, self.engine)
            self._browser = await browser_type.launch(
                headless=not self.headful,
                slow_mo=int(self.slowmo),
            )
            stack.push_async_callback(self._browser.close)

            self._context = await self._browser.new_context(**self._context_kwargs())
            stack.push_async_callback(self._context.close)

            self._page = await self._context.new_page()
            self._stack = stack
        except BaseException:
            await stack.aclose()
            self._pw = None
            self._browser = None
            self._context = None
            self._page = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        if stack is not None:
            await stack.__aexit__(exc_type, exc, tb)

    def _context_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.device:
            assert self._pw is not None
            preset = self._pw.devices.get(self.device)
            if preset is None:
                raise ValueError(f"Unknown device preset: {self.device!r}")
            kwargs.update(preset)
        if (vp := _parse_viewport(self.viewport)) is not None:
            kwargs["viewport"] = {"width": vp[0], "height": vp[1]}
        if self.lang:
            kwargs["locale"] = self.lang
        if self.dark:
            kwargs["color_scheme"] = "dark"
        if self.extra_http_headers:
            kwargs["extra_http_headers"] = dict(self.extra_http_headers)
        if self.proxy:
            if isinstance(self.proxy, str):
                kwargs["proxy"] = {"server": self.proxy}
            else:
                kwargs["proxy"] = dict(self.proxy)
        return kwargs

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Session is not open — use `async with Session(...) as sess:`")
        return self._page

    async def goto(self, url: str, wait: WaitArg | None = None) -> None:
        await self.page.goto(url)
        await self._wait(wait)

    async def screenshot(
        self,
        path: str | Path | None = None,
        *,
        full_page: bool = False,
    ) -> bytes:
        return await self.page.screenshot(
            path=path,
            full_page=full_page,
            type="png",
        )

    async def close(self) -> None:
        await self.__aexit__(None, None, None)

    async def wait(self, wait: WaitArg | None) -> None:
        """Polymorphic wait: number → sleep, load-state → wait_for_load_state, else selector."""
        await self._wait(wait)

    async def _wait(self, wait: WaitArg | None) -> None:
        if wait is None:
            return
        if isinstance(wait, bool):
            raise TypeError("wait must be a number, load state, or selector — not bool")
        if isinstance(wait, int | float):
            await asyncio.sleep(float(wait))
            return
        if not isinstance(wait, str):
            raise TypeError(f"Unsupported wait type: {type(wait).__name__}")
        if wait in _LOAD_STATES:
            await self.page.wait_for_load_state(wait)  # type: ignore[arg-type]
        else:
            await self.page.wait_for_selector(wait)
