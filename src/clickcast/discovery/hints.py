"""Selector-not-found diagnostics — suggest nearby elements on failure.

When a scenario click fails (Playwright: `locator resolved to 0 elements`),
the raw error tells you nothing about what IS on the page. This module
runs the existing :func:`discover` pass, scores each element by string
similarity to the failing selector, and returns the top-N as candidates
so the caller can surface a human- (and agent-) readable hint.

Scoring model (kept intentionally simple — no external deps):

- Role match: exact string equality against the failing selector's
  ``role=<role>`` fragment. +0.4 when it matches.
- Name similarity: ``difflib.SequenceMatcher`` ratio of the failing
  selector's ``[name="..."]`` fragment against each candidate's
  accessible name. Weighted at +0.6.

Total score is in [0.0, 1.0]. Missing fragments contribute 0.

Public API:

- :func:`suggest_candidates` — the top-N candidates for a failed selector.
- :func:`format_candidates` — turn a candidate list into the multi-line
  block that goes into ``Result.error``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from clickcast.core.session import Session
from clickcast.discovery.discovery import Element, discover

__all__ = [
    "ScoredCandidate",
    "format_candidates",
    "parse_selector",
    "suggest_candidates",
]


# Scoring weights — keep in sync with the module docstring / issue #114.
_ROLE_WEIGHT = 0.4
_NAME_WEIGHT = 0.6
# An EXACT name match is almost always what the user wanted — bonus so it
# beats any role-matches-but-name-slightly-off candidate.
_EXACT_NAME_BONUS = 0.5

# `role=<role>[name="<name>"]` — the Playwright locator shape emitted by
# `discover()` and the shape most user-authored scenarios use. We only try
# to extract role + name; other selector shapes get an empty parse (all
# candidates get 0 for that component).
_ROLE_RE = re.compile(r"role=([a-zA-Z][\w-]*)")
_NAME_RE = re.compile(r'\[name=(?:"([^"]*)"|\'([^\']*)\')\]')


@dataclass(slots=True, frozen=True)
class ScoredCandidate:
    """A discovered element paired with its similarity score to the target."""

    element: Element
    score: float


def parse_selector(selector: str) -> tuple[str | None, str | None]:
    """Extract ``(role, name)`` from a Playwright-style selector.

    Returns ``(None, None)`` for shapes that don't match ``role=...`` /
    ``[name="..."]`` — the scorer treats missing components as neutral so
    an id/testid selector still gets a ranked candidate list (just
    driven purely by role hits or, in the fully-unparseable case, all
    zeros).
    """
    role_match = _ROLE_RE.search(selector)
    role = role_match.group(1) if role_match else None
    name_match = _NAME_RE.search(selector)
    if name_match:
        name = name_match.group(1) if name_match.group(1) is not None else name_match.group(2)
    else:
        name = None
    return role, name


def _score_candidate(element: Element, target_role: str | None, target_name: str | None) -> float:
    """Score one candidate against the parsed target components.

    Role exact match contributes up to :data:`_ROLE_WEIGHT`, name similarity
    (via :class:`difflib.SequenceMatcher`) contributes up to
    :data:`_NAME_WEIGHT`. An EXACT name match adds :data:`_EXACT_NAME_BONUS`
    so that a role-mismatched-but-name-perfect candidate outranks a
    role-matched-but-name-slightly-off one — this matches how users
    actually mistype selectors: they know the visible text, they guess the
    role. Missing target components contribute 0.
    """
    score = 0.0
    if target_role is not None and element.role and element.role == target_role:
        score += _ROLE_WEIGHT
    if target_name is not None and element.text:
        # SequenceMatcher.ratio() is 0..1 and uses a Ratcliff/Obershelp
        # variant — close enough to a normalized Levenshtein for our
        # ranking purposes and it's in the stdlib (no new dep).
        target_lower = target_name.lower()
        element_lower = element.text.lower()
        ratio = SequenceMatcher(None, target_lower, element_lower).ratio()
        score += _NAME_WEIGHT * ratio
        if target_lower == element_lower:
            score += _EXACT_NAME_BONUS
    return score


async def suggest_candidates(
    session: Session,
    failed_selector: str,
    *,
    top_n: int = 5,
) -> list[ScoredCandidate]:
    """Return the top-N discovered elements ranked by similarity to
    ``failed_selector``.

    Runs :func:`discover` on the session's current page (no navigation),
    scores each element, and returns the top-N sorted by descending score.
    An empty discovery pool returns an empty list — callers should handle
    that themselves (see :func:`format_candidates`).
    """
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    # Discover with a generous cap: hint quality improves with a wider
    # candidate pool. The default `limit=20` in `discover()` matches
    # the auto engine's usual pool, so line up with that.
    elements = await discover(session, limit=20)
    target_role, target_name = parse_selector(failed_selector)
    scored = [
        ScoredCandidate(element=el, score=_score_candidate(el, target_role, target_name))
        for el in elements
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_n]


def format_candidates(
    failed_selector: str,
    candidates: list[ScoredCandidate],
    total_discovered: int,
    *,
    dump_hint: bool = True,
) -> str:
    """Render a candidate list as the multi-line diagnostic block that
    goes into :attr:`ActionResult.error` (and thus into the sidecar's
    ``step.error``).

    Empty candidate lists degrade gracefully: the caller gets a clean
    single-line message noting that discovery found nothing, with no
    crash and no trailing empty "Candidates" header.
    """
    lines = [f"selector {failed_selector!r} resolved to 0 elements."]
    if not candidates:
        lines.append("  No interactive elements discovered on the current page.")
        return "\n".join(lines)

    lines.append("")
    lines.append(
        f"  Candidates that might be what you meant (top {len(candidates)} by similarity):"
    )
    for c in candidates:
        el = c.element
        bbox = f"[{el.bbox[0]}, {el.bbox[1]}, {el.bbox[2]}, {el.bbox[3]}]"
        lines.append(f"    {el.selector:<45} bbox={bbox:<28} score={c.score:.2f}")
    lines.append("")
    tail = f"  Full page discovery: {total_discovered} interactive elements."
    if dump_hint:
        tail += " Rerun with --dump-elements to see all."
    lines.append(tail)
    return "\n".join(lines)
