"""Perception primitives — "what is the page doing right now?" (#263, #265, #271).

Three primitives live here:

- :func:`observe` — a token-bounded snapshot of the page's interactive
  elements, addressed by stable opaque ``ref=eN`` handles
  (:mod:`clickcast.perception.observe`).
- :func:`settle` — a deterministic, budgeted quiescence probe across network,
  DOM and compositor activity (:mod:`clickcast.perception.settle`), answering
  the question bare sleeps and ``networkidle`` cannot: *has the page stopped
  changing?*
- :func:`clickcast.perception.receipt.write_receipt` / ``build_receipt`` —
  the closing call of the loop: a ≤200-token summary plus one artifact
  directory holding the details (:mod:`clickcast.perception.receipt`).
"""

from clickcast.perception.observe import MAX_NODES, SCHEMA, observe
from clickcast.perception.receipt import (
    RECEIPT_FILENAME,
    RECEIPT_TOKEN_BUDGET,
    build_receipt,
    write_receipt,
)
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
    "RECEIPT_FILENAME",
    "RECEIPT_TOKEN_BUDGET",
    "SCHEMA",
    "SIGNALS",
    "RefCandidate",
    "RefEntry",
    "RefRegistry",
    "SettleResult",
    "SignalResult",
    "SignalSpec",
    "build_receipt",
    "compact_json",
    "estimate_tokens",
    "fits_budget",
    "observe",
    "registry_for",
    "reset_registry",
    "settle",
    "write_receipt",
]
