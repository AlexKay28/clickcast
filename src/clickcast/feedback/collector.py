"""Per-step page-state collector.

Subscribes to ``console``, ``pageerror`` and ``requestfailed`` events on a
Playwright :class:`Page` and keeps a per-step buffer. Call
:meth:`snapshot_and_clear` after each action to fold the buffered events plus
the current title/URL into a :class:`~clickcast.feedback.models.PageState`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from clickcast.feedback.models import PageState

if TYPE_CHECKING:
    from playwright.async_api import Page


__all__ = ["PageStateCollector"]


class PageStateCollector:
    _MAX = 50

    def __init__(self, page: Page) -> None:
        self._page = page
        self._attached = False
        self._console_errors: list[str] = []
        self._page_errors: list[str] = []
        self._network_failed: list[str] = []

        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("requestfailed", self._on_requestfailed)
        self._attached = True

    def detach(self) -> None:
        """Remove all listeners from the page. Idempotent."""
        if not self._attached:
            return
        with contextlib.suppress(Exception):
            self._page.remove_listener("console", self._on_console)
        with contextlib.suppress(Exception):
            self._page.remove_listener("pageerror", self._on_pageerror)
        with contextlib.suppress(Exception):
            self._page.remove_listener("requestfailed", self._on_requestfailed)
        self._attached = False

    def _on_console(self, msg: Any) -> None:
        if len(self._console_errors) >= self._MAX:
            return
        try:
            msg_type = msg.type
            msg_text = msg.text
        except AttributeError:
            return
        if msg_type == "error":
            self._console_errors.append(str(msg_text))

    def _on_pageerror(self, err: Any) -> None:
        if len(self._page_errors) >= self._MAX:
            return
        self._page_errors.append(str(err))

    def _on_requestfailed(self, req: Any) -> None:
        if len(self._network_failed) >= self._MAX:
            return
        with contextlib.suppress(AttributeError):
            self._network_failed.append(str(req.url))

    async def snapshot_and_clear(self) -> PageState:
        """Capture the current title / URL and buffered events, then clear."""
        try:
            title = await self._page.title()
        except Exception:
            title = ""
        try:
            url_after = self._page.url or ""
        except Exception:
            url_after = ""

        state = PageState(
            title=title,
            url_after=url_after,
            console_errors=list(self._console_errors),
            page_errors=list(self._page_errors),
            network_failed=list(self._network_failed),
        )
        self._console_errors.clear()
        self._page_errors.clear()
        self._network_failed.clear()
        return state
