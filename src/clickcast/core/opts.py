"""Grouped browser-behaviour and render-output option dataclasses.

Before this module existed, the same ~8 browser fields (engine, viewport,
device, headful, lang, dark, slowmo, proxy) lived in four separate
containers — `Meta`, `Config`, `_BaseReel.__init__`, `_session_kwargs` —
each with a slightly different shape. Adding a new browser flag meant
touching four files and hoping the drift didn't cause bugs (it usually
did; see #77 and #84).

This module makes `BrowserOpts` and `RenderOpts` the **source of truth**
for the field lists. `Meta` and `Config` embed them as nested fields
(with `model_validator`s that accept the old flat YAML / env-var shape
for backwards compatibility). Everything downstream — Reel, Session,
CLI — reads/writes through the nested structs.

Design notes:

- Both dataclasses are **mutable** (no ``frozen=True``). The CLI's
  `run` command mutates `meta.browser.headful` post-load when the user
  overrides via ``--headful``, and Reel's fluent chain builds them up
  step by step. Frozen would force a lot of ``dataclasses.replace``.
- Slotted for memory + attribute-typo hygiene.
- `BrowserOpts.viewport` uses the :class:`~clickcast.core.viewport.Viewport`
  value type from #96 instead of a raw ``"WxH"`` string / tuple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clickcast.core.viewport import Viewport

__all__ = ["BrowserOpts", "RenderOpts"]


@dataclass(slots=True)
class BrowserOpts:
    """Every field that describes HOW the browser is launched + configured.

    New browser-behaviour flag? Add it here (and to
    :meth:`to_session_kwargs` if it needs to propagate to Playwright).
    Meta / Config / Reel pick it up automatically via their nested field.
    """

    engine: str = "chromium"  # "chromium" | "firefox" | "webkit"
    viewport: Viewport = field(default_factory=lambda: Viewport(1280, 800))
    device: str | None = None
    headful: bool = False
    lang: str | None = None
    dark: bool = False
    slowmo: int = 0
    proxy: str | None = None

    def to_session_kwargs(self) -> dict[str, Any]:
        """Dict shape :class:`~clickcast.core.session.Session` expects.

        Kept as a dict rather than typed kwargs so callers can spread
        it with ``**opts.to_session_kwargs()`` unchanged when
        :class:`Session` gains a new constructor field — the field lands
        in :class:`BrowserOpts`, this method threads it through, done.
        """
        return {
            "engine": self.engine,
            "viewport": self.viewport.as_tuple(),
            "device": self.device,
            "headful": self.headful,
            "lang": self.lang,
            "dark": self.dark,
            "slowmo": self.slowmo,
        }


@dataclass(slots=True)
class RenderOpts:
    """Every field that describes HOW the recorded frames are encoded."""

    fps: int = 12
    quality: int = 8
    loop: int = 0
    format: str = "gif"  # "gif" | "mp4" | "webp" | "frames"
