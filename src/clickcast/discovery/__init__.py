"""Auto-discovery of interactive elements."""

from clickcast.discovery.discovery import Element, discover
from clickcast.discovery.hints import ScoredCandidate, format_candidates, suggest_candidates

__all__ = [
    "Element",
    "ScoredCandidate",
    "discover",
    "format_candidates",
    "suggest_candidates",
]
