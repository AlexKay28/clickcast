"""Token accounting for the ``observe()`` payload (#263).

The shipped contract is a **budget in estimated tokens**, so the estimator
lives here — in production code — rather than in the tests that assert it.
``observe()`` serializes its payload with :func:`compact_json`, measures the
actual bytes with :func:`estimate_tokens`, and drops nodes until both the
token budget and the hard character ceiling hold. Tests assert exactly the
same formula against exactly the same serialization, so the budget cannot
drift away from what the SDK/MCP consumers actually receive.

The estimator is deliberately crude: ``ceil(len(text) / 4)``. It is not a
tokenizer; it is a stable, provider-independent upper-bound-ish proxy that
an agent can reason about ("800 tokens ≈ 3200 compact JSON chars") without
clickcast pulling in a tokenizer dependency.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "MAX_COMPACT_CHARS",
    "TOKENS_PER_CHAR_DIVISOR",
    "char_limit_for",
    "compact_json",
    "estimate_tokens",
    "fits_budget",
]

#: Default ``observe()`` token budget (issue #263).
DEFAULT_TOKEN_BUDGET = 800

#: Rough chars-per-token ratio the estimator assumes.
TOKENS_PER_CHAR_DIVISOR = 4

#: Absolute ceiling on the compact JSON payload, independent of the token
#: budget. ``DEFAULT_TOKEN_BUDGET * 4 == MAX_COMPACT_CHARS``, so the default
#: contract satisfies both limits at once; the constant also protects callers
#: who pass an unusually large budget.
MAX_COMPACT_CHARS = 3200


def compact_json(value: Any) -> str:
    """Serialize ``value`` exactly the way ``observe()`` ships it.

    Compact separators (no insignificant whitespace), UTF-8 text preserved
    as-is (``ensure_ascii=False``) — the same string is what the SDK returns
    through ``json.dumps``-equivalent consumers and what the MCP tool puts in
    its ``TextContent`` block.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def estimate_tokens(text: str) -> int:
    """``ceil(len(text) / 4)`` — the one estimator used everywhere."""
    return (len(text) + TOKENS_PER_CHAR_DIVISOR - 1) // TOKENS_PER_CHAR_DIVISOR


def char_limit_for(token_budget: int) -> int:
    """Hard character ceiling for ``token_budget``.

    Never more than :data:`MAX_COMPACT_CHARS`; a budget of 800 maps to 3200.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")
    return min(token_budget * TOKENS_PER_CHAR_DIVISOR, MAX_COMPACT_CHARS)


def fits_budget(text: str, *, token_budget: int) -> bool:
    """True when ``text`` satisfies both the token and character limits."""
    return estimate_tokens(text) <= token_budget and len(text) <= char_limit_for(token_budget)
