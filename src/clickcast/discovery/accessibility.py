"""Accessibility-node capture, fused with pixel + grid coordinates (#196).

Playwright's own accessibility tooling is the thing agents cite as useful
about Playwright — but ``clickcast elements``/the sidecar only ever
surfaced a hand-rolled DOM heuristic (see :mod:`clickcast.discovery.discovery`).
This module closes that gap for every element clickcast already discovers:

- :func:`capture_accessibility` resolves one :class:`AccessibleElement` per
  discovered candidate via Playwright's ``Locator.aria_snapshot()`` — the
  modern replacement for the (Python-binding-removed) ``page.accessibility``
  tree API. ``aria_snapshot()`` returns a small YAML fragment; only the
  first line describes the queried element itself (nested lines describe
  its children / attributes), so :func:`parse_aria_snapshot` reads just
  that line for ``role``, accessible ``name``, and interactive ``state``
  (``disabled``/``checked``/``expanded``/``pressed``/``selected`` — ARIA
  snapshots only emit states Playwright can positively confirm, so an
  absent key means "not applicable/unknown", not "false").
- Never raises over an unresolved accessible name: any Playwright error
  (locator vanished, ambiguous match, timeout) degrades to an
  all-``None`` role/name/state rather than failing discovery (#197).
- :class:`AccessibleElement` additionally carries the element's ``bbox``
  cast through :func:`clickcast.annotate.grid.grid_cell` — the exact pitch
  math the pixel-grid overlay (#171) draws with — so a caller with an
  active :class:`~clickcast.annotate.grid.GridConfig` gets one fused
  payload: selector + bbox + score + role/name/state + grid cell (#198).
  ``grid_cell`` stays ``None`` when no grid options were passed.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from clickcast.annotate.grid import GridConfig
from clickcast.annotate.grid import grid_cell as _grid_cell

if TYPE_CHECKING:
    from clickcast.core.session import Session
    from clickcast.discovery.discovery import Element

__all__ = [
    "AccessibilityState",
    "AccessibleElement",
    "capture_accessibility",
    "capture_accessibility_batch",
    "parse_aria_snapshot",
]

# Matches the first line of a Playwright ARIA snapshot, e.g.:
#   - button "Get started": Go
#   - checkbox "agree" [checked]
#   - button
_ARIA_LINE_RE = re.compile(r'^-\s+(?P<role>[A-Za-z][\w-]*)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?')
# Bracketed state tokens: `[disabled]`, `[checked=mixed]`, `[level=2]`, ...
_STATE_TOKEN_RE = re.compile(r"\[([a-zA-Z]+)(?:=([\w-]+))?\]")
# Only these are meaningful "interactive state" per #197 — other bracketed
# aria-snapshot annotations (e.g. `[level=2]` on headings) are ignored.
_KNOWN_STATE_KEYS = frozenset({"disabled", "checked", "expanded", "pressed", "selected"})

# Playwright's own default timeout (30s) is far too generous for a
# best-effort, must-not-block-discovery lookup; a resolvable element
# responds in well under this on any real page.
_DEFAULT_TIMEOUT_MS = 2000.0


@dataclass(slots=True, frozen=True)
class AccessibilityState:
    """Interactive ARIA states for one element — #197.

    Each field is ``None`` when Playwright's ARIA snapshot didn't report
    that state for this element (either the role doesn't support it, or
    the value is the non-affirmative default) — not to be read as "false".
    ``checked`` can be the string ``"mixed"`` (tri-state checkboxes), so
    it's typed as ``bool | str | None`` rather than a plain ``bool``.
    """

    disabled: bool | None = None
    checked: bool | str | None = None
    expanded: bool | None = None
    pressed: bool | None = None
    selected: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        """True when no state was captured — every field is ``None``."""
        return all(v is None for v in asdict(self).values())


@dataclass(slots=True, frozen=True)
class AccessibleElement:
    """One discovered element's selector/bbox/score fused with its
    accessibility node and (optionally) its grid cell — #198.

    ``selector``/``bbox``/``score`` mirror the corresponding
    :class:`clickcast.discovery.discovery.Element` fields verbatim (same
    element, same discovery pass) so a consumer never has to cross-
    reference two payloads. ``role``/``name`` come from Playwright's own
    accessibility resolution — NOT the DOM-heuristic ``Element.role`` /
    ``Element.text`` fields, which stay driven by :mod:`discovery.hints`
    for selector construction and are unaffected by this module (#197
    acceptance: "discovery's existing selector/score output unchanged").
    """

    selector: str
    bbox: tuple[int, int, int, int]
    score: int
    role: str | None
    name: str | None
    state: AccessibilityState
    grid_cell: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "bbox": list(self.bbox),
            "score": self.score,
            "role": self.role,
            "name": self.name,
            "state": self.state.to_dict(),
            "grid_cell": list(self.grid_cell) if self.grid_cell is not None else None,
        }


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _coerce_checked_value(raw: str | None) -> bool | str:
    """Turn one ``checked`` bracket token's raw value into ``True`` / ``bool``
    / the enum string as-is (e.g. ``"mixed"``). A bare ``[checked]`` (no
    ``=value``) means the state is affirmatively present → ``True``.
    """
    if raw is None:
        return True
    if raw == "true":
        return True
    if raw == "false":
        return False
    return raw


def _coerce_bool_state_value(raw: str | None) -> bool:
    """Same as :func:`_coerce_checked_value` but for the plain-boolean
    states (``disabled``/``expanded``/``pressed``/``selected``) — these
    never carry an enum value like ``checked``'s ``"mixed"``, so any
    present token (bare or ``=true``/``=false``) resolves to a ``bool``.
    """
    if raw is None or raw == "true":
        return True
    return raw != "false"


def parse_aria_snapshot(snapshot: str) -> tuple[str | None, str | None, AccessibilityState]:
    """Parse the first line of a Playwright ``aria_snapshot()`` string.

    Returns ``(role, name, state)``. Only the first line is read — nested
    lines describe children / attributes of OTHER nodes (a link's ``/url``,
    a container's child rows), not additional facts about the queried
    element itself. An empty or unparseable snapshot returns
    ``(None, None, AccessibilityState())`` — the graceful-null path #197
    requires rather than raising.
    """
    first_line = snapshot.splitlines()[0] if snapshot else ""
    match = _ARIA_LINE_RE.match(first_line)
    if not match:
        return None, None, AccessibilityState()

    role = match.group("role")
    name = match.group("name")
    if name is not None:
        name = _unescape(name)

    # `re.findall` yields `''` (not `None`) for an unmatched optional group —
    # normalize so a bare `[disabled]` token and a valued `[checked=mixed]`
    # token are distinguishable in `_coerce_state_value`.
    raw_states: dict[str, str | None] = {
        key: (value or None)
        for key, value in _STATE_TOKEN_RE.findall(first_line)
        if key in _KNOWN_STATE_KEYS
    }
    state = AccessibilityState(
        disabled=(
            _coerce_bool_state_value(raw_states["disabled"]) if "disabled" in raw_states else None
        ),
        checked=(_coerce_checked_value(raw_states["checked"]) if "checked" in raw_states else None),
        expanded=(
            _coerce_bool_state_value(raw_states["expanded"]) if "expanded" in raw_states else None
        ),
        pressed=(
            _coerce_bool_state_value(raw_states["pressed"]) if "pressed" in raw_states else None
        ),
        selected=(
            _coerce_bool_state_value(raw_states["selected"]) if "selected" in raw_states else None
        ),
    )
    return role, name, state


async def capture_accessibility(
    session: Session,
    *,
    selector: str,
    bbox: tuple[int, int, int, int],
    score: int,
    grid: GridConfig | None = None,
    timeout_ms: float = _DEFAULT_TIMEOUT_MS,
) -> AccessibleElement:
    """Capture one element's accessibility node + grid cell (#197/#198).

    ``selector``/``bbox``/``score`` are carried straight into the returned
    :class:`AccessibleElement` (see its docstring for why). ``grid`` is a
    :class:`~clickcast.annotate.grid.GridConfig`; a disabled/``None`` grid
    leaves :attr:`AccessibleElement.grid_cell` ``None``, matching how the
    grid overlay itself is opt-in (#171).

    Any Playwright failure resolving the accessible name/role (selector no
    longer matches, ambiguous match, timeout) is swallowed — the element
    still comes back with a fully-``None`` role/name/state rather than
    aborting the caller's discovery pass.
    """
    role: str | None = None
    name: str | None = None
    state = AccessibilityState()
    try:
        locator = session.locator(selector).first
        snapshot = await locator.aria_snapshot(timeout=timeout_ms)
        role, name, state = parse_aria_snapshot(snapshot)
    except Exception:
        # See module docstring: graceful null, never a discovery failure.
        pass

    cell: tuple[int, int] | None = None
    if grid is not None and grid.enabled:
        cell = _grid_cell(bbox[0], bbox[1], grid.pitch)

    return AccessibleElement(
        selector=selector,
        bbox=bbox,
        score=score,
        role=role,
        name=name,
        state=state,
        grid_cell=cell,
    )


async def capture_accessibility_batch(
    session: Session,
    elements: list[Element],
    *,
    grid: GridConfig | None = None,
    timeout_ms: float = _DEFAULT_TIMEOUT_MS,
) -> list[AccessibleElement]:
    """Capture :func:`capture_accessibility` for every element, in order.

    Runs concurrently (``asyncio.gather``) — these are independent
    read-only locator queries against the same already-loaded page, so
    there's no ordering hazard, and it keeps the added latency close to
    one round trip instead of ``len(elements)`` sequential ones.
    """
    return list(
        await asyncio.gather(
            *(
                capture_accessibility(
                    session,
                    selector=el.selector,
                    bbox=el.bbox,
                    score=el.score,
                    grid=grid,
                    timeout_ms=timeout_ms,
                )
                for el in elements
            )
        )
    )
