"""Per-step page-state collector.

Subscribes to ``console``, ``pageerror`` and ``requestfailed`` events on a
:class:`~clickcast.core.session.Session` and keeps a per-step buffer. Call
:meth:`snapshot_and_clear` after each action to fold the buffered events plus
the current title/URL into a :class:`~clickcast.feedback.models.PageState`.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from clickcast.feedback.models import PageState

if TYPE_CHECKING:
    from clickcast.core.session import Session


__all__ = ["PageStateCollector"]

logger = logging.getLogger(__name__)


class PageStateCollector:
    _MAX = 50

    def __init__(self, session: Session) -> None:
        self._session = session
        self._attached = False
        self._console_errors: list[str] = []
        self._page_errors: list[str] = []
        self._network_failed: list[str] = []

        session.on("console", self._on_console)
        session.on("pageerror", self._on_pageerror)
        session.on("requestfailed", self._on_requestfailed)
        self._attached = True

    def detach(self) -> None:
        """Remove all listeners from the session's page. Idempotent."""
        if not self._attached:
            return
        try:
            self._session.off("console", self._on_console)
        except Exception as exc:
            logger.debug("collector detach failed for %s: %r", "console", exc)
        try:
            self._session.off("pageerror", self._on_pageerror)
        except Exception as exc:
            logger.debug("collector detach failed for %s: %r", "pageerror", exc)
        try:
            self._session.off("requestfailed", self._on_requestfailed)
        except Exception as exc:
            logger.debug("collector detach failed for %s: %r", "requestfailed", exc)
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
        # Belt-and-suspenders: Session.title() and .url_now already swallow
        # exceptions, but tests that pass a raw duck-typed object rely on
        # this collector catching too.
        try:
            title = await self._session.title()
        except Exception:
            title = ""
        try:
            url_after = self._session.url_now
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
