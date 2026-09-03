"""Auto-discovery of interactive elements."""

from clickcast.discovery.accessibility import (
    AccessibilityState,
    AccessibleElement,
    capture_accessibility,
    capture_accessibility_batch,
)
from clickcast.discovery.discovery import Element, discover, discover_with_accessibility
from clickcast.discovery.hints import ScoredCandidate, format_candidates, suggest_candidates

__all__ = [
    "AccessibilityState",
    "AccessibleElement",
    "Element",
    "ScoredCandidate",
    "capture_accessibility",
    "capture_accessibility_batch",
    "discover",
    "discover_with_accessibility",
    "format_candidates",
    "suggest_candidates",
]
