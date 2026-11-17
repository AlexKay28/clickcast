"""Perception primitives — "what is the page doing right now?" (#265).

Today this package exposes one primitive: :func:`settle`, a deterministic,
budgeted quiescence probe across network, DOM and compositor activity. It
answers the question bare sleeps and ``networkidle`` cannot: *has the page
stopped changing?* See :mod:`clickcast.perception.settle` for the signal
table and the timeout contract.
"""

from clickcast.perception.settle import (
    DEFAULT_BUDGET_MS,
    POLL_MS,
    SIGNALS,
    SettleResult,
    SignalResult,
    SignalSpec,
    settle,
)

__all__ = [
    "DEFAULT_BUDGET_MS",
    "POLL_MS",
    "SIGNALS",
    "SettleResult",
    "SignalResult",
    "SignalSpec",
    "settle",
]
