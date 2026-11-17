"""Session-scoped opaque element refs — ``ref=eN`` → selector registry (#263).

Why refs
--------
An agent observing a page should not have to carry a fragile Playwright
selector in its context. ``observe()`` returns short, opaque ``ref=eN``
handles; this module owns the mapping from those handles to the selector
clickcast can act on, plus the identity rules that keep a handle stable
across observations.

Identity rules
--------------
An element's identity is ``role + accessible name``. When that label is
unique on the page it *is* the identity — the ref survives layout shifts,
hiding/revealing, and a menu opening below it. When two or more elements
share a label (header vs. footer "Home", repeated rows), the registry
tie-breaks with the 64px-quantized bbox bucket from
:func:`clickcast.feedback.graph.dom_signature` — the exact fingerprint
``feedback/graph.py`` already uses for landmark dedup (reused by import, not
copied). A header nav link and a footer nav link therefore get distinct refs
because their 64px buckets differ.

If duplicates still share role + name + bucket — e.g. two adjacent identical
buttons inside the same 64px cell — the registry adds a **document-order
ordinal** within that label group (0, 1, …). The ordinal is stable for an
unchanged DOM and disambiguates the selector with Playwright's shipped
``>> nth=`` form (see
:func:`clickcast.discovery.discovery._disambiguate_selectors`). It can shift
when an earlier duplicate is removed, or when a label group changes size
(e.g. one of two duplicates disappears); that is the documented limit of this
identity scheme.

Removed refs
------------
Every :meth:`RefRegistry.observe` call reconciles the refs it knows about
against the **full raw candidate set** (visible *and* hidden). A ref that was
alive in the previous observation but no longer appears anywhere in the DOM's
interactive candidate set transitions to ``removed``: the registry marks the
entry dead and reports its ``eN`` handle in :attr:`RefRegistry.removed`; the
:func:`clickcast.perception.observe.observe` payload carries the same list in
its top-level ``"removed"`` field. Hidden-but-present elements are not
``removed`` — they are simply omitted from ``nodes``. A ref is reported
``removed`` once (on the transition), not on every subsequent observation.

Session scope
-------------
Registries live in a :class:`weakref.WeakKeyDictionary` keyed by the
:class:`clickcast.core.session.Session` instance, so a ref is stable for as
long as the live session is, and the mapping is garbage-collected with it.
This module imports no Playwright types (see ``tests/test_architecture.py``).
"""

from __future__ import annotations

import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from clickcast.feedback.graph import dom_signature

if TYPE_CHECKING:
    from clickcast.core.session import Session

__all__ = [
    "RefCandidate",
    "RefEntry",
    "RefRegistry",
    "registry_for",
    "reset_registry",
]


@dataclass(slots=True)
class RefCandidate:
    """One observed interactive candidate, as the registry sees it.

    ``visible`` candidates are assigned refs; hidden candidates still take
    part in reconciliation so a merely hidden element is not reported
    ``removed``.
    """

    role: str
    name: str
    bbox: tuple[int, int, int, int]
    selector: str
    visible: bool = True


@dataclass(slots=True)
class RefEntry:
    """A live ref and the selector it resolves to."""

    ref: str
    selector: str
    role: str
    name: str
    signature: str
    ordinal: int
    bbox: tuple[int, int, int, int]
    alive: bool = True


class RefRegistry:
    """Session-scoped ``ref=eN`` registry. See the module docstring."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], RefEntry] = {}
        self._by_ref: dict[str, RefEntry] = {}
        self._next = 1
        self._removed: list[str] = []

    @property
    def removed(self) -> list[str]:
        """Refs whose elements disappeared during the last :meth:`observe`."""
        return list(self._removed)

    def observe(self, candidates: Sequence[RefCandidate]) -> dict[int, str]:
        """Reconcile ``candidates`` and return ``{index: ref}``.

        Candidates are in document order (the order the page's DOM reports
        them), which is what makes the per-signature ordinals stable. The
        returned mapping includes every candidate that has a ref — hidden
        ones too — so callers can decide what to publish.
        """
        seen: set[tuple[str, int]] = set()
        counts: dict[tuple[str, str], int] = {}
        for candidate in candidates:
            identity = (candidate.role, candidate.name)
            counts[identity] = counts.get(identity, 0) + 1

        ordinals: dict[tuple[str, str], int] = {}
        refs: dict[int, str] = {}

        for index, candidate in enumerate(candidates):
            identity = (candidate.role, candidate.name)
            if counts[identity] == 1:
                # Unique role+name: the identity is the label itself, so the
                # ref survives any layout shift (and hiding/revealing).
                signature = f"u:{candidate.role}|{candidate.name}"
                ordinal = 0
            else:
                # Ambiguous label (header + footer nav, repeated rows): fall
                # back to the 64px bbox bucket and a document-order ordinal.
                signature = f"d:{dom_signature(candidate.role, candidate.name, candidate.bbox)}"
                ordinal = ordinals.get(identity, 0)
                ordinals[identity] = ordinal + 1
            key = (signature, ordinal)
            seen.add(key)

            entry = self._by_key.get(key)
            if entry is None:
                if not candidate.visible:
                    # Hidden elements do not consume refs until they become
                    # visible; keeping the numbering tied to shippable nodes
                    # makes payloads easier to read.
                    continue
                entry = RefEntry(
                    ref=f"e{self._next}",
                    selector=candidate.selector,
                    role=candidate.role,
                    name=candidate.name,
                    signature=signature,
                    ordinal=ordinal,
                    bbox=candidate.bbox,
                )
                self._next += 1
                self._by_key[key] = entry
                self._by_ref[entry.ref] = entry
            else:
                entry.selector = candidate.selector
                entry.bbox = candidate.bbox
                entry.alive = True
            refs[index] = entry.ref

        self._removed = []
        for key, entry in self._by_key.items():
            if key not in seen and entry.alive:
                entry.alive = False
                self._removed.append(entry.ref)
        return refs

    def lookup(self, ref: str) -> str | None:
        """Return the selector for ``ref``, or ``None`` for an unknown ref."""
        entry = self._by_ref.get(ref)
        return entry.selector if entry is not None else None

    def entry(self, ref: str) -> RefEntry | None:
        """Return the full registry entry for ``ref`` (``None`` if unknown)."""
        return self._by_ref.get(ref)

    def is_removed(self, ref: str) -> bool:
        """True when ``ref`` is known but its element has disappeared."""
        entry = self._by_ref.get(ref)
        return entry is not None and not entry.alive

    def entries(self) -> list[RefEntry]:
        """All entries, in allocation order (a defensive copy)."""
        return list(self._by_ref.values())


_REGISTRIES: weakref.WeakKeyDictionary[Session, RefRegistry] = weakref.WeakKeyDictionary()


def registry_for(session: Session) -> RefRegistry:
    """Return (creating on first use) the registry owned by ``session``."""
    registry = _REGISTRIES.get(session)
    if registry is None:
        registry = RefRegistry()
        _REGISTRIES[session] = registry
    return registry


def reset_registry(session: Session) -> None:
    """Forget ``session``'s registry — the next observation starts at ``e1``.

    Normal callers never need this: a new :class:`Session` gets a new
    registry automatically. It exists for tests and for callers that
    deliberately navigate to an unrelated page and want a clean namespace.
    """
    _REGISTRIES.pop(session, None)
