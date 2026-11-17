"""Perception primitives — "what is the page doing right now?" (#263, #265).

Two primitives live here:

- :func:`observe` — a token-bounded snapshot of the page's interactive
  elements, addressed by stable opaque ``ref=eN`` handles
  (:mod:`clickcast.perception.observe`).
- :func:`settle` — a deterministic, budgeted quiescence probe across network,
  DOM and compositor activity (:mod:`clickcast.perception.settle`), answering
  the question bare sleeps and ``networkidle`` cannot: *has the page stopped
  changing?*
"""

from clickcast.perception.observe import MAX_NODES, SCHEMA, observe
from clickcast.perception.refs import (
    RefCandidate,
    RefEntry,
    RefRegistry,
    registry_for,
    reset_registry,
)
from clickcast.perception.settle import (
    DEFAULT_BUDGET_MS,
    POLL_MS,
    SIGNALS,
    SettleResult,
    SignalResult,
    SignalSpec,
    settle,
)
from clickcast.perception.tokens import (
    DEFAULT_TOKEN_BUDGET,
    MAX_COMPACT_CHARS,
    compact_json,
    estimate_tokens,
    fits_budget,
)

__all__ = [
    "DEFAULT_BUDGET_MS",
    "DEFAULT_TOKEN_BUDGET",
    "MAX_COMPACT_CHARS",
    "MAX_NODES",
    "POLL_MS",
    "SCHEMA",
    "SIGNALS",
    "RefCandidate",
    "RefEntry",
    "RefRegistry",
    "SettleResult",
    "SignalResult",
    "SignalSpec",
    "compact_json",
    "estimate_tokens",
    "fits_budget",
    "observe",
    "registry_for",
    "reset_registry",
    "settle",
]
