"""AI-feedback JSON sidecar — versioned schema + builder + public loader."""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback.builder import ReportBuilder
from clickcast.feedback.collector import PageStateCollector
from clickcast.feedback.models import (
    DiscoveredElement,
    Media,
    PageState,
    Report,
    StepReport,
)

__all__ = [
    "DiscoveredElement",
    "Media",
    "PageState",
    "PageStateCollector",
    "Report",
    "ReportBuilder",
    "StepReport",
    "load",
    "write",
]


def load(path: str | Path) -> Report:
    """Load a sidecar JSON from disk and validate it against the current schema."""
    text = Path(path).read_text()
    return Report.model_validate_json(text)


def write(report: Report, path: str | Path) -> Path:
    """Serialize ``report`` to disk as pretty-printed JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.model_dump(mode="json"), indent=2))
    return out
