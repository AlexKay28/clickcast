"""Browser session core: owns Playwright launch, context, and page lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from clickcast.core.viewport import Viewport

__all__ = [
    "Engine",
    "LoadState",
    "Locator",
    "PlaywrightTimeoutError",
    "Session",
    "WaitArg",
    "hostname_matches",
]

Engine = Literal["chromium", "firefox", "webkit"]
LoadState = Literal["load", "domcontentloaded", "networkidle"]
WaitArg = int | float | str

_LOAD_STATES: frozenset[str] = frozenset({"load", "domcontentloaded", "networkidle"})


def hostname_matches(url: str, host: str) -> bool:
    """True if the request URL's hostname equals ``host`` or is a subdomain.

    Substring `in` was tempting but a footgun — e.g. ``host=".net"`` would
    match ``cdn.example.net`` and defeat the whole point of `--header-host`.
    A dotted-suffix match (``.example.com`` matches ``a.example.com`` and
    ``example.com``) is the sharp form. To keep users from accidentally
    scoping to a whole TLD, we only allow dotted-suffix matching when the
    target itself contains at least one internal dot — a bare label like
    ``net`` or ``localhost`` only matches exactly. Empty ``host`` never
    matches.
    """
    if not host:
        return False
    parsed_host = (urlparse(url).hostname or "").lower()
    if not parsed_host:
        return False
    target = host.lower().lstrip(".")
    if not target:
        return False
    if parsed_host == target:
        return True
    if "." in target:
        return parsed_host.endswith("." + target)
    return False


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
        viewport: str | tuple[int, int] | Viewport | None = None,
        device: str | None = None,
        headful: bool = False,
        slowmo: int = 0,
        proxy: str | dict[str, str] | None = None,
        lang: str | None = None,
        dark: bool = False,
        extra_http_headers: dict[str, str] | None = None,
        ignore_https_errors: bool = False,
        header_host: str | None = None,
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
        self.ignore_https_errors = ignore_https_errors
        # #166: when set, extra_http_headers is NOT applied at context level;
        # a route interceptor injects it only for requests whose hostname
        # matches ``header_host`` (exact or dotted-suffix). Keeps bearer
        # tokens off CDN / analytics subresources.
        self.header_host = header_host

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

            # #166: scoped-header path. When ``header_host`` is set, headers
            # attach via route interception at request time rather than the
            # context-wide ``extra_http_headers`` set above (which fires on
            # every origin, leaking tokens to CDNs / analytics).
            if self.header_host and self.extra_http_headers:
                await self._install_scoped_header_route()

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
        if self.viewport is not None:
            vp = Viewport.parse(self.viewport)
            kwargs["viewport"] = {"width": vp.width, "height": vp.height}
        if self.lang:
            kwargs["locale"] = self.lang
        if self.dark:
            kwargs["color_scheme"] = "dark"
        # #166: only set context-wide headers when NOT scoped to a host —
        # scoped delivery happens via the route interceptor installed in
        # __aenter__.
        if self.extra_http_headers and not self.header_host:
            kwargs["extra_http_headers"] = dict(self.extra_http_headers)
        if self.ignore_https_errors:
            kwargs["ignore_https_errors"] = True
        if self.proxy:
            if isinstance(self.proxy, str):
                kwargs["proxy"] = {"server": self.proxy}
            else:
                kwargs["proxy"] = dict(self.proxy)
        return kwargs

    async def _install_scoped_header_route(self) -> None:
        """Register a ``context.route`` that injects ``extra_http_headers``
        only for requests whose hostname matches :attr:`header_host`.

        Note: route interception adds a Python round-trip per request, so
        we only install it when both a header and a scope are set — the
        common no-auth or global-header cases still fly through Chromium
        untouched via the context-wide path in :meth:`_context_kwargs`.
        """
        assert self._context is not None
        headers = dict(self.extra_http_headers or {})
        host = self.header_host

        async def _inject(route: Route) -> None:
            request_headers = dict(route.request.headers)
            if host and hostname_matches(route.request.url, host):
                request_headers.update(headers)
            await route.continue_(headers=request_headers)

        await self._context.route("**/*", _inject)

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

    async def bbox(self, selector: str) -> tuple[int, int, int, int] | None:
        """Return ``(x, y, width, height)`` of the first match for ``selector``.

        Returns ``None`` if the element exists but has no layout box (e.g.
        ``display: none``). Raises whatever Playwright raises when the
        selector matches nothing (usually :class:`TimeoutError` from
        ``wait_for``); callers wrap for a friendlier message.
        """
        locator = self.page.locator(selector).first
        # Ensure the element is present before asking for a box; without this
        # Playwright returns ``None`` for "not attached yet" *and* for
        # zero-size elements, which we want to distinguish.
        await locator.wait_for(state="attached")
        box = await locator.bounding_box()
        if box is None:
            return None
        # ``round`` on a float already returns an int in Python 3.
        return (
            round(box["x"]),
            round(box["y"]),
            round(box["width"]),
            round(box["height"]),
        )

    async def close(self) -> None:
        await self.__aexit__(None, None, None)

    # ------------------------------------------------------------------
    # Narrow Page seam (#98) — every business-logic caller reaches through
    # these methods instead of `session.page.*`. Playwright's `Locator` /
    # `TimeoutError` types are re-exported from this module so callers
    # never need to `from playwright.async_api import ...`.
    # ------------------------------------------------------------------

    def locator(self, selector: str) -> Locator:
        """Return a Playwright :class:`Locator` for ``selector``."""
        return self.page.locator(selector)

    async def evaluate(self, script: str, *args: Any) -> Any:
        """Run a JS ``script`` in the page context. Positional ``args`` are
        packed into a single array argument as Playwright's ``evaluate``
        expects a single JSON-serializable payload."""
        if args:
            payload = args[0] if len(args) == 1 else list(args)
            return await self.page.evaluate(script, payload)
        return await self.page.evaluate(script)

    async def press_key(self, key: str) -> None:
        """Press ``key`` on the page-level keyboard (no locator needed)."""
        await self.page.keyboard.press(key)

    async def wheel(self, dx: int, dy: int) -> None:
        """Dispatch a page-level wheel event at the current mouse position."""
        await self.page.mouse.wheel(dx, dy)

    async def title(self) -> str:
        """Return the current page title. Empty string on failure."""
        try:
            return await self.page.title()
        except Exception:
            return ""

    @property
    def url_now(self) -> str:
        """Return the current page URL. Empty string on failure. Kept as a
        distinct name from ``self.url`` (the constructor arg) so callers can
        tell them apart."""
        try:
            return self.page.url or ""
        except Exception:
            return ""

    def on(self, event: str, callback: Any) -> None:
        """Subscribe ``callback`` to a page event
        (``console`` / ``pageerror`` / ``requestfailed`` / etc.)."""
        # Playwright's Page.on has per-event Literal-typed overloads; our
        # narrow-seam wrapper accepts the union of them as a plain str for
        # collector-side flexibility.
        self.page.on(event, callback)  # type: ignore[call-overload]

    def off(self, event: str, callback: Any) -> None:
        """Unsubscribe ``callback`` from a page event. Idempotent."""
        self.page.remove_listener(event, callback)

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
