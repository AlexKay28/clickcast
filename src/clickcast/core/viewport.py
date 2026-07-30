"""Single value type for browser viewport dimensions.

Before this module existed, half a dozen sites parsed ``"WxH"`` strings
into ``(int, int)`` tuples with slightly different code, slightly
different error handling, and slightly different accepted input shapes
(some accepted ``None``, some accepted tuples, one accepted a Meta
object). This module collapses all of that into one immutable value
type + one classmethod that accepts every prior input shape idempotently.

The public surface is deliberately tiny:

- :class:`Viewport` — a frozen dataclass of ``(width, height)``.
- :meth:`Viewport.parse` — accepts ``str`` (``"WxH"``), ``tuple[int, int]``,
  or an existing ``Viewport``; returns a canonical instance. Idempotent.
- :meth:`Viewport.__str__` — canonical ``"WxH"`` serialization.
- :meth:`Viewport.as_tuple` / :meth:`Viewport.as_list` — convenience
  accessors for the two most common downstream shapes (Playwright wants
  ``(w, h)``, the sidecar JSON wants ``[w, h]``).

Callers that used to accept ``str | tuple[int, int] | None`` should now
accept ``str | tuple[int, int] | Viewport | None`` and coerce at the
edge via ``Viewport.parse(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Viewport"]


@dataclass(frozen=True, slots=True)
class Viewport:
    """Immutable ``(width, height)`` value type."""

    width: int
    height: int

    @classmethod
    def parse(cls, raw: str | tuple[int, int] | Viewport) -> Viewport:
        """Coerce any supported input shape into a canonical ``Viewport``.

        Idempotent: passing an existing ``Viewport`` returns it unchanged.
        Case-insensitive on the string form (``"1280x800"``, ``"1280X800"``
        both parse the same). Rejects malformed input with a ``ValueError``
        that names the offending value.
        """
        if isinstance(raw, Viewport):
            return raw
        if isinstance(raw, tuple):
            if len(raw) != 2:
                raise ValueError(f"viewport tuple must be (width, height); got {raw!r}")
            w, h = raw
            return cls(int(w), int(h))
        if isinstance(raw, str):
            try:
                w_str, h_str = raw.lower().split("x", 1)
                return cls(int(w_str), int(h_str))
            except ValueError as exc:
                raise ValueError(
                    f"invalid viewport string {raw!r}; expected 'WxH' (e.g. '1280x800')"
                ) from exc
        raise TypeError(
            f"viewport must be str, tuple[int, int], or Viewport; got {type(raw).__name__}"
        )

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"

    def as_tuple(self) -> tuple[int, int]:
        return (self.width, self.height)

    def as_list(self) -> list[int]:
        return [self.width, self.height]
